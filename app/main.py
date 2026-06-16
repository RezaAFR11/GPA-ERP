"""
GPA-ERP V5.0 — FastAPI application entry point
"""
from contextlib import asynccontextmanager

from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.exception_handlers import http_exception_handler
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import inspect, text

from app.config import get_settings
from app.database import engine
from app.menu_permissions import ensure_default_menus, require_menu_access
from app.models import Base
from app.routers import admin, auth, expenses, inventory, legal, notifications, petty_cash, projects, receivables, reports as reports_router, search, users, vault
from app.routers import hris_employees, hris_attendance, hris_payroll, hris_recruitment, hris_self_service

settings = get_settings()


def _ensure_incremental_schema():
    """Bridge existing local DBs that were created by create_all before newer fields."""
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    with engine.begin() as conn:
        if "projects" in table_names:
            cols = {c["name"] for c in inspector.get_columns("projects")}
            if "currency" not in cols:
                conn.execute(text("ALTER TABLE projects ADD COLUMN currency VARCHAR(3) NOT NULL DEFAULT 'IDR'"))
            if "is_archived" not in cols:
                conn.execute(text("ALTER TABLE projects ADD COLUMN is_archived BOOLEAN NOT NULL DEFAULT FALSE"))
        if "account_receivables" in table_names:
            cols = {c["name"] for c in inspector.get_columns("account_receivables")}
            ar_columns = {
                "invoice_no": "VARCHAR(100)",
                "customer_name": "VARCHAR(255)",
                "invoice_date": "TIMESTAMP WITH TIME ZONE",
                "due_date": "TIMESTAMP WITH TIME ZONE",
                "expected_payment": "NUMERIC(18, 2)",
                "actual_payment": "NUMERIC(18, 2)",
                "remaining_amount": "NUMERIC(18, 2)",
                "paid_at": "TIMESTAMP WITH TIME ZONE",
            }
            for name, ddl in ar_columns.items():
                if name not in cols:
                    conn.execute(text(f"ALTER TABLE account_receivables ADD COLUMN {name} {ddl}"))
        if "expenses" in table_names:
            cols = {c["name"] for c in inspector.get_columns("expenses")}
            if "cost_centre_id" not in cols:
                conn.execute(text("ALTER TABLE expenses ADD COLUMN cost_centre_id INTEGER REFERENCES cost_centres(id)"))
            if "petty_cash_line_id" not in cols and "petty_cash_report_lines" in table_names:
                conn.execute(text("ALTER TABLE expenses ADD COLUMN petty_cash_line_id INTEGER REFERENCES petty_cash_report_lines(id)"))
            if "vendor_name" not in cols:
                conn.execute(text("ALTER TABLE expenses ADD COLUMN vendor_name VARCHAR(255)"))
            if "reference_no" not in cols:
                conn.execute(text("ALTER TABLE expenses ADD COLUMN reference_no VARCHAR(100)"))
            # V5.1 — reimbursement support
            if "expense_type" not in cols:
                # Create enum type if it doesn't exist, then add column
                conn.execute(text("DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'expensetype') THEN CREATE TYPE expensetype AS ENUM ('regular', 'reimbursement'); END IF; END $$"))
                conn.execute(text("ALTER TABLE expenses ADD COLUMN expense_type expensetype NOT NULL DEFAULT 'regular'"))
            if "receipt_reviewed_by" not in cols:
                conn.execute(text("ALTER TABLE expenses ADD COLUMN receipt_reviewed_by INTEGER REFERENCES users(id)"))
            # Make project_id nullable (reimbursements don't require a project)
            # Safe: only alters constraint, doesn't touch data
            try:
                conn.execute(text("ALTER TABLE expenses ALTER COLUMN project_id DROP NOT NULL"))
            except Exception:
                pass  # already nullable
        if "legal_documents" in table_names:
            cols = {c["name"] for c in inspector.get_columns("legal_documents")}
            if "reference_number" not in cols:
                conn.execute(text("ALTER TABLE legal_documents ADD COLUMN reference_number VARCHAR(100)"))
        # HRIS work locations & geolocation columns (added in V5.1)
        if "hris_employees" in table_names:
            cols = {c["name"] for c in inspector.get_columns("hris_employees")}
            if "work_location_id" not in cols:
                # hris_work_locations must exist first — created by create_all above
                conn.execute(text(
                    "ALTER TABLE hris_employees ADD COLUMN work_location_id INTEGER "
                    "REFERENCES hris_work_locations(id)"
                ))
            if "work_group_id" not in cols:
                conn.execute(text(
                    "ALTER TABLE hris_employees ADD COLUMN work_group_id INTEGER "
                    "REFERENCES hris_work_groups(id)"
                ))
            if "ptkp_status" not in cols:
                conn.execute(text(
                    "ALTER TABLE hris_employees ADD COLUMN ptkp_status VARCHAR(10) DEFAULT 'TK/0'"
                ))
        if "hris_leave_types" in table_names:
            cols = {c["name"] for c in inspector.get_columns("hris_leave_types")}
            if "category" not in cols:
                conn.execute(text("ALTER TABLE hris_leave_types ADD COLUMN IF NOT EXISTS category VARCHAR(20) DEFAULT 'annual'"))
            if "requires_doctor_cert" not in cols:
                conn.execute(text("ALTER TABLE hris_leave_types ADD COLUMN IF NOT EXISTS requires_doctor_cert BOOLEAN DEFAULT FALSE"))
        if "hris_attendance_records" in table_names:
            cols = {c["name"] for c in inspector.get_columns("hris_attendance_records")}
            if "location_ok" not in cols:
                conn.execute(text("ALTER TABLE hris_attendance_records ADD COLUMN location_ok BOOLEAN"))
            if "location_distance_m" not in cols:
                conn.execute(text("ALTER TABLE hris_attendance_records ADD COLUMN location_distance_m NUMERIC(10,1)"))
            if "matched_work_location_id" not in cols:
                conn.execute(text(
                    "ALTER TABLE hris_attendance_records ADD COLUMN matched_work_location_id INTEGER "
                    "REFERENCES hris_work_locations(id)"
                ))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS hris_work_groups (
                id          SERIAL PRIMARY KEY,
                name        VARCHAR(255) NOT NULL UNIQUE,
                role        VARCHAR(50)  NOT NULL,
                description TEXT,
                is_active   BOOLEAN NOT NULL DEFAULT TRUE,
                created_at  TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
                updated_at  TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
            )
        """))
        if "notifications" not in table_names:
            conn.execute(text("""
                CREATE TABLE notifications (
                    id         SERIAL PRIMARY KEY,
                    user_id    INTEGER NOT NULL REFERENCES users(id),
                    title      VARCHAR(200) NOT NULL,
                    body       VARCHAR(500) NOT NULL,
                    link       VARCHAR(500),
                    is_read    BOOLEAN NOT NULL DEFAULT FALSE,
                    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
                )
            """))
            conn.execute(text("CREATE INDEX ix_notifications_user_id ON notifications (user_id)"))
            conn.execute(text("CREATE INDEX ix_notifications_user_is_read ON notifications (user_id, is_read)"))
            conn.execute(text("CREATE INDEX ix_notifications_created_at ON notifications (created_at DESC)"))

        # ── Enhancement Pack tables ───────────────────────────────────────────
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS hris_holiday_calendar (
                id          SERIAL PRIMARY KEY,
                date        DATE NOT NULL UNIQUE,
                name        VARCHAR(255) NOT NULL,
                is_national BOOLEAN NOT NULL DEFAULT TRUE,
                year        INTEGER NOT NULL,
                created_at  TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
                updated_at  TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
            )
        """))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_holiday_date ON hris_holiday_calendar (date)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_holiday_year ON hris_holiday_calendar (year)"))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS hris_overtime_requests (
                id               SERIAL PRIMARY KEY,
                employee_id      INTEGER NOT NULL REFERENCES hris_employees(id),
                date             DATE NOT NULL,
                planned_hours    NUMERIC(4,1) NOT NULL,
                reason           TEXT NOT NULL,
                status           VARCHAR(20) NOT NULL DEFAULT 'submitted',
                approved_by      INTEGER REFERENCES users(id),
                approved_at      TIMESTAMP WITH TIME ZONE,
                rejection_reason TEXT,
                attendance_id    INTEGER REFERENCES hris_attendance_records(id),
                created_at       TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
                updated_at       TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
            )
        """))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_ot_requests_employee ON hris_overtime_requests (employee_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_ot_requests_status ON hris_overtime_requests (status)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_ot_requests_date ON hris_overtime_requests (date)"))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS hris_data_change_requests (
                id           SERIAL PRIMARY KEY,
                employee_id  INTEGER NOT NULL REFERENCES hris_employees(id),
                field_name   VARCHAR(100) NOT NULL,
                old_value    TEXT,
                new_value    TEXT NOT NULL,
                reason       TEXT,
                status       VARCHAR(20) NOT NULL DEFAULT 'pending',
                reviewed_by  INTEGER REFERENCES users(id),
                reviewed_at  TIMESTAMP WITH TIME ZONE,
                review_note  TEXT,
                created_at   TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
                updated_at   TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
            )
        """))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_data_change_employee ON hris_data_change_requests (employee_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_data_change_status ON hris_data_change_requests (status)"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create all tables on startup (in production, rely on Alembic migrations instead)
    Base.metadata.create_all(bind=engine)
    _ensure_incremental_schema()
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        ensure_default_menus(db)
    finally:
        db.close()
    yield
    # Teardown: nothing needed for SQLAlchemy synchronous engine


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description=(
        "GPA Cost Control ERP — Multi-project expense management with "
        "configurable approval matrix, revenue-driven budget tracking, "
        "and immutable audit logging."
    ),
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# ─── CORS ────────────────────────────────────────────────────────────────────

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

# ─── Global exception handlers ───────────────────────────────────────────────

@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    if settings.DEBUG:
        raise exc
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "An internal error occurred. Please contact support."},
    )


@app.exception_handler(HTTPException)
async def admin_http_exception_handler(request: Request, exc: HTTPException):
    if request.url.path.startswith("/admin") and "text/html" in request.headers.get("accept", ""):
        detail = str(exc.detail)
        return HTMLResponse(
            f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Admin Error</title>
<style>body{{font-family:Segoe UI,Arial,sans-serif;background:#f4f3ee;margin:0;padding:40px;color:#111827}}
.card{{max-width:560px;background:white;border:1px solid #d9dde5;border-radius:8px;padding:24px;margin:60px auto;box-shadow:0 1px 3px rgba(15,23,42,.08)}}
a{{color:#2746c7;font-weight:700;text-decoration:none}}</style></head>
<body><section class="card"><h1>Admin action could not be completed</h1>
<p>{detail}</p><p><a href="/admin/menu-access">Back to admin</a></p></section></body></html>""",
            status_code=exc.status_code,
            headers=exc.headers,
        )
    return await http_exception_handler(request, exc)

# ─── Routers ─────────────────────────────────────────────────────────────────

API_PREFIX = "/api"

app.include_router(auth.router,        prefix=API_PREFIX)
app.include_router(users.router,       prefix=API_PREFIX, dependencies=[Depends(require_menu_access("settings", "vault"))])
app.include_router(projects.router,    prefix=API_PREFIX, dependencies=[Depends(require_menu_access("project_command"))])
app.include_router(receivables.router, prefix=API_PREFIX, dependencies=[Depends(require_menu_access("revenue_ar"))])
app.include_router(expenses.router,    prefix=API_PREFIX, dependencies=[Depends(require_menu_access("spending", "action_center"))])
app.include_router(petty_cash.router,  prefix=API_PREFIX, dependencies=[Depends(require_menu_access("petty_cash", "spending"))])
app.include_router(vault.router,       prefix=API_PREFIX)
app.include_router(legal.router,       prefix=API_PREFIX, dependencies=[Depends(require_menu_access("legal"))])
app.include_router(inventory.router,   prefix=API_PREFIX, dependencies=[Depends(require_menu_access("inventory"))])
app.include_router(search.router,         prefix=API_PREFIX)
app.include_router(notifications.router,  prefix=API_PREFIX)
app.include_router(reports_router.router, prefix=API_PREFIX)
app.include_router(admin.router)

# ─── HRIS Routers ────────────────────────────────────────────────────────────
app.include_router(hris_employees.router, prefix=API_PREFIX,
                   dependencies=[Depends(require_menu_access("hris_employees", "hris_dashboard"))])
app.include_router(hris_attendance.router, prefix=API_PREFIX,
                   dependencies=[Depends(require_menu_access("hris_attendance", "hris_leave", "hris_dashboard"))])
app.include_router(hris_payroll.router, prefix=API_PREFIX,
                   dependencies=[Depends(require_menu_access("hris_payroll", "hris_dashboard"))])
app.include_router(hris_recruitment.router, prefix=API_PREFIX,
                   dependencies=[Depends(require_menu_access("hris_recruitment", "hris_dashboard"))])
# Self-service: any user with attendance OR leave OR payslip access can hit /hris/me/*
app.include_router(hris_self_service.router, prefix=API_PREFIX,
                   dependencies=[Depends(require_menu_access("hris_attendance", "hris_leave", "hris_my_payslip"))])

# ─── Authenticated file serving ──────────────────────────────────────────────
# Uploaded files (receipts, selfies, employee docs) are served via an
# authenticated endpoint so unauthenticated users cannot download them by URL.

_UPLOADS_DIR = Path("uploads")
_UPLOADS_DIR.mkdir(exist_ok=True)

from app.dependencies import get_current_user  # noqa: E402  (after app init)
from app.models import User  # noqa: E402

@app.get("/uploads/{file_path:path}", include_in_schema=False)
def serve_upload(
    file_path: str,
    _: User = Depends(get_current_user),
):
    """Serve uploaded files only to authenticated users."""
    abs_path = (_UPLOADS_DIR / file_path).resolve()
    # Prevent path traversal outside the uploads directory
    if not str(abs_path).startswith(str(_UPLOADS_DIR.resolve())):
        raise HTTPException(status_code=403, detail="Access denied")
    if not abs_path.exists() or not abs_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(abs_path)

# ─── Root redirect ───────────────────────────────────────────────────────────

@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse(url="/docs")

# ─── Health check ────────────────────────────────────────────────────────────

@app.get("/health", tags=["Meta"], summary="Liveness probe")
def health():
    return {"status": "ok", "app": settings.APP_NAME, "version": settings.APP_VERSION}

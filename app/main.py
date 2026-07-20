"""
GPA-ERP V5.0 — FastAPI application entry point
"""
import asyncio
from contextlib import asynccontextmanager

from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.exception_handlers import http_exception_handler
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import or_, text

from app.config import get_settings
from app.database import engine
from app.menu_permissions import require_menu_access, user_has_menu_access
from app.routers import admin, auth, expenses, inventory, legal, notifications, operations, petty_cash, projects, receivables, reports as reports_router, search, settings as settings_router, users, vault
from app.routers import hris_employees, hris_attendance, hris_payroll, hris_recruitment, hris_self_service

settings = get_settings()




@asynccontextmanager
async def lifespan(app: FastAPI):
    # Local development can repair an older create_all database. Production is
    # intentionally migration-only so schema drift cannot be hidden at startup.
    if settings.DEBUG:
        from app.development_schema import prepare_development_database

        prepare_development_database()
    from app.notify import run_email_outbox_worker
    email_worker_stop = asyncio.Event()
    email_worker = asyncio.create_task(run_email_outbox_worker(email_worker_stop))
    try:
        yield
    finally:
        email_worker_stop.set()
        await email_worker


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


@app.middleware("http")
async def protect_cookie_authenticated_requests(request: Request, call_next):
    """Reject cross-origin state changes that rely on the browser session cookie."""
    unsafe_method = request.method.upper() not in {"GET", "HEAD", "OPTIONS"}
    uses_app_cookie = bool(request.cookies.get("access_token"))
    if unsafe_method and uses_app_cookie and not request.headers.get("authorization"):
        origin = request.headers.get("origin")
        own_origin = str(request.base_url).rstrip("/")
        if not origin or origin.rstrip("/") not in {*settings.allowed_origins_list, own_origin}:
            return JSONResponse(status_code=403, content={"detail": "Untrusted request origin"})
    return await call_next(request)

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
app.include_router(users.router,       prefix=API_PREFIX)  # per-endpoint role guards; /me/* must stay reachable for all authenticated users
app.include_router(settings_router.router, prefix=API_PREFIX)
app.include_router(projects.router,    prefix=API_PREFIX, dependencies=[Depends(require_menu_access("project_command", "dashboard"))])
app.include_router(receivables.router, prefix=API_PREFIX, dependencies=[Depends(require_menu_access("revenue_ar"))])
app.include_router(expenses.router,    prefix=API_PREFIX, dependencies=[Depends(require_menu_access("spending", "action_center"))])
app.include_router(petty_cash.router,  prefix=API_PREFIX, dependencies=[Depends(require_menu_access("petty_cash", "spending"))])
app.include_router(vault.router,       prefix=API_PREFIX)
app.include_router(legal.router,       prefix=API_PREFIX, dependencies=[Depends(require_menu_access("legal"))])
app.include_router(inventory.router,   prefix=API_PREFIX, dependencies=[Depends(require_menu_access("inventory"))])
app.include_router(operations.router,  prefix=API_PREFIX)
app.include_router(search.router,         prefix=API_PREFIX)
app.include_router(notifications.router,  prefix=API_PREFIX)
app.include_router(reports_router.router, prefix=API_PREFIX)
app.include_router(admin.router)

# ─── HRIS Routers ────────────────────────────────────────────────────────────
app.include_router(hris_employees.router, prefix=API_PREFIX,
                   dependencies=[Depends(require_menu_access("hris_employees", "hris_dashboard"))])
# Attendance, leave, overtime, and HRIS-setting endpoints share one router but
# enforce their own menu dependency at endpoint level.
app.include_router(hris_attendance.router, prefix=API_PREFIX)
app.include_router(hris_payroll.router, prefix=API_PREFIX,
                   dependencies=[Depends(require_menu_access("hris_payroll", "hris_settings"))])
app.include_router(hris_recruitment.router, prefix=API_PREFIX,
                   dependencies=[Depends(require_menu_access("hris_recruitment"))])
# Self-service: any user with attendance OR leave OR payslip access can hit /hris/me/*
app.include_router(hris_self_service.router, prefix=API_PREFIX,
                   dependencies=[Depends(require_menu_access("hris_attendance", "hris_leave", "hris_my_payslip"))])

# ─── Authenticated file serving ──────────────────────────────────────────────
# Uploaded files (receipts, selfies, employee docs) are served via an
# authenticated endpoint so unauthenticated users cannot download them by URL.

_UPLOADS_DIR = Path(settings.UPLOAD_DIR)
_UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

from sqlalchemy.orm import Session  # noqa: E402
from app.database import get_db  # noqa: E402
from app.dependencies import get_current_user  # noqa: E402  (after app init)
from app.models import (  # noqa: E402
    AttendanceRecord, Employee, EmployeeDocument, Expense, LeaveRequest,
    RoleName, User, effective_roles,
)


def _authorize_upload(file_url: str, file_path: str, current_user: User, db: Session) -> None:
    """Enforce menu access and record ownership for every served upload."""
    folder = Path(file_path).parts[0] if Path(file_path).parts else ""

    if folder == "receipts":
        if not user_has_menu_access(db, current_user, "spending", "action_center", "petty_cash"):
            raise HTTPException(status_code=403, detail="Access denied")
        if current_user.role.name in {RoleName.STAFF, RoleName.WORKER}:
            linked_receipt = db.query(Expense.id).filter(
                Expense.submitted_by == current_user.id,
                or_(
                    Expense.receipt_url == file_url,
                    Expense.receipt_url.endswith(file_url),
                ),
            ).first()
            is_new_upload = Path(file_path).name.startswith(f"user_{current_user.id}_")
            if not linked_receipt and not is_new_upload:
                raise HTTPException(status_code=403, detail="Access denied")
        return

    sensitive_hris_folders = {
        "employee_docs", "employee_photos", "selfies", "leave_certificates",
    }
    if folder not in sensitive_hris_folders:
        raise HTTPException(status_code=403, detail="Access denied")

    user_roles = effective_roles(current_user.role.name)
    if set(user_roles).intersection({RoleName.SUPER_ADMIN, RoleName.MD, RoleName.GA, RoleName.HR}):
        return

    own_employee = db.query(Employee).filter(Employee.user_id == current_user.id).first()
    if folder == "employee_docs":
        document = db.query(EmployeeDocument).filter(EmployeeDocument.file_url == file_url).first()
        allowed = bool(document and own_employee and document.employee_id == own_employee.id)
    elif folder == "employee_photos":
        employee = db.query(Employee).filter(Employee.photo_url == file_url).first()
        allowed = bool(employee and own_employee and employee.id == own_employee.id)
    elif folder == "selfies":
        attendance = db.query(AttendanceRecord).filter(AttendanceRecord.selfie_url == file_url).first()
        allowed = bool(attendance and own_employee and attendance.employee_id == own_employee.id)
    else:
        leave_request = db.query(LeaveRequest).filter(LeaveRequest.doctor_cert_url == file_url).first()
        allowed = bool(
            leave_request
            and own_employee
            and leave_request.employee_id == own_employee.id
        )
        if leave_request and leave_request.current_approver_role:
            try:
                allowed = allowed or RoleName(leave_request.current_approver_role) in user_roles
            except ValueError:
                pass
        # A newly uploaded certificate is not linked until the leave form is submitted.
        if not leave_request:
            allowed = Path(file_path).name.startswith(f"user_{current_user.id}_")

    if not allowed:
        raise HTTPException(status_code=403, detail="Access denied")

@app.get("/uploads/{file_path:path}", include_in_schema=False)
def serve_upload(
    file_path: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Serve uploads to authenticated and, for HRIS data, authorized users."""
    abs_path = (_UPLOADS_DIR / file_path).resolve()
    uploads_root = _UPLOADS_DIR.resolve()
    if not abs_path.is_relative_to(uploads_root):
        raise HTTPException(status_code=403, detail="Access denied")
    if not abs_path.exists() or not abs_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    normalized_path = abs_path.relative_to(uploads_root).as_posix()
    _authorize_upload(
        f"/uploads/{normalized_path}", normalized_path, current_user, db,
    )
    return FileResponse(abs_path)

# ─── Root redirect ───────────────────────────────────────────────────────────

@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse(url="/docs")

# ─── Health check ────────────────────────────────────────────────────────────

@app.get("/health", tags=["Meta"], summary="Readiness probe")
def health():
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Database unavailable") from exc
    return {"status": "ok", "app": settings.APP_NAME, "version": settings.APP_VERSION}

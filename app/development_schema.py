"""Development-only compatibility bootstrap for legacy local databases.

Production deployments apply Alembic migrations before the application starts.
This module only keeps older local databases created with create_all usable while
developers migrate them at their own pace.
"""
from sqlalchemy import inspect, text

from app.database import SessionLocal, engine
from app.menu_permissions import (
    ensure_all_roles,
    ensure_default_menus,
    grant_menu_to_roles,
)
from app.models import Base, RoleName


def _ensure_incremental_schema():
    """Bridge existing local DBs that were created by create_all before newer fields."""
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())

    # New role enum values (HR, PROJECT_CONTROL) — run in autocommit, outside any
    # transaction, for compatibility across PostgreSQL versions. Idempotent.
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as ac:
        for _role_val in ("PROJECT_CONTROL", "HR"):
            ac.execute(text(f"ALTER TYPE rolename ADD VALUE IF NOT EXISTS '{_role_val}'"))

    with engine.begin() as conn:
        if "users" in table_names:
            cols = {c["name"] for c in inspector.get_columns("users")}
            if "must_change_password" not in cols:
                conn.execute(text(
                    "ALTER TABLE users ADD COLUMN must_change_password BOOLEAN NOT NULL DEFAULT FALSE"
                ))
            if "token_version" not in cols:
                conn.execute(text(
                    "ALTER TABLE users ADD COLUMN token_version INTEGER NOT NULL DEFAULT 0"
                ))
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
        if "hris_work_locations" in table_names:
            cols = {c["name"] for c in inspector.get_columns("hris_work_locations")}
            if "timezone_name" not in cols:
                conn.execute(text(
                    "ALTER TABLE hris_work_locations ADD COLUMN timezone_name "
                    "VARCHAR(64) NOT NULL DEFAULT 'Asia/Jakarta'"
                ))
        if "hris_applicants" in table_names:
            cols = {c["name"] for c in inspector.get_columns("hris_applicants")}
            if "employee_id" not in cols:
                conn.execute(text(
                    "ALTER TABLE hris_applicants ADD COLUMN employee_id INTEGER UNIQUE "
                    "REFERENCES hris_employees(id)"
                ))
        if "hris_leave_types" in table_names:
            cols = {c["name"] for c in inspector.get_columns("hris_leave_types")}
            if "category" not in cols:
                conn.execute(text(
                    "ALTER TABLE hris_leave_types ADD COLUMN IF NOT EXISTS "
                    "category VARCHAR(20) NOT NULL DEFAULT 'annual'"
                ))
            if "requires_doctor_cert" not in cols:
                conn.execute(text(
                    "ALTER TABLE hris_leave_types ADD COLUMN IF NOT EXISTS "
                    "requires_doctor_cert BOOLEAN NOT NULL DEFAULT FALSE"
                ))
        if "hris_leave_requests" in table_names:
            cols = {c["name"] for c in inspector.get_columns("hris_leave_requests")}
            if "doctor_cert_url" not in cols:
                conn.execute(text(
                    "ALTER TABLE hris_leave_requests ADD COLUMN doctor_cert_url VARCHAR(500)"
                ))
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
        if "hris_overtime_requests" in table_names:
            cols = {c["name"] for c in inspector.get_columns("hris_overtime_requests")}
            if "attendance_id" not in cols:
                conn.execute(text(
                    "ALTER TABLE hris_overtime_requests ADD COLUMN attendance_id INTEGER "
                    "REFERENCES hris_attendance_records(id)"
                ))

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


def prepare_development_database() -> None:
    """Prepare legacy local data without changing production startup behavior."""
    Base.metadata.create_all(bind=engine)
    _ensure_incremental_schema()

    database = SessionLocal()
    try:
        ensure_all_roles(database)
        ensure_default_menus(database)
        grant_menu_to_roles(
            database,
            "hris_employees",
            (RoleName.PM, RoleName.PROJECT_CONTROL),
        )
    finally:
        database.close()

"""initial_schema

Revision ID: a0_initial_schema
Revises:
Create Date: 2026-05-01 00:00:00.000000

Creates the core ERP tables that pre-existed the incremental migration chain.
Previously these were created via Base.metadata.create_all(); this migration
makes a fresh Railway Postgres deployment work without that shortcut.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'a0_initial_schema'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    # ── Enums (idempotent raw SQL — avoids SQLAlchemy hook conflicts) ─────────
    conn.execute(sa.text("""
        DO $$ BEGIN
            CREATE TYPE rolename AS ENUM (
                'SUPER_ADMIN','MD','PM','COST_CONTROL','FINANCE','GA','STAFF','WORKER'
            );
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$
    """))
    conn.execute(sa.text("""
        DO $$ BEGIN
            CREATE TYPE projectstatus AS ENUM ('active','completed','on_hold','cancelled');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$
    """))
    conn.execute(sa.text("""
        DO $$ BEGIN
            CREATE TYPE costcodecategory AS ENUM (
                'Direct','Site','Personnel','Overhead','Other','Reimbursement'
            );
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$
    """))
    conn.execute(sa.text("""
        DO $$ BEGIN
            CREATE TYPE arstatus AS ENUM ('draft','confirmed');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$
    """))
    conn.execute(sa.text("""
        DO $$ BEGIN
            CREATE TYPE expensestatus AS ENUM (
                'draft','submitted','verified','approved','paid','hard_locked','rejected'
            );
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$
    """))
    conn.execute(sa.text("""
        DO $$ BEGIN
            CREATE TYPE expensetype AS ENUM ('regular','reimbursement');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$
    """))

    # ── Tables (fully idempotent raw SQL) ─────────────────────────────────────

    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS roles (
            id   SERIAL PRIMARY KEY,
            name rolename NOT NULL UNIQUE
        )
    """))

    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS users (
            id               SERIAL PRIMARY KEY,
            email            VARCHAR(320) NOT NULL UNIQUE,
            hashed_password  VARCHAR(255) NOT NULL,
            full_name        VARCHAR(255) NOT NULL,
            role_id          INTEGER NOT NULL REFERENCES roles(id),
            is_active        BOOLEAN NOT NULL DEFAULT TRUE,
            created_at       TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
            updated_at       TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
        )
    """))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_users_email ON users (email)"))

    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS projects (
            id             SERIAL PRIMARY KEY,
            code           VARCHAR(50)  NOT NULL UNIQUE,
            name           VARCHAR(255) NOT NULL,
            contract_value NUMERIC(18,2) NOT NULL DEFAULT 0,
            is_archived    BOOLEAN NOT NULL DEFAULT FALSE,
            start_date     TIMESTAMP WITH TIME ZONE,
            end_date       TIMESTAMP WITH TIME ZONE,
            status         projectstatus NOT NULL DEFAULT 'active',
            imported_at    TIMESTAMP WITH TIME ZONE,
            created_at     TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
            updated_at     TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
        )
    """))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_projects_code ON projects (code)"))

    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS cost_codes (
            id        SERIAL PRIMARY KEY,
            code      VARCHAR(50) NOT NULL UNIQUE,
            name      VARCHAR(255) NOT NULL,
            parent_id INTEGER REFERENCES cost_codes(id),
            category  costcodecategory NOT NULL,
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
        )
    """))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_cost_codes_code ON cost_codes (code)"))

    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS approval_rules (
            id                 SERIAL PRIMARY KEY,
            min_amount         NUMERIC(18,2) NOT NULL DEFAULT 0,
            max_amount         NUMERIC(18,2),
            cost_code_category costcodecategory,
            required_role      rolename NOT NULL,
            priority           INTEGER NOT NULL DEFAULT 1,
            is_active          BOOLEAN NOT NULL DEFAULT TRUE,
            created_at         TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
            updated_at         TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
        )
    """))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_approval_rules_priority ON approval_rules (priority)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_approval_rules_active ON approval_rules (is_active)"))

    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS account_receivables (
            id               SERIAL PRIMARY KEY,
            project_id       INTEGER NOT NULL REFERENCES projects(id),
            amount           NUMERIC(18,2) NOT NULL,
            description      TEXT NOT NULL,
            invoice_no       VARCHAR(100),
            customer_name    VARCHAR(255),
            invoice_date     TIMESTAMP WITH TIME ZONE,
            due_date         TIMESTAMP WITH TIME ZONE,
            expected_payment NUMERIC(18,2),
            actual_payment   NUMERIC(18,2),
            remaining_amount NUMERIC(18,2),
            paid_at          TIMESTAMP WITH TIME ZONE,
            status           arstatus NOT NULL DEFAULT 'draft',
            confirmed_by     INTEGER REFERENCES users(id),
            confirmed_at     TIMESTAMP WITH TIME ZONE,
            created_at       TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
            updated_at       TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
        )
    """))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_account_receivables_invoice_no ON account_receivables (invoice_no)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_ar_project_status ON account_receivables (project_id, status)"))

    # expenses: cost_centre_id added by d2a7f4c9b001, petty_cash_line_id by e1b7a9c4d002
    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS expenses (
            id                   SERIAL PRIMARY KEY,
            expense_type         expensetype NOT NULL DEFAULT 'regular',
            project_id           INTEGER REFERENCES projects(id),
            cost_code_id         INTEGER NOT NULL REFERENCES cost_codes(id),
            amount               NUMERIC(18,2) NOT NULL,
            description          TEXT NOT NULL,
            vendor_name          VARCHAR(255),
            reference_no         VARCHAR(100),
            receipt_url          VARCHAR(2048),
            status               expensestatus NOT NULL DEFAULT 'draft',
            submitted_by         INTEGER REFERENCES users(id),
            receipt_reviewed_by  INTEGER REFERENCES users(id),
            verified_by          INTEGER REFERENCES users(id),
            approved_by          INTEGER REFERENCES users(id),
            paid_by              INTEGER REFERENCES users(id),
            current_approver_role VARCHAR(50),
            approval_chain       JSONB,
            approval_step        INTEGER NOT NULL DEFAULT 0,
            approval_history     JSONB,
            rejection_reason     TEXT,
            created_at           TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
            updated_at           TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
        )
    """))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_expenses_project_status ON expenses (project_id, status)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_expenses_current_approver ON expenses (current_approver_role)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_expenses_submitted_by ON expenses (submitted_by)"))

    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS audit_logs (
            id          SERIAL PRIMARY KEY,
            entity_type VARCHAR(100) NOT NULL,
            entity_id   INTEGER NOT NULL,
            action      VARCHAR(100) NOT NULL,
            before_state JSONB,
            after_state  JSONB,
            changed_by  INTEGER REFERENCES users(id),
            ip_address  VARCHAR(45),
            created_at  TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
        )
    """))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_audit_entity ON audit_logs (entity_type, entity_id)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_audit_changed_by ON audit_logs (changed_by)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_audit_created_at ON audit_logs (created_at)"))

    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS notifications (
            id         SERIAL PRIMARY KEY,
            user_id    INTEGER NOT NULL REFERENCES users(id),
            title      VARCHAR(200) NOT NULL,
            body       VARCHAR(500) NOT NULL,
            link       VARCHAR(500),
            is_read    BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
        )
    """))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_notifications_user_id ON notifications (user_id)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_notifications_user_is_read ON notifications (user_id, is_read)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_notifications_created_at ON notifications (created_at DESC)"))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("DROP TABLE IF EXISTS notifications"))
    conn.execute(sa.text("DROP TABLE IF EXISTS audit_logs"))
    conn.execute(sa.text("DROP TABLE IF EXISTS expenses"))
    conn.execute(sa.text("DROP TABLE IF EXISTS account_receivables"))
    conn.execute(sa.text("DROP TABLE IF EXISTS approval_rules"))
    conn.execute(sa.text("DROP TABLE IF EXISTS cost_codes"))
    conn.execute(sa.text("DROP TABLE IF EXISTS projects"))
    conn.execute(sa.text("DROP TABLE IF EXISTS users"))
    conn.execute(sa.text("DROP TABLE IF EXISTS roles"))
    conn.execute(sa.text("DROP TYPE IF EXISTS expensetype"))
    conn.execute(sa.text("DROP TYPE IF EXISTS expensestatus"))
    conn.execute(sa.text("DROP TYPE IF EXISTS arstatus"))
    conn.execute(sa.text("DROP TYPE IF EXISTS costcodecategory"))
    conn.execute(sa.text("DROP TYPE IF EXISTS projectstatus"))
    conn.execute(sa.text("DROP TYPE IF EXISTS rolename"))

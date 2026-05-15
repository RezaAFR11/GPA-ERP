"""add hris attendance leave

Revision ID: g2b3c4d5e6f7
Revises: g1a2b3c4d5e6
Create Date: 2026-05-16 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "g2b3c4d5e6f7"
down_revision = "g1a2b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Enums ──────────────────────────────────────────────────────────────────
    attendance_source = postgresql.ENUM(
        "manual", "mobile", "fingerprint", "import",
        name="attendancesource", create_type=True
    )
    leave_request_status = postgresql.ENUM(
        "draft", "submitted", "approved", "rejected",
        name="leaverequeststatus", create_type=True
    )
    attendance_source.create(op.get_bind(), checkfirst=True)
    leave_request_status.create(op.get_bind(), checkfirst=True)

    # ── face_embedding on employees ────────────────────────────────────────────
    op.add_column(
        "hris_employees",
        sa.Column("face_embedding", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )

    # ── hris_attendance_records ────────────────────────────────────────────────
    op.create_table(
        "hris_attendance_records",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("employee_id", sa.Integer(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("clock_in", sa.DateTime(timezone=True), nullable=True),
        sa.Column("clock_out", sa.DateTime(timezone=True), nullable=True),
        sa.Column("hours_regular", sa.Numeric(5, 2), nullable=True),
        sa.Column("hours_overtime_weekday", sa.Numeric(5, 2), nullable=True),
        sa.Column("hours_overtime_weekend", sa.Numeric(5, 2), nullable=True),
        sa.Column("hours_overtime_holiday", sa.Numeric(5, 2), nullable=True),
        sa.Column("source", sa.Enum("manual", "mobile", "fingerprint", "import", name="attendancesource"), nullable=False),
        # Geolocation
        sa.Column("latitude", sa.Numeric(9, 6), nullable=True),
        sa.Column("longitude", sa.Numeric(9, 6), nullable=True),
        sa.Column("accuracy", sa.Numeric(10, 2), nullable=True),
        # Face verification
        sa.Column("selfie_url", sa.String(500), nullable=True),
        sa.Column("face_verified", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("face_confidence", sa.Numeric(4, 3), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.ForeignKeyConstraint(["employee_id"], ["hris_employees.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("employee_id", "date", name="uq_attendance_employee_date"),
    )
    op.create_index("ix_hris_attendance_employee_id", "hris_attendance_records", ["employee_id"])
    op.create_index("ix_hris_attendance_date", "hris_attendance_records", ["date"])

    # ── hris_leave_types ───────────────────────────────────────────────────────
    op.create_table(
        "hris_leave_types",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(20), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("max_days_per_year", sa.Integer(), nullable=True),
        sa.Column("is_paid", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("requires_approval", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_leave_type_code"),
    )

    # Seed standard leave types (Indonesian standard)
    op.execute("""
        INSERT INTO hris_leave_types (code, name, max_days_per_year, is_paid, requires_approval, is_active)
        VALUES
            ('TAHUNAN',    'Cuti Tahunan',     12,   true,  true,  true),
            ('SAKIT',      'Cuti Sakit',        14,   true,  false, true),
            ('MELAHIRKAN', 'Cuti Melahirkan',   90,   true,  true,  true),
            ('TANPA_GAJI', 'Cuti Tanpa Gaji',   NULL, false, true,  true)
        ON CONFLICT (code) DO NOTHING
    """)

    # ── hris_leave_balances ────────────────────────────────────────────────────
    op.create_table(
        "hris_leave_balances",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("employee_id", sa.Integer(), nullable=False),
        sa.Column("leave_type_id", sa.Integer(), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("accrued", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("used", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.ForeignKeyConstraint(["employee_id"], ["hris_employees.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["leave_type_id"], ["hris_leave_types.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("employee_id", "leave_type_id", "year", name="uq_leave_balance_emp_type_year"),
    )
    op.create_index("ix_hris_leave_balances_employee_id", "hris_leave_balances", ["employee_id"])

    # ── hris_leave_requests ────────────────────────────────────────────────────
    op.create_table(
        "hris_leave_requests",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("employee_id", sa.Integer(), nullable=False),
        sa.Column("leave_type_id", sa.Integer(), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("days", sa.Integer(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("status", sa.Enum("draft", "submitted", "approved", "rejected", name="leaverequeststatus"), nullable=False),
        sa.Column("approval_chain", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("approval_step", sa.Integer(), nullable=True),
        sa.Column("current_approver_role", sa.String(50), nullable=True),
        sa.Column("approval_history", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("submitted_by", sa.Integer(), nullable=True),
        sa.Column("approved_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.ForeignKeyConstraint(["employee_id"], ["hris_employees.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["leave_type_id"], ["hris_leave_types.id"]),
        sa.ForeignKeyConstraint(["submitted_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["approved_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_hris_leave_requests_employee_id", "hris_leave_requests", ["employee_id"])
    op.create_index("ix_hris_leave_requests_status", "hris_leave_requests", ["status"])


def downgrade() -> None:
    op.drop_table("hris_leave_requests")
    op.drop_table("hris_leave_balances")
    op.drop_table("hris_leave_types")
    op.drop_table("hris_attendance_records")
    op.drop_column("hris_employees", "face_embedding")

    op.execute("DROP TYPE IF EXISTS leaverequeststatus")
    op.execute("DROP TYPE IF EXISTS attendancesource")

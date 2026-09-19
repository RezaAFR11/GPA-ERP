"""Shift schedules, attendance clarification and browser reminders."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "l9a6b7c8d9e0"
down_revision = "k8f5a6b7c8d9"
branch_labels = None
depends_on = None


def timestamps():
    return [sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())]


def upgrade():
    op.create_table("hris_work_shifts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("start_time", sa.String(5), nullable=False),
        sa.Column("break_start_minutes", sa.Integer(), nullable=False),
        sa.Column("grace_minutes", sa.Integer(), nullable=False),
        sa.Column("reminder_minutes", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False), *timestamps())
    op.create_table("hris_shift_assignments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("employee_id", sa.Integer(), sa.ForeignKey("hris_employees.id"), nullable=False),
        sa.Column("shift_id", sa.Integer(), sa.ForeignKey("hris_work_shifts.id"), nullable=False),
        sa.Column("work_location_id", sa.Integer(), sa.ForeignKey("hris_work_locations.id"), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("snapshot", JSONB(), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("employee_id", "date", name="uq_shift_employee_date"), *timestamps())
    op.create_index("ix_hris_shift_assignments_employee_id", "hris_shift_assignments", ["employee_id"])
    op.create_index("ix_hris_shift_assignments_starts_at", "hris_shift_assignments", ["starts_at"])
    op.add_column("hris_attendance_records", sa.Column("schedule_snapshot", JSONB(), nullable=True))
    for name in ("auto_close_at", "auto_closed_at", "reminder_at", "reminder_sent_at"):
        op.add_column("hris_attendance_records", sa.Column(name, sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_hris_attendance_records_auto_close_at", "hris_attendance_records", ["auto_close_at"])
    for name in ("late_minutes", "beyond_grace_minutes"):
        op.add_column("hris_attendance_records", sa.Column(name, sa.Integer(), nullable=False, server_default="0"))
    op.add_column("hris_attendance_records", sa.Column("clarification_status", sa.String(20), nullable=True))
    op.create_table("hris_attendance_clarifications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("attendance_id", sa.Integer(), sa.ForeignKey("hris_attendance_records.id"), nullable=False),
        sa.Column("reason", sa.String(20), nullable=False),
        sa.Column("actual_clock_out", sa.DateTime(timezone=True), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("reviewed_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_note", sa.Text(), nullable=True), *timestamps())
    op.create_index("ix_hris_attendance_clarifications_attendance_id", "hris_attendance_clarifications", ["attendance_id"])
    op.create_table("browser_push_subscriptions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("endpoint", sa.Text(), nullable=False, unique=True),
        sa.Column("keys", JSONB(), nullable=False), *timestamps())
    op.create_index("ix_browser_push_subscriptions_user_id", "browser_push_subscriptions", ["user_id"])
    op.create_table("attendance_push_deliveries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("attendance_id", sa.Integer(), sa.ForeignKey("hris_attendance_records.id"), nullable=False),
        sa.Column("subscription_id", sa.Integer(), sa.ForeignKey("browser_push_subscriptions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("attendance_id", "subscription_id", name="uq_attendance_push_subscription"), *timestamps())


def downgrade():
    for table in ("attendance_push_deliveries", "browser_push_subscriptions", "hris_attendance_clarifications"):
        op.drop_table(table)
    op.drop_index("ix_hris_attendance_records_auto_close_at", table_name="hris_attendance_records")
    for name in ("schedule_snapshot", "auto_close_at", "auto_closed_at", "reminder_at", "reminder_sent_at", "late_minutes", "beyond_grace_minutes", "clarification_status"):
        op.drop_column("hris_attendance_records", name)
    op.drop_table("hris_shift_assignments")
    op.drop_table("hris_work_shifts")

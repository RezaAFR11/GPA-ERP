"""Persistent weekly schedules without an end date."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "m0b7c8d9e0f1"
down_revision = "l9a6b7c8d9e0"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("hris_weekly_schedules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("employee_id", sa.Integer(), sa.ForeignKey("hris_employees.id"), nullable=False, unique=True),
        sa.Column("shift_id", sa.Integer(), sa.ForeignKey("hris_work_shifts.id"), nullable=False),
        sa.Column("work_location_id", sa.Integer(), sa.ForeignKey("hris_work_locations.id"), nullable=False),
        sa.Column("weekdays", JSONB(), nullable=False),
        sa.Column("snapshot", JSONB(), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))


def downgrade():
    op.drop_table("hris_weekly_schedules")

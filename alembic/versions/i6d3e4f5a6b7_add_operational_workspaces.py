"""add reusable operational workspaces

Revision ID: i6d3e4f5a6b7
Revises: h5c2d3e4f5a6
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "i6d3e4f5a6b7"
down_revision: Union[str, None] = "h5c2d3e4f5a6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "operational_records",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("module", sa.String(length=50), nullable=False),
        sa.Column("record_type", sa.String(length=60), nullable=False),
        sa.Column("reference_no", sa.String(length=100), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="draft"),
        sa.Column("priority", sa.String(length=20), nullable=False, server_default="normal"),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=True),
        sa.Column("partner_name", sa.String(length=255), nullable=True),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("currency", sa.String(length=3), nullable=False, server_default="IDR"),
        sa.Column("progress", sa.Numeric(5, 2), nullable=False, server_default="0"),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("approved_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "details",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "workflow_history",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("module", "reference_no", name="uq_operational_record_reference"),
    )
    op.create_index(
        "ix_operational_records_module_status",
        "operational_records",
        ["module", "status"],
    )
    op.create_index("ix_operational_records_project", "operational_records", ["project_id"])
    op.create_index("ix_operational_records_due_date", "operational_records", ["due_date"])
    op.create_index("ix_operational_records_owner", "operational_records", ["owner_id"])


def downgrade() -> None:
    op.drop_index("ix_operational_records_owner", table_name="operational_records")
    op.drop_index("ix_operational_records_due_date", table_name="operational_records")
    op.drop_index("ix_operational_records_project", table_name="operational_records")
    op.drop_index("ix_operational_records_module_status", table_name="operational_records")
    op.drop_table("operational_records")


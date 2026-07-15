"""Secure settings accounts, shared branding, and email outbox.

Revision ID: h4b1c2d3e4f5
Revises: h3a0b1c2d3e4
Create Date: 2026-07-15
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "h4b1c2d3e4f5"
down_revision = "h3a0b1c2d3e4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    table_names = set(inspector.get_table_names())

    user_columns = {column["name"] for column in inspector.get_columns("users")}
    if "token_version" not in user_columns:
        op.add_column(
            "users",
            sa.Column("token_version", sa.Integer(), nullable=False, server_default="0"),
        )
        op.alter_column("users", "token_version", server_default=None)

    if "workspace_branding" not in table_names:
        op.create_table(
            "workspace_branding",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("logo", sa.String(length=12), nullable=False, server_default="GP"),
            sa.Column("title", sa.String(length=80), nullable=False, server_default="GPA"),
            sa.Column("subtitle", sa.String(length=120), nullable=False, server_default="Cost Control"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )

    if "email_outbox" not in table_names:
        op.create_table(
            "email_outbox",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("to_email", sa.String(length=320), nullable=False),
            sa.Column("subject", sa.String(length=200), nullable=False),
            sa.Column("body_html", sa.Text(), nullable=False),
            sa.Column("body_text", sa.Text(), nullable=True),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
            sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_email_outbox_to_email", "email_outbox", ["to_email"])
        op.create_index("ix_email_outbox_status", "email_outbox", ["status"])
        op.create_index("ix_email_outbox_pending", "email_outbox", ["status", "created_at"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    table_names = set(inspector.get_table_names())

    if "email_outbox" in table_names:
        op.drop_table("email_outbox")
    if "workspace_branding" in table_names:
        op.drop_table("workspace_branding")

    user_columns = {column["name"] for column in inspector.get_columns("users")}
    if "token_version" in user_columns:
        op.drop_column("users", "token_version")

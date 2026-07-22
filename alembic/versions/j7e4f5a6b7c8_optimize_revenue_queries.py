"""Add indexes used by revenue list, filters, and project lookup.

Revision ID: j7e4f5a6b7c8
Revises: i6d3e4f5a6b7
Create Date: 2026-07-22
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "j7e4f5a6b7c8"
down_revision = "i6d3e4f5a6b7"
branch_labels = None
depends_on = None


def _ensure_index(
    table_name: str,
    index_name: str,
    columns: list[str],
    **dialect_options,
) -> None:
    inspector = sa.inspect(op.get_bind())
    existing = {index["name"] for index in inspector.get_indexes(table_name)}
    if index_name not in existing:
        op.create_index(
            index_name,
            table_name,
            columns,
            unique=False,
            **dialect_options,
        )


def upgrade() -> None:
    # Trigram indexes keep contains-search responsive as invoice history grows.
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    _ensure_index("projects", "ix_projects_archived_code", ["is_archived", "code"])
    _ensure_index("account_receivables", "ix_ar_status", ["status"])
    _ensure_index("account_receivables", "ix_ar_actual_payment", ["actual_payment"])
    _ensure_index("account_receivables", "ix_ar_amount", ["amount"])
    _ensure_index("account_receivables", "ix_ar_due_date", ["due_date"])
    _ensure_index("account_receivables", "ix_ar_customer_name", ["customer_name"])
    _ensure_index(
        "account_receivables",
        "ix_ar_invoice_no_trgm",
        ["invoice_no"],
        postgresql_using="gin",
        postgresql_ops={"invoice_no": "gin_trgm_ops"},
    )
    _ensure_index(
        "account_receivables",
        "ix_ar_customer_name_trgm",
        ["customer_name"],
        postgresql_using="gin",
        postgresql_ops={"customer_name": "gin_trgm_ops"},
    )


def downgrade() -> None:
    for index_name, table_name in (
        ("ix_ar_customer_name_trgm", "account_receivables"),
        ("ix_ar_invoice_no_trgm", "account_receivables"),
        ("ix_ar_customer_name", "account_receivables"),
        ("ix_ar_due_date", "account_receivables"),
        ("ix_ar_amount", "account_receivables"),
        ("ix_ar_actual_payment", "account_receivables"),
        ("ix_ar_status", "account_receivables"),
        ("ix_projects_archived_code", "projects"),
    ):
        op.drop_index(index_name, table_name=table_name, if_exists=True)

    # pg_trgm may be shared by other application indexes, so keep the extension.

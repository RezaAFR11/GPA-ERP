"""Add structured client PO lines, payment terms, and protected attachments.

Revision ID: k8f5a6b7c8d9
Revises: j7e4f5a6b7c8
Create Date: 2026-07-24
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "k8f5a6b7c8d9"
down_revision = "j7e4f5a6b7c8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "client_po_line_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "operational_record_id",
            sa.Integer(),
            sa.ForeignKey("operational_records.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("item_no", sa.String(length=30), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("manufacturer", sa.String(length=120), nullable=True),
        sa.Column("model", sa.String(length=120), nullable=True),
        sa.Column("quantity", sa.Numeric(18, 4), nullable=False),
        sa.Column("uom", sa.String(length=20), nullable=False),
        sa.Column("unit_price", sa.Numeric(18, 2), nullable=False),
        sa.Column("line_total", sa.Numeric(18, 2), nullable=False),
        sa.Column("technical_specs", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("operational_record_id", "sequence", name="uq_client_po_line_sequence"),
        sa.UniqueConstraint("operational_record_id", "item_no", name="uq_client_po_line_item_no"),
    )
    op.create_index("ix_client_po_line_items_operational_record_id", "client_po_line_items", ["operational_record_id"])

    op.create_table(
        "client_po_payment_terms",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "operational_record_id",
            sa.Integer(),
            sa.ForeignKey("operational_records.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("percentage", sa.Numeric(5, 2), nullable=False),
        sa.Column("trigger", sa.Text(), nullable=False),
        sa.Column("calculation_basis", sa.String(length=20), nullable=False, server_default="grand_total"),
        sa.Column("dpp_amount", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("tax_amount", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("gross_amount", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="planned"),
        sa.Column("invoice_no", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("operational_record_id", "sequence", name="uq_client_po_payment_sequence"),
    )
    op.create_index("ix_client_po_payment_terms_operational_record_id", "client_po_payment_terms", ["operational_record_id"])
    op.create_index("ix_client_po_payment_status", "client_po_payment_terms", ["status"])

    op.create_table(
        "operational_attachments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "operational_record_id",
            sa.Integer(),
            sa.ForeignKey("operational_records.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("doc_type", sa.String(length=50), nullable=False, server_default="supporting_document"),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("reference_no", sa.String(length=100), nullable=True),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("stored_filename", sa.String(length=255), nullable=False),
        sa.Column("file_path", sa.String(length=2048), nullable=False),
        sa.Column("content_type", sa.String(length=100), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=False),
        sa.Column("is_confidential", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("uploaded_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_operational_attachments_operational_record_id", "operational_attachments", ["operational_record_id"])
    op.create_index(
        "ix_operational_attachments_record_type",
        "operational_attachments",
        ["operational_record_id", "doc_type"],
    )


def downgrade() -> None:
    op.drop_table("operational_attachments")
    op.drop_table("client_po_payment_terms")
    op.drop_table("client_po_line_items")

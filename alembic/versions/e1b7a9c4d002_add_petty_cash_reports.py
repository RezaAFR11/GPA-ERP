"""add_petty_cash_reports

Revision ID: e1b7a9c4d002
Revises: d2a7f4c9b001
Create Date: 2026-05-13 22:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "e1b7a9c4d002"
down_revision: Union[str, None] = "d2a7f4c9b001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "petty_cash_reports",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("report_no", sa.String(100), nullable=False),
        sa.Column("month", sa.String(7), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("cost_code_id", sa.Integer(), nullable=False),
        sa.Column("cost_centre_id", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(255), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("status", sa.Enum("DRAFT", "POSTED", "VOID", name="pettycashreportstatus"), nullable=False),
        sa.Column("total_amount", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["cost_code_id"], ["cost_codes.id"]),
        sa.ForeignKeyConstraint(["cost_centre_id"], ["cost_centres.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("report_no"),
    )
    op.create_index(op.f("ix_petty_cash_reports_report_no"), "petty_cash_reports", ["report_no"], unique=True)
    op.create_index(op.f("ix_petty_cash_reports_month"), "petty_cash_reports", ["month"])
    op.create_index(op.f("ix_petty_cash_reports_project_id"), "petty_cash_reports", ["project_id"])
    op.create_index("ix_petty_cash_reports_project_month", "petty_cash_reports", ["project_id", "month"])
    op.create_index("ix_petty_cash_reports_status", "petty_cash_reports", ["status"])

    op.create_table(
        "petty_cash_report_lines",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("report_id", sa.Integer(), nullable=False),
        sa.Column("line_no", sa.Integer(), nullable=False),
        sa.Column("spent_on", sa.Date(), nullable=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("receipt_url", sa.String(2048), nullable=True),
        sa.Column("source", sa.String(50), nullable=True),
        sa.Column("ocr_text", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["report_id"], ["petty_cash_reports.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_petty_cash_report_lines_report_id"), "petty_cash_report_lines", ["report_id"])
    op.create_index("ix_petty_cash_lines_report_line", "petty_cash_report_lines", ["report_id", "line_no"])

    op.add_column("expenses", sa.Column("petty_cash_line_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_expenses_petty_cash_line_id",
        "expenses",
        "petty_cash_report_lines",
        ["petty_cash_line_id"],
        ["id"],
    )
    op.create_index("ix_expenses_petty_cash_line", "expenses", ["petty_cash_line_id"])


def downgrade() -> None:
    op.drop_index("ix_expenses_petty_cash_line", table_name="expenses")
    op.drop_constraint("fk_expenses_petty_cash_line_id", "expenses", type_="foreignkey")
    op.drop_column("expenses", "petty_cash_line_id")
    op.drop_index("ix_petty_cash_lines_report_line", table_name="petty_cash_report_lines")
    op.drop_index(op.f("ix_petty_cash_report_lines_report_id"), table_name="petty_cash_report_lines")
    op.drop_table("petty_cash_report_lines")
    op.drop_index("ix_petty_cash_reports_status", table_name="petty_cash_reports")
    op.drop_index("ix_petty_cash_reports_project_month", table_name="petty_cash_reports")
    op.drop_index(op.f("ix_petty_cash_reports_project_id"), table_name="petty_cash_reports")
    op.drop_index(op.f("ix_petty_cash_reports_month"), table_name="petty_cash_reports")
    op.drop_index(op.f("ix_petty_cash_reports_report_no"), table_name="petty_cash_reports")
    op.drop_table("petty_cash_reports")
    op.execute("DROP TYPE IF EXISTS pettycashreportstatus")

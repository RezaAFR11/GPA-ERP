"""add hris payroll

Revision ID: g3c4d5e6f7a8
Revises: g2b3c4d5e6f7
Create Date: 2026-05-16 00:01:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "g3c4d5e6f7a8"
down_revision = "g2b3c4d5e6f7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Enums ──────────────────────────────────────────────────────────────────
    for name, values in [
        ("salarycomponenttype", ["BASIC", "ALLOWANCE", "DEDUCTION", "BPJS", "TAX"]),
        ("payrollstatus",       ["OPEN", "LOCKED", "POSTED"]),
        ("pph21method",         ["GROSS_UP", "NETTO"]),
    ]:
        postgresql.ENUM(*values, name=name).create(op.get_bind(), checkfirst=True)

    # ── hris_salary_components ─────────────────────────────────────────────────
    op.create_table(
        "hris_salary_components",
        sa.Column("id",             sa.Integer(),    primary_key=True),
        sa.Column("code",           sa.String(20),   nullable=False),
        sa.Column("name",           sa.String(100),  nullable=False),
        sa.Column("component_type", sa.Enum("BASIC","ALLOWANCE","DEDUCTION","BPJS","TAX", name="salarycomponenttype"), nullable=False),
        sa.Column("is_taxable",     sa.Boolean(),    nullable=False, server_default=sa.text("true")),
        sa.Column("is_active",      sa.Boolean(),    nullable=False, server_default=sa.text("true")),
        sa.Column("created_at",     sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at",     sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.UniqueConstraint("code", name="uq_salary_component_code"),
    )

    # Seed standard components
    op.execute("""
        INSERT INTO hris_salary_components (code, name, component_type, is_taxable, is_active)
        VALUES
            ('BASIC',      'Gaji Pokok',          'BASIC',     true,  true),
            ('TRANSPORT',  'Tunjangan Transport',  'ALLOWANCE', false, true),
            ('MEAL',       'Tunjangan Makan',      'ALLOWANCE', false, true),
            ('POSITION',   'Tunjangan Jabatan',    'ALLOWANCE', true,  true),
            ('BPJS_TK_EE', 'BPJS TK Karyawan',    'BPJS',      false, true),
            ('BPJS_KES_EE','BPJS Kes Karyawan',    'BPJS',      false, true),
            ('PPH21',      'PPh 21',               'TAX',       false, true)
        ON CONFLICT (code) DO NOTHING
    """)

    # ── hris_salary_assignments ────────────────────────────────────────────────
    op.create_table(
        "hris_salary_assignments",
        sa.Column("id",             sa.Integer(),         primary_key=True),
        sa.Column("employee_id",    sa.Integer(),         nullable=False),
        sa.Column("component_id",   sa.Integer(),         nullable=False),
        sa.Column("amount",         sa.Numeric(18, 2),    nullable=False),
        sa.Column("effective_from", sa.Date(),            nullable=False),
        sa.Column("effective_to",   sa.Date(),            nullable=True),
        sa.Column("created_at",     sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at",     sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.ForeignKeyConstraint(["employee_id"],  ["hris_employees.id"],        ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["component_id"], ["hris_salary_components.id"]),
    )
    op.create_index("ix_salary_assignments_emp", "hris_salary_assignments", ["employee_id"])

    # ── hris_payroll_periods ───────────────────────────────────────────────────
    op.create_table(
        "hris_payroll_periods",
        sa.Column("id",        sa.Integer(),  primary_key=True),
        sa.Column("year",      sa.Integer(),  nullable=False),
        sa.Column("month",     sa.Integer(),  nullable=False),
        sa.Column("status",    sa.Enum("OPEN","LOCKED","POSTED", name="payrollstatus"), nullable=False, server_default="OPEN"),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("locked_by", sa.Integer(),  nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.ForeignKeyConstraint(["locked_by"], ["users.id"]),
        sa.UniqueConstraint("year", "month", name="uq_payroll_period_ym"),
    )

    # ── hris_payroll_runs ──────────────────────────────────────────────────────
    op.create_table(
        "hris_payroll_runs",
        sa.Column("id",                  sa.Integer(),       primary_key=True),
        sa.Column("period_id",           sa.Integer(),       nullable=False),
        sa.Column("employee_id",         sa.Integer(),       nullable=False),
        sa.Column("gross_salary",        sa.Numeric(18, 2),  nullable=False, server_default=sa.text("0")),
        sa.Column("bpjs_tk_employee",    sa.Numeric(18, 2),  nullable=False, server_default=sa.text("0")),
        sa.Column("bpjs_tk_employer",    sa.Numeric(18, 2),  nullable=False, server_default=sa.text("0")),
        sa.Column("bpjs_kes_employee",   sa.Numeric(18, 2),  nullable=False, server_default=sa.text("0")),
        sa.Column("bpjs_kes_employer",   sa.Numeric(18, 2),  nullable=False, server_default=sa.text("0")),
        sa.Column("pph21_amount",        sa.Numeric(18, 2),  nullable=False, server_default=sa.text("0")),
        sa.Column("pph21_method",        sa.Enum("GROSS_UP","NETTO", name="pph21method"), nullable=False, server_default="NETTO"),
        sa.Column("net_salary",          sa.Numeric(18, 2),  nullable=False, server_default=sa.text("0")),
        sa.Column("thr_amount",          sa.Numeric(18, 2),  nullable=True),
        sa.Column("components_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("cost_centre_id",      sa.Integer(),       nullable=True),
        sa.Column("expense_id",          sa.Integer(),       nullable=True),
        sa.Column("created_at",          sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at",          sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.ForeignKeyConstraint(["period_id"],      ["hris_payroll_periods.id"]),
        sa.ForeignKeyConstraint(["employee_id"],    ["hris_employees.id"]),
        sa.ForeignKeyConstraint(["cost_centre_id"], ["cost_centres.id"]),
        sa.ForeignKeyConstraint(["expense_id"],     ["expenses.id"]),
        sa.UniqueConstraint("period_id", "employee_id", name="uq_payroll_run_period_emp"),
    )
    op.create_index("ix_payroll_runs_period_id",   "hris_payroll_runs", ["period_id"])
    op.create_index("ix_payroll_runs_employee_id", "hris_payroll_runs", ["employee_id"])

    # ── hris_payslips ──────────────────────────────────────────────────────────
    op.create_table(
        "hris_payslips",
        sa.Column("id",           sa.Integer(),  primary_key=True),
        sa.Column("run_id",       sa.Integer(),  nullable=False, unique=True),
        sa.Column("pdf_url",      sa.String(500), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at",   sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at",   sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.ForeignKeyConstraint(["run_id"], ["hris_payroll_runs.id"]),
    )


def downgrade() -> None:
    op.drop_table("hris_payslips")
    op.drop_table("hris_payroll_runs")
    op.drop_table("hris_payroll_periods")
    op.drop_table("hris_salary_assignments")
    op.drop_table("hris_salary_components")
    op.execute("DROP TYPE IF EXISTS pph21method")
    op.execute("DROP TYPE IF EXISTS payrollstatus")
    op.execute("DROP TYPE IF EXISTS salarycomponenttype")

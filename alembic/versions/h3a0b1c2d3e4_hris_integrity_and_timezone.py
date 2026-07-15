"""HRIS integrity constraints, recruitment link, and work timezone.

Revision ID: h3a0b1c2d3e4
Revises: h2f9a0b1c2d3
Create Date: 2026-07-13
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "h3a0b1c2d3e4"
down_revision = "h2f9a0b1c2d3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    table_names = set(inspector.get_table_names())

    # Older local installations created these enhancement tables through
    # create_all. A fresh migration-only database does not have them yet; the
    # production-alignment revision creates them after this legacy revision.
    if "hris_work_locations" in table_names:
        work_location_columns = {
            column["name"] for column in inspector.get_columns("hris_work_locations")
        }
        if "timezone_name" not in work_location_columns:
            op.add_column(
                "hris_work_locations",
                sa.Column(
                    "timezone_name",
                    sa.String(length=64),
                    nullable=False,
                    server_default="Asia/Jakarta",
                ),
            )
            op.alter_column("hris_work_locations", "timezone_name", server_default=None)

    salary_unique_constraints = inspector.get_unique_constraints("hris_salary_assignments")
    if not any(
        set(constraint.get("column_names") or [])
        == {"employee_id", "component_id", "effective_from"}
        for constraint in salary_unique_constraints
    ):
        op.create_unique_constraint(
            "uq_salary_assignment_effective_start",
            "hris_salary_assignments",
            ["employee_id", "component_id", "effective_from"],
        )

    applicant_columns = {
        column["name"] for column in inspector.get_columns("hris_applicants")
    }
    if "employee_id" not in applicant_columns:
        op.add_column(
            "hris_applicants",
            sa.Column("employee_id", sa.Integer(), nullable=True),
        )
    applicant_foreign_keys = inspector.get_foreign_keys("hris_applicants")
    if not any(
        constraint.get("constrained_columns") == ["employee_id"]
        for constraint in applicant_foreign_keys
    ):
        op.create_foreign_key(
            "fk_hris_applicants_employee_id",
            "hris_applicants",
            "hris_employees",
            ["employee_id"],
            ["id"],
        )
    applicant_unique_constraints = inspector.get_unique_constraints("hris_applicants")
    if not any(
        constraint.get("column_names") == ["employee_id"]
        for constraint in applicant_unique_constraints
    ):
        op.create_unique_constraint(
            "uq_hris_applicants_employee_id",
            "hris_applicants",
            ["employee_id"],
        )

    if "hris_overtime_requests" in table_names:
        overtime_columns = {
            column["name"] for column in inspector.get_columns("hris_overtime_requests")
        }
        if "attendance_id" not in overtime_columns:
            op.add_column(
                "hris_overtime_requests",
                sa.Column("attendance_id", sa.Integer(), nullable=True),
            )
            op.create_foreign_key(
                "fk_hris_overtime_requests_attendance_id",
                "hris_overtime_requests",
                "hris_attendance_records",
                ["attendance_id"],
                ["id"],
            )
        elif not any(
            constraint.get("constrained_columns") == ["attendance_id"]
            for constraint in inspector.get_foreign_keys("hris_overtime_requests")
        ):
            op.create_foreign_key(
                "fk_hris_overtime_requests_attendance_id",
                "hris_overtime_requests",
                "hris_attendance_records",
                ["attendance_id"],
                ["id"],
            )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    table_names = set(inspector.get_table_names())
    if "hris_overtime_requests" in table_names:
        overtime_columns = {
            column["name"]
            for column in inspector.get_columns("hris_overtime_requests")
        }
    else:
        overtime_columns = set()
    if "attendance_id" in overtime_columns:
        foreign_keys = {
            constraint["name"]
            for constraint in inspector.get_foreign_keys("hris_overtime_requests")
        }
        if "fk_hris_overtime_requests_attendance_id" in foreign_keys:
            op.drop_constraint(
                "fk_hris_overtime_requests_attendance_id",
                "hris_overtime_requests",
                type_="foreignkey",
            )
        op.drop_column("hris_overtime_requests", "attendance_id")
    applicant_unique_names = {
        constraint["name"]
        for constraint in inspector.get_unique_constraints("hris_applicants")
    }
    if "uq_hris_applicants_employee_id" in applicant_unique_names:
        op.drop_constraint("uq_hris_applicants_employee_id", "hris_applicants", type_="unique")
    applicant_foreign_key_names = {
        constraint["name"]
        for constraint in inspector.get_foreign_keys("hris_applicants")
    }
    if "fk_hris_applicants_employee_id" in applicant_foreign_key_names:
        op.drop_constraint("fk_hris_applicants_employee_id", "hris_applicants", type_="foreignkey")
    applicant_columns = {
        column["name"] for column in inspector.get_columns("hris_applicants")
    }
    if "employee_id" in applicant_columns:
        op.drop_column("hris_applicants", "employee_id")

    salary_unique_names = {
        constraint["name"]
        for constraint in inspector.get_unique_constraints("hris_salary_assignments")
    }
    if "uq_salary_assignment_effective_start" in salary_unique_names:
        op.drop_constraint(
            "uq_salary_assignment_effective_start",
            "hris_salary_assignments",
            type_="unique",
        )
    work_location_columns = (
        {
            column["name"]
            for column in inspector.get_columns("hris_work_locations")
        }
        if "hris_work_locations" in table_names
        else set()
    )
    if "timezone_name" in work_location_columns:
        op.drop_column("hris_work_locations", "timezone_name")

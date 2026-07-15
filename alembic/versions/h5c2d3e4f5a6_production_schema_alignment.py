"""Align the production schema with the current application models.

Revision ID: h5c2d3e4f5a6
Revises: h4b1c2d3e4f5
Create Date: 2026-07-15

Historically, several HRIS enhancement tables and columns were created by the
development startup bridge. This revision makes the same schema available to a
fresh migration-only production database and remains safe for existing local
databases where those objects already exist.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "h5c2d3e4f5a6"
down_revision = "h4b1c2d3e4f5"
branch_labels = None
depends_on = None


def _inspector() -> sa.Inspector:
    return sa.inspect(op.get_bind())


def _has_table(table_name: str) -> bool:
    return table_name in set(_inspector().get_table_names())


def _columns(table_name: str) -> set[str]:
    return {column["name"] for column in _inspector().get_columns(table_name)}


def _ensure_index(
    table_name: str,
    index_name: str,
    columns: list[str],
    *,
    unique: bool = False,
) -> None:
    names = {index["name"] for index in _inspector().get_indexes(table_name)}
    if index_name not in names:
        op.create_index(index_name, table_name, columns, unique=unique)


def _ensure_fk(
    table_name: str,
    column_name: str,
    target_table: str,
    constraint_name: str,
) -> None:
    foreign_keys = _inspector().get_foreign_keys(table_name)
    if not any(fk.get("constrained_columns") == [column_name] for fk in foreign_keys):
        op.create_foreign_key(
            constraint_name,
            table_name,
            target_table,
            [column_name],
            ["id"],
        )


def upgrade() -> None:
    # Role aliases used by the application were previously added only by the
    # local startup bridge.
    op.execute("ALTER TYPE rolename ADD VALUE IF NOT EXISTS 'PROJECT_CONTROL'")
    op.execute("ALTER TYPE rolename ADD VALUE IF NOT EXISTS 'HR'")

    if "must_change_password" not in _columns("users"):
        op.add_column(
            "users",
            sa.Column(
                "must_change_password",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
        )

    work_location_type = postgresql.ENUM(
        "home_office", "site", "other",
        name="worklocationtype",
        create_type=False,
    )
    work_location_type.create(op.get_bind(), checkfirst=True)
    if not _has_table("hris_work_locations"):
        op.create_table(
            "hris_work_locations",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("name", sa.String(255), nullable=False),
            sa.Column("location_type", work_location_type, nullable=False),
            sa.Column("latitude", sa.Numeric(9, 6), nullable=False),
            sa.Column("longitude", sa.Numeric(9, 6), nullable=False),
            sa.Column("radius_meters", sa.Integer(), nullable=False, server_default="100"),
            sa.Column("timezone_name", sa.String(64), nullable=False, server_default="Asia/Jakarta"),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )

    role_type = postgresql.ENUM(name="rolename", create_type=False)
    if not _has_table("hris_work_groups"):
        op.create_table(
            "hris_work_groups",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("name", sa.String(255), nullable=False),
            sa.Column("role", role_type, nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("name", name="hris_work_groups_name_key"),
        )

    employee_columns = _columns("hris_employees")
    if "work_location_id" not in employee_columns:
        op.add_column("hris_employees", sa.Column("work_location_id", sa.Integer(), nullable=True))
    _ensure_fk(
        "hris_employees", "work_location_id", "hris_work_locations",
        "fk_hris_employees_work_location_id",
    )
    if "work_group_id" not in employee_columns:
        op.add_column("hris_employees", sa.Column("work_group_id", sa.Integer(), nullable=True))
    _ensure_fk(
        "hris_employees", "work_group_id", "hris_work_groups",
        "fk_hris_employees_work_group_id",
    )
    if "ptkp_status" not in employee_columns:
        op.add_column(
            "hris_employees",
            sa.Column("ptkp_status", sa.String(10), nullable=True, server_default="TK/0"),
        )
    _ensure_index(
        "hris_employees", "ix_hris_employees_work_location", ["work_location_id"]
    )
    _ensure_index(
        "hris_employees", "ix_hris_employees_work_group", ["work_group_id"]
    )

    attendance_columns = _columns("hris_attendance_records")
    if "location_ok" not in attendance_columns:
        op.add_column("hris_attendance_records", sa.Column("location_ok", sa.Boolean(), nullable=True))
    if "location_distance_m" not in attendance_columns:
        op.add_column(
            "hris_attendance_records",
            sa.Column("location_distance_m", sa.Numeric(10, 1), nullable=True),
        )
    if "matched_work_location_id" not in attendance_columns:
        op.add_column(
            "hris_attendance_records",
            sa.Column("matched_work_location_id", sa.Integer(), nullable=True),
        )
    _ensure_fk(
        "hris_attendance_records",
        "matched_work_location_id",
        "hris_work_locations",
        "fk_hris_attendance_matched_work_location_id",
    )

    if not _has_table("hris_holiday_calendar"):
        op.create_table(
            "hris_holiday_calendar",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("date", sa.Date(), nullable=False),
            sa.Column("name", sa.String(255), nullable=False),
            sa.Column("is_national", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("year", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
    _ensure_index(
        "hris_holiday_calendar", "ix_hris_holiday_calendar_date", ["date"], unique=True
    )
    _ensure_index("hris_holiday_calendar", "ix_hris_holiday_calendar_year", ["year"])
    _ensure_index("hris_holiday_calendar", "ix_holiday_date", ["date"])
    _ensure_index("hris_holiday_calendar", "ix_holiday_year", ["year"])

    overtime_status = postgresql.ENUM(
        "draft", "submitted", "approved", "rejected",
        name="overtimerequeststatus",
        create_type=False,
    )
    overtime_status.create(op.get_bind(), checkfirst=True)
    if not _has_table("hris_overtime_requests"):
        op.create_table(
            "hris_overtime_requests",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("employee_id", sa.Integer(), nullable=False),
            sa.Column("date", sa.Date(), nullable=False),
            sa.Column("planned_hours", sa.Numeric(4, 1), nullable=False),
            sa.Column("reason", sa.Text(), nullable=False),
            sa.Column("status", overtime_status, nullable=False, server_default="submitted"),
            sa.Column("approved_by", sa.Integer(), nullable=True),
            sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("rejection_reason", sa.Text(), nullable=True),
            sa.Column("attendance_id", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["employee_id"], ["hris_employees.id"]),
            sa.ForeignKeyConstraint(["approved_by"], ["users.id"]),
            sa.ForeignKeyConstraint(["attendance_id"], ["hris_attendance_records.id"]),
        )
    _ensure_index(
        "hris_overtime_requests", "ix_hris_overtime_requests_employee_id", ["employee_id"]
    )
    _ensure_index("hris_overtime_requests", "ix_ot_requests_employee", ["employee_id"])
    _ensure_index("hris_overtime_requests", "ix_ot_requests_status", ["status"])
    _ensure_index("hris_overtime_requests", "ix_ot_requests_date", ["date"])

    data_change_status = postgresql.ENUM(
        "pending", "approved", "rejected",
        name="datachangestatus",
        create_type=False,
    )
    data_change_status.create(op.get_bind(), checkfirst=True)
    if not _has_table("hris_data_change_requests"):
        op.create_table(
            "hris_data_change_requests",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("employee_id", sa.Integer(), nullable=False),
            sa.Column("field_name", sa.String(100), nullable=False),
            sa.Column("old_value", sa.Text(), nullable=True),
            sa.Column("new_value", sa.Text(), nullable=False),
            sa.Column("reason", sa.Text(), nullable=True),
            sa.Column("status", data_change_status, nullable=False, server_default="pending"),
            sa.Column("reviewed_by", sa.Integer(), nullable=True),
            sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("review_note", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["employee_id"], ["hris_employees.id"]),
            sa.ForeignKeyConstraint(["reviewed_by"], ["users.id"]),
        )
    _ensure_index(
        "hris_data_change_requests",
        "ix_hris_data_change_requests_employee_id",
        ["employee_id"],
    )
    _ensure_index("hris_data_change_requests", "ix_data_change_employee", ["employee_id"])
    _ensure_index("hris_data_change_requests", "ix_data_change_status", ["status"])

    leave_type_columns = {column["name"]: column for column in _inspector().get_columns("hris_leave_types")}
    if getattr(leave_type_columns["code"]["type"], "length", None) != 50:
        op.alter_column(
            "hris_leave_types",
            "code",
            existing_type=leave_type_columns["code"]["type"],
            type_=sa.String(50),
            existing_nullable=False,
        )
    if getattr(leave_type_columns["name"]["type"], "length", None) != 255:
        op.alter_column(
            "hris_leave_types",
            "name",
            existing_type=leave_type_columns["name"]["type"],
            type_=sa.String(255),
            existing_nullable=False,
        )

    op.execute("UPDATE hris_leave_requests SET approval_step = 0 WHERE approval_step IS NULL")
    op.alter_column(
        "hris_leave_requests",
        "approval_step",
        existing_type=sa.Integer(),
        nullable=False,
    )

    _ensure_index("hris_interviews", "ix_hris_interviews_applicant_id", ["applicant_id"])
    _ensure_index(
        "hris_onboarding_tasks",
        "ix_hris_onboarding_tasks_applicant_id",
        ["applicant_id"],
    )


def downgrade() -> None:
    # Keep enhancement tables and columns intact: they may contain data from
    # installations where the former development bridge created them before
    # this revision existed. Reverting the revision must not delete that data.
    if _has_table("hris_leave_requests"):
        op.alter_column(
            "hris_leave_requests",
            "approval_step",
            existing_type=sa.Integer(),
            nullable=True,
        )

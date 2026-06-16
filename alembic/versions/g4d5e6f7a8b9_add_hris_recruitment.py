"""add hris recruitment

Revision ID: g4d5e6f7a8b9
Revises: g3c4d5e6f7a8
Create Date: 2026-05-16 00:02:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "g4d5e6f7a8b9"
down_revision = "g3c4d5e6f7a8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── hris_job_postings ──────────────────────────────────────────────────────
    op.create_table(
        "hris_job_postings",
        sa.Column("id",            sa.Integer(),     primary_key=True),
        sa.Column("title",         sa.String(200),   nullable=False),
        sa.Column("department_id", sa.Integer(),     nullable=True),
        sa.Column("grade_id",      sa.Integer(),     nullable=True),
        sa.Column("description",   sa.Text(),        nullable=True),
        sa.Column("requirements",  sa.Text(),        nullable=True),
        sa.Column("status",        sa.Enum("OPEN","CLOSED","ON_HOLD", name="postingstatus"),
                  nullable=False, server_default="OPEN"),
        sa.Column("opened_at",     sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at",     sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by",    sa.Integer(),     nullable=False),
        sa.Column("created_at",    sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at",    sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.ForeignKeyConstraint(["department_id"], ["hris_departments.id"]),
        sa.ForeignKeyConstraint(["grade_id"],      ["hris_job_grades.id"]),
        sa.ForeignKeyConstraint(["created_by"],    ["users.id"]),
    )

    # ── hris_applicants ────────────────────────────────────────────────────────
    op.create_table(
        "hris_applicants",
        sa.Column("id",         sa.Integer(),   primary_key=True),
        sa.Column("posting_id", sa.Integer(),   nullable=False),
        sa.Column("full_name",  sa.String(200), nullable=False),
        sa.Column("email",      sa.String(200), nullable=True),
        sa.Column("phone",      sa.String(30),  nullable=True),
        sa.Column("source",     sa.Enum("JOBSTREET","LINKEDIN","REFERRAL","WALK_IN","OTHER",
                  name="applicantsource"), nullable=False, server_default="OTHER"),
        sa.Column("stage",      sa.Enum("RECEIVED","SCREENING","INTERVIEW","OFFER","HIRED","REJECTED",
                  name="applicantstage"), nullable=False, server_default="RECEIVED"),
        sa.Column("cv_url",     sa.String(500), nullable=True),
        sa.Column("note",       sa.Text(),      nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.ForeignKeyConstraint(["posting_id"], ["hris_job_postings.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_hris_applicants_posting_id", "hris_applicants", ["posting_id"])
    op.create_index("ix_hris_applicants_stage",      "hris_applicants", ["stage"])

    # ── hris_interviews ────────────────────────────────────────────────────────
    op.create_table(
        "hris_interviews",
        sa.Column("id",             sa.Integer(),  primary_key=True),
        sa.Column("applicant_id",   sa.Integer(),  nullable=False),
        sa.Column("scheduled_at",   sa.DateTime(timezone=True), nullable=False),
        sa.Column("interviewer_id", sa.Integer(),  nullable=True),
        sa.Column("result",         sa.Enum("PENDING","PASS","FAIL","HOLD", name="interviewresult"),
                  nullable=False, server_default="PENDING"),
        sa.Column("notes",          sa.Text(),     nullable=True),
        sa.Column("created_at",     sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at",     sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.ForeignKeyConstraint(["applicant_id"],   ["hris_applicants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["interviewer_id"], ["users.id"]),
    )

    # ── hris_onboarding_tasks ──────────────────────────────────────────────────
    op.create_table(
        "hris_onboarding_tasks",
        sa.Column("id",           sa.Integer(),   primary_key=True),
        sa.Column("applicant_id", sa.Integer(),   nullable=False),
        sa.Column("task",         sa.String(300), nullable=False),
        sa.Column("is_completed", sa.Boolean(),   nullable=False, server_default=sa.text("false")),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("assigned_to",  sa.Integer(),   nullable=True),
        sa.Column("sort_order",   sa.Integer(),   nullable=False, server_default=sa.text("0")),
        sa.Column("created_at",   sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at",   sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.ForeignKeyConstraint(["applicant_id"], ["hris_applicants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["assigned_to"],  ["users.id"]),
    )


def downgrade() -> None:
    op.drop_table("hris_onboarding_tasks")
    op.drop_table("hris_interviews")
    op.drop_table("hris_applicants")
    op.drop_table("hris_job_postings")
    op.execute("DROP TYPE IF EXISTS interviewresult")
    op.execute("DROP TYPE IF EXISTS applicantsource")
    op.execute("DROP TYPE IF EXISTS applicantstage")
    op.execute("DROP TYPE IF EXISTS postingstatus")

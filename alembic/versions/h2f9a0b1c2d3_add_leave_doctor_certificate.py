"""add leave categories and doctor certificates

Revision ID: h2f9a0b1c2d3
Revises: h1e8f9a0b1c2
Create Date: 2026-07-13
"""
from __future__ import annotations

from alembic import op


revision = "h2f9a0b1c2d3"
down_revision = "h1e8f9a0b1c2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE hris_leave_types ADD COLUMN IF NOT EXISTS "
        "category VARCHAR(20) NOT NULL DEFAULT 'annual'"
    )
    op.execute(
        "ALTER TABLE hris_leave_types ADD COLUMN IF NOT EXISTS "
        "requires_doctor_cert BOOLEAN NOT NULL DEFAULT FALSE"
    )
    op.execute(
        "ALTER TABLE hris_leave_requests ADD COLUMN IF NOT EXISTS "
        "doctor_cert_url VARCHAR(500)"
    )
    # Older local databases may have received these columns from the startup
    # compatibility bridge before this migration was applied.
    op.execute("UPDATE hris_leave_types SET category = 'annual' WHERE category IS NULL")
    op.execute(
        "UPDATE hris_leave_types SET requires_doctor_cert = FALSE "
        "WHERE requires_doctor_cert IS NULL"
    )
    op.execute("ALTER TABLE hris_leave_types ALTER COLUMN category SET DEFAULT 'annual'")
    op.execute("ALTER TABLE hris_leave_types ALTER COLUMN category SET NOT NULL")
    op.execute(
        "ALTER TABLE hris_leave_types ALTER COLUMN requires_doctor_cert SET DEFAULT FALSE"
    )
    op.execute(
        "ALTER TABLE hris_leave_types ALTER COLUMN requires_doctor_cert SET NOT NULL"
    )
    op.execute("UPDATE hris_leave_types SET category = 'sick', requires_doctor_cert = TRUE WHERE code = 'SAKIT'")
    op.execute("UPDATE hris_leave_types SET category = 'maternity' WHERE code = 'MELAHIRKAN'")
    op.execute("UPDATE hris_leave_types SET category = 'unpaid' WHERE code = 'TANPA_GAJI'")


def downgrade() -> None:
    op.execute("ALTER TABLE hris_leave_requests DROP COLUMN IF EXISTS doctor_cert_url")
    op.execute("ALTER TABLE hris_leave_types DROP COLUMN IF EXISTS requires_doctor_cert")
    op.execute("ALTER TABLE hris_leave_types DROP COLUMN IF EXISTS category")

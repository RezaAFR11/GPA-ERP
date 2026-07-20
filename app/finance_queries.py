"""Reusable finance query helpers.

This module keeps reporting and approval rules out of the HTTP handlers.  The
helpers deliberately return the same payload shapes used by the existing API;
their purpose is to reduce repeated queries, not to redefine finance policy.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import and_, case, func, or_, true
from sqlalchemy.orm import joinedload

from app.models import Expense, ExpenseStatus, Project, RoleName, User, effective_roles


# These groups mirror the existing spending cards. Draft and rejected records
# are excluded until they re-enter the approval workflow.
LOGGED_EXPENSE_STATUSES = (
    ExpenseStatus.SUBMITTED,
    ExpenseStatus.VERIFIED,
    ExpenseStatus.APPROVED,
    ExpenseStatus.PAID,
    ExpenseStatus.HARD_LOCKED,
)
APPROVED_EXPENSE_STATUSES = (
    ExpenseStatus.APPROVED,
    ExpenseStatus.PAID,
    ExpenseStatus.HARD_LOCKED,
)
PAID_EXPENSE_STATUSES = (
    ExpenseStatus.PAID,
    ExpenseStatus.HARD_LOCKED,
)
def expense_response_options():
    """Load nested response fields in the list query instead of per row."""
    return (
        joinedload(Expense.cost_code),
        joinedload(Expense.cost_centre),
        joinedload(Expense.submitter).joinedload(User.role),
    )


def expense_stats_columns():
    """Build all spending-card aggregates for a single SQL statement."""

    def amount_for(statuses: tuple[ExpenseStatus, ...]):
        return func.coalesce(
            func.sum(case((Expense.status.in_(statuses), Expense.amount), else_=0)),
            0,
        )

    def count_for(status: ExpenseStatus):
        return func.coalesce(
            func.sum(case((Expense.status == status, 1), else_=0)),
            0,
        )

    return (
        amount_for(LOGGED_EXPENSE_STATUSES).label("total_logged"),
        amount_for(APPROVED_EXPENSE_STATUSES).label("total_approved"),
        amount_for(PAID_EXPENSE_STATUSES).label("total_paid"),
        *(
            count_for(status).label(f"count_{status.value}")
            for status in ExpenseStatus
        ),
    )


def expense_stats_payload(row: Any) -> dict[str, Any]:
    """Normalize a SQLAlchemy aggregate row to the public ExpenseStats shape."""
    values = row._mapping if hasattr(row, "_mapping") else row
    return {
        "total_logged": Decimal(str(values["total_logged"] or 0)),
        "total_approved": Decimal(str(values["total_approved"] or 0)),
        "total_paid": Decimal(str(values["total_paid"] or 0)),
        "count_by_status": {
            status.value: int(values[f"count_{status.value}"] or 0)
            for status in ExpenseStatus
        },
    }


def project_list_payload(
    project: Project,
    total_revenue: Decimal | int | None,
    total_committed: Decimal | int | None,
) -> dict[str, Any]:
    """Create the unchanged ProjectResponse without loading child collections."""
    revenue = Decimal(str(total_revenue or 0))
    committed = Decimal(str(total_committed or 0))
    return {
        "id": project.id,
        "code": project.code,
        "name": project.name,
        "contract_value": project.contract_value,
        "currency": project.currency,
        "is_archived": project.is_archived,
        "status": project.status,
        "start_date": project.start_date,
        "end_date": project.end_date,
        "imported_at": project.imported_at,
        "created_at": project.created_at,
        "total_revenue": revenue,
        "total_committed": committed,
        "budget": revenue - committed,
    }


def expense_action_queue_clause(role: RoleName, user_id: int):
    """Return the exact records that can produce an Action Center command.

    The frontend still decides which command to display. Filtering candidates
    here avoids downloading the complete historical expense ledger on every
    page that shows the Action Center badge.
    """
    expected_role = Expense.current_approver_role
    role_values = tuple(item.value for item in effective_roles(role))
    is_super_admin = role == RoleName.SUPER_ADMIN
    is_cost_control = role == RoleName.COST_CONTROL

    can_act_as_expected = true() if is_super_admin else expected_role.in_(role_values)
    is_verification_step = expected_role.in_((RoleName.GA.value, RoleName.COST_CONTROL.value))
    can_submit_record = true() if is_super_admin or is_cost_control else Expense.submitted_by == user_id

    return or_(
        and_(
            Expense.status.in_((ExpenseStatus.DRAFT, ExpenseStatus.REJECTED)),
            can_submit_record,
        ),
        and_(
            Expense.status == ExpenseStatus.SUBMITTED,
            is_verification_step,
            can_act_as_expected,
        ),
        and_(
            Expense.status.in_((ExpenseStatus.SUBMITTED, ExpenseStatus.VERIFIED)),
            expected_role.notin_((RoleName.GA.value, RoleName.COST_CONTROL.value)),
            can_act_as_expected,
        ),
        and_(
            Expense.status == ExpenseStatus.APPROVED,
            true() if is_super_admin or role == RoleName.FINANCE else False,
        ),
    )

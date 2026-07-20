"""Expense approval state transitions shared by the HTTP handlers.

Only deterministic workflow mutations live here. Database commits, audit
records, and notifications remain in the router so transaction boundaries stay
visible at the API layer.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.dependencies import get_required_approvers_from_matrix
from app.models import (
    CostCode,
    Expense,
    ExpenseStatus,
    ExpenseType,
    RoleName,
    effective_roles,
)


def build_submission_chain(db: Session, expense: Expense) -> list[str]:
    """Resolve the immutable approval chain captured at submission time."""
    if expense.expense_type == ExpenseType.REIMBURSEMENT:
        # Receipt evidence is reviewed before cost verification and payment.
        return [RoleName.GA.value, RoleName.COST_CONTROL.value, RoleName.FINANCE.value]

    cost_code = db.query(CostCode).filter(CostCode.id == expense.cost_code_id).first()
    chain = get_required_approvers_from_matrix(db, expense.amount, cost_code.category)
    if RoleName.COST_CONTROL.value not in chain:
        chain.insert(0, RoleName.COST_CONTROL.value)
    return chain


def append_history_event(
    expense: Expense,
    actor_id: int,
    action: str,
    note: str | None = None,
) -> None:
    """Append rather than mutate JSON in place so SQLAlchemy detects the change."""
    history = list(expense.approval_history or [])
    history.append({
        "action": action,
        "role": expense.current_approver_role,
        "user_id": actor_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "note": note,
    })
    expense.approval_history = history


def start_approval(
    expense: Expense,
    actor_id: int,
    chain: list[str],
    note: str | None = None,
) -> None:
    expense.status = ExpenseStatus.SUBMITTED
    expense.submitted_by = actor_id
    expense.approval_chain = chain
    expense.approval_step = 0
    expense.current_approver_role = chain[0] if chain else None
    expense.rejection_reason = None
    append_history_event(expense, actor_id, "SUBMIT", note)


def advance_approval_chain(expense: Expense) -> None:
    """Advance one captured chain step and set the corresponding status."""
    chain = expense.approval_chain or []
    expense.approval_step += 1

    if expense.approval_step >= len(chain):
        expense.status = ExpenseStatus.APPROVED
        expense.current_approver_role = None
        return

    next_role = chain[expense.approval_step]
    if chain[expense.approval_step - 1] == RoleName.COST_CONTROL.value:
        expense.status = ExpenseStatus.VERIFIED
    expense.current_approver_role = next_role


def role_can_act(actor_role: RoleName, expected_role: str | None) -> bool:
    """Apply role aliases (HR->GA and Project Control->PM) consistently."""
    return (
        actor_role == RoleName.SUPER_ADMIN
        or expected_role in {role.value for role in effective_roles(actor_role)}
    )


def role_can_reject(actor_role: RoleName, approval_chain: list[str] | None) -> bool:
    allowed_roles = set(approval_chain or []) | {
        RoleName.SUPER_ADMIN.value,
        RoleName.FINANCE.value,
    }
    return any(role.value in allowed_roles for role in effective_roles(actor_role))

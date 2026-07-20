from types import SimpleNamespace

from app.expense_workflow import (
    advance_approval_chain,
    append_history_event,
    role_can_act,
    role_can_reject,
    start_approval,
)
from app.models import ExpenseStatus, RoleName


def expense_stub(**overrides):
    values = {
        "status": ExpenseStatus.DRAFT,
        "submitted_by": None,
        "approval_chain": [],
        "approval_step": 0,
        "current_approver_role": None,
        "rejection_reason": "old rejection",
        "approval_history": [],
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_start_approval_initializes_the_captured_chain_and_history():
    expense = expense_stub()
    chain = [RoleName.COST_CONTROL.value, RoleName.MD.value]

    start_approval(expense, actor_id=9, chain=chain, note="ready")

    assert expense.status == ExpenseStatus.SUBMITTED
    assert expense.submitted_by == 9
    assert expense.approval_chain == chain
    assert expense.approval_step == 0
    assert expense.current_approver_role == RoleName.COST_CONTROL.value
    assert expense.rejection_reason is None
    assert expense.approval_history[-1]["action"] == "SUBMIT"
    assert expense.approval_history[-1]["note"] == "ready"


def test_advance_chain_preserves_verified_then_approved_transitions():
    expense = expense_stub(
        status=ExpenseStatus.SUBMITTED,
        approval_chain=[RoleName.COST_CONTROL.value, RoleName.MD.value],
        current_approver_role=RoleName.COST_CONTROL.value,
    )

    advance_approval_chain(expense)
    assert expense.status == ExpenseStatus.VERIFIED
    assert expense.approval_step == 1
    assert expense.current_approver_role == RoleName.MD.value

    advance_approval_chain(expense)
    assert expense.status == ExpenseStatus.APPROVED
    assert expense.approval_step == 2
    assert expense.current_approver_role is None


def test_history_append_replaces_the_json_list_and_keeps_previous_events():
    previous = {"action": "CREATE"}
    expense = expense_stub(
        current_approver_role=RoleName.GA.value,
        approval_history=[previous],
    )
    original_list = expense.approval_history

    append_history_event(expense, actor_id=3, action="RECEIPT_REVIEW")

    assert expense.approval_history is not original_list
    assert expense.approval_history[0] is previous
    assert expense.approval_history[1]["role"] == RoleName.GA.value


def test_role_aliases_and_rejection_membership_are_preserved():
    assert role_can_act(RoleName.HR, RoleName.GA.value)
    assert role_can_act(RoleName.PROJECT_CONTROL, RoleName.PM.value)
    assert role_can_act(RoleName.SUPER_ADMIN, None)
    assert not role_can_act(RoleName.STAFF, RoleName.GA.value)

    assert role_can_reject(RoleName.PROJECT_CONTROL, [RoleName.PM.value])
    assert role_can_reject(RoleName.FINANCE, [])
    assert not role_can_reject(RoleName.STAFF, [RoleName.MD.value])

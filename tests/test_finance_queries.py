from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

from app.finance_queries import (
    APPROVED_EXPENSE_STATUSES,
    LOGGED_EXPENSE_STATUSES,
    PAID_EXPENSE_STATUSES,
    expense_stats_payload,
    project_list_payload,
)
from app.models import ExpenseStatus, ProjectStatus


def test_finance_status_groups_preserve_existing_card_and_budget_rules():
    assert LOGGED_EXPENSE_STATUSES == (
        ExpenseStatus.SUBMITTED,
        ExpenseStatus.VERIFIED,
        ExpenseStatus.APPROVED,
        ExpenseStatus.PAID,
        ExpenseStatus.HARD_LOCKED,
    )
    assert APPROVED_EXPENSE_STATUSES == (
        ExpenseStatus.APPROVED,
        ExpenseStatus.PAID,
        ExpenseStatus.HARD_LOCKED,
    )
    assert PAID_EXPENSE_STATUSES == (
        ExpenseStatus.PAID,
        ExpenseStatus.HARD_LOCKED,
    )
def test_expense_stats_payload_preserves_public_response_shape():
    aggregate = {
        "total_logged": Decimal("150.25"),
        "total_approved": Decimal("100.00"),
        "total_paid": Decimal("40.00"),
        "count_draft": 1,
        "count_submitted": 2,
        "count_verified": 3,
        "count_approved": 4,
        "count_paid": 5,
        "count_hard_locked": 6,
        "count_rejected": 7,
    }

    assert expense_stats_payload(aggregate) == {
        "total_logged": Decimal("150.25"),
        "total_approved": Decimal("100.00"),
        "total_paid": Decimal("40.00"),
        "count_by_status": {
            "draft": 1,
            "submitted": 2,
            "verified": 3,
            "approved": 4,
            "paid": 5,
            "hard_locked": 6,
            "rejected": 7,
        },
    }


def test_project_list_payload_keeps_values_and_revenue_driven_budget():
    created_at = datetime(2026, 7, 18, tzinfo=timezone.utc)
    project = SimpleNamespace(
        id=7,
        code="UAT-007",
        name="UAT Project",
        contract_value=Decimal("500.00"),
        currency="IDR",
        is_archived=False,
        status=ProjectStatus.ACTIVE,
        start_date=None,
        end_date=None,
        imported_at=None,
        created_at=created_at,
    )

    payload = project_list_payload(project, Decimal("300.00"), Decimal("125.00"))

    assert payload["id"] == 7
    assert payload["code"] == "UAT-007"
    assert payload["contract_value"] == Decimal("500.00")
    assert payload["total_revenue"] == Decimal("300.00")
    assert payload["total_committed"] == Decimal("125.00")
    assert payload["budget"] == Decimal("175.00")
    assert payload["created_at"] is created_at

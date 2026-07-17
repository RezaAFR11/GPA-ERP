from decimal import Decimal

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.menu_permissions import DEFAULT_MENUS, OBSOLETE_MENU_KEYS
from app.operational_modules import MODULE_DEFINITIONS, STATUS_TRANSITIONS, next_status
from app.routers.operations import _validate_domain_fields
from app.schemas import OperationalRecordCreate, OperationalTransition


EXPECTED_MODULES = {
    "procurement",
    "accounts_payable",
    "accounting_tax",
    "project_execution",
    "engineering_documents",
    "quality_control",
    "hse",
    "warehouse_logistics",
    "equipment_assets",
    "contract_management",
    "crm_tender",
    "manpower_operations",
    "budget_bi",
}


def test_all_operational_modules_are_registered_as_active_menus():
    menu_keys = {row[0] for row in DEFAULT_MENUS}
    assert EXPECTED_MODULES == set(MODULE_DEFINITIONS)
    assert EXPECTED_MODULES <= menu_keys
    assert not EXPECTED_MODULES.intersection(OBSOLETE_MENU_KEYS)


def test_workflow_uses_explicit_transitions_and_locks_closed_records():
    assert next_status("draft", "submit") == "submitted"
    assert next_status("submitted", "approve") == "approved"
    assert next_status("approved", "close") == "closed"
    assert next_status("closed", "reopen") is None
    assert STATUS_TRANSITIONS["closed"] == {}


def test_operational_payload_normalizes_currency_and_validates_progress():
    payload = OperationalRecordCreate(
        record_type="purchase_order",
        title="Structural steel purchase",
        currency="idr",
        progress=Decimal("25.50"),
    )
    assert payload.currency == "IDR"
    with pytest.raises(ValidationError):
        OperationalRecordCreate(
            record_type="milestone",
            title="Invalid progress",
            progress=Decimal("101"),
        )


def test_rejection_requires_a_supported_action_value():
    assert OperationalTransition(action="reject", note="Incorrect quantity").action == "reject"
    with pytest.raises(ValidationError):
        OperationalTransition(action="force_approve")


def test_accounts_payable_requires_vendor_and_positive_amount():
    with pytest.raises(HTTPException) as error:
        _validate_domain_fields(
            "accounts_payable",
            "vendor_invoice",
            None,
            Decimal("100"),
            {},
        )
    assert error.value.status_code == 422


def test_journal_totals_must_balance_when_provided():
    with pytest.raises(HTTPException) as error:
        _validate_domain_fields(
            "accounting_tax",
            "journal_entry",
            None,
            Decimal("0"),
            {"debit_total": "100", "credit_total": "90"},
        )
    assert "balance" in str(error.value.detail).lower()


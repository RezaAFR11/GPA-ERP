from decimal import Decimal

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.menu_permissions import DEFAULT_MENUS, OBSOLETE_MENU_KEYS
from app.operational_modules import MODULE_DEFINITIONS, STATUS_TRANSITIONS, next_status
from app.routers.operations import _validate_client_po, _validate_domain_fields
from app.schemas import ClientPODataInput, OperationalRecordCreate, OperationalTransition


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


def _valid_client_po() -> ClientPODataInput:
    return ClientPODataInput(
        line_items=[{
            "sequence": 1,
            "item_no": "001",
            "description": "Gas detector",
            "quantity": "2",
            "uom": "EA",
            "unit_price": "500",
            "line_total": "1000",
        }],
        payment_terms=[
            {
                "sequence": 1,
                "percentage": "40",
                "trigger": "Down payment",
                "dpp_amount": "400",
                "tax_amount": "44",
                "gross_amount": "444",
            },
            {
                "sequence": 2,
                "percentage": "60",
                "trigger": "Delivery",
                "dpp_amount": "600",
                "tax_amount": "66",
                "gross_amount": "666",
            },
        ],
    )


def test_client_po_is_registered_and_commercial_totals_are_validated():
    assert "client_purchase_order" in MODULE_DEFINITIONS["contract_management"].record_types
    _validate_client_po(
        module="contract_management",
        record_type="client_purchase_order",
        project_id=1,
        partner_name="PT Client",
        amount=Decimal("1000"),
        details={"tax_amount": "110", "grand_total": "1110"},
        client_po=_valid_client_po(),
    )


def test_client_po_rejects_boq_total_that_differs_from_dpp():
    client_po = _valid_client_po()
    client_po.line_items[0].line_total = Decimal("900")
    with pytest.raises(HTTPException) as error:
        _validate_client_po(
            module="contract_management",
            record_type="client_purchase_order",
            project_id=1,
            partner_name="PT Client",
            amount=Decimal("1000"),
            details={"tax_amount": "110", "grand_total": "1110"},
            client_po=client_po,
        )
    assert "quantity times unit price" in str(error.value.detail)


def test_client_po_submission_requires_delivery_boq_and_payment_schedule():
    with pytest.raises(HTTPException) as error:
        _validate_client_po(
            module="contract_management",
            record_type="client_purchase_order",
            project_id=1,
            partner_name="PT Client",
            amount=Decimal("1000"),
            details={"tax_amount": "110", "grand_total": "1110", "po_date": "2026-07-24"},
            require_complete=True,
        )
    assert "delivery term" in str(error.value.detail)

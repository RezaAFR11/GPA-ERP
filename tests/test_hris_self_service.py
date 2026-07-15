from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.hris_access import ensure_employee_can_use_self_service
from app.models import EmployeeStatus
from app.routers.hris_attendance import router as attendance_router
from app.routers.hris_self_service import _payroll_earnings, _snapshot_component_items
from app.schemas import DataChangeRequestCreate, OvertimeRequestCreate


class EmptyDatabase:
    def get(self, _model, _identifier):
        return None


def test_itemized_snapshot_is_read_as_items_not_dictionary_keys() -> None:
    items = _snapshot_component_items(
        {
            "components": [
                {
                    "component_id": None,
                    "component_name": "Gaji Pokok",
                    "component_type": "BASIC",
                    "is_taxable": True,
                    "amount": 10_000_000,
                },
            ],
            "BASIC": 10_000_000,
        },
        EmptyDatabase(),
    )

    assert len(items) == 1
    assert items[0]["component_name"] == "Gaji Pokok"
    assert items[0]["amount"] == 10_000_000


def test_legacy_dictionary_snapshot_is_normalized() -> None:
    items = _snapshot_component_items(
        {"BASIC": 8_000_000, "ALLOWANCE": 1_000_000, "DEDUCTION": 0},
        EmptyDatabase(),
    )

    assert [item["component_type"] for item in items] == ["BASIC", "ALLOWANCE"]


def test_payroll_earnings_include_tax_allowance_and_thr() -> None:
    run = SimpleNamespace(
        gross_salary=10_000_000,
        thr_amount=5_000_000,
        components_snapshot={"tunjangan_pajak": 750_000},
    )

    tax_allowance, total_earnings = _payroll_earnings(run)

    assert tax_allowance == 750_000
    assert total_earnings == 15_750_000


def test_terminated_employee_cannot_use_self_service() -> None:
    employee = SimpleNamespace(status=EmployeeStatus.TERMINATED)

    with pytest.raises(HTTPException) as exc_info:
        ensure_employee_can_use_self_service(employee)

    assert exc_info.value.status_code == 403


@pytest.mark.parametrize(
    ("field_name", "new_value"),
    [
        ("email", "not-an-email"),
        ("phone", "12"),
        ("bank_account", "account-abc"),
        ("npwp", "ABC12.345.678.9-012.345"),
        ("bpjs_tk_no", "123"),
        ("unknown_field", "value"),
    ],
)
def test_data_change_request_rejects_invalid_values(field_name: str, new_value: str) -> None:
    with pytest.raises(ValidationError):
        DataChangeRequestCreate(field_name=field_name, new_value=new_value)


def test_data_change_request_normalizes_valid_value() -> None:
    request = DataChangeRequestCreate(
        field_name=" PHONE ",
        new_value=" +62 812-3456-7890 ",
        reason=" Nomor baru ",
    )

    assert request.field_name == "phone"
    assert request.new_value == "+62 812-3456-7890"
    assert request.reason == "Nomor baru"


def test_overtime_request_rejects_blank_reason() -> None:
    with pytest.raises(ValidationError):
        OvertimeRequestCreate(date="2026-07-15", planned_hours=2, reason="   ")


def test_shared_attendance_router_declares_menu_access_per_endpoint() -> None:
    for route in attendance_router.routes:
        menu_dependencies = [
            dependency.call.required_menu_keys
            for dependency in route.dependant.dependencies
            if hasattr(dependency.call, "required_menu_keys")
        ]
        assert menu_dependencies, f"{sorted(route.methods)} {route.path} has no menu guard"

    by_route = {
        (method, route.path): set().union(*[
            dependency.call.required_menu_keys
            for dependency in route.dependant.dependencies
            if hasattr(dependency.call, "required_menu_keys")
        ])
        for route in attendance_router.routes
        for method in route.methods
    }
    assert by_route[("GET", "/hris/attendance")] == {"hris_attendance"}
    assert by_route[("GET", "/hris/leave-requests")] == {"hris_leave"}
    assert by_route[("POST", "/hris/work-locations")] == {"hris_settings"}

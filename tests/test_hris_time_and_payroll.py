import os
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

os.environ["DEBUG"] = "true"

from app.hris_time import local_date_for_employee, local_date_from_browser_offset
from app.routers.hris_payroll import _calculate_net_pay, _cap_overtime_hours


def test_work_location_timezone_overrides_browser_offset() -> None:
    employee = SimpleNamespace(
        work_location=SimpleNamespace(timezone_name="Asia/Makassar"),
    )
    utc_now = datetime(2026, 7, 7, 16, 30, tzinfo=timezone.utc)

    assert local_date_for_employee(employee, -420, utc_now).isoformat() == "2026-07-08"


def test_browser_timezone_is_fallback_without_work_location() -> None:
    employee = SimpleNamespace(work_location=None)
    utc_now = datetime(2026, 7, 7, 16, 30, tzinfo=timezone.utc)

    assert local_date_for_employee(employee, -480, utc_now).isoformat() == "2026-07-08"


def test_browser_timezone_offset_is_validated() -> None:
    with pytest.raises(ValueError):
        local_date_from_browser_offset(841)


def test_payroll_net_includes_thr_and_tax_refund() -> None:
    assert _calculate_net_pay(
        gross_salary=Decimal("10_000_000"),
        tax_allowance=Decimal("0"),
        bpjs_employee=Decimal("500_000"),
        pph21_amount=Decimal("1_000_000"),
        thr_amount=Decimal("5_000_000"),
    ) == Decimal("13_500_000")
    assert _calculate_net_pay(
        gross_salary=Decimal("10_000_000"),
        tax_allowance=Decimal("0"),
        bpjs_employee=Decimal("500_000"),
        pph21_amount=Decimal("-250_000"),
        thr_amount=None,
    ) == Decimal("9_750_000")


def test_overtime_is_capped_by_approved_hours() -> None:
    assert _cap_overtime_hours(
        Decimal("2"), Decimal("3"), Decimal("4"), Decimal("4"),
    ) == (Decimal("2"), Decimal("2"), Decimal("0"))
    assert _cap_overtime_hours(
        Decimal("2"), Decimal("3"), Decimal("4"), Decimal("0"),
    ) == (Decimal("0"), Decimal("0"), Decimal("0"))

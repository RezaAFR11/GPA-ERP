from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

from app.hris_attendance_service import calculate_overtime, haversine
from app.hris_employee_service import generate_password
from app.hris_payroll_service import (
    calculate_overtime_pay,
    period_bounds,
)


def test_attendance_overtime_preserves_weekday_and_holiday_buckets() -> None:
    clock_in = datetime(2026, 7, 1, 0, 0, tzinfo=timezone.utc)
    clock_out = datetime(2026, 7, 1, 10, 0, tzinfo=timezone.utc)

    assert calculate_overtime(clock_in, clock_out, False, False) == (
        Decimal("8"),
        Decimal("2.0"),
        Decimal("0"),
        Decimal("0"),
    )
    assert calculate_overtime(clock_in, clock_out, False, True) == (
        Decimal("0"),
        Decimal("0"),
        Decimal("0"),
        Decimal("10.0"),
    )


def test_geofence_distance_is_zero_for_the_same_coordinates() -> None:
    assert haversine(-6.2, 106.8, -6.2, 106.8) == 0


def test_payroll_period_bounds_include_the_full_calendar_month() -> None:
    period = SimpleNamespace(year=2026, month=2)
    start, end, exclusive_end = period_bounds(period)

    assert start.isoformat() == "2026-02-01"
    assert end.isoformat() == "2026-02-28"
    assert exclusive_end.isoformat() == "2026-03-01"


def test_overtime_pay_keeps_existing_permenaker_multipliers() -> None:
    # A monthly basic salary of 17.3m produces an hourly rate of 100k.
    assert calculate_overtime_pay(
        Decimal("17_300_000"),
        Decimal("2"),
        Decimal("0"),
        Decimal("0"),
    ) == Decimal("350_000")


def test_generated_employee_password_contains_required_character_groups() -> None:
    password = generate_password()

    assert len(password) == 12
    assert any(character.isupper() for character in password)
    assert any(character.isdigit() for character in password)
    assert any(character in "!@#$" for character in password)

from decimal import Decimal

from app.hris_tax import (
    calculate_pph21_annual,
    calculate_pph21_final_period,
    calculate_pph21_gross_up,
    calculate_pph21_ter,
    ter_category,
    ter_rate,
)


def test_ter_categories_follow_ptkp_groups() -> None:
    assert ter_category("TK/0") == "A"
    assert ter_category("K/2") == "B"
    assert ter_category("K/3") == "C"


def test_ter_uses_inclusive_official_boundaries() -> None:
    assert ter_rate(Decimal("5_400_000"), "TK/0") == Decimal("0")
    assert ter_rate(Decimal("5_400_001"), "TK/0") == Decimal("0.0025")
    assert ter_rate(Decimal("6_200_000"), "TK/2") == Decimal("0")
    assert ter_rate(Decimal("6_600_000"), "K/3") == Decimal("0")


def test_monthly_ter_withholding() -> None:
    assert calculate_pph21_ter(Decimal("10_000_000"), "TK/0") == Decimal("200_000")


def test_final_period_reconciles_annual_tax_and_refunds_overpayment() -> None:
    annual_tax = calculate_pph21_annual(Decimal("120_000_000"), "TK/0")
    assert annual_tax == Decimal("3_000_000")
    assert calculate_pph21_final_period(
        Decimal("120_000_000"), Decimal("2_500_000"), "TK/0",
    ) == Decimal("500_000")
    assert calculate_pph21_final_period(
        Decimal("120_000_000"), Decimal("3_500_000"), "TK/0",
    ) == Decimal("-500_000")


def test_monthly_gross_up_allowance_matches_withholding() -> None:
    allowance, withholding = calculate_pph21_gross_up(Decimal("10_000_000"), "TK/0")
    assert allowance == withholding
    assert allowance > 0

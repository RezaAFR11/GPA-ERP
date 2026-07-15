from decimal import Decimal

import pytest

from app.hris_bpjs import calculate_bpjs


def test_bpjs_uses_configured_salary_ceilings() -> None:
    result = calculate_bpjs(Decimal("20_000_000"))

    assert result["jht_employee"] == Decimal("400_000")
    assert result["jp_employee"] == Decimal("105_474")
    assert result["jp_employer"] == Decimal("210_948")
    assert result["kes_employee"] == Decimal("120_000")
    assert result["kes_employer"] == Decimal("480_000")
    assert result["total_employee"] == Decimal("625_474")
    assert result["total_employer"] == Decimal("1_668_948")


def test_bpjs_accepts_company_specific_parameters() -> None:
    result = calculate_bpjs(
        Decimal("10_000_000"),
        jkk_rate=Decimal("0.0024"),
        jp_salary_ceiling=Decimal("5_000_000"),
        kes_salary_ceiling=Decimal("8_000_000"),
    )

    assert result["jkk_employer"] == Decimal("24_000")
    assert result["jp_employee"] == Decimal("50_000")
    assert result["kes_employee"] == Decimal("80_000")


def test_bpjs_rejects_invalid_configuration() -> None:
    with pytest.raises(ValueError):
        calculate_bpjs(Decimal("10_000_000"), jp_salary_ceiling=Decimal("0"))


def test_bpjs_does_not_create_negative_contributions() -> None:
    result = calculate_bpjs(Decimal("-1"))
    assert all(value == 0 for value in result.values())

"""Indonesian PPh 21 calculations under PP 58/2023 and PMK 168/2023.

Permanent employees use the monthly effective rate (TER) for every tax period
except their final period. The final period reconciles year-to-date tax using
the progressive Article 17 rates.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP


PTKP: dict[str, int] = {
    "TK/0": 54_000_000,
    "TK/1": 58_500_000,
    "TK/2": 63_000_000,
    "TK/3": 67_500_000,
    "K/0": 58_500_000,
    "K/1": 63_000_000,
    "K/2": 67_500_000,
    "K/3": 72_000_000,
}
DEFAULT_PTKP = "TK/0"

# Each tuple is the inclusive upper gross-income bound and its effective rate.
# The last row in every category is unbounded.
_TER_TABLES: dict[str, tuple[tuple[int | None, str], ...]] = {
    "A": (
        (5_400_000, "0"), (5_650_000, "0.0025"),
        (5_950_000, "0.005"), (6_300_000, "0.0075"),
        (6_750_000, "0.01"), (7_500_000, "0.0125"),
        (8_550_000, "0.015"), (9_650_000, "0.0175"),
        (10_050_000, "0.02"), (10_350_000, "0.0225"),
        (10_700_000, "0.025"), (11_050_000, "0.03"),
        (11_600_000, "0.035"), (12_500_000, "0.04"),
        (13_750_000, "0.05"), (15_100_000, "0.06"),
        (16_950_000, "0.07"), (19_750_000, "0.08"),
        (24_150_000, "0.09"), (26_450_000, "0.10"),
        (28_000_000, "0.11"), (30_050_000, "0.12"),
        (32_400_000, "0.13"), (35_400_000, "0.14"),
        (39_100_000, "0.15"), (43_850_000, "0.16"),
        (47_800_000, "0.17"), (51_400_000, "0.18"),
        (56_300_000, "0.19"), (62_200_000, "0.20"),
        (68_600_000, "0.21"), (77_500_000, "0.22"),
        (89_000_000, "0.23"), (103_000_000, "0.24"),
        (125_000_000, "0.25"), (157_000_000, "0.26"),
        (206_000_000, "0.27"), (337_000_000, "0.28"),
        (454_000_000, "0.29"), (550_000_000, "0.30"),
        (695_000_000, "0.31"), (910_000_000, "0.32"),
        (1_400_000_000, "0.33"), (None, "0.34"),
    ),
    "B": (
        (6_200_000, "0"), (6_500_000, "0.0025"),
        (6_850_000, "0.005"), (7_300_000, "0.0075"),
        (9_200_000, "0.01"), (10_750_000, "0.015"),
        (11_250_000, "0.02"), (11_600_000, "0.025"),
        (12_600_000, "0.03"), (13_600_000, "0.04"),
        (14_950_000, "0.05"), (16_400_000, "0.06"),
        (18_450_000, "0.07"), (21_850_000, "0.08"),
        (26_000_000, "0.09"), (27_700_000, "0.10"),
        (29_350_000, "0.11"), (31_450_000, "0.12"),
        (33_950_000, "0.13"), (37_100_000, "0.14"),
        (41_100_000, "0.15"), (45_800_000, "0.16"),
        (49_500_000, "0.17"), (53_800_000, "0.18"),
        (58_500_000, "0.19"), (64_000_000, "0.20"),
        (71_000_000, "0.21"), (80_000_000, "0.22"),
        (93_000_000, "0.23"), (109_000_000, "0.24"),
        (129_000_000, "0.25"), (163_000_000, "0.26"),
        (211_000_000, "0.27"), (374_000_000, "0.28"),
        (459_000_000, "0.29"), (555_000_000, "0.30"),
        (704_000_000, "0.31"), (957_000_000, "0.32"),
        (1_405_000_000, "0.33"), (None, "0.34"),
    ),
    "C": (
        (6_600_000, "0"), (6_950_000, "0.0025"),
        (7_350_000, "0.005"), (7_800_000, "0.0075"),
        (8_850_000, "0.01"), (9_800_000, "0.0125"),
        (10_950_000, "0.015"), (11_200_000, "0.0175"),
        (12_050_000, "0.02"), (12_950_000, "0.03"),
        (14_150_000, "0.04"), (15_550_000, "0.05"),
        (17_050_000, "0.06"), (19_500_000, "0.07"),
        (22_700_000, "0.08"), (26_600_000, "0.09"),
        (28_100_000, "0.10"), (30_100_000, "0.11"),
        (32_600_000, "0.12"), (35_400_000, "0.13"),
        (38_900_000, "0.14"), (43_000_000, "0.15"),
        (47_400_000, "0.16"), (51_200_000, "0.17"),
        (55_800_000, "0.18"), (60_400_000, "0.19"),
        (66_700_000, "0.20"), (74_500_000, "0.21"),
        (83_200_000, "0.22"), (95_600_000, "0.23"),
        (110_000_000, "0.24"), (134_000_000, "0.25"),
        (169_000_000, "0.26"), (221_000_000, "0.27"),
        (390_000_000, "0.28"), (463_000_000, "0.29"),
        (561_000_000, "0.30"), (709_000_000, "0.31"),
        (965_000_000, "0.32"), (1_419_000_000, "0.33"),
        (None, "0.34"),
    ),
}

_TER_CATEGORY_BY_PTKP = {
    "TK/0": "A", "TK/1": "A", "K/0": "A",
    "TK/2": "B", "TK/3": "B", "K/1": "B", "K/2": "B",
    "K/3": "C",
}

_BRACKETS: tuple[tuple[int | None, str], ...] = (
    (60_000_000, "0.05"),
    (250_000_000, "0.15"),
    (500_000_000, "0.25"),
    (5_000_000_000, "0.30"),
    (None, "0.35"),
)


def ter_category(ptkp_status: str = DEFAULT_PTKP) -> str:
    return _TER_CATEGORY_BY_PTKP.get(ptkp_status, "A")


def ter_rate(gross_monthly: Decimal, ptkp_status: str = DEFAULT_PTKP) -> Decimal:
    gross = max(Decimal(0), Decimal(gross_monthly))
    for upper, rate in _TER_TABLES[ter_category(ptkp_status)]:
        if upper is None or gross <= Decimal(upper):
            return Decimal(rate)
    return Decimal("0.34")


def calculate_pph21_ter(
    gross_monthly: Decimal,
    ptkp_status: str = DEFAULT_PTKP,
) -> Decimal:
    """Calculate a non-final monthly withholding using the official TER table."""
    gross = max(Decimal(0), Decimal(gross_monthly))
    return (gross * ter_rate(gross, ptkp_status)).quantize(
        Decimal("1"), rounding=ROUND_HALF_UP,
    )


def _annual_tax(pkp: Decimal) -> Decimal:
    """Apply Article 17 progressive rates to PKP rounded down to Rp1,000."""
    pkp = max(Decimal(0), Decimal(pkp))
    pkp = (pkp / Decimal("1000")).quantize(Decimal("1"), rounding=ROUND_DOWN) * Decimal("1000")
    tax = Decimal(0)
    lower = Decimal(0)
    for upper, rate in _BRACKETS:
        if upper is None:
            layer = pkp - lower
        else:
            layer = min(pkp, Decimal(upper)) - lower
        if layer <= 0:
            break
        tax += layer * Decimal(rate)
        if upper is not None:
            lower = Decimal(upper)
    return tax.quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def calculate_pph21_annual(
    annual_gross: Decimal,
    ptkp_status: str = DEFAULT_PTKP,
    employee_retirement_contributions: Decimal = Decimal(0),
) -> Decimal:
    """Calculate annual PPh 21 for a permanent employee."""
    gross = max(Decimal(0), Decimal(annual_gross))
    contributions = max(Decimal(0), Decimal(employee_retirement_contributions))
    job_expense = min(gross * Decimal("0.05"), Decimal("6_000_000"))
    ptkp = Decimal(PTKP.get(ptkp_status, PTKP[DEFAULT_PTKP]))
    return _annual_tax(gross - job_expense - contributions - ptkp)


def calculate_pph21_final_period(
    annual_gross: Decimal,
    prior_tax_withheld: Decimal,
    ptkp_status: str = DEFAULT_PTKP,
    employee_retirement_contributions: Decimal = Decimal(0),
) -> Decimal:
    """Return final-period tax, including a negative amount when tax must be refunded."""
    annual_tax = calculate_pph21_annual(
        annual_gross,
        ptkp_status,
        employee_retirement_contributions,
    )
    return (annual_tax - Decimal(prior_tax_withheld)).quantize(
        Decimal("1"), rounding=ROUND_HALF_UP,
    )


def calculate_pph21_netto(
    gross_monthly: Decimal,
    ptkp_status: str = DEFAULT_PTKP,
    months_remaining: int = 12,
) -> Decimal:
    """Backward-compatible alias for TER monthly withholding.

    ``months_remaining`` is retained for callers outside payroll but is no
    longer used because TER is based on income in the current tax period.
    """
    del months_remaining
    return calculate_pph21_ter(gross_monthly, ptkp_status)


def calculate_pph21_gross_up(
    gross_monthly: Decimal,
    ptkp_status: str = DEFAULT_PTKP,
    months_remaining: int = 12,
) -> tuple[Decimal, Decimal]:
    """Solve the monthly TER gross-up allowance iteratively."""
    del months_remaining
    allowance = Decimal(0)
    for _ in range(100):
        tax = calculate_pph21_ter(Decimal(gross_monthly) + allowance, ptkp_status)
        if abs(tax - allowance) < Decimal("1"):
            allowance = tax
            break
        allowance = tax
    return allowance, allowance


def calculate_pph21_final_gross_up(
    annual_gross_before_allowance: Decimal,
    prior_tax_withheld: Decimal,
    ptkp_status: str = DEFAULT_PTKP,
    employee_retirement_contributions: Decimal = Decimal(0),
) -> tuple[Decimal, Decimal]:
    """Solve the final-period annual reconciliation for gross-up payroll."""
    allowance = Decimal(0)
    for _ in range(100):
        tax = calculate_pph21_final_period(
            Decimal(annual_gross_before_allowance) + allowance,
            prior_tax_withheld,
            ptkp_status,
            employee_retirement_contributions,
        )
        # A refund is not an employer tax allowance.
        next_allowance = max(Decimal(0), tax)
        if abs(next_allowance - allowance) < Decimal("1"):
            allowance = next_allowance
            break
        allowance = next_allowance
    tax = calculate_pph21_final_period(
        Decimal(annual_gross_before_allowance) + allowance,
        prior_tax_withheld,
        ptkp_status,
        employee_retirement_contributions,
    )
    return allowance, tax

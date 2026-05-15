"""
GPA-ERP HRIS — PPh 21 income tax calculation engine (H3)

Indonesian personal income tax (PPh 21) for salaried employees.
Uses annual projection method: gross monthly × 12, calculate annual tax, divide by 12.

PTKP table effective 2024 (PMK 101/PMK.010/2016):
    TK/0 = Rp 54,000,000
    K/0  = Rp 58,500,000  (+4,500,000 for married)
    K/1  = Rp 63,000,000  (+4,500,000 per dependant)
    K/2  = Rp 67,500,000
    K/3  = Rp 72,000,000

Progressive tax brackets (UU HPP No.7/2021, effective 2022):
    ≤  60,000,000 :  5%
    ≤ 250,000,000 : 15%
    ≤ 500,000,000 : 25%
    ≤ 5,000,000,000: 30%
    > 5,000,000,000: 35%

Methods:
    NETTO   — PPh 21 borne by employee (deducted from net pay)
    GROSS_UP — PPh 21 grossed up and paid by employer (adds to gross)
"""
from __future__ import annotations
from decimal import Decimal, ROUND_HALF_UP

# ─── PTKP (non-taxable income) table ─────────────────────────────────────────

PTKP: dict[str, int] = {
    "TK/0": 54_000_000,
    "TK/1": 58_500_000,   # single + 1 dependant (unusual, but listed)
    "TK/2": 63_000_000,
    "TK/3": 67_500_000,
    "K/0":  58_500_000,
    "K/1":  63_000_000,
    "K/2":  67_500_000,
    "K/3":  72_000_000,
}
DEFAULT_PTKP = "TK/0"

# ─── Progressive brackets ─────────────────────────────────────────────────────

_BRACKETS: list[tuple[int, float]] = [
    (60_000_000,    0.05),
    (250_000_000,   0.15),
    (500_000_000,   0.25),
    (5_000_000_000, 0.30),
    (float("inf"),  0.35),
]


def _annual_tax(pkp: Decimal) -> Decimal:
    """Calculate annual PPh 21 from taxable income (PKP) using progressive brackets."""
    pkp = max(Decimal(0), pkp)
    tax   = Decimal(0)
    lower = Decimal(0)
    for upper, rate in _BRACKETS:
        upper = Decimal(upper)
        layer = min(pkp, upper) - lower
        if layer <= 0:
            break
        tax  += layer * Decimal(str(rate))
        lower = upper
    return tax.quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def calculate_pph21_netto(
    gross_monthly: Decimal,
    ptkp_status:   str = DEFAULT_PTKP,
) -> Decimal:
    """
    Calculate monthly PPh 21 (NETTO method — borne by employee).

    Formula:
        Annual gross = gross_monthly × 12
        Biaya jabatan = min(annual_gross × 5%, 6_000_000)
        PKP = Annual gross − biaya_jabatan − PTKP
        Annual tax = progressive(PKP)
        Monthly tax = annual_tax / 12

    Returns monthly PPh 21 amount (Rp, rounded to nearest rupiah).
    """
    ptkp_amount = Decimal(PTKP.get(ptkp_status, PTKP[DEFAULT_PTKP]))

    annual_gross    = gross_monthly * 12
    biaya_jabatan   = min(annual_gross * Decimal("0.05"), Decimal("6_000_000"))
    pkp             = annual_gross - biaya_jabatan - ptkp_amount
    annual_tax      = _annual_tax(pkp)
    monthly_tax     = (annual_tax / 12).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return monthly_tax


def calculate_pph21_gross_up(
    gross_monthly: Decimal,
    ptkp_status:   str = DEFAULT_PTKP,
) -> tuple[Decimal, Decimal]:
    """
    Calculate PPh 21 gross-up (employer bears the tax).

    Iteratively solve for tunjangan_pajak T such that:
        PPh21(gross + T) = T

    Returns (tunjangan_pajak, pph21_amount) — both rounded to nearest rupiah.
    The employer adds tunjangan_pajak to gross; pph21_amount = tunjangan_pajak.
    """
    # Iterative convergence (typically < 10 iterations)
    T = Decimal(0)
    for _ in range(50):
        pph21 = calculate_pph21_netto(gross_monthly + T, ptkp_status)
        if abs(pph21 - T) < 1:
            T = pph21
            break
        T = pph21
    return T, T

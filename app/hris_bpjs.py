"""
GPA-ERP HRIS — BPJS contribution calculation engine (H3)

Covers both BPJS Ketenagakerjaan (TK) and BPJS Kesehatan (Kes).
Rates per PP 44/2015 (TK) and Perpres 82/2018 (Kes) as of 2024.

BPJS Ketenagakerjaan:
    JHT (Jaminan Hari Tua):
        Employee:  3.7%
        Employer:  2.0%
    JP  (Jaminan Pensiun):
        Employee:  1.0%   (capped at salary ceiling Rp 9,559,600 / month in 2024)
        Employer:  2.0%
    JKK (Jaminan Kecelakaan Kerja):
        Employer:  0.24% – 1.74% (risk group); default 0.89%
    JKM (Jaminan Kematian):
        Employer:  0.3%

BPJS Kesehatan:
    Employee:  1.0%  (capped at 4% × max salary Rp 12,000,000 = Rp 480,000)
    Employer:  4.0%  (same cap)
"""
from __future__ import annotations
from decimal import Decimal, ROUND_HALF_UP

# ─── Salary ceilings (per 2024) ───────────────────────────────────────────────

JP_SALARY_CEILING  = Decimal("9_559_600")   # max wage for JP contribution
KES_SALARY_CEILING = Decimal("12_000_000")  # max wage for BPJS Kes contribution

# ─── Rates ────────────────────────────────────────────────────────────────────

JHT_EMP_RATE    = Decimal("0.037")
JHT_EMPLOYER_RATE = Decimal("0.020")

JP_EMP_RATE     = Decimal("0.010")
JP_EMPLOYER_RATE = Decimal("0.020")

JKK_RATE        = Decimal("0.0089")   # default mid-risk group
JKM_RATE        = Decimal("0.003")

KES_EMP_RATE    = Decimal("0.010")
KES_EMPLOYER_RATE = Decimal("0.040")


def _round(v: Decimal) -> Decimal:
    return v.quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def calculate_bpjs(
    gross_salary: Decimal,
    jkk_rate:     Decimal | None = None,
) -> dict[str, Decimal]:
    """
    Calculate all BPJS contributions for a given gross monthly salary.

    Args:
        gross_salary: Employee's gross monthly salary (Rp)
        jkk_rate:     Custom JKK rate (employer) — defaults to 0.89%

    Returns dict with keys:
        jht_employee, jht_employer,
        jp_employee,  jp_employer,
        jkk_employer, jkm_employer,
        kes_employee, kes_employer,
        total_employee, total_employer
    """
    jkk = jkk_rate if jkk_rate is not None else JKK_RATE

    # JHT — no ceiling
    jht_emp  = _round(gross_salary * JHT_EMP_RATE)
    jht_er   = _round(gross_salary * JHT_EMPLOYER_RATE)

    # JP — capped
    jp_base  = min(gross_salary, JP_SALARY_CEILING)
    jp_emp   = _round(jp_base * JP_EMP_RATE)
    jp_er    = _round(jp_base * JP_EMPLOYER_RATE)

    # JKK + JKM (employer only)
    jkk_er   = _round(gross_salary * jkk)
    jkm_er   = _round(gross_salary * JKM_RATE)

    # BPJS Kes — capped
    kes_base = min(gross_salary, KES_SALARY_CEILING)
    kes_emp  = _round(kes_base * KES_EMP_RATE)
    kes_er   = _round(kes_base * KES_EMPLOYER_RATE)

    total_emp = jht_emp + jp_emp + kes_emp
    total_er  = jht_er + jp_er + jkk_er + jkm_er + kes_er

    return {
        "jht_employee":  jht_emp,
        "jht_employer":  jht_er,
        "jp_employee":   jp_emp,
        "jp_employer":   jp_er,
        "jkk_employer":  jkk_er,
        "jkm_employer":  jkm_er,
        "kes_employee":  kes_emp,
        "kes_employer":  kes_er,
        "total_employee": total_emp,
        "total_employer": total_er,
    }

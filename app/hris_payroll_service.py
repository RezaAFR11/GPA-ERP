"""Payroll calculation and query services.

The HTTP router delegates here so payroll rules can be tested independently
from FastAPI request handling. Functions intentionally mutate the SQLAlchemy
session but leave commit and audit ownership to the caller.
"""
from __future__ import annotations

from calendar import monthrange
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import and_, func as sql_func, or_
from sqlalchemy.orm import Session, joinedload

from app.config import get_settings
from app.hris_bpjs import calculate_bpjs
from app.hris_tax import (
    DEFAULT_PTKP,
    calculate_pph21_final_gross_up,
    calculate_pph21_final_period,
    calculate_pph21_gross_up,
    calculate_pph21_ter,
    ter_category,
    ter_rate,
)
from app.models import (
    AttendanceRecord,
    Employee,
    EmployeeStatus,
    OvertimeRequest,
    OvertimeRequestStatus,
    PayrollPeriod,
    PayrollRun,
    PPh21Method,
    SalaryAssignment,
    SalaryComponent,
    SalaryComponentType,
)


settings = get_settings()
HOURS_PER_MONTH = Decimal("173")


@dataclass(frozen=True)
class PayrollCalculationResult:
    """Mutated payroll runs and metadata needed by the audit layer."""

    runs: list[PayrollRun]
    linked_legacy_overtime: int


def calculate_net_pay(
    gross_salary: Decimal,
    tax_allowance: Decimal,
    bpjs_employee: Decimal,
    pph21_amount: Decimal,
    thr_amount: Decimal | None,
) -> Decimal:
    net = (
        gross_salary
        + tax_allowance
        + (thr_amount or Decimal(0))
        - bpjs_employee
        - pph21_amount
    )
    return max(Decimal(0), net)


def period_bounds(period: PayrollPeriod) -> tuple[date, date, date]:
    """Return first day, final day, and exclusive end of a payroll month."""
    start = date(period.year, period.month, 1)
    end_inclusive = date(
        period.year,
        period.month,
        monthrange(period.year, period.month)[1],
    )
    return start, end_inclusive, end_inclusive + timedelta(days=1)


def eligible_employees(db: Session, period: PayrollPeriod) -> list[Employee]:
    """Employees whose employment overlaps the payroll period."""
    period_start, period_end, _ = period_bounds(period)
    return (
        db.query(Employee)
        .filter(or_(Employee.join_date.is_(None), Employee.join_date <= period_end))
        .filter(or_(Employee.end_date.is_(None), Employee.end_date >= period_start))
        .filter(or_(
            Employee.status != EmployeeStatus.TERMINATED,
            Employee.end_date >= period_start,
        ))
        .order_by(Employee.id)
        .all()
    )


def calculate_employee_bpjs(gross_salary: Decimal) -> dict[str, Decimal]:
    return calculate_bpjs(
        gross_salary,
        jkk_rate=settings.BPJS_JKK_RATE,
        jp_salary_ceiling=settings.BPJS_JP_SALARY_CEILING,
        kes_salary_ceiling=settings.BPJS_KES_SALARY_CEILING,
    )


def prior_tax_context(
    db: Session,
    employee_id: int,
    period: PayrollPeriod,
) -> tuple[Decimal, Decimal, Decimal]:
    """Return prior taxable gross, deductible contributions, and PPh 21 YTD."""
    rows = (
        db.query(PayrollRun)
        .join(PayrollPeriod, PayrollRun.period_id == PayrollPeriod.id)
        .filter(
            PayrollRun.employee_id == employee_id,
            PayrollPeriod.year == period.year,
            PayrollPeriod.month < period.month,
        )
        .all()
    )
    gross = Decimal(0)
    contributions = Decimal(0)
    tax = Decimal(0)
    for row in rows:
        snapshot = row.components_snapshot or {}
        gross += Decimal(str(snapshot.get(
            "taxable_gross",
            Decimal(row.gross_salary or 0)
            + Decimal(row.thr_amount or 0)
            + Decimal(str(snapshot.get("tunjangan_pajak", 0))),
        )))
        contributions += Decimal(str(snapshot.get(
            "tax_retirement_contribution",
            row.bpjs_tk_employee or 0,
        )))
        tax += Decimal(row.pph21_amount or 0)
    return gross, contributions, tax


def calculate_period_tax(
    db: Session,
    employee: Employee,
    period: PayrollPeriod,
    taxable_gross: Decimal,
    retirement_contribution: Decimal,
    method: PPh21Method,
) -> tuple[Decimal, Decimal, dict]:
    """Calculate TER or final-period reconciliation and audit metadata."""
    ptkp_status = employee.ptkp_status or DEFAULT_PTKP
    period_start, period_end, _ = period_bounds(period)
    is_final_period = period.month == 12 or bool(
        employee.end_date and period_start <= employee.end_date <= period_end
    )
    prior_gross, prior_contributions, prior_tax = prior_tax_context(
        db,
        employee.id,
        period,
    )

    if is_final_period:
        annual_gross = prior_gross + taxable_gross
        annual_contributions = prior_contributions + retirement_contribution
        if method == PPh21Method.GROSS_UP:
            allowance, tax = calculate_pph21_final_gross_up(
                annual_gross,
                prior_tax,
                ptkp_status,
                annual_contributions,
            )
        else:
            allowance = Decimal(0)
            tax = calculate_pph21_final_period(
                annual_gross,
                prior_tax,
                ptkp_status,
                annual_contributions,
            )
        metadata = {
            "tax_scheme": "ARTICLE_17_FINAL",
            "is_final_tax_period": True,
            "annual_taxable_gross": float(annual_gross + allowance),
            "annual_retirement_contribution": float(annual_contributions),
            "prior_tax_withheld": float(prior_tax),
        }
    elif method == PPh21Method.GROSS_UP:
        allowance, tax = calculate_pph21_gross_up(taxable_gross, ptkp_status)
        metadata = {
            "tax_scheme": "TER",
            "is_final_tax_period": False,
            "ter_category": ter_category(ptkp_status),
            "ter_rate": float(ter_rate(taxable_gross + allowance, ptkp_status)),
        }
    else:
        allowance = Decimal(0)
        tax = calculate_pph21_ter(taxable_gross, ptkp_status)
        metadata = {
            "tax_scheme": "TER",
            "is_final_tax_period": False,
            "ter_category": ter_category(ptkp_status),
            "ter_rate": float(ter_rate(taxable_gross, ptkp_status)),
        }

    metadata["ptkp_status"] = ptkp_status
    return allowance, tax, metadata


def validate_period_complete(db: Session, period: PayrollPeriod) -> list[PayrollRun]:
    eligible = eligible_employees(db, period)
    runs = db.query(PayrollRun).filter_by(period_id=period.id).all()
    eligible_ids = {employee.id for employee in eligible}
    run_ids = {run.employee_id for run in runs}
    missing = [
        employee.employee_no
        for employee in eligible
        if employee.id not in run_ids
    ]
    extra = sorted(run_ids - eligible_ids)
    if missing or extra:
        details = []
        if missing:
            details.append("missing payroll: " + ", ".join(missing))
        if extra:
            details.append(
                "ineligible employee IDs: " + ", ".join(map(str, extra))
            )
        raise HTTPException(
            409,
            "Payroll period is incomplete (" + "; ".join(details) + ")",
        )
    if not runs:
        raise HTTPException(409, "Payroll period has no eligible employee runs")
    return runs


def cap_overtime_hours(
    weekday: Decimal,
    weekend: Decimal,
    holiday: Decimal,
    approved_hours: Decimal,
) -> tuple[Decimal, Decimal, Decimal]:
    """Cap actual attendance overtime to the hours approved for that date."""
    remaining = max(Decimal(0), approved_hours)
    paid: list[Decimal] = []
    for actual in (weekday, weekend, holiday):
        value = min(max(Decimal(0), actual), remaining)
        paid.append(value)
        remaining -= value
    return paid[0], paid[1], paid[2]


def backfill_approved_overtime_attendance_links(
    db: Session,
    employee_ids: list[int],
    period_start: date,
    period_end: date,
) -> int:
    """Link legacy approved overtime requests to attendance by employee/date."""
    requests = (
        db.query(OvertimeRequest)
        .filter(
            OvertimeRequest.employee_id.in_(employee_ids),
            OvertimeRequest.status == OvertimeRequestStatus.APPROVED,
            OvertimeRequest.attendance_id.is_(None),
            OvertimeRequest.date >= period_start,
            OvertimeRequest.date < period_end,
        )
        .all()
    )
    if not requests:
        return 0

    keys = {(request.employee_id, request.date) for request in requests}
    attendances = (
        db.query(AttendanceRecord)
        .filter(
            AttendanceRecord.employee_id.in_(employee_ids),
            AttendanceRecord.date >= period_start,
            AttendanceRecord.date < period_end,
        )
        .all()
    )
    attendance_by_key = {
        (attendance.employee_id, attendance.date): attendance.id
        for attendance in attendances
        if (attendance.employee_id, attendance.date) in keys
    }
    linked = 0
    for request in requests:
        attendance_id = attendance_by_key.get((request.employee_id, request.date))
        if attendance_id is not None:
            request.attendance_id = attendance_id
            linked += 1
    if linked:
        db.flush()
    return linked


def build_salary_map(assignments: list[SalaryAssignment]) -> dict[str, Decimal]:
    """Sum pre-loaded salary assignments by component type."""
    result: dict[str, Decimal] = {}
    for assignment in assignments:
        component_type = assignment.component.component_type.value
        result[component_type] = (
            result.get(component_type, Decimal(0)) + assignment.amount
        )
    return result


def salary_component_snapshot(assignments: list[SalaryAssignment]) -> list[dict]:
    return [
        {
            "component_id": assignment.component_id,
            "component_name": assignment.component.name,
            "component_type": assignment.component.component_type.value,
            "is_taxable": assignment.component.is_taxable,
            "amount": float(assignment.amount),
        }
        for assignment in assignments
    ]


def calculate_overtime_pay(
    basic_monthly: Decimal,
    overtime_weekday: Decimal,
    overtime_weekend: Decimal,
    overtime_holiday: Decimal,
) -> Decimal:
    """Calculate overtime pay using the existing Permenaker multipliers."""
    if HOURS_PER_MONTH == 0 or basic_monthly <= 0:
        return Decimal(0)
    hourly = basic_monthly / HOURS_PER_MONTH

    weekday_pay = Decimal(0)
    if overtime_weekday > 0:
        first_hour = min(overtime_weekday, Decimal("1"))
        later_hours = max(Decimal("0"), overtime_weekday - Decimal("1"))
        weekday_pay = (
            hourly * Decimal("1.5") * first_hour
            + hourly * Decimal("2") * later_hours
        )

    weekend_pay = Decimal(0)
    if overtime_weekend > 0:
        first_eight = min(overtime_weekend, Decimal("8"))
        later_hours = max(Decimal("0"), overtime_weekend - Decimal("8"))
        weekend_pay = (
            hourly * Decimal("2") * first_eight
            + hourly * Decimal("3") * later_hours
        )

    holiday_pay = Decimal(0)
    if overtime_holiday > 0:
        first_eight = min(overtime_holiday, Decimal("8"))
        later_hours = max(Decimal("0"), overtime_holiday - Decimal("8"))
        holiday_pay = (
            hourly * Decimal("2") * first_eight
            + hourly * Decimal("3") * later_hours
        )

    return (weekday_pay + weekend_pay + holiday_pay).quantize(Decimal("1"))


def _load_salary_assignments(
    db: Session,
    employee_ids: list[int],
    as_of: date,
) -> dict[int, list[SalaryAssignment]]:
    assignments = (
        db.query(SalaryAssignment)
        .options(joinedload(SalaryAssignment.component))
        .join(SalaryComponent, SalaryAssignment.component_id == SalaryComponent.id)
        .filter(SalaryAssignment.employee_id.in_(employee_ids))
        .filter(SalaryComponent.is_active.is_(True))
        .filter(SalaryAssignment.effective_from <= as_of)
        .filter(or_(
            SalaryAssignment.effective_to.is_(None),
            SalaryAssignment.effective_to >= as_of,
        ))
        .all()
    )
    assignments_by_employee: dict[int, list[SalaryAssignment]] = defaultdict(list)
    for assignment in assignments:
        assignments_by_employee[assignment.employee_id].append(assignment)
    return assignments_by_employee


def _validate_salary_assignments(
    employees: list[Employee],
    assignments_by_employee: dict[int, list[SalaryAssignment]],
) -> None:
    issues: list[str] = []
    for employee in employees:
        assignments = assignments_by_employee[employee.id]
        basic_assignments = [
            assignment
            for assignment in assignments
            if assignment.component.component_type == SalaryComponentType.BASIC
        ]
        component_ids = [assignment.component_id for assignment in assignments]
        if len(basic_assignments) != 1:
            issues.append(
                f"{employee.employee_no}: requires exactly one active BASIC assignment"
            )
        elif any(assignment.amount <= 0 for assignment in assignments):
            issues.append(f"{employee.employee_no}: salary amount must be positive")
        elif len(component_ids) != len(set(component_ids)):
            issues.append(f"{employee.employee_no}: overlapping salary assignments")
    if issues:
        raise HTTPException(
            409,
            "Payroll cannot be calculated: " + "; ".join(issues),
        )


def _ensure_thr_not_duplicated(
    db: Session,
    employee_ids: list[int],
    period: PayrollPeriod,
) -> None:
    duplicate_rows = (
        db.query(Employee.employee_no)
        .join(PayrollRun, PayrollRun.employee_id == Employee.id)
        .join(PayrollPeriod, PayrollRun.period_id == PayrollPeriod.id)
        .filter(
            Employee.id.in_(employee_ids),
            PayrollPeriod.year == period.year,
            PayrollPeriod.id != period.id,
            PayrollRun.thr_amount.isnot(None),
            PayrollRun.thr_amount > 0,
        )
        .all()
    )
    if duplicate_rows:
        employee_numbers = ", ".join(sorted({row[0] for row in duplicate_rows}))
        raise HTTPException(409, f"THR already exists this year for: {employee_numbers}")


def _load_approved_overtime(
    db: Session,
    employee_ids: list[int],
    period_start: date,
    period_end: date,
) -> tuple[dict[int, tuple[Decimal, Decimal, Decimal]], int]:
    linked = backfill_approved_overtime_attendance_links(
        db,
        employee_ids,
        period_start,
        period_end,
    )
    rows = (
        db.query(
            AttendanceRecord.id,
            AttendanceRecord.employee_id,
            AttendanceRecord.hours_overtime_weekday.label("ot_wd"),
            AttendanceRecord.hours_overtime_weekend.label("ot_we"),
            AttendanceRecord.hours_overtime_holiday.label("ot_hol"),
            sql_func.max(OvertimeRequest.planned_hours).label("approved_hours"),
        )
        .join(OvertimeRequest, and_(
            OvertimeRequest.attendance_id == AttendanceRecord.id,
            OvertimeRequest.employee_id == AttendanceRecord.employee_id,
            OvertimeRequest.date == AttendanceRecord.date,
        ))
        .filter(AttendanceRecord.employee_id.in_(employee_ids))
        .filter(AttendanceRecord.date >= period_start)
        .filter(AttendanceRecord.date < period_end)
        .filter(OvertimeRequest.status == OvertimeRequestStatus.APPROVED)
        .group_by(
            AttendanceRecord.id,
            AttendanceRecord.employee_id,
            AttendanceRecord.hours_overtime_weekday,
            AttendanceRecord.hours_overtime_weekend,
            AttendanceRecord.hours_overtime_holiday,
        )
        .all()
    )
    totals_by_employee: dict[int, list[Decimal]] = defaultdict(
        lambda: [Decimal(0), Decimal(0), Decimal(0)]
    )
    for row in rows:
        paid_hours = cap_overtime_hours(
            Decimal(str(row.ot_wd or 0)),
            Decimal(str(row.ot_we or 0)),
            Decimal(str(row.ot_hol or 0)),
            Decimal(str(row.approved_hours or 0)),
        )
        for index, paid in enumerate(paid_hours):
            totals_by_employee[row.employee_id][index] += paid
    overtime_by_employee = {
        employee_id: (totals[0], totals[1], totals[2])
        for employee_id, totals in totals_by_employee.items()
    }
    return overtime_by_employee, linked


def _remove_stale_runs(
    db: Session,
    period: PayrollPeriod,
    eligible_ids: set[int],
) -> None:
    query = db.query(PayrollRun).filter(PayrollRun.period_id == period.id)
    query = query.filter(
        ~PayrollRun.employee_id.in_(eligible_ids) if eligible_ids else True
    )
    stale_runs = query.all()
    if any(run.expense_id for run in stale_runs):
        raise HTTPException(
            409,
            "Payroll contains posted runs for ineligible employees",
        )
    for run in stale_runs:
        db.delete(run)


def _calculate_employee_run(
    db: Session,
    period: PayrollPeriod,
    employee: Employee,
    assignments: list[SalaryAssignment],
    overtime: tuple[Decimal, Decimal, Decimal],
    as_of: date,
    pph21_method: PPh21Method,
    include_thr: bool,
) -> PayrollRun:
    salary_map = build_salary_map(assignments)

    gross = Decimal(0)
    snapshot: dict = {"components": salary_component_snapshot(assignments)}
    for component_type in (
        SalaryComponentType.BASIC.value,
        SalaryComponentType.ALLOWANCE.value,
    ):
        amount = salary_map.get(component_type, Decimal(0))
        gross += amount
        snapshot[component_type] = float(amount)

    deductions = salary_map.get(SalaryComponentType.DEDUCTION.value, Decimal(0))
    manual_bpjs = salary_map.get(SalaryComponentType.BPJS.value, Decimal(0))
    manual_tax = salary_map.get(SalaryComponentType.TAX.value, Decimal(0))
    gross -= deductions
    snapshot.update({
        "DEDUCTION": float(deductions),
        "BPJS": float(manual_bpjs),
        "TAX": float(manual_tax),
    })
    if gross < 0:
        raise HTTPException(
            409,
            f"Salary deductions exceed earnings for {employee.employee_no}",
        )

    basic = salary_map.get(SalaryComponentType.BASIC.value, Decimal(0))
    overtime_pay = calculate_overtime_pay(basic, *overtime)
    gross += overtime_pay
    snapshot["overtime_pay"] = float(overtime_pay)

    bpjs = calculate_employee_bpjs(gross)
    bpjs_tk_employee = bpjs["jht_employee"] + bpjs["jp_employee"]
    bpjs_tk_employer = (
        bpjs["jht_employer"]
        + bpjs["jp_employer"]
        + bpjs["jkk_employer"]
        + bpjs["jkm_employer"]
    )
    bpjs_kes_employee = bpjs["kes_employee"]
    bpjs_kes_employer = bpjs["kes_employer"]

    thr_amount = None
    if include_thr and employee.join_date:
        months_worked = max(
            1,
            (as_of.year - employee.join_date.year) * 12
            + as_of.month
            - employee.join_date.month
            + 1,
        )
        thr_amount = basic if months_worked >= 12 else (
            basic * Decimal(months_worked) / Decimal(12)
        ).quantize(Decimal("1"))

    taxable_earnings = sum(
        assignment.amount
        for assignment in assignments
        if assignment.component.is_taxable
        and assignment.component.component_type in (
            SalaryComponentType.BASIC,
            SalaryComponentType.ALLOWANCE,
        )
    ) + overtime_pay
    taxable_gross = max(
        Decimal(0),
        taxable_earnings + (thr_amount or Decimal(0)),
    )
    retirement_contribution = (
        bpjs["jht_employee"] + bpjs["jp_employee"] + manual_bpjs
    )
    tax_allowance, pph21, tax_metadata = calculate_period_tax(
        db,
        employee,
        period,
        taxable_gross,
        retirement_contribution,
        pph21_method,
    )

    net_salary = calculate_net_pay(
        gross,
        tax_allowance,
        bpjs_tk_employee + bpjs_kes_employee + manual_bpjs,
        pph21 + manual_tax,
        thr_amount,
    )

    snapshot.update({
        "bpjs": {key: float(value) for key, value in bpjs.items()},
        "bpjs_jht_employee": float(bpjs["jht_employee"]),
        "bpjs_jp_employee": float(bpjs["jp_employee"]),
        "bpjs_kes_employee": float(bpjs_kes_employee),
        "manual_bpjs_deduction": float(manual_bpjs),
        "manual_tax_deduction": float(manual_tax),
        "taxable_earnings": float(taxable_earnings),
        "taxable_gross": float(taxable_gross + tax_allowance),
        "tax_retirement_contribution": float(retirement_contribution),
        "pph21": float(pph21),
        "tunjangan_pajak": float(tax_allowance),
        **tax_metadata,
        "bpjs_parameters": {
            "jp_salary_ceiling": float(settings.BPJS_JP_SALARY_CEILING),
            "kes_salary_ceiling": float(settings.BPJS_KES_SALARY_CEILING),
            "jkk_rate": float(settings.BPJS_JKK_RATE),
        },
        "thr_amount": float(thr_amount or 0),
        "total_earnings": float(
            gross + tax_allowance + (thr_amount or Decimal(0))
        ),
    })

    run = (
        db.query(PayrollRun)
        .filter_by(period_id=period.id, employee_id=employee.id)
        .first()
    )
    if run is None:
        run = PayrollRun(period_id=period.id, employee_id=employee.id)
        db.add(run)

    run.gross_salary = gross
    run.bpjs_tk_employee = bpjs_tk_employee
    run.bpjs_tk_employer = bpjs_tk_employer
    run.bpjs_kes_employee = bpjs_kes_employee
    run.bpjs_kes_employer = bpjs_kes_employer
    run.pph21_amount = pph21
    run.pph21_method = pph21_method
    run.net_salary = net_salary
    run.thr_amount = thr_amount
    run.components_snapshot = snapshot
    return run


def calculate_payroll_runs(
    db: Session,
    period: PayrollPeriod,
    pph21_method: PPh21Method,
    include_thr: bool,
) -> PayrollCalculationResult:
    """Calculate or recalculate every eligible employee in one payroll period."""
    period_start, as_of, period_end = period_bounds(period)
    employees = eligible_employees(db, period)
    employee_ids = [employee.id for employee in employees]

    assignments_by_employee = _load_salary_assignments(
        db,
        employee_ids,
        as_of,
    )
    _validate_salary_assignments(employees, assignments_by_employee)
    if include_thr:
        _ensure_thr_not_duplicated(db, employee_ids, period)

    overtime_by_employee, linked_legacy_overtime = _load_approved_overtime(
        db,
        employee_ids,
        period_start,
        period_end,
    )
    _remove_stale_runs(db, period, set(employee_ids))

    runs = [
        _calculate_employee_run(
            db=db,
            period=period,
            employee=employee,
            assignments=assignments_by_employee[employee.id],
            overtime=overtime_by_employee.get(
                employee.id,
                (Decimal(0), Decimal(0), Decimal(0)),
            ),
            as_of=as_of,
            pph21_method=pph21_method,
            include_thr=include_thr,
        )
        for employee in employees
    ]
    return PayrollCalculationResult(
        runs=runs,
        linked_legacy_overtime=linked_legacy_overtime,
    )

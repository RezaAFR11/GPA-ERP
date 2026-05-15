"""
GPA-ERP HRIS — Payroll router (H3)

Endpoints:
    GET/POST  /hris/payroll/periods
    POST      /hris/payroll/periods/{id}/lock
    POST      /hris/payroll/periods/{id}/calculate
    GET       /hris/payroll/runs
    PATCH     /hris/payroll/runs/{run_id}
    GET       /hris/payroll/runs/{run_id}/slip
    GET/POST  /hris/salary-components
    GET/POST  /hris/salary-assignments
    DELETE    /hris/salary-assignments/{id}
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.audit import write_audit
from app.database import get_db
from app.dependencies import CurrentUser, get_current_user
from app.hris_bpjs import calculate_bpjs
from app.hris_tax import calculate_pph21_netto, calculate_pph21_gross_up
from app.models import (
    Employee, PayrollPeriod, PayrollRun, PaySlip,
    SalaryAssignment, SalaryComponent,
    PayrollStatus, PPh21Method, SalaryComponentType, RoleName,
)
from app.schemas import (
    PayrollPeriodCreate, PayrollPeriodResponse,
    PayrollRunResponse, PayrollRunAdjust,
    SalaryComponentCreate, SalaryComponentResponse,
    SalaryAssignmentCreate, SalaryAssignmentResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["HRIS Payroll"])

_HR_ROLES      = (RoleName.SUPER_ADMIN, RoleName.MD)
_FINANCE_ROLES = (RoleName.SUPER_ADMIN, RoleName.MD, RoleName.FINANCE)


def _require(cu: Employee, roles: tuple) -> None:
    if cu.role.name not in roles:
        raise HTTPException(403, f"Requires one of: {[r.value for r in roles]}")


# ─── Salary Components ────────────────────────────────────────────────────────

@router.get("/hris/salary-components", response_model=list[SalaryComponentResponse])
def list_salary_components(
    cu: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    return db.query(SalaryComponent).order_by(SalaryComponent.component_type, SalaryComponent.code).all()


@router.post("/hris/salary-components", response_model=SalaryComponentResponse, status_code=201)
def create_salary_component(
    body: SalaryComponentCreate,
    cu: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    _require(cu, _HR_ROLES)
    existing = db.query(SalaryComponent).filter_by(code=body.code).first()
    if existing:
        raise HTTPException(400, f"Component code '{body.code}' already exists")
    comp = SalaryComponent(**body.model_dump())
    db.add(comp)
    db.commit()
    db.refresh(comp)
    write_audit(db, cu.id, "CREATE", "hris_salary_components", comp.id, None, body.model_dump())
    return comp


# ─── Salary Assignments ───────────────────────────────────────────────────────

@router.get("/hris/salary-assignments", response_model=list[SalaryAssignmentResponse])
def list_salary_assignments(
    cu: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
    employee_id: int | None = None,
):
    q = db.query(SalaryAssignment)
    if employee_id:
        q = q.filter_by(employee_id=employee_id)
    return q.order_by(SalaryAssignment.employee_id, SalaryAssignment.effective_from.desc()).all()


@router.post("/hris/salary-assignments", response_model=SalaryAssignmentResponse, status_code=201)
def create_salary_assignment(
    body: SalaryAssignmentCreate,
    cu: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    _require(cu, _HR_ROLES)
    emp  = db.get(Employee, body.employee_id)
    comp = db.get(SalaryComponent, body.component_id)
    if not emp:  raise HTTPException(404, "Employee not found")
    if not comp: raise HTTPException(404, "Salary component not found")

    asgn = SalaryAssignment(**body.model_dump())
    db.add(asgn)
    db.commit()
    db.refresh(asgn)
    write_audit(db, cu.id, "CREATE", "hris_salary_assignments", asgn.id, None, body.model_dump(mode="json"))
    return asgn


@router.delete("/hris/salary-assignments/{asgn_id}", status_code=204)
def delete_salary_assignment(
    asgn_id: int,
    cu: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    _require(cu, _HR_ROLES)
    asgn = db.get(SalaryAssignment, asgn_id)
    if not asgn:
        raise HTTPException(404, "Assignment not found")
    write_audit(db, cu.id, "DELETE", "hris_salary_assignments", asgn.id, None, None)
    db.delete(asgn)
    db.commit()


# ─── Payroll Periods ──────────────────────────────────────────────────────────

@router.get("/hris/payroll/periods", response_model=list[PayrollPeriodResponse])
def list_periods(
    cu: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    return db.query(PayrollPeriod).order_by(PayrollPeriod.year.desc(), PayrollPeriod.month.desc()).all()


@router.post("/hris/payroll/periods", response_model=PayrollPeriodResponse, status_code=201)
def create_period(
    body: PayrollPeriodCreate,
    cu: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    _require(cu, _HR_ROLES)
    if not (1 <= body.month <= 12):
        raise HTTPException(400, "month must be 1–12")
    existing = db.query(PayrollPeriod).filter_by(year=body.year, month=body.month).first()
    if existing:
        raise HTTPException(400, f"Period {body.year}-{body.month:02d} already exists")
    period = PayrollPeriod(year=body.year, month=body.month, status=PayrollStatus.OPEN)
    db.add(period)
    db.commit()
    db.refresh(period)
    write_audit(db, cu.id, "CREATE", "hris_payroll_periods", period.id, None, body.model_dump())
    return period


@router.post("/hris/payroll/periods/{period_id}/lock", response_model=PayrollPeriodResponse)
def lock_period(
    period_id: int,
    cu: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    _require(cu, _HR_ROLES)
    period = db.get(PayrollPeriod, period_id)
    if not period:
        raise HTTPException(404, "Period not found")
    if period.status != PayrollStatus.OPEN:
        raise HTTPException(400, f"Period is already {period.status}")
    period.status    = PayrollStatus.LOCKED
    period.locked_at = datetime.now(timezone.utc)
    period.locked_by = cu.id
    db.commit()
    db.refresh(period)
    write_audit(db, cu.id, "LOCK", "hris_payroll_periods", period.id, None, None)
    return period


# ─── Payroll Calculation ──────────────────────────────────────────────────────

def _get_active_salary(db: Session, employee_id: int, as_of: date) -> dict[str, Decimal]:
    """Sum up active salary assignments for an employee as of a given date."""
    rows = (
        db.query(SalaryAssignment)
        .filter(SalaryAssignment.employee_id == employee_id)
        .filter(SalaryAssignment.effective_from <= as_of)
        .filter(
            (SalaryAssignment.effective_to == None) |  # noqa: E711
            (SalaryAssignment.effective_to >= as_of)
        )
        .all()
    )
    result: dict[str, Decimal] = {}
    for r in rows:
        comp_type = r.component.component_type.value
        result[comp_type] = result.get(comp_type, Decimal(0)) + r.amount
    return result


@router.post("/hris/payroll/periods/{period_id}/calculate", response_model=list[PayrollRunResponse])
def calculate_period(
    period_id: int,
    cu: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
    pph21_method: PPh21Method = PPh21Method.NETTO,
    include_thr:  bool = False,
):
    """Run payroll for all active employees. Idempotent — recalculates existing runs."""
    _require(cu, _HR_ROLES)
    period = db.get(PayrollPeriod, period_id)
    if not period:
        raise HTTPException(404, "Period not found")
    if period.status == PayrollStatus.POSTED:
        raise HTTPException(400, "Period is already posted — cannot recalculate")

    as_of = date(period.year, period.month, 1)
    employees = db.query(Employee).filter(Employee.status.in_(["active", "probation"])).all()

    runs: list[PayrollRun] = []
    for emp in employees:
        salary_map = _get_active_salary(db, emp.id, as_of)

        # Gross = BASIC + ALLOWANCE
        gross = Decimal(0)
        snapshot: dict = {}
        for comp_type_val in [SalaryComponentType.BASIC.value, SalaryComponentType.ALLOWANCE.value]:
            amt = salary_map.get(comp_type_val, Decimal(0))
            gross += amt
            snapshot[comp_type_val] = float(amt)

        # Deductions reduce gross
        deductions = salary_map.get(SalaryComponentType.DEDUCTION.value, Decimal(0))
        gross -= deductions
        snapshot["DEDUCTION"] = float(deductions)
        gross = max(Decimal(0), gross)

        # BPJS
        bpjs = calculate_bpjs(gross)
        bpjs_tk_emp  = bpjs["jht_employee"] + bpjs["jp_employee"]
        bpjs_tk_er   = bpjs["jht_employer"] + bpjs["jp_employer"] + bpjs["jkk_employer"] + bpjs["jkm_employer"]
        bpjs_kes_emp = bpjs["kes_employee"]
        bpjs_kes_er  = bpjs["kes_employer"]

        # PPh 21
        taxable = gross - bpjs_tk_emp - bpjs_kes_emp
        if pph21_method == PPh21Method.NETTO:
            pph21     = calculate_pph21_netto(taxable)
            tunjangan = Decimal(0)
        else:
            tunjangan, pph21 = calculate_pph21_gross_up(taxable)

        # THR (pro-rata or full basic)
        thr = None
        if include_thr and emp.join_date:
            months_worked = (as_of.year - emp.join_date.year) * 12 + (as_of.month - emp.join_date.month)
            basic = salary_map.get(SalaryComponentType.BASIC.value, Decimal(0))
            if months_worked >= 12:
                thr = basic
            elif months_worked > 0:
                thr = (basic * months_worked / 12).quantize(Decimal("1"))

        # Net pay
        net = gross + tunjangan - bpjs_tk_emp - bpjs_kes_emp - pph21
        net = max(Decimal(0), net)

        snapshot.update({
            "bpjs_jht_employee": float(bpjs["jht_employee"]),
            "bpjs_jp_employee":  float(bpjs["jp_employee"]),
            "bpjs_kes_employee": float(bpjs_kes_emp),
            "pph21":             float(pph21),
            "tunjangan_pajak":   float(tunjangan),
        })

        # Upsert
        run = db.query(PayrollRun).filter_by(period_id=period.id, employee_id=emp.id).first()
        if run is None:
            run = PayrollRun(period_id=period.id, employee_id=emp.id)
            db.add(run)

        run.gross_salary      = gross
        run.bpjs_tk_employee  = bpjs_tk_emp
        run.bpjs_tk_employer  = bpjs_tk_er
        run.bpjs_kes_employee = bpjs_kes_emp
        run.bpjs_kes_employer = bpjs_kes_er
        run.pph21_amount      = pph21
        run.pph21_method      = pph21_method
        run.net_salary        = net
        run.thr_amount        = thr
        run.components_snapshot = snapshot
        runs.append(run)

    db.commit()
    for r in runs:
        db.refresh(r)

    write_audit(db, cu.id, "CALCULATE", "hris_payroll_periods", period.id, None, {"employee_count": len(runs)})
    logger.info(f"Payroll calculated: period={period_id}, employees={len(runs)}")
    return runs


# ─── Payroll Runs ─────────────────────────────────────────────────────────────

@router.get("/hris/payroll/runs", response_model=list[PayrollRunResponse])
def list_runs(
    cu: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
    period_id:   int | None = None,
    employee_id: int | None = None,
):
    q = db.query(PayrollRun)
    if period_id:   q = q.filter_by(period_id=period_id)
    if employee_id: q = q.filter_by(employee_id=employee_id)
    return q.order_by(PayrollRun.period_id.desc(), PayrollRun.employee_id).all()


@router.patch("/hris/payroll/runs/{run_id}", response_model=PayrollRunResponse)
def adjust_run(
    run_id: int,
    body: PayrollRunAdjust,
    cu: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    _require(cu, _HR_ROLES + _FINANCE_ROLES)
    run = db.get(PayrollRun, run_id)
    if not run:
        raise HTTPException(404, "Payroll run not found")
    period = db.get(PayrollPeriod, run.period_id)
    if period and period.status == PayrollStatus.POSTED:
        raise HTTPException(400, "Cannot adjust a posted period")

    before = {
        "gross_salary":   float(run.gross_salary),
        "thr_amount":     float(run.thr_amount) if run.thr_amount else None,
        "pph21_method":   run.pph21_method.value,
        "cost_centre_id": run.cost_centre_id,
    }

    if body.gross_salary   is not None: run.gross_salary   = body.gross_salary
    if body.thr_amount     is not None: run.thr_amount     = body.thr_amount
    if body.pph21_method   is not None: run.pph21_method   = body.pph21_method
    if body.cost_centre_id is not None: run.cost_centre_id = body.cost_centre_id

    # Recalculate net
    bpjs     = calculate_bpjs(run.gross_salary)
    bpjs_emp = bpjs["jht_employee"] + bpjs["jp_employee"] + bpjs["kes_employee"]
    if run.pph21_method == PPh21Method.NETTO:
        pph21     = calculate_pph21_netto(run.gross_salary - bpjs_emp)
        tunjangan = Decimal(0)
    else:
        tunjangan, pph21 = calculate_pph21_gross_up(run.gross_salary - bpjs_emp)
    run.pph21_amount = pph21
    run.net_salary   = run.gross_salary + tunjangan - bpjs_emp - pph21

    db.commit()
    db.refresh(run)
    write_audit(db, cu.id, "ADJUST", "hris_payroll_runs", run.id, before, body.model_dump(mode="json", exclude_none=True))
    return run


# ─── Pay Slip ─────────────────────────────────────────────────────────────────

@router.get("/hris/payroll/runs/{run_id}/slip")
def get_payslip(
    run_id: int,
    cu: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    """Return a structured pay slip JSON for a payroll run."""
    run = db.get(PayrollRun, run_id)
    if not run:
        raise HTTPException(404, "Payroll run not found")
    emp    = db.get(Employee, run.employee_id)
    period = db.get(PayrollPeriod, run.period_id)

    return {
        "period":            f"{period.year}-{period.month:02d}",
        "employee_no":       emp.employee_no   if emp else None,
        "employee_name":     emp.full_name     if emp else None,
        "department":        emp.department.name if emp and emp.department else None,
        "gross_salary":      float(run.gross_salary),
        "bpjs_tk_employee":  float(run.bpjs_tk_employee),
        "bpjs_tk_employer":  float(run.bpjs_tk_employer),
        "bpjs_kes_employee": float(run.bpjs_kes_employee),
        "bpjs_kes_employer": float(run.bpjs_kes_employer),
        "pph21_amount":      float(run.pph21_amount),
        "pph21_method":      run.pph21_method.value,
        "thr_amount":        float(run.thr_amount) if run.thr_amount else None,
        "net_salary":        float(run.net_salary),
        "components":        run.components_snapshot,
    }

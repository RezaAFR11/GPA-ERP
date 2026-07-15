"""
GPA-ERP HRIS — Self-Service Portal
/hris/me/* endpoints — scoped to the calling user's linked Employee record.

Accessible by any authenticated user who has an Employee linked via Employee.user_id.
Workers (WORKER role) use these exclusively; other roles (STAFF, PM, etc.) can
also reach them to see their own data without needing HR-admin access.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.audit import model_to_dict, write_audit
from app.database import get_db
from app.dependencies import CurrentUser
from app.hris_access import ensure_employee_can_use_self_service
from app.menu_permissions import require_menu_access
from app.hris_time import (
    MAX_BROWSER_TIMEZONE_OFFSET,
    MIN_BROWSER_TIMEZONE_OFFSET,
    local_date_for_employee,
)
from app.models import (
    AttendanceRecord, Employee,
    EmployeeDataChangeRequest, DataChangeStatus,
    LeaveBalance, LeaveRequest, LeaveRequestStatus, LeaveType,
    PayrollRun, PaySlip, PayrollPeriod, PayrollStatus,
    SalaryComponent, SalaryComponentType, User,
)
from app.schemas import (
    CHANGEABLE_FIELDS,
    DataChangeRequestCreate,
    DataChangeRequestResponse,
)

router = APIRouter(prefix="/hris/me", tags=["HRIS – Self Service"])


# ─── Helper ───────────────────────────────────────────────────────────────────

def _my_employee(cu: Any, db: Session, *, lock: bool = False) -> Employee:
    """Resolve the current user → their linked Employee, or 404."""
    query = db.query(Employee).filter(Employee.user_id == cu.id)
    if lock:
        query = query.with_for_update()
    emp = query.first()
    if not emp:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No employee record is linked to your account. Contact HR.",
        )
    return ensure_employee_can_use_self_service(emp)


_SNAPSHOT_COMPONENT_LABELS = {
    SalaryComponentType.BASIC.value: "Gaji Pokok",
    SalaryComponentType.ALLOWANCE.value: "Tunjangan",
    SalaryComponentType.DEDUCTION.value: "Potongan",
    SalaryComponentType.BPJS.value: "Potongan BPJS Tambahan",
    SalaryComponentType.TAX.value: "Potongan Pajak Tambahan",
}


def _snapshot_component_items(snapshot: Any, db: Session) -> list[dict]:
    """Normalize legacy dict and newer itemized payroll snapshots."""
    if isinstance(snapshot, list):
        raw_items = snapshot
    elif isinstance(snapshot, dict) and isinstance(snapshot.get("components"), list):
        raw_items = snapshot["components"]
    elif isinstance(snapshot, dict):
        raw_items = [
            {
                "component_id": None,
                "component_name": label,
                "component_type": component_type,
                "amount": snapshot.get(component_type, 0),
            }
            for component_type, label in _SNAPSHOT_COMPONENT_LABELS.items()
            if snapshot.get(component_type) not in (None, 0, 0.0)
        ]
    else:
        raw_items = []

    enriched: list[dict] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        component_id = item.get("component_id")
        component = db.get(SalaryComponent, component_id) if isinstance(component_id, int) else None
        component_type = item.get("component_type") or (
            component.component_type.value if component else None
        )
        enriched.append({
            **item,
            "component_id": component_id if isinstance(component_id, int) else None,
            "component_name": item.get("component_name") or (
                component.name if component else _SNAPSHOT_COMPONENT_LABELS.get(component_type, str(component_type or "Komponen"))
            ),
            "component_type": component_type,
            "is_taxable": item.get("is_taxable", component.is_taxable if component else None),
            "amount": float(item.get("amount") or 0),
        })
    return enriched


def _payroll_earnings(run: PayrollRun) -> tuple[Decimal, Decimal]:
    """Return tax allowance and all earnings represented by a payroll run."""
    snapshot = run.components_snapshot or {}
    if isinstance(snapshot, dict):
        tax_allowance = Decimal(str(snapshot.get("tunjangan_pajak") or 0))
        stored_total = snapshot.get("total_earnings")
        total_earnings = (
            Decimal(str(stored_total))
            if stored_total is not None
            else Decimal(run.gross_salary or 0) + tax_allowance + Decimal(run.thr_amount or 0)
        )
        return tax_allowance, total_earnings
    return Decimal(0), Decimal(run.gross_salary or 0) + Decimal(run.thr_amount or 0)


# ─── My Profile ───────────────────────────────────────────────────────────────

@router.get("", summary="My employee profile")
def my_profile(
    cu: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    emp = _my_employee(cu, db)
    dept = emp.department
    grade = emp.grade
    return {
        "id":           emp.id,
        "employee_no":  emp.employee_no,
        "full_name":    emp.full_name,
        "email":        emp.email,
        "phone":        emp.phone,
        "tipe":         emp.tipe.value,
        "status":       emp.status.value,
        "site":         emp.site,
        "join_date":    emp.join_date.isoformat() if emp.join_date else None,
        "department":   {"id": dept.id, "name": dept.name} if dept else None,
        "grade":        {"id": grade.id, "name": grade.name, "level": grade.level} if grade else None,
        "bank_name":    emp.bank_name,
        "bank_account": emp.bank_account,
        "photo_url":    emp.photo_url,
    }


@router.post("/data-change-requests", response_model=DataChangeRequestResponse, status_code=201)
def submit_data_change_request(
    payload: DataChangeRequestCreate,
    cu: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    emp = _my_employee(cu, db, lock=True)
    if payload.field_name not in CHANGEABLE_FIELDS:
        raise HTTPException(
            400,
            f"Field '{payload.field_name}' is not changeable. Allowed: {sorted(CHANGEABLE_FIELDS)}",
        )
    old_value = str(getattr(emp, payload.field_name, "") or "")
    if old_value.strip() == payload.new_value:
        raise HTTPException(409, "The new value is the same as the current value")
    pending = db.query(EmployeeDataChangeRequest.id).filter(
        EmployeeDataChangeRequest.employee_id == emp.id,
        EmployeeDataChangeRequest.field_name == payload.field_name,
        EmployeeDataChangeRequest.status == DataChangeStatus.PENDING,
    ).first()
    if pending:
        raise HTTPException(409, "A pending request already exists for this field")
    request = EmployeeDataChangeRequest(
        employee_id=emp.id,
        field_name=payload.field_name,
        old_value=old_value,
        new_value=payload.new_value,
        reason=payload.reason,
        status=DataChangeStatus.PENDING,
    )
    db.add(request)
    db.flush()
    write_audit(
        db, "EmployeeDataChangeRequest", request.id, "SUBMIT",
        changed_by=cu.id, after=model_to_dict(request),
    )
    db.commit()
    db.refresh(request)
    return request


@router.get("/data-change-requests", response_model=list[DataChangeRequestResponse])
def my_data_change_requests(
    cu: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    emp = _my_employee(cu, db)
    return (
        db.query(EmployeeDataChangeRequest)
        .filter(EmployeeDataChangeRequest.employee_id == emp.id)
        .order_by(EmployeeDataChangeRequest.created_at.desc())
        .all()
    )


# ─── My Attendance ────────────────────────────────────────────────────────────

@router.get(
    "/attendance",
    summary="My attendance records",
    dependencies=[Depends(require_menu_access("hris_attendance"))],
)
def my_attendance(
    cu: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
    year:  int = Query(default=None),
    month: int = Query(default=None),
    limit: int = Query(default=31, le=100),
    timezone_offset_minutes: int = Query(
        default=...,
        ge=MIN_BROWSER_TIMEZONE_OFFSET,
        le=MAX_BROWSER_TIMEZONE_OFFSET,
    ),
) -> dict:
    emp = _my_employee(cu, db)
    today = local_date_for_employee(emp, timezone_offset_minutes)
    y = year  or today.year
    m = month or today.month

    q = (
        db.query(AttendanceRecord)
        .filter(AttendanceRecord.employee_id == emp.id)
    )

    # Filter to the requested month
    from sqlalchemy import extract
    q = q.filter(
        extract("year",  AttendanceRecord.date) == y,
        extract("month", AttendanceRecord.date) == m,
    ).order_by(AttendanceRecord.date.desc()).limit(limit)

    records = q.all()

    # Surface any unresolved shift so it cannot disappear after midnight.
    today_rec = db.query(AttendanceRecord).filter(
        AttendanceRecord.employee_id == emp.id,
        AttendanceRecord.date == today,
    ).first()
    open_rec = (
        db.query(AttendanceRecord)
        .filter(
            AttendanceRecord.employee_id == emp.id,
            AttendanceRecord.clock_in.isnot(None),
            AttendanceRecord.clock_out.is_(None),
        )
        .order_by(AttendanceRecord.date.desc())
        .first()
    )
    active_rec = open_rec or today_rec

    def _fmt(r: AttendanceRecord) -> dict:
        return {
            "id":                       r.id,
            "date":                     r.date.isoformat(),
            "clock_in":                 r.clock_in.isoformat() if r.clock_in else None,
            "clock_out":                r.clock_out.isoformat() if r.clock_out else None,
            "hours_regular":            float(r.hours_regular or 0),
            "hours_overtime_weekday":   float(r.hours_overtime_weekday or 0),
            "hours_overtime_weekend":   float(r.hours_overtime_weekend or 0),
            "hours_overtime_holiday":   float(r.hours_overtime_holiday or 0),
            "source":                   r.source.value if r.source else None,
            "face_verified":            r.face_verified,
            "face_confidence":          r.face_confidence,
            "latitude":                 float(r.latitude)  if r.latitude  else None,
            "longitude":                float(r.longitude) if r.longitude else None,
            "location_ok":              r.location_ok,
            "location_distance_m":      float(r.location_distance_m) if r.location_distance_m is not None else None,
            "matched_location_name":    r.matched_work_location.name if r.matched_work_location else None,
            "matched_location_type":    r.matched_work_location.location_type.value if r.matched_work_location else None,
            "selfie_url":               r.selfie_url,
            "note":                     r.note,
        }

    # Summary: attendance records and total hours this month.
    total_hours = sum(
        float(r.hours_regular or 0) + float(r.hours_overtime_weekday or 0)
        + float(r.hours_overtime_weekend or 0) + float(r.hours_overtime_holiday or 0)
        for r in records
    )

    return {
        "year":        y,
        "month":       m,
        "employee_id": emp.id,
        "today": _fmt(active_rec) if active_rec else None,
        "clock_state": (
            "clocked_in"   if open_rec else
            "clocked_out"  if today_rec and today_rec.clock_out else
            "not_clocked_in"
        ),
        "summary": {
            "attendance_days": sum(1 for record in records if record.clock_in),
            "total_hours":   round(total_hours, 2),
        },
        "records": [_fmt(r) for r in records],
    }


# ─── My Leave ─────────────────────────────────────────────────────────────────

@router.get(
    "/leave-balance",
    summary="My leave balances for the current year",
    dependencies=[Depends(require_menu_access("hris_leave"))],
)
def my_leave_balance(
    cu: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
    year: int = Query(default=None),
) -> list[dict]:
    emp = _my_employee(cu, db)
    y = year or date.today().year

    rows = (
        db.query(LeaveBalance, LeaveType)
        .join(LeaveType, LeaveBalance.leave_type_id == LeaveType.id)
        .filter(
            LeaveBalance.employee_id == emp.id,
            LeaveBalance.year == y,
        )
        .all()
    )

    return [
        {
            "leave_type_id":    lt.id,
            "code":             lt.code,
            "name":             lt.name,
            "is_paid":          lt.is_paid,
            "max_days":         lt.max_days_per_year,
            "accrued":          float(bal.accrued or 0),
            "used":             float(bal.used or 0),
            "remaining":        float((bal.accrued or 0) - (bal.used or 0)),
            "year":             y,
        }
        for bal, lt in rows
    ]


@router.get(
    "/leave-requests",
    summary="My leave request history",
    dependencies=[Depends(require_menu_access("hris_leave"))],
)
def my_leave_requests(
    cu: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
    status: str | None = Query(default=None),
    limit:  int        = Query(default=20, le=50),
) -> list[dict]:
    emp = _my_employee(cu, db)

    q = (
        db.query(LeaveRequest)
        .filter(LeaveRequest.employee_id == emp.id)
        .order_by(LeaveRequest.created_at.desc())
    )
    if status:
        try:
            normalized_status = status.strip().lower()
            q = q.filter(LeaveRequest.status == LeaveRequestStatus(normalized_status))
        except ValueError:
            raise HTTPException(400, f"Invalid status: {status}")
    requests = q.limit(limit).all()

    actor_ids = {
        event.get("user_id")
        for request in requests
        for event in (request.approval_history or [])
        if isinstance(event, dict) and isinstance(event.get("user_id"), int)
    }
    actor_names = dict(
        db.query(User.id, User.full_name).filter(User.id.in_(actor_ids)).all()
    ) if actor_ids else {}

    def _fmt(r: LeaveRequest) -> dict:
        lt = db.get(LeaveType, r.leave_type_id)
        history = []
        for event in r.approval_history or []:
            if not isinstance(event, dict):
                continue
            action = str(event.get("action") or "").lower()
            user_id = event.get("user_id")
            history.append({
                "actor": actor_names.get(user_id) or event.get("role") or "Sistem",
                "role": event.get("role"),
                "action": action,
                "note": event.get("note"),
                "at": event.get("timestamp"),
            })
        return {
            "id":           r.id,
            "leave_type":   {"id": lt.id, "name": lt.name} if lt else None,
            "start_date":   r.start_date.isoformat(),
            "end_date":     r.end_date.isoformat(),
            "days":         r.days,
            "reason":       r.reason,
            "doctor_cert_url": r.doctor_cert_url,
            "status":       r.status.value,
            "current_approver_role": r.current_approver_role,
            "submitted_at": r.created_at.isoformat() if r.created_at else None,
            "approval_history": history,
        }

    return [_fmt(r) for r in requests]


# ─── My Payslips ──────────────────────────────────────────────────────────────

@router.get(
    "/payslips",
    summary="My payslip list",
    dependencies=[Depends(require_menu_access("hris_my_payslip"))],
)
def my_payslips(
    cu: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
    limit: int = Query(default=12, le=24),
) -> list[dict]:
    emp = _my_employee(cu, db)

    rows = (
        db.query(PayrollRun, PayrollPeriod)
        .join(PayrollPeriod, PayrollRun.period_id == PayrollPeriod.id)
        .filter(
            PayrollRun.employee_id == emp.id,
            PayrollPeriod.status == PayrollStatus.POSTED,
        )
        .order_by(PayrollPeriod.year.desc(), PayrollPeriod.month.desc())
        .limit(limit)
        .all()
    )

    def _fmt(run: PayrollRun, period: PayrollPeriod) -> dict:
        slip = db.query(PaySlip).filter(PaySlip.run_id == run.id).first()
        _, total_earnings = _payroll_earnings(run)
        return {
            "run_id":           run.id,
            "year":             period.year,
            "month":            period.month,
            "period_label":     f"{period.year}-{period.month:02d}",
            "gross_salary":     float(run.gross_salary or 0),
            "total_earnings":   float(total_earnings),
            "net_salary":       float(run.net_salary or 0),
            "bpjs_tk_employee": float(run.bpjs_tk_employee or 0),
            "bpjs_kes_employee":float(run.bpjs_kes_employee or 0),
            "pph21_amount":     float(run.pph21_amount or 0),
            "thr_amount":       float(run.thr_amount or 0) if run.thr_amount else None,
            "pdf_url":          f"/api/hris/me/payslips/{run.id}/pdf" if slip else None,
            "has_pdf":          slip is not None and bool(slip.pdf_url),
        }

    return [_fmt(run, period) for run, period in rows]


@router.get(
    "/payslips/{run_id}",
    summary="My payslip detail (full breakdown)",
    dependencies=[Depends(require_menu_access("hris_my_payslip"))],
)
def my_payslip_detail(
    run_id: int,
    cu: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    emp = _my_employee(cu, db)

    run = db.query(PayrollRun).filter(
        PayrollRun.id == run_id,
        PayrollRun.employee_id == emp.id,   # ownership check
    ).first()
    if not run:
        raise HTTPException(404, "Payslip not found")

    period = db.get(PayrollPeriod, run.period_id)
    if not period:
        raise HTTPException(404, "Payroll period not found")
    if period.status != PayrollStatus.POSTED:
        raise HTTPException(403, "Payslip not yet released")

    slip = db.query(PaySlip).filter(PaySlip.run_id == run.id).first()

    enriched = _snapshot_component_items(run.components_snapshot, db)
    tax_allowance, total_earnings = _payroll_earnings(run)

    return {
        "run_id":             run.id,
        "year":               period.year,
        "month":              period.month,
        "period_label":       f"{period.year}-{period.month:02d}",
        "employee": {
            "id":          emp.id,
            "employee_no": emp.employee_no,
            "full_name":   emp.full_name,
            "bank_name":   emp.bank_name,
            "bank_account":emp.bank_account,
        },
        "gross_salary":       float(run.gross_salary or 0),
        "total_earnings":     float(total_earnings),
        "tax_allowance":      float(tax_allowance),
        "net_salary":         float(run.net_salary or 0),
        "bpjs_tk_employee":   float(run.bpjs_tk_employee or 0),
        "bpjs_tk_employer":   float(run.bpjs_tk_employer or 0),
        "bpjs_kes_employee":  float(run.bpjs_kes_employee or 0),
        "bpjs_kes_employer":  float(run.bpjs_kes_employer or 0),
        "pph21_amount":       float(run.pph21_amount or 0),
        "pph21_method":       run.pph21_method.value if run.pph21_method else None,
        "thr_amount":         float(run.thr_amount or 0) if run.thr_amount else None,
        "components":         enriched,
        "pdf_url":            f"/api/hris/me/payslips/{run.id}/pdf" if slip else None,
    }


@router.get(
    "/payslips/{run_id}/pdf",
    summary="Download my released payslip PDF",
    dependencies=[Depends(require_menu_access("hris_my_payslip"))],
)
def my_payslip_pdf(
    run_id: int,
    cu: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    from app.routers.hris_payroll import download_payslip_pdf

    run = db.query(PayrollRun).filter(
        PayrollRun.id == run_id,
        PayrollRun.employee_id == _my_employee(cu, db).id,
    ).first()
    if not run:
        raise HTTPException(404, "Payslip not found")
    period = db.get(PayrollPeriod, run.period_id)
    if not period or period.status != PayrollStatus.POSTED:
        raise HTTPException(403, "Payslip not yet released")
    return download_payslip_pdf(run_id, cu, db)


@router.get(
    "/documents",
    response_model=list[dict],
    summary="My downloadable documents",
    dependencies=[Depends(require_menu_access("hris_my_payslip"))],
)
def my_documents(
    cu: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    """Return the current employee's documents and released payslips."""
    emp = _my_employee(cu, db)
    items: list[dict] = []

    for document in emp.documents:
        items.append({
            "doc_type": document.doc_type.value,
            "name": f"Dokumen {document.doc_type.value}",
            "date": document.uploaded_at.date().isoformat() if document.uploaded_at else None,
            "file_url": document.file_url,
            "period_label": None,
        })

    rows = (
        db.query(PayrollRun, PayrollPeriod)
        .join(PaySlip, PayrollRun.id == PaySlip.run_id)
        .join(PayrollPeriod, PayrollRun.period_id == PayrollPeriod.id)
        .filter(
            PayrollRun.employee_id == emp.id,
            PayrollPeriod.status == PayrollStatus.POSTED,
        )
        .order_by(PayrollPeriod.year.desc(), PayrollPeriod.month.desc())
        .all()
    )
    months = ["", "Januari", "Februari", "Maret", "April", "Mei", "Juni",
              "Juli", "Agustus", "September", "Oktober", "November", "Desember"]
    for run, period in rows:
        items.append({
            "doc_type": "payslip",
            "name": f"Slip Gaji {months[period.month]} {period.year}",
            "date": f"{period.year}-{period.month:02d}-01",
            "file_url": f"/api/hris/me/payslips/{run.id}/pdf",
            "period_label": f"{months[period.month]} {period.year}",
        })

    return items

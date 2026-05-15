"""
GPA-ERP HRIS — Phase H2: Absensi & Cuti
Endpoints for attendance (with geolocation + face verification) and leave management.
"""
from __future__ import annotations

import csv
import io
import uuid
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile, status
from sqlalchemy.orm import Session
from sqlalchemy import func, or_

from app.audit import model_to_dict, write_audit
from app.database import get_db
from app.dependencies import CurrentUser, get_client_ip, require_role
from app.models import (
    AttendanceRecord, AttendanceSource,
    Employee, LeaveBalance, LeaveRequest, LeaveRequestStatus, LeaveType,
    RoleName,
)
from app.notify import push, push_to_role
from app.schemas import (
    AttendanceManualCreate, AttendanceRecordResponse, AttendanceSummaryItem,
    LeaveActionRequest, LeaveBalanceResponse, LeaveRequestCreate,
    LeaveRequestResponse, LeaveTypeCreate, LeaveTypeResponse,
    MessageResponse, PaginatedResponse,
)

router = APIRouter(prefix="/hris", tags=["HRIS – Attendance & Leave"])

_hr_roles  = (RoleName.SUPER_ADMIN, RoleName.MD, RoleName.GA)
_mgr_roles = (RoleName.SUPER_ADMIN, RoleName.MD, RoleName.PM, RoleName.GA)

_SELFIE_DIR = Path("uploads") / "selfies"
_SELFIE_DIR.mkdir(parents=True, exist_ok=True)

# Default leave approval chain: GA reviews, MD approves
_LEAVE_APPROVAL_CHAIN = ["GA", "MD"]


# ─── Overtime calculation (Permenaker No. 2 Tahun 2023) ─────────────────────

def _calculate_overtime(
    clock_in:  datetime,
    clock_out: datetime,
    is_weekend: bool,
    is_holiday: bool,
) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    """
    Returns (hours_regular, hours_ot_weekday, hours_ot_weekend, hours_ot_holiday).
    Permenaker 2023 rules:
      Weekday: first 8h regular, OT starts after 8h
      Weekend/Holiday: all hours are OT
    """
    total_hours = Decimal(str((clock_out - clock_in).total_seconds() / 3600))
    REGULAR_HOURS = Decimal("8")

    if is_holiday:
        return Decimal("0"), Decimal("0"), Decimal("0"), total_hours
    if is_weekend:
        return Decimal("0"), Decimal("0"), total_hours, Decimal("0")

    # Weekday
    regular = min(total_hours, REGULAR_HOURS)
    ot      = max(Decimal("0"), total_hours - REGULAR_HOURS)
    return regular, ot, Decimal("0"), Decimal("0")


def _is_weekend(d: date) -> bool:
    return d.weekday() >= 5  # Saturday=5, Sunday=6


# ─── Attendance: clock-in (mobile, geolocation + selfie) ─────────────────────

@router.post("/attendance/clock-in", response_model=AttendanceRecordResponse,
             summary="Mobile clock-in with GPS + selfie")
async def clock_in(
    current_user: CurrentUser,
    db:           Annotated[Session, Depends(get_db)],
    latitude:     float              = Form(...),
    longitude:    float              = Form(...),
    accuracy:     float              = Form(100.0),
    employee_id:  int | None         = Form(None),
    selfie:       UploadFile | None  = File(None),
):
    """
    Mobile clock-in: accepts GPS coordinates + selfie photo.
    - If employee_id not provided, uses the employee linked to current_user.
    - Runs face verification if employee has a registered face embedding.
    - Creates or updates the AttendanceRecord for today.
    """
    # Resolve employee
    if employee_id:
        emp = db.query(Employee).filter(Employee.id == employee_id).first()
        if not emp:
            raise HTTPException(404, "Employee not found")
        # Only HR/admin can clock in for others
        if emp.user_id != current_user.id and current_user.role.name not in _hr_roles:
            raise HTTPException(403, "Can only clock in for yourself")
    else:
        emp = db.query(Employee).filter(Employee.user_id == current_user.id).first()
        if not emp:
            raise HTTPException(404, "No employee record linked to your account")

    today      = datetime.now(timezone.utc).date()
    now        = datetime.now(timezone.utc)
    face_verified   = False
    face_confidence: Decimal | None = None
    selfie_url: str | None = None

    # Process selfie
    if selfie:
        selfie_bytes = await selfie.read()
        ext      = Path(selfie.filename or "selfie").suffix or ".jpg"
        filename = f"{emp.id}_{today.isoformat()}_{uuid.uuid4().hex[:8]}{ext}"
        dest     = _SELFIE_DIR / filename
        dest.write_bytes(selfie_bytes)
        selfie_url = f"/uploads/selfies/{filename}"

        # Face verification (if embedding registered)
        if emp.face_embedding:
            try:
                from app.hris_face import verify_face
                verified, confidence = verify_face(emp.face_embedding, selfie_bytes)
                face_verified   = verified
                face_confidence = Decimal(str(confidence))
            except Exception:
                pass  # log but don't block clock-in

    # Upsert attendance record (one per employee per day)
    record = db.query(AttendanceRecord).filter(
        AttendanceRecord.employee_id == emp.id,
        AttendanceRecord.date        == today,
    ).first()

    if record:
        # Already clocked in — update selfie/geo if re-submitted
        if selfie_url:
            record.selfie_url     = selfie_url
            record.face_verified  = face_verified
            record.face_confidence = face_confidence
        record.latitude  = Decimal(str(latitude))
        record.longitude = Decimal(str(longitude))
        record.accuracy  = Decimal(str(accuracy))
    else:
        record = AttendanceRecord(
            employee_id    = emp.id,
            date           = today,
            clock_in       = now,
            source         = AttendanceSource.MOBILE,
            latitude       = Decimal(str(latitude)),
            longitude      = Decimal(str(longitude)),
            accuracy       = Decimal(str(accuracy)),
            selfie_url     = selfie_url,
            face_verified  = face_verified,
            face_confidence = face_confidence,
        )
        db.add(record)

    db.flush()
    write_audit(db, "AttendanceRecord", record.id, "CLOCK_IN",
                changed_by=current_user.id, after={"employee_id": emp.id, "date": str(today),
                "face_verified": face_verified, "face_confidence": str(face_confidence or "")})
    db.commit()
    db.refresh(record)

    # Warn if face not verified
    if selfie and emp.face_embedding and not face_verified:
        push_to_role(db, RoleName.GA,
                     "Absensi: Wajah Tidak Terverifikasi",
                     f"{emp.full_name} clock-in tapi wajah tidak cocok (confidence: {face_confidence})",
                     "/hris/attendance")
        db.commit()

    return record


# ─── Attendance: clock-out ────────────────────────────────────────────────────

@router.post("/attendance/clock-out", response_model=AttendanceRecordResponse,
             summary="Clock out — calculates hours worked")
def clock_out(
    current_user: CurrentUser,
    db:           Annotated[Session, Depends(get_db)],
    employee_id:  int | None = Query(None),
    is_holiday:   bool       = Query(False),
    note:         str | None = Query(None),
):
    if employee_id:
        emp = db.query(Employee).filter(Employee.id == employee_id).first()
        if not emp:
            raise HTTPException(404, "Employee not found")
        if emp.user_id != current_user.id and current_user.role.name not in _hr_roles:
            raise HTTPException(403, "Can only clock out for yourself")
    else:
        emp = db.query(Employee).filter(Employee.user_id == current_user.id).first()
        if not emp:
            raise HTTPException(404, "No employee record linked to your account")

    today  = datetime.now(timezone.utc).date()
    now    = datetime.now(timezone.utc)

    record = db.query(AttendanceRecord).filter(
        AttendanceRecord.employee_id == emp.id,
        AttendanceRecord.date        == today,
    ).first()

    if not record:
        raise HTTPException(409, "No clock-in found for today. Please clock in first.")
    if record.clock_out:
        raise HTTPException(409, "Already clocked out today")

    record.clock_out = now
    if record.clock_in:
        weekend = _is_weekend(today)
        reg, ot_wd, ot_we, ot_hol = _calculate_overtime(
            record.clock_in, now, weekend, is_holiday
        )
        record.hours_regular          = reg
        record.hours_overtime_weekday = ot_wd
        record.hours_overtime_weekend = ot_we
        record.hours_overtime_holiday = ot_hol

    if note:
        record.note = note

    write_audit(db, "AttendanceRecord", record.id, "CLOCK_OUT",
                changed_by=current_user.id,
                after={"clock_out": str(now), "hours_regular": str(record.hours_regular or "")})
    db.commit()
    db.refresh(record)
    return record


# ─── Attendance: manual entry (HR admin) ─────────────────────────────────────

@router.post("/attendance", response_model=AttendanceRecordResponse, status_code=201,
             summary="Manual attendance entry (HR admin)")
def create_attendance_manual(
    request:      Request,
    payload:      AttendanceManualCreate,
    current_user: Annotated[CurrentUser, Depends(require_role(*_hr_roles))],
    db:           Annotated[Session, Depends(get_db)],
):
    emp = db.query(Employee).filter(Employee.id == payload.employee_id).first()
    if not emp:
        raise HTTPException(404, "Employee not found")

    existing = db.query(AttendanceRecord).filter(
        AttendanceRecord.employee_id == payload.employee_id,
        AttendanceRecord.date        == payload.date,
    ).first()
    if existing:
        raise HTTPException(409, "Attendance record already exists for this date. Use PATCH to update.")

    # Auto-calculate hours if clock_in and clock_out provided
    reg = payload.hours_regular
    ot_wd = payload.hours_overtime_weekday
    ot_we = payload.hours_overtime_weekend
    ot_hol = payload.hours_overtime_holiday

    if payload.clock_in and payload.clock_out and reg is None:
        weekend = _is_weekend(payload.date)
        reg, ot_wd, ot_we, ot_hol = _calculate_overtime(
            payload.clock_in, payload.clock_out, weekend, False
        )

    record = AttendanceRecord(
        employee_id            = payload.employee_id,
        date                   = payload.date,
        clock_in               = payload.clock_in,
        clock_out              = payload.clock_out,
        hours_regular          = reg,
        hours_overtime_weekday = ot_wd,
        hours_overtime_weekend = ot_we,
        hours_overtime_holiday = ot_hol,
        source                 = AttendanceSource.MANUAL,
        note                   = payload.note,
        face_verified          = False,
    )
    db.add(record)
    db.flush()
    write_audit(db, "AttendanceRecord", record.id, "MANUAL_CREATE",
                changed_by=current_user.id, ip_address=get_client_ip(request),
                after=model_to_dict(record))
    db.commit()
    db.refresh(record)
    return record


# ─── Attendance: list ─────────────────────────────────────────────────────────

@router.get("/attendance", response_model=PaginatedResponse[AttendanceRecordResponse],
            summary="List attendance records")
def list_attendance(
    current_user: CurrentUser,
    db:           Annotated[Session, Depends(get_db)],
    employee_id:  int | None = Query(None),
    date_from:    date | None = Query(None),
    date_to:      date | None = Query(None),
    skip:         int         = Query(0, ge=0),
    limit:        int         = Query(50, ge=1, le=200),
):
    q = db.query(AttendanceRecord)

    # Non-admin users can only see their own records
    if current_user.role.name not in (*_hr_roles, RoleName.MD, RoleName.PM, RoleName.COST_CONTROL, RoleName.FINANCE):
        my_emp = db.query(Employee).filter(Employee.user_id == current_user.id).first()
        if my_emp:
            q = q.filter(AttendanceRecord.employee_id == my_emp.id)
        else:
            return {"items": [], "total": 0}
    elif employee_id:
        q = q.filter(AttendanceRecord.employee_id == employee_id)

    if date_from:
        q = q.filter(AttendanceRecord.date >= date_from)
    if date_to:
        q = q.filter(AttendanceRecord.date <= date_to)

    total = q.count()
    items = q.order_by(AttendanceRecord.date.desc()).offset(skip).limit(limit).all()
    return {"items": items, "total": total}


# ─── Attendance: monthly summary ─────────────────────────────────────────────

@router.get("/attendance/summary", summary="Monthly attendance summary per employee")
def attendance_summary(
    current_user: CurrentUser,
    db:           Annotated[Session, Depends(get_db)],
    year:         int = Query(...),
    month:        int = Query(...),
    dept_id:      int | None = Query(None),
):
    from calendar import monthrange
    first_day = date(year, month, 1)
    last_day  = date(year, month, monthrange(year, month)[1])

    q = (
        db.query(
            AttendanceRecord.employee_id,
            func.count(AttendanceRecord.id).label("days_present"),
            func.coalesce(func.sum(AttendanceRecord.hours_regular),          0).label("hours_regular"),
            func.coalesce(func.sum(AttendanceRecord.hours_overtime_weekday),  0).label("hours_ot_weekday"),
            func.coalesce(func.sum(AttendanceRecord.hours_overtime_weekend),  0).label("hours_ot_weekend"),
            func.coalesce(func.sum(AttendanceRecord.hours_overtime_holiday),  0).label("hours_ot_holiday"),
        )
        .filter(
            AttendanceRecord.date >= first_day,
            AttendanceRecord.date <= last_day,
        )
        .group_by(AttendanceRecord.employee_id)
    )

    if dept_id:
        q = q.join(Employee, Employee.id == AttendanceRecord.employee_id).filter(
            Employee.dept_id == dept_id
        )

    rows = q.all()
    emp_ids = [r.employee_id for r in rows]
    emp_map = {
        e.id: e for e in db.query(Employee).filter(Employee.id.in_(emp_ids)).all()
    }

    result = []
    for r in rows:
        emp = emp_map.get(r.employee_id)
        if not emp:
            continue
        result.append({
            "employee_id":    r.employee_id,
            "employee_no":    emp.employee_no,
            "full_name":      emp.full_name,
            "days_present":   r.days_present,
            "hours_regular":  str(r.hours_regular),
            "hours_ot_total": str(
                Decimal(str(r.hours_ot_weekday)) +
                Decimal(str(r.hours_ot_weekend)) +
                Decimal(str(r.hours_ot_holiday))
            ),
        })

    return result


# ─── Face: register template (HR admin) ──────────────────────────────────────

@router.post("/employees/{emp_id}/face", summary="Register employee face template")
async def register_face_template(
    emp_id:       int,
    photo:        UploadFile,
    current_user: Annotated[CurrentUser, Depends(require_role(*_hr_roles))],
    db:           Annotated[Session, Depends(get_db)],
):
    emp = db.query(Employee).filter(Employee.id == emp_id).first()
    if not emp:
        raise HTTPException(404, "Employee not found")

    try:
        from app.hris_face import register_face
        photo_bytes = await photo.read()
        embedding   = register_face(photo_bytes)
    except RuntimeError as e:
        raise HTTPException(503, str(e))
    except ValueError as e:
        raise HTTPException(422, str(e))

    emp.face_embedding = embedding
    db.commit()
    return MessageResponse(message=f"Face template registered for {emp.full_name} ({len(embedding)} dimensions)")


# ─── Leave Types ─────────────────────────────────────────────────────────────

@router.get("/leave-types", response_model=list[LeaveTypeResponse], summary="List leave types")
def list_leave_types(
    _:  CurrentUser,
    db: Annotated[Session, Depends(get_db)],
    active_only: bool = True,
):
    q = db.query(LeaveType)
    if active_only:
        q = q.filter(LeaveType.is_active == True)
    return q.order_by(LeaveType.id).all()


@router.post("/leave-types", response_model=LeaveTypeResponse, status_code=201,
             summary="Create leave type")
def create_leave_type(
    payload:      LeaveTypeCreate,
    current_user: Annotated[CurrentUser, Depends(require_role(RoleName.SUPER_ADMIN, RoleName.MD))],
    db:           Annotated[Session, Depends(get_db)],
):
    if db.query(LeaveType).filter(LeaveType.code == payload.code).first():
        raise HTTPException(409, "Leave type code already exists")
    lt = LeaveType(**payload.model_dump())
    db.add(lt)
    db.commit()
    db.refresh(lt)
    return lt


# ─── Leave Balance ────────────────────────────────────────────────────────────

@router.get("/leave-balance/{employee_id}", response_model=list[LeaveBalanceResponse],
            summary="Get leave balances for employee (current year)")
def get_leave_balance(
    employee_id:  int,
    current_user: CurrentUser,
    db:           Annotated[Session, Depends(get_db)],
    year:         int = Query(default=None),
):
    if year is None:
        year = datetime.now(timezone.utc).year

    # Ensure employee exists
    emp = db.query(Employee).filter(Employee.id == employee_id).first()
    if not emp:
        raise HTTPException(404, "Employee not found")

    # Self-service: staff can only see their own balance
    if current_user.role.name not in (*_hr_roles, RoleName.MD, RoleName.PM):
        my_emp = db.query(Employee).filter(Employee.user_id == current_user.id).first()
        if not my_emp or my_emp.id != employee_id:
            raise HTTPException(403, "Access denied")

    balances = (
        db.query(LeaveBalance)
        .filter(LeaveBalance.employee_id == employee_id, LeaveBalance.year == year)
        .all()
    )
    return balances


@router.post("/leave-balance/seed", summary="Seed leave balances for all active employees (HR admin)")
def seed_leave_balances(
    current_user: Annotated[CurrentUser, Depends(require_role(*_hr_roles, RoleName.MD))],
    db:           Annotated[Session, Depends(get_db)],
    year:         int = Query(default=None),
):
    """Ensure every active employee has a balance row for each active leave type."""
    if year is None:
        year = datetime.now(timezone.utc).year

    employees   = db.query(Employee).filter(Employee.status == "active").all()
    leave_types = db.query(LeaveType).filter(LeaveType.is_active == True).all()
    created = 0

    for emp in employees:
        for lt in leave_types:
            existing = db.query(LeaveBalance).filter(
                LeaveBalance.employee_id   == emp.id,
                LeaveBalance.leave_type_id == lt.id,
                LeaveBalance.year          == year,
            ).first()
            if not existing:
                db.add(LeaveBalance(
                    employee_id   = emp.id,
                    leave_type_id = lt.id,
                    year          = year,
                    accrued       = lt.max_days_per_year or 0,
                    used          = 0,
                ))
                created += 1

    db.commit()
    return MessageResponse(message=f"Seeded {created} leave balance rows for {year}")


# ─── Leave Requests ───────────────────────────────────────────────────────────

@router.get("/leave-requests", response_model=PaginatedResponse[LeaveRequestResponse],
            summary="List leave requests")
def list_leave_requests(
    current_user: CurrentUser,
    db:           Annotated[Session, Depends(get_db)],
    employee_id:  int | None                = Query(None),
    req_status:   LeaveRequestStatus | None = Query(None, alias="status"),
    skip:         int                       = Query(0, ge=0),
    limit:        int                       = Query(50, ge=1, le=200),
):
    q = db.query(LeaveRequest)

    # Self-service: non-managers see only their own
    if current_user.role.name not in (*_mgr_roles, RoleName.FINANCE, RoleName.COST_CONTROL):
        my_emp = db.query(Employee).filter(Employee.user_id == current_user.id).first()
        if my_emp:
            q = q.filter(LeaveRequest.employee_id == my_emp.id)
        else:
            return {"items": [], "total": 0}
    elif employee_id:
        q = q.filter(LeaveRequest.employee_id == employee_id)

    if req_status:
        q = q.filter(LeaveRequest.status == req_status)

    total = q.count()
    items = q.order_by(LeaveRequest.id.desc()).offset(skip).limit(limit).all()
    return {"items": items, "total": total}


@router.post("/leave-requests", response_model=LeaveRequestResponse, status_code=201,
             summary="Submit a leave request")
def submit_leave_request(
    request:      Request,
    payload:      LeaveRequestCreate,
    current_user: CurrentUser,
    db:           Annotated[Session, Depends(get_db)],
):
    emp = db.query(Employee).filter(Employee.id == payload.employee_id).first()
    if not emp:
        raise HTTPException(404, "Employee not found")

    # Self-service guard
    if emp.user_id != current_user.id and current_user.role.name not in _hr_roles:
        raise HTTPException(403, "Can only submit leave for yourself")

    lt = db.query(LeaveType).filter(
        LeaveType.id == payload.leave_type_id, LeaveType.is_active == True
    ).first()
    if not lt:
        raise HTTPException(404, "Leave type not found or inactive")

    # Calculate business days (simple: calendar days including weekends for now)
    delta = (payload.end_date - payload.start_date).days + 1

    # Check balance
    year = payload.start_date.year
    balance = db.query(LeaveBalance).filter(
        LeaveBalance.employee_id   == emp.id,
        LeaveBalance.leave_type_id == lt.id,
        LeaveBalance.year          == year,
    ).first()

    if balance and lt.max_days_per_year is not None:
        if balance.remaining < delta:
            raise HTTPException(422, f"Insufficient leave balance: {balance.remaining} days remaining")

    # Build approval chain
    chain = _LEAVE_APPROVAL_CHAIN if lt.requires_approval else []

    req = LeaveRequest(
        employee_id           = emp.id,
        leave_type_id         = lt.id,
        start_date            = payload.start_date,
        end_date              = payload.end_date,
        days                  = delta,
        reason                = payload.reason,
        status                = LeaveRequestStatus.SUBMITTED if chain else LeaveRequestStatus.APPROVED,
        approval_chain        = chain,
        approval_step         = 0,
        current_approver_role = chain[0] if chain else None,
        approval_history      = [{
            "action": "SUBMIT",
            "role": None,
            "user_id": current_user.id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "note": None,
        }],
        submitted_by = current_user.id,
    )
    db.add(req)
    db.flush()

    # If auto-approved (no approval required), deduct balance
    if req.status == LeaveRequestStatus.APPROVED:
        _deduct_balance(db, emp.id, lt.id, year, delta)

    write_audit(db, "LeaveRequest", req.id, "SUBMIT",
                changed_by=current_user.id, ip_address=get_client_ip(request),
                after=model_to_dict(req))
    db.commit()
    db.refresh(req)

    # Notify approver
    if chain:
        push_to_role(db, RoleName[chain[0]],
                     "Pengajuan Cuti Baru",
                     f"{emp.full_name} mengajukan cuti {lt.name} {delta} hari",
                     "/hris/leave")
        db.commit()

    return req


@router.post("/leave-requests/{req_id}/approve", response_model=LeaveRequestResponse,
             summary="Approve a leave request")
def approve_leave_request(
    req_id:       int,
    request:      Request,
    payload:      LeaveActionRequest,
    current_user: Annotated[CurrentUser, Depends(require_role(*_mgr_roles))],
    db:           Annotated[Session, Depends(get_db)],
):
    req = _get_leave_or_404(req_id, db)

    if req.status != LeaveRequestStatus.SUBMITTED:
        raise HTTPException(409, f"Cannot approve: status is '{req.status.value}'")

    # Verify current user's role matches expected approver
    if (req.current_approver_role and
            current_user.role.name.value != req.current_approver_role and
            current_user.role.name != RoleName.SUPER_ADMIN):
        raise HTTPException(403, f"Approval expected from role: {req.current_approver_role}")

    chain = req.approval_chain or []
    step  = req.approval_step + 1

    _add_leave_history(req, current_user.id, "APPROVE", payload.note)

    if step >= len(chain):
        # Final approval
        req.status                = LeaveRequestStatus.APPROVED
        req.current_approver_role = None
        req.approved_by           = current_user.id
        req.approval_step         = step
        # Deduct balance
        _deduct_balance(db, req.employee_id, req.leave_type_id, req.start_date.year, req.days)
        # Notify employee
        if req.employee and req.employee.user_id:
            push(db, req.employee.user_id,
                 "Cuti Disetujui",
                 f"Pengajuan cuti {req.leave_type.name} {req.days} hari telah disetujui",
                 "/hris/leave")
    else:
        # Advance to next approver
        req.approval_step         = step
        req.current_approver_role = chain[step]

    write_audit(db, "LeaveRequest", req.id, "APPROVE",
                changed_by=current_user.id, ip_address=get_client_ip(request))
    db.commit()
    db.refresh(req)
    return req


@router.post("/leave-requests/{req_id}/reject", response_model=LeaveRequestResponse,
             summary="Reject a leave request")
def reject_leave_request(
    req_id:       int,
    request:      Request,
    payload:      LeaveActionRequest,
    current_user: Annotated[CurrentUser, Depends(require_role(*_mgr_roles))],
    db:           Annotated[Session, Depends(get_db)],
):
    req = _get_leave_or_404(req_id, db)

    if req.status != LeaveRequestStatus.SUBMITTED:
        raise HTTPException(409, f"Cannot reject: status is '{req.status.value}'")

    _add_leave_history(req, current_user.id, "REJECT", payload.note)
    req.status = LeaveRequestStatus.REJECTED

    write_audit(db, "LeaveRequest", req.id, "REJECT",
                changed_by=current_user.id, ip_address=get_client_ip(request))
    db.commit()
    db.refresh(req)

    # Notify employee
    if req.employee and req.employee.user_id:
        push(db, req.employee.user_id,
             "Cuti Ditolak",
             f"Pengajuan cuti {req.leave_type.name} {req.days} hari ditolak. {payload.note or ''}",
             "/hris/leave")
        db.commit()

    return req


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _get_leave_or_404(req_id: int, db: Session) -> LeaveRequest:
    req = db.query(LeaveRequest).filter(LeaveRequest.id == req_id).first()
    if not req:
        raise HTTPException(404, "Leave request not found")
    return req


def _add_leave_history(req: LeaveRequest, actor_id: int, action: str, note: str | None):
    history = list(req.approval_history or [])
    history.append({
        "action":    action,
        "role":      req.current_approver_role,
        "user_id":   actor_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "note":      note,
    })
    req.approval_history = history


def _deduct_balance(db: Session, employee_id: int, leave_type_id: int, year: int, days: int):
    balance = db.query(LeaveBalance).filter(
        LeaveBalance.employee_id   == employee_id,
        LeaveBalance.leave_type_id == leave_type_id,
        LeaveBalance.year          == year,
    ).first()
    if balance:
        balance.used = min(balance.accrued, balance.used + days)

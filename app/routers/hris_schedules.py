"""Shift setup and clarification permissions deliberately exclude inherited roles."""
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import Annotated, Literal
from fastapi import APIRouter, Depends, HTTPException
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field, AwareDatetime, ConfigDict
from sqlalchemy.orm import Session

from app.audit import write_audit
from app.database import get_db
from app.dependencies import CurrentUser
from app.hris_access import ensure_employee_can_use_self_service
from app.hris_schedule_service import snapshot, current_assignment, close_due_for_employee, apply_hours, ensure_weekly_assignments
from app.models import (WorkShift, ShiftAssignment, WorkLocation, Employee, AttendanceRecord,
    AttendanceClarification, RoleName, AuditLog, OvertimeRequest, OvertimeRequestStatus,
    PayrollPeriod, PayrollStatus, WeeklySchedule)
from app.notify import push

router = APIRouter(prefix="/hris/scheduling", tags=["HRIS schedules"])
DB = Annotated[Session, Depends(get_db)]


def model_to_dict(row):
    return jsonable_encoder({column.key: getattr(row, column.key) for column in row.__table__.columns})


def manager(user):
    if user.role.name not in (RoleName.SUPER_ADMIN, RoleName.HR):
        raise HTTPException(403, "Only Super Admin and HR can manage schedules and clarifications")


def employee(db, user):
    emp = db.query(Employee).filter(Employee.user_id == user.id).first()
    if not emp:
        raise HTTPException(409, "No employee record linked to your account. Contact HR.")
    ensure_employee_can_use_self_service(emp)
    return emp


class ShiftInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=100)
    start_time: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    break_start_minutes: int = Field(default=240, ge=0, le=480)
    grace_minutes: int = Field(default=15, ge=0, le=120)
    reminder_minutes: int = Field(default=10, ge=0, le=120)
    is_active: bool = True


class AssignmentInput(BaseModel):
    employee_ids: list[int] = Field(default_factory=list, max_length=500)
    work_group_id: int | None = None
    shift_id: int
    work_location_id: int
    start_date: date
    end_date: date
    weekdays: list[int] = Field(min_length=1, max_length=7)
    replace_existing: bool = False


class WeeklyScheduleInput(BaseModel):
    employee_ids: list[int] = Field(default_factory=list, max_length=500)
    work_group_id: int | None = None
    shift_id: int
    work_location_id: int
    weekdays: list[int] = Field(min_length=1, max_length=7)


def clear_unused_schedules(db, emp_id, now):
    """Replace only unconsumed current/future dates; used snapshots are immutable."""
    records = db.query(AttendanceRecord).filter(AttendanceRecord.employee_id == emp_id).all()
    protected = {r.date for r in records}
    for row in db.query(ShiftAssignment).filter(ShiftAssignment.employee_id == emp_id,
        ShiftAssignment.date >= (now - timedelta(days=1)).date()).all():
        local_today = now.astimezone(ZoneInfo(row.snapshot["timezone"])).date()
        if row.date >= local_today and row.date not in protected:
            db.delete(row)
    db.flush()
    return records


@router.get("/weekly-schedules")
def weekly_schedules(cu: CurrentUser, db: DB):
    manager(cu)
    return [{**model_to_dict(r), "employee_name": r.employee.full_name}
            for r in db.query(WeeklySchedule).order_by(WeeklySchedule.employee_id)]


@router.post("/weekly-schedules")
def set_weekly_schedule(payload: WeeklyScheduleInput, cu: CurrentUser, db: DB):
    manager(cu)
    if any(day not in range(7) for day in payload.weekdays):
        raise HTTPException(422, "Pilih hari kerja yang valid.")
    shift, loc = db.get(WorkShift, payload.shift_id), db.get(WorkLocation, payload.work_location_id)
    if not shift or not shift.is_active or not loc or not loc.is_active:
        raise HTTPException(422, "Pilih shift dan lokasi kerja yang aktif.")
    ids = set(payload.employee_ids)
    if payload.work_group_id:
        ids.update(r[0] for r in db.query(Employee.id).filter(Employee.work_group_id == payload.work_group_id))
    if not ids or len(ids) > 500:
        raise HTTPException(422, "Pilih 1 hingga 500 karyawan.")
    employees = db.query(Employee).filter(Employee.id.in_(ids)).order_by(Employee.id).with_for_update().all()
    if len(employees) != len(ids):
        raise HTTPException(422, "Karyawan tidak ditemukan.")
    now = datetime.now(timezone.utc)
    today = now.astimezone(ZoneInfo(loc.timezone_name)).date()
    preserved = 0
    for emp in employees:
        ensure_employee_can_use_self_service(emp)
        row = db.query(WeeklySchedule).filter(WeeklySchedule.employee_id == emp.id).first()
        before = model_to_dict(row) if row else None
        records = clear_unused_schedules(db, emp.id, now)
        has_today = any(r.date == today for r in records)
        if has_today or any(r.clock_in and not r.clock_out and not r.auto_closed_at for r in records):
            preserved += 1
        if not row:
            row = WeeklySchedule(employee_id=emp.id)
            db.add(row)
        row.shift_id, row.work_location_id = shift.id, loc.id
        row.weekdays = sorted(set(payload.weekdays))
        row.snapshot = snapshot(shift, loc, today)
        row.effective_from = today + timedelta(days=1) if has_today else today
        row.is_active = True
        db.flush()
        ensure_weekly_assignments(db, emp.id, now)
        write_audit(db, "WeeklySchedule", row.id, "UPDATE" if before else "CREATE", changed_by=cu.id,
                    before=before, after=model_to_dict(row))
    db.commit()
    return {"assigned": len(employees), "preserved_sessions": preserved}


@router.delete("/weekly-schedules/{schedule_id}")
def deactivate_weekly_schedule(schedule_id: int, cu: CurrentUser, db: DB):
    manager(cu)
    row = db.get(WeeklySchedule, schedule_id)
    if not row:
        raise HTTPException(404, "Jadwal tidak ditemukan.")
    db.query(Employee).filter(Employee.id == row.employee_id).with_for_update().one()
    db.refresh(row)
    before = model_to_dict(row)
    clear_unused_schedules(db, row.employee_id, datetime.now(timezone.utc))
    row.is_active = False
    write_audit(db, "WeeklySchedule", row.id, "DEACTIVATE", changed_by=cu.id, before=before, after=model_to_dict(row))
    db.commit()
    return {"message": "Jadwal dinonaktifkan. Sesi yang sudah absen tetap tersimpan."}


@router.get("/shifts")
def shifts(cu: CurrentUser, db: DB):
    manager(cu)
    return [model_to_dict(s) for s in db.query(WorkShift).order_by(WorkShift.id).all()]


@router.post("/shifts")
def create_shift(payload: ShiftInput, cu: CurrentUser, db: DB):
    manager(cu)
    row = WorkShift(**payload.model_dump())
    db.add(row)
    db.flush()
    write_audit(db, "WorkShift", row.id, "CREATE", changed_by=cu.id, after=model_to_dict(row))
    db.commit()
    return model_to_dict(row)


@router.put("/shifts/{shift_id}")
def update_shift(shift_id: int, payload: ShiftInput, cu: CurrentUser, db: DB):
    manager(cu)
    row = db.get(WorkShift, shift_id)
    if not row:
        raise HTTPException(404, "Shift not found")
    before = model_to_dict(row)
    for key, value in payload.model_dump().items():
        setattr(row, key, value)
    write_audit(db, "WorkShift", row.id, "UPDATE", changed_by=cu.id, before=before, after=model_to_dict(row))
    db.commit()
    return model_to_dict(row)


@router.post("/assignments")
def assign(payload: AssignmentInput, cu: CurrentUser, db: DB):
    manager(cu)
    if not 0 <= (payload.end_date - payload.start_date).days <= 365 or any(d not in range(7) for d in payload.weekdays):
        raise HTTPException(422, "Choose valid weekdays and a period of at most 366 days")
    shift = db.get(WorkShift, payload.shift_id)
    loc = db.get(WorkLocation, payload.work_location_id)
    if not shift or not shift.is_active or not loc or not loc.is_active:
        raise HTTPException(422, "Choose an active shift and work location")
    ids = set(payload.employee_ids)
    if payload.work_group_id:
        ids.update(row[0] for row in db.query(Employee.id).filter(Employee.work_group_id == payload.work_group_id))
    if not ids or len(ids) > 500:
        raise HTTPException(422, "Select between 1 and 500 employees")
    selected_days = sum((payload.start_date + timedelta(days=n)).weekday() in payload.weekdays
                        for n in range((payload.end_date - payload.start_date).days + 1))
    if not selected_days or selected_days * len(ids) > 5000:
        raise HTTPException(422, "Select working days and create at most 5000 assignments per submission")
    employees = db.query(Employee).filter(Employee.id.in_(ids)).order_by(Employee.id).with_for_update().all()
    if len(employees) != len(ids):
        raise HTTPException(422, "Employee not found")
    now = datetime.now(timezone.utc)
    count = 0
    for emp in employees:
        ensure_employee_can_use_self_service(emp)
        if db.query(WeeklySchedule.id).filter(WeeklySchedule.employee_id == emp.id, WeeklySchedule.is_active.is_(True)).first():
            raise HTTPException(409, "Karyawan memiliki jadwal mingguan aktif. Ubah melalui pengaturan jadwal mingguan.")
        for offset in range((payload.end_date - payload.start_date).days + 1):
            day = payload.start_date + timedelta(days=offset)
            if day.weekday() not in payload.weekdays:
                continue
            data = snapshot(shift, loc, day)
            start, end = datetime.fromisoformat(data["starts_at"]), datetime.fromisoformat(data["ends_at"])
            if start <= now:
                raise HTTPException(409, "Schedules can only be assigned before their start time")
            if db.query(AttendanceRecord.id).filter(AttendanceRecord.employee_id == emp.id, AttendanceRecord.date == day).first():
                raise HTTPException(409, f"Attendance already exists for {emp.full_name} on {day}")
            existing = db.query(ShiftAssignment).filter(ShiftAssignment.employee_id == emp.id, ShiftAssignment.date == day).first()
            if existing and (not payload.replace_existing or existing.starts_at <= now):
                raise HTTPException(409, f"Schedule already exists for {emp.full_name} on {day}. Enable replacement for future schedules.")
            overlap = db.query(ShiftAssignment).filter(ShiftAssignment.employee_id == emp.id,
                ShiftAssignment.starts_at < end, ShiftAssignment.ends_at > start)
            if existing:
                overlap = overlap.filter(ShiftAssignment.id != existing.id)
            if overlap.first():
                raise HTTPException(409, f"Overlapping shift for {emp.full_name} on {day}")
            before = model_to_dict(existing) if existing else None
            row = existing or ShiftAssignment(employee_id=emp.id, date=day)
            row.shift_id, row.work_location_id = shift.id, loc.id
            row.snapshot, row.starts_at, row.ends_at = data, start, end
            db.add(row)
            db.flush()
            write_audit(db, "ShiftAssignment", row.id, "UPDATE" if existing else "CREATE",
                        changed_by=cu.id, before=before, after=model_to_dict(row))
            count += 1
    db.commit()
    return {"assigned": count}


@router.get("/assignments")
def assignments(cu: CurrentUser, db: DB, start_date: date, end_date: date, employee_id: int | None = None):
    manager(cu)
    if not 0 <= (end_date - start_date).days <= 365:
        raise HTTPException(422, "Choose a period of at most 366 days")
    q = db.query(ShiftAssignment).filter(ShiftAssignment.date.between(start_date, end_date))
    if employee_id:
        q = q.filter(ShiftAssignment.employee_id == employee_id)
    return [{**model_to_dict(r), "employee_name": r.employee.full_name} for r in q.order_by(ShiftAssignment.date, ShiftAssignment.employee_id).limit(5000)]


@router.delete("/assignments/{assignment_id}")
def cancel(assignment_id: int, cu: CurrentUser, db: DB):
    manager(cu)
    row = db.get(ShiftAssignment, assignment_id)
    if not row:
        raise HTTPException(404, "Schedule not found")
    db.query(Employee).filter(Employee.id == row.employee_id).with_for_update().first()
    if row.snapshot.get("weekly_schedule_id"):
        raise HTTPException(409, "Ubah atau nonaktifkan jadwal melalui pengaturan jadwal mingguan.")
    if row.starts_at <= datetime.now(timezone.utc) or db.query(AttendanceRecord.id).filter(
        AttendanceRecord.employee_id == row.employee_id, AttendanceRecord.date == row.date).first():
        raise HTTPException(409, "A started schedule cannot be cancelled")
    write_audit(db, "ShiftAssignment", row.id, "DELETE", changed_by=cu.id, before=model_to_dict(row))
    db.delete(row)
    db.commit()
    return {"message": "Schedule cancelled"}


@router.get("/me")
def mine(cu: CurrentUser, db: DB):
    emp = employee(db, cu)
    now = datetime.now(timezone.utc)
    close_due_for_employee(db, emp.id, now)
    db.commit()
    selected = current_assignment(db, emp.id, now)
    open_record = db.query(AttendanceRecord).filter(AttendanceRecord.employee_id == emp.id,
        AttendanceRecord.clock_in.isnot(None), AttendanceRecord.clock_out.is_(None),
        AttendanceRecord.auto_closed_at.is_(None)).order_by(AttendanceRecord.date.desc()).first()
    upcoming = db.query(ShiftAssignment).filter(ShiftAssignment.employee_id == emp.id,
        ShiftAssignment.ends_at > now, ShiftAssignment.starts_at < now + timedelta(days=32)).order_by(ShiftAssignment.starts_at).all()
    unresolved = db.query(AttendanceRecord).filter(AttendanceRecord.employee_id == emp.id,
        AttendanceRecord.auto_closed_at.isnot(None), AttendanceRecord.clarification_status != "approved").order_by(AttendanceRecord.date.desc()).all()
    corrections = db.query(AttendanceClarification).join(AttendanceRecord).filter(
        AttendanceRecord.employee_id == emp.id).order_by(AttendanceClarification.id.desc()).limit(100).all()
    current = open_record.schedule_snapshot if open_record and open_record.schedule_snapshot else (selected.snapshot if selected else None)
    result = {"current": current,
            "upcoming": [r.snapshot for r in upcoming],
            "unresolved": [model_to_dict(r) for r in unresolved],
            "clarifications": [model_to_dict(r) for r in corrections]}
    db.commit()
    return result


class ClarificationInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    reason: Literal["forgot", "overtime", "other"]
    actual_clock_out: AwareDatetime
    note: str = Field(min_length=5, max_length=2000)


def validate_departure(db, record, actual):
    if actual <= record.clock_in or actual > datetime.now(timezone.utc):
        raise HTTPException(422, "Departure must be after clock-in and cannot be in the future")
    next_record = db.query(AttendanceRecord).filter(AttendanceRecord.employee_id == record.employee_id,
        AttendanceRecord.clock_in > record.clock_in, AttendanceRecord.id != record.id).order_by(AttendanceRecord.clock_in).first()
    if next_record and actual > next_record.clock_in:
        raise HTTPException(422, "Departure overlaps the next attendance session")


@router.post("/attendance/{record_id}/clarifications")
def clarify(record_id: int, payload: ClarificationInput, cu: CurrentUser, db: DB):
    emp = employee(db, cu)
    record = db.query(AttendanceRecord).filter(AttendanceRecord.id == record_id,
        AttendanceRecord.employee_id == emp.id).with_for_update().first()
    if not record or not record.auto_closed_at or record.clarification_status not in ("required", "rejected"):
        raise HTTPException(409, "This attendance is not awaiting a new clarification")
    validate_departure(db, record, payload.actual_clock_out)
    row = AttendanceClarification(attendance_id=record.id, **payload.model_dump())
    db.add(row)
    record.clarification_status = "pending"
    db.flush()
    write_audit(db, "AttendanceClarification", row.id, "SUBMIT", changed_by=cu.id, after=model_to_dict(row))
    db.commit()
    return model_to_dict(row)


@router.get("/clarifications")
def clarifications(cu: CurrentUser, db: DB):
    manager(cu)
    return [{**model_to_dict(r), "employee_name": r.attendance.employee.full_name,
             "attendance_date": r.attendance.date, "schedule": r.attendance.schedule_snapshot,
             "clock_in": r.attendance.clock_in}
            for r in db.query(AttendanceClarification).order_by(AttendanceClarification.id.desc()).limit(500)]


class ReviewInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    approve: bool
    note: str = Field(min_length=3, max_length=2000)


@router.post("/clarifications/{clarification_id}/review")
def review(clarification_id: int, payload: ReviewInput, cu: CurrentUser, db: DB):
    manager(cu)
    row = db.query(AttendanceClarification).filter(AttendanceClarification.id == clarification_id).with_for_update().first()
    if not row or row.status != "pending":
        raise HTTPException(409, "Clarification already reviewed or not found")
    record = db.query(AttendanceRecord).filter(AttendanceRecord.id == row.attendance_id).with_for_update().one()
    before = model_to_dict(record)
    if payload.approve:
        period = db.query(PayrollPeriod).filter(PayrollPeriod.year == record.date.year,
            PayrollPeriod.month == record.date.month).with_for_update().first()
        if period and period.status != PayrollStatus.OPEN:
            raise HTTPException(409, "Payroll period is locked. HR must resolve the payroll period before applying this correction.")
        validate_departure(db, record, row.actual_clock_out)
        record.clock_out = row.actual_clock_out
        apply_hours(db, record, row.actual_clock_out, allow_overtime=row.reason == "overtime")
        extra = sum((record.hours_overtime_weekday, record.hours_overtime_weekend, record.hours_overtime_holiday))
        if row.reason == "overtime" and extra > 0:
            # A reviewed overtime clarification is also an explicit HR approval.
            overtime = OvertimeRequest(employee_id=record.employee_id, date=record.date,
                planned_hours=extra, reason=row.note, status=OvertimeRequestStatus.APPROVED,
                approved_by=cu.id, approved_at=datetime.now(timezone.utc), attendance_id=record.id)
            db.add(overtime)
            db.flush()
            write_audit(db, "OvertimeRequest", overtime.id, "APPROVE", changed_by=cu.id, after=model_to_dict(overtime))
    row.status = record.clarification_status = "approved" if payload.approve else "rejected"
    row.reviewed_by, row.reviewed_at, row.review_note = cu.id, datetime.now(timezone.utc), payload.note
    write_audit(db, "AttendanceClarification", row.id, "REVIEW", changed_by=cu.id, after=model_to_dict(row))
    write_audit(db, "AttendanceRecord", record.id, "CLARIFICATION_REVIEW", changed_by=cu.id, before=before, after=model_to_dict(record))
    if record.employee.user_id:
        push(db, record.employee.user_id, "Attendance clarification reviewed", payload.note, "/hris/me/attendance")
    db.commit()
    return model_to_dict(row)


@router.get("/history")
def history(cu: CurrentUser, db: DB):
    manager(cu)
    return [model_to_dict(r) for r in db.query(AuditLog).filter(AuditLog.entity_type.in_(
        ["WorkShift", "ShiftAssignment", "WeeklySchedule", "AttendanceClarification", "AttendanceRecord", "WorkLocation"])).order_by(AuditLog.id.desc()).limit(200)]

"""Server-clock attendance schedules. All stored instants use UTC."""
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from math import ceil
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.audit import write_audit
from app.models import AttendanceRecord, ShiftAssignment, WorkShift, WorkLocation
from app.notify import push

AUTO_CLOSE_HOURS = 5


def snapshot(shift: WorkShift, location: WorkLocation, day: date) -> dict:
    start = datetime.combine(day, time.fromisoformat(shift.start_time), ZoneInfo(location.timezone_name))
    end = start + timedelta(hours=9)
    rest = start + timedelta(minutes=shift.break_start_minutes)
    return {
        "shift_name": shift.name, "shift_id": shift.id,
        "date": day.isoformat(), "timezone": location.timezone_name,
        "location_name": location.name, "work_location_id": location.id,
        "starts_at": start.astimezone(timezone.utc).isoformat(),
        "ends_at": end.astimezone(timezone.utc).isoformat(),
        "break_starts_at": rest.astimezone(timezone.utc).isoformat(),
        "break_ends_at": (rest + timedelta(hours=1)).astimezone(timezone.utc).isoformat(),
        "grace_minutes": shift.grace_minutes, "reminder_minutes": shift.reminder_minutes,
        "auto_close_hours": AUTO_CLOSE_HOURS,
    }


def current_assignment(db: Session, employee_id: int, now: datetime):
    # Allow clock-in up to two hours early. Never infer a shift from the phone.
    return db.query(ShiftAssignment).filter(
        ShiftAssignment.employee_id == employee_id,
        ShiftAssignment.starts_at <= now + timedelta(hours=2),
        ShiftAssignment.ends_at > now,
    ).order_by(ShiftAssignment.starts_at).first()


def attach_schedule(record: AttendanceRecord, assignment: ShiftAssignment, now: datetime):
    data = dict(assignment.snapshot)
    data["assignment_id"] = assignment.id
    record.schedule_snapshot = data
    record.auto_close_at = assignment.ends_at + timedelta(hours=AUTO_CLOSE_HOURS)
    record.reminder_at = assignment.ends_at - timedelta(minutes=data["reminder_minutes"])
    delta = max(0, (now - assignment.starts_at).total_seconds())
    record.late_minutes = ceil(delta / 60)
    record.beyond_grace_minutes = ceil(max(0, delta - data["grace_minutes"] * 60) / 60)


def close_due_record(db: Session, record: AttendanceRecord, now: datetime) -> bool:
    if record.clock_out or record.auto_closed_at or not record.auto_close_at or record.auto_close_at > now:
        return False
    record.auto_closed_at = now
    record.clarification_status = "required"
    # Unknown work duration must never be estimated using the cutoff timestamp.
    record.hours_regular = None
    record.hours_overtime_weekday = None
    record.hours_overtime_weekend = None
    record.hours_overtime_holiday = None
    write_audit(db, "AttendanceRecord", record.id, "AUTO_CLOSE", after={
        "deadline": record.auto_close_at.isoformat(), "processed_at": now.isoformat(),
        "status": "required",
    })
    if record.employee.user_id:
        push(db, record.employee.user_id, "Attendance needs clarification",
             "Your attendance was closed automatically. Submit your actual departure time and reason.",
             "/hris/me/attendance")
    return True


def close_due_for_employee(db: Session, employee_id: int, now: datetime):
    records = db.query(AttendanceRecord).filter(
        AttendanceRecord.employee_id == employee_id,
        AttendanceRecord.clock_out.is_(None), AttendanceRecord.auto_closed_at.is_(None),
        AttendanceRecord.auto_close_at <= now,
    ).with_for_update().all()
    for record in records:
        close_due_record(db, record, now)


def scheduled_hours(record: AttendanceRecord, actual_out: datetime, allow_overtime=True):
    data = record.schedule_snapshot
    if not data:
        raise HTTPException(409, "This attendance has no schedule snapshot")
    start = datetime.fromisoformat(data["starts_at"])
    end = datetime.fromisoformat(data["ends_at"])
    rest_start = datetime.fromisoformat(data["break_starts_at"])
    rest_end = datetime.fromisoformat(data["break_ends_at"])
    # Early arrival is not overtime. Deduct only the break overlapping attendance.
    actual_start = max(record.clock_in, start)
    effective_end = actual_out if allow_overtime else min(actual_out, end)
    span = max(0, (effective_end - actual_start).total_seconds())
    rest = max(0, (min(effective_end, rest_end) - max(actual_start, rest_start)).total_seconds())
    total = Decimal(str(max(0, span - rest) / 3600)).quantize(Decimal("0.01"))
    regular_end = min(effective_end, end)
    regular_span = max(0, (regular_end - actual_start).total_seconds())
    regular_rest = max(0, (min(regular_end, rest_end) - max(actual_start, rest_start)).total_seconds())
    regular = min(Decimal("8"), Decimal(str(max(0, regular_span - regular_rest) / 3600))).quantize(Decimal("0.01"))
    # Scheduled weekend shifts are regular work; approved extra hours remain OT.
    return regular, max(Decimal("0"), total - regular)


def apply_hours(db: Session, record: AttendanceRecord, actual_out: datetime, allow_overtime=True):
    from app.hris_attendance_service import is_holiday, is_weekend
    regular, extra = scheduled_hours(record, actual_out, allow_overtime)
    record.hours_regular = regular
    record.hours_overtime_weekday = Decimal("0")
    record.hours_overtime_weekend = Decimal("0")
    record.hours_overtime_holiday = Decimal("0")
    if is_holiday(db, record.date):
        record.hours_overtime_holiday = extra
    elif is_weekend(record.date):
        record.hours_overtime_weekend = extra
    else:
        record.hours_overtime_weekday = extra

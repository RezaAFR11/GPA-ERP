"""Server-clock attendance schedules. All stored instants use UTC."""
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from math import ceil
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.audit import write_audit
from app.models import AttendanceRecord, ShiftAssignment, WorkShift, WorkLocation, WeeklySchedule, Employee
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
    ensure_weekly_assignments(db, employee_id, now)
    # Allow clock-in up to two hours early. Never infer a shift from the phone.
    return db.query(ShiftAssignment).filter(
        ShiftAssignment.employee_id == employee_id,
        ShiftAssignment.starts_at <= now + timedelta(hours=2),
        ShiftAssignment.ends_at > now,
    ).order_by(ShiftAssignment.starts_at).first()


def weekly_snapshot(rule: WeeklySchedule, day: date) -> dict:
    """Rebase frozen local shift rules to a date, keeping overnight boundaries."""
    data = dict(rule.snapshot)
    zone = ZoneInfo(data["timezone"])
    original = datetime.fromisoformat(data["starts_at"]).astimezone(zone)
    start = datetime.combine(day, original.time().replace(tzinfo=None), zone)
    for key in ("starts_at", "ends_at", "break_starts_at", "break_ends_at"):
        offset = datetime.fromisoformat(data[key]).astimezone(zone) - original
        data[key] = (start + offset).astimezone(timezone.utc).isoformat()
    data["date"] = day.isoformat()
    data["weekly_schedule_id"] = rule.id
    return data


def ensure_weekly_assignments(db: Session, employee_id: int, now: datetime):
    # Use the same employee lock as HR changes and clock-in; no duplicate dates
    # when portal requests and clock-in arrive together. Caller owns the commit.
    db.query(Employee).filter(Employee.id == employee_id).with_for_update().first()
    rule = db.query(WeeklySchedule).filter(WeeklySchedule.employee_id == employee_id,
        WeeklySchedule.is_active.is_(True)).first()
    if not rule:
        return
    today = now.astimezone(ZoneInfo(rule.snapshot["timezone"])).date()
    first = max(rule.effective_from, today - timedelta(days=1))
    last = today + timedelta(days=32)
    rows = db.query(ShiftAssignment).filter(ShiftAssignment.employee_id == employee_id,
        ShiftAssignment.date.between(first - timedelta(days=1), last + timedelta(days=1))).all()
    occupied_dates = {r.date for r in rows}
    attended = db.query(AttendanceRecord).filter(AttendanceRecord.employee_id == employee_id,
        AttendanceRecord.date.between(first - timedelta(days=1), last + timedelta(days=1))).all()
    occupied_dates.update(r.date for r in attended)
    spans = [(r.starts_at, r.ends_at) for r in rows]
    for r in attended:
        if r.schedule_snapshot:
            spans.append((datetime.fromisoformat(r.schedule_snapshot["starts_at"]),
                          datetime.fromisoformat(r.schedule_snapshot["ends_at"])))
        elif r.clock_in:
            spans.append((r.clock_in, r.clock_out or now))
    for offset in range(max(0, (last - first).days + 1)):
        day = first + timedelta(days=offset)
        if day.weekday() not in rule.weekdays or day in occupied_dates:
            continue
        data = weekly_snapshot(rule, day)
        start, end = datetime.fromisoformat(data["starts_at"]), datetime.fromisoformat(data["ends_at"])
        if end <= now or any(a < end and b > start for a, b in spans):
            continue
        db.add(ShiftAssignment(employee_id=employee_id, shift_id=rule.shift_id,
            work_location_id=rule.work_location_id, date=day, snapshot=data,
            starts_at=start, ends_at=end))
        spans.append((start, end))
    db.flush()


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

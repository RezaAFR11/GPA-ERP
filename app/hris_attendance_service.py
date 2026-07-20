"""Attendance, geofence, leave-balance, and overtime domain helpers."""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import (
    AttendanceRecord,
    HolidayCalendar,
    LeaveBalance,
    LeaveCategory,
    LeaveType,
    OvertimeRequest,
    WorkLocation,
)


def calculate_overtime(
    clock_in: datetime,
    clock_out: datetime,
    is_weekend_day: bool,
    is_holiday_day: bool,
) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    """Return regular, weekday OT, weekend OT, and holiday OT hours."""
    max_daily_hours = Decimal("24")
    regular_hours_per_day = Decimal("8")
    total_hours = max(Decimal(0), min(
        Decimal(str((clock_out - clock_in).total_seconds() / 3600)),
        max_daily_hours,
    ))

    if is_holiday_day:
        return Decimal("0"), Decimal("0"), Decimal("0"), total_hours
    if is_weekend_day:
        return Decimal("0"), Decimal("0"), total_hours, Decimal("0")

    regular = min(total_hours, regular_hours_per_day)
    overtime = max(Decimal("0"), total_hours - regular_hours_per_day)
    return regular, overtime, Decimal("0"), Decimal("0")


def is_weekend(value: date) -> bool:
    return value.weekday() >= 5


def is_holiday(db: Session, attendance_date: date) -> bool:
    return (
        db.query(HolidayCalendar.id)
        .filter(HolidayCalendar.date == attendance_date)
        .first()
        is not None
    )


def leave_duration(
    db: Session,
    start_date: date,
    end_date: date,
) -> tuple[int, list[dict]]:
    """Count Monday-Friday leave days, excluding configured holidays."""
    if end_date < start_date:
        raise HTTPException(
            422,
            "end_date must be greater than or equal to start_date",
        )

    holidays = (
        db.query(HolidayCalendar)
        .filter(
            HolidayCalendar.date >= start_date,
            HolidayCalendar.date <= end_date,
        )
        .all()
    )
    holidays_by_date = {holiday.date: holiday for holiday in holidays}
    excluded_holidays: list[dict] = []
    working_days = 0

    for offset in range((end_date - start_date).days + 1):
        current_date = start_date + timedelta(days=offset)
        if is_weekend(current_date):
            continue
        holiday = holidays_by_date.get(current_date)
        if holiday:
            excluded_holidays.append({
                "date": holiday.date.isoformat(),
                "name": holiday.name,
            })
            continue
        working_days += 1

    return working_days, excluded_holidays


def leave_days_by_year(
    db: Session,
    start_date: date,
    end_date: date,
) -> dict[int, int]:
    allocations: dict[int, int] = {}
    for year in range(start_date.year, end_date.year + 1):
        segment_start = max(start_date, date(year, 1, 1))
        segment_end = min(end_date, date(year, 12, 31))
        days, _ = leave_duration(db, segment_start, segment_end)
        if days:
            allocations[year] = days
    return allocations


def get_or_create_leave_balance(
    db: Session,
    employee_id: int,
    leave_type: LeaveType,
    year: int,
    lock: bool = False,
) -> LeaveBalance:
    query = db.query(LeaveBalance).filter(
        LeaveBalance.employee_id == employee_id,
        LeaveBalance.leave_type_id == leave_type.id,
        LeaveBalance.year == year,
    )
    if lock:
        query = query.with_for_update()
    balance = query.first()
    if balance is None:
        balance = LeaveBalance(
            employee_id=employee_id,
            leave_type_id=leave_type.id,
            year=year,
            accrued=leave_type.max_days_per_year or 0,
            used=0,
        )
        db.add(balance)
        db.flush()
    return balance


def check_or_deduct_leave_balances(
    db: Session,
    employee_id: int,
    leave_type: LeaveType,
    allocations: dict[int, int],
    deduct: bool = False,
) -> None:
    if leave_type.category in (
        LeaveCategory.MATERNITY,
        LeaveCategory.PATERNITY,
    ):
        return
    if leave_type.max_days_per_year is None:
        return

    balances: list[tuple[LeaveBalance, int]] = []
    for year, days in allocations.items():
        balance = get_or_create_leave_balance(
            db,
            employee_id,
            leave_type,
            year,
            lock=deduct,
        )
        if balance.remaining < days:
            raise HTTPException(
                422,
                f"Insufficient {leave_type.name} balance for {year}: "
                f"{balance.remaining} days remaining, {days} required",
            )
        balances.append((balance, days))
    if deduct:
        for balance, days in balances:
            balance.used += days


def link_overtime_requests_to_attendance(
    db: Session,
    record: AttendanceRecord,
) -> list[int]:
    """Attach same-day overtime requests to an attendance record."""
    requests = (
        db.query(OvertimeRequest)
        .filter(
            OvertimeRequest.employee_id == record.employee_id,
            OvertimeRequest.date == record.date,
            OvertimeRequest.attendance_id.is_(None),
        )
        .all()
    )
    for overtime_request in requests:
        overtime_request.attendance_id = record.id
    return [overtime_request.id for overtime_request in requests]


def haversine(
    latitude_a: float,
    longitude_a: float,
    latitude_b: float,
    longitude_b: float,
) -> float:
    """Return great-circle distance in metres between two GPS points."""
    earth_radius_metres = 6_371_000
    phi_a = math.radians(latitude_a)
    phi_b = math.radians(latitude_b)
    phi_delta = math.radians(latitude_b - latitude_a)
    longitude_delta = math.radians(longitude_b - longitude_a)
    arc = (
        math.sin(phi_delta / 2) ** 2
        + math.cos(phi_a)
        * math.cos(phi_b)
        * math.sin(longitude_delta / 2) ** 2
    )
    return 2 * earth_radius_metres * math.asin(math.sqrt(arc))


def check_location(
    db: Session,
    latitude: float,
    longitude: float,
    assigned_location: WorkLocation | None = None,
) -> tuple[bool, WorkLocation | None, float]:
    """Match coordinates to the assigned or nearest active work location."""
    if assigned_location is not None:
        locations = [assigned_location]
    else:
        locations = (
            db.query(WorkLocation)
            .filter(WorkLocation.is_active.is_(True))
            .all()
        )

    best_location: WorkLocation | None = None
    best_distance = float("inf")
    for location in locations:
        distance = haversine(
            latitude,
            longitude,
            float(location.latitude),
            float(location.longitude),
        )
        if distance <= location.radius_meters and distance < best_distance:
            best_location = location
            best_distance = distance

    if best_location is None:
        nearest_distance = float("inf")
        for location in locations:
            distance = haversine(
                latitude,
                longitude,
                float(location.latitude),
                float(location.longitude),
            )
            nearest_distance = min(nearest_distance, distance)
        if nearest_distance == float("inf"):
            nearest_distance = 0.0
        return False, None, nearest_distance

    return True, best_location, best_distance

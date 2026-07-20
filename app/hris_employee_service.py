"""Employee account helpers and HRIS dashboard aggregation."""
from __future__ import annotations

import secrets
import string
from calendar import monthrange
from datetime import date, timedelta

from sqlalchemy import func as sql_func, or_
from sqlalchemy.orm import Session

from app.menu_permissions import ROLE_PRESETS
from app.models import (
    AppMenu,
    AttendanceRecord,
    Department,
    Employee,
    EmployeeStatus,
    EmploymentType,
    HolidayCalendar,
    LeaveBalance,
    LeaveType,
    RoleName,
    User,
    UserMenuPermission,
)
from app.schemas import (
    DeptAttendanceItem,
    EmployeeResponse,
    HeadcountTrendItem,
    HrisDashboardStats,
    PkwtAlertItem,
)


HR_DATA_ROLES = {
    RoleName.SUPER_ADMIN,
    RoleName.MD,
    RoleName.GA,
    RoleName.HR,
}
EMPLOYEE_SENSITIVE_FIELDS = (
    "nik",
    "npwp",
    "email",
    "phone",
    "bank_name",
    "bank_account",
    "bpjs_tk_no",
    "bpjs_kes_no",
)


def generate_password(length: int = 12) -> str:
    """Generate a password containing uppercase, digit, and symbol groups."""
    alphabet = string.ascii_letters + string.digits + "!@#$"
    while True:
        password = "".join(secrets.choice(alphabet) for _ in range(length))
        if (
            any(character.isupper() for character in password)
            and any(character.isdigit() for character in password)
            and any(character in "!@#$" for character in password)
        ):
            return password


def next_employee_number(db: Session) -> str:
    """Generate a unique employee number such as EMP0007."""
    sequence = db.query(Employee).count() + 1
    while (
        db.query(Employee)
        .filter(Employee.employee_no == f"EMP{sequence:04d}")
        .first()
    ):
        sequence += 1
    return f"EMP{sequence:04d}"


def seed_user_menus(db: Session, user: User) -> None:
    """Seed a new user's explicit permissions from the selected role preset."""
    menus = {
        menu.key: menu
        for menu in db.query(AppMenu).filter(AppMenu.is_active.is_(True)).all()
    }
    preset_keys = ROLE_PRESETS.get(
        user.role.name.value,
        ROLE_PRESETS["STAFF"],
    )
    for key in preset_keys:
        menu = menus.get(key)
        if menu:
            db.add(UserMenuPermission(
                user_id=user.id,
                menu_id=menu.id,
                can_access=True,
            ))


def employee_for_view(employee: Employee, current_user: User) -> dict:
    """Redact private HR/payroll data from directory-only viewers."""
    data = EmployeeResponse.model_validate(employee).model_dump()
    if current_user.role.name not in HR_DATA_ROLES:
        for field in EMPLOYEE_SENSITIVE_FIELDS:
            data[field] = None
        data["documents"] = []
        data["user"] = None
    return data


def _headcount_trend(
    employees: list[Employee],
    today: date,
    current_count: int,
) -> list[HeadcountTrendItem]:
    trend: list[HeadcountTrendItem] = []
    for month_offset in range(5, -1, -1):
        if today.month - month_offset <= 0:
            year = today.year - 1
            month = today.month - month_offset + 12
        else:
            year = today.year
            month = today.month - month_offset
        snapshot_date = date(year, month, monthrange(year, month)[1])
        count = sum(
            1
            for employee in employees
            if (employee.join_date is None or employee.join_date <= snapshot_date)
            and (employee.end_date is None or employee.end_date >= snapshot_date)
        )
        if month == today.month and year == today.year:
            count = current_count
        trend.append(HeadcountTrendItem(month=f"{year}-{month:02d}", count=count))
    return trend


def _working_dates(
    db: Session,
    year: int,
    month: int,
    today: date,
) -> list[date]:
    month_start = date(year, month, 1)
    month_end = date(year, month, monthrange(year, month)[1])
    report_end = min(month_end, today)
    if report_end < month_start:
        return []

    holidays = {
        row[0]
        for row in db.query(HolidayCalendar.date).filter(
            HolidayCalendar.date >= month_start,
            HolidayCalendar.date <= report_end,
        ).all()
    }
    return [
        month_start + timedelta(days=offset)
        for offset in range((report_end - month_start).days + 1)
        if (month_start + timedelta(days=offset)).weekday() < 5
        and (month_start + timedelta(days=offset)) not in holidays
    ]


def _is_employed_on(employee: Employee, work_date: date) -> bool:
    return (
        (employee.join_date is None or employee.join_date <= work_date)
        and (employee.end_date is None or employee.end_date >= work_date)
    )


def build_hris_dashboard_stats(
    db: Session,
    year: int,
    month: int,
    *,
    today: date | None = None,
) -> HrisDashboardStats:
    """Aggregate HRIS dashboard metrics for one reporting month."""
    today = today or date.today()
    employees = db.query(Employee).all()
    active = sum(
        1 for employee in employees if employee.status == EmployeeStatus.ACTIVE
    )
    probation = sum(
        1 for employee in employees if employee.status == EmployeeStatus.PROBATION
    )
    current_employees = [
        employee
        for employee in employees
        if employee.status in (EmployeeStatus.ACTIVE, EmployeeStatus.PROBATION)
    ]
    employment_type_counts = {
        employment_type.value: sum(
            1
            for employee in current_employees
            if employee.tipe == employment_type
        )
        for employment_type in EmploymentType
    }

    year_start = date(year, 1, 1)
    report_year_end = min(date(year, 12, 31), today)
    terminated_ytd = sum(
        1
        for employee in employees
        if employee.status == EmployeeStatus.TERMINATED
        and employee.end_date
        and year_start <= employee.end_date <= report_year_end
    )
    hired_ytd = sum(
        1
        for employee in employees
        if employee.join_date
        and year_start <= employee.join_date <= report_year_end
    )

    pkwt_employees = [
        employee
        for employee in employees
        if employee.tipe == EmploymentType.PKWT
        and employee.end_date
        and employee.status in (EmployeeStatus.ACTIVE, EmployeeStatus.PROBATION)
    ]

    def days_left(employee: Employee) -> int:
        return (employee.end_date - today).days if employee.end_date else 9999

    expiring_30 = [
        employee for employee in pkwt_employees if 0 <= days_left(employee) <= 30
    ]
    expiring_60 = [
        employee for employee in pkwt_employees if 0 <= days_left(employee) <= 60
    ]
    expiring_90 = [
        employee for employee in pkwt_employees if 0 <= days_left(employee) <= 90
    ]
    alert_items = [
        PkwtAlertItem(
            id=employee.id,
            employee_no=employee.employee_no,
            full_name=employee.full_name,
            dept=employee.department.name if employee.department else None,
            end_date=employee.end_date,
            days_left=days_left(employee),
        )
        for employee in sorted(
            expiring_90,
            key=lambda item: item.end_date,
        )[:10]
    ]

    balances = (
        db.query(LeaveBalance)
        .filter(LeaveBalance.year == year)
        .join(LeaveType)
        .filter(LeaveType.is_paid.is_(True))
        .all()
    )
    leave_liability_days = sum(
        max(0, balance.accrued - balance.used)
        for balance in balances
    )

    working_dates = _working_dates(db, year, month, today)
    attendance_rows = (
        db.query(AttendanceRecord.date, sql_func.count(AttendanceRecord.id))
        .join(Employee, Employee.id == AttendanceRecord.employee_id)
        .filter(
            AttendanceRecord.date.in_(working_dates),
            AttendanceRecord.clock_in.isnot(None),
            or_(
                Employee.join_date.is_(None),
                Employee.join_date <= AttendanceRecord.date,
            ),
            or_(
                Employee.end_date.is_(None),
                Employee.end_date >= AttendanceRecord.date,
            ),
        )
        .group_by(AttendanceRecord.date)
        .all()
    )
    expected_total = sum(
        1
        for work_date in working_dates
        for employee in employees
        if _is_employed_on(employee, work_date)
    )
    actual_present = sum(count for _, count in attendance_rows)
    attendance_rate = (
        round(actual_present / expected_total * 100, 1)
        if expected_total > 0
        else 0.0
    )

    department_rows = (
        db.query(Employee.dept_id, sql_func.count(AttendanceRecord.id))
        .join(AttendanceRecord, AttendanceRecord.employee_id == Employee.id)
        .filter(
            AttendanceRecord.date.in_(working_dates),
            AttendanceRecord.clock_in.isnot(None),
            or_(
                Employee.join_date.is_(None),
                Employee.join_date <= AttendanceRecord.date,
            ),
            or_(
                Employee.end_date.is_(None),
                Employee.end_date >= AttendanceRecord.date,
            ),
        )
        .group_by(Employee.dept_id)
        .all()
    )
    expected_by_department: dict[int, int] = {}
    for work_date in working_dates:
        for employee in employees:
            if (
                employee.dept_id is not None
                and _is_employed_on(employee, work_date)
            ):
                expected_by_department[employee.dept_id] = (
                    expected_by_department.get(employee.dept_id, 0) + 1
                )

    department_names = {
        department.id: department.name
        for department in db.query(Department).all()
    }
    present_by_department = {
        department_id: count
        for department_id, count in department_rows
        if department_id is not None
    }
    department_attendance = [
        DeptAttendanceItem(
            dept=department_names.get(department_id, "Unknown"),
            rate_pct=(
                round(
                    present_by_department.get(department_id, 0)
                    / expected
                    * 100,
                    1,
                )
                if expected > 0
                else 0.0
            ),
        )
        for department_id, expected in expected_by_department.items()
    ]
    department_attendance.sort(key=lambda item: item.rate_pct)

    return HrisDashboardStats(
        total_employees=len(employees),
        active=active,
        probation=probation,
        terminated_ytd=terminated_ytd,
        hired_ytd=hired_ytd,
        employment_type_counts=employment_type_counts,
        headcount_trend=_headcount_trend(
            employees,
            today,
            active + probation,
        ),
        pkwt_expiring_30d=len(expiring_30),
        pkwt_expiring_60d=len(expiring_60),
        pkwt_expiring_90d=len(expiring_90),
        pkwt_expiring_list=alert_items,
        leave_liability_days=leave_liability_days,
        attendance_rate_pct=attendance_rate,
        dept_attendance=department_attendance,
    )

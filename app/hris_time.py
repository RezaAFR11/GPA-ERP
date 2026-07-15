"""Time helpers shared by HRIS attendance endpoints."""
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


MIN_BROWSER_TIMEZONE_OFFSET = -14 * 60
MAX_BROWSER_TIMEZONE_OFFSET = 14 * 60


def local_date_from_browser_offset(
    timezone_offset_minutes: int,
    now: datetime | None = None,
) -> date:
    """Return the user's local date from JavaScript's getTimezoneOffset value."""
    if not MIN_BROWSER_TIMEZONE_OFFSET <= timezone_offset_minutes <= MAX_BROWSER_TIMEZONE_OFFSET:
        raise ValueError("Browser timezone offset must be between -840 and 840 minutes")

    utc_now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return (utc_now - timedelta(minutes=timezone_offset_minutes)).date()


def timezone_from_browser_offset(timezone_offset_minutes: int) -> timezone:
    """Convert JavaScript's UTC-minus-local offset into a Python timezone."""
    if not MIN_BROWSER_TIMEZONE_OFFSET <= timezone_offset_minutes <= MAX_BROWSER_TIMEZONE_OFFSET:
        raise ValueError("Browser timezone offset must be between -840 and 840 minutes")
    return timezone(timedelta(minutes=-timezone_offset_minutes))


def employee_timezone(employee, timezone_offset_minutes: int):
    """Use the configured work-location timezone as the authoritative zone."""
    timezone_name = getattr(getattr(employee, "work_location", None), "timezone_name", None)
    if timezone_name:
        try:
            return ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError:
            pass
    return timezone_from_browser_offset(timezone_offset_minutes)


def local_date_for_employee(
    employee,
    timezone_offset_minutes: int,
    now: datetime | None = None,
) -> date:
    utc_now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return utc_now.astimezone(employee_timezone(employee, timezone_offset_minutes)).date()

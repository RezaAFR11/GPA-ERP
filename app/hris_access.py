"""Shared HRIS self-service access checks."""
from fastapi import HTTPException, status

from app.models import Employee, EmployeeStatus


def ensure_employee_can_use_self_service(employee: Employee) -> Employee:
    """Reject new self-service activity for former employees."""
    if employee.status == EmployeeStatus.TERMINATED:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Employee access has ended. Contact HR if this is incorrect.",
        )
    return employee

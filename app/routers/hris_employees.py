"""
GPA-ERP HRIS — Phase H1: Data Karyawan & Organisasi
Endpoints for employees, departments, and job grades.
"""
import secrets
import string
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status
from pydantic import BaseModel
from sqlalchemy import func as sql_func, and_
from sqlalchemy.orm import Session

from app.audit import model_to_dict, write_audit
from app.database import get_db
from app.dependencies import CurrentUser, get_client_ip, hash_password, require_role
from app.menu_permissions import ROLE_PRESETS
from app.models import (
    AppMenu, Department, EmpDocType, Employee, EmployeeDocument, EmployeeStatus, EmploymentType,
    JobGrade, LeaveBalance, LeaveType, Role, RoleName, User, UserMenuPermission, WorkGroup,
    HolidayCalendar, EmployeeDataChangeRequest, DataChangeStatus,
    AttendanceRecord,
    JobPosting, PostingStatus,
)
from app.schemas import (
    DepartmentCreate, DepartmentResponse, DepartmentUpdate, DepartmentNode,
    EmployeeCreate, EmployeeDocumentResponse, EmployeeResponse, EmployeeUpdate,
    EmployeeSummary,
    JobGradeCreate, JobGradeResponse, JobGradeUpdate,
    MessageResponse, PaginatedResponse, UserSummary,
    WorkGroupCreate, WorkGroupResponse, WorkGroupUpdate,
    HolidayCalendarCreate, HolidayCalendarResponse,
    DataChangeRequestCreate, DataChangeRequestResponse, DataChangeActionRequest,
    HrisDashboardStats, HeadcountTrendItem, DeptAttendanceItem, PkwtAlertItem,
    CHANGEABLE_FIELDS,
)


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _generate_password(length: int = 12) -> str:
    """Generate a random password: at least one uppercase, one digit, one symbol."""
    alphabet = string.ascii_letters + string.digits + "!@#$"
    while True:
        pw = "".join(secrets.choice(alphabet) for _ in range(length))
        if (any(c.isupper() for c in pw)
                and any(c.isdigit() for c in pw)
                and any(c in "!@#$" for c in pw)):
            return pw


def _next_employee_no(db: Session) -> str:
    """Generate a unique auto employee number like EMP0007."""
    n = db.query(Employee).count() + 1
    while db.query(Employee).filter(Employee.employee_no == f"EMP{n:04d}").first():
        n += 1
    return f"EMP{n:04d}"


def _seed_user_menus(db: Session, user: User) -> None:
    """Seed menu permissions for a newly created user from their role preset."""
    menus = {m.key: m for m in db.query(AppMenu).filter(AppMenu.is_active == True).all()}
    preset_keys = ROLE_PRESETS.get(user.role.name.value, ROLE_PRESETS["STAFF"])
    for key in preset_keys:
        menu = menus.get(key)
        if menu:
            db.add(UserMenuPermission(user_id=user.id, menu_id=menu.id, can_access=True))

router = APIRouter(prefix="/hris", tags=["HRIS – Employees"])

# Roles allowed to manage HRIS data (GA = General Affairs / HR operator)
_hr_roles = (RoleName.SUPER_ADMIN, RoleName.MD, RoleName.GA, RoleName.HR)
# Roles allowed to tie employees <-> user accounts (GA/HR, PM, Project Control, MD).
_account_link_roles = (
    RoleName.SUPER_ADMIN, RoleName.MD, RoleName.GA, RoleName.HR,
    RoleName.PM, RoleName.PROJECT_CONTROL,
)
# Only these roles may assign any role when bulk-creating accounts; GA is limited
# to non-privileged roles (see _ga_assignable) to prevent privilege escalation.
_account_admin_roles = (RoleName.SUPER_ADMIN, RoleName.MD)
_ga_assignable       = (RoleName.WORKER, RoleName.STAFF)

_UPLOADS_DIR = Path("uploads") / "employee_docs"
_UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

_PHOTO_DIR = Path("uploads") / "employee_photos"
_PHOTO_DIR.mkdir(parents=True, exist_ok=True)

_ALLOWED_EXTS = {".pdf", ".jpg", ".jpeg", ".png"}
_MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB


# ─── Departments ─────────────────────────────────────────────────────────────

@router.get("/departments", response_model=list[DepartmentResponse], summary="List departments")
def list_departments(
    _:  CurrentUser,
    db: Annotated[Session, Depends(get_db)],
    active_only: bool = True,
):
    q = db.query(Department)
    if active_only:
        q = q.filter(Department.is_active == True)
    return q.order_by(Department.code).all()


@router.get("/departments/tree", response_model=list[DepartmentNode], summary="Department org tree")
def departments_tree(
    _:  CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    """Returns nested department tree with headcounts and open job posting counts."""
    all_depts = db.query(Department).filter(Department.is_active == True).all()

    # Count active employees per dept
    emp_counts: dict[int, int] = {}
    rows = (
        db.query(Employee.dept_id, sql_func.count(Employee.id))
        .filter(Employee.status != EmployeeStatus.TERMINATED, Employee.dept_id.isnot(None))
        .group_by(Employee.dept_id)
        .all()
    )
    for dept_id, cnt in rows:
        emp_counts[dept_id] = cnt

    # Count open job postings per dept
    posting_counts: dict[int, int] = {}
    p_rows = (
        db.query(JobPosting.department_id, sql_func.count(JobPosting.id))
        .filter(JobPosting.status == PostingStatus.OPEN, JobPosting.department_id.isnot(None))
        .group_by(JobPosting.department_id)
        .all()
    )
    for dept_id, cnt in p_rows:
        posting_counts[dept_id] = cnt

    # Build nodes dict
    nodes: dict[int, DepartmentNode] = {}
    for d in all_depts:
        nodes[d.id] = DepartmentNode(
            id=d.id, code=d.code, name=d.name, parent_id=d.parent_id,
            is_active=d.is_active,
            headcount=emp_counts.get(d.id, 0),
            open_positions=posting_counts.get(d.id, 0),
            children=[],
        )

    # Build tree (roots = no parent, or parent not in active set)
    roots: list[DepartmentNode] = []
    for node in nodes.values():
        if node.parent_id and node.parent_id in nodes:
            nodes[node.parent_id].children.append(node)
        else:
            roots.append(node)

    return sorted(roots, key=lambda n: n.code)


@router.post("/departments", response_model=DepartmentResponse, status_code=201,
             summary="Create department")
def create_department(
    request:      Request,
    payload:      DepartmentCreate,
    current_user: Annotated[CurrentUser, Depends(require_role(*_hr_roles))],
    db:           Annotated[Session, Depends(get_db)],
):
    if db.query(Department).filter(Department.code == payload.code).first():
        raise HTTPException(409, "Department code already exists")
    if payload.parent_id:
        parent = db.query(Department).filter(Department.id == payload.parent_id).first()
        if not parent:
            raise HTTPException(404, "Parent department not found")

    dept = Department(**payload.model_dump())
    db.add(dept)
    db.flush()
    write_audit(db, "Department", dept.id, "CREATE",
                changed_by=current_user.id, ip_address=get_client_ip(request),
                after=model_to_dict(dept))
    db.commit()
    db.refresh(dept)
    return dept


@router.patch("/departments/{dept_id}", response_model=DepartmentResponse,
              summary="Update department")
def update_department(
    dept_id:      int,
    request:      Request,
    payload:      DepartmentUpdate,
    current_user: Annotated[CurrentUser, Depends(require_role(*_hr_roles))],
    db:           Annotated[Session, Depends(get_db)],
):
    dept = db.query(Department).filter(Department.id == dept_id).first()
    if not dept:
        raise HTTPException(404, "Department not found")

    updates = payload.model_dump(exclude_unset=True)
    if "code" in updates:
        existing = db.query(Department).filter(
            Department.code == updates["code"], Department.id != dept_id
        ).first()
        if existing:
            raise HTTPException(409, "Department code already exists")

    before = model_to_dict(dept)
    for k, v in updates.items():
        setattr(dept, k, v)
    write_audit(db, "Department", dept.id, "UPDATE",
                changed_by=current_user.id, ip_address=get_client_ip(request),
                before=before, after=model_to_dict(dept))
    db.commit()
    db.refresh(dept)
    return dept


# ─── Job Grades ──────────────────────────────────────────────────────────────

@router.get("/job-grades", response_model=list[JobGradeResponse], summary="List job grades")
def list_job_grades(
    _:  CurrentUser,
    db: Annotated[Session, Depends(get_db)],
    active_only: bool = True,
):
    q = db.query(JobGrade)
    if active_only:
        q = q.filter(JobGrade.is_active == True)
    return q.order_by(JobGrade.level, JobGrade.code).all()


@router.post("/job-grades", response_model=JobGradeResponse, status_code=201,
             summary="Create job grade")
def create_job_grade(
    request:      Request,
    payload:      JobGradeCreate,
    current_user: Annotated[CurrentUser, Depends(require_role(*_hr_roles))],
    db:           Annotated[Session, Depends(get_db)],
):
    if db.query(JobGrade).filter(JobGrade.code == payload.code).first():
        raise HTTPException(409, "Job grade code already exists")

    grade = JobGrade(**payload.model_dump())
    db.add(grade)
    db.flush()
    write_audit(db, "JobGrade", grade.id, "CREATE",
                changed_by=current_user.id, ip_address=get_client_ip(request),
                after=model_to_dict(grade))
    db.commit()
    db.refresh(grade)
    return grade


@router.patch("/job-grades/{grade_id}", response_model=JobGradeResponse,
              summary="Update job grade")
def update_job_grade(
    grade_id:     int,
    request:      Request,
    payload:      JobGradeUpdate,
    current_user: Annotated[CurrentUser, Depends(require_role(*_hr_roles))],
    db:           Annotated[Session, Depends(get_db)],
):
    grade = db.query(JobGrade).filter(JobGrade.id == grade_id).first()
    if not grade:
        raise HTTPException(404, "Job grade not found")

    updates = payload.model_dump(exclude_unset=True)
    if "code" in updates:
        existing = db.query(JobGrade).filter(
            JobGrade.code == updates["code"], JobGrade.id != grade_id
        ).first()
        if existing:
            raise HTTPException(409, "Job grade code already exists")

    before = model_to_dict(grade)
    for k, v in updates.items():
        setattr(grade, k, v)
    write_audit(db, "JobGrade", grade.id, "UPDATE",
                changed_by=current_user.id, ip_address=get_client_ip(request),
                before=before, after=model_to_dict(grade))
    db.commit()
    db.refresh(grade)
    return grade


# ─── Employees ───────────────────────────────────────────────────────────────

@router.get("/employees", response_model=PaginatedResponse[EmployeeResponse],
            summary="List employees")
def list_employees(
    _:           CurrentUser,
    db:          Annotated[Session, Depends(get_db)],
    search:      str | None  = None,
    dept_id:     int | None  = None,
    tipe:        str | None  = None,
    status:      str | None  = "active",   # default: active employees only
    skip:        int         = 0,
    limit:       int         = 50,
):
    from app.models import EmploymentType, EmployeeStatus
    q = db.query(Employee)
    if search:
        like = f"%{search}%"
        from sqlalchemy import or_
        q = q.filter(or_(
            Employee.full_name.ilike(like),
            Employee.employee_no.ilike(like),
            Employee.nik.ilike(like),
            Employee.email.ilike(like),
        ))
    if dept_id:
        q = q.filter(Employee.dept_id == dept_id)
    if tipe:
        try:
            q = q.filter(Employee.tipe == EmploymentType(tipe))
        except ValueError:
            pass
    if status:
        try:
            q = q.filter(Employee.status == EmployeeStatus(status))
        except ValueError:
            pass

    total = q.count()
    items = q.order_by(Employee.full_name).offset(skip).limit(limit).all()
    return PaginatedResponse(items=items, total=total)


@router.post("/employees", response_model=EmployeeResponse, status_code=201,
             summary="Create employee")
def create_employee(
    request:      Request,
    payload:      EmployeeCreate,
    current_user: Annotated[CurrentUser, Depends(require_role(*_hr_roles))],
    db:           Annotated[Session, Depends(get_db)],
):
    if db.query(Employee).filter(Employee.employee_no == payload.employee_no).first():
        raise HTTPException(409, "Employee number already exists")
    if payload.nik and db.query(Employee).filter(Employee.nik == payload.nik).first():
        raise HTTPException(409, "NIK already registered")
    if payload.user_id:
        if db.query(Employee).filter(
            Employee.user_id == payload.user_id, Employee.id != -1
        ).first():
            raise HTTPException(409, "User already linked to another employee")

    emp = Employee(**payload.model_dump())
    db.add(emp)
    db.flush()
    write_audit(db, "Employee", emp.id, "CREATE",
                changed_by=current_user.id, ip_address=get_client_ip(request),
                after=model_to_dict(emp))
    db.commit()
    db.refresh(emp)
    return emp


@router.get("/employees/linkable-users", response_model=list[UserSummary],
            summary="Active user accounts not yet linked to any employee")
def linkable_users(
    current_user: Annotated[CurrentUser, Depends(require_role(*_account_link_roles))],
    db:           Annotated[Session, Depends(get_db)],
):
    linked = {
        uid for (uid,) in
        db.query(Employee.user_id).filter(Employee.user_id.isnot(None)).all()
    }
    q = db.query(User).filter(User.is_active == True)
    if linked:
        q = q.filter(~User.id.in_(linked))
    return q.order_by(User.full_name).all()


@router.post("/employees/{emp_id}/link-user/{user_id}", response_model=EmployeeResponse,
             summary="Link an existing employee to an existing user account")
def link_employee_user(
    emp_id:       int,
    user_id:      int,
    request:      Request,
    current_user: Annotated[CurrentUser, Depends(require_role(*_account_link_roles))],
    db:           Annotated[Session, Depends(get_db)],
):
    emp = db.query(Employee).filter(Employee.id == emp_id).first()
    if not emp:
        raise HTTPException(404, "Employee not found")
    if emp.user_id:
        raise HTTPException(409, "Employee is already linked to a user account")
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(404, "User not found")
    if db.query(Employee).filter(Employee.user_id == user_id).first():
        raise HTTPException(409, "User is already linked to another employee")

    before = model_to_dict(emp)
    emp.user_id = user.id
    write_audit(db, "Employee", emp.id, "LINK_USER",
                changed_by=current_user.id, ip_address=get_client_ip(request),
                before=before, after=model_to_dict(emp))
    db.commit()
    db.refresh(emp)
    return emp


@router.post("/employees/from-user/{user_id}", response_model=EmployeeResponse,
             summary="Create or link an employee (pegawai) record for an existing user account")
def employee_from_user(
    user_id:      int,
    request:      Request,
    current_user: Annotated[CurrentUser, Depends(require_role(*_account_link_roles))],
    db:           Annotated[Session, Depends(get_db)],
):
    """Idempotent: returns the existing linked employee, links an unlinked
    employee that shares the user's email, or creates a minimal new one. The
    admin can then complete the details in Data Karyawan."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(404, "User not found")

    existing = db.query(Employee).filter(Employee.user_id == user_id).first()
    if existing:
        return existing

    # Link an unlinked employee with the same email, if one exists.
    if user.email:
        match = db.query(Employee).filter(
            Employee.email == user.email, Employee.user_id.is_(None)
        ).first()
        if match:
            before = model_to_dict(match)
            match.user_id = user.id
            write_audit(db, "Employee", match.id, "LINK_USER",
                        changed_by=current_user.id, ip_address=get_client_ip(request),
                        before=before, after=model_to_dict(match))
            db.commit()
            db.refresh(match)
            return match

    emp = Employee(
        employee_no=_next_employee_no(db),
        full_name=user.full_name,
        email=user.email,
        tipe=EmploymentType.TETAP,
        status=EmployeeStatus.ACTIVE,
        join_date=date.today(),
        user_id=user.id,
    )
    db.add(emp)
    db.flush()
    write_audit(db, "Employee", emp.id, "CREATE",
                changed_by=current_user.id, ip_address=get_client_ip(request),
                after=model_to_dict(emp))
    db.commit()
    db.refresh(emp)
    return emp


@router.get("/employees/{emp_id}", response_model=EmployeeResponse,
            summary="Get employee detail")
def get_employee(
    emp_id: int,
    _:      CurrentUser,
    db:     Annotated[Session, Depends(get_db)],
):
    emp = db.query(Employee).filter(Employee.id == emp_id).first()
    if not emp:
        raise HTTPException(404, "Employee not found")
    return emp


@router.patch("/employees/{emp_id}", response_model=EmployeeResponse,
              summary="Update employee")
def update_employee(
    emp_id:       int,
    request:      Request,
    payload:      EmployeeUpdate,
    current_user: Annotated[CurrentUser, Depends(require_role(*_hr_roles))],
    db:           Annotated[Session, Depends(get_db)],
):
    emp = db.query(Employee).filter(Employee.id == emp_id).first()
    if not emp:
        raise HTTPException(404, "Employee not found")

    updates = payload.model_dump(exclude_unset=True)
    if "nik" in updates and updates["nik"]:
        existing = db.query(Employee).filter(
            Employee.nik == updates["nik"], Employee.id != emp_id
        ).first()
        if existing:
            raise HTTPException(409, "NIK already registered to another employee")

    before = model_to_dict(emp)
    for k, v in updates.items():
        setattr(emp, k, v)
    write_audit(db, "Employee", emp.id, "UPDATE",
                changed_by=current_user.id, ip_address=get_client_ip(request),
                before=before, after=model_to_dict(emp))
    db.commit()
    db.refresh(emp)
    return emp


@router.post("/employees/{emp_id}/photo", summary="Upload employee photo")
async def upload_employee_photo(
    emp_id:       int,
    file:         UploadFile,
    current_user: Annotated[CurrentUser, Depends(require_role(*_hr_roles))],
    db:           Annotated[Session, Depends(get_db)],
):
    emp = db.query(Employee).filter(Employee.id == emp_id).first()
    if not emp:
        raise HTTPException(404, "Employee not found")

    ext = Path(file.filename or "").suffix.lower()
    if ext not in {".jpg", ".jpeg", ".png"}:
        raise HTTPException(400, "Photo must be JPEG or PNG")

    data = await file.read()
    if len(data) > _MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"File too large. Maximum size is {_MAX_UPLOAD_BYTES // 1024 // 1024} MB")
    dest = _PHOTO_DIR / f"{emp_id}{ext}"
    dest.write_bytes(data)

    emp.photo_url = f"/uploads/employee_photos/{emp_id}{ext}"
    db.commit()
    return {"url": emp.photo_url}


@router.post("/employees/{emp_id}/documents",
             response_model=EmployeeDocumentResponse, status_code=201,
             summary="Upload employee document")
async def upload_employee_document(
    emp_id:       int,
    doc_type:     EmpDocType,
    file:         UploadFile,
    current_user: Annotated[CurrentUser, Depends(require_role(*_hr_roles))],
    db:           Annotated[Session, Depends(get_db)],
):
    emp = db.query(Employee).filter(Employee.id == emp_id).first()
    if not emp:
        raise HTTPException(404, "Employee not found")

    ext = Path(file.filename or "").suffix.lower()
    if ext not in _ALLOWED_EXTS:
        raise HTTPException(400, f"Allowed file types: {', '.join(_ALLOWED_EXTS)}")

    import uuid
    data = await file.read()
    if len(data) > _MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"File too large. Maximum size is {_MAX_UPLOAD_BYTES // 1024 // 1024} MB")
    filename = f"{emp_id}_{doc_type.value}_{uuid.uuid4().hex[:8]}{ext}"
    dest = _UPLOADS_DIR / filename
    dest.write_bytes(data)

    doc = EmployeeDocument(
        employee_id=emp_id,
        doc_type=doc_type,
        file_url=f"/uploads/employee_docs/{filename}",
    )
    db.add(doc)
    db.flush()
    write_audit(db, "EmployeeDocument", doc.id, "CREATE",
                changed_by=current_user.id, ip_address=None,
                after={"employee_id": emp_id, "doc_type": doc_type.value})
    db.commit()
    db.refresh(doc)
    return doc


# ─── Bulk: create user accounts for employees ────────────────────────────────

class BulkAccountItem(BaseModel):
    employee_id: int
    role_name:   RoleName  # e.g. "WORKER", "STAFF"


class BulkAccountResult(BaseModel):
    employee_id:  int
    employee_no:  str
    full_name:    str
    status:       str   # "created" | "skipped" | "error"
    detail:       str   # username / skip reason / error message
    temp_password: str | None = None


class BulkAccountResponse(BaseModel):
    created: int
    skipped: int
    errors:  int
    results: list[BulkAccountResult]


@router.post("/employees/bulk-create-accounts",
             response_model=BulkAccountResponse,
             summary="Bulk-create user accounts for employees",
             status_code=200)
def bulk_create_accounts(
    request:      Request,
    payload:      list[BulkAccountItem],
    current_user: Annotated[CurrentUser, Depends(require_role(*_hr_roles))],
    db:           Annotated[Session, Depends(get_db)],
):
    """
    For each (employee_id, role_name) pair:
    - Skip if employee already has a linked user account.
    - Skip if employee has no email address.
    - Create a new User with a random temporary password.
    - Link Employee.user_id → new user.
    - Seed menu permissions from the role preset.
    Returns a per-item result list plus aggregate counts.
    """
    if not payload:
        raise HTTPException(400, "Empty list")

    results: list[BulkAccountResult] = []

    for item in payload:
        emp = db.query(Employee).filter(Employee.id == item.employee_id).first()
        if not emp:
            results.append(BulkAccountResult(
                employee_id=item.employee_id, employee_no="?", full_name="?",
                status="error", detail="Employee not found",
            ))
            continue

        # Already linked
        if emp.user_id:
            results.append(BulkAccountResult(
                employee_id=emp.id, employee_no=emp.employee_no, full_name=emp.full_name,
                status="skipped", detail="Already has an account",
            ))
            continue

        # Need an email to create a login
        if not emp.email:
            results.append(BulkAccountResult(
                employee_id=emp.id, employee_no=emp.employee_no, full_name=emp.full_name,
                status="skipped", detail="No email address on record",
            ))
            continue

        email = emp.email.lower()

        # Email already taken by another user
        if db.query(User).filter(User.email == email).first():
            results.append(BulkAccountResult(
                employee_id=emp.id, employee_no=emp.employee_no, full_name=emp.full_name,
                status="skipped", detail=f"Email {email} already registered",
            ))
            continue

        # Resolve role
        role = db.query(Role).filter(Role.name == item.role_name).first()
        if not role:
            results.append(BulkAccountResult(
                employee_id=emp.id, employee_no=emp.employee_no, full_name=emp.full_name,
                status="error", detail=f"Role '{item.role_name}' not found in DB",
            ))
            continue

        # Privilege guard: only SUPER_ADMIN / MD may grant elevated roles.
        # GA (and any other HR operator) may only create WORKER / STAFF accounts.
        if (current_user.role.name not in _account_admin_roles
                and role.name not in _ga_assignable):
            results.append(BulkAccountResult(
                employee_id=emp.id, employee_no=emp.employee_no, full_name=emp.full_name,
                status="error",
                detail=f"Not permitted to assign role {role.name.value}",
            ))
            continue

        temp_password = _generate_password()
        user = User(
            email=email,
            hashed_password=hash_password(temp_password),
            full_name=emp.full_name,
            role_id=role.id,
        )
        db.add(user)
        db.flush()  # get user.id

        # Link employee → user
        emp.user_id = user.id
        db.flush()

        # Seed menu permissions
        _seed_user_menus(db, user)

        write_audit(db, "User", user.id, "CREATE",
                    changed_by=current_user.id, ip_address=get_client_ip(request),
                    after={"email": email, "role": item.role_name.value,
                           "employee_id": emp.id, "bulk": True})

        results.append(BulkAccountResult(
            employee_id=emp.id, employee_no=emp.employee_no, full_name=emp.full_name,
            status="created", detail=email, temp_password=temp_password,
        ))

    db.commit()

    return BulkAccountResponse(
        created=sum(1 for r in results if r.status == "created"),
        skipped=sum(1 for r in results if r.status == "skipped"),
        errors=sum(1 for r in results if r.status == "error"),
        results=results,
    )


# ─── WorkGroup CRUD ──────────────────────────────────────────────────────────

@router.get("/work-groups", response_model=list[WorkGroupResponse], summary="List work groups")
def list_work_groups(
    role: str | None = Query(None),
    is_active: bool | None = Query(None),
    db: Session = Depends(get_db),
    _: User = Depends(require_role(RoleName.SUPER_ADMIN, RoleName.MD, RoleName.GA)),
):
    q = db.query(WorkGroup)
    if role:
        q = q.filter(WorkGroup.role == role)
    if is_active is not None:
        q = q.filter(WorkGroup.is_active == is_active)
    return q.order_by(WorkGroup.name).all()


@router.post("/work-groups", response_model=WorkGroupResponse, status_code=201, summary="Create work group")
def create_work_group(
    body: WorkGroupCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(RoleName.SUPER_ADMIN, RoleName.MD, RoleName.GA)),
):
    wg = WorkGroup(**body.model_dump())
    db.add(wg)
    db.commit()
    db.refresh(wg)
    return wg


@router.patch("/work-groups/{wg_id}", response_model=WorkGroupResponse, summary="Update work group")
def update_work_group(
    wg_id: int,
    body: WorkGroupUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(require_role(RoleName.SUPER_ADMIN, RoleName.MD, RoleName.GA)),
):
    wg = db.query(WorkGroup).filter(WorkGroup.id == wg_id).first()
    if not wg:
        raise HTTPException(status_code=404, detail="Work group not found")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(wg, field, value)
    db.commit()
    db.refresh(wg)
    return wg


@router.patch("/employees/{emp_id}/work-group", response_model=EmployeeResponse, summary="Assign/unassign employee work group")
def assign_work_group(
    emp_id: int,
    work_group_id: int | None = Query(None, description="Pass null/omit to unassign"),
    db: Session = Depends(get_db),
    _: User = Depends(require_role(RoleName.SUPER_ADMIN, RoleName.MD, RoleName.GA)),
):
    emp = db.query(Employee).filter(Employee.id == emp_id).first()
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")
    if work_group_id is not None:
        wg = db.query(WorkGroup).filter(WorkGroup.id == work_group_id).first()
        if not wg:
            raise HTTPException(status_code=404, detail="Work group not found")
    emp.work_group_id = work_group_id
    db.commit()
    db.refresh(emp)
    return emp


# ═══════════════════════════════════════════════════════════════════════════════
# HRIS Dashboard Stats
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/dashboard/stats", response_model=HrisDashboardStats, summary="HRIS dashboard statistics")
def hris_dashboard_stats(
    cu: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
    year:  int = Query(default=None),
    month: int = Query(default=None),
):
    today = date.today()
    year  = year  or today.year
    month = month or today.month

    # ── Headcount KPIs ───────────────────────────────────────────────────────
    all_emps = db.query(Employee).all()
    total_employees = len(all_emps)
    active    = sum(1 for e in all_emps if e.status == EmployeeStatus.ACTIVE)
    probation = sum(1 for e in all_emps if e.status == EmployeeStatus.PROBATION)

    year_start = date(year, 1, 1)
    terminated_ytd = sum(
        1 for e in all_emps
        if e.status == EmployeeStatus.TERMINATED and e.end_date and e.end_date >= year_start
    )
    hired_ytd = sum(
        1 for e in all_emps
        if e.join_date and e.join_date >= year_start
    )

    # ── Headcount trend (last 6 months) ──────────────────────────────────────
    trend: list[HeadcountTrendItem] = []
    for i in range(5, -1, -1):
        # Last day of each of the past 6 months
        mn = (today.month - i - 1) % 12 + 1
        yr = today.year - ((today.month - i - 1) // 12 + 1 if today.month - i <= 0 else 0)
        if today.month - i <= 0:
            yr = today.year - 1
            mn = today.month - i + 12
        else:
            yr = today.year
            mn = today.month - i
        snap_date = date(yr, mn, 1)
        # employees joined before snap_date and not terminated before snap_date
        count = sum(
            1 for e in all_emps
            if (e.join_date is None or e.join_date <= snap_date)
            and (e.end_date is None or e.end_date >= snap_date)
            and e.status != EmployeeStatus.TERMINATED
        )
        # Simpler: just count current active + probation for current month
        if mn == today.month and yr == today.year:
            count = active + probation
        trend.append(HeadcountTrendItem(month=f"{yr}-{mn:02d}", count=count))

    # ── PKWT expiry alerts ────────────────────────────────────────────────────
    pkwt_emps = [e for e in all_emps if e.tipe == EmploymentType.PKWT and e.end_date]
    def days_left(e: Employee) -> int:
        return (e.end_date - today).days if e.end_date else 9999

    expiring_30  = [e for e in pkwt_emps if 0 <= days_left(e) <= 30]
    expiring_60  = [e for e in pkwt_emps if 0 <= days_left(e) <= 60]
    expiring_90  = [e for e in pkwt_emps if 0 <= days_left(e) <= 90]

    pkwt_list = sorted(
        [e for e in pkwt_emps if 0 <= days_left(e) <= 90],
        key=lambda e: e.end_date,
    )[:10]

    alert_items = [
        PkwtAlertItem(
            id=e.id,
            employee_no=e.employee_no,
            full_name=e.full_name,
            dept=e.department.name if e.department else None,
            end_date=e.end_date,
            days_left=days_left(e),
        )
        for e in pkwt_list
    ]

    # ── Leave liability (accrued - used, current year) ───────────────────────
    balances = db.query(LeaveBalance).filter(
        LeaveBalance.year == year,
    ).join(LeaveType).filter(LeaveType.is_paid == True).all()
    leave_liability_days = sum(max(0, b.accrued - b.used) for b in balances)

    # ── Attendance rate current month ─────────────────────────────────────────
    month_start = date(year, month, 1)
    # Count distinct attendance dates in this month with at least one employee present
    att_rows = (
        db.query(AttendanceRecord.date, sql_func.count(AttendanceRecord.id))
        .filter(
            AttendanceRecord.date >= month_start,
            AttendanceRecord.date <= today,
            AttendanceRecord.clock_in.isnot(None),
        )
        .group_by(AttendanceRecord.date)
        .all()
    )

    # Working days so far this month (Mon–Fri, before today inclusive)
    working_days = sum(
        1 for d in range((today - month_start).days + 1)
        if (month_start + timedelta(days=d)).weekday() < 5
    )
    expected_total = working_days * max(active + probation, 1)
    actual_present = sum(cnt for _, cnt in att_rows)
    attendance_rate_pct = round(actual_present / expected_total * 100, 1) if expected_total > 0 else 0.0

    # ── Attendance rate by dept ───────────────────────────────────────────────
    dept_att_rows = (
        db.query(Employee.dept_id, sql_func.count(AttendanceRecord.id))
        .join(AttendanceRecord, AttendanceRecord.employee_id == Employee.id)
        .filter(
            AttendanceRecord.date >= month_start,
            AttendanceRecord.date <= today,
            AttendanceRecord.clock_in.isnot(None),
        )
        .group_by(Employee.dept_id)
        .all()
    )
    dept_emp_counts: dict[int, int] = {}
    for dept_id, cnt in (
        db.query(Employee.dept_id, sql_func.count(Employee.id))
        .filter(Employee.status.in_([EmployeeStatus.ACTIVE, EmployeeStatus.PROBATION]))
        .filter(Employee.dept_id.isnot(None))
        .group_by(Employee.dept_id).all()
    ):
        dept_emp_counts[dept_id] = cnt

    dept_map = {d.id: d.name for d in db.query(Department).all()}
    dept_attendance: list[DeptAttendanceItem] = []
    for dept_id, present_count in dept_att_rows:
        if dept_id is None:
            continue
        expected = working_days * dept_emp_counts.get(dept_id, 1)
        rate = round(present_count / expected * 100, 1) if expected > 0 else 0.0
        dept_attendance.append(DeptAttendanceItem(
            dept=dept_map.get(dept_id, "Unknown"),
            rate_pct=rate,
        ))
    dept_attendance.sort(key=lambda x: x.rate_pct)

    return HrisDashboardStats(
        total_employees=total_employees,
        active=active,
        probation=probation,
        terminated_ytd=terminated_ytd,
        hired_ytd=hired_ytd,
        headcount_trend=trend,
        pkwt_expiring_30d=len(expiring_30),
        pkwt_expiring_60d=len(expiring_60),
        pkwt_expiring_90d=len(expiring_90),
        pkwt_expiring_list=alert_items,
        leave_liability_days=leave_liability_days,
        attendance_rate_pct=attendance_rate_pct,
        dept_attendance=dept_attendance,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Holiday Calendar
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/holiday-calendar", response_model=list[HolidayCalendarResponse], summary="List holidays by year")
def list_holidays(
    cu: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
    year: int = Query(default=None),
):
    year = year or date.today().year
    return (
        db.query(HolidayCalendar)
        .filter(HolidayCalendar.year == year)
        .order_by(HolidayCalendar.date)
        .all()
    )


@router.post("/holiday-calendar", response_model=HolidayCalendarResponse, status_code=201, summary="Add holiday")
def create_holiday(
    payload: HolidayCalendarCreate,
    cu: Annotated[CurrentUser, Depends(require_role(RoleName.SUPER_ADMIN, RoleName.GA))],
    db: Annotated[Session, Depends(get_db)],
):
    existing = db.query(HolidayCalendar).filter(HolidayCalendar.date == payload.date).first()
    if existing:
        raise HTTPException(409, f"Holiday on {payload.date} already exists")
    h = HolidayCalendar(
        date=payload.date,
        name=payload.name,
        is_national=payload.is_national,
        year=payload.date.year,
    )
    db.add(h)
    db.commit()
    db.refresh(h)
    return h


@router.delete("/holiday-calendar/{holiday_id}", status_code=204, summary="Delete holiday")
def delete_holiday(
    holiday_id: int,
    cu: Annotated[CurrentUser, Depends(require_role(RoleName.SUPER_ADMIN, RoleName.GA))],
    db: Annotated[Session, Depends(get_db)],
):
    h = db.query(HolidayCalendar).filter(HolidayCalendar.id == holiday_id).first()
    if not h:
        raise HTTPException(404, "Holiday not found")
    db.delete(h)
    db.commit()


# ═══════════════════════════════════════════════════════════════════════════════
# Employee Data Change Requests
# ═══════════════════════════════════════════════════════════════════════════════

def _get_my_employee(db: Session, cu: CurrentUser) -> Employee:
    emp = db.query(Employee).filter(Employee.user_id == cu.id).first()
    if not emp:
        raise HTTPException(404, "No employee profile linked to your account")
    return emp


@router.post("/me/data-change-requests", response_model=DataChangeRequestResponse, status_code=201)
def submit_data_change_request(
    payload: DataChangeRequestCreate,
    cu: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    emp = _get_my_employee(db, cu)
    if payload.field_name not in CHANGEABLE_FIELDS:
        raise HTTPException(400, f"Field '{payload.field_name}' is not changeable. Allowed: {sorted(CHANGEABLE_FIELDS)}")
    old_val = str(getattr(emp, payload.field_name, "") or "")
    req = EmployeeDataChangeRequest(
        employee_id=emp.id,
        field_name=payload.field_name,
        old_value=old_val,
        new_value=payload.new_value,
        reason=payload.reason,
        status=DataChangeStatus.PENDING,
    )
    db.add(req)
    db.commit()
    db.refresh(req)
    return req


@router.get("/me/data-change-requests", response_model=list[DataChangeRequestResponse])
def my_data_change_requests(
    cu: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    emp = _get_my_employee(db, cu)
    return (
        db.query(EmployeeDataChangeRequest)
        .filter(EmployeeDataChangeRequest.employee_id == emp.id)
        .order_by(EmployeeDataChangeRequest.created_at.desc())
        .all()
    )


@router.get("/data-change-requests", response_model=list[DataChangeRequestResponse])
def list_data_change_requests(
    cu: Annotated[CurrentUser, Depends(require_role(RoleName.SUPER_ADMIN, RoleName.GA, RoleName.MD))],
    db: Annotated[Session, Depends(get_db)],
    status_filter: str | None = Query(None, alias="status"),
):
    q = db.query(EmployeeDataChangeRequest)
    if status_filter:
        q = q.filter(EmployeeDataChangeRequest.status == status_filter)
    return q.order_by(EmployeeDataChangeRequest.created_at.desc()).all()


@router.post("/data-change-requests/{req_id}/approve", response_model=DataChangeRequestResponse)
def approve_data_change(
    req_id: int,
    payload: DataChangeActionRequest,
    cu: Annotated[CurrentUser, Depends(require_role(RoleName.SUPER_ADMIN, RoleName.GA, RoleName.MD))],
    db: Annotated[Session, Depends(get_db)],
):
    req = db.query(EmployeeDataChangeRequest).filter(EmployeeDataChangeRequest.id == req_id).first()
    if not req:
        raise HTTPException(404, "Request not found")
    if req.status != DataChangeStatus.PENDING:
        raise HTTPException(400, f"Request is already {req.status.value}")
    emp = db.query(Employee).filter(Employee.id == req.employee_id).first()
    if emp and hasattr(emp, req.field_name):
        setattr(emp, req.field_name, req.new_value)
        write_audit(db, "Employee", emp.id, "DATA_CHANGE_APPROVED",
                    changed_by=cu.id, after={req.field_name: req.new_value})
    req.status = DataChangeStatus.APPROVED
    req.reviewed_by = cu.id
    req.reviewed_at = datetime.now(timezone.utc)
    req.review_note = payload.note
    db.commit()
    db.refresh(req)
    return req


@router.post("/data-change-requests/{req_id}/reject", response_model=DataChangeRequestResponse)
def reject_data_change(
    req_id: int,
    payload: DataChangeActionRequest,
    cu: Annotated[CurrentUser, Depends(require_role(RoleName.SUPER_ADMIN, RoleName.GA, RoleName.MD))],
    db: Annotated[Session, Depends(get_db)],
):
    req = db.query(EmployeeDataChangeRequest).filter(EmployeeDataChangeRequest.id == req_id).first()
    if not req:
        raise HTTPException(404, "Request not found")
    if req.status != DataChangeStatus.PENDING:
        raise HTTPException(400, f"Request is already {req.status.value}")
    req.status = DataChangeStatus.REJECTED
    req.reviewed_by = cu.id
    req.reviewed_at = datetime.now(timezone.utc)
    req.review_note = payload.note
    db.commit()
    db.refresh(req)
    return req


# ═══════════════════════════════════════════════════════════════════════════════
# Employee Documents Hub (self-service)
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/me/documents", response_model=list[dict], summary="My downloadable documents")
def my_documents(
    cu: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    """Returns employee's own uploaded documents + payslip list combined."""
    emp = _get_my_employee(db, cu)

    from app.models import PayrollRun, PaySlip, PayrollPeriod
    items = []

    # Employee documents
    for doc in emp.documents:
        items.append({
            "doc_type":     doc.doc_type.value,
            "name":         f"Dokumen {doc.doc_type.value}",
            "date":         doc.uploaded_at.date().isoformat() if doc.uploaded_at else None,
            "file_url":     doc.file_url,
            "period_label": None,
        })

    # Payslips
    from sqlalchemy.orm import joinedload
    runs = (
        db.query(PayrollRun)
        .join(PaySlip, PayrollRun.id == PaySlip.run_id)
        .join(PayrollPeriod, PayrollRun.period_id == PayrollPeriod.id)
        .filter(PayrollRun.employee_id == emp.id)
        .options(joinedload(PayrollRun.payslip), joinedload(PayrollRun.period))
        .order_by(PayrollPeriod.year.desc(), PayrollPeriod.month.desc())
        .all()
    )
    MONTH_ID = ["","Januari","Februari","Maret","April","Mei","Juni",
                "Juli","Agustus","September","Oktober","November","Desember"]
    for run in runs:
        if run.payslip:
            items.append({
                "doc_type":     "payslip",
                "name":         f"Slip Gaji {MONTH_ID[run.period.month]} {run.period.year}",
                "date":         f"{run.period.year}-{run.period.month:02d}-01",
                "file_url":     f"/api/hris/payroll/runs/{run.id}/slip.pdf",
                "period_label": f"{MONTH_ID[run.period.month]} {run.period.year}",
            })

    return items

"""
GPA-ERP HRIS — Recruitment & Onboarding router (H4)

Endpoints:
    GET/POST  /hris/job-postings
    PATCH     /hris/job-postings/{id}
    GET/POST  /hris/applicants
    PATCH     /hris/applicants/{id}/stage
    POST      /hris/applicants/{id}/hire
    POST      /hris/interviews
    PATCH     /hris/interviews/{id}
    GET       /hris/onboarding/{applicant_id}
    PATCH     /hris/onboarding/tasks/{id}
"""
from __future__ import annotations

import logging
import secrets
import string
from datetime import date, datetime, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.audit import write_audit
from app.database import get_db
from app.dependencies import CurrentUser, hash_password
from app.menu_permissions import ROLE_PRESETS
from app.models import (
    AppMenu,
    Applicant, ApplicantSource, ApplicantStage,
    Employee, EmployeeStatus, EmploymentType,
    Interview, InterviewResult,
    JobPosting, OnboardingTask,
    LeaveBalance, LeaveType,
    PostingStatus, Role, RoleName, User, UserMenuPermission, effective_roles,
)
from app.schemas import (
    ApplicantCreate, ApplicantResponse,
    HireRequest, HireResponse,
    InterviewCreate, InterviewResponse,
    JobPostingCreate, JobPostingResponse,
    OnboardingTaskResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["HRIS Recruitment"])

_HR_ROLES  = (RoleName.SUPER_ADMIN, RoleName.MD, RoleName.GA, RoleName.HR)
_MGR_ROLES = (RoleName.SUPER_ADMIN, RoleName.MD, RoleName.PM, RoleName.PROJECT_CONTROL, RoleName.GA, RoleName.HR)


def _require(cu: Any, roles: tuple) -> None:
    if not any(r in roles for r in effective_roles(cu.role.name)):
        raise HTTPException(403, f"Requires one of: {[r.value for r in roles]}")


def _temporary_password(length: int = 14) -> str:
    alphabet = string.ascii_letters + string.digits + "!@#$"
    while True:
        password = "".join(secrets.choice(alphabet) for _ in range(length))
        if (any(char.isupper() for char in password)
                and any(char.islower() for char in password)
                and any(char.isdigit() for char in password)
                and any(char in "!@#$" for char in password)):
            return password


def _seed_staff_menus(db: Session, user_id: int) -> int:
    menu_by_key = {
        menu.key: menu
        for menu in db.query(AppMenu).filter(AppMenu.is_active == True).all()
    }
    created = 0
    for key in ROLE_PRESETS[RoleName.STAFF.value]:
        menu = menu_by_key.get(key)
        if menu:
            db.add(UserMenuPermission(user_id=user_id, menu_id=menu.id, can_access=True))
            created += 1
    return created


# ─── Default onboarding checklist ────────────────────────────────────────────

DEFAULT_TASKS = [
    "Persiapkan surat kontrak kerja",
    "Input data karyawan ke sistem HRIS",
    "Daftarkan ke BPJS Ketenagakerjaan",
    "Daftarkan ke BPJS Kesehatan",
    "Setup akun email & sistem",
    "Orientasi & pengenalan rekan kerja",
    "Serahkan seragam / perlengkapan kerja",
    "Tanda tangan kontrak & NDA",
    "Pelatihan K3 / HSE (jika diperlukan)",
    "Verifikasi dokumen pribadi (KTP, NPWP, dll.)",
]

# ─── Job Postings ─────────────────────────────────────────────────────────────

@router.get("/hris/job-postings", response_model=list[JobPostingResponse])
def list_postings(
    cu: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
    status: str | None = None,
    dept_id: int | None = None,
):
    q = db.query(JobPosting)
    if status:
        try:
            posting_status = PostingStatus(status.upper())
        except ValueError:
            raise HTTPException(400, f"Invalid posting status '{status}'")
        q = q.filter(JobPosting.status == posting_status)
    if dept_id: q = q.filter_by(department_id=dept_id)
    return q.order_by(JobPosting.created_at.desc()).all()


@router.post("/hris/job-postings", response_model=JobPostingResponse, status_code=201)
def create_posting(
    body: JobPostingCreate,
    cu: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    _require(cu, _MGR_ROLES)
    posting = JobPosting(
        **body.model_dump(),
        status=PostingStatus.OPEN,
        opened_at=datetime.now(timezone.utc),
        created_by=cu.id,
    )
    db.add(posting)
    db.flush()
    write_audit(
        db, "hris_job_postings", posting.id, "CREATE",
        changed_by=cu.id, after=body.model_dump(),
    )
    db.commit()
    db.refresh(posting)
    return posting


@router.patch("/hris/job-postings/{posting_id}", response_model=JobPostingResponse)
def update_posting(
    posting_id: int,
    body: dict[str, Any],
    cu: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    _require(cu, _MGR_ROLES)
    posting = db.get(JobPosting, posting_id)
    if not posting:
        raise HTTPException(404, "Job posting not found")
    before  = {"status": posting.status.value, "title": posting.title}
    allowed = {"title", "description", "requirements", "status", "grade_id", "department_id"}
    for k, v in body.items():
        if k in allowed:
            if k == "status":
                try:
                    v = PostingStatus(str(v).upper())
                except ValueError:
                    raise HTTPException(400, f"Invalid posting status '{v}'")
                if v == PostingStatus.CLOSED:
                    posting.closed_at = datetime.now(timezone.utc)
                elif posting.status == PostingStatus.CLOSED:
                    posting.closed_at = None
            setattr(posting, k, v)
    write_audit(
        db, "hris_job_postings", posting.id, "UPDATE",
        changed_by=cu.id, before=before, after=body,
    )
    db.commit()
    db.refresh(posting)
    return posting


# ─── Applicants ───────────────────────────────────────────────────────────────

@router.get("/hris/applicants", response_model=list[ApplicantResponse])
def list_applicants(
    cu: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
    posting_id: int | None = None,
    stage:      str | None = None,
    search:     str | None = None,
):
    q = db.query(Applicant)
    if posting_id:
        q = q.filter_by(posting_id=posting_id)
    if stage:
        try:
            applicant_stage = ApplicantStage(stage.upper())
        except ValueError:
            raise HTTPException(400, f"Invalid applicant stage '{stage}'")
        q = q.filter(Applicant.stage == applicant_stage)
    if search:
        s = f"%{search}%"
        q = q.filter(Applicant.full_name.ilike(s) | Applicant.email.ilike(s))
    return q.order_by(Applicant.created_at.desc()).all()


@router.post("/hris/applicants", response_model=ApplicantResponse, status_code=201)
def create_applicant(
    body: ApplicantCreate,
    cu: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    _require(cu, _MGR_ROLES)
    posting = db.get(JobPosting, body.posting_id)
    if not posting:  raise HTTPException(404, "Job posting not found")
    if posting.status != PostingStatus.OPEN:
        raise HTTPException(400, "Job posting is not open")

    applicant = Applicant(**body.model_dump(), stage=ApplicantStage.RECEIVED)
    db.add(applicant)
    db.flush()
    write_audit(
        db, "hris_applicants", applicant.id, "CREATE",
        changed_by=cu.id, after=body.model_dump(),
    )
    db.commit()
    db.refresh(applicant)
    return applicant


@router.patch("/hris/applicants/{applicant_id}/stage", response_model=ApplicantResponse)
def update_stage(
    applicant_id: int,
    stage: str,
    cu: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    _require(cu, _MGR_ROLES)
    applicant = (
        db.query(Applicant)
        .filter(Applicant.id == applicant_id)
        .with_for_update()
        .first()
    )
    if not applicant:
        raise HTTPException(404, "Applicant not found")
    try:
        new_stage = ApplicantStage(stage.upper())
    except ValueError:
        raise HTTPException(400, f"Invalid stage '{stage}'")
    allowed_transitions = {
        ApplicantStage.RECEIVED: {ApplicantStage.SCREENING, ApplicantStage.REJECTED},
        ApplicantStage.SCREENING: {ApplicantStage.INTERVIEW, ApplicantStage.REJECTED},
        ApplicantStage.INTERVIEW: {ApplicantStage.OFFER, ApplicantStage.REJECTED},
        ApplicantStage.OFFER: {ApplicantStage.REJECTED},
        ApplicantStage.HIRED: set(),
        ApplicantStage.REJECTED: set(),
    }
    if new_stage == ApplicantStage.HIRED:
        raise HTTPException(409, "Use the hire action to move an applicant to HIRED")
    if new_stage not in allowed_transitions[applicant.stage]:
        raise HTTPException(
            409,
            f"Invalid recruitment transition: {applicant.stage.value} -> {new_stage.value}",
        )
    if new_stage == ApplicantStage.OFFER:
        passed_interview = db.query(Interview.id).filter(
            Interview.applicant_id == applicant.id,
            Interview.result == InterviewResult.PASS,
        ).first()
        if not passed_interview:
            raise HTTPException(409, "A passed interview is required before moving to OFFER")
    old_stage = applicant.stage.value
    applicant.stage = new_stage
    write_audit(
        db, "hris_applicants", applicant.id, "STAGE_CHANGE",
        changed_by=cu.id,
        before={"stage": old_stage},
        after={"stage": new_stage.value},
    )
    db.commit()
    db.refresh(applicant)
    return applicant


@router.post("/hris/applicants/{applicant_id}/hire", response_model=HireResponse)
def hire_applicant(
    applicant_id: int,
    body: HireRequest,
    cu: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    """
    Convert an applicant to Employee + optional User.
    Generates default 10-task onboarding checklist.
    """
    _require(cu, _HR_ROLES)
    applicant = (
        db.query(Applicant)
        .filter(Applicant.id == applicant_id)
        .with_for_update()
        .first()
    )
    if not applicant:
        raise HTTPException(404, "Applicant not found")
    if applicant.employee_id or applicant.stage == ApplicantStage.HIRED:
        raise HTTPException(409, "Applicant is already linked to an employee")
    if applicant.stage != ApplicantStage.OFFER:
        raise HTTPException(409, "Applicant must be in OFFER stage before hiring")

    previous_stage = applicant.stage.value
    join_date = body.join_date or date.today()

    # Applicant ID makes the number deterministic and avoids count()+1 races.
    emp_no = f"EMP-{join_date.strftime('%Y%m%d')}-{applicant.id:06d}"
    suffix = 1
    while db.query(Employee.id).filter(Employee.employee_no == emp_no).first():
        emp_no = f"EMP-{join_date.strftime('%Y%m%d')}-{applicant.id:06d}-{suffix}"
        suffix += 1

    employee = Employee(
        employee_no = emp_no,
        full_name   = applicant.full_name,
        email       = applicant.email,
        phone       = applicant.phone,
        tipe        = EmploymentType.PKWT,
        status      = EmployeeStatus.PROBATION,
        dept_id     = body.department_id,
        grade_id    = body.grade_id,
        join_date   = join_date,
    )
    db.add(employee)
    db.flush()

    user_id: int | None = None
    user_email: str | None = None
    temp_password: str | None = None
    menus_created = 0

    if body.create_user and not applicant.email:
        raise HTTPException(422, "Applicant email is required to create a login account")

    # Optional user creation
    if body.create_user and applicant.email:
        user_email = applicant.email.lower()
        existing_user = db.query(User).filter_by(email=user_email).first()
        if existing_user:
            linked_employee = db.query(Employee).filter(Employee.user_id == existing_user.id).first()
            if linked_employee:
                raise HTTPException(409, "This user account is already linked to another employee")
            employee.user_id = existing_user.id
            user_id = existing_user.id
        else:
            staff_role = db.query(Role).filter_by(name=RoleName.STAFF).first()
            if not staff_role:
                raise HTTPException(409, "STAFF role is not configured")
            temp_password = _temporary_password()
            new_user = User(
                email=user_email,
                full_name=applicant.full_name,
                hashed_password=hash_password(temp_password),
                role_id=staff_role.id,
                is_active=True,
                must_change_password=True,
            )
            db.add(new_user)
            db.flush()
            employee.user_id = new_user.id
            user_id = new_user.id
            menus_created = _seed_staff_menus(db, new_user.id)

    leave_balances_created = 0
    leave_types = db.query(LeaveType).filter(LeaveType.is_active == True).all()
    for leave_type in leave_types:
        db.add(LeaveBalance(
            employee_id=employee.id,
            leave_type_id=leave_type.id,
            year=join_date.year,
            accrued=leave_type.max_days_per_year or 0,
            used=0,
        ))
        leave_balances_created += 1

    applicant.stage = ApplicantStage.HIRED
    applicant.employee_id = employee.id

    # Generate onboarding checklist
    for i, task_text in enumerate(DEFAULT_TASKS):
        db.add(OnboardingTask(applicant_id=applicant.id, task=task_text, sort_order=i))

    write_audit(
        db, "hris_applicants", applicant.id, "HIRE",
        changed_by=cu.id,
        before={"stage": previous_stage},
        after={"stage": "HIRED", "employee_id": employee.id,
               "user_id": user_id, "menus_created": menus_created,
               "leave_balances_created": leave_balances_created},
    )
    db.commit()
    db.refresh(applicant)
    logger.info(f"Hired applicant {applicant_id} → Employee {employee.id} ({emp_no})")
    return HireResponse(
        applicant=ApplicantResponse.model_validate(applicant),
        employee_id=employee.id,
        employee_no=employee.employee_no,
        user_id=user_id,
        user_email=user_email,
        temp_password=temp_password,
        leave_balances_created=leave_balances_created,
    )


# ─── Interviews ───────────────────────────────────────────────────────────────

@router.post("/hris/interviews", response_model=InterviewResponse, status_code=201)
def create_interview(
    body: InterviewCreate,
    cu: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    _require(cu, _MGR_ROLES)
    applicant = (
        db.query(Applicant)
        .filter(Applicant.id == body.applicant_id)
        .with_for_update()
        .first()
    )
    if not applicant:
        raise HTTPException(404, "Applicant not found")
    if applicant.stage not in (ApplicantStage.SCREENING, ApplicantStage.INTERVIEW):
        raise HTTPException(409, "Applicant must be in SCREENING or INTERVIEW stage")
    duplicate = (
        db.query(Interview)
        .filter(
            Interview.applicant_id == applicant.id,
            Interview.result == InterviewResult.PENDING,
        )
        .first()
    )
    if duplicate:
        raise HTTPException(409, "A pending interview is already scheduled for this applicant")

    interview_data = body.model_dump()
    interview_data["interviewer_id"] = body.interviewer_id or cu.id
    interview = Interview(**interview_data, result=InterviewResult.PENDING)
    db.add(interview)
    db.flush()

    applicant.stage = ApplicantStage.INTERVIEW

    write_audit(
        db, "hris_interviews", interview.id, "CREATE",
        changed_by=cu.id, after=body.model_dump(mode="json"),
    )
    db.commit()
    db.refresh(interview)
    return interview


@router.get("/hris/interviews", response_model=list[InterviewResponse])
def list_interviews(
    cu: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
    applicant_id: int | None = None,
):
    _require(cu, _MGR_ROLES)
    query = db.query(Interview)
    if applicant_id is not None:
        query = query.filter(Interview.applicant_id == applicant_id)
    return query.order_by(Interview.scheduled_at.desc(), Interview.id.desc()).all()


@router.patch("/hris/interviews/{interview_id}", response_model=InterviewResponse)
def update_interview(
    interview_id: int,
    result: str,
    cu: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
    notes: str | None = None,
):
    _require(cu, _MGR_ROLES)
    interview = (
        db.query(Interview)
        .filter(Interview.id == interview_id)
        .with_for_update()
        .first()
    )
    if not interview:
        raise HTTPException(404, "Interview not found")
    if interview.result != InterviewResult.PENDING:
        raise HTTPException(409, "Interview result has already been recorded")
    before = {"result": interview.result.value, "notes": interview.notes}
    try:
        new_result = InterviewResult(result.upper())
    except ValueError:
        raise HTTPException(400, f"Invalid result '{result}'")
    if new_result == InterviewResult.PENDING:
        raise HTTPException(400, "Choose PASS, FAIL, or HOLD as the interview result")

    applicant = (
        db.query(Applicant)
        .filter(Applicant.id == interview.applicant_id)
        .with_for_update()
        .first()
    )
    if not applicant:
        raise HTTPException(404, "Applicant not found")
    previous_stage = applicant.stage
    if new_result == InterviewResult.PASS:
        if applicant.stage != ApplicantStage.INTERVIEW:
            raise HTTPException(409, "Applicant is no longer in the INTERVIEW stage")
        applicant.stage = ApplicantStage.OFFER
    elif new_result == InterviewResult.FAIL:
        if applicant.stage != ApplicantStage.INTERVIEW:
            raise HTTPException(409, "Applicant is no longer in the INTERVIEW stage")
        applicant.stage = ApplicantStage.REJECTED

    interview.result = new_result
    if notes is not None:
        interview.notes = notes
    write_audit(
        db, "hris_interviews", interview.id, "UPDATE",
        changed_by=cu.id,
        before=before,
        after={"result": interview.result.value, "notes": interview.notes},
    )
    if applicant.stage != previous_stage:
        write_audit(
            db, "hris_applicants", applicant.id, "INTERVIEW_RESULT",
            changed_by=cu.id,
            before={"stage": previous_stage.value},
            after={"stage": applicant.stage.value, "interview_result": new_result.value},
        )
    db.commit()
    db.refresh(interview)
    return interview


# ─── Onboarding ───────────────────────────────────────────────────────────────

@router.get("/hris/onboarding/{applicant_id}", response_model=list[OnboardingTaskResponse])
def get_onboarding(
    applicant_id: int,
    cu: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    _require(cu, _MGR_ROLES)
    if not db.get(Applicant, applicant_id):
        raise HTTPException(404, "Applicant not found")
    return (
        db.query(OnboardingTask)
        .filter_by(applicant_id=applicant_id)
        .order_by(OnboardingTask.sort_order)
        .all()
    )


@router.patch("/hris/onboarding/tasks/{task_id}", response_model=OnboardingTaskResponse)
def complete_task(
    task_id: int,
    cu: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
    is_completed: bool = True,
):
    _require(cu, _MGR_ROLES)
    task = db.get(OnboardingTask, task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    before = {"is_completed": task.is_completed}
    task.is_completed = is_completed
    task.completed_at = datetime.now(timezone.utc) if is_completed else None
    write_audit(
        db, "hris_onboarding_tasks", task.id, "UPDATE",
        changed_by=cu.id,
        before=before,
        after={"is_completed": is_completed},
    )
    db.commit()
    db.refresh(task)
    return task

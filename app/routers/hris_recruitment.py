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
from datetime import date, datetime, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.audit import write_audit
from app.database import get_db
from app.dependencies import CurrentUser
from app.models import (
    Applicant, ApplicantSource, ApplicantStage,
    Employee, EmployeeStatus, EmploymentType,
    Interview, InterviewResult,
    JobPosting, OnboardingTask,
    PostingStatus, RoleName,
)
from app.schemas import (
    ApplicantCreate, ApplicantResponse,
    HireRequest,
    InterviewCreate, InterviewResponse,
    JobPostingCreate, JobPostingResponse,
    OnboardingTaskResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["HRIS Recruitment"])

_HR_ROLES  = (RoleName.SUPER_ADMIN, RoleName.MD)
_MGR_ROLES = (RoleName.SUPER_ADMIN, RoleName.MD, RoleName.PM, RoleName.GA)


def _require(cu: Any, roles: tuple) -> None:
    if cu.role.name not in roles:
        raise HTTPException(403, f"Requires one of: {[r.value for r in roles]}")


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
    if status:  q = q.filter(JobPosting.status == status.upper())
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
    db.commit()
    db.refresh(posting)
    write_audit(db, cu.id, "CREATE", "hris_job_postings", posting.id, None, body.model_dump())
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
                v = PostingStatus(v.upper())
                if v == PostingStatus.CLOSED:
                    posting.closed_at = datetime.now(timezone.utc)
            setattr(posting, k, v)
    db.commit()
    db.refresh(posting)
    write_audit(db, cu.id, "UPDATE", "hris_job_postings", posting.id, before, body)
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
    if posting_id: q = q.filter_by(posting_id=posting_id)
    if stage:      q = q.filter(Applicant.stage == stage.upper())
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
    posting = db.get(JobPosting, body.posting_id)
    if not posting:  raise HTTPException(404, "Job posting not found")
    if posting.status != PostingStatus.OPEN:
        raise HTTPException(400, "Job posting is not open")

    applicant = Applicant(**body.model_dump(), stage=ApplicantStage.RECEIVED)
    db.add(applicant)
    db.commit()
    db.refresh(applicant)
    write_audit(db, cu.id, "CREATE", "hris_applicants", applicant.id, None, body.model_dump())
    return applicant


@router.patch("/hris/applicants/{applicant_id}/stage", response_model=ApplicantResponse)
def update_stage(
    applicant_id: int,
    stage: str,
    cu: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    _require(cu, _MGR_ROLES)
    applicant = db.get(Applicant, applicant_id)
    if not applicant:
        raise HTTPException(404, "Applicant not found")
    try:
        new_stage = ApplicantStage(stage.upper())
    except ValueError:
        raise HTTPException(400, f"Invalid stage '{stage}'")
    old_stage      = applicant.stage.value
    applicant.stage = new_stage
    db.commit()
    db.refresh(applicant)
    write_audit(db, cu.id, "STAGE_CHANGE", "hris_applicants", applicant.id,
                {"stage": old_stage}, {"stage": new_stage.value})
    return applicant


@router.post("/hris/applicants/{applicant_id}/hire", response_model=ApplicantResponse)
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
    applicant = db.get(Applicant, applicant_id)
    if not applicant:
        raise HTTPException(404, "Applicant not found")
    if applicant.stage == ApplicantStage.HIRED:
        raise HTTPException(400, "Applicant is already hired")

    join_date = body.join_date or date.today()

    # Generate employee number
    count  = db.query(Employee).count()
    emp_no = f"EMP-{join_date.strftime('%Y%m%d')}-{count + 1:03d}"

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

    # Optional user creation
    if body.create_user and applicant.email:
        from app.models import User as UserModel, Role
        existing_user = db.query(UserModel).filter_by(email=applicant.email).first()
        if existing_user:
            employee.user_id = existing_user.id
        else:
            staff_role = db.query(Role).filter_by(name=RoleName.STAFF).first()
            if staff_role:
                import secrets
                from passlib.context import CryptContext
                _pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
                new_user = UserModel(
                    email=applicant.email,
                    full_name=applicant.full_name,
                    hashed_password=_pwd.hash(secrets.token_urlsafe(12)),
                    role_id=staff_role.id,
                    is_active=True,
                )
                db.add(new_user)
                db.flush()
                employee.user_id = new_user.id

    applicant.stage = ApplicantStage.HIRED

    # Generate onboarding checklist
    for i, task_text in enumerate(DEFAULT_TASKS):
        db.add(OnboardingTask(applicant_id=applicant.id, task=task_text, sort_order=i))

    db.commit()
    db.refresh(applicant)

    write_audit(db, cu.id, "HIRE", "hris_applicants", applicant.id,
                {"stage": "OFFER"}, {"stage": "HIRED", "employee_id": employee.id})
    logger.info(f"Hired applicant {applicant_id} → Employee {employee.id} ({emp_no})")
    return applicant


# ─── Interviews ───────────────────────────────────────────────────────────────

@router.post("/hris/interviews", response_model=InterviewResponse, status_code=201)
def create_interview(
    body: InterviewCreate,
    cu: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    _require(cu, _MGR_ROLES)
    applicant = db.get(Applicant, body.applicant_id)
    if not applicant:
        raise HTTPException(404, "Applicant not found")

    interview = Interview(**body.model_dump(), result=InterviewResult.PENDING)
    db.add(interview)
    db.commit()

    # Auto-advance stage
    if applicant.stage in [ApplicantStage.RECEIVED, ApplicantStage.SCREENING]:
        applicant.stage = ApplicantStage.INTERVIEW
        db.commit()

    db.refresh(interview)
    write_audit(db, cu.id, "CREATE", "hris_interviews", interview.id, None, body.model_dump(mode="json"))
    return interview


@router.patch("/hris/interviews/{interview_id}", response_model=InterviewResponse)
def update_interview(
    interview_id: int,
    result: str,
    cu: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
    notes: str | None = None,
):
    _require(cu, _MGR_ROLES)
    interview = db.get(Interview, interview_id)
    if not interview:
        raise HTTPException(404, "Interview not found")
    try:
        interview.result = InterviewResult(result.upper())
    except ValueError:
        raise HTTPException(400, f"Invalid result '{result}'")
    if notes is not None:
        interview.notes = notes
    db.commit()
    db.refresh(interview)
    write_audit(db, cu.id, "UPDATE", "hris_interviews", interview.id, None, {"result": result})
    return interview


# ─── Onboarding ───────────────────────────────────────────────────────────────

@router.get("/hris/onboarding/{applicant_id}", response_model=list[OnboardingTaskResponse])
def get_onboarding(
    applicant_id: int,
    cu: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
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
    task = db.get(OnboardingTask, task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    task.is_completed = is_completed
    task.completed_at = datetime.now(timezone.utc) if is_completed else None
    db.commit()
    db.refresh(task)
    write_audit(db, cu.id, "UPDATE", "hris_onboarding_tasks", task.id,
                {"is_completed": not is_completed}, {"is_completed": is_completed})
    return task

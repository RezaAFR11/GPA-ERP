"""
Projects router — CRUD + Excel/CSV bulk import.
Import template columns (case-insensitive):
  code | name | contract_value | start_date | end_date | status
"""
from __future__ import annotations

import io
from pathlib import Path
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Annotated

import pandas as pd
from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status
from sqlalchemy import case, or_
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.audit import model_to_dict, write_audit
from app.database import get_db
from app.dependencies import CurrentUser, get_client_ip, require_role
from app.finance_queries import project_list_payload, project_lookup_payload
from app.models import (
    AccountReceivable, Expense, InventoryTxn, LegalDocument, PettyCashReport,
    Project, ProjectDocument, ProjectStatus, RoleName,
)
from app.query_sorting import apply_sorting
from app.schemas import (
    MessageResponse, PaginatedResponse, ProjectCreate, ProjectDocumentResponse,
    ProjectImportResult, ProjectLookupResponse, ProjectResponse, ProjectUpdate,
)

router = APIRouter(prefix="/projects", tags=["Projects"])

_write_roles = (RoleName.SUPER_ADMIN, RoleName.MD, RoleName.PM, RoleName.PROJECT_CONTROL, RoleName.COST_CONTROL)


def _get_or_404(project_id: int, db: Session) -> Project:
    p = db.query(Project).filter(Project.id == project_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Project not found")
    return p


# ─── CRUD ────────────────────────────────────────────────────────────────────

@router.get("", response_model=PaginatedResponse[ProjectResponse], summary="List all projects")
def list_projects(
    current_user: CurrentUser,
    db:           Annotated[Session, Depends(get_db)],
    status:       ProjectStatus | None = None,
    archived:     bool | None = None,
    include_archived: bool = False,
    search:       str | None = Query(None, description="Search project name or code"),
    sort_by:      str | None = Query(None, description="Column used to order the result"),
    sort_dir:     str | None = Query(None, pattern="^(asc|desc)$"),
    skip:         int = Query(0, ge=0),
    limit:        int = Query(100, ge=1, le=500),
):
    q = db.query(Project)
    if status:
        q = q.filter(Project.status == status)
    if archived is not None:
        q = q.filter(Project.is_archived == archived)
    elif not include_archived:
        q = q.filter(Project.is_archived == False)  # noqa: E712
    if search:
        q = q.filter(or_(
            Project.name.ilike(f"%{search}%"),
            Project.code.ilike(f"%{search}%"),
        ))
    total = q.count()
    burn_rate = case(
        (Project.contract_value > 0, Project.total_committed / Project.contract_value),
        else_=0,
    )
    margin_rate = case(
        (
            Project.total_revenue > 0,
            (Project.total_revenue - Project.total_committed) / Project.total_revenue,
        ),
        else_=0,
    )
    q = apply_sorting(
        q,
        sort_by=sort_by,
        sort_dir=sort_dir,
        columns={
            "code": Project.code,
            "name": Project.name,
            "status": Project.status,
            "contract_value": Project.contract_value,
            "burn_rate": burn_rate,
            "margin": margin_rate,
            "end_date": Project.end_date,
        },
        default_key="code",
        default_dir="asc",
        tie_breaker=Project.id,
    )
    # Select hybrid totals with each project so response serialization does not
    # lazily load every expense and receivable collection (the N+1 pattern).
    rows = (
        q.with_entities(
            Project,
            Project.total_revenue.label("total_revenue"),
            Project.total_committed.label("total_committed"),
        )
        .offset(skip)
        .limit(limit)
        .all()
    )
    items = [
        project_list_payload(project, total_revenue, total_committed)
        for project, total_revenue, total_committed in rows
    ]
    return {"items": items, "total": total}


@router.get(
    "/lookup",
    response_model=list[ProjectLookupResponse],
    summary="Lightweight project options",
)
def list_project_lookup(
    current_user: CurrentUser,
    db:           Annotated[Session, Depends(get_db)],
    include_archived: bool = False,
    limit:        int = Query(500, ge=1, le=500),
):
    """Load selector fields without project-wide revenue/expense aggregates."""
    q = db.query(Project)
    if not include_archived:
        q = q.filter(Project.is_archived == False)  # noqa: E712
    projects = q.order_by(Project.code.asc(), Project.id.asc()).limit(limit).all()
    return [project_lookup_payload(project) for project in projects]


@router.post("", response_model=ProjectResponse, status_code=201,
             summary="Create a project")
def create_project(
    request:      Request,
    payload:      ProjectCreate,
    current_user: Annotated[object, Depends(require_role(*_write_roles))],
    db:           Annotated[Session, Depends(get_db)],
):
    if db.query(Project).filter(Project.code == payload.code).first():
        raise HTTPException(status_code=409, detail=f"Project code '{payload.code}' already exists")

    project = Project(**payload.model_dump())
    db.add(project)
    db.flush()

    write_audit(db, "Project", project.id, "CREATE",
                changed_by=current_user.id, ip_address=get_client_ip(request),
                after=model_to_dict(project))
    db.commit()
    db.refresh(project)
    return project


@router.get("/{project_id}", response_model=ProjectResponse)
def get_project(
    project_id:   int,
    current_user: CurrentUser,
    db:           Annotated[Session, Depends(get_db)],
):
    return _get_or_404(project_id, db)


@router.get("/{project_id}/documents", response_model=list[ProjectDocumentResponse])
def list_project_documents(
    project_id:   int,
    current_user: CurrentUser,
    db:           Annotated[Session, Depends(get_db)],
):
    _get_or_404(project_id, db)
    return (
        db.query(ProjectDocument)
        .filter(ProjectDocument.project_id == project_id)
        .order_by(ProjectDocument.doc_type, ProjectDocument.title)
        .all()
    )


@router.get("/{project_id}/documents/{doc_id}/file")
def view_project_document(
    project_id:   int,
    doc_id:       int,
    current_user: CurrentUser,
    db:           Annotated[Session, Depends(get_db)],
):
    doc = (
        db.query(ProjectDocument)
        .filter(ProjectDocument.id == doc_id, ProjectDocument.project_id == project_id)
        .first()
    )
    if not doc:
        raise HTTPException(status_code=404, detail="Project document not found")
    path = Path(doc.file_path)
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Original file is missing")
    return FileResponse(path, filename=path.name)


@router.patch("/{project_id}", response_model=ProjectResponse)
def update_project(
    project_id:   int,
    request:      Request,
    payload:      ProjectUpdate,
    current_user: Annotated[object, Depends(require_role(*_write_roles))],
    db:           Annotated[Session, Depends(get_db)],
):
    project = _get_or_404(project_id, db)
    before  = model_to_dict(project)

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(project, field, value)

    write_audit(db, "Project", project.id, "UPDATE",
                changed_by=current_user.id, ip_address=get_client_ip(request),
                before=before, after=model_to_dict(project))
    db.commit()
    db.refresh(project)
    return project


@router.delete("/{project_id}", response_model=MessageResponse)
def delete_project(
    project_id:   int,
    request:      Request,
    current_user: Annotated[object, Depends(require_role(RoleName.SUPER_ADMIN, RoleName.MD))],
    db:           Annotated[Session, Depends(get_db)],
):
    project = _get_or_404(project_id, db)
    if not project.is_archived:
        raise HTTPException(status_code=409, detail="Archive project before deleting it permanently")

    before  = model_to_dict(project)

    petty_reports = db.query(PettyCashReport).filter(PettyCashReport.project_id == project_id).all()
    petty_line_ids = [line.id for report in petty_reports for line in report.lines]
    related_counts = {
        "project_documents": db.query(ProjectDocument).filter(ProjectDocument.project_id == project_id).count(),
        "receivables": db.query(AccountReceivable).filter(AccountReceivable.project_id == project_id).count(),
        "expenses": db.query(Expense).filter(Expense.project_id == project_id).count(),
        "petty_cash_reports": len(petty_reports),
        "petty_cash_lines": len(petty_line_ids),
    }

    if petty_line_ids:
        db.query(Expense).filter(Expense.petty_cash_line_id.in_(petty_line_ids)).update(
            {Expense.petty_cash_line_id: None},
            synchronize_session=False,
        )
        db.flush()

    for doc in db.query(ProjectDocument).filter(ProjectDocument.project_id == project_id).all():
        db.delete(doc)
    for receivable in db.query(AccountReceivable).filter(AccountReceivable.project_id == project_id).all():
        db.delete(receivable)
    for expense in db.query(Expense).filter(Expense.project_id == project_id).all():
        db.delete(expense)
    for report in petty_reports:
        db.delete(report)

    db.query(LegalDocument).filter(LegalDocument.project_id == project_id).update(
        {LegalDocument.project_id: None},
        synchronize_session=False,
    )
    db.query(InventoryTxn).filter(InventoryTxn.project_id == project_id).update(
        {InventoryTxn.project_id: None},
        synchronize_session=False,
    )

    write_audit(db, "Project", project.id, "DELETE",
                changed_by=current_user.id, ip_address=get_client_ip(request),
                before={**before, "deleted_related": related_counts})
    db.delete(project)
    db.commit()
    return MessageResponse(message=f"Archived project {project.code} deleted")


# ─── Excel / CSV Import ──────────────────────────────────────────────────────

@router.post("/import-excel", response_model=ProjectImportResult,
             summary="Bulk-import projects from Excel or CSV")
def import_projects(
    request:      Request,
    file:         UploadFile = File(...),
    current_user: Annotated[object, Depends(require_role(*_write_roles))] = None,
    db:           Annotated[Session, Depends(get_db)] = None,
):
    content_type = file.content_type or ""
    filename     = (file.filename or "").lower()

    try:
        raw = file.file.read()
        if filename.endswith(".csv") or "csv" in content_type:
            df = pd.read_csv(io.BytesIO(raw))
        else:
            df = pd.read_excel(io.BytesIO(raw))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Cannot parse file: {exc}")

    # Normalise column names
    df.columns = [str(c).strip().lower().replace(" ", "_") for c in df.columns]

    required = {"code", "name", "contract_value"}
    missing  = required - set(df.columns)
    if missing:
        raise HTTPException(
            status_code=422,
            detail=f"Missing required columns: {missing}. "
                   "Expected: code, name, contract_value[, start_date, end_date, status]",
        )

    imported = 0
    skipped  = 0
    errors:  list[dict] = []
    now      = datetime.now(timezone.utc)

    for idx, row in df.iterrows():
        row_num = int(idx) + 2  # 1-indexed + header row
        try:
            code = str(row["code"]).strip().upper()
            name = str(row["name"]).strip()

            try:
                contract_value = Decimal(str(row["contract_value"])).quantize(Decimal("0.01"))
            except InvalidOperation:
                raise ValueError(f"Invalid contract_value: {row['contract_value']}")

            if db.query(Project).filter(Project.code == code).first():
                skipped += 1
                continue

            def _parse_date(val) -> datetime | None:
                if pd.isna(val) or str(val).strip() in ("", "nan", "None"):
                    return None
                try:
                    return pd.to_datetime(val).to_pydatetime().replace(tzinfo=timezone.utc)
                except Exception:
                    return None

            start_date = _parse_date(row.get("start_date"))
            end_date   = _parse_date(row.get("end_date"))

            raw_status = str(row.get("status", "active")).strip().lower()
            try:
                proj_status = ProjectStatus(raw_status)
            except ValueError:
                proj_status = ProjectStatus.ACTIVE

            project = Project(
                code=code, name=name, contract_value=contract_value,
                start_date=start_date, end_date=end_date,
                status=proj_status, imported_at=now,
                currency=str(row.get("currency", "IDR") or "IDR").strip().upper()[:3],
            )
            db.add(project)
            db.flush()
            write_audit(db, "Project", project.id, "IMPORT",
                        changed_by=current_user.id, ip_address=get_client_ip(request),
                        after=model_to_dict(project))
            imported += 1

        except Exception as exc:
            errors.append({"row": row_num, "error": str(exc)})

    db.commit()
    return ProjectImportResult(imported=imported, skipped=skipped, errors=errors)

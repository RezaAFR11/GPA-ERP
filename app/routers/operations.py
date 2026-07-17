"""Reusable CRUD and approval workflow for EPC operational modules."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit import model_to_dict, write_audit
from app.database import get_db
from app.dependencies import CurrentUser, get_client_ip
from app.menu_permissions import user_has_menu_access
from app.models import AuditLog, OperationalRecord, Project, RoleName, User
from app.notify import push, push_to_role
from app.operational_modules import (
    APPROVER_ACTIONS,
    DELETABLE_STATUSES,
    EDITABLE_STATUSES,
    MODULE_DEFINITIONS,
    STATUS_TRANSITIONS,
    ModuleDefinition,
    next_status,
)
from app.schemas import (
    AuditLogResponse,
    MessageResponse,
    OperationalModuleResponse,
    OperationalRecordCreate,
    OperationalRecordResponse,
    OperationalRecordUpdate,
    OperationalSummary,
    OperationalTransition,
    PaginatedResponse,
)


router = APIRouter(prefix="/operations", tags=["Operational Workspaces"])


def _module_for_user(module: str, db: Session, user: User) -> ModuleDefinition:
    definition = MODULE_DEFINITIONS.get(module)
    if not definition:
        raise HTTPException(status_code=404, detail="Operational module not found")
    if not user_has_menu_access(db, user, definition.key):
        raise HTTPException(status_code=403, detail=f"Menu access required: {definition.key}")
    return definition


def _get_record(module: str, record_id: int, db: Session) -> OperationalRecord:
    record = (
        db.query(OperationalRecord)
        .filter(OperationalRecord.module == module, OperationalRecord.id == record_id)
        .first()
    )
    if not record:
        raise HTTPException(status_code=404, detail="Operational record not found")
    return record


def _is_approver(user: User, definition: ModuleDefinition) -> bool:
    return user.role.name == RoleName.SUPER_ADMIN or user.role.name in definition.approver_roles


def _can_manage(record: OperationalRecord, user: User, definition: ModuleDefinition) -> bool:
    return (
        _is_approver(user, definition)
        or record.created_by == user.id
        or record.owner_id == user.id
    )


def _validate_record_type(definition: ModuleDefinition, record_type: str) -> None:
    if record_type not in definition.record_types:
        allowed = ", ".join(definition.record_types)
        raise HTTPException(status_code=422, detail=f"Invalid record_type. Allowed: {allowed}")


def _validate_relations(db: Session, project_id: int | None, owner_id: int | None) -> None:
    if project_id is not None and not db.query(Project.id).filter(Project.id == project_id).first():
        raise HTTPException(status_code=404, detail="Project not found")
    if owner_id is not None and not db.query(User.id).filter(User.id == owner_id, User.is_active == True).first():
        raise HTTPException(status_code=404, detail="Active owner user not found")


def _validate_domain_fields(
    module: str,
    record_type: str,
    partner_name: str | None,
    amount: Decimal,
    details: dict,
) -> None:
    if module == "accounts_payable" and record_type in {"vendor_invoice", "payment_voucher"}:
        if not partner_name:
            raise HTTPException(status_code=422, detail="Vendor is required for payable transactions")
        if amount <= 0:
            raise HTTPException(status_code=422, detail="Amount must be greater than zero")

    # Journal payloads may carry line totals in details; when supplied they must balance.
    if module == "accounting_tax" and record_type == "journal_entry":
        debit = Decimal(str(details.get("debit_total", 0)))
        credit = Decimal(str(details.get("credit_total", 0)))
        if (debit or credit) and debit != credit:
            raise HTTPException(status_code=422, detail="Journal debit and credit totals must balance")


def _new_reference(definition: ModuleDefinition) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    return f"{definition.prefix}-{stamp}-{uuid4().hex[:6].upper()}"


def _filtered_query(
    db: Session,
    module: str,
    *,
    project_id: int | None = None,
    record_type: str | None = None,
    record_status: str | None = None,
    search: str | None = None,
):
    query = db.query(OperationalRecord).filter(OperationalRecord.module == module)
    if project_id is not None:
        query = query.filter(OperationalRecord.project_id == project_id)
    if record_type:
        query = query.filter(OperationalRecord.record_type == record_type)
    if record_status:
        query = query.filter(OperationalRecord.status == record_status)
    if search:
        term = f"%{search.strip()}%"
        query = query.filter(or_(
            OperationalRecord.reference_no.ilike(term),
            OperationalRecord.title.ilike(term),
            OperationalRecord.partner_name.ilike(term),
        ))
    return query


@router.get("/modules", response_model=list[OperationalModuleResponse])
def list_modules(current_user: CurrentUser, db: Annotated[Session, Depends(get_db)]):
    return [
        OperationalModuleResponse(
            key=item.key,
            label=item.label,
            description=item.description,
            path=item.path,
            record_types=item.record_types,
            statuses=list(STATUS_TRANSITIONS),
            can_approve=_is_approver(current_user, item),
        )
        for item in MODULE_DEFINITIONS.values()
        if user_has_menu_access(db, current_user, item.key)
    ]


@router.get("/action-queue", response_model=list[OperationalRecordResponse])
def operational_action_queue(
    current_user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    approver_modules = [
        definition.key
        for definition in MODULE_DEFINITIONS.values()
        if user_has_menu_access(db, current_user, definition.key)
        and _is_approver(current_user, definition)
    ]
    if not approver_modules:
        return []
    return (
        db.query(OperationalRecord)
        .filter(
            OperationalRecord.module.in_(approver_modules),
            OperationalRecord.status.in_(["submitted", "in_review"]),
        )
        .order_by(OperationalRecord.due_date.asc().nullslast(), OperationalRecord.id.desc())
        .limit(500)
        .all()
    )


@router.get("/{module}/summary", response_model=OperationalSummary)
def module_summary(
    module: str,
    current_user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    _module_for_user(module, db, current_user)
    query = db.query(OperationalRecord).filter(OperationalRecord.module == module)
    today = date.today()
    due_soon_limit = today + timedelta(days=30)
    total = query.count()
    total_amount = query.with_entities(func.coalesce(func.sum(OperationalRecord.amount), 0)).scalar()
    average_progress = query.with_entities(func.coalesce(func.avg(OperationalRecord.progress), 0)).scalar()
    overdue = query.filter(
        OperationalRecord.due_date < today,
        OperationalRecord.status.notin_(["completed", "closed", "cancelled"]),
    ).count()
    due_soon = query.filter(
        OperationalRecord.due_date >= today,
        OperationalRecord.due_date <= due_soon_limit,
        OperationalRecord.status.notin_(["completed", "closed", "cancelled"]),
    ).count()
    by_status = {
        row.status: row.count
        for row in query.with_entities(
            OperationalRecord.status,
            func.count(OperationalRecord.id).label("count"),
        ).group_by(OperationalRecord.status).all()
    }
    return OperationalSummary(
        total=total,
        total_amount=total_amount or Decimal("0"),
        overdue=overdue,
        due_soon=due_soon,
        average_progress=float(average_progress or 0),
        by_status=by_status,
    )


@router.get("/{module}", response_model=PaginatedResponse[OperationalRecordResponse])
def list_records(
    module: str,
    current_user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
    project_id: int | None = None,
    record_type: str | None = None,
    record_status: str | None = Query(None, alias="status"),
    search: str | None = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
):
    definition = _module_for_user(module, db, current_user)
    if record_type:
        _validate_record_type(definition, record_type)
    query = _filtered_query(
        db, module, project_id=project_id, record_type=record_type,
        record_status=record_status, search=search,
    )
    total = query.count()
    items = query.order_by(OperationalRecord.id.desc()).offset(skip).limit(limit).all()
    return {"items": items, "total": total}


@router.post("/{module}", response_model=OperationalRecordResponse, status_code=status.HTTP_201_CREATED)
def create_record(
    module: str,
    request: Request,
    payload: OperationalRecordCreate,
    current_user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    definition = _module_for_user(module, db, current_user)
    _validate_record_type(definition, payload.record_type)
    _validate_relations(db, payload.project_id, payload.owner_id)
    _validate_domain_fields(module, payload.record_type, payload.partner_name, payload.amount, payload.details)

    reference_no = payload.reference_no or _new_reference(definition)
    if db.query(OperationalRecord.id).filter(
        OperationalRecord.module == module,
        OperationalRecord.reference_no == reference_no,
    ).first():
        raise HTTPException(status_code=409, detail="Reference number already exists in this module")

    now = datetime.now(timezone.utc)
    record = OperationalRecord(
        module=module,
        record_type=payload.record_type,
        reference_no=reference_no,
        title=payload.title,
        description=payload.description,
        priority=payload.priority,
        project_id=payload.project_id,
        partner_name=payload.partner_name,
        amount=payload.amount,
        currency=payload.currency,
        progress=payload.progress,
        due_date=payload.due_date,
        owner_id=payload.owner_id or current_user.id,
        created_by=current_user.id,
        details=payload.details,
        workflow_history=[{
            "action": "create",
            "from_status": None,
            "to_status": "draft",
            "user_id": current_user.id,
            "timestamp": now.isoformat(),
            "note": None,
        }],
    )
    db.add(record)
    db.flush()
    write_audit(
        db, "OperationalRecord", record.id, "CREATE",
        changed_by=current_user.id, ip_address=get_client_ip(request),
        after=model_to_dict(record),
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Reference number already exists") from exc
    db.refresh(record)
    return record


@router.get("/{module}/{record_id}", response_model=OperationalRecordResponse)
def get_record(
    module: str,
    record_id: int,
    current_user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    _module_for_user(module, db, current_user)
    return _get_record(module, record_id, db)


@router.patch("/{module}/{record_id}", response_model=OperationalRecordResponse)
def update_record(
    module: str,
    record_id: int,
    request: Request,
    payload: OperationalRecordUpdate,
    current_user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    definition = _module_for_user(module, db, current_user)
    record = _get_record(module, record_id, db)
    if not _can_manage(record, current_user, definition):
        raise HTTPException(status_code=403, detail="Only the owner, creator, or module approver can update this record")

    updates = payload.model_dump(exclude_unset=True)
    if "record_type" in updates:
        _validate_record_type(definition, updates["record_type"])
    _validate_relations(db, updates.get("project_id", record.project_id), updates.get("owner_id", record.owner_id))

    # Approved records keep their financial and identity fields immutable for audit integrity.
    locked_fields = {"record_type", "reference_no", "title", "project_id", "partner_name", "amount", "currency"}
    if record.status not in EDITABLE_STATUSES:
        changed_locked = [
            field for field in locked_fields
            if field in updates and updates[field] != getattr(record, field)
        ]
        if changed_locked:
            raise HTTPException(
                status_code=409,
                detail=f"Cannot change locked fields after submission: {', '.join(sorted(changed_locked))}",
            )

    candidate_type = updates.get("record_type", record.record_type)
    candidate_partner = updates.get("partner_name", record.partner_name)
    candidate_amount = updates.get("amount", record.amount)
    candidate_details = updates.get("details", record.details)
    _validate_domain_fields(module, candidate_type, candidate_partner, candidate_amount, candidate_details)

    before = model_to_dict(record)
    for field, value in updates.items():
        setattr(record, field, value)
    write_audit(
        db, "OperationalRecord", record.id, "UPDATE",
        changed_by=current_user.id, ip_address=get_client_ip(request),
        before=before, after=model_to_dict(record),
    )
    db.commit()
    db.refresh(record)
    return record


@router.post("/{module}/{record_id}/transition", response_model=OperationalRecordResponse)
def transition_record(
    module: str,
    record_id: int,
    request: Request,
    payload: OperationalTransition,
    current_user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    definition = _module_for_user(module, db, current_user)
    record = _get_record(module, record_id, db)
    target_status = next_status(record.status, payload.action)
    if not target_status:
        raise HTTPException(status_code=409, detail=f"Action '{payload.action}' is not valid from status '{record.status}'")
    if payload.action == "reject" and not (payload.note or "").strip():
        raise HTTPException(status_code=422, detail="A rejection note is required")
    if payload.action in APPROVER_ACTIONS and not _is_approver(current_user, definition):
        raise HTTPException(status_code=403, detail="Module approver role required")
    if payload.action in {"submit", "cancel", "reopen"} and not _can_manage(record, current_user, definition):
        raise HTTPException(status_code=403, detail="Only the owner, creator, or module approver can perform this action")

    before = model_to_dict(record)
    previous_status = record.status
    now = datetime.now(timezone.utc)
    record.status = target_status
    if payload.action == "approve":
        record.approved_by = current_user.id
        record.approved_at = now
    if target_status in {"completed", "closed"}:
        record.progress = Decimal("100")
    if target_status == "closed":
        record.closed_at = now
    elif payload.action == "reopen":
        record.closed_at = None

    record.workflow_history = [
        *(record.workflow_history or []),
        {
            "action": payload.action,
            "from_status": previous_status,
            "to_status": target_status,
            "user_id": current_user.id,
            "timestamp": now.isoformat(),
            "note": payload.note,
        },
    ]
    write_audit(
        db, "OperationalRecord", record.id, payload.action.upper(),
        changed_by=current_user.id, ip_address=get_client_ip(request),
        before=before, after=model_to_dict(record),
    )

    if payload.action == "submit":
        for role in definition.approver_roles:
            push_to_role(
                db, role,
                f"{definition.label}: approval required",
                f"{record.reference_no} - {record.title}",
                f"{definition.path}?record={record.id}",
            )
    elif record.created_by != current_user.id:
        push(
            db, record.created_by,
            f"{definition.label}: {target_status.replace('_', ' ').title()}",
            f"{record.reference_no} - {record.title}",
            f"{definition.path}?record={record.id}",
        )

    db.commit()
    db.refresh(record)
    return record


@router.delete("/{module}/{record_id}", response_model=MessageResponse)
def delete_record(
    module: str,
    record_id: int,
    request: Request,
    current_user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    definition = _module_for_user(module, db, current_user)
    record = _get_record(module, record_id, db)
    if record.status not in DELETABLE_STATUSES:
        raise HTTPException(
            status_code=409,
            detail="Submitted or approved records must remain in the audit trail; cancel or close them instead",
        )
    if not _can_manage(record, current_user, definition):
        raise HTTPException(status_code=403, detail="Only the owner, creator, or module approver can delete this record")

    reference_no = record.reference_no
    write_audit(
        db, "OperationalRecord", record.id, "DELETE",
        changed_by=current_user.id, ip_address=get_client_ip(request),
        before=model_to_dict(record),
    )
    db.delete(record)
    db.commit()
    return MessageResponse(message=f"{reference_no} deleted")


@router.get("/{module}/{record_id}/audit", response_model=list[AuditLogResponse])
def record_audit(
    module: str,
    record_id: int,
    current_user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    _module_for_user(module, db, current_user)
    _get_record(module, record_id, db)
    return (
        db.query(AuditLog)
        .filter(AuditLog.entity_type == "OperationalRecord", AuditLog.entity_id == record_id)
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .all()
    )

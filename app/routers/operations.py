"""Reusable CRUD and approval workflow for EPC operational modules."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit import model_to_dict, write_audit
from app.config import get_settings
from app.database import get_db
from app.dependencies import CurrentUser, get_client_ip
from app.menu_permissions import user_has_menu_access
from app.models import (
    AuditLog,
    ClientPOLineItem,
    ClientPOPaymentTerm,
    OperationalAttachment,
    OperationalRecord,
    Project,
    RoleName,
    User,
)
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
from app.query_sorting import apply_sorting
from app.schemas import (
    AuditLogResponse,
    ClientPODataInput,
    ClientPODetailResponse,
    MessageResponse,
    OperationalModuleResponse,
    OperationalAttachmentResponse,
    OperationalRecordCreate,
    OperationalRecordResponse,
    OperationalRecordUpdate,
    OperationalSummary,
    OperationalTransition,
    PaginatedResponse,
)


router = APIRouter(prefix="/operations", tags=["Operational Workspaces"])
settings = get_settings()
OPERATIONAL_UPLOAD_ROOT = Path(settings.UPLOAD_DIR) / "operational"
_MAX_ATTACHMENT_SIZE = settings.MAX_UPLOAD_MB * 1024 * 1024
_CLIENT_PO_TYPE = "client_purchase_order"
_MONEY_TOLERANCE = Decimal("1.00")


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


def _detail_decimal(details: dict, key: str) -> Decimal:
    try:
        return Decimal(str(details.get(key, 0) or 0))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"Invalid numeric value for '{key}'") from exc


def _sum_decimal(items, field: str) -> Decimal:
    return sum((Decimal(str(getattr(item, field))) for item in items), Decimal("0"))


def _within_money_tolerance(left: Decimal, right: Decimal) -> bool:
    return abs(left - right) <= _MONEY_TOLERANCE


def _validate_client_po(
    *,
    module: str,
    record_type: str,
    project_id: int | None,
    partner_name: str | None,
    amount: Decimal,
    details: dict,
    client_po: ClientPODataInput | None = None,
    existing_line_items=(),
    existing_payment_terms=(),
    require_complete: bool = False,
) -> None:
    if record_type != _CLIENT_PO_TYPE:
        if client_po is not None:
            raise HTTPException(
                status_code=422,
                detail="Structured client_po data is only valid for Client Purchase Order records",
            )
        return
    if module != "contract_management":
        raise HTTPException(status_code=422, detail="Client Purchase Orders belong to Contract Management")
    if project_id is None:
        raise HTTPException(status_code=422, detail="Project is required for a Client Purchase Order")
    if not (partner_name or "").strip():
        raise HTTPException(status_code=422, detail="Client name is required for a Client Purchase Order")
    if amount <= 0:
        raise HTTPException(status_code=422, detail="DPP amount must be greater than zero")

    tax_amount = _detail_decimal(details, "tax_amount")
    grand_total = _detail_decimal(details, "grand_total")
    if tax_amount < 0:
        raise HTTPException(status_code=422, detail="Tax amount cannot be negative")
    if not _within_money_tolerance(grand_total, amount + tax_amount):
        raise HTTPException(status_code=422, detail="Grand total must equal DPP plus tax")

    line_items = client_po.line_items if client_po is not None else list(existing_line_items)
    payment_terms = client_po.payment_terms if client_po is not None else list(existing_payment_terms)
    if require_complete:
        required_details = {
            "po_date": "PO date",
            "delivery_term": "delivery term",
            "ship_to_location": "ship-to location",
            "warranty_months": "warranty period",
        }
        missing = [label for key, label in required_details.items() if not details.get(key)]
        if missing:
            raise HTTPException(status_code=422, detail=f"Complete Client PO fields before submission: {', '.join(missing)}")
        if not line_items:
            raise HTTPException(status_code=422, detail="At least one BOQ line is required before submission")
        if not payment_terms:
            raise HTTPException(status_code=422, detail="At least one payment term is required before submission")

    if line_items:
        sequences = [item.sequence for item in line_items]
        item_numbers = [item.item_no.strip().lower() for item in line_items]
        if len(sequences) != len(set(sequences)):
            raise HTTPException(status_code=422, detail="BOQ line sequence must be unique")
        if len(item_numbers) != len(set(item_numbers)):
            raise HTTPException(status_code=422, detail="BOQ item number must be unique")
        for item in line_items:
            expected_line_total = Decimal(str(item.quantity)) * Decimal(str(item.unit_price))
            if not _within_money_tolerance(Decimal(str(item.line_total)), expected_line_total):
                raise HTTPException(
                    status_code=422,
                    detail=f"BOQ item {item.item_no} total must equal quantity times unit price",
                )
        if not _within_money_tolerance(_sum_decimal(line_items, "line_total"), amount):
            raise HTTPException(status_code=422, detail="BOQ line totals must equal the Client PO DPP")

    if payment_terms:
        sequences = [term.sequence for term in payment_terms]
        if len(sequences) != len(set(sequences)):
            raise HTTPException(status_code=422, detail="Payment term sequence must be unique")
        percentage_total = _sum_decimal(payment_terms, "percentage")
        if abs(percentage_total - Decimal("100")) > Decimal("0.01"):
            raise HTTPException(status_code=422, detail="Payment term percentages must total 100%")
        if not _within_money_tolerance(_sum_decimal(payment_terms, "dpp_amount"), amount):
            raise HTTPException(status_code=422, detail="Payment term DPP amounts must equal the Client PO DPP")
        if not _within_money_tolerance(_sum_decimal(payment_terms, "tax_amount"), tax_amount):
            raise HTTPException(status_code=422, detail="Payment term tax amounts must equal the Client PO tax")
        if not _within_money_tolerance(_sum_decimal(payment_terms, "gross_amount"), grand_total):
            raise HTTPException(status_code=422, detail="Payment term gross amounts must equal the Client PO grand total")
        for term in payment_terms:
            if not _within_money_tolerance(
                Decimal(str(term.gross_amount)),
                Decimal(str(term.dpp_amount)) + Decimal(str(term.tax_amount)),
            ):
                raise HTTPException(
                    status_code=422,
                    detail=f"Payment term {term.sequence} gross amount must equal DPP plus tax",
                )


def _replace_client_po_children(
    db: Session,
    record: OperationalRecord,
    client_po: ClientPODataInput,
) -> None:
    record.client_po_line_items.clear()
    record.client_po_payment_terms.clear()
    # Flush removals first so replacement rows can reuse the same sequence.
    db.flush()
    record.client_po_line_items.extend(
        ClientPOLineItem(**item.model_dump()) for item in client_po.line_items
    )
    record.client_po_payment_terms.extend(
        ClientPOPaymentTerm(**term.model_dump()) for term in client_po.payment_terms
    )


def _safe_attachment_path(file_path: str) -> Path:
    root = OPERATIONAL_UPLOAD_ROOT.resolve()
    path = Path(file_path).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Attachment file not found") from exc
    return path


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
    sort_by: str | None = Query(None, description="Column used to order the result"),
    sort_dir: str | None = Query(None, pattern="^(asc|desc)$"),
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
    query = query.outerjoin(Project, OperationalRecord.project_id == Project.id)
    query = apply_sorting(
        query,
        sort_by=sort_by,
        sort_dir=sort_dir,
        columns={
            "id": OperationalRecord.id,
            "reference_no": OperationalRecord.reference_no,
            "record_type": OperationalRecord.record_type,
            "title": OperationalRecord.title,
            "project_partner": func.coalesce(Project.code, OperationalRecord.partner_name),
            "amount": OperationalRecord.amount,
            "progress": OperationalRecord.progress,
            "due_date": OperationalRecord.due_date,
            "status": OperationalRecord.status,
        },
        default_key="id",
        default_dir="desc",
        tie_breaker=OperationalRecord.id,
    )
    items = query.offset(skip).limit(limit).all()
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
    _validate_client_po(
        module=module,
        record_type=payload.record_type,
        project_id=payload.project_id,
        partner_name=payload.partner_name,
        amount=payload.amount,
        details=payload.details,
        client_po=payload.client_po,
    )

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
    if payload.client_po is not None:
        _replace_client_po_children(db, record, payload.client_po)
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


@router.get("/{module}/{record_id}/client-po", response_model=ClientPODetailResponse)
def get_client_po_detail(
    module: str,
    record_id: int,
    current_user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    definition = _module_for_user(module, db, current_user)
    record = _get_record(module, record_id, db)
    if record.record_type != _CLIENT_PO_TYPE:
        raise HTTPException(status_code=404, detail="Client Purchase Order detail not found")
    attachments = [
        attachment
        for attachment in record.attachments
        if not attachment.is_confidential or _can_manage(record, current_user, definition)
    ]
    return ClientPODetailResponse(
        line_items=record.client_po_line_items,
        payment_terms=record.client_po_payment_terms,
        attachments=attachments,
    )


@router.post(
    "/{module}/{record_id}/attachments",
    response_model=OperationalAttachmentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_operational_attachment(
    module: str,
    record_id: int,
    request: Request,
    current_user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
    file: UploadFile = File(...),
    title: str = Form(..., min_length=1, max_length=255),
    doc_type: str = Form(default="client_po", min_length=1, max_length=50),
    reference_no: str | None = Form(default=None, max_length=100),
    is_confidential: bool = Form(default=True),
):
    definition = _module_for_user(module, db, current_user)
    record = _get_record(module, record_id, db)
    if record.record_type != _CLIENT_PO_TYPE:
        raise HTTPException(status_code=422, detail="This attachment endpoint is reserved for Client Purchase Orders")
    if record.status not in EDITABLE_STATUSES:
        raise HTTPException(status_code=409, detail="Attachments cannot be changed after submission")
    if not _can_manage(record, current_user, definition):
        raise HTTPException(status_code=403, detail="Only the owner, creator, or module approver can upload attachments")

    original_filename = Path(file.filename or "client-po.pdf").name[:255]
    if Path(original_filename).suffix.lower() != ".pdf":
        raise HTTPException(status_code=400, detail="Client PO attachments must be PDF files")
    data = await file.read(_MAX_ATTACHMENT_SIZE + 1)
    if not data:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")
    if len(data) > _MAX_ATTACHMENT_SIZE:
        raise HTTPException(status_code=400, detail=f"File must be under {settings.MAX_UPLOAD_MB} MB")
    if not data.startswith(b"%PDF-"):
        raise HTTPException(status_code=400, detail="Uploaded file is not a valid PDF")

    target_dir = OPERATIONAL_UPLOAD_ROOT / module / str(record.id)
    target_dir.mkdir(parents=True, exist_ok=True)
    stored_filename = f"{uuid4().hex}.pdf"
    destination = target_dir / stored_filename
    destination.write_bytes(data)

    attachment = OperationalAttachment(
        operational_record_id=record.id,
        doc_type=doc_type,
        title=title,
        reference_no=reference_no,
        original_filename=original_filename,
        stored_filename=stored_filename,
        file_path=str(destination),
        content_type="application/pdf",
        file_size=len(data),
        is_confidential=is_confidential,
        uploaded_by=current_user.id,
    )
    db.add(attachment)
    db.flush()
    write_audit(
        db, "OperationalAttachment", attachment.id, "CREATE",
        changed_by=current_user.id, ip_address=get_client_ip(request),
        after=model_to_dict(attachment),
    )
    try:
        db.commit()
    except Exception:
        db.rollback()
        destination.unlink(missing_ok=True)
        raise
    db.refresh(attachment)
    return attachment


@router.get("/{module}/{record_id}/attachments/{attachment_id}/file")
def download_operational_attachment(
    module: str,
    record_id: int,
    attachment_id: int,
    current_user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    definition = _module_for_user(module, db, current_user)
    record = _get_record(module, record_id, db)
    attachment = db.query(OperationalAttachment).filter(
        OperationalAttachment.id == attachment_id,
        OperationalAttachment.operational_record_id == record.id,
    ).first()
    if not attachment:
        raise HTTPException(status_code=404, detail="Attachment not found")
    if attachment.is_confidential and not _can_manage(record, current_user, definition):
        raise HTTPException(status_code=403, detail="Confidential attachment access denied")
    file_path = _safe_attachment_path(attachment.file_path)
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="Attachment file not found")
    return FileResponse(
        file_path,
        media_type=attachment.content_type,
        filename=attachment.original_filename,
    )


@router.delete("/{module}/{record_id}/attachments/{attachment_id}", response_model=MessageResponse)
def delete_operational_attachment(
    module: str,
    record_id: int,
    attachment_id: int,
    request: Request,
    current_user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    definition = _module_for_user(module, db, current_user)
    record = _get_record(module, record_id, db)
    if record.status not in EDITABLE_STATUSES:
        raise HTTPException(status_code=409, detail="Attachments cannot be changed after submission")
    if not _can_manage(record, current_user, definition):
        raise HTTPException(status_code=403, detail="Only the owner, creator, or module approver can delete attachments")
    attachment = db.query(OperationalAttachment).filter(
        OperationalAttachment.id == attachment_id,
        OperationalAttachment.operational_record_id == record.id,
    ).first()
    if not attachment:
        raise HTTPException(status_code=404, detail="Attachment not found")

    file_path = _safe_attachment_path(attachment.file_path)
    original_filename = attachment.original_filename
    write_audit(
        db, "OperationalAttachment", attachment.id, "DELETE",
        changed_by=current_user.id, ip_address=get_client_ip(request),
        before=model_to_dict(attachment),
    )
    db.delete(attachment)
    db.commit()
    file_path.unlink(missing_ok=True)
    return MessageResponse(message=f"{original_filename} deleted")


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

    client_po_supplied = "client_po" in payload.model_fields_set and payload.client_po is not None
    updates = payload.model_dump(exclude_unset=True, exclude={"client_po"})
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
        if client_po_supplied or (
            "details" in updates
            and record.record_type == _CLIENT_PO_TYPE
            and updates["details"] != record.details
        ):
            raise HTTPException(status_code=409, detail="Client PO commercial details are locked after submission")

    candidate_type = updates.get("record_type", record.record_type)
    candidate_partner = updates.get("partner_name", record.partner_name)
    candidate_amount = updates.get("amount", record.amount)
    candidate_details = updates.get("details", record.details)
    _validate_domain_fields(module, candidate_type, candidate_partner, candidate_amount, candidate_details)
    _validate_client_po(
        module=module,
        record_type=candidate_type,
        project_id=updates.get("project_id", record.project_id),
        partner_name=candidate_partner,
        amount=candidate_amount,
        details=candidate_details,
        client_po=payload.client_po if client_po_supplied else None,
        existing_line_items=record.client_po_line_items,
        existing_payment_terms=record.client_po_payment_terms,
    )

    before = model_to_dict(record)
    for field, value in updates.items():
        setattr(record, field, value)
    if client_po_supplied:
        _replace_client_po_children(db, record, payload.client_po)
    elif candidate_type != _CLIENT_PO_TYPE:
        record.client_po_line_items.clear()
        record.client_po_payment_terms.clear()
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
    if payload.action == "submit" and record.record_type == _CLIENT_PO_TYPE:
        _validate_client_po(
            module=module,
            record_type=record.record_type,
            project_id=record.project_id,
            partner_name=record.partner_name,
            amount=record.amount,
            details=record.details,
            existing_line_items=record.client_po_line_items,
            existing_payment_terms=record.client_po_payment_terms,
            require_complete=True,
        )

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
    attachment_paths = [attachment.file_path for attachment in record.attachments]
    write_audit(
        db, "OperationalRecord", record.id, "DELETE",
        changed_by=current_user.id, ip_address=get_client_ip(request),
        before=model_to_dict(record),
    )
    db.delete(record)
    db.commit()
    for attachment_path in attachment_paths:
        _safe_attachment_path(attachment_path).unlink(missing_ok=True)
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

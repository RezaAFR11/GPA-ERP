"""
Account Receivables router.
Revenue can only be recognised by MD or SUPER_ADMIN (confirm step).
Confirming an AR updates the revenue-driven budget visible on Project.
"""
from datetime import datetime, timezone
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.audit import model_to_dict, write_audit
from app.database import get_db
from app.dependencies import CurrentUser, get_client_ip, require_role
from app.models import ARStatus, AccountReceivable, Project, RoleName
from app.schemas import ARConfirm, ARCreate, ARResponse, ARUpdate, MessageResponse, PaginatedResponse

router = APIRouter(prefix="/receivables", tags=["Revenue – Account Receivables"])

_create_roles  = (RoleName.SUPER_ADMIN, RoleName.MD, RoleName.FINANCE)
_confirm_roles = (RoleName.SUPER_ADMIN, RoleName.MD)


def _get_or_404(ar_id: int, db: Session) -> AccountReceivable:
    ar = db.query(AccountReceivable).filter(AccountReceivable.id == ar_id).first()
    if not ar:
        raise HTTPException(status_code=404, detail="Receivable not found")
    return ar


@router.get("", response_model=PaginatedResponse[ARResponse], summary="List receivables")
def list_receivables(
    current_user:  CurrentUser,
    db:            Annotated[Session, Depends(get_db)],
    project_id:    int | None     = None,
    ar_status:     ARStatus | None = None,
    search:        str | None     = Query(None, description="Search invoice number or customer name"),
    payment_state: str | None     = Query(None, description="Filter by payment state: paid | partial | open"),
    skip:          int = Query(0, ge=0),
    limit:         int = Query(100, ge=1, le=500),
):
    q = db.query(AccountReceivable)
    if project_id:
        q = q.filter(AccountReceivable.project_id == project_id)
    if ar_status:
        q = q.filter(AccountReceivable.status == ar_status)
    if search:
        q = q.filter(or_(
            AccountReceivable.invoice_no.ilike(f"%{search}%"),
            AccountReceivable.customer_name.ilike(f"%{search}%"),
        ))
    if payment_state == "paid":
        q = q.filter(
            (AccountReceivable.remaining_amount <= 0) |
            (AccountReceivable.actual_payment >= AccountReceivable.amount)
        )
    elif payment_state == "partial":
        q = q.filter(
            AccountReceivable.actual_payment > 0,
            AccountReceivable.remaining_amount > 0,
        )
    elif payment_state == "open":
        q = q.filter(
            (AccountReceivable.actual_payment == None) |
            (AccountReceivable.actual_payment == 0)
        )
    total = q.count()
    items = q.order_by(AccountReceivable.id.desc()).offset(skip).limit(limit).all()
    return {"items": items, "total": total}


@router.post("", response_model=ARResponse, status_code=201,
             summary="Create a draft receivable (billing claim)")
def create_receivable(
    request:      Request,
    payload:      ARCreate,
    current_user: Annotated[object, Depends(require_role(*_create_roles))],
    db:           Annotated[Session, Depends(get_db)],
):
    project = db.query(Project).filter(Project.id == payload.project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    ar = AccountReceivable(
        project_id        = payload.project_id,
        amount            = payload.amount,
        description       = payload.description,
        invoice_no        = payload.invoice_no,
        customer_name     = payload.customer_name,
        invoice_date      = payload.invoice_date,
        due_date          = payload.due_date,
        expected_payment  = payload.expected_payment,
        actual_payment    = payload.actual_payment,
        remaining_amount  = payload.remaining_amount,
        paid_at           = payload.paid_at,
        status            = ARStatus.DRAFT,
    )
    db.add(ar)
    db.flush()

    write_audit(db, "AccountReceivable", ar.id, "CREATE",
                changed_by=current_user.id, ip_address=get_client_ip(request),
                after=model_to_dict(ar))
    db.commit()
    db.refresh(ar)
    return ar


@router.get("/{ar_id}", response_model=ARResponse)
def get_receivable(
    ar_id:        int,
    current_user: CurrentUser,
    db:           Annotated[Session, Depends(get_db)],
):
    return _get_or_404(ar_id, db)


@router.patch("/{ar_id}", response_model=ARResponse, summary="Update invoice/payment details")
def update_receivable(
    ar_id:        int,
    request:      Request,
    payload:      ARUpdate,
    current_user: Annotated[object, Depends(require_role(*_create_roles))],
    db:           Annotated[Session, Depends(get_db)],
):
    ar = _get_or_404(ar_id, db)
    before = model_to_dict(ar)

    updates = payload.model_dump(exclude_unset=True)

    # Once confirmed, the receivable drives the project budget ceiling —
    # amount/project can no longer be changed, only payment tracking fields.
    if ar.status == ARStatus.CONFIRMED:
        for locked_field in ("amount", "project_id"):
            if locked_field in updates and updates[locked_field] != getattr(ar, locked_field):
                raise HTTPException(
                    status_code=409,
                    detail=f"Cannot change '{locked_field}' on a confirmed receivable — it drives the project budget ceiling",
                )
            updates.pop(locked_field, None)

    for field, value in updates.items():
        setattr(ar, field, value)

    if ar.actual_payment is not None and ar.remaining_amount is None:
        ar.remaining_amount = max(ar.amount - ar.actual_payment, Decimal("0"))

    write_audit(db, "AccountReceivable", ar.id, "UPDATE",
                changed_by=current_user.id, ip_address=get_client_ip(request),
                before=before, after=model_to_dict(ar))
    db.commit()
    db.refresh(ar)
    return ar


@router.post("/{ar_id}/confirm", response_model=ARResponse,
             summary="Confirm (recognise) revenue — MD/SUPER_ADMIN only")
def confirm_receivable(
    ar_id:        int,
    request:      Request,
    payload:      ARConfirm,
    current_user: Annotated[object, Depends(require_role(*_confirm_roles))],
    db:           Annotated[Session, Depends(get_db)],
):
    ar = _get_or_404(ar_id, db)

    if ar.status == ARStatus.CONFIRMED:
        raise HTTPException(status_code=409, detail="Receivable is already confirmed")

    before = model_to_dict(ar)
    ar.status       = ARStatus.CONFIRMED
    ar.confirmed_by = current_user.id
    ar.confirmed_at = datetime.now(timezone.utc)

    write_audit(db, "AccountReceivable", ar.id, "CONFIRM",
                changed_by=current_user.id, ip_address=get_client_ip(request),
                before=before, after=model_to_dict(ar))
    db.commit()
    db.refresh(ar)
    return ar


@router.delete("/{ar_id}", response_model=MessageResponse,
               summary="Delete a DRAFT receivable")
def delete_receivable(
    ar_id:        int,
    request:      Request,
    current_user: Annotated[object, Depends(require_role(*_confirm_roles))],
    db:           Annotated[Session, Depends(get_db)],
):
    ar = _get_or_404(ar_id, db)
    if ar.status == ARStatus.CONFIRMED:
        raise HTTPException(status_code=409, detail="Cannot delete a confirmed receivable")

    write_audit(db, "AccountReceivable", ar.id, "DELETE",
                changed_by=current_user.id, ip_address=get_client_ip(request),
                before=model_to_dict(ar))
    db.delete(ar)
    db.commit()
    return MessageResponse(message=f"Receivable #{ar_id} deleted")

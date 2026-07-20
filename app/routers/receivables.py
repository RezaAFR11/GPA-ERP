"""
Account Receivables router.
Revenue can only be recognised by MD or SUPER_ADMIN (confirm step).
Confirming an AR updates the revenue-driven budget visible on Project.
"""
from datetime import datetime, timezone
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import case, func, or_
from sqlalchemy.orm import Session, joinedload

from app.audit import model_to_dict, write_audit
from app.database import get_db
from app.dependencies import CurrentUser, get_client_ip, require_role
from app.models import ARStatus, AccountReceivable, Project, ProjectStatus, RoleName, User
from app.query_sorting import apply_sorting
from app.schemas import ARConfirm, ARCreate, ARResponse, ARSummary, ARUpdate, MessageResponse, PaginatedResponse

router = APIRouter(prefix="/receivables", tags=["Revenue – Account Receivables"])

_create_roles  = (RoleName.SUPER_ADMIN, RoleName.MD, RoleName.FINANCE)
_confirm_roles = (RoleName.SUPER_ADMIN, RoleName.MD)


def _get_or_404(ar_id: int, db: Session) -> AccountReceivable:
    ar = db.query(AccountReceivable).filter(AccountReceivable.id == ar_id).first()
    if not ar:
        raise HTTPException(status_code=404, detail="Receivable not found")
    return ar


def _paid_amount_expr():
    return case(
        (AccountReceivable.actual_payment.isnot(None), AccountReceivable.actual_payment),
        else_=Decimal("0"),
    )


def _remaining_amount_expr(paid_expr):
    return func.greatest(AccountReceivable.amount - paid_expr, 0)


def _require_active_project(project_id: int, db: Session) -> Project:
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if project.is_archived or project.status != ProjectStatus.ACTIVE:
        raise HTTPException(status_code=409, detail="New invoices require an active, non-archived project")
    return project


def _apply_receivable_filters(
    q,
    project_id: int | None = None,
    ar_status: ARStatus | None = None,
    search: str | None = None,
    payment_state: str | None = None,
):
    if project_id:
        q = q.filter(AccountReceivable.project_id == project_id)
    if ar_status:
        q = q.filter(AccountReceivable.status == ar_status)
    if search:
        q = q.filter(or_(
            AccountReceivable.invoice_no.ilike(f"%{search}%"),
            AccountReceivable.customer_name.ilike(f"%{search}%"),
        ))

    paid_expr = _paid_amount_expr()
    remaining_expr = _remaining_amount_expr(paid_expr)
    if payment_state == "paid":
        q = q.filter(paid_expr > 0, remaining_expr <= Decimal("1"))
    elif payment_state == "partial":
        q = q.filter(paid_expr > 0, remaining_expr > Decimal("1"))
    elif payment_state == "open":
        q = q.filter(paid_expr <= 0)
    return q


@router.get("", response_model=PaginatedResponse[ARResponse], summary="List receivables")
def list_receivables(
    current_user:  CurrentUser,
    db:            Annotated[Session, Depends(get_db)],
    project_id:    int | None     = None,
    ar_status:     ARStatus | None = None,
    search:        str | None     = Query(None, description="Search invoice number or customer name"),
    payment_state: str | None     = Query(None, description="Filter by payment state: paid | partial | open"),
    sort_by:       str | None     = Query(None, description="Column used to order the result"),
    sort_dir:      str | None     = Query(None, pattern="^(asc|desc)$"),
    skip:          int = Query(0, ge=0),
    limit:         int = Query(100, ge=1, le=500),
):
    paid_expr = _paid_amount_expr()
    remaining_expr = _remaining_amount_expr(paid_expr)
    q = _apply_receivable_filters(
        db.query(AccountReceivable)
        .outerjoin(Project)
        # ARResponse includes the confirmer and role; load both with the page.
        .options(joinedload(AccountReceivable.confirmer).joinedload(User.role)),
        project_id=project_id,
        ar_status=ar_status,
        search=search,
        payment_state=payment_state,
    )
    total = q.count()
    q = apply_sorting(
        q,
        sort_by=sort_by,
        sort_dir=sort_dir,
        columns={
            "id": AccountReceivable.id,
            "invoice_no": AccountReceivable.invoice_no,
            "project": Project.code,
            "customer_name": AccountReceivable.customer_name,
            "description": AccountReceivable.description,
            "amount": AccountReceivable.amount,
            "paid": paid_expr,
            "outstanding": remaining_expr,
            "due_date": AccountReceivable.due_date,
            "status": AccountReceivable.status,
        },
        default_key="id",
        default_dir="desc",
        tie_breaker=AccountReceivable.id,
    )
    items = q.offset(skip).limit(limit).all()
    return {"items": items, "total": total}


@router.get("/summary", response_model=ARSummary, summary="Receivables summary totals")
def receivables_summary(
    current_user:  CurrentUser,
    db:            Annotated[Session, Depends(get_db)],
    project_id:    int | None      = None,
    ar_status:     ARStatus | None = None,
    search:        str | None      = Query(None, description="Search invoice number or customer name"),
    payment_state: str | None      = Query(None, description="Filter by payment state: paid | partial | open"),
):
    paid_expr = _paid_amount_expr()
    remaining_expr = _remaining_amount_expr(paid_expr)
    q = _apply_receivable_filters(
        db.query(
            func.coalesce(func.sum(AccountReceivable.amount), 0).label("total_invoiced"),
            func.coalesce(func.sum(paid_expr), 0).label("total_paid"),
            func.coalesce(func.sum(remaining_expr), 0).label("total_outstanding"),
            func.count(AccountReceivable.id).label("count"),
        ),
        project_id=project_id,
        ar_status=ar_status,
        search=search,
        payment_state=payment_state,
    )
    row = q.one()._mapping
    total_invoiced = row["total_invoiced"] or Decimal("0")
    total_paid = row["total_paid"] or Decimal("0")
    total_outstanding = row["total_outstanding"] or Decimal("0")
    collection_rate = float((total_paid / total_invoiced) * Decimal("100")) if total_invoiced else 0.0
    return ARSummary(
        total_invoiced=total_invoiced,
        total_paid=total_paid,
        total_outstanding=total_outstanding,
        collection_rate=collection_rate,
        count=row["count"] or 0,
    )


@router.post("", response_model=ARResponse, status_code=201,
             summary="Create a draft receivable (billing claim)")
def create_receivable(
    request:      Request,
    payload:      ARCreate,
    current_user: Annotated[object, Depends(require_role(*_create_roles))],
    db:           Annotated[Session, Depends(get_db)],
):
    _require_active_project(payload.project_id, db)

    paid_amount = payload.actual_payment or Decimal("0")
    if paid_amount > payload.amount:
        raise HTTPException(status_code=422, detail="Payment received cannot exceed invoice amount")

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
        remaining_amount  = max(payload.amount - paid_amount, Decimal("0")),
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
    updates.pop("remaining_amount", None)

    if "project_id" in updates and updates["project_id"] != ar.project_id:
        _require_active_project(updates["project_id"], db)

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

    paid_amount = ar.actual_payment or Decimal("0")
    if paid_amount > ar.amount:
        raise HTTPException(status_code=422, detail="Payment received cannot exceed invoice amount")
    ar.remaining_amount = max(ar.amount - paid_amount, Decimal("0"))

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

"""
GPA-ERP — Inventory & Stock router
CRUD for inventory items + stock-in / stock-out / adjustment transactions.
"""
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.audit import model_to_dict, write_audit
from app.database import get_db
from app.dependencies import get_client_ip, get_current_user, require_role
from app.models import InventoryItem, InventoryTxn, Project, ProjectStatus, RoleName, TxnType, User
from app.schemas import (
    InventoryItemCreate, InventoryItemResponse, InventoryItemUpdate, InventorySummary,
    InventoryTxnCreate, InventoryTxnResponse, MessageResponse, PaginatedResponse,
)

router = APIRouter(prefix="/inventory", tags=["Inventory"])

DB        = Annotated[Session, Depends(get_db)]
Auth      = Annotated[User,    Depends(get_current_user)]
# Mutations restricted to roles that manage physical stock
_inv_roles = (RoleName.GA, RoleName.HR, RoleName.COST_CONTROL, RoleName.PM, RoleName.PROJECT_CONTROL, RoleName.MD, RoleName.SUPER_ADMIN)
InvWrite  = Annotated[User,    Depends(require_role(*_inv_roles))]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_item_or_404(item_id: int, db: Session) -> InventoryItem:
    item = db.get(InventoryItem, item_id)
    if not item:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Item not found")
    return item


def _apply_txn(item: InventoryItem, txn: InventoryTxnCreate) -> None:
    if txn.txn_type == TxnType.IN:
        item.qty_on_hand += txn.quantity
    elif txn.txn_type == TxnType.OUT:
        if item.qty_on_hand < txn.quantity:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"Insufficient stock: have {item.qty_on_hand} {item.unit}, need {txn.quantity}",
            )
        item.qty_on_hand -= txn.quantity
    else:
        item.qty_on_hand = txn.quantity


# ── Item endpoints ────────────────────────────────────────────────────────────

@router.get("", response_model=PaginatedResponse[InventoryItemResponse])
def list_items(
    db:          DB,
    _:           Auth,
    category:    str | None = Query(None),
    low_stock:   bool       = Query(False),
    active_only: bool       = Query(True),
    is_active:   bool | None = Query(None),
    q:           str | None = Query(None),
    skip:        int        = Query(0, ge=0),
    limit:       int        = Query(50, ge=1, le=200),
):
    query = db.query(InventoryItem)
    if is_active is not None:
        query = query.filter(InventoryItem.is_active == is_active)
    elif active_only:
        query = query.filter(InventoryItem.is_active == True)
    if category:
        query = query.filter(InventoryItem.category == category)
    if low_stock:
        query = query.filter(
            InventoryItem.min_stock > 0,
            InventoryItem.qty_on_hand <= InventoryItem.min_stock,
        )
    if q:
        like = f"%{q}%"
        query = query.filter(
            InventoryItem.name.ilike(like) | InventoryItem.code.ilike(like)
        )
    total = query.count()
    items = query.order_by(InventoryItem.category, InventoryItem.name).offset(skip).limit(limit).all()
    return {"items": items, "total": total}


@router.get("/summary", response_model=InventorySummary, summary="Active inventory totals")
def inventory_summary(db: DB, _: Auth):
    active_filter = InventoryItem.is_active == True
    total_items = db.query(func.count(InventoryItem.id)).filter(active_filter).scalar() or 0
    low_stock_count = (
        db.query(func.count(InventoryItem.id))
        .filter(
            active_filter,
            InventoryItem.min_stock > 0,
            InventoryItem.qty_on_hand <= InventoryItem.min_stock,
        )
        .scalar()
        or 0
    )
    total_value = (
        db.query(func.coalesce(func.sum(
            func.coalesce(InventoryItem.unit_cost, Decimal("0")) * InventoryItem.qty_on_hand
        ), Decimal("0")))
        .filter(active_filter)
        .scalar()
        or Decimal("0")
    )
    return InventorySummary(
        total_items=total_items,
        low_stock_count=low_stock_count,
        total_value=total_value,
    )


@router.post("", response_model=InventoryItemResponse, status_code=status.HTTP_201_CREATED)
def create_item(request: Request, payload: InventoryItemCreate, db: DB, current_user: InvWrite):
    if db.query(InventoryItem).filter(InventoryItem.code == payload.code).first():
        raise HTTPException(status.HTTP_409_CONFLICT, f"Item code '{payload.code}' already exists")
    item = InventoryItem(**payload.model_dump())
    db.add(item)
    db.flush()
    write_audit(
        db, "InventoryItem", item.id, "CREATE",
        changed_by=current_user.id, ip_address=get_client_ip(request),
        after=model_to_dict(item),
    )
    db.commit()
    db.refresh(item)
    return item


@router.get("/{item_id}", response_model=InventoryItemResponse)
def get_item(item_id: int, db: DB, _: Auth):
    return _get_item_or_404(item_id, db)


@router.patch("/{item_id}", response_model=InventoryItemResponse)
def update_item(item_id: int, request: Request, payload: InventoryItemUpdate, db: DB, current_user: InvWrite):
    item = _get_item_or_404(item_id, db)
    before = model_to_dict(item)
    was_active = item.is_active
    updates = payload.model_dump(exclude_unset=True)
    if updates.get("is_active") is False and item.qty_on_hand != 0:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Item must have zero stock before it can be deactivated",
        )
    for k, v in updates.items():
        setattr(item, k, v)
    action = "RESTORE" if not was_active and item.is_active else "UPDATE"
    write_audit(
        db, "InventoryItem", item.id, action,
        changed_by=current_user.id, ip_address=get_client_ip(request),
        before=before, after=model_to_dict(item),
    )
    db.commit()
    db.refresh(item)
    return item


@router.delete("/{item_id}", response_model=MessageResponse)
def delete_item(item_id: int, request: Request, db: DB, current_user: InvWrite):
    item = _get_item_or_404(item_id, db)
    if item.qty_on_hand != 0:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Item must have zero stock before it can be deactivated",
        )
    before = model_to_dict(item)
    item.is_active = False
    write_audit(
        db, "InventoryItem", item.id, "DEACTIVATE",
        changed_by=current_user.id, ip_address=get_client_ip(request),
        before=before, after=model_to_dict(item),
    )
    db.commit()
    return {"message": "Item deactivated"}


# ── Transaction endpoints ─────────────────────────────────────────────────────

@router.post("/{item_id}/txn", response_model=InventoryItemResponse, status_code=status.HTTP_201_CREATED)
def record_transaction(item_id: int, request: Request, payload: InventoryTxnCreate, db: DB, user: InvWrite):
    item = _get_item_or_404(item_id, db)
    if not item.is_active:
        raise HTTPException(status.HTTP_409_CONFLICT, "Cannot record transactions for an inactive item")
    if payload.project_id is not None:
        project = db.query(Project).filter(Project.id == payload.project_id).first()
        if not project:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
        if project.is_archived or project.status != ProjectStatus.ACTIVE:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Inventory transactions require an active, non-archived project",
            )
    item_before = model_to_dict(item)
    _apply_txn(item, payload)
    txn = InventoryTxn(
        item_id    = item_id,
        txn_type   = payload.txn_type,
        quantity   = payload.quantity,
        reference  = payload.reference,
        notes      = payload.notes,
        project_id = payload.project_id,
        created_by = user.id,
    )
    db.add(txn)
    db.flush()
    write_audit(
        db, "InventoryTxn", txn.id, f"STOCK_{payload.txn_type.value.upper()}",
        changed_by=user.id, ip_address=get_client_ip(request),
        before={"item": item_before},
        after={"item": model_to_dict(item), "transaction": model_to_dict(txn)},
    )
    db.commit()
    db.refresh(item)
    return item


@router.get("/{item_id}/txns", response_model=list[InventoryTxnResponse])
def list_transactions(
    item_id: int,
    db:      DB,
    _:       Auth,
    limit:   int = Query(50, le=200),
):
    _get_item_or_404(item_id, db)
    return (
        db.query(InventoryTxn)
        .filter(InventoryTxn.item_id == item_id)
        .order_by(InventoryTxn.created_at.desc())
        .limit(limit)
        .all()
    )

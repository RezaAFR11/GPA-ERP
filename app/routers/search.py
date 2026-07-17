"""
GPA-ERP — Global search endpoint.
Returns top-N results per entity group in a single call.
"""
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import CurrentUser
from app.menu_permissions import user_has_menu_access
from app.models import (
    AccountReceivable, Expense, InventoryItem,
    LegalDocument, OperationalRecord, Project, RoleName,
)
from app.operational_modules import MODULE_DEFINITIONS

router = APIRouter(prefix="/search", tags=["Search"])

_LIMIT = 5  # results per group


@router.get("", summary="Global cross-entity search")
def global_search(
    current_user: CurrentUser,
    db:           Annotated[Session, Depends(get_db)],
    q:            str = Query(..., min_length=1, max_length=200),
    limit:        int = Query(_LIMIT, ge=1, le=20),
):
    like = f"%{q}%"

    projects = []
    if user_has_menu_access(db, current_user, "project_command"):
        projects = (
            db.query(Project.id, Project.code, Project.name, Project.status)
            .filter(
                Project.name.ilike(like) | Project.code.ilike(like),
                Project.is_archived == False,  # noqa: E712
            )
            .order_by(Project.code)
            .limit(limit)
            .all()
        )

    expenses = []
    if user_has_menu_access(db, current_user, "spending", "action_center"):
        expenses_query = db.query(
            Expense.id, Expense.description, Expense.amount, Expense.status,
        ).filter(Expense.description.ilike(like))
        if current_user.role.name in {RoleName.STAFF, RoleName.WORKER}:
            expenses_query = expenses_query.filter(Expense.submitted_by == current_user.id)
        expenses = expenses_query.order_by(Expense.id.desc()).limit(limit).all()

    receivables = []
    if user_has_menu_access(db, current_user, "revenue_ar"):
        receivables = (
            db.query(
                AccountReceivable.id,
                AccountReceivable.invoice_no,
                AccountReceivable.customer_name,
                AccountReceivable.amount,
                AccountReceivable.status,
            )
            .filter(
                AccountReceivable.invoice_no.ilike(like)
                | AccountReceivable.customer_name.ilike(like)
            )
            .order_by(AccountReceivable.id.desc())
            .limit(limit)
            .all()
        )

    legal_docs = []
    if user_has_menu_access(db, current_user, "legal"):
        legal_docs = (
            db.query(
                LegalDocument.id,
                LegalDocument.doc_number,
                LegalDocument.title,
                LegalDocument.doc_type,
                LegalDocument.status,
            )
            .filter(
                LegalDocument.doc_number.ilike(like) | LegalDocument.title.ilike(like)
            )
            .order_by(LegalDocument.created_at.desc())
            .limit(limit)
            .all()
        )

    inventory = []
    if user_has_menu_access(db, current_user, "inventory"):
        inventory = (
            db.query(
                InventoryItem.id,
                InventoryItem.code,
                InventoryItem.name,
                InventoryItem.category,
                InventoryItem.qty_on_hand,
                InventoryItem.unit,
            )
            .filter(
                InventoryItem.name.ilike(like) | InventoryItem.code.ilike(like),
                InventoryItem.is_active == True,  # noqa: E712
            )
            .order_by(InventoryItem.name)
            .limit(limit)
            .all()
        )

    allowed_operational_modules = [
        key for key in MODULE_DEFINITIONS
        if user_has_menu_access(db, current_user, key)
    ]
    operational_records = []
    if allowed_operational_modules:
        operational_records = (
            db.query(
                OperationalRecord.id,
                OperationalRecord.module,
                OperationalRecord.reference_no,
                OperationalRecord.title,
                OperationalRecord.status,
            )
            .filter(
                OperationalRecord.module.in_(allowed_operational_modules),
                or_(
                    OperationalRecord.reference_no.ilike(like),
                    OperationalRecord.title.ilike(like),
                    OperationalRecord.partner_name.ilike(like),
                ),
            )
            .order_by(OperationalRecord.updated_at.desc())
            .limit(limit)
            .all()
        )

    def _row(r):
        return dict(zip(r._fields, r))

    operational_payload = []
    for row in operational_records:
        payload = _row(row)
        payload["path"] = MODULE_DEFINITIONS[row.module].path
        operational_payload.append(payload)

    return {
        "projects":    [_row(r) for r in projects],
        "expenses":    [_row(r) for r in expenses],
        "receivables": [_row(r) for r in receivables],
        "legal_docs":  [_row(r) for r in legal_docs],
        "inventory":   [_row(r) for r in inventory],
        "operational_records": operational_payload,
    }

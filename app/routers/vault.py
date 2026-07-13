"""
Vault router — Super-Admin-only configuration space.
Manages Cost Codes and Approval Rules (the approval matrix).
Also exposes the global Audit Log.
"""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from app.audit import model_to_dict, write_audit
from app.database import get_db
from app.dependencies import SuperAdminUser, get_client_ip, require_role
from app.models import AuditLog, ApprovalRule, CostCentre, CostCode, RoleName
from app.schemas import (
    ApprovalRuleCreate, ApprovalRuleResponse, ApprovalRuleUpdate,
    AuditLogResponse, CostCentreCreate, CostCentreResponse, CostCentreUpdate,
    CostCodeCreate, CostCodeResponse, CostCodeUpdate, MessageResponse, PaginatedResponse,
)

router = APIRouter(prefix="/vault", tags=["Vault – Super Admin"])


# ─── Cost Codes ──────────────────────────────────────────────────────────────

@router.get("/cost-codes", response_model=list[CostCodeResponse],
            summary="List all cost codes")
def list_cost_codes(
    current_user: Annotated[object, Depends(require_role(
        RoleName.SUPER_ADMIN, RoleName.MD, RoleName.COST_CONTROL, RoleName.PM
    ))],
    db:           Annotated[Session, Depends(get_db)],
    active_only:  bool = True,
    skip:         int = 0,
    limit:        int = 500,
):
    q = db.query(CostCode)
    if active_only:
        q = q.filter(CostCode.is_active == True)
    return q.order_by(CostCode.code).offset(skip).limit(limit).all()


@router.post("/cost-codes", response_model=CostCodeResponse, status_code=201,
             summary="Create cost code — Super Admin only")
def create_cost_code(
    request:      Request,
    payload:      CostCodeCreate,
    current_user: SuperAdminUser,
    db:           Annotated[Session, Depends(get_db)],
):
    if db.query(CostCode).filter(CostCode.code == payload.code).first():
        raise HTTPException(status_code=409, detail="Cost code already exists")

    if payload.parent_id:
        parent = db.query(CostCode).filter(CostCode.id == payload.parent_id).first()
        if not parent:
            raise HTTPException(status_code=404, detail="Parent cost code not found")

    cc = CostCode(**payload.model_dump())
    db.add(cc)
    db.flush()

    write_audit(db, "CostCode", cc.id, "CREATE",
                changed_by=current_user.id, ip_address=get_client_ip(request),
                after=model_to_dict(cc))
    db.commit()
    db.refresh(cc)
    return cc


@router.get("/cost-codes/{cc_id}", response_model=CostCodeResponse)
def get_cost_code(
    cc_id:        int,
    current_user: Annotated[object, Depends(require_role(
        RoleName.SUPER_ADMIN, RoleName.MD, RoleName.COST_CONTROL, RoleName.PM
    ))],
    db:           Annotated[Session, Depends(get_db)],
):
    cc = db.query(CostCode).filter(CostCode.id == cc_id).first()
    if not cc:
        raise HTTPException(status_code=404, detail="Cost code not found")
    return cc


@router.patch("/cost-codes/{cc_id}", response_model=CostCodeResponse,
              summary="Update cost code — Super Admin only")
def update_cost_code(
    cc_id:        int,
    request:      Request,
    payload:      CostCodeUpdate,
    current_user: SuperAdminUser,
    db:           Annotated[Session, Depends(get_db)],
):
    cc = db.query(CostCode).filter(CostCode.id == cc_id).first()
    if not cc:
        raise HTTPException(status_code=404, detail="Cost code not found")

    before = model_to_dict(cc)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(cc, field, value)

    write_audit(db, "CostCode", cc.id, "UPDATE",
                changed_by=current_user.id, ip_address=get_client_ip(request),
                before=before, after=model_to_dict(cc))
    db.commit()
    db.refresh(cc)
    return cc


@router.delete("/cost-codes/{cc_id}", response_model=MessageResponse,
               summary="Deactivate cost code — Super Admin only")
def deactivate_cost_code(
    cc_id:        int,
    request:      Request,
    current_user: SuperAdminUser,
    db:           Annotated[Session, Depends(get_db)],
):
    cc = db.query(CostCode).filter(CostCode.id == cc_id).first()
    if not cc:
        raise HTTPException(status_code=404, detail="Cost code not found")

    before = model_to_dict(cc)
    cc.is_active = False
    write_audit(db, "CostCode", cc.id, "DEACTIVATE",
                changed_by=current_user.id, ip_address=get_client_ip(request),
                before=before, after=model_to_dict(cc))
    db.commit()
    return MessageResponse(message=f"Cost code {cc.code} deactivated")


@router.get("/cost-centres", response_model=list[CostCentreResponse],
            summary="List cost centres")
def list_cost_centres(
    current_user: Annotated[object, Depends(require_role(
        RoleName.SUPER_ADMIN, RoleName.MD, RoleName.COST_CONTROL, RoleName.PM, RoleName.FINANCE
    ))],
    db:           Annotated[Session, Depends(get_db)],
    active_only:  bool = True,
):
    q = db.query(CostCentre)
    if active_only:
        q = q.filter(CostCentre.is_active == True)
    return q.order_by(CostCentre.code).all()


@router.post("/cost-centres", response_model=CostCentreResponse, status_code=201,
             summary="Create cost centre - Super Admin only")
def create_cost_centre(
    request:      Request,
    payload:      CostCentreCreate,
    current_user: SuperAdminUser,
    db:           Annotated[Session, Depends(get_db)],
):
    if db.query(CostCentre).filter(CostCentre.code == payload.code).first():
        raise HTTPException(status_code=409, detail="Cost centre already exists")
    centre = CostCentre(**payload.model_dump())
    db.add(centre)
    db.flush()
    write_audit(db, "CostCentre", centre.id, "CREATE",
                changed_by=current_user.id, ip_address=get_client_ip(request),
                after=model_to_dict(centre))
    db.commit()
    db.refresh(centre)
    return centre


@router.patch("/cost-centres/{centre_id}", response_model=CostCentreResponse,
              summary="Update cost centre - Super Admin only")
def update_cost_centre(
    centre_id:    int,
    request:      Request,
    payload:      CostCentreUpdate,
    current_user: SuperAdminUser,
    db:           Annotated[Session, Depends(get_db)],
):
    centre = db.query(CostCentre).filter(CostCentre.id == centre_id).first()
    if not centre:
        raise HTTPException(status_code=404, detail="Cost centre not found")
    before = model_to_dict(centre)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(centre, field, value)
    write_audit(db, "CostCentre", centre.id, "UPDATE",
                changed_by=current_user.id, ip_address=get_client_ip(request),
                before=before, after=model_to_dict(centre))
    db.commit()
    db.refresh(centre)
    return centre


@router.delete("/cost-centres/{centre_id}", response_model=MessageResponse,
               summary="Deactivate cost centre - Super Admin only")
def deactivate_cost_centre(
    centre_id:    int,
    request:      Request,
    current_user: SuperAdminUser,
    db:           Annotated[Session, Depends(get_db)],
):
    centre = db.query(CostCentre).filter(CostCentre.id == centre_id).first()
    if not centre:
        raise HTTPException(status_code=404, detail="Cost centre not found")
    before = model_to_dict(centre)
    centre.is_active = False
    write_audit(db, "CostCentre", centre.id, "DEACTIVATE",
                changed_by=current_user.id, ip_address=get_client_ip(request),
                before=before, after=model_to_dict(centre))
    db.commit()
    return MessageResponse(message=f"Cost centre {centre.code} deactivated")


# ─── Approval Rules (the matrix) ─────────────────────────────────────────────

@router.get("/approval-rules", response_model=list[ApprovalRuleResponse],
            summary="List approval matrix rules")
def list_approval_rules(
    current_user: Annotated[object, Depends(require_role(
        RoleName.SUPER_ADMIN, RoleName.MD
    ))],
    db:           Annotated[Session, Depends(get_db)],
    active_only:  bool = True,
):
    q = db.query(ApprovalRule)
    if active_only:
        q = q.filter(ApprovalRule.is_active == True)
    return q.order_by(ApprovalRule.priority, ApprovalRule.min_amount).all()


@router.post("/approval-rules", response_model=ApprovalRuleResponse, status_code=201,
             summary="Create approval rule — Super Admin only")
def create_approval_rule(
    request:      Request,
    payload:      ApprovalRuleCreate,
    current_user: SuperAdminUser,
    db:           Annotated[Session, Depends(get_db)],
):
    rule = ApprovalRule(**payload.model_dump())
    db.add(rule)
    db.flush()

    write_audit(db, "ApprovalRule", rule.id, "CREATE",
                changed_by=current_user.id, ip_address=get_client_ip(request),
                after=model_to_dict(rule))
    db.commit()
    db.refresh(rule)
    return rule


@router.get("/approval-rules/{rule_id}", response_model=ApprovalRuleResponse)
def get_approval_rule(
    rule_id:      int,
    current_user: Annotated[object, Depends(require_role(RoleName.SUPER_ADMIN, RoleName.MD))],
    db:           Annotated[Session, Depends(get_db)],
):
    rule = db.query(ApprovalRule).filter(ApprovalRule.id == rule_id).first()
    if not rule:
        raise HTTPException(status_code=404, detail="Approval rule not found")
    return rule


@router.patch("/approval-rules/{rule_id}", response_model=ApprovalRuleResponse,
              summary="Update approval rule — Super Admin only")
def update_approval_rule(
    rule_id:      int,
    request:      Request,
    payload:      ApprovalRuleUpdate,
    current_user: SuperAdminUser,
    db:           Annotated[Session, Depends(get_db)],
):
    rule = db.query(ApprovalRule).filter(ApprovalRule.id == rule_id).first()
    if not rule:
        raise HTTPException(status_code=404, detail="Approval rule not found")

    updates = payload.model_dump(exclude_unset=True)
    next_min = updates.get("min_amount", rule.min_amount)
    next_max = updates.get("max_amount", rule.max_amount)
    if next_min is None:
        raise HTTPException(status_code=422, detail="min_amount cannot be null")
    if next_max is not None and next_max <= next_min:
        raise HTTPException(status_code=422, detail="max_amount must be greater than min_amount")

    before = model_to_dict(rule)
    for field, value in updates.items():
        setattr(rule, field, value)

    write_audit(db, "ApprovalRule", rule.id, "UPDATE",
                changed_by=current_user.id, ip_address=get_client_ip(request),
                before=before, after=model_to_dict(rule))
    db.commit()
    db.refresh(rule)
    return rule


@router.delete("/approval-rules/{rule_id}", response_model=MessageResponse,
               summary="Deactivate approval rule — Super Admin only")
def deactivate_approval_rule(
    rule_id:      int,
    request:      Request,
    current_user: SuperAdminUser,
    db:           Annotated[Session, Depends(get_db)],
):
    rule = db.query(ApprovalRule).filter(ApprovalRule.id == rule_id).first()
    if not rule:
        raise HTTPException(status_code=404, detail="Approval rule not found")

    before = model_to_dict(rule)
    rule.is_active = False
    write_audit(db, "ApprovalRule", rule.id, "DEACTIVATE",
                changed_by=current_user.id, ip_address=get_client_ip(request),
                before=before, after=model_to_dict(rule))
    db.commit()
    return MessageResponse(message=f"Approval rule #{rule_id} deactivated")


# ─── Global Audit Log ────────────────────────────────────────────────────────

@router.get("/audit-log/entity-types", response_model=list[str],
            summary="List audit entity types — Super Admin only")
def audit_entity_types(
    current_user: SuperAdminUser,
    db:           Annotated[Session, Depends(get_db)],
):
    return [
        row[0]
        for row in db.query(AuditLog.entity_type)
        .distinct()
        .order_by(AuditLog.entity_type)
        .all()
    ]


@router.get("/audit-log", response_model=PaginatedResponse[AuditLogResponse],
            summary="Global audit log — Super Admin only")
def global_audit_log(
    current_user:  SuperAdminUser,
    db:            Annotated[Session, Depends(get_db)],
    entity_type:   str | None = None,
    entity_id:     int | None = None,
    changed_by:    int | None = None,
    skip:          int = Query(0, ge=0),
    limit:         int = Query(50, ge=1, le=500),
):
    q = db.query(AuditLog)
    if entity_type:
        q = q.filter(AuditLog.entity_type == entity_type)
    if entity_id:
        q = q.filter(AuditLog.entity_id == entity_id)
    if changed_by:
        q = q.filter(AuditLog.changed_by == changed_by)
    total = q.count()
    items = q.order_by(AuditLog.created_at.desc()).offset(skip).limit(limit).all()
    return {"items": items, "total": total}

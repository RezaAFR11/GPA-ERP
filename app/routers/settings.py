from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.audit import model_to_dict, write_audit
from app.database import get_db
from app.dependencies import CurrentUser, get_client_ip, super_admin_only
from app.models import User, WorkspaceBranding
from app.schemas import WorkspaceBrandingResponse, WorkspaceBrandingUpdate


router = APIRouter(prefix="/settings", tags=["Settings"])

_DEFAULT_BRANDING = WorkspaceBrandingResponse(
    logo="GP",
    title="GPA",
    subtitle="Cost Control",
)


@router.get("/branding", response_model=WorkspaceBrandingResponse)
def get_workspace_branding(
    current_user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    branding = db.query(WorkspaceBranding).order_by(WorkspaceBranding.id).first()
    return branding or _DEFAULT_BRANDING


@router.put("/branding", response_model=WorkspaceBrandingResponse)
def update_workspace_branding(
    request: Request,
    payload: WorkspaceBrandingUpdate,
    current_user: Annotated[User, Depends(super_admin_only)],
    db: Annotated[Session, Depends(get_db)],
):
    branding = db.query(WorkspaceBranding).order_by(WorkspaceBranding.id).first()
    before = model_to_dict(branding) if branding else None
    if branding is None:
        branding = WorkspaceBranding()
        db.add(branding)
        db.flush()

    branding.logo = payload.logo
    branding.title = payload.title
    branding.subtitle = payload.subtitle
    write_audit(
        db,
        "WorkspaceBranding",
        branding.id,
        "UPDATE",
        changed_by=current_user.id,
        ip_address=get_client_ip(request),
        before=before,
        after=model_to_dict(branding),
    )
    db.commit()
    db.refresh(branding)
    return branding

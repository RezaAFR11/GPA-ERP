"""
GPA-ERP — Legal Documents router
Proposal / offering letters on company KOP SURAT with MD/PM approval flow.
"""
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import Response
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.audit import model_to_dict, write_audit
from app.database import get_db
from app.dependencies import CurrentUser, get_client_ip, get_current_user, require_role
from app.models import DocStatus, DocType, LegalDocument, RoleName, User
from app.pdf_generator import MD_SIGNATURE_PATH, generate_document_pdf, resolve_md_signature_path
from app.schemas import (
    LegalDocCreate, LegalDocRejectRequest, LegalDocResponse, LegalDocUpdate,
    MessageResponse, PaginatedResponse,
)

router = APIRouter(prefix="/legal", tags=["Legal"])

_TYPE_CODES = {
    DocType.PROPOSAL:     "SPH",
    DocType.BERITA_ACARA: "BA",
    DocType.SURAT_JALAN:  "SJ",
    DocType.OTHER:        "SRT",
}

_SIGN_ROLES = (RoleName.MD, RoleName.PM, RoleName.PROJECT_CONTROL, RoleName.SUPER_ADMIN)
_SIGNATURE_TYPES = {"image/png", "image/jpeg", "image/jpg"}


def _next_doc_number(db: Session, doc_type: DocType) -> str:
    year = datetime.now().year
    prefix = f"GPA/{_TYPE_CODES[doc_type]}/{year}/"
    count = db.query(func.count(LegalDocument.id)).filter(
        LegalDocument.doc_type == doc_type,
        LegalDocument.doc_number.like(f"{prefix}%"),
    ).scalar() or 0
    return f"{prefix}{count + 1:03d}"


@router.get("/signature/md", summary="Check MD signature asset")
def md_signature_status(
    current_user: Annotated[User, Depends(require_role(RoleName.MD, RoleName.SUPER_ADMIN))],
):
    return {"exists": resolve_md_signature_path().exists()}


@router.post("/signature/md", summary="Upload MD signature asset")
async def upload_md_signature(
    current_user: Annotated[User, Depends(require_role(RoleName.MD, RoleName.SUPER_ADMIN))],
    file: UploadFile = File(...),
):
    if file.content_type not in _SIGNATURE_TYPES:
        raise HTTPException(status_code=400, detail="Signature must be a PNG or JPG image")
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Signature image is empty")
    if len(data) > 2_000_000:
        raise HTTPException(status_code=400, detail="Signature image must be under 2 MB")

    try:
        MD_SIGNATURE_PATH.parent.mkdir(parents=True, exist_ok=True)
        MD_SIGNATURE_PATH.write_bytes(data)
    except OSError as exc:
        raise HTTPException(
            status_code=503,
            detail="Signature storage is unavailable. Check UPLOAD_DIR configuration.",
        ) from exc
    return MessageResponse(message="MD signature uploaded")


# ─── List ─────────────────────────────────────────────────────────────────────

@router.get("", response_model=PaginatedResponse[LegalDocResponse], summary="List legal documents")
def list_documents(
    current_user: Annotated[User, Depends(get_current_user)],
    db:           Annotated[Session, Depends(get_db)],
    doc_type: DocType | None = None,
    status:   DocStatus | None = None,
    search:   str | None = Query(None, description="Search document number or title"),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
):
    q = db.query(LegalDocument)
    if doc_type:
        q = q.filter(LegalDocument.doc_type == doc_type)
    if status:
        q = q.filter(LegalDocument.status == status)
    if search:
        q = q.filter(or_(
            LegalDocument.doc_number.ilike(f"%{search}%"),
            LegalDocument.title.ilike(f"%{search}%"),
        ))
    total = q.count()
    items = q.order_by(LegalDocument.created_at.desc()).offset(skip).limit(limit).all()
    return {"items": items, "total": total}


# ─── Create ───────────────────────────────────────────────────────────────────

@router.post("", response_model=LegalDocResponse, status_code=status.HTTP_201_CREATED,
             summary="Create a draft legal document")
def create_document(
    request:      Request,
    payload:      LegalDocCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    db:           Annotated[Session, Depends(get_db)],
):
    doc = LegalDocument(
        doc_number        = payload.doc_number or _next_doc_number(db, payload.doc_type),
        reference_number  = payload.reference_number,
        doc_type          = payload.doc_type,
        title             = payload.title,
        subject           = payload.subject,
        body              = payload.body,
        recipient_name    = payload.recipient_name,
        recipient_company = payload.recipient_company,
        recipient_address = payload.recipient_address,
        closing           = payload.closing,
        quoted_amount     = payload.quoted_amount,
        project_id        = payload.project_id,
        created_by        = current_user.id,
        status            = DocStatus.DRAFT,
    )
    db.add(doc)
    db.flush()
    write_audit(db, "LegalDocument", doc.id, "CREATE",
                changed_by=current_user.id, ip_address=get_client_ip(request),
                after=model_to_dict(doc))
    db.commit()
    db.refresh(doc)
    return doc


# ─── Get ──────────────────────────────────────────────────────────────────────

@router.get("/{doc_id}", response_model=LegalDocResponse, summary="Get a document")
def get_document(
    doc_id:       int,
    current_user: Annotated[User, Depends(get_current_user)],
    db:           Annotated[Session, Depends(get_db)],
):
    doc = db.query(LegalDocument).filter(LegalDocument.id == doc_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    return doc


# ─── Update (draft or rejected) ───────────────────────────────────────────────

@router.patch("/{doc_id}", response_model=LegalDocResponse, summary="Update a draft or rejected document")
def update_document(
    doc_id:       int,
    request:      Request,
    payload:      LegalDocUpdate,
    current_user: Annotated[User, Depends(get_current_user)],
    db:           Annotated[Session, Depends(get_db)],
):
    doc = db.query(LegalDocument).filter(LegalDocument.id == doc_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    if doc.status not in (DocStatus.DRAFT, DocStatus.REJECTED):
        raise HTTPException(status_code=400, detail="Only draft or rejected documents can be edited")
    if doc.created_by != current_user.id and current_user.role.name not in (RoleName.SUPER_ADMIN, RoleName.MD):
        raise HTTPException(status_code=403, detail="Not your document")

    before = model_to_dict(doc)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(doc, field, value)

    if doc.doc_type != DocType.PROPOSAL:
        doc.quoted_amount = None

    if doc.status == DocStatus.REJECTED:
        doc.status = DocStatus.DRAFT

    write_audit(db, "LegalDocument", doc.id, "UPDATE",
                changed_by=current_user.id, ip_address=get_client_ip(request),
                before=before, after=model_to_dict(doc))
    db.commit()
    db.refresh(doc)
    return doc


# ─── Submit for signing ───────────────────────────────────────────────────────

@router.post("/{doc_id}/submit", response_model=LegalDocResponse,
             summary="Submit document for MD/PM signature")
def submit_document(
    doc_id:       int,
    request:      Request,
    current_user: Annotated[User, Depends(get_current_user)],
    db:           Annotated[Session, Depends(get_db)],
):
    doc = db.query(LegalDocument).filter(LegalDocument.id == doc_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    if doc.status != DocStatus.DRAFT:
        raise HTTPException(status_code=400, detail="Document is not in draft status")
    if doc.created_by != current_user.id and current_user.role.name != RoleName.SUPER_ADMIN:
        raise HTTPException(status_code=403, detail="Not authorized to submit this document")

    before = model_to_dict(doc)
    doc.status         = DocStatus.SUBMITTED
    doc.rejection_note = None
    write_audit(db, "LegalDocument", doc.id, "SUBMIT",
                changed_by=current_user.id, ip_address=get_client_ip(request),
                before=before, after=model_to_dict(doc))
    db.commit()
    db.refresh(doc)
    return doc


# ─── Sign (MD / PM only) ──────────────────────────────────────────────────────

@router.post("/{doc_id}/sign", response_model=LegalDocResponse,
             summary="Approve and sign the document (MD/PM only)")
def sign_document(
    doc_id:       int,
    request:      Request,
    current_user: Annotated[User, Depends(require_role(*_SIGN_ROLES))],
    db:           Annotated[Session, Depends(get_db)],
):
    doc = db.query(LegalDocument).filter(LegalDocument.id == doc_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    if doc.status != DocStatus.SUBMITTED:
        raise HTTPException(status_code=400, detail="Document must be submitted before signing")

    before = model_to_dict(doc)
    doc.status    = DocStatus.SIGNED
    doc.signed_by = current_user.id
    doc.signed_at = datetime.now(timezone.utc)

    write_audit(db, "LegalDocument", doc.id, "SIGN",
                changed_by=current_user.id, ip_address=get_client_ip(request),
                before=before, after=model_to_dict(doc))
    db.commit()
    db.refresh(doc)
    return doc


# ─── Reject ───────────────────────────────────────────────────────────────────

@router.post("/{doc_id}/reject", response_model=LegalDocResponse,
             summary="Reject a submitted document (MD/PM only)")
def reject_document(
    doc_id:       int,
    request:      Request,
    payload:      LegalDocRejectRequest,
    current_user: Annotated[User, Depends(require_role(*_SIGN_ROLES))],
    db:           Annotated[Session, Depends(get_db)],
):
    doc = db.query(LegalDocument).filter(LegalDocument.id == doc_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    if doc.status != DocStatus.SUBMITTED:
        raise HTTPException(status_code=400, detail="Only submitted documents can be rejected")

    before = model_to_dict(doc)
    doc.status         = DocStatus.REJECTED
    doc.rejection_note = payload.note

    write_audit(db, "LegalDocument", doc.id, "REJECT",
                changed_by=current_user.id, ip_address=get_client_ip(request),
                before=before, after=model_to_dict(doc))
    db.commit()
    db.refresh(doc)
    return doc


# ─── Delete (draft/rejected, own doc or admin) ─────────────────────────────────

@router.delete("/{doc_id}", response_model=MessageResponse, summary="Delete a draft or rejected document")
def delete_document(
    doc_id:       int,
    request:      Request,
    current_user: Annotated[User, Depends(get_current_user)],
    db:           Annotated[Session, Depends(get_db)],
):
    doc = db.query(LegalDocument).filter(LegalDocument.id == doc_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    if doc.status not in (DocStatus.DRAFT, DocStatus.REJECTED):
        raise HTTPException(status_code=400, detail="Only draft or rejected documents can be deleted")
    if doc.created_by != current_user.id and current_user.role.name not in (RoleName.SUPER_ADMIN, RoleName.MD):
        raise HTTPException(status_code=403, detail="Not authorized")

    write_audit(db, "LegalDocument", doc.id, "DELETE",
                changed_by=current_user.id, ip_address=get_client_ip(request),
                before=model_to_dict(doc))
    db.delete(doc)
    db.commit()
    return MessageResponse(message=f"Document {doc.doc_number} deleted")


# ─── Download PDF ─────────────────────────────────────────────────────────────

@router.get("/{doc_id}/pdf", summary="Download the document as PDF")
def download_pdf(
    doc_id:       int,
    current_user: Annotated[User, Depends(get_current_user)],
    db:           Annotated[Session, Depends(get_db)],
):
    doc = db.query(LegalDocument).filter(LegalDocument.id == doc_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    signer_name  = doc.signer.full_name  if doc.signer  else None
    signer_title = None
    if doc.signer:
        role_titles = {
            RoleName.MD:          "Managing Director",
            RoleName.PM:          "Project Manager",
            RoleName.SUPER_ADMIN: "Super Admin",
        }
        signer_title = role_titles.get(doc.signer.role.name, doc.signer.role.name.value)

    pdf_bytes = generate_document_pdf(
        doc_number        = doc.doc_number or "-",
        doc_type          = doc.doc_type.value,
        title             = doc.title,
        subject           = doc.subject,
        body              = doc.body,
        recipient_name    = doc.recipient_name,
        recipient_company = doc.recipient_company,
        recipient_address = doc.recipient_address,
        closing           = doc.closing,
        quoted_amount     = doc.quoted_amount,
        creator_name      = doc.creator.full_name,
        signer_name       = signer_name,
        signer_title      = signer_title,
        signed_at         = doc.signed_at,
        created_at        = doc.created_at,
    )

    safe_number = (doc.doc_number or f"doc-{doc.id}").replace("/", "-")
    filename    = f"{safe_number}.pdf"

    return Response(
        content      = pdf_bytes,
        media_type   = "application/pdf",
        headers      = {"Content-Disposition": f'attachment; filename="{filename}"'},
    )

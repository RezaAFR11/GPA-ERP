"""
GPA-ERP V5.0 — Audit service
Provides a single write_audit() helper used by all routers.
The AuditLog table is append-only; rows are never updated or deleted.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models import AuditLog


_SENSITIVE_COLUMNS = {"hashed_password"}


def _serialize(obj: Any) -> Any:
    """Recursively make an object JSON-safe."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_serialize(i) for i in obj]
    # Decimal / date / datetime / enum
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    if hasattr(obj, "value"):       # Enum
        return obj.value
    if hasattr(obj, "__str__"):
        return str(obj)
    return obj


def model_to_dict(instance: Any) -> dict:
    """Convert a SQLAlchemy model instance to a plain dict (columns only)."""
    return {
        col.key: _serialize(getattr(instance, col.key))
        for col in instance.__table__.columns
        if col.key not in _SENSITIVE_COLUMNS
    }


def write_audit(
    db:           Session,
    entity_type:  str,
    entity_id:    int,
    action:       str,
    changed_by:   int | None = None,
    ip_address:   str | None = None,
    before:       dict | None = None,
    after:        dict | None = None,
) -> AuditLog:
    """
    Append one audit record.  Call this *inside* the same transaction as the
    mutation so that both commit or rollback together.
    """
    log = AuditLog(
        entity_type  = entity_type,
        entity_id    = entity_id,
        action       = action,
        before_state = before,
        after_state  = after,
        changed_by   = changed_by,
        ip_address   = ip_address,
    )
    db.add(log)
    return log

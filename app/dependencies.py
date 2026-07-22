"""
GPA-ERP V5.0 — FastAPI dependencies
Handles JWT auth, role-based access control, and the approval matrix resolver.
"""
from __future__ import annotations

from decimal import Decimal
from functools import lru_cache
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer, OAuth2PasswordBearer
import jwt
from jwt import InvalidTokenError
from passlib.context import CryptContext
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import ApprovalRule, CostCodeCategory, RoleName, User, effective_roles

settings = get_settings()

# ─── Password hashing ────────────────────────────────────────────────────────

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(plain: str) -> str:
    return pwd_context.hash(plain)


# Alias used in some routers
get_password_hash = hash_password


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


# ─── JWT ─────────────────────────────────────────────────────────────────────

# Kept for OpenAPI /docs "Authorize" button compatibility
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)
# Optional Bearer extractor (does not auto-raise 401)
_http_bearer = HTTPBearer(auto_error=False)


def create_access_token(data: dict) -> tuple[str, int]:
    """Returns (encoded_jwt, expires_in_seconds)."""
    from datetime import datetime, timedelta, timezone
    expire_seconds = settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60
    payload = data.copy()
    payload["exp"] = datetime.now(timezone.utc) + timedelta(seconds=expire_seconds)
    token = jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return token, expire_seconds


# ─── Current user ────────────────────────────────────────────────────────────

def get_current_user(
    request:     Request,
    db:          Annotated[Session, Depends(get_db)],
    bearer:      Annotated[HTTPAuthorizationCredentials | None, Depends(_http_bearer)] = None,
) -> User:
    """
    Resolves the current user from two token sources (in priority order):
    1. httpOnly cookie ``access_token`` — set by POST /api/auth/login for browser clients.
    2. ``Authorization: Bearer <token>`` header — for API / mobile clients.
    """
    credentials_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    # 1. Try cookie first
    token: str | None = request.cookies.get("access_token")

    # 2. Fall back to Authorization: Bearer header
    if not token and bearer is not None:
        token = bearer.credentials

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        user_id: str | None = payload.get("sub")
        if user_id is None:
            raise credentials_exc
        token_version = int(payload.get("ver", 0))
        parsed_user_id = int(user_id)
    except (InvalidTokenError, TypeError, ValueError):
        raise credentials_exc

    user = db.query(User).filter(User.id == parsed_user_id).first()
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive",
        )
    if token_version != user.token_version:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session has expired. Please sign in again.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    password_change_allowlist = {
        "/api/auth/me",
        "/api/auth/menu-permissions",
        "/api/users/me/password",
    }
    if user.must_change_password and request.url.path not in password_change_allowlist:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Password change required before accessing this resource",
        )
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


# ─── Role guards ─────────────────────────────────────────────────────────────

def require_role(*roles: RoleName):
    """
    Factory that returns a dependency checking the current user's role.
    Usage: Depends(require_role(RoleName.PM, RoleName.SUPER_ADMIN))
    """
    def _check(current_user: CurrentUser) -> User:
        if not any(r in roles for r in effective_roles(current_user.role.name)):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires one of: {[r.value for r in roles]}",
            )
        return current_user
    return _check


def super_admin_only(current_user: CurrentUser) -> User:
    if current_user.role.name != RoleName.SUPER_ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This endpoint is restricted to Super Admins.",
        )
    return current_user


SuperAdminUser = Annotated[User, Depends(super_admin_only)]


# ─── Approval matrix resolver ────────────────────────────────────────────────

def get_required_approvers_from_matrix(
    db:                   Session,
    amount:               Decimal,
    cost_code_category:   CostCodeCategory,
) -> list[str]:
    """
    Query the ApprovalRule table and return an ordered list of role names
    that must approve an expense of `amount` in `cost_code_category`.

    Rules are ordered by `priority` (ascending).
    A rule with null `cost_code_category` matches any category.
    A rule with null `max_amount` has no upper bound.
    """
    rules = (
        db.query(ApprovalRule)
        .filter(
            ApprovalRule.is_active == True,
            ApprovalRule.min_amount <= amount,
            or_(
                ApprovalRule.max_amount >= amount,
                ApprovalRule.max_amount.is_(None),
            ),
            or_(
                ApprovalRule.cost_code_category == cost_code_category,
                ApprovalRule.cost_code_category.is_(None),
            ),
        )
        .order_by(ApprovalRule.priority)
        .all()
    )

    # De-duplicate while preserving order
    seen: set[str] = set()
    chain: list[str] = []
    for rule in rules:
        role_val = rule.required_role.value
        if role_val not in seen:
            seen.add(role_val)
            chain.append(role_val)

    return chain


# ─── Client IP helper ────────────────────────────────────────────────────────

def get_client_ip(request: Request) -> str:
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.client.host if request.client else "unknown"

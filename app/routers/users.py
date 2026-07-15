import secrets
import string
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.audit import model_to_dict, write_audit
from app.config import get_settings
from app.database import get_db
from app.dependencies import (
    CurrentUser, create_access_token, get_client_ip, hash_password, verify_password,
    require_role, super_admin_only,
)
from app.menu_permissions import reset_user_menu_permissions_for_role, seed_user_menu_permissions
from app.models import Role, RoleName, User
from app.schemas import (
    MessageResponse, PasswordChange, PasswordChangeResponse, PasswordResetResponse,
    UserCreate, UserListSummary, UserResponse, UserSelfUpdate, UserUpdate,
)

router = APIRouter(prefix="/users", tags=["Users"])

_manage_roles = (RoleName.SUPER_ADMIN, RoleName.MD)
settings = get_settings()


def _assert_can_manage_target(current_user: User, target: User) -> None:
    if (
        current_user.role.name == RoleName.MD
        and target.role.name == RoleName.SUPER_ADMIN
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Managing a Super Admin account requires Super Admin access",
        )


def _active_super_admin_count(db: Session) -> int:
    return (
        db.query(func.count(User.id))
        .join(User.role)
        .filter(Role.name == RoleName.SUPER_ADMIN, User.is_active == True)
        .scalar()
        or 0
    )


def _assert_not_last_active_super_admin(db: Session, target: User) -> None:
    if (
        target.role.name == RoleName.SUPER_ADMIN
        and target.is_active
        and _active_super_admin_count(db) <= 1
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The last active Super Admin cannot be demoted or deactivated",
        )


def _generate_password(length: int = 12) -> str:
    """Generate a random temp password: at least one uppercase, one digit, one symbol."""
    alphabet = string.ascii_letters + string.digits + "!@#$"
    while True:
        pw = "".join(secrets.choice(alphabet) for _ in range(length))
        if (any(c.isupper() for c in pw)
                and any(c.isdigit() for c in pw)
                and any(c in "!@#$" for c in pw)):
            return pw


@router.get("/roles", summary="List all roles")
def list_roles(
    _:  Annotated[User, Depends(require_role(*_manage_roles))],
    db: Annotated[Session, Depends(get_db)],
):
    return db.query(Role).order_by(Role.id).all()


@router.get("", response_model=list[UserResponse], summary="List all users")
def list_users(
    current_user: Annotated[User, Depends(require_role(*_manage_roles))],
    db:           Annotated[Session, Depends(get_db)],
    skip: int = 0,
    limit: int = 100,
):
    return db.query(User).order_by(User.id).offset(skip).limit(limit).all()


@router.get("/summary", response_model=UserListSummary, summary="User account totals")
def user_list_summary(
    current_user: Annotated[User, Depends(require_role(*_manage_roles))],
    db:           Annotated[Session, Depends(get_db)],
):
    total = db.query(func.count(User.id)).scalar() or 0
    active = db.query(func.count(User.id)).filter(User.is_active == True).scalar() or 0
    return UserListSummary(total=total, active=active, inactive=total - active)


@router.post("", response_model=UserResponse, status_code=status.HTTP_201_CREATED,
             summary="Create a new user")
def create_user(
    request:      Request,
    payload:      UserCreate,
    current_user: Annotated[User, Depends(require_role(*_manage_roles))],
    db:           Annotated[Session, Depends(get_db)],
):
    if db.query(User).filter(User.email == payload.email.lower()).first():
        raise HTTPException(status_code=409, detail="Email already registered")

    role = db.query(Role).filter(Role.id == payload.role_id).first()
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")

    # Only SUPER_ADMIN can create another SUPER_ADMIN
    if role.name == RoleName.SUPER_ADMIN and current_user.role.name != RoleName.SUPER_ADMIN:
        raise HTTPException(status_code=403, detail="Only Super Admin can create Super Admin accounts")

    user = User(
        email=payload.email.lower(),
        hashed_password=hash_password(payload.password),
        full_name=payload.full_name,
        role_id=payload.role_id,
        role=role,
    )
    db.add(user)
    db.flush()
    seed_user_menu_permissions(db, user, commit=False)

    write_audit(db, "User", user.id, "CREATE",
                changed_by=current_user.id, ip_address=get_client_ip(request),
                after=model_to_dict(user))
    db.commit()
    db.refresh(user)
    return user


@router.patch("/me", response_model=UserResponse, summary="Update own profile")
def update_me(
    request:      Request,
    payload:      UserSelfUpdate,
    current_user: CurrentUser,
    db:           Annotated[Session, Depends(get_db)],
):
    before = model_to_dict(current_user)
    current_user.full_name = payload.full_name
    write_audit(db, "User", current_user.id, "UPDATE",
                changed_by=current_user.id, ip_address=get_client_ip(request),
                before=before, after=model_to_dict(current_user))
    db.commit()
    db.refresh(current_user)
    return current_user


@router.patch("/me/password", response_model=PasswordChangeResponse, summary="Change own password")
def change_my_password(
    request:      Request,
    response:     Response,
    payload:      PasswordChange,
    current_user: CurrentUser,
    db:           Annotated[Session, Depends(get_db)],
):
    if not verify_password(payload.current_password, current_user.hashed_password):
        raise HTTPException(status_code=400, detail="Password saat ini tidak sesuai")

    before = model_to_dict(current_user)
    current_user.hashed_password = hash_password(payload.new_password)
    current_user.must_change_password = False
    current_user.token_version += 1
    write_audit(db, "User", current_user.id, "PASSWORD_CHANGE",
                changed_by=current_user.id, ip_address=get_client_ip(request),
                before=before, after=model_to_dict(current_user))
    db.commit()

    token, expires_in = create_access_token({
        "sub": str(current_user.id),
        "role": current_user.role.name.value,
        "ver": current_user.token_version,
    })
    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        secure=not settings.DEBUG,
        samesite=settings.SESSION_COOKIE_SAMESITE,
        max_age=expires_in,
        path="/",
    )
    return PasswordChangeResponse(
        message="Password berhasil diubah",
        access_token=token,
        expires_in=expires_in,
    )


@router.post("/{user_id}/reset-password", response_model=PasswordResetResponse,
             summary="Admin: reset a user's password to a temporary one")
def reset_user_password(
    user_id:      int,
    request:      Request,
    current_user: Annotated[User, Depends(require_role(*_manage_roles))],
    db:           Annotated[Session, Depends(get_db)],
):
    """Generate a new temporary password for a user and flag them to change it on
    next login. The temp password is returned once to the admin to hand off."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    _assert_can_manage_target(current_user, user)
    if user.id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Use Change Password to update your own password",
        )

    temp_password = _generate_password()
    before = model_to_dict(user)
    user.hashed_password = hash_password(temp_password)
    user.must_change_password = True
    user.token_version += 1
    write_audit(db, "User", user.id, "PASSWORD_RESET",
                changed_by=current_user.id, ip_address=get_client_ip(request),
                before=before, after=model_to_dict(user))
    db.commit()
    return PasswordResetResponse(
        message=f"Password reset for {user.full_name}. Share the temporary password securely.",
        temp_password=temp_password,
    )


@router.get("/{user_id}", response_model=UserResponse)
def get_user(
    user_id:      int,
    current_user: Annotated[User, Depends(require_role(*_manage_roles))],
    db:           Annotated[Session, Depends(get_db)],
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@router.patch("/{user_id}", response_model=UserResponse)
def update_user(
    user_id:      int,
    request:      Request,
    payload:      UserUpdate,
    current_user: Annotated[User, Depends(require_role(*_manage_roles))],
    db:           Annotated[Session, Depends(get_db)],
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    _assert_can_manage_target(current_user, user)

    updates = {
        field: value
        for field, value in payload.model_dump(exclude_unset=True).items()
        if value is not None
    }

    # Only SUPER_ADMIN may change roles
    if "role_id" in updates and current_user.role.name != RoleName.SUPER_ADMIN:
        raise HTTPException(status_code=403, detail="Only Super Admin can change user roles")

    # Validate new role exists
    new_role: Role | None = None
    if "role_id" in updates and updates["role_id"] is not None:
        new_role = db.query(Role).filter(Role.id == updates["role_id"]).first()
        if not new_role:
            raise HTTPException(status_code=404, detail="Role not found")
        if user.id == current_user.id and new_role.name != RoleName.SUPER_ADMIN:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="You cannot remove your own Super Admin role",
            )

    role_changed = new_role is not None and new_role.id != user.role_id
    active_changed = "is_active" in updates and updates["is_active"] != user.is_active

    if active_changed and not updates["is_active"] and user.id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You cannot deactivate your own account",
        )
    if (
        (role_changed and new_role is not None and new_role.name != RoleName.SUPER_ADMIN)
        or (active_changed and not updates["is_active"])
    ):
        _assert_not_last_active_super_admin(db, user)

    before = model_to_dict(user)
    for field, value in updates.items():
        if field == "role_id":
            continue
        setattr(user, field, value)

    if role_changed and new_role is not None:
        user.role = new_role
        user.role_id = new_role.id
        db.flush()
        reset_user_menu_permissions_for_role(db, user)

    if role_changed or active_changed:
        user.token_version += 1

    write_audit(db, "User", user.id, "UPDATE",
                changed_by=current_user.id, ip_address=get_client_ip(request),
                before=before, after=model_to_dict(user))
    db.commit()
    db.refresh(user)
    return user


@router.delete("/{user_id}", response_model=MessageResponse)
def deactivate_user(
    user_id:      int,
    request:      Request,
    current_user: Annotated[User, Depends(super_admin_only)],
    db:           Annotated[Session, Depends(get_db)],
):
    """Soft-delete: sets is_active=False. Hard delete is not permitted."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.id == current_user.id:
        raise HTTPException(status_code=400, detail="Cannot deactivate your own account")
    _assert_not_last_active_super_admin(db, user)

    before = model_to_dict(user)
    user.is_active = False
    user.token_version += 1
    write_audit(db, "User", user.id, "DEACTIVATE",
                changed_by=current_user.id, ip_address=get_client_ip(request),
                before=before, after=model_to_dict(user))
    db.commit()
    return MessageResponse(message=f"User {user.email} deactivated")

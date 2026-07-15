from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.audit import write_audit
from app.config import get_settings
from app.database import get_db
from app.dependencies import (
    create_access_token, get_client_ip, get_current_user, verify_password,
)
from app.menu_permissions import ensure_default_menus, menu_access_keys_for_user
from app.models import AppMenu, User
from app.schemas import TokenResponse, UserResponse

router = APIRouter(prefix="/auth", tags=["Auth"])
settings = get_settings()


@router.post("/login", response_model=TokenResponse, summary="Obtain JWT access token")
def login(
    request:  Request,
    response: Response,
    form:     Annotated[OAuth2PasswordRequestForm, Depends()],
    db:       Annotated[Session, Depends(get_db)],
):
    user = db.query(User).filter(User.email == form.username.lower()).first()
    if not user or not verify_password(form.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account is disabled")

    token, expires_in = create_access_token({
        "sub": str(user.id),
        "role": user.role.name.value,
        "ver": user.token_version,
    })

    # Set httpOnly cookie (works for browser clients; Bearer header fallback kept for API/mobile)
    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        secure=not settings.DEBUG,  # False in dev (HTTP), True in prod (HTTPS)
        samesite=settings.SESSION_COOKIE_SAMESITE,
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        path="/",
    )

    write_audit(
        db, "User", user.id, "LOGIN",
        changed_by=user.id,
        ip_address=get_client_ip(request),
    )
    db.commit()
    return TokenResponse(access_token=token, expires_in=expires_in)


@router.post("/logout", summary="Clear auth cookie and log out")
def logout(response: Response):
    response.delete_cookie(
        "access_token",
        path="/",
        secure=not settings.DEBUG,
        samesite=settings.SESSION_COOKIE_SAMESITE,
    )
    return {"detail": "Logged out"}


@router.get("/me", response_model=UserResponse, summary="Current user profile")
def me(current_user: Annotated[User, Depends(get_current_user)]):
    return current_user


@router.get("/menu-permissions", summary="Current user's allowed menus")
def menu_permissions(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    ensure_default_menus(db)
    allowed_keys = menu_access_keys_for_user(db, current_user)
    menus = (
        db.query(AppMenu)
        .filter(AppMenu.is_active == True)
        .order_by(AppMenu.section, AppMenu.sort_order, AppMenu.label)
        .all()
    )
    return {
        "allowed_keys": sorted(allowed_keys),
        "menus": [
            {
                "key": menu.key,
                "label": menu.label,
                "section": menu.section,
                "path": menu.path,
                "description": menu.description,
                "sort_order": menu.sort_order,
                "can_access": menu.key in allowed_keys,
            }
            for menu in menus
        ],
    }

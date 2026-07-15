import asyncio
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from starlette.requests import Request

from app.audit import model_to_dict
from app.dependencies import create_access_token, get_current_user
from app.menu_permissions import reset_user_menu_permissions_for_role
from app.models import AppMenu, Role, RoleName, User, UserMenuPermission
from app.notify_channels import build_notification_email
from app.routers.users import _assert_can_manage_target, _assert_not_last_active_super_admin


def make_user(user_id: int, role_name: RoleName, **overrides) -> User:
    role = Role(id=user_id, name=role_name)
    values = {
        "id": user_id,
        "email": f"user{user_id}@example.com",
        "hashed_password": "secret-hash",
        "full_name": f"User {user_id}",
        "role_id": user_id,
        "role": role,
        "is_active": True,
        "must_change_password": False,
        "token_version": 0,
    }
    values.update(overrides)
    return User(**values)


def make_request(path: str) -> Request:
    return Request({
        "type": "http",
        "method": "GET",
        "path": path,
        "headers": [],
        "query_string": b"",
        "scheme": "http",
        "server": ("testserver", 80),
        "client": ("127.0.0.1", 1234),
    })


def auth_db_for(user: User) -> MagicMock:
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = user
    return db


def test_md_cannot_manage_super_admin() -> None:
    md = make_user(1, RoleName.MD)
    super_admin = make_user(2, RoleName.SUPER_ADMIN)

    with pytest.raises(HTTPException) as exc:
        _assert_can_manage_target(md, super_admin)

    assert exc.value.status_code == 403


def test_last_active_super_admin_is_protected() -> None:
    super_admin = make_user(1, RoleName.SUPER_ADMIN)
    db = MagicMock()
    db.query.return_value.join.return_value.filter.return_value.scalar.return_value = 1

    with pytest.raises(HTTPException) as exc:
        _assert_not_last_active_super_admin(db, super_admin)

    assert exc.value.status_code == 409


def test_role_permission_reset_replaces_old_permissions() -> None:
    user = make_user(1, RoleName.STAFF)
    settings_menu = AppMenu(id=10, key="settings", label="Settings", section="System")
    dashboard_menu = AppMenu(id=11, key="dashboard", label="Dashboard", section="Workspace")
    delete_query = MagicMock()
    menu_query = MagicMock()
    menu_query.filter.return_value.all.return_value = [settings_menu, dashboard_menu]
    db = MagicMock()
    db.query.side_effect = [delete_query, menu_query]

    reset_user_menu_permissions_for_role(db, user)

    delete_query.filter.return_value.delete.assert_called_once_with(synchronize_session=False)
    added = [call.args[0] for call in db.add.call_args_list]
    assert len(added) == 1
    assert isinstance(added[0], UserMenuPermission)
    assert added[0].menu_id == settings_menu.id


def test_token_version_invalidates_existing_session() -> None:
    user = make_user(1, RoleName.STAFF, token_version=2)
    token, _ = create_access_token({"sub": "1", "role": "STAFF", "ver": 1})
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(get_current_user(make_request("/api/users/me"), auth_db_for(user), credentials))

    assert exc.value.status_code == 401


def test_must_change_password_is_enforced_by_backend() -> None:
    user = make_user(1, RoleName.STAFF, must_change_password=True)
    token, _ = create_access_token({"sub": "1", "role": "STAFF", "ver": 0})
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(get_current_user(make_request("/api/projects"), auth_db_for(user), credentials))
    assert exc.value.status_code == 403

    resolved = asyncio.run(
        get_current_user(make_request("/api/users/me/password"), auth_db_for(user), credentials)
    )
    assert resolved.id == user.id


def test_audit_serialization_excludes_password_hash() -> None:
    serialized = model_to_dict(make_user(1, RoleName.SUPER_ADMIN))
    assert "hashed_password" not in serialized


def test_email_links_use_configured_frontend_and_escape_content() -> None:
    html, text = build_notification_email(
        "Review <invoice>",
        "Open & approve",
        "/action-center",
        base_url="https://erp.example.com/",
    )

    assert "https://erp.example.com/action-center" in html
    assert "Review &lt;invoice&gt;" in html
    assert "Open &amp; approve" in html
    assert "https://erp.example.com/action-center" in text

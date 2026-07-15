from __future__ import annotations

import time
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from html import escape
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
import jwt
from jwt import InvalidTokenError
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.audit import model_to_dict, write_audit
from app.config import get_settings
from app.database import get_db
from app.dependencies import create_access_token, get_client_ip, verify_password
from app.menu_permissions import DEFAULT_MENUS, OBSOLETE_MENU_KEYS, ROLE_PRESETS, ensure_default_menus
from app.models import (
    AccountReceivable,
    AppMenu,
    ApprovalRule,
    AuditLog,
    CostCodeCategory,
    Expense,
    InventoryItem,
    LegalDocument,
    PettyCashReport,
    Project,
    RoleName,
    User,
    UserMenuPermission,
)

router = APIRouter(prefix="/admin", tags=["Backend Admin"])
settings = get_settings()
ADMIN_COOKIE = "gpa_backend_admin"

# ── Brute-force protection (in-memory, per IP) ────────────────────────────────
_FAIL_WINDOW   = 300   # 5-minute rolling window
_MAX_FAILURES  = 5     # max failed attempts before lockout
_LOCKOUT_SECS  = 900   # 15-minute lockout
_login_attempts: dict[str, list[float]] = defaultdict(list)

def _check_login_rate(ip: str) -> None:
    now = time.time()
    attempts = _login_attempts[ip]
    # Drop attempts outside the rolling window
    _login_attempts[ip] = [t for t in attempts if now - t < _FAIL_WINDOW]
    if len(_login_attempts[ip]) >= _MAX_FAILURES:
        remaining = int(_LOCKOUT_SECS - (now - _login_attempts[ip][0]))
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"Too many failed login attempts. Try again in {remaining // 60 + 1} minute(s).",
        )

def _record_failure(ip: str) -> None:
    _login_attempts[ip].append(time.time())

def _clear_failures(ip: str) -> None:
    _login_attempts.pop(ip, None)


def _redirect(path: str, status_code: int = status.HTTP_303_SEE_OTHER) -> RedirectResponse:
    return RedirectResponse(path, status_code=status_code)


def _money(value: Decimal | None) -> str:
    if value is None:
        return ""
    return f"{value:,.0f}"


def _parse_decimal(raw: str | None, *, default: Decimal | None = None) -> Decimal | None:
    if raw is None or raw.strip() == "":
        return default
    normalized = raw.replace("Rp", "").replace(",", "").strip()
    try:
        return Decimal(normalized)
    except InvalidOperation as exc:
        raise HTTPException(status_code=400, detail=f"Invalid amount: {raw}") from exc


def _get_admin_user(
    token: Annotated[str | None, Cookie(alias=ADMIN_COOKIE)] = None,
    db: Annotated[Session, Depends(get_db)] = None,
) -> User:
    login_redirect = HTTPException(
        status_code=status.HTTP_303_SEE_OTHER,
        detail="Login required",
        headers={"Location": "/admin/login"},
    )
    if not token:
        raise login_redirect
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        user_id = int(payload.get("sub"))
        token_version = int(payload.get("ver", 0))
    except (InvalidTokenError, TypeError, ValueError) as exc:
        raise login_redirect from exc

    user = db.query(User).filter(User.id == user_id).first()
    if not user or not user.is_active:
        raise login_redirect
    if user.role.name != RoleName.SUPER_ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Super Admin only")
    if token_version != user.token_version:
        raise login_redirect
    return user


AdminUser = Annotated[User, Depends(_get_admin_user)]


def _page(title: str, body: str, user: User | None = None) -> HTMLResponse:
    active_matrix = "active" if "Matrix" in title or "Approval" in title else ""
    active_menu   = "active" if "Menu" in title else ""
    active_docs   = ""

    sidebar = ""
    topbar_right = ""
    if user:
        sidebar = f"""
        <aside class="sidebar">
          <div class="sidebar-logo">
            <div class="logo-mark">G</div>
            <div>
              <div class="logo-name">GPA ERP</div>
              <div class="logo-sub">Backend Admin</div>
            </div>
          </div>
          <nav class="sidebar-nav">
            <div class="nav-section-label">Configuration</div>
            <a href="/admin/approval-matrix" class="nav-item {active_matrix}">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/></svg>
              Approval Matrix
            </a>
            <a href="/admin/menu-access" class="nav-item {active_menu}">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/></svg>
              Menu Access
            </a>
            <div class="nav-section-label" style="margin-top:16px">Developer</div>
            <a href="/docs" target="_blank" class="nav-item {active_docs}">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
              API Docs
            </a>
            <a href="/health" target="_blank" class="nav-item">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></svg>
              Health Check
            </a>
          </nav>
          <div class="sidebar-footer">
            <div class="sidebar-user">
              <div class="user-avatar">{escape(user.full_name[:1].upper())}</div>
              <div>
                <div class="user-name">{escape(user.full_name)}</div>
                <div class="user-role">Super Admin</div>
              </div>
            </div>
            <a href="/admin/logout" class="logout-btn">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/></svg>
              Logout
            </a>
          </div>
        </aside>"""
        topbar_right = f'<span class="topbar-title">{escape(title)}</span>'
    else:
        topbar_right = '<span class="topbar-title">GPA ERP — Backend Admin</span>'

    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(title)} — GPA Admin</title>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    :root {{
      --sidebar-w: 228px;
      --bg: #F8FAFC;
      --panel: #FFFFFF;
      --border: #E2E8F0;
      --border-light: #F1F5F9;
      --ink: #0F172A;
      --ink-2: #334155;
      --muted: #64748B;
      --muted-2: #94A3B8;
      --brand: #2563EB;
      --brand-bg: #EFF6FF;
      --ok: #16A34A;
      --ok-bg: #F0FDF4;
      --danger: #DC2626;
      --danger-bg: #FEF2F2;
      --warn: #D97706;
      --warn-bg: #FFFBEB;
      --sidebar-bg: #0F172A;
      --sidebar-hover: rgba(255,255,255,.07);
      --sidebar-active: rgba(255,255,255,.12);
      --shadow-sm: 0 1px 2px rgba(15,23,42,.06);
      --shadow: 0 1px 3px rgba(15,23,42,.08), 0 1px 2px rgba(15,23,42,.04);
      --radius: 8px;
    }}
    body {{ font-family: "Inter", "Segoe UI", system-ui, Arial, sans-serif; font-size: 14px; background: var(--bg); color: var(--ink); line-height: 1.5; }}
    a {{ color: var(--brand); text-decoration: none; }}

    /* ── Layout ── */
    .layout {{ display: flex; min-height: 100vh; }}
    .sidebar {{
      width: var(--sidebar-w); flex-shrink: 0; background: var(--sidebar-bg);
      display: flex; flex-direction: column; position: fixed; inset: 0 auto 0 0; z-index: 40;
    }}
    .main-wrap {{ margin-left: var(--sidebar-w); flex: 1; display: flex; flex-direction: column; min-height: 100vh; }}
    .topbar {{
      position: sticky; top: 0; z-index: 30; background: var(--panel);
      border-bottom: 1px solid var(--border); padding: 0 28px;
      height: 52px; display: flex; align-items: center; gap: 12px;
      box-shadow: var(--shadow-sm);
    }}
    .topbar-title {{ font-size: 15px; font-weight: 600; color: var(--ink-2); }}
    .topbar-pill {{ font-size: 11px; font-weight: 700; color: var(--ok); background: var(--ok-bg); padding: 3px 8px; border-radius: 999px; letter-spacing: .03em; }}
    .content {{ padding: 28px; flex: 1; }}

    /* ── Sidebar ── */
    .sidebar-logo {{
      display: flex; align-items: center; gap: 10px;
      padding: 20px 16px 16px; border-bottom: 1px solid rgba(255,255,255,.08);
    }}
    .logo-mark {{
      width: 34px; height: 34px; border-radius: 8px; background: var(--brand);
      display: flex; align-items: center; justify-content: center;
      font-weight: 800; font-size: 16px; color: white; flex-shrink: 0;
    }}
    .logo-name {{ font-size: 14px; font-weight: 700; color: white; line-height: 1.2; }}
    .logo-sub {{ font-size: 10px; color: #64748B; letter-spacing: .06em; text-transform: uppercase; }}
    .sidebar-nav {{ flex: 1; padding: 14px 10px; overflow-y: auto; }}
    .nav-section-label {{
      font-size: 10px; font-weight: 700; color: #475569; letter-spacing: .1em;
      text-transform: uppercase; padding: 0 8px 6px;
    }}
    .nav-item {{
      display: flex; align-items: center; gap: 9px; padding: 8px 10px; border-radius: 6px;
      color: #94A3B8; font-size: 13px; font-weight: 500; margin-bottom: 2px;
      transition: background .15s, color .15s; text-decoration: none;
    }}
    .nav-item:hover {{ background: var(--sidebar-hover); color: #E2E8F0; }}
    .nav-item.active {{ background: var(--sidebar-active); color: white; font-weight: 600; }}
    .sidebar-footer {{
      padding: 12px 10px; border-top: 1px solid rgba(255,255,255,.08);
      display: flex; align-items: center; justify-content: space-between; gap: 8px;
    }}
    .sidebar-user {{ display: flex; align-items: center; gap: 8px; min-width: 0; }}
    .user-avatar {{
      width: 30px; height: 30px; border-radius: 50%; background: var(--brand);
      display: flex; align-items: center; justify-content: center; color: white;
      font-size: 13px; font-weight: 700; flex-shrink: 0;
    }}
    .user-name {{ font-size: 12px; font-weight: 600; color: #E2E8F0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 110px; }}
    .user-role {{ font-size: 10px; color: #64748B; }}
    .logout-btn {{
      display: flex; align-items: center; gap: 5px; padding: 6px 9px; border-radius: 6px;
      color: #64748B; font-size: 12px; font-weight: 500; flex-shrink: 0;
      transition: background .15s, color .15s;
    }}
    .logout-btn:hover {{ background: rgba(220,38,38,.15); color: #F87171; }}

    /* ── Metrics grid ── */
    .metrics {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; margin-bottom: 24px; }}
    @media (min-width: 1200px) {{ .metrics {{ grid-template-columns: repeat(5, 1fr); }} }}
    .metric-card {{
      background: var(--panel); border: 1px solid var(--border); border-radius: var(--radius);
      padding: 16px 18px; box-shadow: var(--shadow);
    }}
    .metric-label {{ font-size: 11px; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; color: var(--muted); }}
    .metric-value {{ font-size: 28px; font-weight: 800; color: var(--ink); margin-top: 6px; letter-spacing: -.02em; font-variant-numeric: tabular-nums; }}

    /* ── Section heading ── */
    .section-head {{
      display: flex; align-items: center; justify-content: space-between; gap: 16px;
      margin: 28px 0 14px;
    }}
    .section-head:first-child {{ margin-top: 0; }}
    .section-head h2 {{ font-size: 18px; font-weight: 700; color: var(--ink); }}
    .section-head p {{ font-size: 13px; color: var(--muted); margin-top: 2px; }}
    .section-links {{ display: flex; gap: 8px; flex-shrink: 0; }}

    /* ── Card ── */
    .card {{
      background: var(--panel); border: 1px solid var(--border); border-radius: var(--radius);
      padding: 20px 22px; box-shadow: var(--shadow); margin-bottom: 16px;
    }}
    .card h3 {{ font-size: 14px; font-weight: 700; margin-bottom: 12px; color: var(--ink); }}

    /* ── Table ── */
    .table-wrap {{ background: var(--panel); border: 1px solid var(--border); border-radius: var(--radius); box-shadow: var(--shadow); overflow: hidden; margin-bottom: 16px; }}
    table {{ width: 100%; border-collapse: collapse; }}
    thead {{ background: #F8FAFC; border-bottom: 1px solid var(--border); }}
    th {{
      padding: 10px 14px; font-size: 11px; font-weight: 700; letter-spacing: .07em;
      text-transform: uppercase; color: var(--muted); text-align: left; white-space: nowrap;
    }}
    td {{ padding: 11px 14px; vertical-align: middle; border-bottom: 1px solid var(--border-light); font-size: 13px; color: var(--ink-2); }}
    tr:last-child td {{ border-bottom: none; }}
    tbody tr:hover td {{ background: #FAFBFD; }}

    /* ── Inputs ── */
    input:not([type="checkbox"]), select {{
      width: 100%; padding: 7px 10px; border: 1px solid var(--border);
      border-radius: 6px; font: inherit; font-size: 13px; background: var(--panel); color: var(--ink);
      transition: border-color .15s, box-shadow .15s; min-height: 34px;
    }}
    input:not([type="checkbox"]):focus, select:focus {{
      outline: none; border-color: var(--brand); box-shadow: 0 0 0 3px rgba(37,99,235,.12);
    }}
    input[type="checkbox"] {{ width: 16px; height: 16px; accent-color: var(--brand); cursor: pointer; }}
    label {{ display: block; font-size: 12px; font-weight: 600; color: var(--muted); margin-bottom: 5px; }}

    /* ── Buttons ── */
    button, .btn {{
      display: inline-flex; align-items: center; gap: 6px; border: none;
      border-radius: 6px; padding: 7px 13px; font: inherit; font-size: 13px;
      font-weight: 600; cursor: pointer; transition: opacity .15s, box-shadow .15s;
      background: var(--ink); color: white;
    }}
    button:hover, .btn:hover {{ opacity: .87; }}
    button.secondary {{ background: var(--brand-bg); color: var(--brand); }}
    button.danger {{ background: var(--danger-bg); color: var(--danger); border: 1px solid #FECACA; }}
    button.sm {{ padding: 5px 10px; font-size: 12px; }}
    button[disabled] {{ opacity: .45; cursor: not-allowed; }}

    /* ── Pills ── */
    .pill {{ display: inline-flex; align-items: center; gap: 4px; border-radius: 999px; padding: 3px 9px; font-size: 11px; font-weight: 700; letter-spacing: .03em; }}
    .pill::before {{ content: ""; width: 6px; height: 6px; border-radius: 50%; background: currentColor; }}
    .pill.ok {{ color: var(--ok); background: var(--ok-bg); }}
    .pill.off {{ color: var(--muted); background: var(--border-light); }}

    /* ── Utility ── */
    .actions {{ display: flex; align-items: center; gap: 6px; flex-wrap: nowrap; }}
    .mono {{ font-family: Consolas, "Courier New", monospace; }}
    .muted {{ color: var(--muted); }}
    .notice {{ color: var(--muted); font-size: 13px; line-height: 1.6; }}
    .matrix-wrap {{ overflow-x: auto; }}
    .matrix-wrap th:first-child, .matrix-wrap td:first-child {{
      position: sticky; left: 0; background: #F8FAFC; z-index: 2;
    }}
    .matrix-wrap tbody td:first-child {{ background: var(--panel); }}
    .matrix-wrap tbody tr:hover td:first-child {{ background: #FAFBFD; }}
    .menu-head {{ min-width: 110px; font-size: 11px; }}
    .info-bar {{
      display: flex; align-items: center; gap: 12px; padding: 10px 14px;
      background: var(--ok-bg); border: 1px solid #BBF7D0; border-radius: var(--radius);
      font-size: 13px; color: var(--ok); font-weight: 600; margin-bottom: 16px;
    }}

    /* ── Login page ── */
    .login-wrap {{
      display: flex; align-items: center; justify-content: center; min-height: 100vh;
      background: var(--bg); padding: 24px;
    }}
    .login-card {{
      background: var(--panel); border: 1px solid var(--border); border-radius: 12px;
      padding: 36px 40px; width: 100%; max-width: 420px; box-shadow: 0 4px 24px rgba(15,23,42,.10);
    }}
    .login-logo {{ display: flex; align-items: center; gap: 10px; margin-bottom: 28px; }}
    .login-card h2 {{ font-size: 20px; font-weight: 700; margin-bottom: 6px; }}
    .login-card .notice {{ margin-bottom: 22px; }}
    .login-card label {{ display: block; font-size: 12px; font-weight: 600; color: var(--muted); margin: 14px 0 5px; }}
    .login-card button {{ width: 100%; margin-top: 20px; padding: 10px; font-size: 14px; justify-content: center; }}
  </style>
</head>
<body>
  <div class="layout">
    {sidebar}
    <div class="main-wrap">
      <div class="topbar">
        {topbar_right}
        <span class="topbar-pill">● LIVE</span>
      </div>
      <div class="content">{body}</div>
    </div>
  </div>
</body>
</html>"""
    return HTMLResponse(html)


@router.get("", include_in_schema=False)
def admin_root(user: AdminUser):
    return _redirect("/admin/menu-access")


@router.get("/login", response_class=HTMLResponse, include_in_schema=False)
def login_page():
    html = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Login — GPA Admin</title>
  <style>
    *,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
    body{font-family:"Inter","Segoe UI",system-ui,Arial,sans-serif;font-size:14px;background:#F8FAFC;color:#0F172A;display:flex;align-items:center;justify-content:center;min-height:100vh;padding:24px}
    .card{background:#fff;border:1px solid #E2E8F0;border-radius:12px;padding:38px 40px;width:100%;max-width:420px;box-shadow:0 4px 24px rgba(15,23,42,.10)}
    .logo{display:flex;align-items:center;gap:10px;margin-bottom:28px}
    .logo-mark{width:36px;height:36px;border-radius:8px;background:#2563EB;display:flex;align-items:center;justify-content:center;font-weight:800;font-size:17px;color:white}
    .logo-name{font-size:15px;font-weight:700;color:#0F172A;line-height:1.2}
    .logo-sub{font-size:10px;color:#64748B;letter-spacing:.06em;text-transform:uppercase}
    h2{font-size:20px;font-weight:700;margin-bottom:6px}
    .notice{color:#64748B;font-size:13px;line-height:1.6;margin-bottom:22px}
    label{display:block;font-size:12px;font-weight:600;color:#64748B;margin:14px 0 5px}
    input{width:100%;padding:9px 12px;border:1px solid #E2E8F0;border-radius:6px;font:inherit;font-size:13px;color:#0F172A;transition:border-color .15s,box-shadow .15s}
    input:focus{outline:none;border-color:#2563EB;box-shadow:0 0 0 3px rgba(37,99,235,.12)}
    button{width:100%;margin-top:20px;padding:10px;border:none;border-radius:6px;background:#0F172A;color:white;font:inherit;font-size:14px;font-weight:700;cursor:pointer;transition:opacity .15s}
    button:hover{opacity:.87}
  </style>
</head>
<body>
  <div class="card">
    <div class="logo">
      <div class="logo-mark">G</div>
      <div><div class="logo-name">GPA ERP</div><div class="logo-sub">Backend Admin</div></div>
    </div>
    <h2>Sign in</h2>
    <p class="notice">Super Admin accounts only. This console is for backend configuration and master-data management.</p>
    <form method="post" action="/admin/login">
      <label>Email</label>
      <input name="email" type="email" autocomplete="username" placeholder="admin@company.com" required>
      <label>Password</label>
      <input name="password" type="password" autocomplete="current-password" placeholder="••••••••" required>
      <button type="submit">Sign in →</button>
    </form>
  </div>
</body>
</html>"""
    return HTMLResponse(html)


def login_page_with_error(msg: str) -> HTMLResponse:
    html = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Login — GPA Admin</title>
  <style>
    *,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
    body{font-family:"Inter","Segoe UI",system-ui,Arial,sans-serif;font-size:14px;background:#F8FAFC;color:#0F172A;display:flex;align-items:center;justify-content:center;min-height:100vh;padding:24px}
    .card{background:#fff;border:1px solid #E2E8F0;border-radius:12px;padding:38px 40px;width:100%;max-width:420px;box-shadow:0 4px 24px rgba(15,23,42,.10)}
    .logo{display:flex;align-items:center;gap:10px;margin-bottom:28px}
    .logo-mark{width:36px;height:36px;border-radius:8px;background:#2563EB;display:flex;align-items:center;justify-content:center;font-weight:800;font-size:17px;color:white}
    .logo-name{font-size:15px;font-weight:700;color:#0F172A;line-height:1.2}
    .logo-sub{font-size:10px;color:#64748B;letter-spacing:.06em;text-transform:uppercase}
    h2{font-size:20px;font-weight:700;margin-bottom:12px}
    .err{background:#FEF2F2;border:1px solid #FECACA;color:#DC2626;border-radius:8px;padding:10px 14px;font-size:13px;font-weight:600;margin-bottom:20px}
    a{color:#2563EB;font-weight:600;text-decoration:none}
  </style>
</head>
<body>
  <div class="card">
    <div class="logo">
      <div class="logo-mark">G</div>
      <div><div class="logo-name">GPA ERP</div><div class="logo-sub">Backend Admin</div></div>
    </div>
    <h2>Sign in failed</h2>
    <div class="err">""" + escape(msg) + """</div>
    <p><a href="/admin/login">← Try again</a></p>
  </div>
</body>
</html>"""
    return HTMLResponse(html, status_code=401)


@router.post("/login", include_in_schema=False)
def login_submit(
    request: Request,
    email: Annotated[str, Form()],
    password: Annotated[str, Form()],
    db: Annotated[Session, Depends(get_db)],
):
    ip = get_client_ip(request)
    _check_login_rate(ip)
    user = db.query(User).filter(User.email == email.lower().strip()).first()
    if not user or not verify_password(password, user.hashed_password):
        _record_failure(ip)
        return login_page_with_error("Incorrect email or password.")
    if not user.is_active or user.role.name != RoleName.SUPER_ADMIN:
        _record_failure(ip)
        return login_page_with_error("Access restricted to active Super Admin accounts.")
    _clear_failures(ip)

    token, expires_in = create_access_token({
        "sub": str(user.id),
        "role": user.role.name.value,
        "ver": user.token_version,
    })
    write_audit(db, "BackendAdmin", user.id, "LOGIN", changed_by=user.id, ip_address=get_client_ip(request))
    db.commit()
    response = _redirect("/admin/approval-matrix")
    response.set_cookie(
        ADMIN_COOKIE,
        token,
        max_age=expires_in,
        httponly=True,
        secure=not settings.DEBUG,
        samesite="lax",
        path="/admin",
    )
    return response


@router.get("/logout", include_in_schema=False)
def logout():
    response = _redirect("/admin/login")
    response.delete_cookie(
        ADMIN_COOKIE,
        path="/admin",
        secure=not settings.DEBUG,
        samesite="lax",
    )
    return response


def _summary(db: Session) -> str:
    checks = [
        ("Projects", db.query(Project).count()),
        ("Users", db.query(User).count()),
        ("Expenses", db.query(Expense).count()),
        ("AR Invoices", db.query(AccountReceivable).count()),
        ("Legal Docs", db.query(LegalDocument).count()),
        ("Inventory Items", db.query(InventoryItem).count()),
        ("Petty Cash", db.query(PettyCashReport).count()),
        ("Approval Rules", db.query(ApprovalRule).count()),
        ("App Menus", db.query(AppMenu).count()),
    ]
    cards = "".join(
        f"""<div class="metric-card">
          <div class="metric-label">{escape(label)}</div>
          <div class="metric-value">{count:,}</div>
        </div>"""
        for label, count in checks
    )
    return f'<div class="metrics">{cards}</div>'


def _health_panel(db: Session) -> str:
    db.execute(text("SELECT 1"))
    recent = (
        db.query(AuditLog)
        .order_by(AuditLog.created_at.desc())
        .limit(8)
        .all()
    )
    rows = "".join(
        f"<tr><td class='mono' style='font-size:12px;color:#64748B'>{escape(str(log.created_at)[:19])}</td>"
        f"<td><span style='font-size:12px;font-weight:600;color:#334155'>{escape(log.entity_type)}</span></td>"
        f"<td><span style='font-size:11px;font-weight:700;letter-spacing:.05em;color:#2563EB'>{escape(log.action)}</span></td>"
        f"<td style='color:#64748B;font-size:12px'>{escape(str(log.changed_by or 'system'))}</td></tr>"
        for log in recent
    )
    if not rows:
        rows = '<tr><td colspan="4" style="color:#94A3B8;text-align:center;padding:24px">No audit activity yet.</td></tr>'
    return f"""
    <div class="section-head">
      <div><h2>System Status</h2><p>Database is live — {escape(settings.APP_NAME)} v{escape(settings.APP_VERSION)}</p></div>
    </div>
    <div class="info-bar">
      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>
      Database connected &nbsp;·&nbsp; API responding &nbsp;·&nbsp; {escape(settings.APP_NAME)} {escape(settings.APP_VERSION)}
    </div>
    <div class="section-head"><div><h2>Recent Audit Log</h2><p>Last 8 write operations across all entities</p></div></div>
    <div class="table-wrap">
      <table>
        <thead><tr><th>Timestamp</th><th>Entity</th><th>Action</th><th>By User</th></tr></thead>
        <tbody>{rows}</tbody>
      </table>
    </div>
    """


def _menu_options_summary(db: Session) -> tuple[list[AppMenu], list[User], dict[tuple[int, int], bool]]:
    ensure_default_menus(db)
    menus = (
        db.query(AppMenu)
        .filter(AppMenu.key.notin_(OBSOLETE_MENU_KEYS))
        .order_by(AppMenu.section, AppMenu.sort_order, AppMenu.label)
        .all()
    )
    users = db.query(User).order_by(User.full_name).all()
    permissions = {
        (perm.user_id, perm.menu_id): perm.can_access
        for perm in db.query(UserMenuPermission).all()
    }
    return menus, users, permissions


def _menu_registry_table(menus: list[AppMenu]) -> str:
    rows = []
    for menu in menus:
        status_pill = '<span class="pill ok">ACTIVE</span>' if menu.is_active else '<span class="pill off">INACTIVE</span>'
        toggle_label = "Deactivate" if menu.is_active else "Activate"
        toggle_class = "danger" if menu.is_active else "secondary"
        rows.append(f"""
        <tr>
          <form method="post" action="/admin/menus/{menu.id}/update">
            <td><input name="label" value="{escape(menu.label)}" required></td>
            <td><input name="key" value="{escape(menu.key)}" required></td>
            <td><input name="section" value="{escape(menu.section)}" required></td>
            <td><input name="path" value="{escape(menu.path or '')}"></td>
            <td><input name="sort_order" type="number" value="{menu.sort_order}" required></td>
            <td>{status_pill}</td>
            <td class="actions">
              <button class="secondary" type="submit">Save</button>
          </form>
              <form method="post" action="/admin/menus/{menu.id}/toggle">
                <button class="{toggle_class}" type="submit">{toggle_label}</button>
              </form>
            </td>
        </tr>
        """)
    return f"""
    <div class="section-head" style="margin-top:32px">
      <div>
        <h2>Menu Registry</h2>
        <p>All ERP menus that can be toggled per user via the access matrix above.</p>
      </div>
    </div>
    <div class="table-wrap">
      <table>
        <thead><tr><th>Label</th><th>Key</th><th>Section</th><th>Path</th><th>Sort</th><th>Status</th><th>Actions</th></tr></thead>
        <tbody>{''.join(rows)}</tbody>
      </table>
    </div>
    <div class="card">
      <h3>Add Menu Item</h3>
      <form method="post" action="/admin/menus" style="display:grid;grid-template-columns:1fr 1fr 1fr 1fr 90px auto;gap:12px;align-items:end">
        <label>Label<input name="label" placeholder="Tax" required></label>
        <label>Key<input name="key" placeholder="tax" required></label>
        <label>Section<input name="section" placeholder="Finance" required></label>
        <label>Path<input name="path" placeholder="/tax"></label>
        <label>Sort<input name="sort_order" type="number" value="120" required></label>
        <button type="submit">Add</button>
      </form>
    </div>
    """


def _menu_access_matrix(menus: list[AppMenu], users: list[User], permissions: dict[tuple[int, int], bool]) -> str:
    active_menus = [menu for menu in menus if menu.is_active]
    header = "".join(
        f'<th class="menu-head">{escape(menu.label)}<br><span class="notice">{escape(menu.section)}</span></th>'
        for menu in active_menus
    )
    rows = []
    for target_user in users:
        cells = []
        is_super_admin = target_user.role.name == RoleName.SUPER_ADMIN
        for menu in active_menus:
            allowed = True if is_super_admin else permissions.get((target_user.id, menu.id), False)
            checked = " checked" if allowed else ""
            disabled = " disabled" if is_super_admin else ""
            cells.append(
                f'<td style="text-align:center"><input type="checkbox" name="menu_{menu.id}" value="1"{checked}{disabled}></td>'
            )
        lock_note = "Always full access" if is_super_admin else "Editable"
        preset_options = "".join(
            f'<option value="{escape(name)}">{escape(name.replace("_", " ").title())}</option>'
            for name in ROLE_PRESETS
            if name != "SUPER_ADMIN"
        )
        rows.append(f"""
        <tr>
          <form method="post" action="/admin/menu-access/{target_user.id}">
            <td>
              <strong>{escape(target_user.full_name)}</strong><br>
              <span class="notice">{escape(target_user.email)} - {escape(target_user.role.name.value)}</span><br>
              <span class="notice">{lock_note}</span>
            </td>
            {''.join(cells)}
            <td><button class="secondary" type="submit"{' disabled' if is_super_admin else ''}>Save</button></td>
          </form>
          <td>
            <form method="post" action="/admin/menu-access/{target_user.id}/preset" class="actions">
              <select name="preset"{' disabled' if is_super_admin else ''}>{preset_options}</select>
              <button type="submit"{' disabled' if is_super_admin else ''}>Apply</button>
            </form>
          </td>
        </tr>
        """)
    return f"""
    <div class="section-head">
      <div>
        <h2>User Menu Access Matrix</h2>
        <p>Toggle menu visibility per user. Super Admin always has full access.</p>
      </div>
    </div>
    <div class="table-wrap matrix-wrap">
      <table>
        <thead><tr><th>User</th>{header}<th>Save</th><th>Apply Preset</th></tr></thead>
        <tbody>{''.join(rows)}</tbody>
      </table>
    </div>
    """


def _category_options(selected: CostCodeCategory | None) -> str:
    options = ['<option value="">All categories</option>']
    for category in CostCodeCategory:
        is_selected = " selected" if selected == category else ""
        options.append(f'<option value="{escape(category.value)}"{is_selected}>{escape(category.value)}</option>')
    return "".join(options)


def _role_options(selected: RoleName | None) -> str:
    options = []
    for role in RoleName:
        is_selected = " selected" if selected == role else ""
        options.append(f'<option value="{escape(role.value)}"{is_selected}>{escape(role.value.replace("_", " ").title())}</option>')
    return "".join(options)


def _matrix_table(rules: list[ApprovalRule]) -> str:
    rows = []
    for rule in rules:
        checked = " checked" if rule.is_active else ""
        status_pill = '<span class="pill ok">ACTIVE</span>' if rule.is_active else '<span class="pill off">INACTIVE</span>'
        rows.append(f"""
        <tr>
          <form method="post" action="/admin/approval-matrix/{rule.id}/update">
            <td class="amount"><input name="min_amount" value="{escape(_money(rule.min_amount))}" required></td>
            <td class="amount"><input name="max_amount" value="{escape(_money(rule.max_amount))}" placeholder="No limit"></td>
            <td><select name="cost_code_category">{_category_options(rule.cost_code_category)}</select></td>
            <td><select name="required_role">{_role_options(rule.required_role)}</select></td>
            <td><input name="priority" type="number" min="1" value="{rule.priority}" required></td>
            <td>{status_pill}<input name="is_active" type="checkbox" value="true"{checked} title="Active"></td>
            <td class="actions">
              <button class="secondary" type="submit">Save</button>
          </form>
              <form method="post" action="/admin/approval-matrix/{rule.id}/delete">
                <button class="danger" type="submit">Remove</button>
              </form>
            </td>
        </tr>
        """)
    if not rows:
        rows.append('<tr><td colspan="7">No approval rules yet.</td></tr>')

    return f"""
    <div class="section-head">
      <div>
        <h2>Approval Matrix</h2>
        <p>Amount thresholds, cost code categories, required approver roles and priority order.</p>
      </div>
    </div>
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Min Amount (Rp)</th>
            <th>Max Amount (Rp)</th>
            <th>Category</th>
            <th>Required Role</th>
            <th>Priority</th>
            <th>Status</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>{''.join(rows)}</tbody>
      </table>
    </div>
    <div class="card">
      <h3>Add Rule</h3>
      <form method="post" action="/admin/approval-matrix" style="display:grid;grid-template-columns:1fr 1fr 1fr 1fr 100px auto;gap:12px;align-items:end">
        <label>Min Amount<input name="min_amount" placeholder="0" required></label>
        <label>Max Amount<input name="max_amount" placeholder="No limit"></label>
        <label>Category<select name="cost_code_category">{_category_options(None)}</select></label>
        <label>Required Role<select name="required_role">{_role_options(RoleName.COST_CONTROL)}</select></label>
        <label>Priority<input name="priority" type="number" min="1" value="1" required></label>
        <button type="submit">Add Rule</button>
      </form>
    </div>
    """


@router.get("/approval-matrix", response_class=HTMLResponse, include_in_schema=False)
def approval_matrix(user: AdminUser, db: Annotated[Session, Depends(get_db)]):
    rules = (
        db.query(ApprovalRule)
        .order_by(ApprovalRule.priority, ApprovalRule.min_amount, ApprovalRule.id)
        .all()
    )
    body = _summary(db) + _matrix_table(rules) + _health_panel(db)
    return _page("Approval Matrix", body, user)


@router.post("/approval-matrix", include_in_schema=False)
def create_approval_rule(
    request: Request,
    user: AdminUser,
    db: Annotated[Session, Depends(get_db)],
    min_amount: Annotated[str, Form()],
    max_amount: Annotated[str | None, Form()] = None,
    cost_code_category: Annotated[str | None, Form()] = None,
    required_role: Annotated[str, Form()] = RoleName.COST_CONTROL.value,
    priority: Annotated[int, Form()] = 1,
):
    rule = ApprovalRule(
        min_amount=_parse_decimal(min_amount, default=Decimal("0")),
        max_amount=_parse_decimal(max_amount),
        cost_code_category=CostCodeCategory(cost_code_category) if cost_code_category else None,
        required_role=RoleName(required_role),
        priority=priority,
        is_active=True,
    )
    db.add(rule)
    db.flush()
    write_audit(db, "ApprovalRule", rule.id, "CREATE", changed_by=user.id, ip_address=get_client_ip(request), after=model_to_dict(rule))
    db.commit()
    return _redirect("/admin/approval-matrix")


@router.post("/approval-matrix/{rule_id}/update", include_in_schema=False)
def update_approval_rule(
    rule_id: int,
    request: Request,
    user: AdminUser,
    db: Annotated[Session, Depends(get_db)],
    min_amount: Annotated[str, Form()],
    max_amount: Annotated[str | None, Form()] = None,
    cost_code_category: Annotated[str | None, Form()] = None,
    required_role: Annotated[str, Form()] = RoleName.COST_CONTROL.value,
    priority: Annotated[int, Form()] = 1,
    is_active: Annotated[str | None, Form()] = None,
):
    rule = db.query(ApprovalRule).filter(ApprovalRule.id == rule_id).first()
    if not rule:
        raise HTTPException(status_code=404, detail="Approval rule not found")
    before = model_to_dict(rule)
    rule.min_amount = _parse_decimal(min_amount, default=Decimal("0"))
    rule.max_amount = _parse_decimal(max_amount)
    rule.cost_code_category = CostCodeCategory(cost_code_category) if cost_code_category else None
    rule.required_role = RoleName(required_role)
    rule.priority = priority
    rule.is_active = is_active == "true"
    write_audit(db, "ApprovalRule", rule.id, "UPDATE", changed_by=user.id, ip_address=get_client_ip(request), before=before, after=model_to_dict(rule))
    db.commit()
    return _redirect("/admin/approval-matrix")


@router.post("/approval-matrix/{rule_id}/delete", include_in_schema=False)
def delete_approval_rule(
    rule_id: int,
    request: Request,
    user: AdminUser,
    db: Annotated[Session, Depends(get_db)],
):
    rule = db.query(ApprovalRule).filter(ApprovalRule.id == rule_id).first()
    if not rule:
        raise HTTPException(status_code=404, detail="Approval rule not found")
    before = model_to_dict(rule)
    db.delete(rule)
    write_audit(db, "ApprovalRule", rule_id, "DELETE", changed_by=user.id, ip_address=get_client_ip(request), before=before)
    db.commit()
    return _redirect("/admin/approval-matrix")


@router.get("/menu-access", response_class=HTMLResponse, include_in_schema=False)
def menu_access(user: AdminUser, db: Annotated[Session, Depends(get_db)]):
    menus, users, permissions = _menu_options_summary(db)
    body = _summary(db) + _menu_access_matrix(menus, users, permissions) + _menu_registry_table(menus)
    return _page("Menu Access", body, user)


@router.post("/menus", include_in_schema=False)
def create_menu(
    request: Request,
    user: AdminUser,
    db: Annotated[Session, Depends(get_db)],
    label: Annotated[str, Form()],
    key: Annotated[str, Form()],
    section: Annotated[str, Form()],
    path: Annotated[str | None, Form()] = None,
    sort_order: Annotated[int, Form()] = 100,
):
    clean_key = key.strip().lower().replace(" ", "_")
    if db.query(AppMenu).filter(AppMenu.key == clean_key).first():
        raise HTTPException(status_code=409, detail="Menu key already exists")
    menu = AppMenu(
        key=clean_key,
        label=label.strip(),
        section=section.strip(),
        path=path.strip() if path else None,
        sort_order=sort_order,
        is_active=True,
    )
    db.add(menu)
    db.flush()
    write_audit(db, "AppMenu", menu.id, "CREATE", changed_by=user.id, ip_address=get_client_ip(request), after=model_to_dict(menu))
    db.commit()
    return _redirect("/admin/menu-access")


@router.post("/menus/{menu_id}/update", include_in_schema=False)
def update_menu(
    menu_id: int,
    request: Request,
    user: AdminUser,
    db: Annotated[Session, Depends(get_db)],
    label: Annotated[str, Form()],
    key: Annotated[str, Form()],
    section: Annotated[str, Form()],
    path: Annotated[str | None, Form()] = None,
    sort_order: Annotated[int, Form()] = 100,
):
    menu = db.query(AppMenu).filter(AppMenu.id == menu_id).first()
    if not menu:
        raise HTTPException(status_code=404, detail="Menu not found")
    clean_key = key.strip().lower().replace(" ", "_")
    existing = db.query(AppMenu).filter(AppMenu.key == clean_key, AppMenu.id != menu_id).first()
    if existing:
        raise HTTPException(status_code=409, detail="Menu key already exists")
    before = model_to_dict(menu)
    menu.key = clean_key
    menu.label = label.strip()
    menu.section = section.strip()
    menu.path = path.strip() if path else None
    menu.sort_order = sort_order
    write_audit(db, "AppMenu", menu.id, "UPDATE", changed_by=user.id, ip_address=get_client_ip(request), before=before, after=model_to_dict(menu))
    db.commit()
    return _redirect("/admin/menu-access")


@router.post("/menus/{menu_id}/toggle", include_in_schema=False)
def toggle_menu(
    menu_id: int,
    request: Request,
    user: AdminUser,
    db: Annotated[Session, Depends(get_db)],
):
    menu = db.query(AppMenu).filter(AppMenu.id == menu_id).first()
    if not menu:
        raise HTTPException(status_code=404, detail="Menu not found")
    before = model_to_dict(menu)
    menu.is_active = not menu.is_active
    action = "ACTIVATE" if menu.is_active else "DEACTIVATE"
    write_audit(
        db,
        "AppMenu",
        menu_id,
        action,
        changed_by=user.id,
        ip_address=get_client_ip(request),
        before=before,
        after=model_to_dict(menu),
    )
    db.commit()
    return _redirect("/admin/menu-access")


@router.post("/menu-access/{target_user_id}", include_in_schema=False)
async def update_menu_access(
    target_user_id: int,
    request: Request,
    user: AdminUser,
    db: Annotated[Session, Depends(get_db)],
):
    target_user = db.query(User).filter(User.id == target_user_id).first()
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")
    if target_user.role.name == RoleName.SUPER_ADMIN:
        return _redirect("/admin/menu-access")

    form = await request.form()
    menus = db.query(AppMenu).filter(AppMenu.is_active == True).all()
    before = {
        str(perm.menu_id): perm.can_access
        for perm in db.query(UserMenuPermission).filter(UserMenuPermission.user_id == target_user_id).all()
    }
    for menu in menus:
        can_access = f"menu_{menu.id}" in form
        permission = (
            db.query(UserMenuPermission)
            .filter(
                UserMenuPermission.user_id == target_user_id,
                UserMenuPermission.menu_id == menu.id,
            )
            .first()
        )
        if permission:
            permission.can_access = can_access
        else:
            db.add(
                UserMenuPermission(
                    user_id=target_user_id,
                    menu_id=menu.id,
                    can_access=can_access,
                )
            )
    db.flush()
    after = {
        str(perm.menu_id): perm.can_access
        for perm in db.query(UserMenuPermission).filter(UserMenuPermission.user_id == target_user_id).all()
    }
    write_audit(
        db,
        "UserMenuPermission",
        target_user_id,
        "UPDATE",
        changed_by=user.id,
        ip_address=get_client_ip(request),
        before=before,
        after=after,
    )
    db.commit()
    return _redirect("/admin/menu-access")


@router.post("/menu-access/{target_user_id}/preset", include_in_schema=False)
def apply_menu_preset(
    target_user_id: int,
    request: Request,
    user: AdminUser,
    db: Annotated[Session, Depends(get_db)],
    preset: Annotated[str, Form()],
):
    target_user = db.query(User).filter(User.id == target_user_id).first()
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")
    if target_user.role.name == RoleName.SUPER_ADMIN:
        return _redirect("/admin/menu-access")
    if preset not in ROLE_PRESETS:
        raise HTTPException(status_code=400, detail="Unknown preset")

    ensure_default_menus(db)
    menus = db.query(AppMenu).filter(AppMenu.is_active == True).all()
    preset_keys = ROLE_PRESETS[preset]
    before = {
        str(perm.menu_id): perm.can_access
        for perm in db.query(UserMenuPermission).filter(UserMenuPermission.user_id == target_user_id).all()
    }
    for menu in menus:
        permission = (
            db.query(UserMenuPermission)
            .filter(
                UserMenuPermission.user_id == target_user_id,
                UserMenuPermission.menu_id == menu.id,
            )
            .first()
        )
        can_access = menu.key in preset_keys
        if permission:
            permission.can_access = can_access
        else:
            db.add(UserMenuPermission(user_id=target_user_id, menu_id=menu.id, can_access=can_access))
    db.flush()
    after = {
        str(perm.menu_id): perm.can_access
        for perm in db.query(UserMenuPermission).filter(UserMenuPermission.user_id == target_user_id).all()
    }
    write_audit(
        db,
        "UserMenuPermission",
        target_user_id,
        f"APPLY_PRESET:{preset}",
        changed_by=user.id,
        ip_address=get_client_ip(request),
        before=before,
        after=after,
    )
    db.commit()
    return _redirect("/admin/menu-access")

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import CurrentUser
from app.models import AppMenu, Role, RoleName, User, UserMenuPermission

DEFAULT_MENUS = [
    ("dashboard", "Dashboard", "Workspace", "/dashboard", "Executive dashboard and KPI overview", 10),
    ("action_center", "Action Center", "Workspace", "/action-center", "Approval queue and pending actions", 15),
    ("project_command", "Project Command", "Workspace", "/projects", "Project master, contract value, archive controls", 20),
    ("revenue_ar", "Revenue / AR", "Finance", "/revenue", "Client invoices, payments, outstanding AR", 30),
    ("spending", "Spending", "Finance", "/spending", "Expense drafts, approvals, payment workflow", 40),
    ("petty_cash", "Petty Cash", "Finance", "/spending", "Monthly petty cash reports and OCR/clipboard entry", 45),
    ("accounts_payable", "Accounts Payable", "Finance", "/accounts-payable", "Vendor invoices, payment vouchers, payable ageing", 46),
    ("accounting_tax", "Accounting & Tax", "Finance", "/accounting-tax", "Journals, taxes, bank reconciliation, period controls", 47),
    ("reports", "Reports", "Finance", "/reports", "Finance, project, and operational reports", 48),
    ("budget_bi", "Budget & BI", "Finance", "/budget-bi", "Budgets, forecasts, cash flow, profitability, management KPI", 49),
    ("project_execution", "Project Execution", "Project & EPC", "/project-execution", "WBS, progress, milestones, changes, claims, EAC", 50),
    ("procurement", "Procurement", "Project & EPC", "/procurement", "Vendors, PR, RFQ, quotation, PO, goods receipt", 51),
    ("engineering_documents", "Engineering Documents", "Project & EPC", "/engineering-documents", "Drawings, RFI, submittals, transmittals, as-built", 52),
    ("quality_control", "QA / QC", "Project & EPC", "/quality-control", "ITP, inspection, test records, NCR, punch list", 53),
    ("hse", "HSE", "Project & EPC", "/hse", "Incidents, permits, safety inspection, JSA/JHA, PPE", 54),
    ("inventory", "Inventory & Stock", "Operations", "/inventory", "Materials, tools, consumables, and stock movement", 60),
    ("warehouse_logistics", "Warehouse & Logistics", "Operations", "/warehouse-logistics", "Transfers, reservations, issues, returns, stock count", 61),
    ("equipment_assets", "Equipment & Assets", "Operations", "/equipment-assets", "Asset register, maintenance, calibration, utilisation", 62),
    ("legal", "Legal & Proposals", "Operations", "/legal", "Legal proposals, drafts, documents, signatures", 63),
    ("contract_management", "Contract Management", "Operations", "/contracts", "Client, vendor, subcontract agreements and claims", 64),
    ("crm_tender", "CRM & Tenders", "Operations", "/crm-tenders", "Customers, opportunities, tenders, estimates, quotations", 65),
    ("settings", "Settings", "Vault", "/settings", "Users, roles, branding, configuration", 90),
    ("vault", "Vault", "Vault", "/vault", "Approval matrix, cost codes, cost centres, audit log", 100),
    ("backend_admin", "Backend Admin", "System", "/admin", "Backend maintenance console", 110),
    # HRIS modules — admin/HR views
    ("hris_dashboard",   "HRIS Dashboard",    "HRIS", "/hris",             "Headcount KPIs, employment mix, org overview", 200),
    ("hris_employees",   "Data Karyawan",     "HRIS", "/hris/employees",   "Employee master, departments, job grades",    210),
    ("hris_attendance",  "Absensi & Lembur",  "HRIS", "/hris/attendance",  "Daily attendance, geolocation clock-in, overtime", 220),
    ("hris_leave",       "Cuti & Izin",       "HRIS", "/hris/leave",       "Leave requests, balances, approval flow",     230),
    ("hris_payroll",     "Penggajian",        "HRIS", "/hris/payroll",     "Payroll run, BPJS, PPh21, slip gaji",        240),
    ("hris_recruitment", "Rekrutmen",         "HRIS", "/hris/recruitment", "Job postings, applicant pipeline, onboarding", 250),
    ("hris_settings",    "Pengaturan HRIS",   "HRIS", "/hris/settings",    "Work locations, leave types, holiday calendar, salary components", 260),
    ("manpower_operations", "Manpower Operations", "HRIS", "/hris/manpower", "Mobilisation, assignments, rosters, timesheets, competency", 265),
    # HRIS self-service — worker/employee portal
    ("hris_my_payslip",  "Slip Gaji Saya",    "Self Service", "/hris/me/payslip", "View own monthly payslips", 245),
]
OBSOLETE_MENU_KEYS = {"expenses"}

ROLE_PRESETS: dict[str, set[str]] = {
    "SUPER_ADMIN": {key for key, *_ in DEFAULT_MENUS},
    "MD": {
        "dashboard", "action_center", "project_command", "revenue_ar", "spending",
        "accounts_payable", "accounting_tax", "budget_bi", "project_execution",
        "procurement", "engineering_documents", "quality_control", "hse",
        "inventory", "warehouse_logistics", "equipment_assets", "legal",
        "contract_management", "crm_tender", "reports", "manpower_operations",
        "hris_dashboard", "hris_employees", "hris_attendance", "hris_leave",
        "hris_payroll", "hris_recruitment", "hris_my_payslip",
    },
    "PM": {
        "dashboard", "action_center", "project_command", "spending",
        "budget_bi", "project_execution", "procurement", "engineering_documents",
        "quality_control", "hse", "inventory", "warehouse_logistics",
        "equipment_assets", "legal", "contract_management", "crm_tender", "reports",
        "manpower_operations",
        "hris_dashboard", "hris_employees", "hris_attendance", "hris_leave", "hris_my_payslip",
    },
    "COST_CONTROL": {
        "dashboard", "action_center", "project_command", "spending", "petty_cash",
        "accounts_payable", "budget_bi", "project_execution", "procurement",
        "inventory", "warehouse_logistics", "reports",
        "hris_dashboard", "hris_my_payslip",
    },
    "FINANCE": {
        "dashboard", "action_center", "project_command", "revenue_ar", "spending",
        "petty_cash", "accounts_payable", "accounting_tax", "budget_bi",
        "procurement", "equipment_assets", "reports",
        "hris_dashboard", "hris_payroll", "hris_my_payslip",
    },
    "GA": {
        "dashboard", "action_center", "spending", "petty_cash", "inventory",
        "warehouse_logistics", "equipment_assets", "quality_control", "hse",
        "contract_management", "manpower_operations",
        "hris_dashboard", "hris_employees", "hris_attendance", "hris_leave",
        "hris_recruitment", "hris_settings", "hris_my_payslip",
    },
    # Office staff: expense reimbursements only + HRIS self-service portal
    # No ERP dashboard/action_center — frontend routes them to /hris/me
    "STAFF": {
        "spending",
        "hris_attendance", "hris_leave", "hris_my_payslip",
    },
    # Site/field worker: HRIS self-service only — no ERP access
    "WORKER": {
        "hris_attendance", "hris_leave", "hris_my_payslip",
    },
}

# Alias roles inherit their target's menu set verbatim.
ROLE_PRESETS["HR"] = set(ROLE_PRESETS["GA"])
ROLE_PRESETS["PROJECT_CONTROL"] = set(ROLE_PRESETS["PM"])

# Every authenticated user needs Settings for their own profile and password.
# Administrative tabs and endpoints remain protected separately by role guards.
for _preset in ROLE_PRESETS.values():
    _preset.add("settings")


def ensure_all_roles(db: Session) -> None:
    """Create a Role row for every RoleName enum member that is missing.
    Makes newly-added roles (e.g. HR, PROJECT_CONTROL) immediately assignable."""
    existing = {r.name for r in db.query(Role).all()}
    changed = False
    for name in RoleName:
        if name not in existing:
            db.add(Role(name=name))
            changed = True
    if changed:
        db.commit()


def grant_menu_to_roles(db: Session, menu_key: str, role_names: tuple[RoleName, ...]) -> None:
    """Ensure active users with the given roles can access menu_key. Add-only —
    never overrides an explicit per-user deny. Backfills existing users when a
    role preset gains a new menu."""
    menu = db.query(AppMenu).filter(AppMenu.key == menu_key, AppMenu.is_active == True).first()
    if not menu:
        return
    users = (
        db.query(User).join(User.role)
        .filter(Role.name.in_(role_names), User.is_active == True).all()
    )
    changed = False
    for u in users:
        exists = (
            db.query(UserMenuPermission.id)
            .filter(UserMenuPermission.user_id == u.id, UserMenuPermission.menu_id == menu.id)
            .first()
        )
        if not exists:
            db.add(UserMenuPermission(user_id=u.id, menu_id=menu.id, can_access=True))
            changed = True
    if changed:
        db.commit()


def seed_user_menu_permissions(db: Session, user: User, *, commit: bool = True) -> None:
    """Seed a single user's menu permissions from their role preset (idempotent).
    Used when a user is created so they have access without waiting for a restart."""
    if user.role.name == RoleName.SUPER_ADMIN:
        return
    menus = {m.key: m for m in db.query(AppMenu).filter(AppMenu.is_active == True).all()}
    existing = {
        p.menu_id for p in db.query(UserMenuPermission)
        .filter(UserMenuPermission.user_id == user.id).all()
    }
    preset_keys = ROLE_PRESETS.get(user.role.name.value, ROLE_PRESETS["STAFF"])
    changed = False
    for key in preset_keys:
        menu = menus.get(key)
        if menu and menu.id not in existing:
            db.add(UserMenuPermission(user_id=user.id, menu_id=menu.id, can_access=True))
            changed = True
    if changed and commit:
        db.commit()


def reset_user_menu_permissions_for_role(db: Session, user: User) -> None:
    """Replace role-derived access after a role change in the caller's transaction."""
    db.query(UserMenuPermission).filter(UserMenuPermission.user_id == user.id).delete(
        synchronize_session=False
    )
    if user.role.name == RoleName.SUPER_ADMIN:
        return

    menus = {m.key: m for m in db.query(AppMenu).filter(AppMenu.is_active == True).all()}
    preset_keys = ROLE_PRESETS.get(user.role.name.value, ROLE_PRESETS["STAFF"])
    for key in preset_keys:
        menu = menus.get(key)
        if menu:
            db.add(UserMenuPermission(user_id=user.id, menu_id=menu.id, can_access=True))


def ensure_default_menus(db: Session) -> None:
    existing = {menu.key: menu for menu in db.query(AppMenu).all()}
    changed = False
    for key, label, section, path, description, sort_order in DEFAULT_MENUS:
        menu = existing.get(key)
        if menu:
            updates = {
                "label": label,
                "section": section,
                "path": path,
                "description": description,
                "sort_order": sort_order,
                # A previously obsolete menu must become available again when
                # it returns to the supported default registry.
                "is_active": key not in OBSOLETE_MENU_KEYS,
            }
            for field, value in updates.items():
                if getattr(menu, field) != value:
                    setattr(menu, field, value)
                    changed = True
        else:
            db.add(
                AppMenu(
                    key=key,
                    label=label,
                    section=section,
                    path=path,
                    description=description,
                    sort_order=sort_order,
                    is_active=True,
                )
            )
            changed = True
    for key in OBSOLETE_MENU_KEYS:
        menu = existing.get(key)
        if menu and menu.is_active:
            menu.is_active = False
            changed = True
    if changed:
        db.commit()
    seed_missing_user_permissions(db)


def seed_missing_user_permissions(db: Session) -> None:
    menus = {menu.key: menu for menu in db.query(AppMenu).filter(AppMenu.is_active == True).all()}
    users = db.query(User).filter(User.is_active == True).all()
    changed = False
    for user in users:
        if user.role.name == RoleName.SUPER_ADMIN:
            continue
        existing_menu_ids = {
            row[0] for row in db.query(UserMenuPermission.menu_id)
            .filter(UserMenuPermission.user_id == user.id)
            .all()
        }
        preset_keys = ROLE_PRESETS.get(user.role.name.value, ROLE_PRESETS["STAFF"])
        for key in preset_keys:
            menu = menus.get(key)
            if menu and menu.id not in existing_menu_ids:
                db.add(UserMenuPermission(user_id=user.id, menu_id=menu.id, can_access=True))
                changed = True
    if changed:
        db.commit()


def menu_access_keys_for_user(db: Session, user: User) -> set[str]:
    if user.role.name == RoleName.SUPER_ADMIN:
        ensure_default_menus(db)
        return {menu.key for menu in db.query(AppMenu).filter(AppMenu.is_active == True).all()}

    rows = (
        db.query(AppMenu.key)
        .join(UserMenuPermission, UserMenuPermission.menu_id == AppMenu.id)
        .filter(
            UserMenuPermission.user_id == user.id,
            UserMenuPermission.can_access == True,
            AppMenu.is_active == True,
        )
        .all()
    )
    return {row[0] for row in rows}


def user_has_menu_access(db: Session, user: User, *menu_keys: str) -> bool:
    if user.role.name == RoleName.SUPER_ADMIN:
        return True
    allowed = menu_access_keys_for_user(db, user)
    return any(key in allowed for key in menu_keys)


def require_menu_access(*menu_keys: str):
    def _check(
        current_user: CurrentUser,
        db: Annotated[Session, Depends(get_db)],
    ) -> User:
        if not user_has_menu_access(db, current_user, *menu_keys):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Menu access required: {', '.join(menu_keys)}",
            )
        return current_user
    _check.required_menu_keys = frozenset(menu_keys)  # type: ignore[attr-defined]
    return _check

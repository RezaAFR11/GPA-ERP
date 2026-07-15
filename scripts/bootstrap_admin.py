"""Idempotent production bootstrap without sample business data."""
from app.config import get_settings
from app.database import SessionLocal
from app.dependencies import hash_password
from app.menu_permissions import (
    ensure_all_roles, ensure_default_menus, grant_menu_to_roles,
)
from app.models import Role, RoleName, User


def bootstrap() -> None:
    settings = get_settings()
    db = SessionLocal()
    try:
        ensure_all_roles(db)
        ensure_default_menus(db)

        super_admin_role = db.query(Role).filter(Role.name == RoleName.SUPER_ADMIN).first()
        if super_admin_role is None:
            raise RuntimeError("SUPER_ADMIN role was not created")

        email = settings.SEED_SUPER_ADMIN_EMAIL.lower().strip()
        existing = db.query(User).filter(User.email == email).first()
        if existing is None:
            db.add(User(
                email=email,
                hashed_password=hash_password(settings.SEED_SUPER_ADMIN_PASSWORD),
                full_name=settings.SEED_SUPER_ADMIN_NAME.strip(),
                role_id=super_admin_role.id,
                is_active=True,
                must_change_password=True,
            ))
            db.commit()
            print(f"Created initial Super Admin: {email}")
        elif existing.role_id != super_admin_role.id:
            raise RuntimeError(
                f"Bootstrap email {email} already belongs to a non-Super-Admin account"
            )
        else:
            print(f"Initial Super Admin already exists: {email}")

        grant_menu_to_roles(db, "hris_employees", (RoleName.PM, RoleName.PROJECT_CONTROL))
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    bootstrap()

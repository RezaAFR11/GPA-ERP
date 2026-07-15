"""In-app notifications and transaction-bound email delivery."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import SessionLocal
from app.models import EmailOutbox, Notification, RoleName, User, roles_inheriting
from app.notify_channels import build_notification_email, send_email


logger = logging.getLogger(__name__)
settings = get_settings()


def push(db: Session, user_id: int, title: str, body: str, link: str | None = None) -> None:
    """Add in-app and optional email notifications to the caller's transaction."""
    db.add(Notification(user_id=user_id, title=title, body=body, link=link))

    if not settings.SMTP_HOST or not settings.SMTP_USER:
        return
    user = db.query(User).filter(User.id == user_id).first()
    if not user or not user.email:
        return

    html, text = build_notification_email(title, body, link or "")
    db.add(
        EmailOutbox(
            to_email=user.email,
            subject=title,
            body_html=html,
            body_text=text,
        )
    )


def push_to_role(
    db: Session,
    role: RoleName,
    title: str,
    body: str,
    link: str | None = None,
) -> None:
    """Queue a notification for active users holding or inheriting a role."""
    from app.models import Role

    users = (
        db.query(User)
        .join(User.role)
        .filter(Role.name.in_(roles_inheriting(role)), User.is_active == True)
        .all()
    )
    for user in users:
        push(db, user.id, title, body, link)


def process_email_outbox_batch(limit: int = 20) -> int:
    """Deliver one committed outbox batch with retry tracking."""
    if not settings.SMTP_HOST or not settings.SMTP_USER:
        return 0

    db = SessionLocal()
    try:
        rows = (
            db.query(EmailOutbox)
            .filter(EmailOutbox.status == "pending", EmailOutbox.attempts < 5)
            .order_by(EmailOutbox.created_at, EmailOutbox.id)
            .with_for_update(skip_locked=True)
            .limit(limit)
            .all()
        )
        for row in rows:
            row.attempts += 1
            delivered, error = send_email(
                row.to_email,
                row.subject,
                row.body_html,
                row.body_text,
            )
            if delivered:
                row.status = "sent"
                row.sent_at = datetime.now(timezone.utc)
                row.last_error = None
            else:
                row.status = "failed" if row.attempts >= 5 else "pending"
                row.last_error = (error or "Unknown SMTP error")[:2000]
        db.commit()
        return len(rows)
    except Exception:
        db.rollback()
        logger.exception("Email outbox processing failed")
        return 0
    finally:
        db.close()


async def run_email_outbox_worker(stop_event: asyncio.Event) -> None:
    """Process committed outbox rows until application shutdown."""
    while not stop_event.is_set():
        await asyncio.to_thread(process_email_outbox_batch)
        try:
            await asyncio.wait_for(
                stop_event.wait(),
                timeout=settings.EMAIL_OUTBOX_POLL_SECONDS,
            )
        except TimeoutError:
            pass

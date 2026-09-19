"""Durable reminders and automatic closure, independent of an open browser."""
import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.config import get_settings
from app.database import SessionLocal
from app.hris_schedule_service import close_due_record
from app.models import AttendanceRecord, BrowserPushSubscription, AttendancePushDelivery, User
from app.notify import push

logger = logging.getLogger(__name__)


def process_attendance():
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        due = db.query(AttendanceRecord).filter(AttendanceRecord.clock_out.is_(None),
            AttendanceRecord.auto_closed_at.is_(None), AttendanceRecord.auto_close_at <= now
        ).order_by(AttendanceRecord.auto_close_at).with_for_update(skip_locked=True).limit(500).all()
        for record in due:
            close_due_record(db, record, now)
        db.commit()
        reminders = db.query(AttendanceRecord).filter(AttendanceRecord.clock_out.is_(None),
            AttendanceRecord.auto_closed_at.is_(None), AttendanceRecord.reminder_sent_at.is_(None),
            AttendanceRecord.reminder_at <= now, AttendanceRecord.auto_close_at > now
        ).with_for_update(skip_locked=True).limit(500).all()
        for record in reminders:
            record.reminder_sent_at = now
            data = record.schedule_snapshot
            end = datetime.fromisoformat(data["ends_at"])
            # Do not send stale reminders after the shift has ended.
            if now >= end:
                continue
            uid = record.employee.user_id
            user = db.get(User, uid) if uid else None
            if not user or not user.is_active:
                continue
            label = end.astimezone(ZoneInfo(data["timezone"])).strftime("%H:%M")
            payload = {"title": "Remember to clock out", "body": f"Your shift ends at {label}. Remember to clock out before leaving.",
                       "url": "/hris/me/attendance", "tag": f"attendance-{record.id}", "expires_at": data["ends_at"]}
            push(db, uid, payload["title"], payload["body"], payload["url"])
            for subscription in db.query(BrowserPushSubscription).filter(BrowserPushSubscription.user_id == uid):
                db.add(AttendancePushDelivery(attendance_id=record.id, subscription_id=subscription.id,
                    payload=payload, next_attempt_at=now))
        db.commit()


def deliver_push():
    settings = get_settings()
    if not settings.WEB_PUSH_PRIVATE_KEY or not settings.WEB_PUSH_PUBLIC_KEY or not settings.WEB_PUSH_SUBJECT:
        return
    from pywebpush import webpush, WebPushException
    with SessionLocal() as db:
        rows = db.query(AttendancePushDelivery).filter(AttendancePushDelivery.status == "pending",
            AttendancePushDelivery.next_attempt_at <= datetime.now(timezone.utc)
        ).with_for_update(skip_locked=True).limit(10).all()
        for row in rows:
            now = datetime.now(timezone.utc)
            record = db.get(AttendanceRecord, row.attendance_id)
            subscription = db.get(BrowserPushSubscription, row.subscription_id)
            user = db.get(User, subscription.user_id) if subscription else None
            expires = datetime.fromisoformat(row.payload["expires_at"])
            if (not subscription or not user or not user.is_active or record.employee.user_id != subscription.user_id
                    or record.clock_out or record.auto_closed_at or now >= expires):
                row.status = "cancelled"
                continue
            row.attempts += 1
            try:
                webpush(subscription_info={"endpoint": subscription.endpoint, "keys": subscription.keys},
                    data=json.dumps(row.payload), vapid_private_key=settings.WEB_PUSH_PRIVATE_KEY,
                    vapid_claims={"sub": settings.WEB_PUSH_SUBJECT}, timeout=5,
                    ttl=max(1, int((expires - now).total_seconds())))
                row.status = "sent"
            except WebPushException as exc:
                status = exc.response.status_code if exc.response is not None else None
                row.status = "failed" if row.attempts >= 5 or status in (400, 401, 403, 404, 410) else "pending"
                row.next_attempt_at = now + timedelta(seconds=min(300, 30 * 2 ** row.attempts))
                logger.warning("Attendance push delivery %s failed (status %s)", row.id, status)
            except Exception:
                row.status = "failed" if row.attempts >= 5 else "pending"
                row.next_attempt_at = now + timedelta(minutes=2)
                logger.warning("Attendance push delivery %s failed", row.id)
        db.commit()


async def run_attendance_worker(stop: asyncio.Event):
    while not stop.is_set():
        try:
            await asyncio.to_thread(process_attendance)
        except Exception:
            logger.exception("Attendance scheduler failed; will retry")
        try:
            await asyncio.to_thread(deliver_push)
        except Exception:
            logger.exception("Attendance push batch failed; will retry")
        try:
            await asyncio.wait_for(stop.wait(), timeout=30)
        except asyncio.TimeoutError:
            pass

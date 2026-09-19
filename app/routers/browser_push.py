from typing import Annotated
from urllib.parse import urlsplit
import base64

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from app.config import get_settings
from app.database import get_db
from app.dependencies import CurrentUser
from app.models import BrowserPushSubscription, User

router = APIRouter(prefix="/browser-push", tags=["Browser notifications"])
DB = Annotated[Session, Depends(get_db)]


class SubscriptionInput(BaseModel):
    endpoint: str = Field(max_length=2048)
    keys: dict[str, str]


def validate_subscription(payload):
    try:
        url = urlsplit(payload.endpoint)
        port = url.port
    except ValueError:
        raise HTTPException(422, "Invalid push service endpoint")
    # Only known browser push services may be contacted by this server.
    host = url.hostname or ""
    allowed = host in ("fcm.googleapis.com", "updates.push.services.mozilla.com", "web.push.apple.com") or host.endswith(".notify.windows.com")
    if url.scheme != "https" or port not in (None, 443) or url.username or url.password or not allowed:
        raise HTTPException(422, "Unsupported push service endpoint")
    try:
        for name, length in (("p256dh", 65), ("auth", 16)):
            value = payload.keys[name]
            decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
            if len(decoded) != length:
                raise ValueError()
    except (KeyError, ValueError, TypeError):
        raise HTTPException(422, "Invalid push subscription keys")


@router.get("/config")
def config(cu: CurrentUser):
    settings = get_settings()
    enabled = bool(settings.WEB_PUSH_PRIVATE_KEY and settings.WEB_PUSH_PUBLIC_KEY and settings.WEB_PUSH_SUBJECT)
    return {"enabled": enabled, "public_key": settings.WEB_PUSH_PUBLIC_KEY if enabled else None}


@router.post("/subscriptions")
def subscribe(payload: SubscriptionInput, cu: CurrentUser, db: DB):
    validate_subscription(payload)
    if not config(cu)["enabled"]:
        raise HTTPException(409, "Mobile reminders have not been configured by the administrator")
    db.query(User).filter(User.id == cu.id).with_for_update().one()
    row = db.query(BrowserPushSubscription).filter(BrowserPushSubscription.endpoint == payload.endpoint).first()
    if row and row.user_id != cu.id:
        raise HTTPException(409, "Unsubscribe this device from its previous account first")
    if not row:
        if db.query(BrowserPushSubscription).filter(BrowserPushSubscription.user_id == cu.id).count() >= 10:
            raise HTTPException(409, "Too many subscribed devices")
        row = BrowserPushSubscription(user_id=cu.id, endpoint=payload.endpoint)
        db.add(row)
    row.keys = {key: payload.keys[key] for key in ("p256dh", "auth")}
    db.commit()
    return {"message": "Reminders enabled"}


class UnsubscribeInput(BaseModel):
    endpoint: str


@router.post("/unsubscribe")
def unsubscribe(payload: UnsubscribeInput, cu: CurrentUser, db: DB):
    db.query(BrowserPushSubscription).filter(BrowserPushSubscription.user_id == cu.id,
        BrowserPushSubscription.endpoint == payload.endpoint).delete()
    db.commit()
    return {"message": "Reminders disabled"}

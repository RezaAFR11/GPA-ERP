"""SMTP delivery helpers for the persistent notification email outbox."""

import logging
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape

from app.config import get_settings


logger = logging.getLogger(__name__)


def send_email(
    to_email: str,
    subject: str,
    body_html: str,
    body_text: str | None = None,
) -> tuple[bool, str | None]:
    """Send one email and return a success flag plus an optional error."""
    settings = get_settings()
    if not settings.SMTP_HOST or not settings.SMTP_USER:
        return False, "SMTP is not configured"

    message = MIMEMultipart("alternative")
    message["Subject"] = subject
    message["From"] = settings.SMTP_FROM
    message["To"] = to_email

    if body_text:
        message.attach(MIMEText(body_text, "plain"))
    message.attach(MIMEText(body_html, "html"))

    try:
        if settings.SMTP_USE_TLS:
            context = ssl.create_default_context()
            with smtplib.SMTP(
                settings.SMTP_HOST,
                settings.SMTP_PORT,
                timeout=settings.SMTP_TIMEOUT_SECONDS,
            ) as smtp:
                smtp.ehlo()
                smtp.starttls(context=context)
                smtp.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
                smtp.sendmail(settings.SMTP_FROM, [to_email], message.as_string())
        else:
            with smtplib.SMTP(
                settings.SMTP_HOST,
                settings.SMTP_PORT,
                timeout=settings.SMTP_TIMEOUT_SECONDS,
            ) as smtp:
                smtp.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
                smtp.sendmail(settings.SMTP_FROM, [to_email], message.as_string())
        logger.info("Email sent to %s: %s", to_email, subject)
        return True, None
    except Exception as exc:
        logger.warning("Email send failed to %s: %s", to_email, exc)
        return False, str(exc)


def build_notification_email(
    title: str,
    body: str,
    link: str,
    base_url: str | None = None,
) -> tuple[str, str]:
    """Build safe HTML and plain-text bodies using the configured frontend URL."""
    configured_url = (base_url or get_settings().FRONTEND_URL).rstrip("/")
    if link.startswith("/"):
        full_link = f"{configured_url}{link}"
    elif link:
        full_link = link
    else:
        full_link = configured_url

    safe_title = escape(title)
    safe_body = escape(body)
    safe_link = escape(full_link, quote=True)
    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:520px;margin:0 auto;padding:24px;color:#1e293b">
      <div style="background:#0d9488;padding:16px 24px;border-radius:8px 8px 0 0">
        <span style="color:white;font-size:18px;font-weight:bold">GPA ERP</span>
      </div>
      <div style="background:#f8fafc;border:1px solid #e2e8f0;border-top:none;padding:24px;border-radius:0 0 8px 8px">
        <h2 style="margin:0 0 12px;color:#1e293b;font-size:16px">{safe_title}</h2>
        <p style="margin:0 0 20px;color:#475569;font-size:14px;line-height:1.6">{safe_body}</p>
        <a href="{safe_link}"
           style="display:inline-block;background:#0d9488;color:white;padding:10px 20px;border-radius:6px;text-decoration:none;font-size:14px;font-weight:600">
          Lihat Detail &rarr;
        </a>
      </div>
      <p style="margin:16px 0 0;color:#94a3b8;font-size:11px;text-align:center">
        GPA Cost Control ERP &middot; Notifikasi otomatis, jangan balas email ini.
      </p>
    </div>
    """
    text = f"{title}\n\n{body}\n\nLink: {full_link}"
    return html, text

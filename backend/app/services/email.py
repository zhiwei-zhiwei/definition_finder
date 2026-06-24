"""Outbound email — single dispatch point so a production swap-in is one file.

Two backends:
- "console" (dev default): logs the subject + text body (which carries the link
  or code) at INFO. Nothing leaves the box, so every flow is testable offline.
- "resend": POSTs to the Resend HTTPS API. Needs RESEND_API_KEY + EMAIL_FROM.

All call sites (`routes/auth.py`) go through `send_email`; the link/code helpers
just build the body, so adding "ses"/"smtp" later is a single new branch here.
"""
from __future__ import annotations

import logging

import httpx

from app.config import settings

log = logging.getLogger(__name__)

# Test/observability seam: the most recent message handed to the email layer,
# regardless of backend. Lets tests assert which link/code would have been sent
# without hitting the network. Not read by any production code path.
_last_sent: dict | None = None


def last_sent() -> dict | None:
    return _last_sent


def send_email(to: str, subject: str, *, html: str, text: str) -> None:
    global _last_sent
    _last_sent = {"to": to, "subject": subject, "html": html, "text": text}

    backend = settings.EMAIL_BACKEND
    if backend == "console":
        log.info("EMAIL backend=console to=%s subject=%s\n%s", to, subject, text)
        return
    if backend == "resend":
        if not settings.RESEND_API_KEY:
            raise RuntimeError("EMAIL_BACKEND=resend but RESEND_API_KEY is empty")
        resp = httpx.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {settings.RESEND_API_KEY}"},
            json={
                "from": settings.EMAIL_FROM,
                "to": [to],
                "subject": subject,
                "html": html,
                "text": text,
            },
            timeout=15.0,
        )
        if resp.status_code >= 300:
            raise RuntimeError(f"resend send failed: {resp.status_code} {resp.text}")
        return
    # Failing loud beats silently dropping a message.
    raise NotImplementedError(f"email backend not implemented: {backend}")


def _link_html(heading: str, lead: str, cta: str, link: str, footer: str) -> str:
    return (
        f"<div style='font-family:system-ui,sans-serif;max-width:480px;margin:0 auto'>"
        f"<h2 style='color:#1f2937'>{heading}</h2>"
        f"<p style='color:#374151'>{lead}</p>"
        f"<p><a href='{link}' style='display:inline-block;background:#2563eb;color:#fff;"
        f"text-decoration:none;padding:12px 20px;border-radius:8px;font-weight:600'>{cta}</a></p>"
        f"<p style='color:#6b7280;font-size:13px'>Or paste this link into your browser:<br>{link}</p>"
        f"<p style='color:#9ca3af;font-size:12px'>{footer}</p>"
        f"</div>"
    )


def send_verification_link(to: str, link: str) -> None:
    subject = "Verify your LexisAI account"
    text = (
        "Welcome to LexisAI Architect!\n\n"
        f"Confirm this email address to activate your account:\n{link}\n\n"
        "This link expires in 24 hours. If you didn't create an account, ignore this email."
    )
    html = _link_html(
        "Verify your email",
        "Confirm this email address to activate your LexisAI account.",
        "Verify email",
        link,
        "This link expires in 24 hours. If you didn't create an account, you can safely ignore this email.",
    )
    send_email(to, subject, html=html, text=text)


def send_reset_link(to: str, link: str) -> None:
    subject = "Reset your LexisAI password"
    text = (
        "We received a request to reset your LexisAI password.\n\n"
        f"Set a new password here:\n{link}\n\n"
        "This link expires in 30 minutes. If you didn't request this, ignore this email."
    )
    html = _link_html(
        "Reset your password",
        "We received a request to reset your LexisAI password.",
        "Set a new password",
        link,
        "This link expires in 30 minutes. If you didn't request this, you can safely ignore this email.",
    )
    send_email(to, subject, html=html, text=text)


def send_code(to: str, code: str, *, purpose: str = "login") -> None:
    subject = "Your LexisAI sign-in code"
    text = f"Your LexisAI {purpose} code is: {code}\n\nIt expires in 10 minutes."
    html = (
        f"<div style='font-family:system-ui,sans-serif'>"
        f"<p>Your LexisAI {purpose} code is:</p>"
        f"<p style='font-size:28px;font-weight:700;letter-spacing:6px'>{code}</p>"
        f"<p style='color:#6b7280'>It expires in 10 minutes.</p></div>"
    )
    send_email(to, subject, html=html, text=text)

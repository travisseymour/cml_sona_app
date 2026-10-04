"""Thin wrapper over the Resend HTTP API: send mail, fetch received mail,
and verify webhook signatures (Resend signs webhooks using the Svix scheme)."""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import time

import requests

API = "https://api.resend.com"
log = logging.getLogger(__name__)


class WebhookVerificationError(Exception):
    pass


def _headers() -> dict:
    return {"Authorization": f"Bearer {os.environ['RESEND_API_KEY']}"}


def send_email(to: str | list[str], subject: str, text: str, html: str | None = None,
               reply_to: str | None = None) -> None:
    to = [to] if isinstance(to, str) else to
    payload = {
        "from": os.environ.get("MAIL_FROM", ""),
        "to": to,
        "subject": subject,
        "text": text,
    }
    if html:
        payload["html"] = html
    if reply_to:
        payload["reply_to"] = reply_to

    if not os.environ.get("RESEND_API_KEY"):
        log.warning("RESEND_API_KEY not set; would have sent to %s: %s\n%s", to, subject, text)
        return
    if not payload["from"]:
        raise RuntimeError("MAIL_FROM is not set (e.g. 'CML Scheduler <cml@mail.example.org>')")
    r = requests.post(f"{API}/emails", json=payload, headers=_headers(), timeout=20)
    if r.status_code >= 300:
        log.error("Resend send failed (%s): %s", r.status_code, r.text)
        r.raise_for_status()


def get_received_email(email_id: str) -> dict:
    r = requests.get(f"{API}/emails/receiving/{email_id}", headers=_headers(), timeout=20)
    r.raise_for_status()
    return r.json()


def verify_webhook(body: bytes, headers, secret: str, tolerance: int = 300) -> None:
    """Raise WebhookVerificationError unless the Svix signature is valid."""
    msg_id = headers.get("svix-id")
    timestamp = headers.get("svix-timestamp")
    signatures = headers.get("svix-signature")
    if not (msg_id and timestamp and signatures):
        raise WebhookVerificationError("missing svix headers")
    try:
        ts = int(timestamp)
    except ValueError:
        raise WebhookVerificationError("bad timestamp")
    if abs(time.time() - ts) > tolerance:
        raise WebhookVerificationError("timestamp outside tolerance")

    key = base64.b64decode(secret.removeprefix("whsec_"))
    signed = f"{msg_id}.{timestamp}.".encode() + body
    expected = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()
    for sig in signatures.split():
        version, _, value = sig.partition(",")
        if version == "v1" and hmac.compare_digest(value, expected):
            return
    raise WebhookVerificationError("no matching signature")

"""Turn an incoming email into calendar records and RA notifications."""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from . import sona
from .models import InboundEmail, StudySession, db, utcnow
from .ras import load_ras
from .resend_client import send_email

log = logging.getLogger(__name__)


def fmt_when(s: StudySession) -> str:
    day = s.start.strftime("%A, %B %-d, %Y")
    t0 = s.start.strftime("%-I:%M %p")
    return f"{day}, {t0}" + (f" – {s.end.strftime('%-I:%M %p')}" if s.end else "")


def _admin() -> str | None:
    return os.environ.get("ADMIN_EMAIL")


def _calendar_link() -> str:
    url = os.environ.get("APP_URL", "").rstrip("/")
    return f"\n\nLab calendar: {url}/" if url else ""


def _find_matching_signup(n: sona.SonaNotice) -> StudySession | None:
    return (
        StudySession.query
        .filter_by(study=n.study, participant=n.participant, start=n.start, status="scheduled")
        .order_by(StudySession.id.desc())
        .first()
    )


def apply_notice(n: sona.SonaNotice) -> tuple[StudySession, bool]:
    """Record the notice. Returns (session, is_new_information)."""
    if n.kind == sona.SIGNUP:
        existing = _find_matching_signup(n)
        if existing:          # same email forwarded twice
            return existing, False
        s = StudySession(
            study=n.study, participant=n.participant, location=n.location,
            start=n.start, end=n.end, ra_initials=",".join(n.ra_initials),
        )
        db.session.add(s)
        return s, True

    # Cancellation
    s = _find_matching_signup(n)
    if s is None:
        # Sign-up predates this app (or its email was missed): record it as cancelled.
        already = StudySession.query.filter_by(
            study=n.study, participant=n.participant, start=n.start, status="cancelled"
        ).first()
        if already:
            return already, False
        s = StudySession(
            study=n.study, participant=n.participant, location=n.location,
            start=n.start, end=n.end, ra_initials=",".join(n.ra_initials),
        )
        db.session.add(s)
    elif n.ra_initials and not s.ra_initials:
        s.ra_initials = ",".join(n.ra_initials)
    s.status = "cancelled"
    s.cancelled_at = utcnow()
    return s, True


NO_SHOW_TYPES = ("excused", "unexcused")


def lab_now() -> datetime:
    """Current lab-local time, naive, comparable with session start/end (the server runs on UTC)."""
    tz = ZoneInfo(os.environ.get("LAB_TIMEZONE", "America/Los_Angeles"))
    return datetime.now(tz).replace(tzinfo=None)


def no_show_after(s: StudySession) -> datetime:
    """When a session can first be marked as a no-show: NO_SHOW_GRACE_MINUTES (default 5) after it starts."""
    return s.start + timedelta(minutes=float(os.environ.get("NO_SHOW_GRACE_MINUTES", "5")))


def mark_no_show(s: StudySession, kind: str) -> None:
    """Email the grad student about a no-show, then record it.

    The mark is only saved once the email has gone out, so a failed send can be retried.
    """
    if kind not in NO_SHOW_TYPES:
        raise ValueError(f"unknown no-show type {kind!r}")
    ras = load_ras()
    ra_names = ", ".join(f"{ras[i].name} (RA-{i})" if i in ras else f"RA-{i}" for i in s.ra_list)
    body = (
        "A previous experimental session:\n\n"
        f"Study:       {s.study}\n"
        f"When:        {fmt_when(s)}\n"
        f"Location:    {s.location}\n"
        f"RA:          {ra_names or '(none)'}\n"
        f"Participant: {s.participant}\n\n"
        f"has been marked as an {kind.upper()} No-Show."
        f"{_calendar_link()}\n"
    )
    to = os.environ.get("NO_SHOW_EMAIL", "cogmodlab@gmail.com")
    send_email(to, "Participant No-Show Marked", body, reply_to=_admin())
    s.no_show = kind
    s.no_show_at = utcnow()
    db.session.commit()


def notify(s: StudySession, kind: str, original_text: str) -> list[str]:
    """Email each RA tagged on the session. Returns problems worth telling the admin."""
    ras = load_ras()
    problems = []
    if not s.ra_list:
        problems.append("No RA tag (e.g. RA-ABC) was found in this email.")

    if kind == sona.SIGNUP:
        subject = f"New session: {s.study} – {s.start.strftime('%a %b %-d, %-I:%M %p')}"
        headline = "A participant signed up for a session you are running."
    else:
        subject = f"CANCELLED: {s.study} – {s.start.strftime('%a %b %-d, %-I:%M %p')}"
        headline = "A participant CANCELLED a session you were scheduled to run."

    for initials in s.ra_list:
        ra = ras.get(initials)
        if ra is None or not ra.email:
            problems.append(f"RA-{initials} is not in the RA list (ras.json), so nobody was notified for them.")
            continue
        if not ra.active:
            continue
        body = (
            f"Hi {ra.name},\n\n{headline}\n\n"
            f"Study:       {s.study}\n"
            f"When:        {fmt_when(s)}\n"
            f"Location:    {s.location}\n"
            f"Participant: {s.participant}\n"
            f"{_calendar_link()}\n\n"
            f"---- Original Sona message ----\n{original_text.strip()}\n"
        )
        try:
            send_email(ra.email, subject, body, reply_to=_admin())
        except Exception as e:  # keep going for the other RAs
            log.exception("notifying %s failed", initials)
            problems.append(f"Sending to RA-{initials} <{ra.email}> failed: {e}")
    return problems


def process_email(resend_id: str, from_addr: str, subject: str, text: str) -> InboundEmail:
    """Idempotently handle one received email (Resend may retry webhooks)."""
    record = InboundEmail.query.filter_by(resend_id=resend_id).first()
    if record and record.outcome != "error":
        return record
    if record is None:
        record = InboundEmail(resend_id=resend_id)
        db.session.add(record)
    record.from_addr = (from_addr or "")[:300]
    record.subject = (subject or "")[:500]

    notice = sona.parse(text)
    if notice is None:
        # Not a Sona notice -- e.g. Gmail's forwarding-confirmation email.
        # Pass it along to the admin so nothing is silently lost.
        record.outcome = "forwarded_to_admin"
        db.session.commit()
        if _admin():
            send_email(_admin(), f"[CML inbox] {subject}",
                       f"From: {from_addr}\nSubject: {subject}\n\n{text}")
        return record

    session, is_new = apply_notice(notice)
    db.session.flush()
    record.outcome = notice.kind if is_new else "duplicate"
    record.detail = f"session {session.id}"
    db.session.commit()

    if is_new:
        problems = notify(session, notice.kind, text)
        if problems and _admin():
            send_email(
                _admin(), f"[CML scheduler] attention needed: {subject}",
                "\n".join(problems) + f"\n\nSession: {session.study}, {fmt_when(session)}, "
                f"{session.location}\n\n---- Original ----\n{text}",
            )
    return record

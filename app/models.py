from __future__ import annotations

from datetime import datetime, timezone

from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class StudySession(db.Model):
    """One scheduled (or cancelled) experiment session.

    start/end are naive local times exactly as Sona reports them.
    """
    __tablename__ = "study_sessions"

    id = db.Column(db.Integer, primary_key=True)
    study = db.Column(db.String(300), nullable=False)
    participant = db.Column(db.String(300), nullable=False, default="")
    location = db.Column(db.String(300), nullable=False, default="")
    start = db.Column(db.DateTime, nullable=False, index=True)
    end = db.Column(db.DateTime, nullable=True)
    ra_initials = db.Column(db.String(100), nullable=False, default="")  # "TLS,MCP"
    status = db.Column(db.String(20), nullable=False, default="scheduled")  # or "cancelled"
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    cancelled_at = db.Column(db.DateTime, nullable=True)

    @property
    def ra_list(self) -> list[str]:
        return [r for r in self.ra_initials.split(",") if r]

    @property
    def cancelled(self) -> bool:
        return self.status == "cancelled"


class InboundEmail(db.Model):
    """Every email received through the webhook, for dedup and troubleshooting."""
    __tablename__ = "inbound_emails"

    id = db.Column(db.Integer, primary_key=True)
    resend_id = db.Column(db.String(100), unique=True, nullable=False)
    received_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    from_addr = db.Column(db.String(300), nullable=False, default="")
    subject = db.Column(db.String(500), nullable=False, default="")
    outcome = db.Column(db.String(50), nullable=False, default="pending")
    detail = db.Column(db.Text, nullable=False, default="")

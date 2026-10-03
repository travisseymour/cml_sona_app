from __future__ import annotations

from datetime import datetime, timezone

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import inspect, text

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
    no_show = db.Column(db.String(20), nullable=True)  # None, "excused" or "unexcused"
    no_show_at = db.Column(db.DateTime, nullable=True)

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


def add_missing_columns() -> list[str]:
    """Add nullable columns that create_all() can't add to existing tables.

    A stand-in for migrations: it only handles new nullable columns, which is
    all the schema has needed so far. Returns "table.column" for each one added.
    """
    insp = inspect(db.engine)
    added = []
    for table in db.metadata.sorted_tables:
        if not insp.has_table(table.name):
            continue
        have = {c["name"] for c in insp.get_columns(table.name)}
        for col in table.columns:
            if col.name in have or not col.nullable:
                continue
            ddl = col.type.compile(dialect=db.engine.dialect)
            with db.engine.begin() as conn:
                conn.execute(text(f'ALTER TABLE {table.name} ADD COLUMN {col.name} {ddl}'))
            added.append(f"{table.name}.{col.name}")
    return added

from __future__ import annotations

import hmac
import json
import logging
import os
import threading
import time
from datetime import datetime, timedelta
from functools import wraps

from flask import (Blueprint, abort, jsonify, redirect, render_template, request,
                   session, url_for)

from .ingest import NO_SHOW_TYPES, lab_now, mark_no_show, no_show_after, process_email
from .models import InboundEmail, StudySession, db
from .ras import color_for, lighten, load_ras, readable_text
from .resend_client import WebhookVerificationError, get_received_email, verify_webhook
from .sona import html_to_text

bp = Blueprint("main", __name__)
log = logging.getLogger(__name__)


def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if os.environ.get("CALENDAR_PASSWORD") and not session.get("ok"):
            if request.path.startswith("/api/"):
                abort(401)
            return redirect(url_for("main.login", next=request.path))
        return view(*args, **kwargs)
    return wrapper


MAX_LOGIN_FAILURES = 5
LOCKOUT_SECONDS = 15 * 60

# ip -> timestamps of recent failed logins (per process; the app runs one worker)
_login_failures: dict[str, list[float]] = {}
_login_failures_lock = threading.Lock()


def client_ip() -> str:
    """The visitor's IP. Behind a trusted reverse proxy, read it from X-Real-IP."""
    if os.environ.get("CALENDAR_TRUST_PROXY") == "1":
        return request.headers.get("X-Real-IP", request.remote_addr or "")
    return request.remote_addr or ""


def _recent_failures(ip: str, now: float) -> list[float]:
    recent = [t for t in _login_failures.get(ip, []) if now - t < LOCKOUT_SECONDS]
    if recent:
        _login_failures[ip] = recent
    else:
        _login_failures.pop(ip, None)
    return recent


def safe_next(nxt: str) -> str:
    """Only redirect within this site. Browsers read "\\" as "/" and drop tabs and
    newlines, so "/\\evil.com" or "/\\t/evil.com" would otherwise leave the site."""
    cleaned = "".join(c for c in nxt if c.isprintable() and not c.isspace()).replace("\\", "/")
    return cleaned if cleaned.startswith("/") and not cleaned.startswith("//") else "/"


@bp.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        ip = client_ip()
        now = time.time()
        with _login_failures_lock:
            locked = len(_recent_failures(ip, now)) >= MAX_LOGIN_FAILURES
        expected = os.environ.get("CALENDAR_PASSWORD", "")
        if locked:
            log.warning("Login refused for %s: too many failed attempts", ip)
            error = "Too many failed attempts. Try again in 15 minutes."
        elif hmac.compare_digest(request.form.get("password", "").encode(), expected.encode()):
            with _login_failures_lock:
                _login_failures.pop(ip, None)
            session.clear()
            session.permanent = True
            session["ok"] = True
            return redirect(safe_next(request.args.get("next", "/")))
        else:
            with _login_failures_lock:
                _login_failures.setdefault(ip, []).append(now)
                # Keep memory bounded if many IPs are guessing
                if len(_login_failures) > 10_000:
                    for other in list(_login_failures):
                        _recent_failures(other, now)
            error = "Wrong password."
    return render_template("login.html", error=error)


@bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("main.login"))


@bp.route("/")
@login_required
def calendar():
    ras = load_ras()
    legend = [{"initials": r.initials, "name": r.name, "color": r.color}
              for r in ras.values() if r.active]
    return render_template("calendar.html", legend=legend)


def _parse_range_arg(value: str | None, default: datetime) -> datetime:
    if not value:
        return default
    # FullCalendar sends e.g. 2026-09-28T00:00:00-07:00; we store naive local times.
    return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)


@bp.route("/api/events")
@login_required
def events():
    now = datetime.now()
    start = _parse_range_arg(request.args.get("start"), now - timedelta(days=45))
    end = _parse_range_arg(request.args.get("end"), now + timedelta(days=45))
    ras = load_ras()

    rows = (StudySession.query
            .filter(StudySession.start >= start, StudySession.start < end)
            .order_by(StudySession.start).all())
    out = []
    for s in rows:
        primary = s.ra_list[0] if s.ra_list else None
        color = color_for(primary, ras)
        if s.cancelled:
            bg, text = lighten(color), "#333333"
        else:
            bg, text = color, readable_text(color)
        ra_label = ", ".join(s.ra_list) or "no RA"
        out.append({
            "id": s.id,
            "title": f"{ra_label} · {s.study}",
            "start": s.start.isoformat(),
            "end": (s.end or s.start + timedelta(hours=1)).isoformat(),
            "backgroundColor": bg,
            "borderColor": color,
            "textColor": text,
            "classNames": (["is-cancelled"] if s.cancelled else [])
                          + (["is-no-show"] if s.no_show else []),
            "extendedProps": {
                "study": s.study,
                "location": s.location,
                "participant": s.participant,
                "ras": s.ra_list,
                "raNames": [ras[i].name if i in ras else i for i in s.ra_list],
                "status": s.status,
                "noShow": s.no_show,
                "noShowAfter": no_show_after(s).isoformat(),
            },
        })
    return jsonify(out)


@bp.route("/api/events/<int:session_id>/no-show", methods=["POST"])
@login_required
def no_show(session_id: int):
    # Requiring a JSON body means a cross-site form can't trigger this.
    if not request.is_json:
        abort(415)
    kind = (request.get_json(silent=True) or {}).get("type")
    if kind not in NO_SHOW_TYPES:
        return jsonify(error="type must be 'excused' or 'unexcused'"), 400
    s = db.session.get(StudySession, session_id) or abort(404)
    if s.cancelled:
        return jsonify(error="This session was cancelled."), 409
    if lab_now() < no_show_after(s):
        return jsonify(error="It's too early to mark this session as a no-show."), 409
    if s.no_show:
        return jsonify(error=f"Already marked as an {s.no_show} no-show."), 409
    try:
        mark_no_show(s, kind)
    except Exception:
        db.session.rollback()
        log.exception("no-show email for session %s failed", session_id)
        return jsonify(error="The email could not be sent, so nothing was marked. Please try again."), 502
    return jsonify(noShow=s.no_show)


@bp.route("/webhooks/resend", methods=["POST"])
def resend_webhook():
    secret = os.environ.get("RESEND_WEBHOOK_SECRET")
    body = request.get_data()
    if secret:
        try:
            verify_webhook(body, request.headers, secret)
        except WebhookVerificationError as e:
            log.warning("rejected webhook: %s", e)
            abort(401)
    elif os.environ.get("RAILWAY_ENVIRONMENT"):
        log.error("RESEND_WEBHOOK_SECRET is not set; refusing webhooks in production")
        abort(503)

    event = json.loads(body or b"{}")
    if event.get("type") != "email.received":
        return jsonify(ignored=event.get("type")), 200

    email_id = event["data"]["email_id"]
    email = get_received_email(email_id)
    text = email.get("text") or html_to_text(email.get("html") or "")
    try:
        record = process_email(email_id, email.get("from", ""), email.get("subject", ""), text)
    except Exception as e:
        db.session.rollback()
        log.exception("processing %s failed", email_id)
        rec = InboundEmail.query.filter_by(resend_id=email_id).first() or InboundEmail(resend_id=email_id)
        rec.outcome, rec.detail = "error", repr(e)[:2000]
        db.session.add(rec)
        db.session.commit()
        return jsonify(error=str(e)), 500  # Resend will retry
    return jsonify(outcome=record.outcome), 200


@bp.route("/healthz")
def healthz():
    # Railway sets the commit SHA for GitHub deploys; handy for checking what's live.
    sha = os.environ.get("RAILWAY_GIT_COMMIT_SHA", "")[:7]
    return f"ok {sha}".strip()

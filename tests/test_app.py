import base64
import hashlib
import hmac
import json
import time
from datetime import datetime
from unittest import mock

import pytest

from app import sona
from app.resend_client import WebhookVerificationError, verify_webhook

SIGNUP = (
    "This email is to notify you of a new sign-up for one of your studies. "
    "The participant Jamie Testperson <jtest@example.edu> signed up for the study: New Ideas For Group "
    "Learning Online. The study is scheduled to take place on Friday, June 8, 2018 "
    "12:30 PM - 1:30 PM in the location: SS2 443D (RA-TLS). Please log in to the "
    "website for further information: https://ucsc.sona-systems.com"
)
CANCEL = (
    "This email is to notify you of a cancellation for one of your studies.\r\n\r\n"
    "The participant Jamie Testperson cancelled his/her sign-up for the study: New Ideas For Group\n"
    "Learning Online.\n\nThe study was scheduled to take place on Friday, June 8, 2018\n"
    "12:30 PM - 1:30 PM in the location: SS2 443D (RA-TLS).\n\nPlease log in to the "
    "website for further information: https://ucsc.sona-systems.com"
)
RAS = {"TLS": {"name": "Taylor", "email": "tls@example.edu"},
       "MCP": {"name": "Morgan", "email": "mcp@example.edu"}}


def test_parse_signup():
    n = sona.parse(SIGNUP)
    assert n.kind == sona.SIGNUP
    assert n.study == "New Ideas For Group Learning Online"
    assert n.participant == "Jamie Testperson"
    assert n.start == datetime(2018, 6, 8, 12, 30)
    assert n.end == datetime(2018, 6, 8, 13, 30)
    assert n.location == "SS2 443D (RA-TLS)"
    assert n.ra_initials == ["TLS"]


def test_parse_cancel_with_line_breaks_and_quotes():
    quoted = "\n".join("> " + line for line in CANCEL.splitlines())
    n = sona.parse("---------- Forwarded message ---------\n" + quoted)
    assert n.kind == sona.CANCELLATION
    assert n.study == "New Ideas For Group Learning Online"
    assert n.start == datetime(2018, 6, 8, 12, 30)


def test_multiple_and_messy_ra_tags():
    assert sona.find_ra_initials("RA-TLS and ra – mcp, also RA-TLS") == ["TLS", "MCP"]


def test_non_sona_email_is_ignored():
    assert sona.parse("Gmail Forwarding Confirmation - Receive Mail from x@gmail.com") is None


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("CALENDAR_PASSWORD", raising=False)
    monkeypatch.setenv("RA_CONFIG_JSON", json.dumps(RAS))
    monkeypatch.setenv("ADMIN_EMAIL", "admin@example.edu")
    from app import create_app
    app = create_app()
    with app.app_context():
        yield app


def test_signup_then_cancel_flow(app):
    from app import ingest
    from app.models import StudySession
    with mock.patch.object(ingest, "send_email") as send:
        assert ingest.process_email("e1", "sona", "Study Sign-Up", SIGNUP).outcome == "signup"
        assert send.call_args_list[0].args[0] == "tls@example.edu"
        # the same webhook delivered twice does nothing
        ingest.process_email("e1", "sona", "Study Sign-Up", SIGNUP)
        # the same email forwarded twice is a duplicate
        assert ingest.process_email("e2", "sona", "Study Sign-Up", SIGNUP).outcome == "duplicate"
        assert send.call_count == 1

        assert ingest.process_email("e3", "sona", "Study Cancellation", CANCEL).outcome == "cancellation"
        assert send.call_count == 2
        assert send.call_args.args[1].startswith("CANCELLED")

    sessions = StudySession.query.all()
    assert len(sessions) == 1 and sessions[0].status == "cancelled"

    events = app.test_client().get("/api/events?start=2018-06-01T00:00:00-07:00&end=2018-06-30").get_json()
    assert events[0]["classNames"] == ["is-cancelled"]


def test_unknown_ra_and_non_sona_go_to_admin(app):
    from app import ingest
    with mock.patch.object(ingest, "send_email") as send:
        ingest.process_email("x1", "sona", "Study Sign-Up", SIGNUP.replace("RA-TLS", "RA-ZZZ"))
        assert send.call_args.args[0] == "admin@example.edu"
        assert "RA-ZZZ" in send.call_args.args[2]

        rec = ingest.process_email("x2", "forwarding-noreply@google.com", "Gmail Forwarding Confirmation", "code 123")
        assert rec.outcome == "forwarded_to_admin"
        assert send.call_args.args[0] == "admin@example.edu"


def test_webhook_signature():
    secret_bytes = b"supersecretkey-supersecret"
    secret = "whsec_" + base64.b64encode(secret_bytes).decode()
    body = b'{"type":"email.received"}'
    ts = str(int(time.time()))
    sig = base64.b64encode(hmac.new(secret_bytes, f"m1.{ts}.".encode() + body, hashlib.sha256).digest()).decode()
    headers = {"svix-id": "m1", "svix-timestamp": ts, "svix-signature": f"v1,bogus v1,{sig}"}
    verify_webhook(body, headers, secret)
    with pytest.raises(WebhookVerificationError):
        verify_webhook(body + b" ", headers, secret)


def test_roster_required_on_railway(monkeypatch):
    from app.ras import RosterError, load_ras
    monkeypatch.setenv("RAILWAY_ENVIRONMENT", "production")
    monkeypatch.delenv("RA_CONFIG_JSON", raising=False)
    with pytest.raises(RosterError, match="RA_CONFIG_JSON is not set"):
        load_ras()


def test_roster_errors_do_not_leak_emails(monkeypatch):
    from app.ras import RosterError, load_ras
    monkeypatch.setenv("RA_CONFIG_JSON", '{"TLS": "secret@ucsc.edu",}')
    with pytest.raises(RosterError) as e:
        load_ras()
    assert "secret@" not in str(e.value)

    monkeypatch.setenv("RA_CONFIG_JSON", '{"TLS": {"name": "Taylor"}}')
    with pytest.raises(RosterError, match="'TLS' needs an \"email\""):
        load_ras()


def test_roster_short_form(monkeypatch):
    from app.ras import load_ras
    monkeypatch.setenv("RA_CONFIG_JSON", '{"ra-tls": "tls@ucsc.edu"}')
    assert load_ras()["TLS"].email == "tls@ucsc.edu"


def test_ra_tags_that_are_ordinary_words():
    text = ("The participant Sam And signed up for the study: Memory AND Attention. ... "
            "in the location: SS2 443C (RA-AND). Please log in")
    assert sona.find_ra_initials(text) == ["AND"]
    assert sona.find_ra_initials("Memory and Attention, room AND, extra-and") == []


def test_no_show(app, monkeypatch):
    from app import ingest, routes
    from app.models import StudySession
    with mock.patch.object(ingest, "send_email"):
        ingest.process_email("n1", "sona", "Study Sign-Up", SIGNUP)
    sid = StudySession.query.one().id
    client = app.test_client()
    url = f"/api/events/{sid}/no-show"

    assert client.post(url, data={"type": "excused"}).status_code == 415
    # session starts 12:30; marking opens 5 minutes later
    with mock.patch.object(routes, "lab_now", return_value=datetime(2018, 6, 8, 12, 34)):
        assert client.post(url, json={"type": "excused"}).status_code == 409
    monkeypatch.setenv("NO_SHOW_GRACE_MINUTES", "3")
    with mock.patch.object(routes, "lab_now", return_value=datetime(2018, 6, 8, 12, 34)), \
            mock.patch.object(ingest, "send_email"):
        assert client.post(url, json={"type": "excused"}).status_code == 200
    from app.models import db
    StudySession.query.one().no_show = None
    db.session.commit()
    assert client.post(url, json={"type": "maybe"}).status_code == 400

    # a failed send marks nothing
    with mock.patch.object(ingest, "send_email", side_effect=RuntimeError("down")):
        assert client.post(url, json={"type": "unexcused"}).status_code == 502
    assert StudySession.query.one().no_show is None

    with mock.patch.object(ingest, "send_email") as send:
        assert client.post(url, json={"type": "unexcused"}).get_json() == {"noShow": "unexcused"}
        to, subject, body = send.call_args.args
        assert to == "cogmodlab@gmail.com" and subject == "Participant No-Show Marked"
        assert body.startswith("A previous experimental session:")
        assert "Jamie Testperson" in body and "Taylor (RA-TLS)" in body
        assert "has been marked as an UNEXCUSED No-Show." in body
        # can't be marked twice
        assert client.post(url, json={"type": "excused"}).status_code == 409
        assert send.call_count == 1

    events = client.get("/api/events?start=2018-06-01T00:00:00-07:00&end=2018-06-30").get_json()
    assert events[0]["classNames"] == ["is-no-show"]
    assert events[0]["extendedProps"]["noShow"] == "unexcused"


def test_missing_columns_added_to_old_table(tmp_path, monkeypatch):
    import sqlite3
    tmp = tmp_path / "old"
    tmp.mkdir()
    con = sqlite3.connect(tmp / "cml.db")
    con.execute("CREATE TABLE study_sessions (id INTEGER PRIMARY KEY, study VARCHAR(300) NOT NULL, "
                "participant VARCHAR(300) NOT NULL, location VARCHAR(300) NOT NULL, start DATETIME NOT NULL, "
                "\"end\" DATETIME, ra_initials VARCHAR(100) NOT NULL, status VARCHAR(20) NOT NULL, "
                "created_at DATETIME NOT NULL, cancelled_at DATETIME)")
    con.commit()
    con.close()
    monkeypatch.setenv("DATA_DIR", str(tmp))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("RA_CONFIG_JSON", json.dumps(RAS))
    from app import create_app
    from app.models import add_missing_columns
    app = create_app()
    cols = [r[1] for r in sqlite3.connect(tmp / "cml.db").execute("PRAGMA table_info(study_sessions)")]
    assert "no_show" in cols and "no_show_at" in cols
    with app.app_context():
        assert add_missing_columns() == []

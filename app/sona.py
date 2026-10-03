"""Parse Sona Systems sign-up / cancellation notification emails.

Typical bodies (whitespace varies, and forwarded copies may wrap lines):

    This email is to notify you of a new sign-up for one of your studies.
    The participant Jamie Testperson signed up for the study: New Ideas For Group
    Learning Online. The study is scheduled to take place on Friday, June 8,
    2019 12:30 PM - 1:30 PM in the location: SS2 443D (RA-TLS). Please log in
    to the website for further information: https://ucsc.sona-systems.com

    This email is to notify you of a cancellation for one of your studies.
    The participant Jamie Testperson cancelled his/her sign-up for the study: ...
    The study was scheduled to take place on ...
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from datetime import datetime

SIGNUP = "signup"
CANCELLATION = "cancellation"

# RA tags look like "RA-TLS"; tolerate "RA - TLS", "RA–TLS", and lower case.
RA_TAG = re.compile(r"\bRA\s*[-–—]\s*([A-Za-z]{2,5})\b", re.I)

_DATE_TIME = re.compile(
    r"\bon\s+(?:[A-Za-z]+,\s*)?"                      # optional weekday
    r"([A-Za-z]+\s+\d{1,2},\s*\d{4})\s+"              # June 8, 2019
    r"(\d{1,2}:\d{2}\s*[AaPp]\.?[Mm]\.?)"             # 12:30 PM
    r"(?:\s*[-–—]\s*(\d{1,2}:\d{2}\s*[AaPp]\.?[Mm]\.?))?"  # - 1:30 PM
)
_PARTICIPANT = re.compile(
    r"The participant\s+(.+?)\s+(?:signed up|cancell?ed|has cancell?ed)", re.I
)
_STUDY = re.compile(
    r"for the study:\s*(.+?)\.?\s+The study (?:is|was|has been) scheduled", re.I
)
_LOCATION = re.compile(
    r"in the location:\s*(.+?)\.?\s*(?:Please log in|$)", re.I
)


@dataclass
class SonaNotice:
    kind: str                 # SIGNUP or CANCELLATION
    study: str
    participant: str
    start: datetime
    end: datetime | None
    location: str
    ra_initials: list[str] = field(default_factory=list)


def html_to_text(s: str) -> str:
    s = re.sub(r"(?is)<(script|style).*?</\1>", " ", s)
    s = re.sub(r"(?i)<br\s*/?>|</p>|</div>", "\n", s)
    s = re.sub(r"<[^>]+>", " ", s)
    return html.unescape(s)


def normalize(text: str) -> str:
    text = text.replace("#015#012", " ")
    # Gmail forwards quote with leading "> "
    text = re.sub(r"(?m)^\s*>+\s?", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def find_ra_initials(text: str) -> list[str]:
    """All distinct RA initials in the text, upper-cased, in order of appearance."""
    seen: list[str] = []
    for m in RA_TAG.finditer(text):
        initials = m.group(1).upper()
        if initials not in seen:
            seen.append(initials)
    return seen


def _parse_time(date_part: str, time_part: str) -> datetime:
    date_part = re.sub(r",\s*", ", ", date_part.strip())
    time_part = re.sub(r"\s*([AaPp])\.?[Mm]\.?", r" \1M", time_part.strip()).upper()
    return datetime.strptime(f"{date_part} {time_part}", "%B %d, %Y %I:%M %p")


def kind_of(text: str) -> str | None:
    if re.search(r"notify you of a (?:new )?cancell?ation", text, re.I) or re.search(
        r"cancell?ed (?:his/her|their|the) sign-?up", text, re.I
    ):
        return CANCELLATION
    if re.search(r"notify you of a new sign-?up", text, re.I) or re.search(
        r"\bsigned up for the study", text, re.I
    ):
        return SIGNUP
    return None


def parse(text: str) -> SonaNotice | None:
    """Return a SonaNotice, or None if this doesn't look like a Sona notice."""
    text = normalize(text)
    kind = kind_of(text)
    if kind is None:
        return None

    when = _DATE_TIME.search(text)
    study = _STUDY.search(text)
    if not when or not study:
        return None

    start = _parse_time(when.group(1), when.group(2))
    end = _parse_time(when.group(1), when.group(3)) if when.group(3) else None
    if end is not None and end <= start:
        end = None

    participant = _PARTICIPANT.search(text)
    participant_name = participant.group(1) if participant else ""
    participant_name = re.sub(r"\s*<[^>]*>", "", participant_name).strip()

    location = _LOCATION.search(text, when.end())
    return SonaNotice(
        kind=kind,
        study=study.group(1).strip(),
        participant=participant_name,
        start=start,
        end=end,
        location=location.group(1).strip() if location else "",
        ra_initials=find_ra_initials(text),
    )

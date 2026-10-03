"""Research-assistant roster: initials -> name, email, calendar color.

The roster holds RA email addresses, so it never lives in the repo. In
production it comes from the RA_CONFIG_JSON environment variable (a Railway
service variable). For local development, a git-ignored ras.json in the
project root (or the file named by RA_CONFIG_PATH) is used instead, falling
back to ras.example.json.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Distinct, reasonably saturated colors that keep white text readable.
PALETTE = [
    "#1f77b4", "#d62728", "#2ca02c", "#9467bd", "#e377c2", "#8c564b",
    "#ff7f0e", "#17becf", "#7f7f7f", "#bcbd22", "#3949ab", "#00897b",
]


@dataclass
class RA:
    initials: str
    name: str
    email: str
    color: str
    active: bool = True


class RosterError(RuntimeError):
    pass


def _on_railway() -> bool:
    return bool(os.environ.get("RAILWAY_ENVIRONMENT"))


def _raw_config() -> tuple[str, str]:
    """Return (json_text, where_it_came_from)."""
    env = os.environ.get("RA_CONFIG_JSON", "").strip()
    if env:
        return env, "RA_CONFIG_JSON"
    if _on_railway():
        raise RosterError("RA_CONFIG_JSON is not set. Add it as a Railway service variable.")
    path = Path(os.environ.get("RA_CONFIG_PATH", PROJECT_ROOT / "ras.json"))
    if not path.exists():
        path = PROJECT_ROOT / "ras.example.json"
    return path.read_text(), str(path)


@lru_cache(maxsize=4)
def _parse(text: str, source: str) -> dict[str, RA]:
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as e:
        # Don't echo the value itself: it contains email addresses.
        raise RosterError(f"{source} is not valid JSON (line {e.lineno}, column {e.colno}: {e.msg}).") from None
    if not isinstance(raw, dict):
        raise RosterError(f'{source} must be a JSON object like {{"TLS": {{"name": ..., "email": ...}}}}.')

    ras: dict[str, RA] = {}
    for i, (initials, info) in enumerate(sorted(raw.items())):
        if isinstance(info, str):          # allow the short form "TLS": "x@y.edu"
            info = {"email": info}
        if not isinstance(info, dict) or "@" not in str(info.get("email", "")):
            raise RosterError(f"{source}: entry {initials!r} needs an \"email\" address.")
        initials = initials.upper().removeprefix("RA-")
        ras[initials] = RA(
            initials=initials,
            name=info.get("name", initials),
            email=info["email"].strip(),
            color=info.get("color") or PALETTE[i % len(PALETTE)],
            active=info.get("active", True),
        )
    return ras


def load_ras() -> dict[str, RA]:
    return _parse(*_raw_config())


def color_for(initials: str | None, ras: dict[str, RA]) -> str:
    if initials and initials in ras:
        return ras[initials].color
    if not initials:
        return "#607d8b"
    # Unknown RA: stable color derived from the initials
    h = int(hashlib.md5(initials.encode()).hexdigest()[:6], 16)
    return PALETTE[h % len(PALETTE)]


def lighten(hex_color: str, amount: float = 0.65) -> str:
    """Mix a color with white; amount=1 gives white."""
    hex_color = hex_color.lstrip("#")
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (0, 2, 4))
    r, g, b = (round(c + (255 - c) * amount) for c in (r, g, b))
    return f"#{r:02x}{g:02x}{b:02x}"


def readable_text(hex_color: str) -> str:
    hex_color = hex_color.lstrip("#")
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (0, 2, 4))
    luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255
    return "#111111" if luminance > 0.6 else "#ffffff"

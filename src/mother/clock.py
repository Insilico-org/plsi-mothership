"""The one clock (UTC), so tests can fix time."""

from __future__ import annotations

from datetime import datetime, timezone


def now() -> datetime:
    return datetime.now(timezone.utc)


def stamp() -> str:
    return now().isoformat(timespec="seconds")


def month() -> str:
    return now().strftime("%Y-%m")

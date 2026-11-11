"""The one clock (UTC), so tests can fix time."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from mother.errors import UsageError

DAY = re.compile(r"\d{4}-\d{2}-\d{2}")


def now() -> datetime:
    return datetime.now(timezone.utc)


def stamp() -> str:
    return now().isoformat(timespec="seconds")


def month() -> str:
    return now().strftime("%Y-%m")


def since(days: int) -> str:
    return (now() - timedelta(days=days)).isoformat(timespec="seconds")


def day(value: str, flag: str) -> str:
    if not DAY.fullmatch(value):
        raise UsageError(f"{flag} is YYYY-MM-DD")
    return value

"""The append-only access log `audit/audit.jsonl`: one {at, actor, action, target} per CLI read or write. The actor is
env MOTHER_ACTOR (the steward or bot running the command), else "mother". Targets are receipts, ids and versions,
never a contributor pseudonym."""

from __future__ import annotations

import os

from mother import clock
from mother.store import Store, append_jsonl, read_jsonl

PATH = "audit/audit.jsonl"


def actor() -> str:
    return os.environ.get("MOTHER_ACTOR") or "mother"


def record(store: Store, action: str, target: str) -> None:
    append_jsonl(store, PATH, {"at": clock.stamp(), "actor": actor(), "action": action, "target": target})


def entries(store: Store) -> list[dict[str, str]]:
    return read_jsonl(store, PATH)

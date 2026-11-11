"""The append-only access log `audit/audit.jsonl`: one {at, actor, action, target} per CLI read or write. The actor is
env MOTHER_ACTOR (the steward or bot running the command), else "mother". Targets are receipts, ids and versions,
never a contributor pseudonym. Only a name in env MOTHER_STEWARDS decides on a finding, a research note or a registry
update."""

from __future__ import annotations

import os

from mother import clock
from mother.errors import Refused
from mother.store import Store, append_jsonl, read_jsonl

PATH = "audit/audit.jsonl"


def actor() -> str:
    return os.environ.get("MOTHER_ACTOR") or "mother"


def require_steward(name: str) -> str:
    """`name` if it is one of MOTHER_STEWARDS."""
    stewards = [s.strip() for s in os.environ.get("MOTHER_STEWARDS", "").split(",") if s.strip()]
    if not stewards:
        raise Refused("MOTHER_STEWARDS is not set", code="STEWARDS_NOT_CONFIGURED")
    if name not in stewards:
        raise Refused("not one of MOTHER_STEWARDS", code="NOT_A_STEWARD")
    return name


def record(store: Store, action: str, target: str) -> None:
    append_jsonl(store, PATH, {"at": clock.stamp(), "actor": actor(), "action": action, "target": target})


def entries(store: Store) -> list[dict[str, str]]:
    return read_jsonl(store, PATH)

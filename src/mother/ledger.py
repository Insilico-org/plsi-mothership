"""The findings ledger `findings/ledger.jsonl`: append-only entries {at, type, id, ...}. Nothing is edited; a status is
derived from the entries (mother.findings)."""

from __future__ import annotations

from typing import Any

from mother import clock
from mother.store import Store, append_jsonl, read_jsonl
from mother.vocab import Entry

PATH = "findings/ledger.jsonl"


def entries(store: Store, type_: Entry | None = None, id_: str | None = None) -> list[dict[str, Any]]:
    return [e for e in read_jsonl(store, PATH)
            if (type_ is None or e["type"] == type_) and (id_ is None or e["id"] == id_)]


def append(store: Store, type_: Entry, id_: str, **fields: Any) -> dict[str, Any]:
    entry = {"at": clock.stamp(), "type": type_.value, "id": id_, **fields}
    append_jsonl(store, PATH, entry)
    return entry


def first(store: Store, type_: Entry, id_: str) -> dict[str, Any] | None:
    found = entries(store, type_, id_)
    return found[0] if found else None

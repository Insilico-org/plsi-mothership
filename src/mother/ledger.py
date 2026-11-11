"""The findings ledger `findings/ledger.jsonl`: append-only entries {at, type, id, ...}. Nothing is edited; a status is
derived from the entries (mother.findings)."""

from __future__ import annotations

from typing import Any

from mother import clock
from mother.store import Store, append_jsonl, read_jsonl
from mother.vocab import Entry

PATH = "findings/ledger.jsonl"


def select(log: list[dict[str, Any]], type_: Entry | None = None, id_: str | None = None) -> list[dict[str, Any]]:
    """The entries of a read ledger with this type and id (None: any)."""
    return [e for e in log if (type_ is None or e["type"] == type_) and (id_ is None or e["id"] == id_)]


def entries(store: Store, type_: Entry | None = None, id_: str | None = None) -> list[dict[str, Any]]:
    return select(read_jsonl(store, PATH), type_, id_)


def append(store: Store, type_: Entry, id_: str, **fields: Any) -> dict[str, Any]:
    entry = {"at": clock.stamp(), "type": type_.value, "id": id_, **fields}
    append_jsonl(store, PATH, entry)
    return entry


def first(store: Store, type_: Entry, id_: str) -> dict[str, Any] | None:
    return next(iter(entries(store, type_, id_)), None)

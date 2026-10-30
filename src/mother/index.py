"""The contributor index `index.json`: contributor_id -> {receipts, kinds, consent_version, status, last_packed}. It is
never joined with anything that names a professional."""

from __future__ import annotations

from typing import Any

from mother.store import Store, read_json, write_json
from mother.vocab import ContributorStatus

PATH = "index.json"


def load(store: Store) -> dict[str, dict[str, Any]]:
    return read_json(store, PATH, {})


def save(store: Store, index: dict[str, dict[str, Any]]) -> None:
    write_json(store, PATH, index)


def add_contribution(store: Store, doc: dict[str, Any], receipt: str) -> dict[str, Any]:
    """Index a stored contribution; a contribution after a stop means the contributor re-joined."""
    index = load(store)
    entry = index.get(doc["contributor_id"], {"receipts": []})
    entry |= {"receipts": [*entry["receipts"], receipt], "kinds": sorted(doc["kinds"]),
              "consent_version": doc["consent_version"], "status": ContributorStatus.ACTIVE.value,
              "last_packed": max(entry.get("last_packed", ""), doc["packed"])}
    index[doc["contributor_id"]] = entry
    save(store, index)
    return entry


def summary(index: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Counts only: contributors by status and by consented kind."""
    by_status: dict[str, int] = {}
    by_kind: dict[str, int] = {}
    for entry in index.values():
        by_status[entry["status"]] = by_status.get(entry["status"], 0) + 1
        for kind in entry.get("kinds", []):
            by_kind[kind] = by_kind.get(kind, 0) + 1
    return {"contributors": len(index), "by_status": by_status, "by_kind": by_kind}

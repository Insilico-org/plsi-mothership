"""The contributor index `index.json`: contributor_id -> {receipts, kinds, consent_version, status, last_packed}, and
the stored contributions it points to (`intake/<contributor_id>/<receipt>.json`). It is never joined with anything
that names a professional."""

from __future__ import annotations

import json
from collections import Counter
from typing import Any

from mother import audit
from mother.store import Store, read_json, write_json
from mother.vocab import ContributorStatus

PATH = "index.json"


def package_path(contributor_id: str, receipt: str) -> str:
    return f"intake/{contributor_id}/{receipt}.json"


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


def active(store: Store) -> dict[str, dict[str, Any]]:
    """The entries of contributors who have not withdrawn (`stop`): the only ones releases and the catalog count."""
    return {cid: e for cid, e in load(store).items() if e.get("status") == ContributorStatus.ACTIVE}


def packages(store: Store, cid: str, entry: dict[str, Any]) -> list[dict[str, Any]]:
    return [json.loads(store.read(package_path(cid, r))) for r in entry["receipts"]]


def summary(index: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Counts only: contributors by status and by consented kind."""
    by_status = Counter(entry["status"] for entry in index.values())
    by_kind = Counter(kind for entry in index.values() for kind in entry.get("kinds", []))
    return {"contributors": len(index), "by_status": dict(by_status), "by_kind": dict(by_kind)}


def listing(store: Store) -> dict[str, Any]:
    audit.record(store, "index", PATH)
    return summary(load(store))

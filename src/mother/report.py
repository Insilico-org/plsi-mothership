"""`mother steward report`: what happened over the last N days, in counts only: intake by kind, deletions, refusals
(from the audit log), contributors by status, registry updates and research notes waiting for a decision, and
findings by status."""

from __future__ import annotations

from collections import Counter
from datetime import timedelta
from typing import Any

from mother import audit, clock, findings, index, notes, research
from mother.store import Store, read_json


def report(store: Store, days: int = 7) -> dict[str, Any]:
    since = (clock.now() - timedelta(days=days)).isoformat(timespec="seconds")
    recent = [e for e in audit.entries(store) if e["at"] >= since]
    intake = Counter(e["target"].split("/")[0] for e in recent if e["action"] == "intake")
    refusals = Counter(f"{e['action'].removesuffix('_refused')}:{e['target']}" for e in recent
                       if e["action"].endswith("_refused"))
    deletions = [d for d in (read_json(store, p) for p in store.list("deletions")) if d["at"] >= since]
    decided = notes.decisions(store)
    pending = [r for r in notes.receipts(store) if r not in decided]
    audit.record(store, "steward_report", f"{days}d")
    return {"since": since, "days": days, "intake": dict(intake), "refusals": dict(refusals),
            "deletions": {"count": len(deletions), "packages_deleted": sum(d["packages_deleted"] for d in deletions)},
            "contributors": index.summary(index.load(store)), "notes_pending": len(pending),
            "research_waiting": research.waiting(store),
            "findings": dict(Counter(r["status"] for r in findings.listing(store))),
            "audit_entries": len(recent)}

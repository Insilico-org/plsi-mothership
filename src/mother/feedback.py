"""`mother feedback` (WIRE §8): emailed usage reports, stored without the address and pooled in a digest."""

from __future__ import annotations

import hashlib
import json
import statistics
from collections import Counter, defaultdict
from datetime import timedelta
from pathlib import Path
from typing import Any

from mother import audit, clock, flags, gate
from mother.errors import GateFailed, NotFound, Refused, UsageError, violation
from mother.intake import receipt as new_receipt
from mother.research import DAY, _ignored, email
from mother.store import Store, read_json, write_json
from mother.vocab import FeedbackStage, Kind, Sender, Severity

REPORTS = "feedback/reports"
DIGESTS = "feedback/digests"
SUBJECT = "PLSI feedback:"
MAX_PER_WEEK = 5  # reports one sender may send in seven days
WEIGHT = {Severity.HIGH: 3, Severity.MEDIUM: 2, Severity.LOW: 1}
STAGES = list(FeedbackStage)


def _hash(address: str) -> str:
    return hashlib.sha256(address.encode("ascii")).hexdigest()


def receipts(store: Store) -> list[str]:
    return sorted({p.split("/")[-2] for p in store.list(REPORTS) if p.endswith("/report.json")})


def _known(store: Store, rid: str) -> None:
    if not store.exists(f"{REPORTS}/{rid}/report.json"):
        raise NotFound("not a stored feedback report")


def _report(store: Store, rid: str) -> dict[str, Any]:
    return json.loads(store.read(f"{REPORTS}/{rid}/report.json"))


def _meta(store: Store, rid: str) -> dict[str, Any]:
    return read_json(store, f"{REPORTS}/{rid}/meta.json")


def intake(store: Store, raw: bytes) -> dict[str, Any]:
    """Gate and store the report of one raw email."""
    address, text, dmarc, media = email(raw, SUBJECT, "NO_FEEDBACK")
    doc = gate.parse(text)
    if found := gate.violations(doc, Sender.EMAIL):
        raise GateFailed(found)
    if doc["kind"] != Kind.FEEDBACK:
        raise GateFailed([violation("kind", "not_feedback")])
    text, sender = gate.canonical(doc), _hash(address)
    mine = [r for r in receipts(store) if _meta(store, r)["sender_sha256"] == sender]
    counts = {"frustrations": len(doc["frustrations"]), "suggestions": len(doc["suggestions"])}
    if rid := next((r for r in mine if store.read(f"{REPORTS}/{r}/report.json") == text), None):
        return {"receipt": rid, **counts, "dmarc": dmarc, "reply_to": address, "repeat": True,
                "warnings": flags.warnings(doc), **_ignored(media)}
    week = (clock.now() - timedelta(days=7)).isoformat(timespec="seconds")
    if sum(_meta(store, r)["received_at"] >= week for r in mine) >= MAX_PER_WEEK:
        raise Refused("this sender sent enough reports this week", code="TOO_MANY_REPORTS",
                      hint="reply that a new report is welcome next week")
    rid = new_receipt()
    store.write(f"{REPORTS}/{rid}/report.json", text)
    write_json(store, f"{REPORTS}/{rid}/meta.json",
               {"received_at": clock.stamp(), "dmarc": dmarc, "sender_sha256": sender})
    audit.record(store, "feedback_intake", rid)
    return {"receipt": rid, **counts, "dmarc": dmarc, "reply_to": address, "warnings": flags.warnings(doc),
            **_ignored(media)}


def listing(store: Store) -> list[dict[str, Any]]:
    audit.record(store, "feedback_list", "feedback")
    out = []
    for r in receipts(store):
        doc, meta = _report(store, r), _meta(store, r)
        out.append({"receipt": r, "received_at": meta["received_at"], "dmarc": meta["dmarc"], "role": doc["role"],
                    "frustrations": len(doc["frustrations"]), "suggestions": len(doc["suggestions"])})
    return out


def show(store: Store, rid: str) -> dict[str, Any]:
    _known(store, rid)
    meta = _meta(store, rid)
    audit.record(store, "feedback_show", rid)
    text = store.read(f"{REPORTS}/{rid}/report.json")
    return {"receipt": rid, "received_at": meta["received_at"], "dmarc": meta["dmarc"],
            "report": {"kind": "external_text", "text": text}, "warnings": flags.warnings(gate.parse(text))}


def _stages(docs: list[tuple[str, dict[str, Any]]]) -> list[dict[str, Any]]:
    """Measured minutes per stage: count, median, longest."""
    found: dict[str, list[int]] = defaultdict(list)
    for _, doc in docs:
        for e in doc["stages"]:
            if e["measured"]:
                found[e["stage"]].append(e["minutes"])
    return [{"stage": s, "reports": len(found[s]), "median_minutes": statistics.median(found[s]),
             "max_minutes": max(found[s])} for s in STAGES if found[s]]


def _frustrations(docs: list[tuple[str, dict[str, Any]]]) -> list[dict[str, Any]]:
    """Frustrations per stage, ranked by severity-weighted count."""
    found: dict[str, list[dict[str, str]]] = defaultdict(list)
    for rid, doc in docs:
        for e in doc["frustrations"]:
            found[e["stage"]].append({"receipt": rid, "severity": e["severity"], "what": e["what"]})
    rows = [{"stage": s, "count": len(items), "severity": dict(Counter(i["severity"] for i in items)),
             "score": sum(WEIGHT[Severity(i["severity"])] for i in items), "items": items}
            for s, items in found.items()]
    return sorted(rows, key=lambda r: (-r["score"], STAGES.index(FeedbackStage(r["stage"]))))


def _suggestions(docs: list[tuple[str, dict[str, Any]]]) -> list[dict[str, Any]]:
    found: dict[str, list[dict[str, str]]] = defaultdict(list)
    for rid, doc in docs:
        for e in doc["suggestions"]:
            found[e["stage"]].append({"receipt": rid, "what": e["what"]})
    return [{"stage": s, "items": found[s]} for s in STAGES if found[s]]


def digest(store: Store, since: str, save: str | None = None) -> dict[str, Any]:
    """Reports since `since` (UTC, inclusive), pooled; `save` stores the digest text dated today."""
    if not DAY.fullmatch(since):
        raise UsageError("--since is YYYY-MM-DD")
    docs = [(r, _report(store, r)) for r in receipts(store) if _meta(store, r)["received_at"][:10] >= since]
    out: dict[str, Any] = {"since": since, "reports": len(docs),
                           "roles": dict(Counter(doc["role"] for _, doc in docs)), "stages": _stages(docs),
                           "frustrations": _frustrations(docs), "suggestions": _suggestions(docs),
                           "warnings": [{"receipt": r, **w} for r, doc in docs for w in flags.warnings(doc)]}
    if save is not None:
        text = Path(save).read_text(encoding="utf-8")
        if gate.PATTERNS["email"].search(text):
            raise Refused("the digest holds an email address", code="DIGEST_HAS_ADDRESS",
                          hint="digests name receipts and stages, never a sender")
        path = f"{DIGESTS}/feedback_{clock.now().strftime('%Y%m%d')}.md"
        store.write(path, text)
        out["saved"] = path
    audit.record(store, "feedback_digest", since)
    return out

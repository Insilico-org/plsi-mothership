"""`mother feedback` (WIRE §8): emailed usage reports, stored without the address and pooled in a digest."""

from __future__ import annotations

import json
import statistics
from collections import Counter, defaultdict
from typing import Any

from mother import audit, clock, flags, gate, mail
from mother.errors import GateFailed, NotFound, Refused, violation
from mother.store import Store, new_receipt, read_json, sha256_hex, write_json
from mother.vocab import FeedbackStage, Kind, Sender, Severity

REPORTS = "feedback/reports"
DIGESTS = "feedback/digests"
SUBJECT = "PLSI feedback:"
MAX_PER_WEEK = 5  # reports one sender may send in seven days
WEIGHT = {Severity.HIGH: 3, Severity.MEDIUM: 2, Severity.LOW: 1}
STAGES = list(FeedbackStage)
Reports = list[tuple[str, dict[str, Any]]]  # (receipt, report)


def receipts(store: Store) -> list[str]:
    return sorted({p.split("/")[-2] for p in store.list(REPORTS) if p.endswith("/report.json")})


def _text(store: Store, rid: str) -> str:
    try:
        return store.read(f"{REPORTS}/{rid}/report.json")
    except NotFound:
        raise NotFound("not a stored feedback report") from None


def _report(store: Store, rid: str) -> dict[str, Any]:
    return json.loads(_text(store, rid))


def _meta(store: Store, rid: str) -> dict[str, Any]:
    return read_json(store, f"{REPORTS}/{rid}/meta.json")


def _answer(doc: dict[str, Any], rid: str, dmarc: str, address: str, media: Any, **extra: Any) -> dict[str, Any]:
    return {"receipt": rid, "frustrations": len(doc["frustrations"]), "suggestions": len(doc["suggestions"]),
            "dmarc": dmarc, "reply_to": address, **extra, "warnings": flags.warnings(doc), **mail.ignored(media)}


def intake(store: Store, raw: bytes) -> dict[str, Any]:
    address, text, dmarc, media = mail.read(raw, SUBJECT, "NO_FEEDBACK")
    doc = gate.checked(text, Sender.EMAIL)
    if doc["kind"] != Kind.FEEDBACK:
        raise GateFailed([violation("kind", "not_feedback")])
    text, sender = gate.canonical(doc), sha256_hex(address)
    mine = [r for r in receipts(store) if _meta(store, r)["sender_sha256"] == sender]
    if rid := next((r for r in mine if _text(store, r) == text), None):
        return _answer(doc, rid, dmarc, address, media, repeat=True)
    week = clock.since(7)
    if sum(_meta(store, r)["received_at"] >= week for r in mine) >= MAX_PER_WEEK:
        raise Refused("this sender sent enough reports this week", code="TOO_MANY_REPORTS",
                      hint="reply that a new report is welcome next week")
    rid = new_receipt()
    store.write(f"{REPORTS}/{rid}/report.json", text)
    write_json(store, f"{REPORTS}/{rid}/meta.json",
               {"received_at": clock.stamp(), "dmarc": dmarc, "sender_sha256": sender})
    audit.record(store, "feedback_intake", rid)
    return _answer(doc, rid, dmarc, address, media)


def listing(store: Store) -> list[dict[str, Any]]:
    audit.record(store, "feedback_list", "feedback")
    out = []
    for r in receipts(store):
        doc, meta = _report(store, r), _meta(store, r)
        out.append({"receipt": r, "received_at": meta["received_at"], "dmarc": meta["dmarc"], "role": doc["role"],
                    "plsi_version": gate.version(doc),
                    "frustrations": len(doc["frustrations"]), "suggestions": len(doc["suggestions"])})
    return out


def show(store: Store, rid: str) -> dict[str, Any]:
    text, meta = _text(store, rid), _meta(store, rid)
    audit.record(store, "feedback_show", rid)
    return {"receipt": rid, "received_at": meta["received_at"], "dmarc": meta["dmarc"],
            "report": flags.external(text), "warnings": flags.warnings(gate.parse(text))}


def _by_stage(docs: Reports, key: str, *fields: str) -> dict[str, list[dict[str, Any]]]:
    """The `key` entries of every report by stage, each with its receipt and `fields`."""
    found: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for rid, doc in docs:
        for e in doc[key]:
            found[e["stage"]].append({"receipt": rid, **{f: e[f] for f in fields}})
    return found


def _stages(docs: Reports) -> list[dict[str, Any]]:
    """Measured minutes per stage: count, median, longest."""
    found = _by_stage(docs, "stages", "minutes", "measured")
    minutes = {s: [e["minutes"] for e in found[s] if e["measured"]] for s in STAGES}
    return [{"stage": s, "reports": len(m), "median_minutes": statistics.median(m), "max_minutes": max(m)}
            for s, m in minutes.items() if m]


def _frustrations(docs: Reports) -> list[dict[str, Any]]:
    """Frustrations per stage, ranked by severity-weighted count."""
    rows = [{"stage": s, "count": len(items), "severity": dict(Counter(i["severity"] for i in items)),
             "score": sum(WEIGHT[Severity(i["severity"])] for i in items), "items": items}
            for s, items in _by_stage(docs, "frustrations", "severity", "what").items()]
    return sorted(rows, key=lambda r: (-r["score"], STAGES.index(FeedbackStage(r["stage"]))))


def _suggestions(docs: Reports) -> list[dict[str, Any]]:
    found = _by_stage(docs, "suggestions", "what")
    return [{"stage": s, "items": found[s]} for s in STAGES if found[s]]


def digest(store: Store, since: str, save: str | None = None) -> dict[str, Any]:
    """Reports since `since` (UTC, inclusive), pooled; `save` stores the digest text dated today."""
    clock.day(since, "--since")
    docs = [(r, _report(store, r)) for r in receipts(store) if _meta(store, r)["received_at"][:10] >= since]
    out: dict[str, Any] = {"since": since, "reports": len(docs),
                           "roles": dict(Counter(doc["role"] for _, doc in docs)),
                           "versions": dict(Counter(gate.version(doc) for _, doc in docs)), "stages": _stages(docs),
                           "frustrations": _frustrations(docs), "suggestions": _suggestions(docs),
                           "warnings": [{"receipt": r, **w} for r, doc in docs for w in flags.warnings(doc)]}
    if save is not None:
        out |= mail.save_digest(store, f"{DIGESTS}/feedback_{clock.now().strftime('%Y%m%d')}.md", save,
                                "receipts and stages")
    audit.record(store, "feedback_digest", since)
    return out

"""`mother findings`: the review flow of the findings ledger. `check` reruns the analysis on the stored release and
compares it with the submitted result; `review` records an independent reviewer's verdict; `accept` / `reject` record
a steward's decision; `fdr` gives Benjamini-Hochberg q-values over every preregistration, from the p the code
recomputed (no finding, or no passing latest check, counts as p = 1); `show` gives the bodies as external_text.
Every step is a new ledger entry; status is derived, never stored."""

from __future__ import annotations

import json
import math
import os
from typing import Any

from mother import audit, clock, ledger, release, stats
from mother.errors import MotherError, NotFound, Refused
from mother.store import Store, write_json
from mother.vocab import Decision, Entry, FindingStatus, Verdict

RESULT_KEYS = ("effect", "statistic", "df", "p", "n")
REL_TOL = 1e-6


def _body(store: Store, id_: str, name: str) -> dict[str, Any]:
    return json.loads(store.read(f"findings/{id_}/{name}.json"))


def _finding(store: Store, fid: str) -> dict[str, Any]:
    entry = ledger.first(store, Entry.FINDING, fid)
    if entry is None:
        raise NotFound("not a finding in the ledger", hint="mother findings list")
    return entry


def status(store: Store, fid: str) -> FindingStatus:
    entries = ledger.entries(store, id_=fid)
    by_type = {t: [e for e in entries if e["type"] == t] for t in Entry}
    if by_type[Entry.DECISION]:
        return FindingStatus(by_type[Entry.DECISION][-1]["decision"])
    if by_type[Entry.CHECK] and not by_type[Entry.CHECK][-1]["ok"]:
        return FindingStatus.CHECK_FAILED
    if by_type[Entry.REVIEW]:
        return FindingStatus.REVIEWED
    if by_type[Entry.CHECK]:
        return FindingStatus.CHECKED
    return FindingStatus.SUBMITTED if by_type[Entry.FINDING] else FindingStatus.PREREGISTERED


def listing(store: Store) -> list[dict[str, Any]]:
    """Every preregistration with its finding (if any) and derived status."""
    findings = {f["preregistration_id"]: f for f in ledger.entries(store, Entry.FINDING)}
    out = []
    for pre in ledger.entries(store, Entry.PREREGISTRATION):
        f = findings.get(pre["id"])
        reviews = ledger.entries(store, Entry.REVIEW, f["id"]) if f else []
        out.append({"preregistration_id": pre["id"], "finding_id": f["id"] if f else None, "release": pre["release"],
                    "professional_id": pre["professional_id"], "status": status(store, f["id"] if f else pre["id"]).value,
                    "verdicts": [r["verdict"] for r in reviews]})
    return out


def show(store: Store, id_: str) -> dict[str, Any]:
    """A preregistration (`pr_`) or finding (`f_`) as stored, with the preregistration of a finding and its checks."""
    if id_.startswith("f_"):
        pid = _finding(store, id_)["preregistration_id"]
    elif ledger.first(store, Entry.PREREGISTRATION, id_) is not None:
        pid = id_
    else:
        raise NotFound("not a preregistration or finding in the ledger", hint="mother findings list")
    out: dict[str, Any] = {"id": id_, "status": status(store, id_).value, "preregistration_id": pid,
                           "preregistration": {"kind": "external_text",
                                               "text": store.read(f"findings/{pid}/preregistration.json")}}
    if id_.startswith("f_"):
        out["finding"] = {"kind": "external_text", "text": store.read(f"findings/{id_}/finding.json")}
        out["checks"] = [json.loads(store.read(c["file"])) for c in ledger.entries(store, Entry.CHECK, id_)]
    audit.record(store, "findings_show", id_)
    return out


def _close(a: Any, b: Any) -> bool:
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_close(x, y) for x, y in zip(a, b))
    return (isinstance(a, (int, float)) and isinstance(b, (int, float))
            and math.isclose(a, b, rel_tol=REL_TOL, abs_tol=1e-12))


def check(store: Store, fid: str) -> dict[str, Any]:
    """Rerun the finding's analysis on its release and record whether it reproduces."""
    entry = _finding(store, fid)
    finding, pre = _body(store, fid, "finding"), _body(store, entry["preregistration_id"], "preregistration")
    problems, recomputed = [], None
    if finding["analysis"] != pre["analysis"]:
        problems.append("spec_differs: the analysis is not the preregistered one")
    if finding["release"] != pre["release"]:
        problems.append("release_differs: not the preregistered release")
    if not store.exists(release.path(finding["release"])):
        problems.append(f"release_missing: {finding['release']}")
    else:
        try:
            recomputed = stats.run(release.load(store, finding["release"]), finding["analysis"])
        except MotherError as exc:
            problems.append(f"rerun_failed: {exc.code}")
    if recomputed is not None:
        problems += [f"mismatch: {k}" for k in (*RESULT_KEYS, "interval")
                     if not _close(finding["result"].get(k), recomputed[k])]
    out = {"at": clock.stamp(), "finding_id": fid, "ok": not problems, "problems": problems, "recomputed": recomputed}
    n = len(ledger.entries(store, Entry.CHECK, fid)) + 1
    write_json(store, f"findings/{fid}/check-{n}.json", out)
    ledger.append(store, Entry.CHECK, fid, ok=not problems, problems=problems, file=f"findings/{fid}/check-{n}.json")
    audit.record(store, "findings_check", fid)
    return out


def _undecided(store: Store, fid: str) -> dict[str, Any]:
    entry = _finding(store, fid)
    if ledger.entries(store, Entry.DECISION, fid):
        raise Refused("already decided; the ledger is append-only", code="ALREADY_DECIDED")
    return entry


def review(store: Store, fid: str, verdict: str, by: str, notes: str) -> dict[str, Any]:
    entry = _undecided(store, fid)
    checks = ledger.entries(store, Entry.CHECK, fid)
    if not checks or not checks[-1]["ok"]:
        raise Refused("no passing code check", code="NOT_CHECKED", hint="mother findings check --id <id>")
    if by.strip().lower() == entry["professional_id"].lower():
        raise Refused("a submitter never reviews their own finding", code="SELF_REVIEW")
    added = ledger.append(store, Entry.REVIEW, fid, verdict=Verdict(verdict).value, by=by, notes=notes)
    audit.record(store, "findings_review", fid)
    return added


def require_steward(name: str) -> str:
    """`name` if it is one of MOTHER_STEWARDS; only a steward decides on a finding, a research note or a registry
    update."""
    stewards = [s.strip() for s in os.environ.get("MOTHER_STEWARDS", "").split(",") if s.strip()]
    if not stewards:
        raise Refused("MOTHER_STEWARDS is not set", code="STEWARDS_NOT_CONFIGURED")
    if name not in stewards:
        raise Refused("not one of MOTHER_STEWARDS", code="NOT_A_STEWARD")
    return name


def accept(store: Store, fid: str, steward: str) -> dict[str, Any]:
    _undecided(store, fid)
    checks, reviews = ledger.entries(store, Entry.CHECK, fid), ledger.entries(store, Entry.REVIEW, fid)
    if not checks or not checks[-1]["ok"]:
        raise Refused("no passing code check", code="NOT_CHECKED")
    if not reviews or reviews[-1]["verdict"] != Verdict.OK:
        raise Refused("no independent review with verdict ok", code="NOT_REVIEWED")
    name = require_steward(steward)
    if all(r["by"].strip().casefold() == name.casefold() for r in reviews):
        raise Refused("the accepting steward is the only reviewer", code="STEWARD_ONLY_REVIEWER")
    added = ledger.append(store, Entry.DECISION, fid, decision=Decision.ACCEPTED.value, steward=name)
    audit.record(store, "findings_accept", fid)
    return added


def reject(store: Store, fid: str, steward: str, reason: str) -> dict[str, Any]:
    _undecided(store, fid)
    added = ledger.append(store, Entry.DECISION, fid, decision=Decision.REJECTED.value,
                          steward=require_steward(steward), reason=reason)
    audit.record(store, "findings_reject", fid)
    return added


def bh(ps: list[float]) -> list[float]:
    """Benjamini-Hochberg q-values, in the order of `ps`."""
    m = len(ps)
    order = sorted(range(m), key=lambda i: ps[i])
    q, running = [1.0] * m, 1.0
    for rank in range(m, 0, -1):
        i = order[rank - 1]
        running = min(running, ps[i] * m / rank)
        q[i] = min(running, 1.0)
    return q


def _checked_p(store: Store, fid: str | None) -> float:
    """The p the code recomputed in the finding's latest check, if that check passed; else 1."""
    checks = ledger.entries(store, Entry.CHECK, fid) if fid else []
    if not checks or not checks[-1]["ok"]:
        return 1.0
    p = (json.loads(store.read(checks[-1]["file"])).get("recomputed") or {}).get("p")
    return float(p) if isinstance(p, (int, float)) and not isinstance(p, bool) and 0 <= p <= 1 else 1.0


def fdr(store: Store) -> dict[str, Any]:
    findings = {f["preregistration_id"]: f["id"] for f in ledger.entries(store, Entry.FINDING)}
    rows = []
    for pre in ledger.entries(store, Entry.PREREGISTRATION):
        fid = findings.get(pre["id"])
        p = _checked_p(store, fid)
        rows.append({"preregistration_id": pre["id"], "finding_id": fid, "p": p})
    for row, q in zip(rows, bh([r["p"] for r in rows])):
        row["q"] = stats.sig(q, 6)
    audit.record(store, "findings_fdr", ledger.PATH)
    return {"tests": len(rows), "rows": rows}


def public(store: Store) -> dict[str, Any]:
    """The public findings section: accepted findings in full, plus counts."""
    q = {r["finding_id"]: r["q"] for r in fdr(store)["rows"] if r["finding_id"]}
    rows = listing(store)
    out = []
    for row in rows:
        if row["status"] != FindingStatus.ACCEPTED:
            continue
        fid, pid = row["finding_id"], row["preregistration_id"]
        pre, finding = _body(store, pid, "preregistration"), _body(store, fid, "finding")
        out.append({"finding_id": fid, "preregistration_id": pid, "professional_id": row["professional_id"],
                    "release": finding["release"], "hypothesis": pre["hypothesis"], "outcome": pre["outcome"],
                    "alpha": pre["alpha"], "direction": pre["direction"], "parents": pre["parents"],
                    "analysis": finding["analysis"], "result": finding["result"], "summary": finding["summary"],
                    "q": q.get(fid), "reviews": [{"verdict": r["verdict"], "by": r["by"]}
                                                 for r in ledger.entries(store, Entry.REVIEW, fid)]})
    counts = {s: sum(r["status"] == s for r in rows) for s in (FindingStatus.ACCEPTED, FindingStatus.REJECTED)}
    return {"built": clock.month(), "findings": out,
            "counts": {"preregistered": len(rows), **{str(k): v for k, v in counts.items()}}}

"""`mother findings`: the review flow of the findings ledger. `check` reruns the analysis on the stored release,
`review` records an independent reviewer's verdict, `accept` / `reject` a steward's decision, `fdr` gives
Benjamini-Hochberg q-values from the recomputed p (p = 1 without a passing latest check). Every step is a new ledger
entry; status is derived, never stored."""

from __future__ import annotations

import json
import math
from collections import Counter
from typing import Any

from mother import audit, clock, flags, gate, ledger, release, stats
from mother.errors import MotherError, NotFound, Refused
from mother.ledger import select
from mother.store import Store, write_json
from mother.vocab import Decision, Entry, FindingStatus, Verdict

Log = list[dict[str, Any]]


def path(id_: str, name: str) -> str:
    """A stored body of a ledger id: `preregistration`, `finding` or `check-<n>`."""
    return f"findings/{id_}/{name}.json"


def _body(store: Store, id_: str, name: str) -> dict[str, Any]:
    return json.loads(store.read(path(id_, name)))


def _finding(log: Log, fid: str) -> dict[str, Any]:
    if found := select(log, Entry.FINDING, fid):
        return found[0]
    raise NotFound("not a finding in the ledger", hint="mother findings list")


def _status(log: Log, id_: str) -> FindingStatus:
    mine = select(log, id_=id_)
    by_type = {t: select(mine, t) for t in Entry}
    if by_type[Entry.DECISION]:
        return FindingStatus(by_type[Entry.DECISION][-1]["decision"])
    if by_type[Entry.CHECK] and not by_type[Entry.CHECK][-1]["ok"]:
        return FindingStatus.CHECK_FAILED
    if by_type[Entry.REVIEW]:
        return FindingStatus.REVIEWED
    if by_type[Entry.CHECK]:
        return FindingStatus.CHECKED
    return FindingStatus.SUBMITTED if by_type[Entry.FINDING] else FindingStatus.PREREGISTERED


def status(store: Store, fid: str) -> FindingStatus:
    return _status(ledger.entries(store), fid)


def _pairs(log: Log) -> list[tuple[dict[str, Any], dict[str, Any] | None]]:
    found = {f["preregistration_id"]: f for f in select(log, Entry.FINDING)}
    return [(pre, found.get(pre["id"])) for pre in select(log, Entry.PREREGISTRATION)]


def _rows(log: Log) -> list[dict[str, Any]]:
    return [{"preregistration_id": pre["id"], "finding_id": f["id"] if f else None, "release": pre["release"],
             "professional_id": pre["professional_id"], "status": _status(log, (f or pre)["id"]).value,
             "verdicts": [r["verdict"] for r in select(log, Entry.REVIEW, f["id"])] if f else []}
            for pre, f in _pairs(log)]


def rows(store: Store) -> list[dict[str, Any]]:
    """Every preregistration with its finding and derived status, without an audit entry (report, dashboard)."""
    return _rows(ledger.entries(store))


def listing(store: Store) -> list[dict[str, Any]]:
    audit.record(store, "findings_list", ledger.PATH)
    return rows(store)


def show(store: Store, id_: str) -> dict[str, Any]:
    """A preregistration or finding as stored, with the preregistration of a finding and its checks."""
    log = ledger.entries(store)
    if finding := next(iter(select(log, Entry.FINDING, id_)), None):
        pid = finding["preregistration_id"]
    elif select(log, Entry.PREREGISTRATION, id_):
        pid = id_
    else:
        raise NotFound("not a preregistration or finding in the ledger", hint="mother findings list")
    out: dict[str, Any] = {"id": id_, "status": _status(log, id_).value, "preregistration_id": pid,
                           "preregistration": flags.external(store.read(path(pid, "preregistration")))}
    if finding:
        out["finding"] = flags.external(store.read(path(id_, "finding")))
        out["checks"] = [json.loads(store.read(c["file"])) for c in select(log, Entry.CHECK, id_)]
    audit.record(store, "findings_show", id_)
    return out


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b))
    return (isinstance(a, (int, float)) and isinstance(b, (int, float))
            and math.isclose(a, b, rel_tol=stats.REL_TOL, abs_tol=stats.ABS_TOL))


def check(store: Store, fid: str) -> dict[str, Any]:
    log = ledger.entries(store)
    entry = _finding(log, fid)
    finding, pre = _body(store, fid, "finding"), _body(store, entry["preregistration_id"], "preregistration")
    problems, recomputed = [], None
    if finding["analysis"] != pre["analysis"]:
        problems.append("spec_differs: the analysis is not the preregistered one")
    if finding["release"] != pre["release"]:
        problems.append("release_differs: not the preregistered release")
    try:
        recomputed = stats.run(release.load(store, finding["release"]), finding["analysis"])
    except NotFound:
        problems.append(f"release_missing: {finding['release']}")
    except MotherError as exc:
        problems.append(f"rerun_failed: {exc.code}")
    if recomputed is not None:
        problems += [f"mismatch: {k}" for k in gate.RESULT_KEYS if not _same(finding["result"].get(k), recomputed[k])]
    out = {"at": clock.stamp(), "finding_id": fid, "ok": not problems, "problems": problems, "recomputed": recomputed}
    file = path(fid, f"check-{len(select(log, Entry.CHECK, fid)) + 1}")
    write_json(store, file, out)
    ledger.append(store, Entry.CHECK, fid, ok=not problems, problems=problems, file=file)
    audit.record(store, "findings_check", fid)
    return out


def _passing_check(log: Log, fid: str) -> dict[str, Any] | None:
    checks = select(log, Entry.CHECK, fid)
    return checks[-1] if checks and checks[-1]["ok"] else None


def _require_check(log: Log, fid: str) -> None:
    if _passing_check(log, fid) is None:
        raise Refused("no passing code check", code="NOT_CHECKED", hint="mother findings check --id <id>")


def _undecided(log: Log, fid: str) -> dict[str, Any]:
    entry = _finding(log, fid)
    if select(log, Entry.DECISION, fid):
        raise Refused("already decided; the ledger is append-only", code="ALREADY_DECIDED")
    return entry


def review(store: Store, fid: str, verdict: str, by: str, notes: str) -> dict[str, Any]:
    log = ledger.entries(store)
    entry = _undecided(log, fid)
    _require_check(log, fid)
    if by.strip().lower() == entry["professional_id"].lower():
        raise Refused("a submitter never reviews their own finding", code="SELF_REVIEW")
    added = ledger.append(store, Entry.REVIEW, fid, verdict=Verdict(verdict).value, by=by, notes=notes)
    audit.record(store, "findings_review", fid)
    return added


def accept(store: Store, fid: str, steward: str) -> dict[str, Any]:
    log = ledger.entries(store)
    _undecided(log, fid)
    _require_check(log, fid)
    reviews = select(log, Entry.REVIEW, fid)
    if not reviews or reviews[-1]["verdict"] != Verdict.OK:
        raise Refused("no independent review with verdict ok", code="NOT_REVIEWED")
    name = audit.require_steward(steward)
    if all(r["by"].strip().casefold() == name.casefold() for r in reviews):
        raise Refused("the accepting steward is the only reviewer", code="STEWARD_ONLY_REVIEWER")
    added = ledger.append(store, Entry.DECISION, fid, decision=Decision.ACCEPTED.value, steward=name)
    audit.record(store, "findings_accept", fid)
    return added


def reject(store: Store, fid: str, steward: str, reason: str) -> dict[str, Any]:
    _undecided(ledger.entries(store), fid)
    added = ledger.append(store, Entry.DECISION, fid, decision=Decision.REJECTED.value,
                          steward=audit.require_steward(steward), reason=reason)
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


def _checked_p(store: Store, log: Log, fid: str | None) -> float:
    """The p the code recomputed in the finding's latest check, if that check passed; else 1."""
    passed = _passing_check(log, fid) if fid else None
    if passed is None:
        return 1.0
    p = (json.loads(store.read(passed["file"])).get("recomputed") or {}).get("p")
    return float(p) if isinstance(p, (int, float)) and not isinstance(p, bool) and 0 <= p <= 1 else 1.0


def q_values(store: Store, log: Log) -> list[dict[str, Any]]:
    """Every preregistration's recomputed p and its q, without an audit entry."""
    out = [{"preregistration_id": pre["id"], "finding_id": f["id"] if f else None,
            "p": _checked_p(store, log, f["id"] if f else None)} for pre, f in _pairs(log)]
    return [r | {"q": stats.sig(q, stats.RESULT_DIGITS)} for r, q in zip(out, bh([r["p"] for r in out]))]


def fdr(store: Store) -> dict[str, Any]:
    out = q_values(store, ledger.entries(store))
    audit.record(store, "findings_fdr", ledger.PATH)
    return {"tests": len(out), "rows": out}


def public(store: Store) -> dict[str, Any]:
    """The public findings section: accepted findings in full, plus counts."""
    log = ledger.entries(store)
    q = {r["finding_id"]: r["q"] for r in q_values(store, log) if r["finding_id"]}
    listed, out = _rows(log), []
    for row in listed:
        if row["status"] != FindingStatus.ACCEPTED:
            continue
        fid, pid = row["finding_id"], row["preregistration_id"]
        pre, finding = _body(store, pid, "preregistration"), _body(store, fid, "finding")
        out.append({"finding_id": fid, "preregistration_id": pid, "professional_id": row["professional_id"],
                    "release": finding["release"], "hypothesis": pre["hypothesis"], "outcome": pre["outcome"],
                    "alpha": pre["alpha"], "direction": pre["direction"], "parents": pre["parents"],
                    "analysis": finding["analysis"], "result": finding["result"], "summary": finding["summary"],
                    "q": q.get(fid), "reviews": [{"verdict": r["verdict"], "by": r["by"]}
                                                 for r in select(log, Entry.REVIEW, fid)]})
    counts = Counter(r["status"] for r in listed)
    return {"built": clock.month(), "findings": out, "counts": {
        "preregistered": len(listed), **{s.value: counts[s] for s in (FindingStatus.ACCEPTED, FindingStatus.REJECTED)}}}

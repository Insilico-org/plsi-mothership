"""`mother research` (WIRE §7): research notes arrive only by email (mother.mail), signed by the sender's address.
Intake gates the note with sender `email` and stores it canonically in research/pending/<sender>/[<project>/]
<receipt>/note.json (<sender> a pseudonymous id from research/senders.json, <project> its title and short id), the
address and the DMARC result in sender.json; the Mother's review and a steward's decision are appended to
research/log.jsonl and the note is never edited. A decision moves it to research/accepted/ or research/rejected/,
under the same <sender>/[<project>/]. A note's optional `project` {id, title} goes with it into every listing,
decision and reply. DMARC (`pass`, `fail` or `none`) is recorded and shown to stewards, never a reason to refuse. The
address is read only for replies (`reply_to` of intake, decide and unreplied) and never listed, shown or digested."""

from __future__ import annotations

import json
import re
import secrets
import unicodedata
from collections import defaultdict
from typing import Any

from mother import audit, bundle, clock, flags, gate, mail
from mother.errors import NotFound, Refused
from mother.store import Store, append_jsonl, new_receipt, read_json, read_jsonl, write_json
from mother.vocab import DECIDED, Decision, NoteStatus, ResearchEntry, Sender, Verdict

LOG = "research/log.jsonl"
MAX_PENDING = 5  # notes one sender may have waiting for a decision
PENDING = "research/pending"
SENDERS = "research/senders.json"  # address -> pseudonymous folder id; an address leaves it with its last note
CLAIM_CHARS = 160
SUBJECT = "PLSI research note:"
Log = dict[str, list[dict[str, Any]]]  # receipt -> its log entries, in order


def _folders(store: Store) -> dict[str, str]:
    found: dict[str, str] = {}
    for p in store.list("research"):
        *at, rid, name = p.split("/")
        if name == "note.json" and rid.startswith("r_"):
            found.setdefault(rid, "/".join([*at, rid]))
    return dict(sorted(found.items()))


def _where(folders: dict[str, str], rid: str) -> str:
    if rid not in folders:
        raise NotFound("not a stored research note")
    return folders[rid]


def receipts(store: Store) -> list[str]:
    return list(_folders(store))


def folder(store: Store, rid: str) -> str:
    return _where(_folders(store), rid)


def _log(entries: list[dict[str, Any]]) -> Log:
    out: Log = defaultdict(list)
    for e in entries:
        out[e["receipt"]].append(e)
    return out


def _status(log: Log, rid: str) -> str:
    mine = log.get(rid, [])
    if decision := next((e for e in mine if e["type"] == ResearchEntry.DECISION), None):
        return decision["decision"]
    reviewed = any(e["type"] == ResearchEntry.REVIEW for e in mine)
    return (NoteStatus.REVIEWED if reviewed else NoteStatus.PENDING_REVIEW).value


def _undecided(log: Log, rids: list[str]) -> list[str]:
    return [r for r in rids if _status(log, r) not in DECIDED]


def _note(store: Store, at: str) -> dict[str, Any]:
    return json.loads(store.read(f"{at}/note.json"))


def _signer(store: Store, at: str) -> dict[str, Any]:
    return read_json(store, f"{at}/sender.json")


def _meta(store: Store, at: str) -> dict[str, str]:
    """When the note came and its DMARC result; never the address."""
    sender = _signer(store, at)
    return {"received_at": sender["received_at"], "dmarc": sender["dmarc"]}


def _claims(doc: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"claim": str(i.get("claim", ""))[:CLAIM_CHARS], "grade": str(i.get("grade", "")),
             **({"tags": [t["label"] for t in i["tags"]]} if i.get("tags") else {}),
             **({"hallmarks": i["hallmarks"]} if i.get("hallmarks") else {})}
            for i in doc["items"]]


def _project(doc: dict[str, Any]) -> dict[str, Any]:
    return {"project": doc["project"]} if "project" in doc else {}


def _sender(store: Store, address: str) -> tuple[str, dict[str, str] | None]:
    """The sender's folder id, so a folder never names an address, and the map to save once the note is stored
    (None when the sender is known)."""
    known = read_json(store, SENDERS, {})
    if address in known:
        return known[address], None
    sid = f"sender-{secrets.token_hex(4)}"
    while sid in known.values():
        sid = f"sender-{secrets.token_hex(4)}"
    return sid, {**known, address: sid}


def _drop_address(store: Store, at: str, sender: dict[str, Any]) -> None:
    """Keep only when and how the note came; the address leaves the sender map once no stored note keeps it."""
    write_json(store, f"{at}/sender.json", {k: sender[k] for k in ("received_at", "dmarc")})
    known, address = read_json(store, SENDERS, {}), sender.get("address")
    if address in known and not any(_signer(store, a).get("address") == address for a in _folders(store).values()):
        del known[address]
        write_json(store, SENDERS, known)


def _home(store: Store, doc: dict[str, Any], address: str) -> tuple[str, dict[str, str] | None]:
    """<sender>/[<project title> · <first 8 of its id>] under the status folder, and the sender map to save."""
    sid, senders = _sender(store, address)
    if not (project := doc.get("project")):
        return sid, senders
    shown = "".join(" " if unicodedata.category(ch) == "Cc" else ch for ch in project["title"]
                    if unicodedata.category(ch) != "Cf")  # no line breaks or direction marks in a folder name
    title = re.sub(r"\s+", " ", re.sub(r"[/\\:]+", " ", shown)).strip()[:60]
    return f"{sid}/{title} · {project['id'][:8]}", senders


def waiting(store: Store) -> int:
    return len(_undecided(_log(read_jsonl(store, LOG)), receipts(store)))


def _answer(doc: dict[str, Any], rid: str, status: str, dmarc: str, address: str, media: Any,
            **extra: Any) -> dict[str, Any]:
    return {"receipt": rid, "status": status, "claims": len(doc["items"]), "dmarc": dmarc, "reply_to": address,
            **extra, "warnings": flags.warnings(doc), **_project(doc), **mail.ignored(media)}


def intake(store: Store, raw: bytes, attachment: bytes | None = None, zipped: bytes | None = None) -> dict[str, Any]:
    zipped = zipped if zipped is not None else mail.zip_part(raw)
    inner = bundle.note_in(zipped) if attachment is None else None
    address, text, dmarc, media = mail.read(raw, SUBJECT, "NO_RESEARCH_NOTE", attachment or inner)
    doc = gate.checked(text, Sender.EMAIL)
    log, folders, text = _log(read_jsonl(store, LOG)), _folders(store), gate.canonical(doc)
    mine = [r for r, at in folders.items() if _signer(store, at).get("address") == address]
    if rid := next((r for r in mine if store.read(f"{folders[r]}/note.json") == text), None):
        return _answer(doc, rid, _status(log, rid), dmarc, address, media, repeat=True)  # one email taken twice
    kept = bundle.unpack(doc, zipped)
    if len(_undecided(log, mine)) >= MAX_PENDING:
        raise Refused("this sender already has notes waiting for a decision", code="TOO_MANY_PENDING",
                      hint="reply that new notes are taken once earlier ones are decided")
    rid = new_receipt()
    home, senders = _home(store, doc, address)
    base = f"{PENDING}/{home}/{rid}"
    store.write(f"{base}/note.json", text)
    for name, body in kept.items():
        store.write(f"{base}/files/{name}", body)
    write_json(store, f"{base}/sender.json", {"address": address, "received_at": clock.stamp(), "dmarc": dmarc})
    if senders is not None:  # a new sender is mapped only once their note is stored
        write_json(store, SENDERS, senders)
    audit.record(store, "research_intake", rid)
    return _answer(doc, rid, NoteStatus.PENDING_REVIEW.value, dmarc, address, media, plsi_version=gate.version(doc))


def summaries(store: Store) -> list[dict[str, Any]]:
    """Each note in counts and its first claim, newest first; never the address, never audited (the dashboard)."""
    log, out = _log(read_jsonl(store, LOG)), []
    for rid, at in _folders(store).items():
        doc, mine = _note(store, at), log.get(rid, [])
        review = next((e for e in reversed(mine) if e["type"] == ResearchEntry.REVIEW), None)
        decision = next((e for e in mine if e["type"] == ResearchEntry.DECISION), None)
        out.append({"receipt": rid, **_meta(store, at), "status": _status(log, rid), "items": len(doc["items"]),
                    "claim": str(doc["items"][0].get("claim", ""))[:CLAIM_CHARS] if doc["items"] else "",
                    "tags": sorted({str(t["label"]) for i in doc["items"] for t in i.get("tags") or []}
                                   | {str(h) for i in doc["items"] for h in i.get("hallmarks") or []}),
                    "project": (doc.get("project") or {}).get("title", ""),
                    "files": len(doc.get("files") or []), "verdict": review["verdict"] if review else None,
                    "decided": {"by": decision["by"], "at": decision["at"]} if decision else None})
    return sorted(out, key=lambda n: n["received_at"], reverse=True)


def _item(store: Store, log: Log, folders: dict[str, str], rid: str) -> dict[str, Any]:
    at = _where(folders, rid)
    doc = _note(store, at)
    return {"receipt": rid, **_meta(store, at), "status": _status(log, rid), "plsi_version": gate.version(doc),
            "claims": _claims(doc), **_project(doc)}


def listing(store: Store, project: str | None = None) -> list[dict[str, Any]]:
    log, folders = _log(read_jsonl(store, LOG)), _folders(store)
    audit.record(store, "research_list", "research")
    items = [_item(store, log, folders, rid) for rid in folders]
    return [n for n in items if project is None or n.get("project", {}).get("id") == project]


def show(store: Store, rid: str, name: str | None = None) -> dict[str, Any]:
    at, log = folder(store, rid), _log(read_jsonl(store, LOG))
    audit.record(store, "research_show", rid)
    text = store.read(f"{at}/note.json")
    doc = gate.parse(text)
    files = [{**e, "stored": store.exists(f"{at}/files/{e['name']}")} for e in doc.get("files") or []]
    if name is not None:
        if not any(f["name"] == name and f["stored"] for f in files):
            raise NotFound("no such stored file in this note", hint="a Drive file: mother research fetch --receipt <r> --name <file>")
        return {"receipt": rid, "file": name, "content": flags.external(store.read(f"{at}/files/{name}"))}
    return {"receipt": rid, **_meta(store, at), "status": _status(log, rid), "files": files,
            "note": flags.external(text), "warnings": flags.warnings(doc), "log": log.get(rid, [])}


def review(store: Store, rid: str, verdict: str, notes: str) -> dict[str, Any]:
    """The Mother's own review; it comes before a steward's decision and never after it."""
    folder(store, rid)
    if _status(_log(read_jsonl(store, LOG)), rid) in DECIDED:
        raise Refused("already decided", code="ALREADY_DECIDED")
    entry = {"at": clock.stamp(), "type": ResearchEntry.REVIEW.value, "receipt": rid,
             "verdict": Verdict(verdict).value, "by": audit.actor(), "notes": notes}
    append_jsonl(store, LOG, entry)
    audit.record(store, "research_review", rid)
    return entry


def _file(store: Store, rid: str, decision: Decision, told: bool) -> bool:
    """Move a decided note to accepted/ or rejected/; a rejected note keeps its address only until it is told.
    False when the old copy (with its sender.json) could only go to the drive's trash."""
    src = folder(store, rid)
    sender = _signer(store, src)
    dst = re.sub(r"^research/(?:pending/)?", f"research/{decision.value}/", src)  # same <sender>/[<project>/]
    text = store.read(f"{src}/note.json")
    store.write(f"{dst}/note.json", text)
    kept = gate.parse(text).get("files") or [] if decision == Decision.ACCEPTED else []
    for entry in kept:
        if store.exists(path := f"{src}/files/{entry['name']}"):
            store.write(f"{dst}/files/{entry['name']}", store.read(path))
    write_json(store, f"{dst}/sender.json", sender)
    gone = store.delete(src)  # only after the copy is complete
    if decision == Decision.REJECTED and told:
        _drop_address(store, dst, sender)
    if not gone:
        audit.record(store, "research_trashed", rid)
    return gone


def decide(store: Store, rid: str, decision: str, by: str, reason: str) -> dict[str, Any]:
    """A steward's decision on a reviewed note; `reply_to` is where the decision is emailed."""
    at = folder(store, rid)
    status = _status(_log(read_jsonl(store, LOG)), rid)
    if status in DECIDED:
        raise Refused("already decided", code="ALREADY_DECIDED")
    if status != NoteStatus.REVIEWED:
        raise Refused("no review yet", code="NOT_REVIEWED", hint="mother research review first")
    entry = {"at": clock.stamp(), "type": ResearchEntry.DECISION.value, "receipt": rid,
             "decision": Decision(decision).value, "by": audit.require_steward(by), "reason": reason,
             "reply": "pending", **_project(_note(store, at))}
    reply_to = _signer(store, at)["address"]
    append_jsonl(store, LOG, entry)
    gone = _file(store, rid, Decision(decision), told=False)
    audit.record(store, "research_decide", rid)
    return {**entry, "reply_to": reply_to, **({} if gone else {"trashed": True})}


def _pending(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    told = {e["receipt"] for e in entries if e["type"] == ResearchEntry.REPLIED}
    return [e for e in entries if e["type"] == ResearchEntry.DECISION and e.get("reply") == "pending"
            and e["receipt"] not in told]


def unreplied(store: Store) -> list[dict[str, Any]]:
    """Decided notes whose sender has not been told yet, wherever the decision was taken."""
    audit.record(store, "research_unreplied", "research")
    folders = _folders(store)
    return [{"receipt": e["receipt"], "decision": e["decision"], "reason": e["reason"], "by": e["by"],
             "claims": _claims(_note(store, _where(folders, e["receipt"]))), **_project(e),
             "reply_to": _signer(store, folders[e["receipt"]])["address"]}
            for e in _pending(read_jsonl(store, LOG))]


def replied(store: Store, rid: str) -> dict[str, Any]:
    """Record that the sender was emailed the decision; a rejected note then drops the address."""
    entry = next((e for e in _pending(read_jsonl(store, LOG)) if e["receipt"] == rid), None)
    if entry is None:
        raise Refused("no decision waits to be told for this receipt", code="NOT_PENDING")
    append_jsonl(store, LOG, {"at": clock.stamp(), "type": ResearchEntry.REPLIED.value, "receipt": rid,
                              "by": audit.actor()})
    if entry["decision"] == Decision.REJECTED:
        at = folder(store, rid)
        _drop_address(store, at, _signer(store, at))
    audit.record(store, "research_replied", rid)
    return {"receipt": rid, "decision": entry["decision"]}


def tidy(store: Store) -> dict[str, Any]:
    """Move decided notes from research/<receipt>/ to accepted/ or rejected/."""
    log, moved = _log(read_jsonl(store, LOG)), []
    for rid, at in _folders(store).items():
        status = _status(log, rid)
        if at == f"research/{rid}" and status in DECIDED:
            gone = _file(store, rid, Decision(status), told=True)
            moved.append({"receipt": rid, "to": status, **({} if gone else {"trashed": True})})
    audit.record(store, "research_tidy", "research")
    return {"moved": moved}


def digest(store: Store, day: str, save: str | None = None) -> dict[str, Any]:
    """One day's research: notes received, reviewed and decided that day (UTC), and what still waits; no addresses.
    `save` stores the Mother's digest text (a file it wrote) as research/digests/<day>.md."""
    clock.day(day, "--date")
    entries, folders = read_jsonl(store, LOG), _folders(store)
    log = _log(entries)
    today = [e for e in entries if e["at"].startswith(day)]
    out: dict[str, Any] = {
        "date": day,
        "received": [_item(store, log, folders, r) for r, at in folders.items()
                     if _meta(store, at)["received_at"].startswith(day)],
        "reviewed": [_item(store, log, folders, e["receipt"]) | {"verdict": e["verdict"], "notes": e["notes"]}
                     for e in today if e["type"] == ResearchEntry.REVIEW],
        "decided": [_item(store, log, folders, e["receipt"]) | {"reason": e["reason"]}
                    for e in today if e["type"] == ResearchEntry.DECISION],
        "waiting": len(_undecided(log, list(folders)))}
    if save is not None:
        out |= mail.save_digest(store, f"research/digests/{day}.md", save, "receipts and claims")
    audit.record(store, "research_digest", day)
    return out

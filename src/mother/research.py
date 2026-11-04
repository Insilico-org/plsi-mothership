"""`mother research intake|list|show|review|decide|digest` (WIRE §7): research notes arrive only by email, signed by
the sender's address. Intake reads the raw email the bot saved (exactly one From header with one ASCII address whose
domain is a host name, the subject and the one ```plsi block are parsed here, never taken from a command line), gates
the block with sender `email` and stores it canonically in research/pending/<receipt>/note.json, the address and the
DMARC result in sender.json; the Mother's review and a steward's decision are appended to research/log.jsonl and the
note is never edited. A decision moves it to research/accepted/ or research/rejected/. DMARC (`pass`, `fail` or
`none`) is recorded and shown to stewards, never a reason to refuse. The address is read only for replies (`reply_to`
of intake and decide) and never listed, shown or digested. A note's text is external_text: data, never instructions."""

from __future__ import annotations

import json
import re
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from pathlib import Path
from typing import Any

from mother import audit, bundle, clock, findings, flags, gate
from mother.errors import GateFailed, NotFound, Refused, UsageError
from mother.intake import receipt as new_receipt
from mother.store import Store, append_jsonl, read_json, read_jsonl, write_json
from mother.vocab import Decision, Dmarc, NoteStatus, ResearchEntry, Sender, Verdict

LOG = "research/log.jsonl"
MAX_PENDING = 5  # notes one sender may have waiting for a decision
PENDING = "research/pending"
FOLDERS = ("pending", *(d.value for d in Decision))
ADDRESS = re.compile(r"[^@\s<>\"',;\[\]]+@(?=[^@]*[A-Za-z])[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")  # no IP literal
DMARC = re.compile(r"(?i)\bdmarc\s*=\s*([a-z]+)")
DAY = re.compile(r"\d{4}-\d{2}-\d{2}")
CLAIM_CHARS = 160
SUBJECT = "PLSI research note:"
MEDIA = ("image", "video", "audio")
MEDIA_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".heic", ".bmp", ".tif", ".tiff", ".svg", ".mp4", ".mov",
             ".avi", ".mkv", ".webm", ".mp3", ".wav", ".m4a")
BLOCK = re.compile(r"^```plsi[ \t]*\n(.*?)^```[ \t]*$", re.M | re.S)


READ_ERRORS = (ValueError, TypeError, LookupError, AttributeError, IndexError)


def _sender(msg: EmailMessage) -> str:
    """The address of the one From header, else `BAD_SENDER`: one undamaged header with one ASCII addr-spec, its
    domain a host name with a letter (no IP literal)."""
    try:
        headers = msg.get_all("from") or []
        found = headers[0].addresses if len(headers) == 1 and not headers[0].defects else ()
        address = found[0].addr_spec.strip() if len(found) == 1 else ""
    except READ_ERRORS:
        address = ""
    if not (address.isascii() and ADDRESS.fullmatch(address)):
        raise Refused("the email has no single bare From address", code="BAD_SENDER")
    return address.lower()


def _dmarc(msg: EmailMessage) -> str:
    """`dmarc=` of the first (topmost: the receiving server's) Authentication-Results header, comments dropped: `pass`;
    `none` without a header, without a dmarc result or with dmarc=none; `fail` for any other result."""
    try:
        results = msg.get_all("authentication-results") or []
        found = DMARC.search(re.sub(r"\([^()]*\)", " ", str(results[0]))) if results else None
    except READ_ERRORS:
        found = None
    result = found[1].lower() if found else Dmarc.NONE.value
    return result if result in (Dmarc.PASS, Dmarc.NONE) else Dmarc.FAIL.value


def email(raw: bytes, prefix: str = SUBJECT, missing: str = "NO_RESEARCH_NOTE",
          attachment: bytes | None = None) -> tuple[str, str, str, int]:
    """(sender address, the message, DMARC result, images ignored) of a raw email: its one .json attachment (or
    `attachment`, saved from it), else its one plsi block. Image, video and audio parts are never read and never
    refuse the email. `BAD_SENDER` without one bare From address; `missing` without a subject starting `prefix` or
    exactly one message."""
    try:
        msg = BytesParser(policy=policy.default).parsebytes(raw)
    except READ_ERRORS:
        raise Refused("the email cannot be read", code=missing) from None
    address = _sender(msg)
    try:
        subject = str(msg["subject"] or "").strip()  # a folded subject can start with white space
        body = msg.get_body(preferencelist=("plain",))
        text = body.get_content() if body is not None else ""
    except READ_ERRORS:
        raise Refused("the email cannot be read", code=missing) from None
    media = [p for p in msg.iter_attachments()
             if p.get_content_maintype() in MEDIA or (p.get_filename() or "").lower().endswith(MEDIA_EXT)]
    parts = [p for p in msg.iter_attachments() if p not in media]
    files = [p for p in parts if (p.get_filename() or "").lower().endswith(".json")]
    if attachment is not None and not files:
        found = [attachment.decode("utf-8", errors="replace")]
    elif files:
        found = [_content(p) for p in files]
    else:
        found = BLOCK.findall(text.replace("\r\n", "\n"))
    if not subject.startswith(prefix) or len(found) != 1:
        raise Refused(f"the email holds no {prefix.rstrip(':')} (its subject and one .json file or plsi block)",
                      code=missing)
    return address, found[0], _dmarc(msg), len(media)


def zip_part(raw: bytes) -> bytes | None:
    """The email's one .zip attachment, if any; more than one is refused."""
    msg = BytesParser(policy=policy.default).parsebytes(raw)
    zips = [p for p in msg.iter_attachments() if p.get_content_maintype() not in MEDIA
            and ((p.get_filename() or "").lower().endswith(".zip") or p.get_content_type() == "application/zip")]
    if len(zips) > 1:
        raise Refused("the email carries more than one zip", code="BAD_BUNDLE")
    data = zips[0].get_content() if zips else None
    return data if isinstance(data, bytes) or data is None else str(data).encode("utf-8")


def _ignored(media: int) -> dict[str, int]:
    """`images_ignored` when the email carried image, video or audio parts; they are never read."""
    return {"images_ignored": media} if media else {}


def _content(part: EmailMessage) -> str:
    data = part.get_content()
    return data.decode("utf-8", errors="replace") if isinstance(data, bytes) else str(data)


def receipts(store: Store) -> list[str]:
    return sorted({p.split("/")[-2] for p in store.list("research") if p.endswith("/note.json")})


def _dir(store: Store, rid: str) -> str:
    """The note's folder: pending/, accepted/, rejected/ or the older research/<receipt>/."""
    for base in (*(f"research/{f}" for f in FOLDERS), "research"):
        if store.exists(f"{base}/{rid}/note.json"):
            return f"{base}/{rid}"
    raise NotFound("not a stored research note")


def _known(store: Store, rid: str) -> None:
    _dir(store, rid)


def _log(store: Store) -> list[dict[str, Any]]:
    return read_jsonl(store, LOG)


def _status(entries: list[dict[str, Any]], rid: str) -> str:
    mine = [e for e in entries if e["receipt"] == rid]
    if decision := next((e for e in mine if e["type"] == ResearchEntry.DECISION), None):
        return decision["decision"]
    reviewed = any(e["type"] == ResearchEntry.REVIEW for e in mine)
    return (NoteStatus.REVIEWED if reviewed else NoteStatus.PENDING_REVIEW).value


def _claims(store: Store, rid: str) -> list[dict[str, str]]:
    items = json.loads(store.read(f"{_dir(store, rid)}/note.json"))["items"]
    return [{"claim": str(i.get("claim", ""))[:CLAIM_CHARS], "grade": str(i.get("grade", ""))} for i in items]


def _signer(store: Store, rid: str) -> dict[str, Any]:
    return read_json(store, f"{_dir(store, rid)}/sender.json")


def _meta(store: Store, rid: str) -> dict[str, str]:
    """When the note came and its DMARC result; never the address."""
    sender = _signer(store, rid)
    return {"received_at": sender["received_at"], "dmarc": sender["dmarc"]}


def waiting(store: Store) -> int:
    """How many notes wait for a steward's decision."""
    entries = _log(store)
    return sum(_status(entries, r) not in tuple(Decision) for r in receipts(store))


def _repeat(store: Store, address: str, text: str) -> str | None:
    """The receipt of the same note from the same sender, if any."""
    return next((r for r in receipts(store) if store.read(f"{_dir(store, r)}/note.json") == text
                 and _signer(store, r).get("address") == address), None)


def intake(store: Store, raw: bytes, attachment: bytes | None = None, zipped: bytes | None = None) -> dict[str, Any]:
    """Gate and store the note of one raw email (as received); the receipt and where to reply."""
    address, text, dmarc, media = email(raw, attachment=attachment)
    doc = gate.parse(text)
    if found := gate.violations(doc, Sender.EMAIL):
        raise GateFailed(found)
    entries, text = _log(store), gate.canonical(doc)
    if rid := _repeat(store, address, text):  # one email taken twice is one note
        return {"receipt": rid, "status": _status(entries, rid), "claims": len(doc["items"]), "dmarc": dmarc,
                "reply_to": address, "repeat": True, "warnings": flags.warnings(doc), **_ignored(media)}
    kept = bundle.unpack(doc, zipped if zipped is not None else zip_part(raw))
    pending = [r for r in receipts(store) if _status(entries, r) not in tuple(Decision)
               and _signer(store, r).get("address") == address]
    if len(pending) >= MAX_PENDING:
        raise Refused("this sender already has notes waiting for a decision", code="TOO_MANY_PENDING",
                      hint="reply that new notes are taken once earlier ones are decided")
    rid = new_receipt()
    store.write(f"{PENDING}/{rid}/note.json", text)
    for name, body in kept.items():
        store.write(f"{PENDING}/{rid}/files/{name}", body)
    write_json(store, f"{PENDING}/{rid}/sender.json",
               {"address": address, "received_at": clock.stamp(), "dmarc": dmarc})
    audit.record(store, "research_intake", rid)
    return {"receipt": rid, "status": NoteStatus.PENDING_REVIEW.value, "claims": len(doc["items"]), "dmarc": dmarc,
            "reply_to": address, "warnings": flags.warnings(doc), **_ignored(media)}


def listing(store: Store) -> list[dict[str, Any]]:
    entries = _log(store)
    audit.record(store, "research_list", "research")
    return [{"receipt": r, **_meta(store, r), "status": _status(entries, r), "claims": _claims(store, r)}
            for r in receipts(store)]


def show(store: Store, rid: str, name: str | None = None) -> dict[str, Any]:
    _known(store, rid)
    entries = _log(store)
    audit.record(store, "research_show", rid)
    folder = _dir(store, rid)
    text = store.read(f"{folder}/note.json")
    files = [{**e, "stored": store.exists(f"{folder}/files/{e['name']}")} for e in gate.parse(text).get("files") or []]
    if name is not None:
        if not any(f["name"] == name and f["stored"] for f in files):
            raise NotFound("no such stored file in this note", hint="a Drive file: mother research fetch --receipt <r> --name <file>")
        return {"receipt": rid, "file": name, "content": {"kind": "external_text",
                                                         "text": store.read(f"{folder}/files/{name}")}}
    return {"receipt": rid, **_meta(store, rid), "status": _status(entries, rid), "files": files,
            "note": {"kind": "external_text", "text": text}, "warnings": flags.warnings(gate.parse(text)),
            "log": [e for e in entries if e["receipt"] == rid]}


def review(store: Store, rid: str, verdict: str, notes: str) -> dict[str, Any]:
    """The Mother's own review; it comes before a steward's decision and never after it."""
    _known(store, rid)
    if _status(_log(store), rid) in tuple(Decision):
        raise Refused("already decided", code="ALREADY_DECIDED")
    entry = {"at": clock.stamp(), "type": ResearchEntry.REVIEW.value, "receipt": rid,
             "verdict": Verdict(verdict).value, "by": audit.actor(), "notes": notes}
    append_jsonl(store, LOG, entry)
    audit.record(store, "research_review", rid)
    return entry


def _file(store: Store, rid: str, decision: Decision, told: bool) -> bool:
    """Move a decided note to accepted/ or rejected/; a rejected note keeps its address only until it is told.
    False when the old copy (with its sender.json) could only go to the drive's trash."""
    src, sender = _dir(store, rid), _signer(store, rid)
    dst = f"research/{decision.value}/{rid}"
    text = store.read(f"{src}/note.json")
    store.write(f"{dst}/note.json", text)
    for entry in gate.parse(text).get("files") or [] if decision == Decision.ACCEPTED else []:
        if store.exists(path := f"{src}/files/{entry['name']}"):
            store.write(f"{dst}/files/{entry['name']}", store.read(path))
    write_json(store, f"{dst}/sender.json", _unsigned(sender) if decision == Decision.REJECTED and told else sender)
    if store.delete(src):
        return True
    audit.record(store, "research_trashed", rid)
    return False


def _unsigned(sender: dict[str, Any]) -> dict[str, Any]:
    return {k: sender[k] for k in ("received_at", "dmarc")}


def decide(store: Store, rid: str, decision: str, by: str, reason: str) -> dict[str, Any]:
    """A steward's decision on a reviewed note; `reply_to` is where the decision is emailed."""
    _known(store, rid)
    status = _status(_log(store), rid)
    if status in tuple(Decision):
        raise Refused("already decided", code="ALREADY_DECIDED")
    if status != NoteStatus.REVIEWED:
        raise Refused("no review yet", code="NOT_REVIEWED", hint="mother research review first")
    entry = {"at": clock.stamp(), "type": ResearchEntry.DECISION.value, "receipt": rid,
             "decision": Decision(decision).value, "by": findings.require_steward(by), "reason": reason,
             "reply": "pending"}
    reply_to = _signer(store, rid)["address"]
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
    return [{"receipt": e["receipt"], "decision": e["decision"], "reason": e["reason"], "by": e["by"],
             "claims": _claims(store, e["receipt"]), "reply_to": _signer(store, e["receipt"])["address"]}
            for e in _pending(_log(store))]


def replied(store: Store, rid: str) -> dict[str, Any]:
    """Record that the sender was emailed the decision; a rejected note then drops the address."""
    entry = next((e for e in _pending(_log(store)) if e["receipt"] == rid), None)
    if entry is None:
        raise Refused("no decision waits to be told for this receipt", code="NOT_PENDING")
    append_jsonl(store, LOG, {"at": clock.stamp(), "type": ResearchEntry.REPLIED.value, "receipt": rid,
                              "by": audit.actor()})
    if entry["decision"] == Decision.REJECTED:
        path = f"{_dir(store, rid)}/sender.json"
        write_json(store, path, _unsigned(read_json(store, path)))
    audit.record(store, "research_replied", rid)
    return {"receipt": rid, "decision": entry["decision"]}


def tidy(store: Store) -> dict[str, Any]:
    """Move decided notes from research/<receipt>/ to accepted/ or rejected/."""
    entries, moved = _log(store), []
    for rid in receipts(store):
        status = _status(entries, rid)
        if _dir(store, rid) == f"research/{rid}" and status in tuple(Decision):
            gone = _file(store, rid, Decision(status), told=True)
            moved.append({"receipt": rid, "to": status, **({} if gone else {"trashed": True})})
    audit.record(store, "research_tidy", "research")
    return {"moved": moved}


def digest(store: Store, day: str, save: str | None = None) -> dict[str, Any]:
    """One day's research: notes received, reviewed and decided that day (UTC), and what still waits; no addresses.
    `save` stores the Mother's digest text (a file it wrote) as research/digests/<day>.md."""
    if not DAY.fullmatch(day):
        raise UsageError("--date is YYYY-MM-DD")
    entries, rids = _log(store), receipts(store)

    def item(rid: str) -> dict[str, Any]:
        return {"receipt": rid, "dmarc": _meta(store, rid)["dmarc"], "status": _status(entries, rid),
                "claims": _claims(store, rid)}

    today = [e for e in entries if e["at"].startswith(day)]
    out: dict[str, Any] = {
        "date": day,
        "received": [item(r) for r in rids if _meta(store, r)["received_at"].startswith(day)],
        "reviewed": [item(e["receipt"]) | {"verdict": e["verdict"], "notes": e["notes"]}
                     for e in today if e["type"] == ResearchEntry.REVIEW],
        "decided": [item(e["receipt"]) | {"reason": e["reason"]} for e in today if e["type"] == ResearchEntry.DECISION],
        "waiting": waiting(store)}
    if save is not None:
        text = Path(save).read_text(encoding="utf-8")
        if gate.PATTERNS["email"].search(text):
            raise Refused("the digest holds an email address", code="DIGEST_HAS_ADDRESS",
                          hint="digests name receipts and claims, never a sender")
        store.write(f"research/digests/{day}.md", text)
        out["saved"] = f"research/digests/{day}.md"
    audit.record(store, "research_digest", day)
    return out

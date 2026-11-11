"""`mother intake`: one relayed message, re-checked by the gate and stored canonically (WIRE §6) under a new receipt;
a refused message stores nothing. Contributions go to intake/<contributor_id>/ (`ALREADY_DELETED` once a deletion
receipt covers the pseudonym), withdrawals stop or delete everything of the contributor (the deletion receipt first),
registry updates wait in notes/, preregistrations and findings enter the ledger; research notes come only by email.
The webhook carries only a `relay_ref`: the body and its sender are taken from the relay with the intake key, and the
outcome goes back as the sender's ACK (`INTERNAL` on any unexpected error)."""

from __future__ import annotations

import hmac
import time
from typing import Any

from mother import audit, clock, findings, gate, index, ledger, net, notes, release
from mother.errors import EnvironmentProblem, GateFailed, MotherError, NetworkError, Refused, violation
from mother.store import Store, new_receipt, read_json, sha256_hex, write_json
from mother.vocab import ContributorStatus, Entry, FindingStatus, Kind, NoteStatus, Request, Sender

REF_KEYS = {"kind", "schema_version", "ref", "sha256", "bytes"}
REF_RETRY_S = 65  # a KV write can take up to ~60 s to show at another Cloudflare location
DELETIONS = "deletions"  # one delete_all receipt per file


def _ref(text: str) -> dict[str, Any]:
    """The webhook body: exactly a `relay_ref` (WIRE §4), or a gate failure. A raw message is never accepted here."""
    doc = gate.parse(text)
    if (not isinstance(doc, dict) or set(doc) != REF_KEYS or doc["kind"] != "relay_ref"
            or doc["schema_version"] != gate.SCHEMA_VERSION or isinstance(doc["schema_version"], bool)
            or not isinstance(doc["ref"], str) or not isinstance(doc["bytes"], int)
            or not isinstance(doc["sha256"], str) or len(doc["sha256"]) != 64):
        raise GateFailed([violation("$", "relay_ref")])
    return doc


def _take(ref: dict[str, Any]) -> dict[str, Any]:
    """The parked record `{body, sender, professional_id?, role?}` for `ref`, checked against its bytes and sha256.
    A miss is asked again once after REF_RETRY_S, since the relay wrote it at the sender's location."""
    for wait in (0, REF_RETRY_S):
        time.sleep(wait)
        try:
            parked = net.post("/admin/body", {"ref": ref["ref"]})
            break
        except NetworkError as exc:
            if exc.status != 404:
                raise
    else:
        raise Refused(f"{net.relay()} does not hold this body (already taken, expired, or a relay that does not wake you)",
                      code="REF_NOT_FOUND", hint="tell the stewards which relay you asked when no wake was taken before")
    body = parked.get("body")
    raw = body.encode("utf-8") if isinstance(body, str) else b""
    if len(raw) != ref["bytes"] or not hmac.compare_digest(sha256_hex(raw), ref["sha256"]):
        raise Refused("the body the relay returned does not match the relay_ref", code="REF_MISMATCH")
    return parked


def _deleted_pseudonym(store: Store, contributor_id: str) -> bool:
    """True when a deletion receipt (which holds sha256(contributor_id), never the pseudonym) covers this pseudonym:
    the client may already have dropped the only proof that could delete a later package."""
    digest = sha256_hex(contributor_id)
    return any(hmac.compare_digest(str(read_json(store, p).get("contributor_id_sha256")), digest)
               for p in store.list(DELETIONS) if p.endswith(".json"))


def _contribution(store: Store, doc: dict[str, Any], text: str, rid: str) -> dict[str, Any]:
    if _deleted_pseudonym(store, doc["contributor_id"]):
        raise Refused("a delete_all receipt already covers this contributor; the package is not stored",
                      code="ALREADY_DELETED",
                      hint="leave --delete only after the contribution is stored; a re-join cannot delete the old pseudonym")
    store.write(index.package_path(doc["contributor_id"], rid), text)
    entry = index.add_contribution(store, doc, rid)
    return {"status": entry["status"], "packages": len(entry["receipts"])}


def _withdrawal(store: Store, doc: dict[str, Any], rid: str) -> dict[str, Any]:
    cid, idx = doc["contributor_id"], index.load(store)
    if doc["request"] == Request.STOP:
        if cid in idx:
            idx[cid]["status"] = ContributorStatus.WITHDRAWN.value
            index.save(store, idx)
        return {"status": ContributorStatus.WITHDRAWN.value}
    packages = [p for p in store.list(f"intake/{cid}") if p.endswith(".json")]
    # Tombstone before the files go, so a contribution intake that starts after this one still sees the delete.
    write_json(store, f"{DELETIONS}/{rid}.json", {
        "receipt": rid, "at": clock.stamp(), "contributor_id_sha256": sha256_hex(cid),
        "packages_deleted": len(packages)})
    gone = store.delete(f"intake/{cid}")
    if idx.pop(cid, None) is not None:
        index.save(store, idx)
    out = {"status": ContributorStatus.DELETED.value, "packages_deleted": len(packages)}
    if not gone:
        audit.record(store, "deletion_trashed", rid)
        out["trashed"] = True  # in the drive's trash until it is emptied (30 days at most): WIRE §6
    return out


def _taken(store: Store, id_: str) -> bool:
    return bool(ledger.entries(store, id_=id_)) or bool(store.list(f"findings/{id_}"))


def _professional(store: Store, doc: dict[str, Any], text: str, rid: str, role: str | None) -> dict[str, Any]:
    kind = Kind(doc["kind"])
    key, name = ("preregistration_id", "preregistration") if kind == Kind.PREREGISTRATION else ("finding_id", "finding")
    id_ = doc[key]
    if _taken(store, id_):
        raise Refused("the id is already in the ledger", code="DUPLICATE_ID", hint="make a new random id")
    common = {"professional_id": doc["professional_id"], "release": doc["release"], "receipt": rid, "role": role}
    if kind == Kind.PREREGISTRATION:
        if doc["release"].strip(".") == "" or not store.exists(release.path(doc["release"])):  # "." is no release
            raise Refused("the release does not exist", code="UNKNOWN_RELEASE")
        if any(not ledger.first(store, Entry.FINDING, p) for p in doc["parents"]):
            raise Refused("a parent is not a finding in the ledger", code="UNKNOWN_PARENT")
        extra: dict[str, Any] = {"parents": doc["parents"]}
    else:
        pre = ledger.first(store, Entry.PREREGISTRATION, doc["preregistration_id"])
        if pre is None or pre["professional_id"] != doc["professional_id"]:
            raise Refused("a finding needs the same professional's preregistration", code="NOT_PREREGISTERED")
        if any(f["preregistration_id"] == doc["preregistration_id"] for f in ledger.entries(store, Entry.FINDING)):
            raise Refused("the preregistration already has a finding", code="DUPLICATE_ID")
        extra = {"preregistration_id": doc["preregistration_id"]}
    store.write(findings.path(id_, name), text)
    ledger.append(store, Entry(name), id_, **common, **extra)
    status = FindingStatus.PREREGISTERED if kind == Kind.PREREGISTRATION else FindingStatus.SUBMITTED
    return {"id": id_, "status": status.value}


def _post_ack(store: Store, ref: str, ack: dict[str, Any]) -> None:
    """The outcome for the sender's ticket (WIRE §4): never a body value, only receipt, kind, status and counts, or the
    refusal code and rules. A relay that cannot be reached leaves the ticket pending; intake itself stands."""
    try:
        net.post("/admin/ack", {"ref": ref, "ack": ack})
    except EnvironmentProblem:
        audit.record(store, "ack_failed", ref)


def intake(store: Store, text: str) -> dict[str, Any]:
    """Take, gate, store and index the message a webhook `relay_ref` points to; the receipt for the sender, also
    posted to the relay under the ref (the sender's ticket). `REF_NOT_FOUND` posts nothing: the body was already
    taken (a repeated wake) and that run's ACK stands, or it expired. Any other error still answers the ticket."""
    ref = _ref(text)
    try:
        parked = _take(ref)
        out = admit(store, parked["body"], parked.get("sender"), parked.get("professional_id"), parked.get("role"))
    except MotherError as exc:
        if exc.code != "REF_NOT_FOUND":
            rules = [{"path": v["path"], "rule": v["rule"]} for v in exc.violations or []]
            _post_ack(store, ref["ref"], {"ok": False, "error": exc.code, "violations": rules})
        raise
    except Exception:  # noqa: BLE001 - the sender's ticket never stays pending on a bug
        _post_ack(store, ref["ref"], {"ok": False, "error": "INTERNAL"})
        raise
    _post_ack(store, ref["ref"], {"ok": True, **out})
    return out


def admit(store: Store, text: str, sender: str = Sender.CONTRIBUTOR, professional_id: str | None = None,
          role: str | None = None) -> dict[str, Any]:
    """Gate, store and index one message body (`text`, exactly as the sender sent it) from `sender`."""
    doc = gate.checked(text, sender, professional_id)
    kind, rid, text = Kind(doc["kind"]), new_receipt(), gate.canonical(doc)
    if kind == Kind.CONTRIBUTION:
        data = _contribution(store, doc, text, rid)
    elif kind == Kind.WITHDRAWAL:
        data = _withdrawal(store, doc, rid)
    elif kind == Kind.REGISTRY_UPDATE:
        store.write(notes.path(rid), text)
        data = {"status": NoteStatus.PENDING_REVIEW.value}
    else:
        data = _professional(store, doc, text, rid, role)
    audit.record(store, "intake", f"{kind}/{rid}")
    return {"receipt": rid, "kind": kind.value, **data}

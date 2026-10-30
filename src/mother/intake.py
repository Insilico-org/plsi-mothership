"""`mother intake`: one relayed message, re-checked by the gate and stored per WIRE §6 with a receipt
`r_<16 hex>`. Contributions go to intake/<contributor_id>/, withdrawals stop or delete everything of the contributor
(delete_all only with the proof; the deletion receipt is written first). A contribution for a pseudonym that already
has a deletion receipt is refused (`ALREADY_DELETED`) and stored nowhere. Registry updates wait in notes/ for
review, preregistrations and findings enter the ledger. A research note never comes through the relay: the gate
refuses it here (it arrives by email, `mother research intake`). A refused message stores nothing.

The webhook carries only a `relay_ref` (Grok Bot wakes show only ~4 KiB of a body): the body and its sender (and a
professional's pid and role) are taken from the relay with the intake key, so nothing on the webhook is trusted, and the
outcome goes back to the relay as the sender's ACK, `INTERNAL` on any unexpected error. What is stored is the gated
document re-serialised canonically, never the sender's raw text."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from typing import Any

from mother import audit, clock, gate, index, ledger, net, release
from mother.errors import EnvironmentProblem, GateFailed, MotherError, NetworkError, Refused, violation
from mother.store import Store, read_json, write_json
from mother.vocab import ContributorStatus, Entry, FindingStatus, Kind, NoteStatus, Request, Sender


REF_KEYS = {"kind", "schema_version", "ref", "sha256", "bytes"}
REF_RETRY_S = 65  # a KV write can take up to ~60 s to show at another Cloudflare location


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
        raise Refused("the relay does not hold this body (already taken or expired)", code="REF_NOT_FOUND")
    body = parked.get("body")
    raw = body.encode("utf-8") if isinstance(body, str) else b""
    if len(raw) != ref["bytes"] or not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), ref["sha256"]):
        raise Refused("the body the relay returned does not match the relay_ref", code="REF_MISMATCH")
    return parked


def receipt() -> str:
    return f"r_{secrets.token_hex(8)}"


def _pseudonym_hash(contributor_id: str) -> str:
    return hashlib.sha256(contributor_id.encode("ascii")).hexdigest()


def _deleted_pseudonym(store: Store, contributor_id: str) -> bool:
    """True when a delete_all receipt already covers this pseudonym.

    Receipts store sha256(contributor_id), not the pseudonym. A match means a later contribution must not be
    stored: the withdrawal may have run before this package arrived, and the client may already have dropped
    the only proof that could delete it.
    """
    digest = _pseudonym_hash(contributor_id)
    for path in store.list("deletions"):
        if not path.endswith(".json"):
            continue
        got = read_json(store, path).get("contributor_id_sha256")
        if isinstance(got, str) and len(got) == len(digest) and hmac.compare_digest(got, digest):
            return True
    return False


def _contribution(store: Store, doc: dict[str, Any], text: str, rid: str) -> dict[str, Any]:
    if _deleted_pseudonym(store, doc["contributor_id"]):
        raise Refused(
            "a delete_all receipt already covers this contributor; the package is not stored",
            code="ALREADY_DELETED",
            hint="leave --delete only after the contribution is stored; a re-join cannot delete the old pseudonym")
    store.write(f"intake/{doc['contributor_id']}/{rid}.json", text)
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
    write_json(store, f"deletions/{rid}.json", {
        "receipt": rid, "at": clock.stamp(), "contributor_id_sha256": _pseudonym_hash(cid),
        "packages_deleted": len(packages)})
    store.delete(f"intake/{cid}")
    if idx.pop(cid, None) is not None:
        index.save(store, idx)
    return {"status": ContributorStatus.DELETED.value, "packages_deleted": len(packages)}


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
    store.write(f"findings/{id_}/{name}.json", text)
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
    doc = gate.parse(text)
    if found := gate.violations(doc, sender, professional_id):
        raise GateFailed(found)
    kind, rid, text = Kind(doc["kind"]), receipt(), gate.canonical(doc)
    if kind == Kind.CONTRIBUTION:
        data = _contribution(store, doc, text, rid)
    elif kind == Kind.WITHDRAWAL:
        data = _withdrawal(store, doc, rid)
    elif kind == Kind.REGISTRY_UPDATE:
        store.write(f"notes/{rid}.json", text)
        data = {"status": NoteStatus.PENDING_REVIEW.value}
    else:
        data = _professional(store, doc, text, rid, role)
    audit.record(store, "intake", f"{kind}/{rid}")
    return {"receipt": rid, "kind": kind.value, **data}

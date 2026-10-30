"""`mother professional invite|invites|revoke`: after a steward's manual review (skill `verify`), the relay makes a
one-time invite link that the Mother emails to the verified address; the professional opens it, passes Turnstile and
gets the token once (WIRE §4). The link is a secret until claimed: it is written to the file the steward names, never
printed. The Mother's storage keeps only the invite id and grant (`professional/invites.jsonl`; the pid is read from the
relay), never the email, name or token; the identity record stays in the steward's own registry. These commands need
MOTHER_RELAY_ADMIN_KEY: a steward runs them from their own terminal, never the bot."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from mother import audit, clock, net
from mother.errors import NetworkError, UsageError
from mother.store import Store, append_jsonl, read_jsonl
from mother.vocab import Role, Scope

MAX_DAYS = 366
INVITES = "professional/invites.jsonl"
STATUS_BATCH = 100


def invite(store: Store, role: str, scopes: list[str], days: int, out: str) -> dict[str, Any]:
    role = Role(role).value
    scopes = sorted({Scope(s).value for s in scopes})
    if not scopes or not 0 < days <= MAX_DAYS:
        raise UsageError(f"give at least one scope and 1-{MAX_DAYS} days")
    reply = net.post("/admin/invite", {"role": role, "scopes": scopes, "days": days})
    if not reply.get("ok") or not all(reply.get(k) for k in ("id", "link", "exp")):
        raise NetworkError("the relay did not make an invite (id, link and exp)", code="RELAY_REFUSED")
    path = Path(out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(reply["link"] + "\n", encoding="utf-8", newline="\n")
    os.chmod(path, 0o600)
    row = {"id": reply["id"], "role": role, "scopes": scopes, "days": days, "invited": clock.stamp(), "exp": reply["exp"]}
    append_jsonl(store, INVITES, row)
    audit.record(store, "professional_invite", reply["id"])
    return {"invite": reply["id"], "role": role, "scopes": scopes, "days": days, "exp": reply["exp"],
            "link_file": str(path)}


def invites(store: Store) -> list[dict[str, Any]]:
    """Every invite with its status at the relay: pending, claimed (with the pid to revoke), expired or unknown."""
    rows = read_jsonl(store, INVITES)
    status: dict[str, Any] = {}
    for i in range(0, len(rows), STATUS_BATCH):
        reply = net.post("/admin/invite/status", {"ids": [r["id"] for r in rows[i:i + STATUS_BATCH]]})
        if not reply.get("ok") or not isinstance(reply.get("invites"), dict):
            raise NetworkError("the relay did not report the invites", code="RELAY_REFUSED")
        status |= reply["invites"]
    audit.record(store, "professional_invites", INVITES)
    return [row | status.get(row["id"], {"status": "unknown"}) for row in rows]


def revoke(store: Store, pid: str) -> dict[str, Any]:
    reply = net.post("/admin/revoke", {"id": pid})
    if not reply.get("ok"):
        raise NetworkError("the relay did not revoke the token", code="RELAY_REFUSED")
    audit.record(store, "professional_revoke", pid)
    return {"revoked": pid}

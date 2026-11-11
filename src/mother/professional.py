"""`mother professional invite|invites|revoke` (WIRE §4), run from a steward's terminal (MOTHER_RELAY_ADMIN_KEY) after
the manual review of skill `verify`. The relay makes a one-time invite link, a secret until claimed: it is written to
the file the steward names (owner-only), never printed, and the Mother emails it to the verified address. The store
keeps only the invite id and grant (`professional/invites.jsonl`; the pid comes from the relay), never an email, name
or token."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from mother import audit, clock, net
from mother.errors import UsageError
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
    reply = net.call("/admin/invite", {"role": role, "scopes": scopes, "days": days},
                     "make an invite (id, link and exp)", "id", "link", "exp")
    path = Path(out)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # owner-only from creation
    os.chmod(path, 0o600)  # a file that already existed keeps its old mode otherwise
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
        f.write(reply["link"] + "\n")
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
        status |= net.call("/admin/invite/status", {"ids": [r["id"] for r in rows[i:i + STATUS_BATCH]]},
                           "report the invites", "invites")["invites"]
    audit.record(store, "professional_invites", INVITES)
    return [row | status.get(row["id"], {"status": "unknown"}) for row in rows]


def revoke(store: Store, pid: str) -> dict[str, Any]:
    net.call("/admin/revoke", {"id": pid}, "revoke the token")
    audit.record(store, "professional_revoke", pid)
    return {"revoked": pid}

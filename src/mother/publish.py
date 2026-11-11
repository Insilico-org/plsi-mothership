"""`mother publish catalog|release|findings`: push a built document to the relay's public KV (`/admin/publish`). A
steward runs it from their own terminal (it needs MOTHER_RELAY_ADMIN_KEY, which the bot never holds); the relay serves
it, the store keeps the source."""

from __future__ import annotations

from typing import Any

from mother import audit, catalog, findings, net, release
from mother.errors import UsageError
from mother.store import Store
from mother.vocab import Publication


def payload(store: Store, what: str, version: str | None = None) -> dict[str, Any]:
    what = Publication(what)
    if version and what != Publication.RELEASE:
        raise UsageError("--version goes with publish release only")
    if what == Publication.CATALOG:
        return {"what": what.value, "body": catalog.load(store)}
    if what == Publication.RELEASE:
        if not version:
            raise UsageError("publish release needs --version")
        return {"what": what.value, "version": version, "body": release.load(store, version)}
    return {"what": what.value, "body": findings.public(store)}


def publish(store: Store, what: str, version: str | None = None) -> dict[str, Any]:
    body = payload(store, what, version)
    reply = net.call("/admin/publish", body, f"publish the {body['what']}")
    audit.record(store, f"publish_{body['what']}", version or body["what"])
    return {"published": body["what"], "version": version, "reply": {"ok": bool(reply.get("ok"))}}

"""`mother publish catalog|release|findings`: push a built document to the relay's public KV (`/admin/publish`). A
steward runs it from their own terminal (it needs MOTHER_RELAY_ADMIN_KEY, which the bot never holds); the relay serves
it, the store keeps the source."""

from __future__ import annotations

from typing import Any

from mother import audit, catalog, findings, net, release
from mother.errors import NotFound, UsageError
from mother.store import Store, read_json
from mother.vocab import Publication


def payload(store: Store, what: str, version: str | None = None) -> dict[str, Any]:
    """The `/admin/publish` body for `what`."""
    what = Publication(what)
    if what == Publication.CATALOG:
        if not store.exists(catalog.PATH):
            raise NotFound("no catalog is built yet", hint="mother catalog")
        return {"what": what.value, "body": read_json(store, catalog.PATH)}
    if what == Publication.RELEASE:
        if not version:
            raise UsageError("publish release needs --version")
        if not store.exists(release.path(version)):
            raise NotFound("the release is not built", hint="mother release build --version <v>")
        return {"what": what.value, "version": version, "body": release.load(store, version)}
    return {"what": what.value, "body": findings.public(store)}


def publish(store: Store, what: str, version: str | None = None) -> dict[str, Any]:
    body = payload(store, what, version)
    reply = net.post("/admin/publish", body)
    audit.record(store, f"publish_{body['what']}", version or body["what"])
    return {"published": body["what"], "version": version, "reply": {"ok": bool(reply.get("ok"))}}

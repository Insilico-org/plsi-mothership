"""`mother project` (WIRE §7): published research projects. A steward publishes an accepted note's project into a
stable, shareable Drive folder at projects/<project_id>/ and records it in the registry projects/index.json; the
folder is never moved or deleted, so its link and the collaborators' access last for life. The canonical note pipeline
(research/) is untouched: a living folder is never read as pipeline input or as a source for any published document.

The project OWNER grows and shrinks the team themselves: they email the Mother a project-access message (`mother
project invite --eml`), intake verifies DMARC==pass and that the sender is the project's owner_address, then its
`action` either gives each address Drive WRITER on the folder and records it (invite, the default) or removes that
access and drops the address from the registry (revoke, the symmetric inverse; idempotent). Invitees need only a
Google account. The public table (publish projects) is built only from the registry and carries no address and no
invitee list."""

from __future__ import annotations

import json
from typing import Any

from mother import audit, clock, gate, mail, research
from mother.errors import GateFailed, NotFound, Refused, violation
from mother.store import Store, read_json, write_json
from mother.vocab import Dmarc, Kind, ProjectAction, ProjectStatus, Sender

ROOT = "projects"
INDEX = "projects/index.json"
ACCEPTED = "research/accepted/"
SUBJECT = "PLSI project invite:"
PUBLICATION = "publication"  # the subfolder a gazette-stage project public-shares; the working dir never is


def _load(store: Store) -> dict[str, dict[str, Any]]:
    return read_json(store, INDEX, {})


def _save(store: Store, index: dict[str, dict[str, Any]]) -> None:
    write_json(store, INDEX, index)


def _folder(project_id: str) -> str:
    return f"{ROOT}/{project_id}"


def _publication(project_id: str) -> str:
    return f"{ROOT}/{project_id}/{PUBLICATION}"


def _entry(store: Store, project_id: str) -> dict[str, Any]:
    entry = _load(store).get(project_id)
    if entry is None:
        raise NotFound("not a published project", hint="mother project list")
    return entry


def _summary(entry: dict[str, Any]) -> dict[str, Any]:
    """A steward's view: never the owner's or an invitee's address. `link` is the public gazette link, None until the
    project goes to the gazette (the workspace is never public-shared)."""
    return {"project_id": entry["project_id"], "title": entry["title"], "owner": entry["owner_id"],
            "status": entry["status"], "created": entry["created"], "invitees": len(entry["invitees"]),
            "link": entry.get("publication_url")}


def _accepted_tags(store: Store, project_id: str) -> list[dict[str, Any]]:
    """The deduped MeSH tags of the project's accepted note, found by scanning research/accepted/ for the note whose
    project.id is this project (each tag {mesh, label, tree}, first occurrence kept, distinct by mesh id)."""
    seen: set[str] = set()
    tags: list[dict[str, Any]] = []
    for path in store.list(ACCEPTED):
        if not path.endswith("/note.json"):
            continue
        note = json.loads(store.read(path))
        if (note.get("project") or {}).get("id") != project_id:
            continue
        for item in note.get("items") or []:
            for tag in item.get("tags") or []:
                if tag["mesh"] not in seen:
                    seen.add(tag["mesh"])
                    tags.append({"mesh": tag["mesh"], "label": tag["label"], "tree": tag["tree"]})
    return tags


def create_project(store: Store, receipt: str) -> dict[str, Any]:
    """Open the WORKSPACE stage of an ACCEPTED note's project: make the stable, invite-only folder and register it.
    Nothing is public-shared here — the workspace is private; `mother project gazette` later takes it public."""
    at = research.folder(store, receipt)
    if not at.startswith(ACCEPTED):
        raise Refused("only an accepted research note's project can be published", code="NOT_ACCEPTED",
                      hint="mother research review then mother research decide --decision accepted")
    note = json.loads(store.read(f"{at}/note.json"))
    project = note.get("project")
    if not project:
        raise Refused("the note names no project to publish", code="NO_PROJECT")
    project_id, index = project["id"], _load(store)
    if project_id in index:
        raise Refused("the project is already published", code="ALREADY_PUBLISHED", hint="mother project status --id <id>")
    folder = _folder(project_id)
    store.write(f"{folder}/.keep", "")  # make the stable working folder without public-sharing it (no store.link)
    entry = {"project_id": project_id, "title": project["title"], "owner_id": at.split("/")[2],
             "owner_address": read_json(store, f"{at}/sender.json")["address"], "folder_id": folder,
             "publication_url": None, "status": ProjectStatus.WORKSPACE.value, "created": clock.stamp(), "invitees": []}
    index[project_id] = entry
    _save(store, index)
    audit.record(store, "project_publish", project_id)
    return _summary(entry)


def go_gazette(store: Store, project_id: str) -> dict[str, Any]:
    """The GAZETTE stage: a steward approves and takes the project public. Only projects/<id>/publication/ is ever
    public-shared (never the working dir); the MeSH tags of the accepted note are aggregated and the status becomes
    gazette. Refused if the team has not created the publication/ subfolder yet."""
    _entry(store, project_id)
    publication = _publication(project_id)
    if not store.exists(publication):
        raise Refused("the project has no publication/ subfolder to present yet", code="NO_PUBLICATION",
                      hint="the team adds projects/<id>/publication/ with what to show, then run this again")
    index = _load(store)
    entry = index[project_id]
    entry["publication_url"] = store.link(publication)  # ONLY the publication subfolder is public-shared
    entry["tags"] = _accepted_tags(store, project_id)
    entry["status"] = ProjectStatus.GAZETTE.value
    _save(store, index)
    audit.record(store, "project_gazette", project_id)
    return _summary(entry)


def _owned(store: Store, project_id: str, sender_address: str, dmarc: str) -> dict[str, Any]:
    """The project's registry index after the owner-gate: DMARC pass and the sender is the project's owner, else
    Refused NOT_OWNER. Shared by invite() and revoke(), its symmetric inverse."""
    entry = _entry(store, project_id)
    if dmarc != Dmarc.PASS or sender_address.strip().lower() != entry["owner_address"].strip().lower():
        raise Refused("only the project owner may change the team, with DMARC pass", code="NOT_OWNER")
    return _load(store)


def invite(store: Store, project_id: str, emails: list[str], sender_address: str, dmarc: str) -> dict[str, Any]:
    """Owner-gated self-serve collaborators: DMARC pass and the sender must be the project's owner, then each address
    is granted Drive WRITER on the living folder and recorded. An address already invited is granted again, not doubled."""
    index = _owned(store, project_id, sender_address, dmarc)
    entry = index[project_id]
    known = {i["email"].strip().lower() for i in entry["invitees"]}  # dedup case-insensitively, as revoke() matches
    for email in emails:
        store.grant(_folder(project_id), email, "writer")
        if (key := email.strip().lower()) not in known:
            entry["invitees"].append({"email": email, "at": clock.stamp()})
            known.add(key)
    _save(store, index)
    audit.record(store, "project_invite", project_id)
    return {"project_id": project_id, "title": entry["title"], "invited": list(emails),
            "link": entry.get("publication_url")}


def revoke(store: Store, project_id: str, emails: list[str], sender_address: str, dmarc: str) -> dict[str, Any]:
    """The symmetric inverse of invite(): same owner-gate, then each address loses Drive WRITER on the living folder
    and leaves the invitee list. Idempotent: revoking an address that holds no access is a no-op, never an error.
    Only projects/<id>/ is touched; research/ and every other project are untouched."""
    index = _owned(store, project_id, sender_address, dmarc)
    entry = index[project_id]
    dropped = {e.strip().lower() for e in emails}
    for email in emails:
        store.revoke(_folder(project_id), email)
    entry["invitees"] = [i for i in entry["invitees"] if i["email"].strip().lower() not in dropped]
    _save(store, index)
    audit.record(store, "project_revoke", project_id)
    return {"project_id": project_id, "title": entry["title"], "revoked": list(emails),
            "link": entry.get("publication_url")}


def intake(store: Store, raw: bytes) -> dict[str, Any]:
    """One project-access email: parsed, gated and routed by its action to the owner-gated invite() or revoke()."""
    address, text, dmarc, media = mail.read(raw, SUBJECT, "NO_PROJECT_INVITE")
    doc = gate.checked(text, Sender.EMAIL)
    if doc["kind"] != Kind.PROJECT_INVITE:
        raise GateFailed([violation("kind", "not_project_invite")])
    act = revoke if doc.get("action", ProjectAction.INVITE) == ProjectAction.REVOKE else invite
    out = act(store, doc["project_id"], doc["emails"], address, dmarc)
    return {**out, "dmarc": dmarc, "reply_to": address, **mail.ignored(media)}


def listing(store: Store) -> list[dict[str, Any]]:
    audit.record(store, "project_list", INDEX)
    return [_summary(e) for e in sorted(_load(store).values(), key=lambda e: (e["created"], e["project_id"]))]


def status(store: Store, project_id: str) -> dict[str, Any]:
    entry = _entry(store, project_id)
    audit.record(store, "project_status", project_id)
    return _summary(entry)


def _gazette_rows(store: Store) -> list[dict[str, Any]]:
    """The gazette-stage registry rows, oldest first; a workspace-stage project appears in no public surface."""
    return [e for e in sorted(_load(store).values(), key=lambda e: (e["created"], e["project_id"]))
            if e["status"] == ProjectStatus.GAZETTE]


def public(store: Store) -> dict[str, Any]:
    """The token-gated researcher table (WIRE §4): id, title, the public link, pseudonymous owner and when it was
    published. Only gazette-stage projects, built only from the registry; never an address, never the invitee list."""
    projects = [{"project_id": e["project_id"], "title": e["title"], "link": e["publication_url"],
                 "owner": e["owner_id"], "published": e["created"]}
                for e in _gazette_rows(store)]
    return {"built": clock.month(), "projects": projects}


def gazette_public(store: Store) -> dict[str, Any]:
    """The fully public gazette (WIRE §7): each gazette-stage project with its MeSH tags and the publication link, for
    the booru-style browser. Built only from the registry; never an address, never the invitee list."""
    projects = [{"project_id": e["project_id"], "title": e["title"], "publication_url": e["publication_url"],
                 "published": e["created"], "tags": e.get("tags") or []}
                for e in _gazette_rows(store)]
    return {"built": clock.month(), "projects": projects}

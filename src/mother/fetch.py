"""`mother research fetch`: a Drive file shared with the service account, checked before anyone opens it."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from mother import audit, filecheck, gate, research
from mother.errors import NotFound, Refused, UsageError
from mother.store import Store

DRIVE_ID = re.compile(r"/d/([A-Za-z0-9_-]{20,})|[?&]id=([A-Za-z0-9_-]{20,})|^([A-Za-z0-9_-]{20,})$")


def fetch(store: Store, link: str, check_only: bool, out: str | None) -> dict[str, Any]:
    """--check reads metadata only; otherwise the checked copy goes to --out, never to the store."""
    found = DRIVE_ID.search(link.strip())
    if not found:
        raise UsageError("give a Google Drive file link")
    info, data = _drive(store, next(g for g in found.groups() if g), check_only)
    if data is None:
        return info
    if out is None:
        raise UsageError("--out names where the checked copy goes")
    result = filecheck.check(str(info["name"]), data)
    Path(out).write_bytes(result["data"])
    return {**info, "kind": result["kind"], "violations": result["violations"], "out": out}


def fetch_note(store: Store, rid: str, name: str, keep: bool = False) -> dict[str, Any]:
    """A note's Drive file, read only when its bytes match the note's sha256 and pass the checks; stored only with
    `keep` (a steward's choice), then moved with the note when it is accepted."""
    folder = research._dir(store, rid)
    entry = next((e for e in gate.parse(store.read(f"{folder}/note.json")).get("files") or []
                  if e["name"] == name and "drive_id" in e), None)
    if entry is None:
        raise NotFound("the note lists no Drive file of that name")
    info, _ = _drive(store, entry["drive_id"], True)
    if info["sha256"] != entry["sha256"]:
        raise Refused("the Drive file is not the one the note names", code="HASH_MISMATCH")
    _, data = _drive(store, entry["drive_id"], False)
    if hashlib.sha256(data).hexdigest() != entry["sha256"]:  # the file may change between the two reads
        raise Refused("the Drive file is not the one the note names", code="HASH_MISMATCH")
    result = filecheck.check(name, data)
    if result["violations"] or result["data"] != data:
        return {"name": name, "violations": result["violations"] or [{"path": "file", "rule": "formula"}]}
    if keep:
        store.write(f"{folder}/files/{name}", data.decode("utf-8"))
        audit.record(store, "research_keep", rid)
    return {"name": name, "violations": [], "kept": keep,
            "content": {"kind": "external_text", "text": data.decode("utf-8")}}


def _drive(store: Store, file_id: str, check_only: bool) -> tuple[dict[str, Any], bytes | None]:
    """The file's metadata, and its bytes unless `check_only`; media and Google Docs are refused before download."""
    if not hasattr(store, "shared"):
        raise UsageError("fetch needs the Drive store")
    try:
        meta = store.shared(file_id)
    except Exception as exc:  # noqa: BLE001 - Drive's refusal is reported, never its text
        raise Refused("the file is not shared with the service account", code="NOT_SHARED") from exc
    if str(meta.get("mimeType", "")).startswith("application/vnd.google-apps."):
        raise Refused("a Google Docs, Sheets or Slides file has no fixed bytes; share an uploaded file",
                      code="NOT_A_FILE")
    info = {"name": meta.get("name"), "type": meta.get("mimeType"), "size": int(meta.get("size") or 0),
            "sha256": meta.get("sha256Checksum"), "revision": meta.get("headRevisionId"),
            "modified": meta.get("modifiedTime")}
    if str(info["type"]).startswith(("image/", "video/", "audio/")):
        raise Refused("images, video and audio are never fetched", code="BAD_FILE_TYPE")
    if info["size"] > filecheck.MAX_BYTES:
        raise Refused("file too large", code="BAD_FILE")
    audit.record(store, "research_fetch_check" if check_only else "research_fetch", file_id)
    if check_only:
        return info, None
    try:
        data = store.shared_bytes(file_id)
    except Exception as exc:  # noqa: BLE001
        raise Refused("Google refused the download (possibly flagged as malware)", code="DOWNLOAD_REFUSED") from exc
    if info["sha256"] and hashlib.sha256(data).hexdigest() != info["sha256"]:
        raise Refused("the bytes do not match Drive's sha256", code="HASH_MISMATCH")
    return info, data

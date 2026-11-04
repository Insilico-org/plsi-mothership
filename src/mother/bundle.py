"""The zip a research note's email carries: exactly the note's email files, each matching its sha256 and checked."""

from __future__ import annotations

import hashlib
import io
import zipfile
from typing import Any

from mother import filecheck, note
from mother.errors import GateFailed, Refused, violation

MAX_ZIP = 10_000_000
MAX_TOTAL = 20_000_000
MAX_RATIO = 100  # unpacked/packed, for members over 1 MB


def _members(data: bytes) -> list[tuple[zipfile.ZipInfo, bytes]]:
    if len(data) > MAX_ZIP:
        raise Refused("the zip is too large", code="BAD_BUNDLE")
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
        infos = zf.infolist()
    except (zipfile.BadZipFile, ValueError):
        raise Refused("not a zip file", code="BAD_BUNDLE") from None
    if len(infos) > note.MAX_FILES or sum(i.file_size for i in infos) > MAX_TOTAL:
        raise Refused("too many or too large files in the zip", code="BAD_BUNDLE")
    out = []
    for info in infos:
        if info.is_dir() or info.flag_bits & 0x1 or not note.FILE_NAME.fullmatch(info.filename) \
                or info.file_size > filecheck.MAX_BYTES \
                or info.file_size > 1_000_000 and info.file_size > MAX_RATIO * max(info.compress_size, 1):
            raise Refused("a zip member is a folder, encrypted, badly named or too large", code="BAD_BUNDLE")
        with zf.open(info) as f:
            body = f.read(filecheck.MAX_BYTES + 1)
        if len(body) > filecheck.MAX_BYTES:
            raise Refused("a zip member is larger than it says", code="BAD_BUNDLE")
        out.append((info, body))
    return out


def unpack(doc: dict[str, Any], data: bytes | None) -> dict[str, str]:
    """{name: text} of the note's email files; raises unless the zip holds exactly them, unchanged and clean."""
    wanted = {e["name"]: e["sha256"] for e in note.by_email(doc)}
    if not wanted:
        if data is not None:
            raise Refused("the email carries a zip the note does not list", code="BAD_BUNDLE")
        return {}
    if data is None:
        raise Refused("the note lists files but the email carries no zip", code="NO_BUNDLE")
    members = {info.filename: body for info, body in _members(data)}
    if set(members) != set(wanted):
        raise Refused("the zip does not hold exactly the note's files", code="BAD_BUNDLE")
    found, out = [], {}
    for n, entry in enumerate(doc["files"]):
        name = entry["name"]
        if name not in members:
            continue
        body = members[name]
        if hashlib.sha256(body).hexdigest() != wanted[name]:
            raise Refused(f"{name} does not match its sha256 in the note", code="HASH_MISMATCH")
        checked = filecheck.check(name, body)
        found += [violation(f"files[{n}]", v["rule"]) for v in checked["violations"]]
        if checked["data"] != body:
            found.append(violation(f"files[{n}]", "formula"))
        out[name] = body.decode("utf-8")
    if found:
        raise GateFailed(found)
    return out

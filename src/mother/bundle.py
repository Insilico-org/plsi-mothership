"""The zip a research note's email carries: exactly the note's email files, each matching its sha256 and checked."""

from __future__ import annotations

import hashlib
import io
import zipfile
import zlib
from typing import Any

from mother import filecheck, note
from mother.errors import GateFailed, Refused, violation

MAX_ZIP = 10_000_000
MAX_TOTAL = 20_000_000
MAX_RATIO = 100  # unpacked/packed, for members over RATIO_FROM bytes
RATIO_FROM = 1_000_000
READ_ERRORS = (zipfile.BadZipFile, zlib.error, NotImplementedError, EOFError, ValueError)  # a damaged or odd member
NOTE = "note.json"  # a note too long for the inbox's 32 KB text limit travels in the zip under this name


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
                or info.file_size > RATIO_FROM and info.file_size > MAX_RATIO * max(info.compress_size, 1):
            raise Refused("a zip member is a folder, encrypted, badly named or too large", code="BAD_BUNDLE")
        try:
            with zf.open(info) as f:
                out.append((info, f.read()))  # at most file_size bytes, CRC-checked
        except READ_ERRORS:
            raise Refused("a zip member cannot be read", code="BAD_BUNDLE") from None
    return out


def note_in(data: bytes | None) -> bytes | None:
    """The note itself, when the zip carries it as note.json (a note too long to attach as text)."""
    if data is None:
        return None
    return next((body for info, body in _members(data) if info.filename == NOTE), None)


def unpack(doc: dict[str, Any], data: bytes | None) -> dict[str, str]:
    """{name: text} of the note's email files; raises unless the zip holds exactly them (and the note itself, when
    it came in the zip), unchanged and clean."""
    emailed = note.by_email(doc)
    members = {info.filename: body for info, body in _members(data)} if data is not None else {}
    members.pop(NOTE, None)  # the note itself, read by intake
    if not emailed:
        if members:
            raise Refused("the email carries a zip the note does not list", code="BAD_BUNDLE")
        return {}
    if data is None:
        raise Refused("the note lists files but the email carries no zip", code="NO_BUNDLE")
    if set(members) != {e["name"] for _, e in emailed}:
        raise Refused("the zip does not hold exactly the note's files", code="BAD_BUNDLE")
    found, out = [], {}
    for n, entry in emailed:
        name, body = entry["name"], members[entry["name"]]
        if hashlib.sha256(body).hexdigest() != entry["sha256"]:
            raise Refused(f"{name} does not match its sha256 in the note", code="HASH_MISMATCH")
        checked = filecheck.check(name, body)
        found += [violation(f"files[{n}]", v["rule"]) for v in checked["violations"]]
        if checked["data"] != body:
            found.append(violation(f"files[{n}]", "formula"))
        out[name] = body.decode("utf-8")
    if found:
        raise GateFailed(found)
    return out

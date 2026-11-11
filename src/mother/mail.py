"""Research and feedback emails (WIRE §7, §8), parsed here and never from a command line; image, video and audio
parts are counted, never read. Digests the Mother writes are saved here, never with an address in them."""

from __future__ import annotations

import re
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from pathlib import Path

from mother import gate
from mother.errors import Refused, UsageError
from mother.store import Store
from mother.vocab import Dmarc

ADDRESS = re.compile(r"[^@\s<>\"',;\[\]]+@(?=[^@]*[A-Za-z])[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")  # no IP literal
DMARC = re.compile(r"(?i)\bdmarc\s*=\s*([a-z]+)")
MEDIA = ("image", "video", "audio")
MEDIA_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".heic", ".bmp", ".tif", ".tiff", ".svg", ".mp4", ".mov",
             ".avi", ".mkv", ".webm", ".mp3", ".wav", ".m4a")
BLOCK = re.compile(r"^```plsi[ \t]*\n(.*?)^```[ \t]*$", re.M | re.S)
READ_ERRORS = (ValueError, TypeError, LookupError, AttributeError, IndexError)


def _sender(msg: EmailMessage) -> str:
    """The address of the one From header, else `BAD_SENDER`: one undamaged header with one ASCII addr-spec, its
    domain a host name with a letter (no IP literal)."""
    try:
        headers = msg.get_all("from") or []
        found = headers[0].addresses if len(headers) == 1 and not headers[0].defects else ()
        address = found[0].addr_spec.strip() if len(found) == 1 else ""
    except READ_ERRORS:
        address = ""
    if not (address.isascii() and ADDRESS.fullmatch(address)):
        raise Refused("the email has no single bare From address", code="BAD_SENDER")
    return address.lower()


def _dmarc(msg: EmailMessage) -> str:
    """`dmarc=` of the first (topmost: the receiving server's) Authentication-Results header, comments dropped: `pass`;
    `none` without a header, without a dmarc result or with dmarc=none; `fail` for any other result."""
    try:
        results = msg.get_all("authentication-results") or []
        found = DMARC.search(re.sub(r"\([^()]*\)", " ", str(results[0]))) if results else None
    except READ_ERRORS:
        found = None
    result = found[1].lower() if found else Dmarc.NONE.value
    return result if result in (Dmarc.PASS, Dmarc.NONE) else Dmarc.FAIL.value


def _parse(raw: bytes, code: str) -> EmailMessage:
    """The parsed email, else refused with `code`."""
    try:
        return BytesParser(policy=policy.default).parsebytes(raw)
    except READ_ERRORS:
        raise Refused("the email cannot be read", code=code) from None


def _content(part: EmailMessage) -> str:
    data = part.get_content()
    return data.decode("utf-8", errors="replace") if isinstance(data, bytes) else str(data)


def read(raw: bytes, prefix: str, missing: str, attachment: bytes | None = None) -> tuple[str, str, str, int]:
    """(sender address, the message, DMARC result, media parts ignored) of a raw email: its one .json attachment (or
    `attachment`, saved from it), else its one plsi block. `BAD_SENDER` without one bare From address; `missing`
    without a subject starting `prefix` or exactly one message."""
    msg = _parse(raw, missing)
    address = _sender(msg)
    try:
        subject = str(msg["subject"] or "").strip()  # a folded subject can start with white space
        body = msg.get_body(preferencelist=("plain",))
        text = body.get_content() if body is not None else ""
    except READ_ERRORS:
        raise Refused("the email cannot be read", code=missing) from None
    media = [p for p in msg.iter_attachments()
             if p.get_content_maintype() in MEDIA or (p.get_filename() or "").lower().endswith(MEDIA_EXT)]
    files = [p for p in msg.iter_attachments()
             if p not in media and (p.get_filename() or "").lower().endswith(".json")]
    if attachment is not None and not files:
        found = [attachment.decode("utf-8", errors="replace")]
    elif files:
        found = [_content(p) for p in files]
    else:
        found = BLOCK.findall(text.replace("\r\n", "\n"))
    if not subject.startswith(prefix) or len(found) != 1:
        raise Refused(f"the email holds no {prefix.rstrip(':')} (its subject and one .json file or plsi block)",
                      code=missing)
    return address, found[0], _dmarc(msg), len(media)


def zip_part(raw: bytes) -> bytes | None:
    """The email's one .zip attachment, if any; more than one is refused."""
    msg = _parse(raw, "BAD_BUNDLE")
    zips = [p for p in msg.iter_attachments() if p.get_content_maintype() not in MEDIA
            and ((p.get_filename() or "").lower().endswith(".zip") or p.get_content_type() == "application/zip")]
    if len(zips) > 1:
        raise Refused("the email carries more than one zip", code="BAD_BUNDLE")
    data = zips[0].get_content() if zips else None
    return data if isinstance(data, bytes) or data is None else str(data).encode("utf-8")


def ignored(media: int) -> dict[str, int]:
    """`images_ignored` when the email carried image, video or audio parts."""
    return {"images_ignored": media} if media else {}


def save_digest(store: Store, path: str, source: str, names: str) -> dict[str, str]:
    """Store the digest text the Mother wrote in the file `source` at `path`; refused when it holds an address."""
    try:
        text = Path(source).read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        raise UsageError("a text file cannot be read (UTF-8)") from None
    if gate.PATTERNS["email"].search(text):
        raise Refused("the digest holds an email address", code="DIGEST_HAS_ADDRESS",
                      hint=f"digests name {names}, never a sender")
    store.write(path, text)
    return {"saved": path}

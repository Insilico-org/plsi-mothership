"""Checks on a research file before anyone opens it: text types only, size cap, formulas, charts, text rules."""

from __future__ import annotations

import csv
import io
import json
from typing import Any

from mother import gate, note
from mother.errors import Refused, violation

MAX_BYTES = 9_000_000
MAX_ROWS = 5_000
VEGA_LITE = "https://vega.github.io/schema/vega-lite/"
FORMULA = ("=", "+", "-", "@")


def _kind(name: str, data: bytes) -> str:
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext not in note.FILE_TYPES:
        names = f"{', '.join(note.FILE_TYPES[:-1])} and {note.FILE_TYPES[-1]}"
        raise Refused(f"only {names} files are taken: no images, video or audio", code="BAD_FILE_TYPE")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise Refused("not UTF-8 text", code="BAD_FILE_TYPE") from None
    if any(c < " " and c not in "\t\n\r" for c in text):
        raise Refused("binary bytes in a text file", code="BAD_FILE_TYPE")
    return ext


def _chart(found: list[tuple[str, str, str, bool, Any]]) -> None:
    """A Vega-Lite chart (its gate.strings) draws only the numbers it carries: no outside data, links or images."""
    pairs = [(k, parent[k]) for _, _, k, is_key, parent in found if is_key]
    marks = [v.get("type") if isinstance(v, dict) else v for k, v in pairs if k == "mark"]
    rows = sum(len(v) for k, v in pairs if k == "values" and isinstance(v, list))
    if any(k in ("url", "href") for k, _ in pairs) or "image" in marks or rows > MAX_ROWS:
        raise Refused("a chart may hold only inline numbers: no url, href or image marks", code="BAD_CHART")


def _table(text: str, delimiter: str) -> str:
    out = io.StringIO()
    writer = csv.writer(out, delimiter=delimiter, lineterminator="\n")
    for row in csv.reader(io.StringIO(text), delimiter=delimiter):
        writer.writerow(["'" + c if _formula(c) else c for c in row])
    return out.getvalue()


def _formula(cell: str) -> bool:
    return cell.startswith(FORMULA) and not _number(cell)


def _number(cell: str) -> bool:
    try:
        float(cell)
        return True
    except ValueError:
        return False


def _hits(text: str, numeric: bool = False) -> set[str]:
    """The text rules `text` breaks once its masked forms are blanked; a number cell is no id number."""
    clean = gate.clean(text)
    for pattern in note.FILE_MASKS:
        clean = pattern.sub(" ", clean)
    ids = gate.YEARS.sub(" ", note.STATISTIC.sub(" ", clean))  # what id_number sees: no statistic, no year
    rules = {rule for rule, pattern in gate.PATTERNS.items() if pattern.search(ids if rule == "id_number" else clean)}
    return rules - {"id_number"} if numeric else rules


def check(name: str, data: bytes) -> dict[str, Any]:
    """{kind, data (the copy to keep), violations} of one file; refused files raise."""
    if len(data) > MAX_BYTES:
        raise Refused("file too large", code="BAD_FILE")
    kind = _kind(name, data)
    text, rules = data.decode("utf-8"), set()
    if kind == "json":
        try:
            doc = json.loads(text)
            found = gate.strings(doc)
        except (ValueError, RecursionError):  # RecursionError: nested too deep
            raise Refused("invalid JSON", code="BAD_FILE") from None
        schema = doc.get("$schema") if isinstance(doc, dict) else None
        if isinstance(schema, str) and schema.startswith(VEGA_LITE):
            _chart(found)
        rules = set().union(*(_hits(s) for _, _, s, _, _ in found if s != schema))
    elif kind in ("csv", "tsv"):
        delimiter = "," if kind == "csv" else "\t"
        rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
        rules = set().union(*(_hits(c, _number(c)) for row in rows for c in row))
        if any(_formula(c) for row in rows for c in row):  # only then rewritten: line endings and quoting stay as sent
            text = _table(text, delimiter)
    else:
        rules = _hits(text)
    return {"kind": kind, "data": text.encode("utf-8"), "violations": [violation("file", r) for r in sorted(rules)]}

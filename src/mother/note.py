"""The optional parts of a research note (WIRE §3): item context, the forms its `report` masks, and its `files`."""

from __future__ import annotations

import re
from typing import Any

from mother.errors import violation
from mother.vocab import NoteTopic

MAX_TEXT = 300
MAX_REPORT = 40_000
DOI = r"\b10\.\d{4,9}/[A-Za-z0-9._;()/:-]+"
# the forms a `report` masks (WIRE §3), per rule; every other rule sees the report unmasked
CITATION = re.compile(r"(?i:\b(?:pmids?|pubmed)[:\s#]*\d{1,8}(?:(?:\s*,\s*(?:and\s+)?|\s+and\s+)\d{1,8})*\b"
                      r"|\bNCT\d{8}\b)|" + DOI + r"|\b\d+\.\d+(?:\s*[-\u2013]\s*|\s+to\s+)\d+\.\d+\b"
                      r"|\b\d{1,2}(?:\.\d+)?%\s*CI\b|\bchr(?:2[0-2]|1\d|[1-9]|[XYM]):\d+(?:-\d+)?|\bENS[A-Z]*[GTPE]\d{11}\b"
                      r"|\b[OPQ][0-9][A-Z0-9]{3}[0-9]\b|\brs\d{3,10}\b|\bc[gh]\d{8}\b")
MASKS = {"exact_date": CITATION, "id_number": CITATION, "url": re.compile(r"(?i:\b(?:https?://)?doi\.org/)" + DOI)}
MAX_LIST = 10
FINDINGS = ("support", "against", "risks")
FIELDS = frozenset({"question", "topic", *FINDINGS, "limits", "model_output"})
NAMES = frozenset({"source", "finding", "model", "name", "sha256", "drive_id"})
SOURCE_PATHS = frozenset(f"items[].{k}[].source.id" for k in FINDINGS)
MAX_FILES = 20
FILE_NAME = re.compile(r"[a-z0-9][a-z0-9_-]{0,79}\.(?:csv|tsv|md|json)")
HEX64 = re.compile(r"[0-9a-f]{64}")
DRIVE_FILE = re.compile(r"[A-Za-z0-9_-]{20,80}")
FILE_PATHS = frozenset({"files[].sha256", "files[].drive_id"})


def files(doc: dict[str, Any]) -> list[dict[str, str]]:
    """`bad_files` for a `files` list that is not 1-20 entries of {name, sha256[, drive_id]} with unique names."""
    given = doc.get("files")
    if given is None:
        return []
    if not isinstance(given, list) or not 0 < len(given) <= MAX_FILES:
        return [violation("files", "bad_files")]
    out, seen = [], set()
    for n, e in enumerate(given):
        ok = isinstance(e, dict) and {"name", "sha256"} <= set(e) <= {"name", "sha256", "drive_id"} \
            and isinstance(e["name"], str) and bool(FILE_NAME.fullmatch(e["name"])) and e["name"] not in seen \
            and isinstance(e["sha256"], str) and bool(HEX64.fullmatch(e["sha256"])) \
            and (isinstance(e.get("drive_id", ""), str) and ("drive_id" not in e or bool(DRIVE_FILE.fullmatch(e["drive_id"]))))
        out += [] if ok else [violation(f"files[{n}]", "bad_files")]
        seen.add(e.get("name") if isinstance(e, dict) else None)
    return out


def by_email(doc: dict[str, Any]) -> list[dict[str, str]]:
    """The files that travel in the email's zip: every entry without a drive_id."""
    return [e for e in doc.get("files") or [] if "drive_id" not in e]


def mask(rule: str, report: str) -> str:
    """The report as `rule` sees it: its masked forms blanked, keeping the line breaks."""
    pattern = MASKS.get(rule)
    return pattern.sub(lambda m: " " + "\n" * m[0].count("\n"), report) if pattern else report


def lines(pattern: re.Pattern[str], report: str) -> list[int]:
    """The 1-based lines where a match of `pattern` starts, each once."""
    return list(dict.fromkeys(report.count("\n", 0, m.start()) + 1 for m in pattern.finditer(report)))


def _text(v: Any) -> bool:
    return isinstance(v, str) and 0 < len(v.strip()) <= MAX_TEXT


def _entries(item: dict[str, Any], key: str, n: int, ok) -> list[dict[str, str]]:
    entries = item[key]
    if not isinstance(entries, list) or not 0 < len(entries) <= MAX_LIST:
        return [violation(f"items[{n}].{key}", "bad_context")]
    return [violation(f"items[{n}].{key}[{m}]", "bad_context") for m, e in enumerate(entries) if not ok(e)]


def check(items: list[dict[str, Any]], model_id, report: Any = None) -> list[dict[str, str]]:
    out = [] if report is None or isinstance(report, str) and report.strip() else [violation("report", "bad_context")]
    for n, item in enumerate(items):
        cited = {(s.get("kind"), s.get("id")) for s in item.get("sources", []) if isinstance(s, dict)}
        if "question" in item and not _text(item["question"]):
            out.append(violation(f"items[{n}].question", "bad_context"))
        if "topic" in item and item["topic"] not in tuple(NoteTopic):
            out.append(violation(f"items[{n}].topic", "bad_context"))
        for key in FINDINGS:
            if key in item:
                out += _entries(item, key, n, lambda e: isinstance(e, dict) and set(e) == {"source", "finding"}
                                and _text(e["finding"]) and isinstance(e["source"], dict)
                                and (e["source"].get("kind"), e["source"].get("id")) in cited)
        if "limits" in item:
            out += _entries(item, "limits", n, _text)
        if "model_output" in item:
            out += _entries(item, "model_output", n, lambda e: isinstance(e, dict) and set(e) == {"model", "finding"}
                            and _text(e["finding"]) and isinstance(e["model"], str) and model_id(e["model"]))
    return out

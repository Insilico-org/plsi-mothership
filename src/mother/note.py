"""The optional parts of a research note (WIRE §3): item context, the forms its `report` masks, its `files` and its
`project`."""

from __future__ import annotations

import re
from typing import Any

from mother.contribution import HEX64, fits
from mother.errors import violation
from mother.vocab import NoteHallmark

MAX_TEXT = 300
MAX_REPORT = 40_000
MAX_LIST = 10
DOI = r"\b10\.\d{4,9}/[A-Za-z0-9._;()/:-]+"
# the forms a `report` masks (WIRE §3), per rule; every other rule sees the report unmasked
CITATION = re.compile(r"(?i:\b(?:pmids?|pubmed)[:\s#]*\d{1,8}(?:(?:\s*,\s*(?:and\s+)?|\s+and\s+)\d{1,8})*\b"
                      r"|\bNCT\d{8}\b)|" + DOI + r"|\b\d+\.\d+(?:\s*[-\u2013]\s*|\s+to\s+)\d+\.\d+\b"
                      r"|\b\d{1,2}(?:\.\d+)?%\s*CI\b|\bchr(?:2[0-2]|1\d|[1-9]|[XYM]):\d+(?:-\d+)?|\bENS[A-Z]*[GTPE]\d{11}\b"
                      r"|\b[OPQ][0-9][A-Z0-9]{3}[0-9]\b|\brs\d{3,10}\b|\bc[gh]\d{8}\b")
# a statistic, blanked for `id_number` only: a decimal mean with a decimal SD or SEM, 7.4 (0.3) or -0.3 (0.4), alone
# (nothing digit-like before or after), and a p-value, p < 0.05
STATISTIC = re.compile(r"(?<![\d.\-\u2212])[-\u2212]?\b\d{1,3}\.\d{1,2}\s?\(\s?\d{1,3}\.\d{1,2}\s?\)(?!\s?[\d(])"
                       r"|\bp\s?[<>=\u2264\u2265]\s?0?\.\d{1,4}\b")
MASKS = {"exact_date": CITATION, "id_number": re.compile(f"{CITATION.pattern}|{STATISTIC.pattern}"),
         "url": re.compile(r"(?i:\b(?:https?://)?doi\.org/)" + DOI)}
FILE_MASKS = (MASKS["url"], CITATION)  # blanked in this order in a file's text: a doi.org link is a citation
FINDINGS = ("support", "against", "risks")
FIELDS = frozenset({"question", "tags", "hallmarks", *FINDINGS, "limits", "model_output"})
CITED_FIELDS = frozenset({"source", "finding"})
MODEL_FIELDS = frozenset({"model", "finding"})
TAG_FIELDS = frozenset({"mesh", "label", "tree"})
FILE_FIELDS = frozenset({"name", "sha256", "drive_id"})
PROJECT = frozenset({"id", "title"})
NAMES = CITED_FIELDS | MODEL_FIELDS | TAG_FIELDS | FILE_FIELDS | PROJECT
# an item's tags: 1-MAX_TAGS MeSH descriptors that PLSI resolved against NLM, checked here by form only
MAX_TAGS = 5
MESH = re.compile(r"D\d{6}(?:\d{3})?")
TREE = re.compile(r"[A-Z]\d{2}(?:\.\d{3})*")
MAX_FILES = 20
FILE_TYPES = ("csv", "tsv", "md", "json")
FILE_NAME = re.compile(rf"[a-z0-9][a-z0-9_-]{{0,79}}\.(?:{'|'.join(FILE_TYPES)})")
DRIVE_FILE = re.compile(r"[A-Za-z0-9_-]{20,80}")
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
# item sources: public identifiers, exempt from the id-number rule only in this strict form (WIRE §3)
SOURCE_IDS = {"pmid": re.compile(r"[1-9]\d{0,7}"), "doi": re.compile(r"10\.\d{4,9}/\S{1,200}"),
              "nct": re.compile(r"NCT\d{8}"), "model": re.compile(r"(?=.*[A-Za-z])[A-Za-z0-9._:/-]{1,80}")}
# the public identifiers and hashes of a note, exempt from exact_date and id_number (preprint DOIs hold "2023.12.02")
ID_PATHS = frozenset({"items[].sources[].id", *(f"items[].{k}[].source.id" for k in FINDINGS), "files[].sha256",
                      "files[].drive_id", "project.id", "items[].tags[].mesh", "items[].tags[].tree[]"})


def short_text(v: Any) -> bool:
    """A string of 1-MAX_TEXT characters, not only white space."""
    return isinstance(v, str) and 0 < len(v.strip()) <= MAX_TEXT


def files(doc: dict[str, Any]) -> list[dict[str, str]]:
    """`bad_files` for a `files` list that is not 1-20 entries of {name, sha256[, drive_id]} with unique names."""
    given = doc.get("files")
    if given is None:
        return []
    if not isinstance(given, list) or not 0 < len(given) <= MAX_FILES:
        return [violation("files", "bad_files")]
    out, seen = [], set()
    for n, e in enumerate(given):
        ok = isinstance(e, dict) and {"name", "sha256"} <= set(e) <= FILE_FIELDS and fits(FILE_NAME, e["name"]) \
            and e["name"] not in seen and fits(HEX64, e["sha256"]) \
            and ("drive_id" not in e or fits(DRIVE_FILE, e["drive_id"]))
        out += [] if ok else [violation(f"files[{n}]", "bad_files")]
        if ok:
            seen.add(e["name"])
    return out


def project(doc: dict[str, Any]) -> list[dict[str, str]]:
    """`bad_project` for a `project` that is not an object with `id` (a lowercase uuid) and `title` (1-300 characters);
    its other keys are the gate's `unknown_field`."""
    if "project" not in doc:
        return []
    given = doc["project"]
    ok = isinstance(given, dict) and fits(UUID, given.get("id")) and short_text(given.get("title"))
    return [] if ok else [violation("project", "bad_project")]


def by_email(doc: dict[str, Any]) -> list[tuple[int, dict[str, str]]]:
    """(index in `files`, entry) of the files that travel in the email's zip: every entry without a drive_id."""
    return [(n, e) for n, e in enumerate(doc.get("files") or []) if "drive_id" not in e]


def mask(rule: str, report: str) -> str:
    """The report as `rule` sees it: its masked forms blanked, keeping the line breaks."""
    pattern = MASKS.get(rule)
    return pattern.sub(lambda m: " " + "\n" * m[0].count("\n"), report) if pattern else report


def lines(pattern: re.Pattern[str], report: str) -> list[int]:
    """The 1-based lines where a match of `pattern` starts, each once."""
    return list(dict.fromkeys(report.count("\n", 0, m.start()) + 1 for m in pattern.finditer(report)))


def _sources(items: list[dict[str, Any]]) -> list[dict[str, str]]:
    out = []
    for n, item in enumerate(items):
        sources = item.get("sources", [])
        for m, s in enumerate(sources if isinstance(sources, list) else [None]):
            pattern = SOURCE_IDS.get(s.get("kind")) if isinstance(s, dict) and isinstance(s.get("kind"), str) else None
            if not (pattern and set(s) == {"kind", "id"} and fits(pattern, s["id"])):
                out.append(violation(f"items[{n}].sources[{m}]", "bad_source"))
    return out


def _listed(v: Any) -> bool:
    return isinstance(v, list) and 0 < len(v) <= MAX_LIST


def _tag_list(v: Any) -> bool:
    """1-MAX_TAGS tags, their MeSH ids distinct."""
    return isinstance(v, list) and 0 < len(v) <= MAX_TAGS \
        and len({str(e.get("mesh")) for e in v if isinstance(e, dict)}) == len(v)


def _hallmarks(v: Any) -> bool:
    return isinstance(v, list) and 0 < len(v) == len(set(map(str, v))) and all(h in tuple(NoteHallmark) for h in v)


def _cited(e: Any, cited: set[tuple[Any, Any]]) -> bool:
    return isinstance(e, dict) and set(e) == CITED_FIELDS and short_text(e["finding"]) \
        and isinstance(e["source"], dict) and (e["source"].get("kind"), e["source"].get("id")) in cited


def _model(e: Any, _: set[tuple[Any, Any]]) -> bool:
    return isinstance(e, dict) and set(e) == MODEL_FIELDS and short_text(e["finding"]) \
        and fits(SOURCE_IDS["model"], e["model"])


def _tag(e: Any, _: set[tuple[Any, Any]]) -> bool:
    return isinstance(e, dict) and set(e) == TAG_FIELDS and fits(MESH, e["mesh"]) and short_text(e["label"]) \
        and isinstance(e["tree"], list) and 0 < len(e["tree"]) <= MAX_LIST and all(fits(TREE, t) for t in e["tree"])


def _limit(e: Any, _: set[tuple[Any, Any]]) -> bool:
    return short_text(e)


# an item's context field -> (its value is fine, each entry is fine given the item's cited (kind, id) sources)
CONTEXT = {"question": (short_text, None), "tags": (_tag_list, _tag), "hallmarks": (_hallmarks, None),
           **dict.fromkeys(FINDINGS, (_listed, _cited)), "limits": (_listed, _limit), "model_output": (_listed, _model)}


def check(items: list[dict[str, Any]], report: Any = None) -> list[dict[str, str]]:
    """The items' sources (`bad_source`), then their context and the report (`bad_context`)."""
    out = _sources(items)
    out += [] if report is None or isinstance(report, str) and report.strip() else [violation("report", "bad_context")]
    for n, item in enumerate(items):
        sources = item.get("sources")
        cited = {(s["kind"], s["id"]) for s in sources if isinstance(s, dict) and isinstance(s.get("kind"), str)
                 and isinstance(s.get("id"), str)} if isinstance(sources, list) else set()  # _sources refuses the rest
        for key, (whole, entry) in CONTEXT.items():
            at = f"items[{n}].{key}"
            if key in item and not whole(item[key]):
                out.append(violation(at, "bad_context"))
            elif key in item and entry:
                out += [violation(f"{at}[{m}]", "bad_context") for m, e in enumerate(item[key]) if not entry(e, cited)]
    return out

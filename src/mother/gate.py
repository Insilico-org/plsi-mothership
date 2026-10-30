"""The gate of WIRE §3, Mother side: every message is re-checked on arrival, whatever the sender's PLSI did. Strict JSON
(no duplicate keys, no NaN, at most MAX_DEPTH deep), a known kind of the current schema version from the right
sender, the fields of its kind only, no identifying field anywhere, no string that holds an exact date, contact, URL,
id number or internal id, and the kind's own rules. A violation is {path, rule}: paths name known fields and indexes,
never the sender's text. Empty violations = store, canonically re-serialised."""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from typing import Any

from mother import contribution, stats
from mother.errors import GateFailed, violation
from mother.vocab import ContributionKind, Direction, Kind, Op, Request, Sender

SCHEMA_VERSION = 4
MAX_DEPTH = 32
MAX_STRING = 4000
MAX_KEY = 64
FIELDS = {  # kind -> (required, optional) body fields beyond kind and schema_version
    Kind.CONTRIBUTION: ({"contributor_id", "consent_version", "packed", "kinds", "subject"}, set(ContributionKind)),
    Kind.WITHDRAWAL: ({"contributor_id", "request"}, {"proof"}),
    Kind.RESEARCH_NOTE: ({"items", "method"}, set()),
    Kind.REGISTRY_UPDATE: ({"items"}, set()),
    Kind.PREREGISTRATION: ({"professional_id", "preregistration_id", "hypothesis", "release", "analysis", "outcome",
                            "alpha", "direction", "parents"}, set()),
    Kind.FINDING: ({"professional_id", "finding_id", "preregistration_id", "release", "analysis", "result",
                    "summary"}, set()),
}
ITEM_FIELDS = {
    Kind.RESEARCH_NOTE: {"claim", "grade", "evidence_class", "sources", "falsified_by", "next_step"},
    Kind.REGISTRY_UPDATE: {"kind", "name", "specialty", "country", "public_url", "methods", "credentials", "source_urls"},
}
SENDERS = {Kind.CONTRIBUTION: {Sender.CONTRIBUTOR}, Kind.WITHDRAWAL: {Sender.CONTRIBUTOR},
           Kind.REGISTRY_UPDATE: {Sender.CONTRIBUTOR}, Kind.RESEARCH_NOTE: {Sender.EMAIL},
           Kind.PREREGISTRATION: {Sender.PROFESSIONAL}, Kind.FINDING: {Sender.PROFESSIONAL}}
FORBIDDEN_KEYS = frozenset({"subject_id", "instance", "birth_year", "event_id", "event_ids", "path", "original_path",
                            "install_id"})
MONTH_NAME = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"
ORDINAL = r"(?:st|nd|rd|th)?"
TLDS = "com|org|net|io|ai|co|me|info|biz|ru|cn|uk|de|xyz|app|dev|link|ly|gl"
PATTERNS = {
    "exact_date": re.compile(rf"(?i)\b\d{{4}}-\d{{2}}-\d{{2}}|\b\d{{4}}[./-]\d{{1,2}}[./-]\d{{1,2}}\b"
                             rf"|(?<![\d.])\d{{1,2}}([./-])\d{{1,2}}\1(?:\d{{4}}|\d{{2}})(?![\d.])"
                             rf"|\d{{1,2}}{ORDINAL}\s+{MONTH_NAME}\s+\d{{2,4}}"
                             rf"|\b{MONTH_NAME}\s+\d{{1,2}}{ORDINAL},?\s+\d{{2,4}}|\d{{1,2}}[- ]{MONTH_NAME}[- ]\d{{2,4}}"),
    "email": re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),
    "url": re.compile(rf"(?i)\bhttps?://|\bwww\.|(?<![\w-])[\w-]+\.(?:{TLDS})\b"),
    "id_number": re.compile(r"\d[\d\s().-]{6,}\d"),
    "internal_id": re.compile(r"\b(?:s_[0-9a-f]{12}|evt_[0-9a-f]{8,32})\b"),
}
YEARS = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")
# exemptions by exact path (`[]` any index, `*` any coordinate key), per rule and kind; keys are never exempt. A
# `code.code` is exempt only in a code object of a numbered system (contribution.NUMBERED), never a free or cpg code.
# Source ids (strict public forms) are exempt from exact_date too: preprint DOIs hold "2023.12.02".
EXEMPT = {
    "exact_date": {Kind.RESEARCH_NOTE: frozenset({"items[].sources[].id"})},
    "id_number": {
        Kind.CONTRIBUTION: frozenset({"contributor_id", "states[].map_hash", "states[].coordinates.*.items[].code.code",
                                      "life_events[].code.code"}),
        Kind.WITHDRAWAL: frozenset({"contributor_id", "proof"}),
        Kind.RESEARCH_NOTE: frozenset({"items[].sources[].id"}),
        Kind.PREREGISTRATION: frozenset({"professional_id", "preregistration_id", "release", "parents[]"}),
        Kind.FINDING: frozenset({"professional_id", "finding_id", "preregistration_id", "release"})},
    "url": {Kind.REGISTRY_UPDATE: frozenset({"items[].public_url", "items[].source_urls[]"})},
}
KEYED = frozenset({"states[].coordinates", "scores.by_coordinate"})  # objects keyed by coordinate ids
HEX64 = contribution.HEX64
IDS = {"preregistration_id": re.compile(r"pr_[0-9a-f]{16}"), "finding_id": re.compile(r"f_[0-9a-f]{16}"),
       "professional_id": re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")}
RELEASE = re.compile(r"[0-9A-Za-z._-]{1,64}")
# research-note sources: public identifiers, exempt from the id-number rule only in this strict form (WIRE §3)
SOURCE_IDS = {"pmid": re.compile(r"[1-9]\d{0,7}"), "doi": re.compile(r"10\.\d{4,9}/\S{1,200}"),
              "nct": re.compile(r"NCT\d{8}"), "model": re.compile(r"(?=.*[A-Za-z])[A-Za-z0-9._:/-]{1,80}")}
RESULT_KEYS = ("effect", "interval", "n", "statistic", "df", "p")
# every field name of the wire: the only key text a path ever shows
NAMES = frozenset({"kind", "schema_version", "id", *FORBIDDEN_KEYS, *contribution.NAMES, *RESULT_KEYS,
                   *(k for required, optional in FIELDS.values() for k in required | optional),
                   *(k for fields in ITEM_FIELDS.values() for k in fields),
                   *(k for op in Op for k in stats.SPEC_KEYS[op]), *stats.GROUP_KEYS})


class _Duplicate(Exception):
    pass


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    if len({k for k, _ in pairs}) != len(pairs):
        raise _Duplicate
    return dict(pairs)


def _constant(name: str) -> Any:
    raise ValueError("NaN and Infinity are not JSON")


def canonical(doc: Any) -> str:
    """The stored form of a gated document (WIRE §6)."""
    return json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def parse(text: str) -> Any:
    """Strict JSON, else a gate failure at `$`: `duplicate_key`, `too_deep` or `not_json`."""
    try:
        doc = json.loads(text, object_pairs_hook=_pairs, parse_constant=_constant)
        canonical(doc).encode("utf-8")  # a lone surrogate cannot be stored
    except _Duplicate:
        raise GateFailed([violation("$", "duplicate_key")]) from None
    except RecursionError:
        raise GateFailed([violation("$", "too_deep")]) from None
    except (ValueError, UnicodeError):
        raise GateFailed([violation("$", "not_json")]) from None
    return doc


def _depth(obj: Any) -> int:
    """Nesting depth of objects and lists, without recursion; stops counting past MAX_DEPTH."""
    deepest, stack = 0, [(obj, 1)]
    while stack and deepest <= MAX_DEPTH:
        node, depth = stack.pop()
        if isinstance(node, (dict, list)):
            deepest = max(deepest, depth)
            stack += [(v, depth + 1) for v in (node.values() if isinstance(node, dict) else node)]
    return deepest


def _clean(text: str) -> str:
    """NFKC, without format characters (zero-width and the like): what every pattern is matched against."""
    return "".join(c for c in unicodedata.normalize("NFKC", text) if unicodedata.category(c) != "Cf")


def _strings(obj: Any, shown: str = "", plain: str = "", parent: Any = None) -> list[tuple[str, str, str, bool, Any]]:
    """(path, exemption path, text, is_key, enclosing object) of every string value and every dict key. A key is shown
    by name only when it is a wire field name; a coordinate key is `#key[n]`, any other `#unknown[n]` (n = position)."""
    if isinstance(obj, dict):
        out = []
        for n, (k, v) in enumerate(obj.items()):
            sub, tmpl = (f"#key[{n}]", "*") if plain in KEYED else ((k, k) if k in NAMES else (f"#unknown[{n}]",) * 2)
            sub, tmpl = (f"{shown}.{sub}", f"{plain}.{tmpl}") if shown else (sub, tmpl)
            out += [(sub, tmpl, k, True, obj), *_strings(v, sub, tmpl, obj)]
        return out
    if isinstance(obj, list):
        return [s for n, v in enumerate(obj) for s in _strings(v, f"{shown}[{n}]", f"{plain}[]", obj)]
    return [(shown, plain, obj, False, parent)] if isinstance(obj, str) else []


def _exempt(rule: str, kind: Kind, plain: str, parent: Any) -> bool:
    if plain not in EXEMPT.get(rule, {}).get(kind, frozenset()):
        return False
    system = parent.get("system") if isinstance(parent, dict) else None
    return not plain.endswith("code.code") or (isinstance(system, str) and system in contribution.NUMBERED)


def _text(doc: dict[str, Any], kind: Kind) -> list[dict[str, str]]:
    out = []
    for path, plain, text, is_key, parent in _strings(doc):
        if is_key and text in FORBIDDEN_KEYS:
            out.append(violation(path, "forbidden_field"))
        if len(text) > (MAX_KEY if is_key else MAX_STRING):
            out.append(violation(path, "too_long"))
            continue
        clean = _clean(text)
        for rule, pattern in PATTERNS.items():
            if not is_key and _exempt(rule, kind, plain, parent):
                continue
            if pattern.search(YEARS.sub(" ", clean) if rule == "id_number" else clean):  # "2010-2015" is no id
                out.append(violation(path, rule))
    return out


def _name(k: str, n: int, at: str = "") -> str:
    name = k if k in NAMES else f"#unknown[{n}]"
    return f"{at}.{name}" if at else name


def _fields(doc: dict[str, Any], kind: Kind) -> list[dict[str, str]]:
    required, optional = FIELDS[kind]
    out = [violation(k, "missing_field") for k in sorted(required - set(doc))]
    known = required | set(optional) | {"kind", "schema_version"}
    out += [violation(_name(k, n), "unknown_field") for n, k in enumerate(doc) if k not in known]
    if kind in ITEM_FIELDS:
        items = doc.get("items")
        if not isinstance(items, list) or not all(isinstance(i, dict) for i in items):
            return out + [violation("items", "bad_items")]
        out += [violation(_name(k, m, f"items[{n}]"), "unknown_field")
                for n, item in enumerate(items) for m, k in enumerate(item) if k not in ITEM_FIELDS[kind]]
        if kind == Kind.RESEARCH_NOTE:
            out += _sources(items)
    return out


def _sources(items: list[dict[str, Any]]) -> list[dict[str, str]]:
    out = []
    for n, item in enumerate(items):
        sources = item.get("sources", [])
        for m, s in enumerate(sources if isinstance(sources, list) else [None]):
            pattern = SOURCE_IDS.get(s.get("kind")) if isinstance(s, dict) and isinstance(s.get("kind"), str) else None
            if not (pattern and set(s) == {"kind", "id"} and isinstance(s["id"], str) and pattern.fullmatch(s["id"])):
                out.append(violation(f"items[{n}].sources[{m}]", "bad_source"))
    return out


def _number(v: Any) -> bool:
    return not isinstance(v, bool) and (isinstance(v, int) or isinstance(v, float) and math.isfinite(v))


def _result(result: Any) -> list[dict[str, str]]:
    if not isinstance(result, dict):
        return [violation("result", "bad_result")]
    out = [violation(f"result.{k}", "missing_field") for k in RESULT_KEYS if k not in result]
    out += [violation(_name(k, n, "result"), "unknown_field") for n, k in enumerate(result) if k not in RESULT_KEYS]
    out += [violation(f"result.{k}", "bad_result")
            for k in ("effect", "statistic", "df", "p") if k in result and not _number(result[k])]
    if _number(result.get("p")) and not 0 <= result["p"] <= 1:
        out.append(violation("result.p", "bad_result"))
    if "n" in result and (isinstance(result["n"], bool) or not isinstance(result["n"], int) or result["n"] < 0):
        out.append(violation("result.n", "bad_result"))
    interval = result.get("interval")
    if "interval" in result and not (isinstance(interval, list) and len(interval) == 2 and all(map(_number, interval))):
        out.append(violation("result.interval", "bad_result"))
    return out


def _contributor(doc: dict[str, Any], kind: Kind) -> list[dict[str, str]]:
    cid = doc.get("contributor_id")
    out = [] if isinstance(cid, str) and HEX64.fullmatch(cid) else [violation("contributor_id", "bad_contributor_id")]
    if kind == Kind.CONTRIBUTION:
        return out + contribution.violations(doc)
    request, proof = doc.get("request"), doc.get("proof")
    if request not in tuple(Request):
        out.append(violation("request", "bad_request"))
    elif request == Request.STOP and "proof" in doc:
        out.append(violation("proof", "unexpected_proof"))
    elif request == Request.DELETE_ALL and not proves(proof, cid):
        out.append(violation("proof", "bad_proof"))
    return out


def proves(proof: Any, contributor_id: Any) -> bool:
    """`sha256_hex(proof) == contributor_id`, hashing the hex text of `s` (or, equally strong, its 32 bytes)."""
    if not (isinstance(proof, str) and HEX64.fullmatch(proof) and isinstance(contributor_id, str)):
        return False
    return contributor_id in (hashlib.sha256(proof.encode("ascii")).hexdigest(),
                              hashlib.sha256(bytes.fromhex(proof)).hexdigest())


def _professional(doc: dict[str, Any], kind: Kind, professional_id: str | None) -> list[dict[str, str]]:
    out = [] if professional_id and doc.get("professional_id") == professional_id else [
        violation("professional_id", "professional_mismatch")]
    out += [violation(key, "bad_id") for key, pattern in IDS.items()
            if key in doc and not (isinstance(doc[key], str) and pattern.fullmatch(doc[key]))]
    if "release" in doc and not (isinstance(doc["release"], str) and RELEASE.fullmatch(doc["release"])):
        out.append(violation("release", "bad_release"))
    if stats.problems(doc.get("analysis")):
        out.append(violation("analysis", "bad_spec"))
    if kind == Kind.PREREGISTRATION:
        alpha = doc.get("alpha")
        if not _number(alpha) or not 0 < alpha < 1:
            out.append(violation("alpha", "bad_alpha"))
        if doc.get("direction") not in tuple(Direction):
            out.append(violation("direction", "bad_direction"))
        parents = doc.get("parents")
        if not isinstance(parents, list) or not all(isinstance(p, str) and IDS["finding_id"].fullmatch(p) for p in parents):
            out.append(violation("parents", "bad_parents"))
    elif "result" in doc:  # a missing result is `missing_field` only
        out += _result(doc["result"])
    return out


def violations(doc: Any, sender: str = Sender.CONTRIBUTOR, professional_id: str | None = None) -> list[dict[str, str]]:
    """Everything that keeps `doc` out of the store, given its sender (from the relay's parked record, or `email`)."""
    if _depth(doc) > MAX_DEPTH:
        return [violation("$", "too_deep")]
    if not isinstance(doc, dict) or not isinstance(doc.get("kind"), str) or doc["kind"] not in tuple(Kind):
        return [violation("kind", "unknown_kind")]
    kind = Kind(doc["kind"])
    if type(doc.get("schema_version")) is not int or doc["schema_version"] != SCHEMA_VERSION:  # not 4.0, not True
        return [violation("schema_version", "schema_version")]
    out = [] if sender in SENDERS[kind] else [violation("kind", "sender")]
    out += _fields(doc, kind) + _text(doc, kind)
    if kind in (Kind.CONTRIBUTION, Kind.WITHDRAWAL):
        out += _contributor(doc, kind)
    elif kind in (Kind.PREREGISTRATION, Kind.FINDING):
        out += _professional(doc, kind, professional_id)
    return out

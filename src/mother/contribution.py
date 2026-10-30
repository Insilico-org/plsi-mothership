"""The content of a `plsi_contribution` (WIRE §3): coded fields only, no free text. Every section is an object or list
of the fields below and nothing else; identifiers match ID, codes match their system (LOINC and SNOMED CT with their
check digit), units match UNIT (a dose is exactly {value > 0, unit}), age bands are AGE_BANDS, months are YYYY-MM, years are in YEAR_RANGE, numbers are
finite, not bool and at most MAX_NUMBER in size. Paths name known fields and indexes only: a coordinate key is
`#key[<n>]`, an unknown key `#unknown[<n>]` (n = its position), never the key text."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from mother.errors import violation
from mother.vocab import CodeSystem, ContributionKind, Sex

ID = re.compile(r"[a-z0-9][a-z0-9_.-]{0,63}")
MONTH = re.compile(r"\d{4}-(0[1-9]|1[0-2])")
SEMVER = re.compile(r"\d+\.\d+\.\d+")
MAP_VERSION = re.compile(r"[0-9A-Za-z.+-]{1,32}")
AGE_BANDS = frozenset({*(f"{lo}-{lo + 4}" for lo in range(0, 116, 5)), "90+"})
UNIT = re.compile(r"[A-Za-z0-9%/.*^()\[\]{}'_-]{1,32}")  # UCUM-like, no whitespace
YEAR_RANGE = range(1900, 2101)
HEX64 = re.compile(r"[0-9a-f]{64}")  # contributor_id and map_hash (PLSI strips its `sha256:` prefix)
CODES = {CodeSystem.LOINC: re.compile(r"\d{1,7}-\d"), CodeSystem.RXNORM: re.compile(r"\d{1,10}"),
         CodeSystem.SNOMED: re.compile(r"\d{6,18}"), CodeSystem.ICD10CM: re.compile(r"[A-TV-Z]\d[0-9A-Z](\.[0-9A-Z]{1,4})?"),
         CodeSystem.CPG: re.compile(r"[a-z][a-z0-9_.:-]{0,63}"), CodeSystem.FREE: re.compile(r"[a-z][a-z0-9_.:-]{0,63}")}
# numbered systems: their codes may be long digit runs (exempt from `id_number`); free and cpg codes are not
NUMBERED = frozenset({CodeSystem.LOINC, CodeSystem.RXNORM, CodeSystem.SNOMED, CodeSystem.ICD10CM})
_D = ["0123456789", "1234067895", "2340178956", "3401289567", "4012395678", "5987604321", "6598710432", "7659821043",
      "8765932104", "9876543210"]  # Verhoeff: the dihedral group D5 and its permutation table
_P = ["0123456789", "1576283094", "5803796142", "8916043527", "9453126870", "4286573901", "2793806415", "7046913285"]
COMPARATORS = frozenset({"<", "<=", ">", ">="})
SCORE_KEYS = frozenset({"n_scored", "n_missed", "within_interval_rate", "nominal_level", "mean_abs_error",
                        "beat_persistence_rate", "direction_accuracy"})
MAX_NUMBER = 1e9
Check = Callable[[Any, str], list[dict[str, str]]]


def number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and abs(v) <= MAX_NUMBER  # NaN fails; no float() of a huge int


def _num(v: Any, at: str) -> list[dict[str, str]]:
    return [] if number(v) else [violation(at, "bad_number")]


def _positive(v: Any, at: str) -> list[dict[str, str]]:
    return [] if number(v) and v > 0 else [violation(at, "bad_number")]


def _year(v: Any, at: str) -> list[dict[str, str]]:
    return [] if number(v) and isinstance(v, int) and v in YEAR_RANGE else [violation(at, "bad_number")]


def verhoeff(digits: str) -> bool:
    """SNOMED CT's check digit (the last digit)."""
    c = 0
    for i, d in enumerate(reversed(digits)):
        c = int(_D[c][int(_P[i % 8][int(d)])])
    return c == 0


def luhn(digits: str) -> bool:
    """LOINC's mod-10 check digit (the last digit): the Luhn sum of the code's digits."""
    total = 0
    for i, d in enumerate(reversed(digits)):
        n = int(d) * (2 if i % 2 else 1)
        total += n - 9 if n > 9 else n
    return total % 10 == 0


CHECK_DIGIT = {CodeSystem.LOINC: lambda code: luhn(code.replace("-", "")), CodeSystem.SNOMED: verhoeff}


def code_ok(system: Any, code: Any) -> bool:
    """`code` has the form of its `system` and, for LOINC and SNOMED CT, a valid check digit."""
    if not (isinstance(system, str) and system in CODES and isinstance(code, str) and CODES[system].fullmatch(code)):
        return False
    return CHECK_DIGIT[system](code) if system in CHECK_DIGIT else True


def _match(pattern: re.Pattern[str], rule: str) -> Check:
    return lambda v, at: [] if isinstance(v, str) and pattern.fullmatch(v) else [violation(at, rule)]


def _one_of(allowed: frozenset[str]) -> Check:
    return lambda v, at: [] if isinstance(v, str) and v in allowed else [violation(at, "bad_format")]


def _code(v: Any, at: str) -> list[dict[str, str]]:
    """Exactly {system, code}, a valid code of its system (`bad_code`)."""
    ok = isinstance(v, dict) and set(v) == {"system", "code"} and code_ok(v["system"], v["code"])
    return [] if ok else [violation(at, "bad_code")]


def _list(check: Check) -> Check:
    def run(v: Any, at: str) -> list[dict[str, str]]:
        if not isinstance(v, list):
            return [violation(at, "bad_section")]
        return [x for n, item in enumerate(v) for x in check(item, f"{at}[{n}]")]
    return run


def _object(required: set[str], fields: dict[str, Check]) -> Check:
    """An object of these fields only (`unknown_field`), the required ones present (`missing_field`)."""
    def run(v: Any, at: str) -> list[dict[str, str]]:
        if not isinstance(v, dict):
            return [violation(at, "bad_section")]
        out = [violation(f"{at}.{k}", "missing_field") for k in sorted(required - set(v))]
        for n, (k, item) in enumerate(v.items()):
            out += fields[k](item, f"{at}.{k}") if k in fields else [violation(f"{at}.#unknown[{n}]", "unknown_field")]
        return out
    return run


def _keyed(check: Check) -> Check:
    """An object whose keys are identifiers (`bad_id`), shown as `#key[<n>]`, each value checked."""
    def run(v: Any, at: str) -> list[dict[str, str]]:
        if not isinstance(v, dict):
            return [violation(at, "bad_section")]
        out = []
        for n, (k, item) in enumerate(v.items()):
            sub = f"{at}.#key[{n}]"
            out += ([] if isinstance(k, str) and ID.fullmatch(k) else [violation(sub, "bad_id")]) + check(item, sub)
        return out
    return run


def _rate(v: Any, at: str) -> list[dict[str, str]]:
    return [] if v is None else _num(v, at)


_summary = _object(set(), dict.fromkeys(SCORE_KEYS, _rate))
_id = _match(ID, "bad_id")
_month = _match(MONTH, "bad_format")
_unit = _match(UNIT, "bad_format")
DOSE = _object({"value", "unit"}, {"value": _positive, "unit": _unit})  # stored only: releases and catalog ignore it
COORDINATE = _object(set(), {
    "status": _id, "value": _num, "unit": _unit, "comparator": _one_of(COMPARATORS), "observed": _month,
    "series": _list(_object({"t", "value"}, {"t": _month, "value": _num})),
    "items": _list(_object({"code"}, {"code": _code, "clinical_status": _id, "dose": DOSE}))})
STATE = _object({"map_id", "coordinates"}, {
    "map_id": _id, "map_version": _match(MAP_VERSION, "bad_format"), "map_hash": _match(HEX64, "bad_format"),
    "as_of": _month, "gaps": _list(_id), "coordinates": _keyed(COORDINATE)})
EVENT = _object({"kind", "year", "basis", "code"}, {
    "kind": _id, "year": _year, "until": _year, "basis": _id, "code": _code, "value": _num, "unit": _unit,
    "clinical_status": _id, "dose": DOSE})
SCORES = _object(set(), {"overall": _summary, "by_coordinate": _keyed(_summary)})
SECTIONS = {ContributionKind.STATES: _list(STATE), ContributionKind.LIFE_EVENTS: _list(EVENT),
            ContributionKind.SCORES: SCORES}
SUBJECT = _object(set(), {"sex": _one_of(frozenset(Sex)), "age_band": _one_of(AGE_BANDS)})
# every field name above, for gate paths
NAMES = frozenset({"map_id", "map_version", "map_hash", "as_of", "gaps", "coordinates", "status", "value", "unit",
                   "comparator", "observed", "series", "t", "items", "code", "system", "clinical_status", "kind",
                   "year", "until", "basis", "dose", "overall", "by_coordinate", "sex", "age_band", *SCORE_KEYS})


def violations(doc: dict[str, Any]) -> list[dict[str, str]]:
    """Everything wrong with a contribution's own fields and sections (its top-level fields are the gate's)."""
    out = []
    kinds = doc.get("kinds")
    if not isinstance(kinds, list) or not all(isinstance(k, str) and k in tuple(ContributionKind) for k in kinds):
        out.append(violation("kinds", "unknown_kind"))
    else:
        out += [violation(s, "not_in_kinds") for s in ContributionKind if s in doc and s not in kinds]
    checks = {"packed": _month, "consent_version": _match(SEMVER, "bad_format"), "subject": SUBJECT, **SECTIONS}
    return out + [x for field, check in checks.items() if field in doc for x in check(doc[field], field)]

"""The content of a `plsi_contribution` (WIRE §3): coded fields only, each checked by a (value, path) -> violations
function. Paths name known fields and indexes only: a coordinate key is `#key[<n>]`, an unknown key `#unknown[<n>]`."""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from functools import partial
from typing import Any

from mother.errors import violation
from mother.vocab import ClinicalStatus, CodeSystem, ContributionKind, Ethnicity, Sex, ValueStatus

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
         **dict.fromkeys((CodeSystem.CPG, CodeSystem.FREE), re.compile(r"[a-z][a-z0-9_.:-]{0,63}"))}
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


def finite(v: Any) -> bool:
    """An int or a finite float, not a bool."""
    return not isinstance(v, bool) and (isinstance(v, int) or isinstance(v, float) and math.isfinite(v))


def number(v: Any) -> bool:
    return finite(v) and abs(v) <= MAX_NUMBER


def fits(pattern: re.Pattern[str], v: Any) -> bool:
    """`v` is a string that `pattern` matches in full."""
    return isinstance(v, str) and bool(pattern.fullmatch(v))


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
    """LOINC's mod-10 check digit (the last digit): the Luhn sum of the code's digits, its dash ignored."""
    total = 0
    for i, d in enumerate(reversed(digits.replace("-", ""))):
        n = int(d) * (2 if i % 2 else 1)
        total += n - 9 if n > 9 else n
    return total % 10 == 0


CHECK_DIGIT = {CodeSystem.LOINC: luhn, CodeSystem.SNOMED: verhoeff}


def code_ok(system: Any, code: Any) -> bool:
    """`code` has the form of its `system` and, for LOINC and SNOMED CT, a valid check digit."""
    return isinstance(system, str) and system in CODES and fits(CODES[system], code) \
        and (system not in CHECK_DIGIT or CHECK_DIGIT[system](code))


def _match(pattern: re.Pattern[str], rule: str, v: Any, at: str) -> list[dict[str, str]]:
    return [] if fits(pattern, v) else [violation(at, rule)]


def _one_of(allowed: frozenset[str], v: Any, at: str) -> list[dict[str, str]]:
    return [] if isinstance(v, str) and v in allowed else [violation(at, "bad_format")]


def _code(v: Any, at: str) -> list[dict[str, str]]:
    """Exactly {system, code}, a valid code of its system (`bad_code`)."""
    ok = isinstance(v, dict) and set(v) == {"system", "code"} and code_ok(v["system"], v["code"])
    return [] if ok else [violation(at, "bad_code")]


def _list(check: Check, v: Any, at: str) -> list[dict[str, str]]:
    if not isinstance(v, list):
        return [violation(at, "bad_section")]
    return [x for n, item in enumerate(v) for x in check(item, f"{at}[{n}]")]


def _object(required: set[str], fields: dict[str, Check], v: Any, at: str) -> list[dict[str, str]]:
    """An object of these fields only (`unknown_field`), the required ones present (`missing_field`)."""
    if not isinstance(v, dict):
        return [violation(at, "bad_section")]
    out = [violation(f"{at}.{k}", "missing_field") for k in sorted(required - set(v))]
    for n, (k, item) in enumerate(v.items()):
        out += fields[k](item, f"{at}.{k}") if k in fields else [violation(f"{at}.#unknown[{n}]", "unknown_field")]
    return out


def _keyed(check: Check, v: Any, at: str) -> list[dict[str, str]]:
    """An object whose keys are identifiers (`bad_id`), shown as `#key[<n>]`, each value checked."""
    if not isinstance(v, dict):
        return [violation(at, "bad_section")]
    out = []
    for n, (k, item) in enumerate(v.items()):
        sub = f"{at}.#key[{n}]"
        out += ([] if fits(ID, k) else [violation(sub, "bad_id")]) + check(item, sub)
    return out


def _rate(v: Any, at: str) -> list[dict[str, str]]:
    return [] if v is None else _num(v, at)


_summary = partial(_object, set(), dict.fromkeys(SCORE_KEYS, _rate))
_id = partial(_match, ID, "bad_id")
_month = partial(_match, MONTH, "bad_format")
_unit = partial(_match, UNIT, "bad_format")
_clinical = partial(_one_of, frozenset(ClinicalStatus))
# stored only: releases and catalog ignore it
DOSE = partial(_object, {"value", "unit"}, {"value": _positive, "unit": _unit})


def _count(v: Any, at: str) -> list[dict[str, str]]:
    """How many values a mean covers: an integer of at least 1."""
    return [] if isinstance(v, int) and not isinstance(v, bool) and v >= 1 else [violation(at, "bad_number")]


POINT_FIELDS = {"t": _month, "value": _num, "n": _count}
_points = partial(_list, partial(_object, {"t", "value"}, POINT_FIELDS))


def _series(v: Any, at: str) -> list[dict[str, str]]:
    """Points of {t, value}, at most one per month: a month is the finest grain a contribution may carry."""
    months = [p.get("t") for p in v if isinstance(p, dict)] if isinstance(v, list) else []
    return _points(v, at) + [violation(f"{at}[{n}]", "repeat_month") for n, m in enumerate(months) if m in months[:n]]


ITEM_FIELDS = {"code": _code, "clinical_status": _clinical, "dose": DOSE}
COORDINATE_FIELDS = {"status": partial(_one_of, frozenset(ValueStatus)), "value": _num, "unit": _unit,
                     "comparator": partial(_one_of, COMPARATORS), "observed": _month, "series": _series,
                     "items": partial(_list, partial(_object, {"code"}, ITEM_FIELDS))}
STATE_FIELDS = {"map_id": _id, "map_version": partial(_match, MAP_VERSION, "bad_format"),
                "map_hash": partial(_match, HEX64, "bad_format"), "as_of": _month, "gaps": partial(_list, _id),
                "coordinates": partial(_keyed, partial(_object, set(), COORDINATE_FIELDS))}
EVENT_FIELDS = {"kind": _id, "year": _year, "until": _year, "basis": _id, "code": _code, "value": _num, "unit": _unit,
                "clinical_status": _clinical, "dose": DOSE}
SCORE_FIELDS = {"overall": _summary, "by_coordinate": partial(_keyed, _summary)}
SUBJECT_FIELDS = {"sex": partial(_one_of, frozenset(Sex)), "age_band": partial(_one_of, AGE_BANDS),
                  "ethnicity": partial(_one_of, frozenset(Ethnicity) - {Ethnicity.UNSPECIFIED})}
SECTIONS = {ContributionKind.STATES: partial(_list, partial(_object, {"map_id", "coordinates"}, STATE_FIELDS)),
            ContributionKind.LIFE_EVENTS:
                partial(_list, partial(_object, {"kind", "year", "basis", "code"}, EVENT_FIELDS)),
            ContributionKind.SCORES: partial(_object, set(), SCORE_FIELDS)}
CHECKS = {"packed": _month, "consent_version": partial(_match, SEMVER, "bad_format"),
          "subject": partial(_object, set(), SUBJECT_FIELDS), **SECTIONS}
# every field name above, for gate paths
NAMES = frozenset({"system", *SCORE_KEYS}).union(POINT_FIELDS, ITEM_FIELDS, COORDINATE_FIELDS, STATE_FIELDS,
                                                 EVENT_FIELDS, SCORE_FIELDS, SUBJECT_FIELDS)


def violations(doc: dict[str, Any]) -> list[dict[str, str]]:
    """Everything wrong with a contribution's own fields and sections (its top-level fields are the gate's)."""
    out = []
    kinds = doc.get("kinds")
    if not isinstance(kinds, list) or not all(isinstance(k, str) and k in tuple(ContributionKind) for k in kinds):
        out.append(violation("kinds", "unknown_kind"))
    else:
        out += [violation(s, "not_in_kinds") for s in ContributionKind if s in doc and s not in kinds]
    return out + [x for field, check in CHECKS.items() if field in doc for x in check(doc[field], field)]

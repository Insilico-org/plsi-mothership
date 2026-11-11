"""`mother release build`: the versioned aggregate release of WIRE §5. One value per active contributor and cell (the
latest numeric value of the coordinate in their latest contribution; a withdrawn contributor is left out), cells by
map, coordinate, unit, age band, sex and ethnicity (`unspecified` when the subject has none); cells below the minimum
size are left out and only counted. Releases are never rebuilt under the same version."""

from __future__ import annotations

import statistics
from typing import Any

from mother import audit, clock, index
from mother.contribution import number
from mother.errors import NotFound, Refused
from mother.stats import CELL_DIGITS, sig
from mother.store import Store, read_json, write_json
from mother.vocab import Ethnicity

MIN_CELL = 10


def floor(min_cell: int) -> int:
    """Code may only make the cell size stricter: below MIN_CELL is a code change, never a flag."""
    if min_cell < MIN_CELL:
        raise Refused(f"min_cell {min_cell} is below the floor {MIN_CELL}", code="MIN_CELL_TOO_SMALL",
                      hint="a smaller cell needs a Data Access Committee decision and a code change")
    return min_cell


def path(version: str) -> str:
    return f"releases/{version}/release.json"


def _latest(docs: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The contribution with the latest `packed` month; the last to arrive among equals."""
    return max(reversed(docs), key=lambda d: d["packed"], default=None)


def _number(coordinate: dict[str, Any]) -> float | None:
    if number(coordinate.get("value")):
        return float(coordinate["value"])
    series = coordinate.get("series")
    points = [p for p in series if isinstance(p, dict) and number(p.get("value"))
              and isinstance(p.get("t") or "", str)] if isinstance(series, list) else []
    return float(max(points, key=lambda p: p.get("t") or "")["value"]) if points else None


def values(doc: dict[str, Any]) -> dict[tuple[str, str, str], float]:
    """(map_id, coordinate, unit) -> value, from the state with the latest `as_of` (the last among equals); anything
    malformed is skipped, so one package never stops a release."""
    states = doc.get("states")
    states = [s for s in states if isinstance(s, dict) and isinstance(s.get("map_id"), str)
              and isinstance(s.get("coordinates"), dict)] if isinstance(states, list) else []
    out = {}
    for state in sorted(states, key=lambda s: str(s.get("as_of") or "")):
        for name, coordinate in state["coordinates"].items():
            if not isinstance(coordinate, dict) or not isinstance(unit := coordinate.get("unit") or "", str):
                continue
            if (v := _number(coordinate)) is not None:
                out[state["map_id"], name, unit] = v
    return out


def _cell(key: tuple[str, ...], vals: list[float]) -> dict[str, Any]:
    map_id, coordinate, unit, age_band, sex, ethnicity = key
    p25, p50, p75 = statistics.quantiles(vals, n=4, method="inclusive")
    stat = {"mean": statistics.fmean(vals), "sd": statistics.stdev(vals), "p25": p25, "p50": p50, "p75": p75}
    return {"map_id": map_id, "coordinate": coordinate, "unit": unit, "age_band": age_band, "sex": sex,
            "ethnicity": ethnicity, "n": len(vals), **{k: sig(v, CELL_DIGITS) for k, v in stat.items()}}


def compute(store: Store, version: str, min_cell: int = MIN_CELL) -> dict[str, Any]:
    min_cell = floor(min_cell)
    groups: dict[tuple[str, ...], list[float]] = {}
    for cid, entry in index.active(store).items():
        doc = _latest(index.packages(store, cid, entry))
        subject = (doc or {}).get("subject", {})
        if not all(isinstance(subject.get(k), str) and subject[k] for k in ("age_band", "sex")):
            continue
        ethnicity = subject.get("ethnicity") or Ethnicity.UNSPECIFIED.value
        for key, v in values(doc).items():
            groups.setdefault((*key, subject["age_band"], subject["sex"], ethnicity), []).append(v)
    kept = {k: v for k, v in groups.items() if len(v) >= min_cell}
    return {"version": version, "built": clock.month(), "min_cell": min_cell, "suppressed_cells": len(groups) - len(kept),
            "cells": [_cell(k, v) for k, v in sorted(kept.items())]}


def build(store: Store, version: str, min_cell: int = MIN_CELL) -> dict[str, Any]:
    if store.exists(path(version)):
        raise Refused("the release exists; releases are never rebuilt", code="RELEASE_EXISTS",
                      hint="build a new version")
    doc = compute(store, version, min_cell)
    write_json(store, path(version), doc)
    audit.record(store, "release_build", version)
    return {"version": version, "file": path(version), "cells": len(doc["cells"]),
            "suppressed_cells": doc["suppressed_cells"], "min_cell": doc["min_cell"]}


def load(store: Store, version: str) -> dict[str, Any]:
    try:
        return read_json(store, path(version))
    except NotFound:
        raise NotFound("the release is not built", hint="mother release build --version <v>") from None

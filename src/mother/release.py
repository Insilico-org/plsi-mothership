"""`mother release build`: the versioned aggregate release of WIRE §5. One value per active contributor and cell (the
latest numeric value of the coordinate in their latest contribution; a withdrawn contributor is left out), cells by
map, coordinate, unit, age band and sex; cells below the minimum size are left out and only counted. Releases are
never rebuilt under the same version."""

from __future__ import annotations

import json
import statistics
from typing import Any

from mother import audit, clock, index
from mother.contribution import number
from mother.errors import Refused
from mother.stats import sig
from mother.store import Store, read_json, write_json
from mother.vocab import ContributorStatus

MIN_CELL = 10


def floor(min_cell: int) -> int:
    """Code may only make the cell size stricter: below MIN_CELL is a code change, never a flag."""
    if min_cell < MIN_CELL:
        raise Refused(f"min_cell {min_cell} is below the floor {MIN_CELL}", code="MIN_CELL_TOO_SMALL",
                      hint="a smaller cell needs a Data Access Committee decision and a code change")
    return min_cell


def path(version: str) -> str:
    return f"releases/{version}/release.json"


def active(store: Store) -> dict[str, dict[str, Any]]:
    """The index entries of contributors who have not withdrawn (`stop`): the only ones releases and the catalog count."""
    return {cid: e for cid, e in index.load(store).items() if e.get("status") == ContributorStatus.ACTIVE}


def packages(store: Store, cid: str, entry: dict[str, Any]) -> list[dict[str, Any]]:
    """The contributor's stored contributions, in arrival order."""
    return [json.loads(store.read(f"intake/{cid}/{r}.json")) for r in entry["receipts"]]


def latest(docs: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The contribution with the latest `packed` month; the last to arrive among equals."""
    best = None
    for doc in docs:
        if best is None or doc["packed"] >= best["packed"]:
            best = doc
    return best


def _number(coordinate: dict[str, Any]) -> float | None:
    if number(coordinate.get("value")):
        return float(coordinate["value"])
    series = coordinate.get("series")
    points = [p for p in series if isinstance(p, dict) and number(p.get("value"))
              and isinstance(p.get("t") or "", str)] if isinstance(series, list) else []
    return float(max(points, key=lambda p: p.get("t") or "")["value"]) if points else None


def values(doc: dict[str, Any]) -> dict[tuple[str, str, str], float]:
    """(map_id, coordinate, unit) -> value: one per cell key, from the state with the latest `as_of` (the last among
    equals), however many states of one map a package holds. The gate checks the shape; anything malformed that is
    stored anyway is skipped, so one package never stops a release."""
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
    map_id, coordinate, unit, age_band, sex = key
    p25, p50, p75 = statistics.quantiles(vals, n=4, method="inclusive")
    return {"map_id": map_id, "coordinate": coordinate, "unit": unit, "age_band": age_band, "sex": sex, "n": len(vals),
            "mean": sig(statistics.fmean(vals), 4), "sd": sig(statistics.stdev(vals), 4),
            "p25": sig(p25, 4), "p50": sig(p50, 4), "p75": sig(p75, 4)}


def compute(store: Store, version: str, min_cell: int = MIN_CELL) -> dict[str, Any]:
    """The release document from the current intake (nothing is written)."""
    min_cell = floor(min_cell)
    groups: dict[tuple[str, ...], list[float]] = {}
    for cid, entry in active(store).items():
        doc = latest(packages(store, cid, entry))
        subject = (doc or {}).get("subject", {})
        if not all(isinstance(subject.get(k), str) and subject[k] for k in ("age_band", "sex")):
            continue
        for key, v in values(doc).items():
            groups.setdefault((*key, subject["age_band"], subject["sex"]), []).append(v)
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
            "suppressed_cells": doc["suppressed_cells"], "min_cell": min_cell}


def load(store: Store, version: str) -> dict[str, Any]:
    return read_json(store, path(version))

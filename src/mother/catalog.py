"""`mother catalog`: the public catalog of WIRE §5, what the Mothership holds without any value. Only active
contributors count (a withdrawn one is left out); every total below the minimum cell size becomes the string
"<min_cell"; a coordinate or month with fewer contributors than that is not listed, only counted in `suppressed`."""

from __future__ import annotations

from typing import Any

from mother import audit, clock, index
from mother.errors import NotFound
from mother.release import MIN_CELL, floor, values
from mother.store import Store, read_json, write_json
from mother.vocab import ContributionKind

PATH = "catalog.json"


def _count(n: int, min_cell: int) -> int | str:
    return n if n >= min_cell else f"<{min_cell}"


def compute(store: Store, min_cell: int = MIN_CELL) -> dict[str, Any]:
    min_cell = floor(min_cell)
    idx = index.active(store)
    kinds = dict.fromkeys(ContributionKind, 0)
    coordinates: dict[tuple[str, str, str], dict[str, set[str]]] = {}  # key -> month -> contributors
    for cid, entry in idx.items():
        for kind in entry.get("kinds", []):
            kinds[ContributionKind(kind)] += 1
        for doc in index.packages(store, cid, entry):
            for key in values(doc):
                coordinates.setdefault(key, {}).setdefault(doc["packed"], set()).add(cid)
    listed, suppressed = [], 0
    for (m, c, u), months in sorted(coordinates.items()):
        who = set().union(*months.values())
        if len(who) < min_cell:
            suppressed += 1
            continue
        shown = sorted(month for month, ids in months.items() if len(ids) >= min_cell)
        suppressed += len(months) - len(shown)
        listed.append({"map_id": m, "coordinate": c, "unit": u, "contributors": len(who), "months": shown})
    return {"built": clock.month(), "min_cell": min_cell, "contributors": _count(len(idx), min_cell),
            "kinds": {k.value: _count(n, min_cell) for k, n in kinds.items()}, "suppressed": suppressed, "coordinates": listed}


def build(store: Store, min_cell: int = MIN_CELL) -> dict[str, Any]:
    doc = compute(store, min_cell)
    write_json(store, PATH, doc)
    audit.record(store, "catalog_build", PATH)
    return doc


def load(store: Store) -> dict[str, Any]:
    try:
        return read_json(store, PATH)
    except NotFound:
        raise NotFound("no catalog is built yet", hint="mother catalog") from None

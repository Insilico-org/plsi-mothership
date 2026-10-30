"""The analysis ops of WIRE §5 on a release's cells, in the stdlib only, so PLSI and the Mother agree to 1e-6 relative:
`compare_groups` (Welch's t-test on two pooled groups) and `age_trend` (weighted least squares of cell means on the
age-band midpoint). The Student t CDF is the regularized incomplete beta function by its continued fraction."""

from __future__ import annotations

import math
from typing import Any

from mother.contribution import AGE_BANDS
from mother.errors import Refused
from mother.vocab import Op, Sex

GROUP_KEYS = frozenset({"age_band", "sex"})
GROUP_SEXES = (Sex.F, Sex.M)
SPEC_KEYS = {Op.COMPARE_GROUPS: {"op", "map_id", "coordinate", "group_a", "group_b"},
             Op.AGE_TREND: {"op", "map_id", "coordinate", "sex"}}
REQUIRED = {"op", "map_id", "coordinate"}


def sig(x: float, digits: int) -> float:
    """`x` rounded to `digits` significant digits."""
    return float(f"{x:.{digits}g}")


def _betacf(a: float, b: float, x: float) -> float:
    tiny, qab, qap, qam = 1e-300, a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, 1000):
        m2 = 2 * m
        for aa in (m * (b - m) * x / ((qam + m2) * (a + m2)), -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))):
            d = 1.0 + aa * d
            d = 1.0 / (d if abs(d) > tiny else tiny)
            c = 1.0 + aa / c
            c = c if abs(c) > tiny else tiny
            step = d * c
            h *= step
        if abs(step - 1.0) < 1e-15:
            break
    return h


def betainc(a: float, b: float, x: float) -> float:
    """The regularized incomplete beta function I_x(a, b)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    front = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log1p(-x))
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


def t_two_sided(t: float, df: float) -> float:
    """Two-sided p of a Student t statistic."""
    return betainc(df / 2.0, 0.5, df / (df + t * t))


def t_quantile(p_two: float, df: float) -> float:
    """The t with two-sided p `p_two` (e.g. 0.05 -> the 97.5% quantile), by bisection."""
    lo, hi = 0.0, 1.0
    while t_two_sided(hi, df) > p_two:
        lo, hi = hi, hi * 2.0
    for _ in range(200):
        mid = (lo + hi) / 2.0
        lo, hi = (mid, hi) if t_two_sided(mid, df) > p_two else (lo, mid)
    return (lo + hi) / 2.0


def problems(spec: Any) -> list[str]:
    """What is wrong with an analysis spec; empty = valid."""
    if not isinstance(spec, dict) or spec.get("op") not in tuple(Op):
        return [f"op must be one of {', '.join(Op)}"]
    out = [f"missing {k}" for k in sorted(REQUIRED - set(spec))]
    out += ["an unknown field"] if set(spec) - SPEC_KEYS[Op(spec["op"])] else []
    out += [f"{k} must be a string" for k in ("map_id", "coordinate") if not isinstance(spec.get(k, ""), str)]
    groups = [spec.get("group_a"), spec.get("group_b")] if spec["op"] == Op.COMPARE_GROUPS else [{"sex": spec.get("sex")}]
    for g in groups:
        if not isinstance(g, dict) or set(g) - GROUP_KEYS:
            out.append(f"groups hold only {', '.join(sorted(GROUP_KEYS))}")
        elif g.get("sex") is not None and g["sex"] not in GROUP_SEXES:
            out.append("sex must be F or M")
        elif g.get("age_band") is not None and not (isinstance(g["age_band"], str) and g["age_band"] in AGE_BANDS):
            out.append("age_band must be an age band")
    return out


def _pool(cells: list[dict[str, Any]]) -> tuple[int, float, float]:
    """n, mean and SD of the union of the cells' contributors."""
    n = sum(c["n"] for c in cells)
    mean = sum(c["n"] * c["mean"] for c in cells) / n
    ss = sum((c["n"] - 1) * c["sd"] ** 2 + c["n"] * (c["mean"] - mean) ** 2 for c in cells)
    return n, mean, math.sqrt(ss / (n - 1))


def _matching(release: dict[str, Any], spec: dict[str, Any], group: dict[str, Any]) -> list[dict[str, Any]]:
    return [c for c in release["cells"] if c["map_id"] == spec["map_id"] and c["coordinate"] == spec["coordinate"]
            and all(c.get(k) == v for k, v in group.items() if v is not None)]


def _result(effect: float, se: float, df: float, n: int) -> dict[str, Any]:
    if not se > 0:
        raise Refused("the cells have no spread, so the test is undefined", code="degenerate_fit")
    q = t_quantile(0.05, df)
    return {"effect": sig(effect, 6), "interval": [sig(effect - q * se, 6), sig(effect + q * se, 6)], "n": n,
            "statistic": sig(effect / se, 6), "df": sig(df, 6), "p": sig(t_two_sided(effect / se, df), 6)}


def compare_groups(release: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    pooled = []
    for name in ("group_a", "group_b"):
        cells = _matching(release, spec, spec[name])
        if not cells or sum(c["n"] for c in cells) < 2:
            raise Refused(f"{name} matches no cells of the release", code="too_few_cells")
        pooled.append(_pool(cells))
    (na, ma, sa), (nb, mb, sb) = pooled
    va, vb = sa * sa / na, sb * sb / nb
    df = (va + vb) ** 2 / (va * va / (na - 1) + vb * vb / (nb - 1)) if va + vb > 0 else 1.0
    return _result(ma - mb, math.sqrt(va + vb), df, na + nb)


def _midpoint(band: str) -> float:
    lo, _, hi = band.partition("-")
    try:
        return (int(lo) + int(hi)) / 2.0
    except ValueError:
        raise Refused("an age band is not <low>-<high>", code="bad_age_band") from None


def age_trend(release: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    bands: dict[str, list[dict[str, Any]]] = {}
    for c in _matching(release, spec, {"sex": spec.get("sex")}):
        bands.setdefault(c["age_band"], []).append(c)
    if len(bands) < 3:
        raise Refused("age_trend needs at least 3 age bands", code="too_few_cells")
    points = []
    for band, cells in sorted(bands.items()):
        n, mean, sd = _pool(cells)
        if not sd > 0:
            raise Refused("an age band has no spread", code="degenerate_fit")
        points.append((_midpoint(band), mean, n / sd ** 2, n))
    sw = sum(w for _, _, w, _ in points)
    xbar = sum(w * x for x, _, w, _ in points) / sw
    ybar = sum(w * y for _, y, w, _ in points) / sw
    sxx = sum(w * (x - xbar) ** 2 for x, _, w, _ in points)
    slope = sum(w * (x - xbar) * (y - ybar) for x, y, w, _ in points) / sxx
    df = len(points) - 2
    resid = sum(w * (y - ybar - slope * (x - xbar)) ** 2 for x, y, w, _ in points) / df
    return _result(slope, math.sqrt(resid / sxx), df, sum(n for *_, n in points))


def run(release: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    """The §5 result of `spec` on `release`."""
    if found := problems(spec):
        raise Refused("; ".join(found), code="bad_spec")
    return (compare_groups if spec["op"] == Op.COMPARE_GROUPS else age_trend)(release, spec)

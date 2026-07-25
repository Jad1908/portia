"""Normalize/coerce columns — the resolution to what the profiler flags.

Where `checks.profiling` *reports* `numeric_stored_as_text`, whitespace, or a key
dtype that blocks a join, this op *fixes* it — deterministically, and with a
provenance record of exactly what changed and, crucially, **what failed to
convert**. Coercion that silently turns unparseable values into nulls is the
classic footgun; here every failure is counted and sampled (never silent).

Transforms (per column): ``strip``, ``lower``, ``to_numeric``, ``to_string``.
"""

from __future__ import annotations

import pandas as pd

from portia.core.serialize import to_jsonable
from portia.ops.base import OpResult

SAMPLE_FAILED = 5

#: Every field this op reports — see ``ops.join.PROVENANCE_KEYS`` for why.
#: `tests/test_ops_normalize.py` asserts this matches a real run.
PROVENANCE_KEYS = frozenset({"op", "input_rows", "transforms", "flags"})


def apply_normalize(df: pd.DataFrame, transforms: list[dict]) -> OpResult:
    """Apply an ordered list of column transforms, returning the new frame +
    a provenance report. Each transform is ``{"column": ..., "op": ...}``."""
    out = df.copy()
    records = []
    for t in transforms:
        col, op = t["column"], t["op"]
        if col not in out.columns:
            raise ValueError(f"normalize: no such column {col!r}")
        new, record = _TRANSFORMS_dispatch(op, out[col], t)
        out[col] = new
        records.append({"column": col, "op": op, **record})

    provenance = {
        "op": "normalize",
        "input_rows": int(len(df)),
        "transforms": records,
        "flags": ["coercion_failures"] if any(r.get("n_failed", 0) for r in records) else [],
    }
    return OpResult(frame=out, provenance=provenance)


def _TRANSFORMS_dispatch(op: str, series: pd.Series, t: dict):
    if op == "strip":
        return _strip_like(series, "strip")
    if op == "lower":
        return _strip_like(series, "lower")
    if op == "to_numeric":
        return _to_numeric(series, t)
    if op == "to_string":
        return _to_string(series)
    raise ValueError(f"normalize: unknown transform op {op!r}")


def _strip_like(series: pd.Series, op: str):
    as_str = series.astype("string")
    new = as_str.str.strip() if op == "strip" else as_str.str.lower()
    n_changed = int(((as_str != new) & as_str.notna()).sum())
    return new, {"n_changed": n_changed}


def _to_numeric(series: pd.Series, t: dict):
    new = pd.to_numeric(series, errors="coerce")
    failed = new.isna() & series.notna()  # became NaN but wasn't null before
    n_failed = int(failed.sum())
    record: dict = {"n_converted": int(new.notna().sum()), "n_failed": n_failed}
    if n_failed:
        record["sample_failed"] = [to_jsonable(v) for v in series[failed].head(SAMPLE_FAILED)]
    fill = t.get("fill")
    if fill is not None:
        new = new.fillna(fill)
    return new, record


def _to_string(series: pd.Series):
    new = series.astype("string")
    return new, {"n_changed": int(series.notna().sum())}


def render_text(provenance: dict) -> str:
    """Human-readable normalize report for the CLI."""
    lines = [f"normalize  ({provenance['input_rows']} rows)"]
    for r in provenance["transforms"]:
        line = f"  {r['column']}: {r['op']}"
        if r["op"] == "to_numeric":
            line += f"  converted {r['n_converted']}, failed {r['n_failed']}"
            if r.get("sample_failed"):
                line += f"  e.g. {r['sample_failed']}"
        else:
            line += f"  changed {r.get('n_changed', 0)}"
        lines.append(line)
    if provenance["flags"]:
        lines.append(f"  ⚑ {', '.join(provenance['flags'])}")
    return "\n".join(lines)

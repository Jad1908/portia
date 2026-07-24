"""Join/merge check — the unsuppressable drop report.

Diagnoses what a join between two tables *would* do, without materializing it.
The result size, dropped rows, and fan-out are computed from the **key columns
alone** (set operations + multiplicity counts), so the report is honest at scale:
we can say a join explodes 50M rows to 2B without ever building it (docs/PLAN.md,
"schemas + samples, never full data").

This is diagnosis only — read-only, mutates nothing. Its vocabulary (`keys`,
`join_type`, `fan_out`, unmatched counts) is deliberately the vocabulary the
future execution operation will take as parameters, so diagnose → decide →
execute stay coherent (see CLAUDE.md).

The exact result-row formula, per join type, from key multiplicities:
    inner  = Σ_{k in shared}  mult_left[k] * mult_right[k]
    left   = inner + (left rows whose key is unmatched or null)
    right  = inner + (right rows whose key is unmatched or null)
    outer  = inner + both of the above
"""

from __future__ import annotations

from typing import Any

import pandas as pd
from pandas.api import types as ptypes

from portia.core.serialize import round_float, to_jsonable

SAMPLE_KEYS = 5       # example unmatched keys shown per side
LOW_COVERAGE = 0.5    # left match rate below this -> "low_overlap"


def join_report(
    left: pd.DataFrame,
    right: pd.DataFrame,
    on: str | list[str] | None = None,
    *,
    left_on: str | list[str] | None = None,
    right_on: str | list[str] | None = None,
) -> dict:
    """Report the consequences of joining ``left`` to ``right`` on the keys.

    Provide either ``on`` (same key name(s) both sides) or both ``left_on`` and
    ``right_on`` (differently named keys). Mirrors ``pandas.merge`` vocabulary.
    """
    lkeys, rkeys = _resolve_keys(on, left_on, right_on)
    _require_columns(left, lkeys, "left")
    _require_columns(right, rkeys, "right")

    L = _key_side(left, lkeys)
    R = _key_side(right, rkeys)

    # Align multiplicity counts on key value; NaN where a key is absent one side.
    counts = pd.concat([L["counts"].rename("l"), R["counts"].rename("r")], axis=1)
    shared = counts[counts["l"].notna() & counts["r"].notna()]
    left_only = counts[counts["l"].notna() & counts["r"].isna()]
    right_only = counts[counts["r"].notna() & counts["l"].isna()]

    inner_rows = int((shared["l"] * shared["r"]).sum())
    matched_left = int(shared["l"].sum())
    matched_right = int(shared["r"].sum())

    # A left row is dropped by an inner join if its key is null or matches nothing.
    dropped_left = L["null_rows"] + int(left_only["l"].sum())
    dropped_right = R["null_rows"] + int(right_only["r"].sum())

    max_left_fanout = int(shared["r"].max()) if len(shared) else 0   # one left row -> up to N right
    max_right_fanout = int(shared["l"].max()) if len(shared) else 0

    relationship = _relationship(L["unique"], R["unique"])
    key_dtype_match = L["kinds"] == R["kinds"]
    left_coverage = round_float(matched_left / L["n_rows"]) if L["n_rows"] else 0.0
    right_coverage = round_float(matched_right / R["n_rows"]) if R["n_rows"] else 0.0

    report: dict[str, Any] = {
        "keys": {"left": lkeys, "right": rkeys},
        "left": _side_summary(L),
        "right": _side_summary(R),
        "key_dtypes": {"left": L["kinds"], "right": R["kinds"]},
        "key_dtype_match": key_dtype_match,
        "relationship": relationship,
        "overlap": {
            "n_shared_keys": int(len(shared)),
            "n_left_only_keys": int(len(left_only)),
            "n_right_only_keys": int(len(right_only)),
            "left_coverage": left_coverage,
            "right_coverage": right_coverage,
            "sample_left_only": [to_jsonable(k) for k in left_only.index[:SAMPLE_KEYS]],
            "sample_right_only": [to_jsonable(k) for k in right_only.index[:SAMPLE_KEYS]],
        },
        "fan_out": {
            "max_left_to_right": max_left_fanout,
            "max_right_to_left": max_right_fanout,
            "result_per_matched_left": round_float(inner_rows / matched_left) if matched_left else 0.0,
        },
        # Row conservation across every join type — the drop report. left/right
        # dropped = distinct rows from that side that don't survive the join.
        "joins": {
            "inner": {"result_rows": inner_rows, "left_dropped": dropped_left, "right_dropped": dropped_right},
            "left": {"result_rows": inner_rows + dropped_left, "left_dropped": 0, "right_dropped": dropped_right},
            "right": {"result_rows": inner_rows + dropped_right, "left_dropped": dropped_left, "right_dropped": 0},
            "outer": {"result_rows": inner_rows + dropped_left + dropped_right, "left_dropped": 0, "right_dropped": 0},
        },
    }
    report["flags"] = _flags(
        report, dropped_left=dropped_left, dropped_right=dropped_right,
        inner_rows=inner_rows, null_keys=L["null_rows"] + R["null_rows"],
        max_fanout=max(max_left_fanout, max_right_fanout),
    )
    return report


def _key_side(df: pd.DataFrame, keys: list[str]) -> dict:
    n_rows = int(len(df))
    null_mask = df[keys].isna().any(axis=1)
    null_rows = int(null_mask.sum())
    counts = df.loc[~null_mask].groupby(keys, dropna=True).size()
    max_mult = int(counts.max()) if len(counts) else 0
    return {
        "n_rows": n_rows,
        "null_rows": null_rows,
        "counts": counts,
        "n_distinct": int(len(counts)),
        "n_duplicated": int((counts > 1).sum()),
        "max_mult": max_mult,
        "unique": max_mult <= 1,
        "kinds": [_dtype_kind(df[c]) for c in keys],
    }


def _side_summary(side: dict) -> dict:
    return {
        "n_rows": side["n_rows"],
        "n_null_keys": side["null_rows"],
        "n_distinct_keys": side["n_distinct"],
        "n_duplicated_keys": side["n_duplicated"],
        "max_key_multiplicity": side["max_mult"],
        "unique_keys": side["unique"],
    }


def _relationship(left_unique: bool, right_unique: bool) -> str:
    if left_unique and right_unique:
        return "1:1"
    if left_unique:
        return "1:many"
    if right_unique:
        return "many:1"
    return "many:many"


def _dtype_kind(s: pd.Series) -> str:
    """Coarse structural kind for key comparison. int vs float both 'numeric'
    (they join fine); string vs numeric do not (the '123' != 123 silent miss)."""
    if ptypes.is_bool_dtype(s):
        return "boolean"
    if ptypes.is_datetime64_any_dtype(s):
        return "datetime"
    if ptypes.is_numeric_dtype(s):
        return "numeric"
    return "string"


def _flags(report, *, dropped_left, dropped_right, inner_rows, null_keys, max_fanout) -> list[str]:
    flags: list[str] = []
    if not report["key_dtype_match"]:
        flags.append("key_dtype_mismatch")  # most severe: likely zero real matches
    if inner_rows == 0:
        flags.append("no_matches")
    if report["relationship"] == "many:many":
        flags.append("many_to_many")
    if dropped_left > 0:
        flags.append("left_rows_dropped")
    if max_fanout > 1:
        flags.append("fan_out")
    if null_keys > 0:
        flags.append("null_keys")
    if report["overlap"]["left_coverage"] < LOW_COVERAGE:
        flags.append("low_overlap")
    if dropped_right > 0:
        flags.append("right_rows_dropped")
    return flags


def _resolve_keys(on, left_on, right_on) -> tuple[list[str], list[str]]:
    if on is not None:
        keys = [on] if isinstance(on, str) else list(on)
        return keys, keys
    if left_on is not None and right_on is not None:
        lk = [left_on] if isinstance(left_on, str) else list(left_on)
        rk = [right_on] if isinstance(right_on, str) else list(right_on)
        if len(lk) != len(rk):
            raise ValueError(f"left_on ({lk}) and right_on ({rk}) must have equal length")
        return lk, rk
    raise ValueError("provide `on`, or both `left_on` and `right_on`")


def _require_columns(df: pd.DataFrame, keys: list[str], side: str) -> None:
    missing = [k for k in keys if k not in df.columns]
    if missing:
        raise ValueError(f"{side} table is missing key column(s): {missing}")


def render_text(report: dict) -> str:
    """Human-readable rendering for playing with the check."""
    lk = ", ".join(report["keys"]["left"])
    rk = ", ".join(report["keys"]["right"])
    key_desc = lk if lk == rk else f"{lk} = {rk}"
    lines = [
        f"join on [{key_desc}]  —  {report['relationship']}",
        f"  left  {report['left']['n_rows']} rows, "
        f"{report['left']['n_distinct_keys']} distinct keys "
        f"({report['left']['n_null_keys']} null)",
        f"  right {report['right']['n_rows']} rows, "
        f"{report['right']['n_distinct_keys']} distinct keys "
        f"({report['right']['n_null_keys']} null)",
        "",
        f"  key coverage: {report['overlap']['left_coverage']:.0%} of left, "
        f"{report['overlap']['right_coverage']:.0%} of right match",
        f"  fan-out: 1 left row -> up to {report['fan_out']['max_left_to_right']} right",
        "",
        "  result rows / dropped, by join type:",
    ]
    for jt, j in report["joins"].items():
        lines.append(
            f"    {jt:<6} {j['result_rows']:>6} rows   "
            f"(left dropped {j['left_dropped']}, right dropped {j['right_dropped']})"
        )
    if report["overlap"]["sample_left_only"]:
        lines.append(f"  keys only in left:  {report['overlap']['sample_left_only']}")
    if report["overlap"]["sample_right_only"]:
        lines.append(f"  keys only in right: {report['overlap']['sample_right_only']}")
    if report["flags"]:
        lines.append("")
        lines.append(f"  ⚑ {', '.join(report['flags'])}")
    return "\n".join(lines)

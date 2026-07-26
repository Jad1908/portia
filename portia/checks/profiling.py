"""Single-file deterministic profiler.

The profile is the load-bearing artifact of the whole project: the copilot never
sees raw data, it sees *this*. So the output is deliberately **compact and
JSON-serializable** (token-lean) while still carrying the signals a harmonization
copilot needs to decide what to ask about.

Rigor lives here — every number comes from a reproducible pandas call, never from
eyeballing. See docs/PLAN.md ("Deterministic code detects and measures").
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
from pandas.api import types as ptypes

from portia.core.io import load_frame
from portia.core.serialize import round_float, to_jsonable

# Tunables. Kept as module constants so the thresholds behind each flag are one
# obvious place to read and change, not scattered magic numbers.
SAMPLE_VALUES = 3  # example non-null values shown per column
HIGH_NULL_RATE = 0.5  # >= this null rate -> "high_null"
HIGH_CARDINALITY_RATE = 0.9  # distinct/non-null >= this on text -> "high_cardinality"


def profile_path(path: str | Path, **load_kwargs: Any) -> dict:
    """Load a data file via the canonical loader and profile it.

    Any format :func:`portia.io.load_frame` supports works here — profiling is
    format-agnostic because loading is centralized.
    """
    prof = profile_frame(load_frame(path, **load_kwargs))
    prof["source"] = str(path)
    return prof


def profile_frame(df: pd.DataFrame, *, sample_values: int = SAMPLE_VALUES) -> dict:
    """Return a compact, JSON-serializable profile of ``df``."""
    columns = [_profile_column(df[col], sample_values=sample_values) for col in df.columns]
    candidate_keys = [c["name"] for c in columns if "possible_key" in c["flags"]]
    return {
        "n_rows": int(len(df)),
        "n_cols": int(df.shape[1]),
        "candidate_keys": candidate_keys,
        "columns": columns,
    }


def null_rates(df: pd.DataFrame) -> dict[str, float]:
    """Per-column null rate — the one slice of a profile a post-condition needs.

    Split out so ``checks.outcome`` can measure a produced frame without paying
    for quantiles and value counts on every column of every step of every run,
    and without having to strip the ``samples`` (raw values) a full profile
    carries and a post-condition has no business handling.
    """
    n = len(df)
    return {str(col): round_float(df[col].isna().mean()) if n else 0.0 for col in df.columns}


def _profile_column(s: pd.Series, *, sample_values: int) -> dict:
    n = int(len(s))
    n_null = int(s.isna().sum())
    non_null = s.dropna()
    n_non_null = int(len(non_null))
    n_distinct = int(non_null.nunique())

    col: dict[str, Any] = {
        "name": str(s.name),
        "dtype": str(s.dtype),
        "inferred": _infer_semantic(s, non_null),
        "n_null": n_null,
        "null_rate": round_float(n_null / n) if n else 0.0,
        "n_distinct": n_distinct,
        "distinct_rate": round_float(n_distinct / n_non_null) if n_non_null else 0.0,
        "samples": _samples(non_null, sample_values),
    }

    if ptypes.is_numeric_dtype(s) and not ptypes.is_bool_dtype(s) and n_non_null:
        # describe()-style stats: shape + spread, so the agent can reason about a
        # numeric column without seeing it (skew, outliers, tight-vs-spread).
        q = non_null.quantile([0.25, 0.5, 0.75])
        col["min"] = to_jsonable(non_null.min())
        col["max"] = to_jsonable(non_null.max())
        col["mean"] = round_float(float(non_null.mean()))
        col["std"] = round_float(float(non_null.std())) if n_non_null > 1 else None
        col["q25"] = to_jsonable(q.loc[0.25])
        col["median"] = to_jsonable(q.loc[0.5])
        col["q75"] = to_jsonable(q.loc[0.75])
    elif n_non_null and not ptypes.is_bool_dtype(s):
        # describe()'s 'top'/'freq' for non-numeric columns: the modal value.
        counts = non_null.value_counts()
        col["top"] = to_jsonable(counts.index[0])
        col["top_freq"] = int(counts.iloc[0])

    col["flags"] = _flags(
        s, non_null, n=n, n_null=n_null, n_non_null=n_non_null, n_distinct=n_distinct
    )
    return col


def _infer_semantic(s: pd.Series, non_null: pd.Series) -> str:
    """A coarse semantic label beyond the raw pandas dtype.

    Deliberately cheap and conservative — it's a hint for the copilot, not a
    contract. 'text' vs 'categorical' splits on cardinality so the copilot can
    tell a free-text column from a low-cardinality code.
    """
    if len(non_null) == 0:
        return "empty"
    if ptypes.is_bool_dtype(s):
        return "boolean"
    if ptypes.is_datetime64_any_dtype(s):
        return "datetime"
    if ptypes.is_integer_dtype(s):
        return "integer"
    if ptypes.is_float_dtype(s):
        return "float"
    if ptypes.is_numeric_dtype(s):
        return "numeric"
    # object / string-ish
    distinct_rate = non_null.nunique() / len(non_null)
    return "text" if distinct_rate >= HIGH_CARDINALITY_RATE else "categorical"


def _flags(
    s: pd.Series, non_null: pd.Series, *, n: int, n_null: int, n_non_null: int, n_distinct: int
) -> list[str]:
    flags: list[str] = []

    if n_non_null == 0:
        flags.append("all_null")
        return flags

    if n_distinct == 1:
        flags.append("constant")
    if n_null == 0 and n_distinct == n:
        flags.append("possible_key")
    if n and (n_null / n) >= HIGH_NULL_RATE:
        flags.append("high_null")

    # String-column signals. Covers both legacy ``object`` and pandas' native
    # ``str`` dtype (default since pandas 3.0), otherwise these never fire.
    if _is_stringlike(s):
        str_vals = non_null[non_null.map(lambda v: isinstance(v, str))]

        # More than one python type among non-null values (e.g. ints + strings).
        types = {type(v).__name__ for v in non_null}
        if len(types) > 1:
            flags.append("mixed_types")

        if len(str_vals) and (str_vals != str_vals.str.strip()).any():
            flags.append("leading_trailing_whitespace")

        # Values that *look* numeric but are stored as text.
        if len(str_vals):
            parseable = pd.to_numeric(str_vals, errors="coerce").notna().mean()
            if parseable >= 0.9:
                flags.append("numeric_stored_as_text")

        distinct_rate = n_distinct / n_non_null
        if distinct_rate >= HIGH_CARDINALITY_RATE:
            flags.append("high_cardinality")

    return flags


def _is_stringlike(s: pd.Series) -> bool:
    """True for legacy object columns and pandas' native ``str`` dtype alike."""
    return ptypes.is_object_dtype(s) or ptypes.is_string_dtype(s)


def _samples(non_null: pd.Series, k: int) -> list:
    return [to_jsonable(v) for v in non_null.head(k).tolist()]


def render_text(profile: dict) -> str:
    """Human-readable rendering of a profile for playing with the module."""
    lines = []
    src = profile.get("source", "<dataframe>")
    lines.append(f"{src}  —  {profile['n_rows']} rows × {profile['n_cols']} cols")
    keys = profile["candidate_keys"]
    lines.append(f"candidate keys: {', '.join(keys) if keys else '(none)'}")
    lines.append("")
    for c in profile["columns"]:
        head = f"  {c['name']}  [{c['inferred']}/{c['dtype']}]"
        lines.append(head)
        stats = (
            f"    nulls {c['n_null']} ({c['null_rate']:.0%})   "
            f"distinct {c['n_distinct']} ({c['distinct_rate']:.0%})"
        )
        lines.append(stats)
        if "min" in c:
            lines.append(
                f"    range {c['min']} … {c['max']}   mean {c['mean']}   "
                f"median {c['median']}   std {c['std']}"
            )
            lines.append(f"    quartiles {c['q25']} / {c['median']} / {c['q75']}")
        if "top" in c:
            lines.append(f"    most common: {c['top']!r} ×{c['top_freq']}")
        lines.append(f"    e.g. {c['samples']}")
        if c["flags"]:
            lines.append(f"    ⚑ {', '.join(c['flags'])}")
        lines.append("")
    return "\n".join(lines)

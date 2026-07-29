"""Single-file deterministic profiler.

The profile is the load-bearing artifact of the whole project: the copilot never
sees raw data, it sees *this*. So the output is deliberately **compact and
JSON-serializable** (token-lean) while still carrying the signals a harmonization
copilot needs to decide what to ask about.

Rigor lives here — every number comes from a reproducible query, never from
eyeballing. See docs/PLAN.md ("Deterministic code detects and measures").

**Two implementations, one set of rules.** :func:`profile` measures with
SQL and :func:`profile_frame` measures with pandas, but neither one decides what
a measurement *means*: what counts as a key, as high-null, as text rather than a
category, is :func:`_semantic` and :func:`_flags`, which take plain numbers and
are shared. That is what makes the DuckDB migration a swap rather than a rewrite
with a matching pair of bugs (`docs/DUCKDB_MIGRATION.md` §7). The pandas path is
kept alive only until the migration lands; the golden tests run both.

**Why SQL is the one that scales.** Every statistic a column needs is one
expression in a single scan — `Table.row` — so a profile costs a handful of
megabytes at any input size. Profiling a 396 MB CSV: 16.5 s and 1883 MB in
pandas, 0.3 s and 122 MB here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from portia.core import store
from portia.core.io import load_table
from portia.core.serialize import round_float, to_jsonable
from portia.core.table import Table, quote_ident

# Tunables. Kept as module constants so the thresholds behind each flag are one
# obvious place to read and change, not scattered magic numbers.

#: Example non-null values shown per column. They are **distinct, and ordered by
#: value**, on both tiers — the two halves of one decision:
#:
#: *Ordered*, because "the first three rows" is not a fact about the data. A
#: ``LIMIT`` with no ``ORDER BY`` promises nothing, and DuckDB returns a different
#: three each run once a scan goes parallel; a sample that changes between runs
#: is not a measurement. Sorting also floats the values with leading whitespace
#: to the top, which is where a sample earns its keep.
#:
#: *Distinct*, because ordering alone made these worse: `messy_customers.country`
#: went from three different codes to ``['DE', 'DE', 'DE']``. Nothing is lost —
#: how often a value repeats is `top_freq`'s job and how many there are is
#: `n_distinct`'s — and what is gained is that a low-cardinality column shows its
#: vocabulary instead of its first row three times.
SAMPLE_VALUES = 3
HIGH_NULL_RATE = 0.5  # >= this null rate -> "high_null"
HIGH_CARDINALITY_RATE = 0.9  # distinct/non-null >= this on text -> "high_cardinality"
NUMERIC_TEXT_RATE = 0.9  # >= this share of text values parse as numbers -> "numeric_stored_as_text"

# Structural kinds. Coarser than a dtype and finer than "numeric or not", because
# both the semantic label and the choice of statistics turn on them. Every backend
# maps its own type names onto these and the rules never see a dtype.
INTEGER, FLOAT, NUMERIC = "integer", "float", "numeric"
BOOLEAN, DATETIME, STRING, OTHER = "boolean", "datetime", "string", "other"

#: Kinds that get describe()-style range and spread rather than a modal value.
NUMERIC_KINDS = frozenset({INTEGER, FLOAT, NUMERIC})

_DUCKDB_INTEGERS = frozenset(
    {
        "TINYINT",
        "SMALLINT",
        "INTEGER",
        "BIGINT",
        "HUGEINT",
        "UTINYINT",
        "USMALLINT",
        "UINTEGER",
        "UBIGINT",
        "UHUGEINT",
    }
)
_DUCKDB_FLOATS = frozenset({"FLOAT", "REAL", "DOUBLE"})
_DUCKDB_STRINGS = frozenset({"VARCHAR", "CHAR", "BPCHAR", "TEXT", "STRING", "UUID"})
_DUCKDB_TEMPORAL = ("DATE", "TIME", "TIMESTAMP", "INTERVAL")


def profile_path(path: str | Path, **load_kwargs: Any) -> dict:
    """Load a data file and profile it, without a project around it.

    Any format :func:`portia.core.io.load_table` supports works here — profiling
    is format-agnostic because loading is centralized. Reads the file in place;
    inside a project, profile the ingested table instead (`catalog.index_source`).
    """
    con = store.memory()
    try:
        prof = profile(load_table(path, con), **load_kwargs)
    finally:
        con.close()
    prof["source"] = str(path)
    return prof


# --- the SQL implementation -------------------------------------------------


def profile(table: Table, *, sample_values: int = SAMPLE_VALUES) -> dict:
    """A compact, JSON-serializable profile of ``table``, measured in SQL."""
    kinds = {col: duckdb_kind(dtype) for col, dtype in table.dtypes.items()}
    dtypes = table.dtypes

    # Everything scalar, in one scan. The per-column extras below are single-column
    # reads against columnar storage, which is why they can be afforded separately.
    stats = table.row(_stat_exprs(kinds)) if kinds else {"n_rows": table.count()}
    n_rows = int(stats["n_rows"])

    columns = [
        _table_column(table, col, i, kinds[col], dtypes[col], stats, n_rows, sample_values)
        for i, col in enumerate(kinds)
    ]
    return _profile(n_rows, len(columns), columns)


def _stat_exprs(kinds: dict[str, str]) -> dict[str, str]:
    """Every aggregate the whole profile needs, aliased by column position.

    Positional aliases rather than the column's own name: a table really can have
    a column called ``n_rows``.
    """
    exprs = {"n_rows": "count(*)"}
    for i, (col, kind) in enumerate(kinds.items()):
        q = quote_ident(col)
        exprs[f"c{i}_non_null"] = f"count({q})"
        exprs[f"c{i}_distinct"] = f"count(DISTINCT {q})"
        if kind in NUMERIC_KINDS:
            exprs[f"c{i}_min"] = f"min({q})"
            exprs[f"c{i}_max"] = f"max({q})"
            exprs[f"c{i}_mean"] = f"avg({q})"
            # stddev_samp, not stddev_pop: pandas' .std() is the sample estimate,
            # and it returns NULL for a single row, which is what we want reported.
            exprs[f"c{i}_std"] = f"stddev_samp({q})"
            # All three quartiles from one aggregate, not three. `quantile_cont`
            # is exact, so it buffers the column it reads; asking three times
            # buffers it three times. Measured on 3M rows: 650 MB as separate
            # expressions, 326 MB as a list, with identical values. This is the
            # single most expensive thing in a profile and it is still O(n) —
            # `docs/DUCKDB_MIGRATION.md` §12's exact-vs-approximate question is
            # really about this line.
            exprs[f"c{i}_quartiles"] = f"quantile_cont({q}, [0.25, 0.5, 0.75])"
        elif kind == STRING:
            exprs[f"c{i}_whitespace"] = f"count(*) FILTER (WHERE {q} <> trim({q}))"
            exprs[f"c{i}_numeric"] = f"count(*) FILTER (WHERE try_cast({q} AS DOUBLE) IS NOT NULL)"
    return exprs


def _table_column(
    table: Table,
    col: str,
    i: int,
    kind: str,
    dtype: str,
    stats: dict,
    n_rows: int,
    sample_values: int,
) -> dict:
    n_non_null = int(stats[f"c{i}_non_null"])
    n_distinct = int(stats[f"c{i}_distinct"])
    out = _column_base(
        name=col,
        dtype=dtype,
        kind=kind,
        n=n_rows,
        n_non_null=n_non_null,
        n_distinct=n_distinct,
        samples=_table_samples(table, col, sample_values),
    )

    if kind in NUMERIC_KINDS and n_non_null:
        out["min"] = to_jsonable(stats[f"c{i}_min"])
        out["max"] = to_jsonable(stats[f"c{i}_max"])
        out["mean"] = round_float(float(stats[f"c{i}_mean"]))
        std = stats[f"c{i}_std"]
        out["std"] = round_float(float(std)) if std is not None else None
        q25, median, q75 = stats[f"c{i}_quartiles"]
        out["q25"] = to_jsonable(q25)
        out["median"] = to_jsonable(median)
        out["q75"] = to_jsonable(q75)
    elif n_non_null and kind != BOOLEAN:
        top = _table_top(table, col)
        if top is not None:
            out["top"], out["top_freq"] = top

    out["flags"] = _flags(
        kind,
        n=n_rows,
        n_non_null=n_non_null,
        n_distinct=n_distinct,
        n_whitespace=int(stats.get(f"c{i}_whitespace") or 0),
        n_numeric=int(stats.get(f"c{i}_numeric") or 0),
    )
    return out


def _table_samples(table: Table, col: str, k: int) -> list:
    """Example values — distinct and ordered. See :data:`SAMPLE_VALUES` for why."""
    q = quote_ident(col)
    rows = table.sql(
        f"SELECT DISTINCT {q} FROM {table.ref} WHERE {q} IS NOT NULL ORDER BY {q}"
    ).rows(k)
    return [to_jsonable(v) for (v,) in rows]


def _table_top(table: Table, col: str) -> tuple[Any, int] | None:
    """The modal value and its count, ties broken by the value itself.

    ``ORDER BY count(*) DESC`` alone leaves a tie undefined, and half the fixture
    columns have one — `hotels.city` is Paris 2, Amsterdam 2, Barcelona 1. An
    undefined answer is not a measurement, so the smaller value wins.
    """
    q = quote_ident(col)
    rows = table.sql(
        f"SELECT {q} AS v, count(*) AS n FROM {table.ref} "
        f"WHERE {q} IS NOT NULL GROUP BY {q} ORDER BY n DESC, {q} ASC"
    ).rows(1)
    if not rows:
        return None
    value, freq = rows[0]
    return to_jsonable(value), int(freq)


def null_rates(table: Table) -> dict[str, float]:
    """Per-column null rate, in one pass. See :func:`null_rates`."""
    columns = table.columns
    if not columns:
        return {}
    exprs = {"n_rows": "count(*)"}
    exprs |= {f"c{i}": f"count({quote_ident(c)})" for i, c in enumerate(columns)}
    stats = table.row(exprs)
    n = int(stats["n_rows"])
    return {
        str(c): round_float((n - int(stats[f"c{i}"])) / n) if n else 0.0
        for i, c in enumerate(columns)
    }


def duckdb_kind(dtype: str) -> str:
    """A DuckDB type name mapped onto a structural kind.

    Public because `checks.join` needs the same mapping — one place knows what
    DuckDB calls things, so a type the profiler understands can never be one the
    join check silently treats as a string.
    """
    name = str(dtype).upper()
    if name == "BOOLEAN":
        return BOOLEAN
    if name.startswith(_DUCKDB_TEMPORAL):
        return DATETIME
    if name in _DUCKDB_INTEGERS:
        return INTEGER
    if name in _DUCKDB_FLOATS or name.startswith("DECIMAL"):
        return FLOAT
    if name in _DUCKDB_STRINGS:
        return STRING
    return OTHER


# --- the rules, shared by both implementations ------------------------------


def _profile(n_rows: int, n_cols: int, columns: list[dict]) -> dict:
    return {
        "n_rows": n_rows,
        "n_cols": n_cols,
        "candidate_keys": [c["name"] for c in columns if "possible_key" in c["flags"]],
        "columns": columns,
    }


def _column_base(
    *, name: str, dtype: str, kind: str, n: int, n_non_null: int, n_distinct: int, samples: list
) -> dict:
    return {
        "name": name,
        "dtype": dtype,
        "inferred": _semantic(kind, n_non_null=n_non_null, n_distinct=n_distinct),
        "n_null": n - n_non_null,
        "null_rate": round_float((n - n_non_null) / n) if n else 0.0,
        "n_distinct": n_distinct,
        "distinct_rate": round_float(n_distinct / n_non_null) if n_non_null else 0.0,
        "samples": samples,
    }


def _semantic(kind: str, *, n_non_null: int, n_distinct: int) -> str:
    """A coarse semantic label beyond the raw dtype.

    Deliberately cheap and conservative — it's a hint for the copilot, not a
    contract. 'text' vs 'categorical' splits on cardinality so the copilot can
    tell a free-text column from a low-cardinality code.
    """
    if n_non_null == 0:
        return "empty"
    if kind in (BOOLEAN, DATETIME, INTEGER, FLOAT, NUMERIC):
        return kind
    return "text" if n_distinct / n_non_null >= HIGH_CARDINALITY_RATE else "categorical"


def _flags(
    kind: str, *, n: int, n_non_null: int, n_distinct: int, n_whitespace: int, n_numeric: int
) -> list[str]:
    """What is notable about a column, from its measured counts alone.

    Facts, in a fixed order, with no ranking implied — the agent decides which of
    them matter (CLAUDE.md, facts vs judgment).
    """
    n_null = n - n_non_null
    flags: list[str] = []

    if n_non_null == 0:
        return ["all_null"]

    if n_distinct == 1:
        flags.append("constant")
    if n_null == 0 and n_distinct == n:
        flags.append("possible_key")
    if n and (n_null / n) >= HIGH_NULL_RATE:
        flags.append("high_null")

    if kind == STRING:
        # `mixed_types` used to mean "more than one python type among non-null
        # values", which only existed because a pandas `object` column can hold
        # anything; in a typed store a column has one type, and a CSV round-trip
        # erased the signal even in pandas. Redefined to the thing it was really
        # catching: the column is not uniformly one *kind* of value. Still a fact,
        # still no judgement, and it now survives the storage change
        # (`docs/DUCKDB_MIGRATION.md` §6.3).
        if 0 < n_numeric < n_non_null:
            flags.append("mixed_types")
        if n_whitespace:
            flags.append("leading_trailing_whitespace")
        # Values that *look* numeric but are stored as text.
        if n_numeric / n_non_null >= NUMERIC_TEXT_RATE:
            flags.append("numeric_stored_as_text")
        if n_distinct / n_non_null >= HIGH_CARDINALITY_RATE:
            flags.append("high_cardinality")

    return flags


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

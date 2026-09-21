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

import contextvars
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from portia.core import backend, cancel
from portia.core import dialect as dialects
from portia.core.dialect import Dialect
from portia.core.io import connect, load_table, rescans
from portia.core.serialize import round_float, to_jsonable
from portia.core.table import Table

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

#: DuckDB's names, then Snowflake's, BigQuery's and PostgreSQL's where they differ.
#: Snowflake's numbers are all ``NUMBER(p, s)`` and are read by `_scale`; its
#: text is ``TEXT`` and its timestamps are ``TIMESTAMP_NTZ``/``_LTZ``/``_TZ``,
#: which the prefix match already covers. BigQuery says ``INT64``, ``FLOAT64``,
#: ``NUMERIC``/``BIGNUMERIC`` (decimals, so a float here), ``BOOL`` and
#: ``DATETIME``; its ``ARRAY<…>``, ``STRUCT<…>``, ``JSON`` and ``GEOGRAPHY``
#: are *other*, which is honest — nothing in a profile describes a nested value.
_INTEGERS = frozenset(
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
        "INT",
        "INT64",
    }
)
_FLOATS = frozenset({"FLOAT", "FLOAT4", "FLOAT8", "REAL", "DOUBLE", "DOUBLE PRECISION", "FLOAT64"})
_DECIMALS = ("DECIMAL", "NUMERIC", "BIGNUMERIC")
_STRINGS = frozenset({"VARCHAR", "CHAR", "BPCHAR", "TEXT", "STRING", "UUID"})
#: PostgreSQL spells its bounded strings out, with the bound: ``character
#: varying(20)``, ``character(3)``.
_STRING_PREFIXES = ("CHARACTER",)
_TEMPORAL = ("DATE", "TIME", "TIMESTAMP", "INTERVAL")
_BOOLEANS = frozenset({"BOOLEAN", "BOOL"})


#: Where one parse of a re-scanning file is kept, for the length of one profile.
#: The connection is created and closed by :func:`profile_path`, so the table
#: dies with it and nothing is left on disk.
_PARSED_ONCE = "_portia_profile_source"


def profile_path(path: str | Path, **load_kwargs: Any) -> dict:
    """Load a data file and profile it, without a project around it.

    Any format :func:`portia.core.io.load_table` supports works here — profiling
    is format-agnostic because loading is centralized. Reads the file in place;
    inside a project, profile the ingested table instead (`catalog.index_source`).
    """
    con = connect()
    try:
        prof = profile(_source(path, con), **load_kwargs)
    finally:
        con.close()
    prof["source"] = str(path)
    return prof


def _source(path: str | Path, con: Any) -> Table:
    """The table to profile: the file itself, or one parse of it.

    **A profile is one scan plus two questions per column.** Every scalar
    statistic comes from a single pass (:func:`_stat_exprs`), but `_table_samples`
    and `_table_top` each need their own query, and on a reader that re-parses
    its file that is a full parse per column.

    Measured on a real 40 MB, 34,214-row, **191-column** CSV: **108 s**, against
    **1.57 s** for the same rows and columns stored as Parquet — 69×, and 43% of
    the wall time of indexing a 6.1 GB, 285M-row project that the CSV was 0.01%
    of. Parsing once first is what closes that gap; the per-column queries were
    never the expensive part, the parse behind each of them was.

    **Only for formats that re-scan.** Parquet is columnar and its per-column
    reads are already nearly free, so it stays lazy and is never copied.

    Peak memory *fell* — 5647 MB to 2578 MB on that file — because each of those
    191 parses was buffering too. The copy is bounded by the file and DuckDB
    spills it past ``memory_limit`` rather than failing, so the worst case is
    disk. `DUCKDB_MIGRATION.md` §14.
    """
    table = load_table(path, con)
    if not rescans(path):
        return table
    con.execute(f"CREATE TEMP TABLE {_PARSED_ONCE} AS SELECT * FROM ({table.query})")
    return Table(name=table.name, query=f"SELECT * FROM {_PARSED_ONCE}", con=con)


# --- the SQL implementation -------------------------------------------------


def profile(table: Table, *, sample_values: int = SAMPLE_VALUES) -> dict:
    """A compact, JSON-serializable profile of ``table``, measured in SQL."""
    kinds = {col: kind_of(dtype) for col, dtype in table.dtypes.items()}
    dtypes = table.dtypes
    dialect = dialects.of(table.con)

    # Everything scalar, in one scan. The per-column extras below are single-column
    # reads against columnar storage, which is why they can be afforded separately.
    stats = table.row(_stat_exprs(kinds, dialect)) if kinds else {"n_rows": table.count()}
    n_rows = int(stats["n_rows"])

    extras = _extras(table, kinds, stats, sample_values)
    columns = [
        _table_column(col, i, kinds[col], dtypes[col], stats, n_rows, extras[col], dialect)
        for i, col in enumerate(kinds)
    ]
    return _profile(n_rows, len(columns), columns)


#: What the two per-column queries found: the example values, and the modal
#: value with its count where the column has one.
Extras = tuple[list, "tuple[Any, int] | None"]


def _extras(
    table: Table, kinds: dict[str, str], stats: dict, sample_values: int
) -> dict[str, Extras]:
    """The two questions per column, asked one at a time or several at once.

    **A profile is one scan plus two queries per column**, and on a warehouse
    that charges a fixed toll per query the queries are the whole cost: on
    BigQuery each is about two seconds of job creation and polling whatever it
    scans, so a 37-column table was 75 round trips and six minutes on
    2026-09-06, with the bytes billed trivial. Asking `backend.parallel_queries`
    of them at once overlaps the waiting; the questions, and therefore the
    bill, are the same.

    **Locally nothing changes.** The width is one on DuckDB and on Snowflake,
    and at one this is the plain loop it always was, on the caller's own
    connection — so every golden file still holds. Wider, each column runs on
    a sibling handle (`Table.using`), because a connection is not thread-safe
    and a warehouse session's sibling shares its in-flight state, which is
    what lets one Stop cancel every job at once.

    The order of the answer is the column order whatever the order of arrival;
    a worker inherits the caller's cancel scope (`contextvars.copy_context`)
    and checks it before starting, so a press stops the next column from
    going out as well as the ones in flight.
    """
    columns = list(kinds)
    non_null = {col: int(stats[f"c{i}_non_null"]) for i, col in enumerate(columns)}
    width = backend.parallel_queries(table.con)
    if width == 1 or len(columns) < 2:
        return {
            col: _column_extras(table, col, kinds[col], non_null[col], sample_values)
            for col in columns
        }
    with ThreadPoolExecutor(max_workers=width, thread_name_prefix="portia-profile") as pool:
        futures = {
            col: pool.submit(
                contextvars.copy_context().run,
                _column_extras,
                table.using(table.con.cursor()),
                col,
                kinds[col],
                non_null[col],
                sample_values,
            )
            for col in columns
        }
        try:
            return {col: future.result() for col, future in futures.items()}
        except BaseException:
            # One column failed or was stopped: nothing queued behind it starts.
            pool.shutdown(wait=False, cancel_futures=True)
            raise


def _column_extras(
    table: Table, col: str, kind: str, n_non_null: int, sample_values: int
) -> Extras:
    cancel.check()
    samples = _table_samples(table, col, sample_values, kind)
    wants_top = bool(n_non_null) and kind not in NUMERIC_KINDS and kind != BOOLEAN
    return samples, _table_top(table, col, kind) if wants_top else None


def _comparable(dialect: Dialect, col: str, kind: str) -> str:
    """The column as something the engine can compare, group and order.

    A type portia has no kind for is read as text where the engine needs
    telling (`Dialect.as_text`): PostgreSQL's ``json`` has no equality, so
    ``count(DISTINCT …)`` over one failed the whole table's profile.
    """
    q = dialect.quote(col)
    return dialect.as_text(q) if kind == OTHER else q


def _stat_exprs(kinds: dict[str, str], dialect: Dialect = dialects.DUCKDB) -> dict[str, str]:
    """Every aggregate the whole profile needs, aliased by column position.

    Positional aliases rather than the column's own name: a table really can have
    a column called ``n_rows``.
    """
    exprs = {"n_rows": "count(*)"}
    for i, (col, kind) in enumerate(kinds.items()):
        q = dialect.quote(col)
        exprs[f"c{i}_non_null"] = f"count({q})"
        exprs[f"c{i}_distinct"] = f"count(DISTINCT {_comparable(dialect, col, kind)})"
        if kind in NUMERIC_KINDS:
            exprs[f"c{i}_min"] = f"min({q})"
            exprs[f"c{i}_max"] = f"max({q})"
            # The two moments are sums, and an engine that keeps a decimal's sum
            # in fixed point overflows on them (`Dialect.as_float`).
            moment = dialect.as_float(q)
            exprs[f"c{i}_mean"] = f"avg({moment})"
            # stddev_samp, not stddev_pop: pandas' .std() is the sample estimate,
            # and it returns NULL for a single row, which is what we want reported.
            exprs[f"c{i}_std"] = f"stddev_samp({moment})"
            # The three quartiles, in whatever shape the engine answers them —
            # one list-valued aggregate on DuckDB, three ordered-set aggregates
            # on Snowflake (`core/dialect.py`). This is the single most expensive
            # thing in a profile and it is still O(n) — `docs/DUCKDB_MIGRATION.md`
            # §12's exact-vs-approximate question is really about this line.
            for suffix, expr in dialect.quartile_exprs(q).items():
                exprs[f"c{i}_{suffix}"] = expr
        elif kind == STRING:
            text = dialect.as_text(q)
            exprs[f"c{i}_whitespace"] = dialect.count_where(f"{text} <> trim({text})")
            parses = dialect.try_cast(q, dialect.double)
            exprs[f"c{i}_numeric"] = dialect.count_where(f"{parses} IS NOT NULL")
    return exprs


def _table_column(
    col: str,
    i: int,
    kind: str,
    dtype: str,
    stats: dict,
    n_rows: int,
    extras: Extras,
    dialect: Dialect = dialects.DUCKDB,
) -> dict:
    samples, top = extras
    n_non_null = int(stats[f"c{i}_non_null"])
    n_distinct = int(stats[f"c{i}_distinct"])
    out = _column_base(
        name=col,
        dtype=dtype,
        kind=kind,
        n=n_rows,
        n_non_null=n_non_null,
        n_distinct=n_distinct,
        samples=samples,
    )

    if kind in NUMERIC_KINDS and n_non_null:
        out["min"] = to_jsonable(stats[f"c{i}_min"])
        out["max"] = to_jsonable(stats[f"c{i}_max"])
        out["mean"] = round_float(float(stats[f"c{i}_mean"]))
        std = stats[f"c{i}_std"]
        out["std"] = round_float(float(std)) if std is not None else None
        q25, median, q75 = dialect.read_quartiles(stats, f"c{i}")
        out["q25"] = to_jsonable(q25)
        out["median"] = to_jsonable(median)
        out["q75"] = to_jsonable(q75)
        if dialect.approximate_quartiles:
            # A field, not a silence: on an engine with no exact percentile
            # aggregate these three are a sketch, and a number the agent may
            # repeat has to say so (`core/dialect.BigQuery`).
            out["approximate"] = ["q25", "median", "q75"]
    elif top is not None:
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


def _table_samples(table: Table, col: str, k: int, kind: str = STRING) -> list:
    """Example values — distinct and ordered. See :data:`SAMPLE_VALUES` for why."""
    q = _comparable(table.dialect, col, kind)
    rows = table.sql(
        f"SELECT DISTINCT {q} FROM {table.ref} WHERE {q} IS NOT NULL ORDER BY {q}"
    ).rows(k)
    return [to_jsonable(v) for (v,) in rows]


def _table_top(table: Table, col: str, kind: str = STRING) -> tuple[Any, int] | None:
    """The modal value and its count, ties broken by the value itself.

    ``ORDER BY count(*) DESC`` alone leaves a tie undefined, and half the fixture
    columns have one — `hotels.city` is Paris 2, Amsterdam 2, Barcelona 1. An
    undefined answer is not a measurement, so the smaller value wins.
    """
    q = _comparable(table.dialect, col, kind)
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
    q = table.dialect.quote
    exprs |= {f"c{i}": f"count({q(c)})" for i, c in enumerate(columns)}
    stats = table.row(exprs)
    n = int(stats["n_rows"])
    return {
        str(c): round_float((n - int(stats[f"c{i}"])) / n) if n else 0.0
        for i, c in enumerate(columns)
    }


def kind_of(dtype: str) -> str:
    """An engine's type name mapped onto a structural kind.

    Public because `checks.join` needs the same mapping — one place knows what
    an engine calls things, so a type the profiler understands can never be one
    the join check silently treats as a string.

    DuckDB's names and Snowflake's, in one function rather than one per dialect:
    they barely overlap in spelling and never disagree in meaning, so a second
    table would be the same table with the rows shuffled. Snowflake reports
    every number as ``NUMBER(precision, scale)`` and says integer by a zero
    scale, which is the one shape that needs reading rather than matching.
    """
    name = str(dtype).upper()
    if name in _BOOLEANS:
        return BOOLEAN
    if name.startswith(_TEMPORAL):
        return DATETIME
    if name in _INTEGERS:
        return INTEGER
    if name.startswith("NUMBER"):
        return INTEGER if _scale(name) == 0 else FLOAT
    if name in _FLOATS or name.startswith(_DECIMALS):
        return FLOAT
    if name in _STRINGS or name.startswith(_STRING_PREFIXES):
        return STRING
    return OTHER


#: The name this had while DuckDB was the only engine. Kept so nothing that
#: imported it breaks; new code says `kind_of`.
duckdb_kind = kind_of


def _scale(name: str) -> int | None:
    """The scale in ``NUMBER(38,0)``, or ``None`` when the name carries none."""
    inside = name[name.find("(") + 1 : name.rfind(")")] if "(" in name else ""
    parts = [p.strip() for p in inside.split(",")]
    if len(parts) == 2 and parts[1].isdigit():
        return int(parts[1])
    return None


# --- the rules, shared by both implementations ------------------------------


def _profile(n_rows: int, n_cols: int, columns: list[dict]) -> dict:
    return {
        "n_rows": n_rows,
        "n_cols": n_cols,
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

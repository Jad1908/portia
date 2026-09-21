"""The few SQL expressions the checks spell differently per engine.

Nearly everything `checks/` and `ops/` write is common SQL: ``count``,
``count(DISTINCT …)``, ``min``/``max``/``avg``, ``stddev_samp``, ``coalesce``,
``CAST``, a ``FULL OUTER JOIN`` over two CTEs. This module holds what is not,
and it is short on purpose: a dialect that grows a method per check is a second
checks layer.

**The dialect is read off the connection, never off a global.** A `Table` is a
name, a query and a connection (`core/table.py`), and the connection is the one
thing that knows what engine will parse the query. `of` asks it; a connection
that says nothing is DuckDB, which is what every connection was before
`docs/CONNECTOR.md`. So a check never has to be told which warehouse it is on,
and a test that profiles a DuckDB table while a Snowflake backend is active
still profiles it correctly.

**An instance per engine, no registry.** A new engine adds a subclass here and
one prompt file (`prompts/backend/`); that is the whole of what `CONNECTOR.md`
§2.9 asks of it.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


class Dialect:
    """DuckDB's spellings. The base, because DuckDB is the local engine."""

    name = "duckdb"

    #: The type names the two casts the engine writes spell: to text, in
    #: `ops/normalize` and the join check's key comparison, and to a float, in
    #: the profiler's *does this string parse as a number* count and
    #: ``to_numeric``. Everything else the checks write is common SQL.
    text = "VARCHAR"
    double = "DOUBLE"
    #: Whether `quartile_exprs` answers with a sketch rather than an exact
    #: pass. The profiler writes it on the column when true, because a
    #: silence reads as *exact* (`SQL_LINEAGE.md` §9's rule).
    approximate_quartiles = False

    def quote(self, identifier: str) -> str:
        """An identifier, quoted the way this engine reads one. A column really can be called ``order``.

        Double quotes are the standard's and DuckDB's and Snowflake's. **Not
        every engine's**: BigQuery reads a double-quoted token as a string
        literal and quotes identifiers with backticks, which is why this is a
        dialect method and not the module function it was (`CONNECTORS.md` §8).
        """
        return '"' + str(identifier).replace('"', '""') + '"'

    def try_cast(self, expr: str, type_name: str) -> str:
        """A cast that yields NULL where it would fail, so failures can be counted."""
        return f"try_cast({expr} AS {type_name})"

    def as_text(self, expr: str) -> str:
        """``expr`` where a string function will accept it.

        Unchanged here and on both warehouses, which cast to text by themselves.
        Postgres does not: ``trim`` of a ``uuid`` is an error there, and the
        profiler calls a ``uuid`` a string (`checks.profiling.kind_of`).
        """
        return expr

    def as_float(self, expr: str) -> str:
        """``expr`` where ``avg`` and ``stddev_samp`` can take it without overflowing.

        Unchanged here: DuckDB computes both moments in floating point whatever
        the column's type. Snowflake does not (`Snowflake.as_float`).
        """
        return expr

    def count_where(self, condition: str) -> str:
        """``count(*)`` over the rows where ``condition`` holds."""
        return f"count(*) FILTER (WHERE {condition})"

    def sum_where(self, expr: str, condition: str) -> str:
        return f"sum({expr}) FILTER (WHERE {condition})"

    def max_where(self, expr: str, condition: str) -> str:
        return f"max({expr}) FILTER (WHERE {condition})"

    def quartile_exprs(self, column: str) -> dict[str, str]:
        """The three quartiles, as ``{alias suffix: expression}``.

        **One list-valued aggregate on DuckDB, not three.** ``quantile_cont`` is
        exact, so it buffers the column it reads; asking three times buffers it
        three times. Measured on 3M rows: 650 MB as separate expressions, 326 MB
        as a list, identical values (`DUCKDB_MIGRATION.md` §13). This is the
        single most expensive thing in a profile, which is why the shape of the
        answer is the dialect's to decide and :meth:`read_quartiles` reads it
        back.
        """
        return {"quartiles": f"quantile_cont({column}, [0.25, 0.5, 0.75])"}

    def read_quartiles(self, stats: dict, prefix: str) -> tuple[Any, Any, Any]:
        """``(q25, median, q75)`` out of the row :meth:`quartile_exprs` produced."""
        q25, median, q75 = stats[f"{prefix}_quartiles"]
        return q25, median, q75

    #: Whether every type the engine stores can be sorted. False where one
    #: cannot (PostgreSQL's ``json``), and a caller ordering a projection it did
    #: not choose then passes ``skip`` to :meth:`order_by_all`.
    orders_every_type = True

    def order_by_all(self, n_columns: int, skip: Iterable[int] = ()) -> str:
        """Order by every projected column, so a ``LIMIT`` is the same rows every run.

        ``skip`` is the zero-based positions to leave out of the ordering, for
        an engine that says it cannot sort every type.
        """
        return "ORDER BY ALL"

    def fold(self, identifier: str) -> str:
        """How the engine spells an identifier written unquoted.

        DuckDB keeps the case it was given and matches case-insensitively, so
        the spelling is the writer's. Used where portia **creates** something
        (`pipeline._write_into_warehouse`): a table is created under the folded
        name, quoted, so it is the same object the team reaches unquoted, and a
        reserved word as a model name still works.
        """
        return identifier

    def write_table(self, home: list[str], name: str, query: str) -> list[str]:
        """The statements that leave ``query``'s rows in ``home.name``, replacing what was there.

        ``home`` is ``[database, schema]`` and every part arrives folded. A list
        because not every engine has ``CREATE OR REPLACE TABLE``
        (`pipeline._write_into_warehouse` runs them in order).
        """
        where = ".".join(self.quote(p) for p in home)
        table = ".".join(self.quote(p) for p in [*home, name])
        return [
            f"CREATE SCHEMA IF NOT EXISTS {where}",
            f"CREATE OR REPLACE TABLE {table} AS {query}",
        ]


def _by_ordinals(n_columns: int, skip: Iterable[int] = ()) -> str:
    """``ORDER BY 1, 2, …``: what ``ORDER BY ALL`` says, on an engine without it."""
    left_out = set(skip)
    ordinals = [str(i + 1) for i in range(n_columns) if i not in left_out]
    return "ORDER BY " + ", ".join(ordinals) if ordinals else ""


class Snowflake(Dialect):
    """Snowflake's spellings of the same five things.

    ``FILTER (WHERE …)`` does not exist there: ``count_if`` covers the count,
    and a ``CASE`` inside the aggregate covers a sum or a max, which is what
    ``FILTER`` is sugar for. ``PERCENTILE_CONT`` is an ordered-set aggregate,
    one value per call, so the three quartiles are three expressions and three
    aliases. ``ORDER BY ALL`` is DuckDB's; ordinals say the same thing anywhere.
    """

    name = "snowflake"

    def as_float(self, expr: str) -> str:
        """A ``DOUBLE``, because Snowflake keeps a ``NUMBER``'s moments in fixed point.

        ``stddev_samp`` over a ``NUMBER`` sums squares as a 38-digit integer at
        the column's scale, so a revenue column at scale 10 over a few hundred
        million rows passes 10^38 and the **whole statement** fails with
        *Number out of representable range: type FIXED[SB16](38,0)*. Every
        column of the table rides in that one statement, so one wide ``NUMBER``
        cost the table its profile (2026-09-21, the first real account with
        real volumes; the demo account's numbers never got near it). ``avg``
        sums the same way and is cast for the same reason. The mean and the
        deviation are rounded for the catalog anyway; ``min``, ``max`` and the
        quartiles pick or interpolate values, sum nothing, and stay exact.
        """
        return f"CAST({expr} AS DOUBLE)"

    def count_where(self, condition: str) -> str:
        return f"count_if({condition})"

    def sum_where(self, expr: str, condition: str) -> str:
        return f"sum(CASE WHEN {condition} THEN {expr} END)"

    def max_where(self, expr: str, condition: str) -> str:
        return f"max(CASE WHEN {condition} THEN {expr} END)"

    def quartile_exprs(self, column: str) -> dict[str, str]:
        return {
            alias: f"percentile_cont({p}) WITHIN GROUP (ORDER BY {column})"
            for alias, p in (("q25", 0.25), ("median", 0.5), ("q75", 0.75))
        }

    def read_quartiles(self, stats: dict, prefix: str) -> tuple[Any, Any, Any]:
        return stats[f"{prefix}_q25"], stats[f"{prefix}_median"], stats[f"{prefix}_q75"]

    def order_by_all(self, n_columns: int, skip: Iterable[int] = ()) -> str:
        return _by_ordinals(n_columns, skip)

    def fold(self, identifier: str) -> str:
        """Upper: ``stg_chicago`` created quoted lower would need quoting forever."""
        return identifier.upper()


class BigQuery(Dialect):
    """GoogleSQL's spellings (`docs/CONNECTORS.md` §8).

    **Backticks, not double quotes**: a double-quoted token is a string
    literal here, so ``count("id")`` counts rows and ``FROM "p"."d"."t"`` is
    a syntax error. This is the one spelling that could not be a special case
    anywhere, and is why `quote` is a dialect method at all. ``COUNTIF`` is the
    filtered count, a ``CASE`` inside the aggregate covers a sum or a max, and
    the type names are ``STRING`` and ``FLOAT64`` with ``SAFE_CAST`` as the
    cast that yields NULL. Dataset and table names are case-sensitive and never
    folded, so `fold` is the identity; column names are matched without case,
    which `resolve_columns` already covers.

    **The quartiles are approximate**, and the profile says so. GoogleSQL has
    no exact percentile *aggregate*: ``PERCENTILE_CONT`` is an analytic
    function, one value per call, and cannot sit beside ``count(*)`` in one
    scan. ``APPROX_QUANTILES(x, 1000)`` is one list-valued aggregate, the shape
    DuckDB's answer already has, read back at the 250th, 500th and 750th
    boundaries. An approximate number reported as a fact is `PLAN.md`'s fourth
    non-negotiable broken quietly, so `approximate_quartiles` is set and the
    profiler writes ``approximate`` on the column naming which keys are.
    """

    name = "bigquery"
    text = "STRING"
    double = "FLOAT64"
    approximate_quartiles = True

    def quote(self, identifier: str) -> str:
        return "`" + str(identifier).replace("\\", "\\\\").replace("`", "\\`") + "`"

    def try_cast(self, expr: str, type_name: str) -> str:
        return f"SAFE_CAST({expr} AS {type_name})"

    def count_where(self, condition: str) -> str:
        return f"COUNTIF({condition})"

    def sum_where(self, expr: str, condition: str) -> str:
        return f"sum(CASE WHEN {condition} THEN {expr} END)"

    def max_where(self, expr: str, condition: str) -> str:
        return f"max(CASE WHEN {condition} THEN {expr} END)"

    def quartile_exprs(self, column: str) -> dict[str, str]:
        return {"quartiles": f"APPROX_QUANTILES({column}, {QUANTILE_BUCKETS})"}

    def read_quartiles(self, stats: dict, prefix: str) -> tuple[Any, Any, Any]:
        boundaries = stats[f"{prefix}_quartiles"]
        if not boundaries:
            return None, None, None
        step = QUANTILE_BUCKETS // 4
        return boundaries[step], boundaries[2 * step], boundaries[3 * step]

    def order_by_all(self, n_columns: int, skip: Iterable[int] = ()) -> str:
        return _by_ordinals(n_columns, skip)


class Postgres(Dialect):
    """PostgreSQL's spellings (`docs/CONNECTORS.md` §9). Closest to DuckDB of the three.

    Double quotes, ``FILTER (WHERE …)`` and exact quartiles all hold. What
    differs:

    - **An unquoted identifier folds to lower case**, the opposite of Snowflake.
    - **Nothing casts to text by itself**, so `as_text` spells the cast.
    - **There is no ``try_cast``.** Version 16 added ``pg_input_is_valid``, which
      asks the type's own parser and is exact. `PostgresBefore16` is the
      spelling for an older server.
    - **The quartiles are one ordered-set aggregate over an array of
      fractions**, so one sort and not three, read back as a list the way
      DuckDB's are.
    - **``CREATE OR REPLACE TABLE`` does not exist**, and ``CREATE SCHEMA`` takes
      a bare name because a session reaches one database. `write_table` drops
      and creates in **one** string: the driver sends it as one simple query,
      which the server runs as one transaction, so a build that fails leaves the
      old table. No ``CASCADE``: a view somebody built on the table makes the
      drop fail in the server's own words, which is the right outcome.
    """

    name = "postgres"
    text = "text"
    double = "double precision"
    #: ``json``, ``xml`` and the geometric types have no ordering operator.
    orders_every_type = False

    def as_text(self, expr: str) -> str:
        return f"CAST({expr} AS {self.text})"

    def try_cast(self, expr: str, type_name: str) -> str:
        text = self.as_text(expr)
        return (
            f"CASE WHEN pg_input_is_valid({text}, '{type_name}') "
            f"THEN CAST({text} AS {type_name}) END"
        )

    def quartile_exprs(self, column: str) -> dict[str, str]:
        return {
            "quartiles": f"percentile_cont(ARRAY[0.25, 0.5, 0.75]) WITHIN GROUP (ORDER BY {column})"
        }

    def read_quartiles(self, stats: dict, prefix: str) -> tuple[Any, Any, Any]:
        values = stats[f"{prefix}_quartiles"]
        if not values:
            return None, None, None
        q25, median, q75 = values
        return q25, median, q75

    def order_by_all(self, n_columns: int, skip: Iterable[int] = ()) -> str:
        return _by_ordinals(n_columns, skip)

    def fold(self, identifier: str) -> str:
        """Lower: ``STG_ORDERS`` created quoted upper would need quoting forever."""
        return identifier.lower()

    def write_table(self, home: list[str], name: str, query: str) -> list[str]:
        schema = self.quote(home[-1])
        table = ".".join(self.quote(p) for p in [*home, name])
        return [
            f"CREATE SCHEMA IF NOT EXISTS {schema}",
            f"DROP TABLE IF EXISTS {table}; CREATE TABLE {table} AS {query}",
        ]


class PostgresBefore16(Postgres):
    """A server older than 16, which has no ``pg_input_is_valid``.

    The cast is guarded by a pattern instead, and the pattern is **narrower than
    the parser on purpose**: a string it admits and the cast then refuses would
    fail the whole profile, so it admits plain decimals with a short exponent
    and nothing else. ``NaN`` and ``Infinity`` parse on 16 and are not counted
    here. Only ``double precision`` is asked for anywhere in portia
    (`checks.profiling`, `ops.normalize`); another type is refused rather than
    guessed at.
    """

    def try_cast(self, expr: str, type_name: str) -> str:
        if type_name != self.double:
            raise ValueError(f"no guarded cast to {type_name!r} before PostgreSQL 16")
        text = self.as_text(expr)
        return f"CASE WHEN {text} ~ '{_PLAIN_NUMBER}' THEN CAST({text} AS {type_name}) END"


#: What `PostgresBefore16.try_cast` lets through to the cast. The digit caps keep
#: an admitted string inside ``double precision``'s range, where the cast cannot
#: raise. No backslash in it, so it reads the same whatever
#: ``standard_conforming_strings`` is set to.
_PLAIN_NUMBER = (
    "^[[:space:]]*[+-]?([0-9]{1,200}([.][0-9]*)?|[.][0-9]+)([eE][+-]?[0-9]{1,2})?[[:space:]]*$"
)

#: How many buckets `BigQuery.quartile_exprs` asks ``APPROX_QUANTILES`` for.
#: A thousand puts each quartile within a tenth of a percentile of the exact
#: one on a sketch that costs one pass; four would be exact at the boundaries
#: only by coincidence.
QUANTILE_BUCKETS = 1000

DUCKDB = Dialect()
SNOWFLAKE = Snowflake()
BIGQUERY = BigQuery()
POSTGRES = Postgres()
POSTGRES_BEFORE_16 = PostgresBefore16()

#: Every dialect there is, by name — what `core/backend.py` and the prompt
#: composer look one up by.
BY_NAME: dict[str, Dialect] = {d.name: d for d in (DUCKDB, SNOWFLAKE, BIGQUERY, POSTGRES)}


def of(con: Any) -> Dialect:
    """The dialect a connection speaks. Silence means DuckDB."""
    return getattr(con, "dialect", DUCKDB)


def resolve_columns(names: Iterable[str], columns: Iterable[str]) -> list[str]:
    """Each name spelled the way the table spells it, when only the case differs.

    **Every engine portia runs on folds an unquoted identifier** — DuckDB
    compares it case-insensitively, Snowflake upper-cases it before looking it
    up — so ``SELECT license AS establishment_id`` comes back from a warehouse
    as ``ESTABLISHMENT_ID``, and the agent's ``grain: [establishment_id]`` names
    that column by any reading except a byte comparison. Both real Snowflake
    sessions on 2026-09-06 lost recorded steps to exactly that: the grain check
    said the column was missing, the profile refused the same name, and the
    copilot concluded its columns were "being dropped somewhere" and rewrote a
    correct step four times (`CONNECTOR.md` §2.8.2). Nothing local could see it,
    because every fixture in this repo is lower case.

    The rule: an exact match wins; otherwise a name that matches **exactly one**
    column case-insensitively is that column; otherwise it is returned as it
    came, and the caller reports it missing the way it always did. Two columns
    that differ only by case are both quoted, and a name that could mean either
    is not resolved — the exact spelling is the only honest answer there.
    """
    exact = set(columns)
    folded: dict[str, list[str]] = {}
    for column in exact:
        folded.setdefault(column.lower(), []).append(column)
    out: list[str] = []
    for name in names:
        if name in exact:
            out.append(name)
            continue
        candidates = folded.get(str(name).lower(), [])
        out.append(candidates[0] if len(candidates) == 1 else name)
    return out

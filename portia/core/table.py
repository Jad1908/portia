"""A named, lazily-evaluated relation — the currency the checks layer will speak.

Today the currency is `pd.DataFrame`, which means every check needs several times
a file's size in memory to look at it (`docs/DUCKDB_MIGRATION.md` §1). A `Table`
is a **handle, not data**: it holds a name, a `SELECT` that produces it, and the
connection to run that on. Nothing is materialised until something asks.

**Why a wrapper and not a raw `DuckDBPyRelation`.** Two reasons, both structural.
The checks should not each learn DuckDB's API — they should ask a table for a
count or an aggregate — and this is where the pandas → DuckDB → Snowflake seam
actually lives (`TECH_STACK.md`). Swapping the backend means rewriting this file,
not thirty call sites.

**The rule that keeps the migration honest: `head()` is the only way out.**
Everything else returns a number, a schema, or another `Table`. If a check or op
calls `.df()` on a whole relation, the memory ceiling is back and the migration
has failed at that line — and a reviewer can grep for it.

**Why the query is text rather than a bound relation.** A relation belongs to the
connection that made it, and DuckDB connections are not thread-safe; the app runs
blocking work through `asyncio.to_thread`, so a worker needs its own handle via
`con.cursor()` (§4). Holding SQL means :meth:`using` can rebind the same table to
a thread's own cursor for free. `relation` is still there for anyone who wants it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

#: How many rows :meth:`Table.head` returns when nobody says. Deliberately not
#: `present.PREVIEW_ROWS` — that number is a UI decision about what fits on a
#: screen, and this one is about not accidentally pulling a table into memory.
DEFAULT_HEAD = 10


def quote_ident(name: str) -> str:
    """A SQL identifier, quoted. A column really can be called ``order``."""
    return '"' + str(name).replace('"', '""') + '"'


def quote_literal(value: str) -> str:
    """A SQL string literal. Used for paths, which really do contain apostrophes."""
    return "'" + str(value).replace("'", "''") + "'"


@dataclass(frozen=True)
class Table:
    """A named relation that has not been computed yet."""

    name: str
    #: A ``SELECT`` producing this table. Composable: deriving one wraps this.
    query: str
    #: The DuckDB connection this table is read on. Not part of the identity of
    #: the table — :meth:`using` moves the same table to another handle.
    con: Any = field(repr=False, compare=False)

    # --- schema -------------------------------------------------------------

    @property
    def relation(self):
        """The underlying `DuckDBPyRelation`. Still lazy; still nothing computed."""
        return self.con.sql(self.query)

    @property
    def columns(self) -> list[str]:
        return [str(c) for c in self.relation.columns]

    @property
    def dtypes(self) -> dict[str, str]:
        """Column name -> DuckDB type name (``BIGINT``, ``VARCHAR``, …)."""
        rel = self.relation
        return {str(c): str(t) for c, t in zip(rel.columns, rel.types, strict=True)}

    # --- measurement --------------------------------------------------------

    def count(self) -> int:
        """``SELECT count(*)``. One number, whatever the table's size."""
        return int(self.scalar("count(*)"))

    def scalar(self, expr: str) -> Any:
        """One aggregate over the whole table, as one value."""
        return self._fetchone(f"SELECT {expr} FROM ({self.query})")[0]

    def row(self, exprs: dict[str, str]) -> dict:
        """Many aggregates in **one pass**, as ``{alias: value}``.

        The reason a full profile costs megabytes instead of gigabytes: every
        statistic a column needs is one expression in a single scan, rather than
        a series of passes over a materialised frame.
        """
        if not exprs:
            return {}
        select = ", ".join(f"{e} AS {quote_ident(alias)}" for alias, e in exprs.items())
        values = self._fetchone(f"SELECT {select} FROM ({self.query})")
        return dict(zip(exprs, values, strict=True))

    def _fetchone(self, sql: str) -> tuple:
        result = self.con.execute(sql).fetchone()
        if result is None:  # aggregates always return a row; a bare SELECT need not
            raise ValueError(f"query returned no rows: {sql}")
        return result

    # --- deriving -----------------------------------------------------------

    def sql(self, select: str, *, name: str | None = None) -> Table:
        """A new `Table` from a ``SELECT`` that reads this one via :attr:`ref`.

        ``t.sql(f"SELECT city, count(*) AS n FROM {t.ref} GROUP BY city")``.
        Still lazy — the result is another handle, not rows.
        """
        return Table(name=name or self.name, query=select, con=self.con)

    @property
    def ref(self) -> str:
        """This table, as something you can write after ``FROM``.

        An aliased subquery rather than a bare name, which is what makes
        :meth:`sql` nest without special cases. A CTE would read better but
        cannot be used here: ``WITH "otb" AS (SELECT * FROM "otb")`` is a CTE
        referring to itself, and DuckDB rejects it as an unmarked recursive
        query. Qualifying the inner name by schema would fix that and would then
        break on temp and registered objects, which live in another one.

        The alias means column-qualified references (``otb.hotel_id``) still
        work, and the subquery costs nothing — DuckDB flattens it.
        """
        return f"({self.query}) AS {quote_ident(self.name)}"

    def using(self, con: Any) -> Table:
        """The same table on another connection — a thread's own ``con.cursor()``.

        DuckDB connections are not thread-safe. Rebinding is free because a table
        is a query, not a result.
        """
        return Table(name=self.name, query=self.query, con=con)

    # --- the edges ----------------------------------------------------------

    def head(self, n: int = DEFAULT_HEAD) -> pd.DataFrame:
        """The first ``n`` rows, as pandas. **The only way out of the database.**

        Everything a human or a renderer sees is capped, so this is capped too.
        A check that reaches for the whole table has stopped scaling, and this is
        the one line where that would be visible.
        """
        return self.con.execute(f"SELECT * FROM ({self.query}) LIMIT {int(n)}").fetch_df()

    def to_csv(self, path: str | Path) -> None:
        """Write the table out. ``COPY … TO``, so it never passes through memory."""
        target = quote_literal(str(Path(path)))
        self.con.execute(f"COPY ({self.query}) TO {target} (HEADER, DELIMITER ',')")

    # --- making one ---------------------------------------------------------

    @classmethod
    def from_name(cls, name: str, con: Any) -> Table:
        """A table that already exists in the database (a view or a stored table)."""
        return cls(name=name, query=f"SELECT * FROM {quote_ident(name)}", con=con)

    @classmethod
    def from_frame(cls, frame: pd.DataFrame, name: str, con: Any) -> Table:
        """Bridge a pandas frame into the database, under ``name``.

        For fixtures and tests, which stay pandas on purpose (§9): they are tiny,
        and they are the readable definition of the test data.

        The frame is **copied into a real table**, not registered as a view over
        the python object. A registered object belongs to the connection instance
        that registered it and is invisible to a ``con.cursor()``, so a view would
        make :meth:`using` — and therefore every threaded read — fail with a
        missing-table error. Copying also means a fixture behaves exactly like an
        ingested source, which is the point of testing against one.
        """
        staging = f"__frame_{name}"
        con.register(staging, frame)
        try:
            con.execute(
                f"CREATE OR REPLACE TABLE {quote_ident(name)} AS "
                f"SELECT * FROM {quote_ident(staging)}"
            )
        finally:
            con.unregister(staging)
        return cls.from_name(name, con)

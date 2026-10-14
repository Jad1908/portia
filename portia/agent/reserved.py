"""Tables portia answers for itself in a query's inputs: its own records, read with SQL.

**Facts as tables** *(2026-10-13, `sandbox/limits/REVAMP_SPEC.md`, rule 5)*. Every
fact the catalog holds about a column used to reach the agent only through
`describe_source` and `profile_source`, one table at a time and in whatever
shape those answers took; "which columns of any table are mostly null and
numeric", across eighteen tables, was eighteen calls and the agent's own
arithmetic. The same facts as two tables, ``portia_columns`` and
``portia_tables``, answer it with one ``SELECT`` through the door every
question already goes through (`handlers.query_data`, `plot_data`), and the
answer is measured by the engine like any other.

**Built from the catalog, never from the data.** Nothing here scans a source:
each table is the entries portia already wrote, put into rows. The window's
pane and these tables say the same thing because they read the same files.

**One registry, for every name portia answers for itself** (:func:`register`).
The two catalog tables are its first entries; a chat's kept query results
(``portia_result_kxbt`` …, `portia/results.py`) are the next, registered by
`agent/handlers.py` as a pattern through the same call. A name
the registry claims is built on a local DuckDB connection of its own
(:func:`connection`) and handed to the SQL sandbox as one more declared input,
so `ops/sql.py` copies it in exactly as it copies a source, behind the same
walls, and nothing about the sandbox changes. **A reserved name wins** over a
source or model of the same name, in a query's inputs: a project that indexes
a file called ``portia_columns.csv`` reaches it under no other name, which is
a cost accepted for a prefix nobody's data uses.

**Rows cross into DuckDB as one JSON parameter**, read by ``from_json`` into
typed columns (:func:`_create`): no DataFrame (`tests/test_table.py`'s rule),
no statement text growing with the catalog, and 30,000 rows in a third of a
second, where ``executemany`` took five.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import Any

from portia import catalog, spec
from portia.agent import freshness
from portia.core import cancel
from portia.core.table import Table, quote_ident, quote_literal


class Request:
    """What a reserved table is built for: this project, on this connection.

    ``con`` is the local DuckDB connection the table is created on
    (:func:`connection`). The catalog is read once per request, however many
    of its tables one query names, and only if one of them asks.
    """

    def __init__(self, portia_dir: str | Path, con: Any) -> None:
        self.portia_dir = str(portia_dir)
        self.con = con

    @cached_property
    def loaded(self) -> dict:
        """The whole catalog, read once: every source entry is parsed here."""
        return catalog.load_catalog(self.portia_dir)

    @property
    def sources(self) -> dict[str, dict]:
        """Every indexed source's entry, by name."""
        return dict(self.loaded["sources"])

    @property
    def groups(self) -> list[dict]:
        """The project's groups, as `project.yaml` holds them."""
        return list(self.loaded["groups"] or [])

    @cached_property
    def models(self) -> dict[str, dict]:
        """Every built model's entry, by name."""
        return catalog.load_models(self.portia_dir)

    @cached_property
    def stale(self) -> dict[str, bool | None]:
        """Whether each table changed since it was indexed, asking no warehouse."""
        return freshness.statuses(self.portia_dir, self.sources, self.models)


#: How a reserved table is made: ``(the name as the query typed it, request) -> Table``.
Builder = Callable[[str, Request], Table]


@dataclass(frozen=True)
class Reserved:
    """One name, or one pattern of names, that portia answers for itself."""

    #: An exact name, matched without case as an unquoted identifier is, or a
    #: compiled pattern, which must match the whole name.
    name: str | re.Pattern[str]
    build: Builder

    def claims(self, name: str) -> bool:
        if isinstance(self.name, str):
            return name.lower() == self.name.lower()
        return self.name.fullmatch(name) is not None


_registry: list[Reserved] = []


def register(name: str | re.Pattern[str], build: Builder) -> Reserved:
    """Answer ``name`` in a query's inputs with the table ``build`` makes.

    ``build(name, request)`` receives the name as the query typed it and a
    :class:`Request`, and returns a `Table` on ``request.con``; :func:`create`
    makes one from rows. A table read from a file portia wrote is on a local
    connection of its own instead, since ``request.con`` has no file access:
    the sandbox copies an input in from whatever connection holds it.
    Registered once, at import of the module that owns the table. A second
    registration of the same name replaces the first, so a module reloaded in
    a session does not answer twice.
    """
    entry = Reserved(name, build)
    _registry[:] = [r for r in _registry if r.name != name]
    _registry.append(entry)
    return entry


def find(name: str) -> Reserved | None:
    """The registration that answers ``name``, or ``None``: first registered wins."""
    return next((r for r in _registry if r.claims(name)), None)


def connection() -> Any:
    """A local DuckDB connection for reserved tables: in memory, no file access, watched.

    Its own, whatever the project's engine is: a reserved table is portia's
    records, which are on this machine, and on a warehouse project the
    connection `core/io.connect` hands out is the warehouse's.
    """
    import duckdb

    con = duckdb.connect(":memory:", config={"enable_external_access": False})
    cancel.watch(con)
    return con


def tables(names: Sequence[str], portia_dir: str | Path, con: Any) -> dict[str, Table]:
    """Each reserved name among ``names`` built on ``con``, under the name as it was typed."""
    request = Request(portia_dir, con)
    out = {}
    for name in names:
        reserved = find(name)
        if reserved is not None:
            out[name] = reserved.build(name, request)
    return out


def create(con: Any, name: str, fields: Sequence[tuple[str, str]], rows: list[dict]) -> Table:
    """A table on ``con`` named ``name``, with typed ``fields``, holding ``rows``.

    ``fields`` is ``(column, DuckDB type)`` in order; each row is a mapping from
    column to a JSON-able value, a missing key being ``NULL``. The rows travel
    as one JSON parameter (see the module docstring), and a value that does not
    fit its column's type is an error rather than a guess.
    """
    structure = quote_literal(json.dumps([dict(fields)]))
    con.execute(
        f"CREATE OR REPLACE TABLE {quote_ident(name)} AS "
        f"SELECT unnest(from_json(?::JSON, {structure}), recursive := true)",
        [json.dumps(rows, allow_nan=False)],
    )
    return Table.from_name(name, con)


# --- portia_columns and portia_tables -----------------------------------------------

#: One row per column of every source and model. ``table_name`` and not ``table``,
#: which DuckDB will not read unquoted: ``WHERE table = 'x'`` is a syntax error,
#: and it is the first query anyone writes against this.
COLUMNS = "portia_columns"
COLUMN_FIELDS: tuple[tuple[str, str], ...] = (
    ("table_name", "VARCHAR"),
    ("kind", "VARCHAR"),
    ("position", "INTEGER"),
    ("name", "VARCHAR"),
    ("type", "VARCHAR"),
    ("role", "VARCHAR"),
    ("flags", "VARCHAR[]"),
    ("null_rate", "DOUBLE"),
    ("n_distinct", "BIGINT"),
    ("min", "DOUBLE"),
    ("q25", "DOUBLE"),
    ("median", "DOUBLE"),
    ("q75", "DOUBLE"),
    ("max", "DOUBLE"),
    ("mean", "DOUBLE"),
    ("std", "DOUBLE"),
    ("top", "VARCHAR"),
    ("top_freq", "BIGINT"),
    ("stale", "BOOLEAN"),
)

#: One row per source and model.
TABLES = "portia_tables"
TABLE_FIELDS: tuple[tuple[str, str], ...] = (
    ("name", "VARCHAR"),
    ("kind", "VARCHAR"),
    ("path", "VARCHAR"),
    ("n_rows", "BIGINT"),
    ("n_columns", "INTEGER"),
    ("read", "BOOLEAN"),
    ("summary", "VARCHAR"),
    # Every note, oldest first: a card carries the latest few (`handlers.CARD_NOTES`).
    ("notes", 'STRUCT("at" VARCHAR, "text" VARCHAR)[]'),
    ("groups", "VARCHAR[]"),
    ("stale", "BOOLEAN"),
    ("indexed_at", "VARCHAR"),
)

#: What ``kind`` says a table is.
SOURCE, MODEL = "source", "model"


def vocabulary() -> dict[str, str]:
    """Each catalog table's columns, as a tool description lists them.

    Filled into `prompts/tools/query_data.md` from here, as `handlers.step_vocabulary`
    fills `record_step.md`, so the prose cannot drift from the tables.
    """
    return {
        COLUMNS: ", ".join(name for name, _ in COLUMN_FIELDS),
        TABLES: ", ".join(name for name, _ in TABLE_FIELDS),
    }


def _number(value: Any) -> float | None:
    """A measured number as the column holds it; anything else (a hand edit) is ``NULL``."""
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    return None


def _count(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _text(value: Any) -> str | None:
    return None if value is None else str(value)


def _entries(request: Request) -> list[tuple[str, str, dict]]:
    """``(name, kind, entry)`` for every source, then every model, each by name."""
    return [
        *((name, SOURCE, request.sources[name]) for name in sorted(request.sources)),
        *((name, MODEL, request.models[name]) for name in sorted(request.models)),
    ]


def _column_rows(request: Request) -> list[dict]:
    rows = []
    for table, kind, entry in _entries(request):
        stale = request.stale.get(table)
        for position, column in enumerate(entry.get("columns") or [], start=1):
            measured = "inferred" in column
            rows.append(
                {
                    "table_name": table,
                    "kind": kind,
                    "position": position,
                    "name": _text(column.get("name")),
                    "type": _text(column.get("inferred") or column.get("dtype")),
                    "role": _text(column.get("role")),
                    # Absent on a column nobody measured, which is not the same
                    # as measured and unflagged.
                    "flags": [str(f) for f in column.get("flags") or []] if measured else None,
                    "null_rate": _number(column.get("null_rate")),
                    "n_distinct": _count(column.get("n_distinct")),
                    "min": _number(column.get("min")),
                    "q25": _number(column.get("q25")),
                    "median": _number(column.get("median")),
                    "q75": _number(column.get("q75")),
                    "max": _number(column.get("max")),
                    "mean": _number(column.get("mean")),
                    "std": _number(column.get("std")),
                    "top": _text(column.get("top")),
                    "top_freq": _count(column.get("top_freq")),
                    "stale": stale,
                }
            )
    return rows


def _table_rows(request: Request) -> list[dict]:
    root = catalog.project_root(request.portia_dir)
    specs = spec.discover_specs(root) if request.models else {}
    groups: dict[str, list[str]] = {}
    for group in request.groups:
        for member in group.get("sources") or []:
            groups.setdefault(str(member), []).append(str(group.get("name")))
    rows = []
    for name, kind, entry in _entries(request):
        written = catalog.written_summary(entry)
        if kind == SOURCE:
            path = _text(entry.get("source"))
        else:
            found = specs.get(name)
            path = found.as_posix() if found is not None else None
        rows.append(
            {
                "name": name,
                "kind": kind,
                "path": path,
                "n_rows": catalog.row_count(entry),
                "n_columns": len(entry.get("columns") or []),
                "read": written is not None,
                "summary": written,
                "notes": [
                    {"at": _text(note.get("at")), "text": _text(note.get("text"))}
                    for note in entry.get(catalog.NOTES) or []
                ],
                "groups": groups.get(name, []),
                "stale": request.stale.get(name),
                "indexed_at": _text(
                    (entry.get("indexed") or {}).get("at") or (entry.get("built") or {}).get("at")
                ),
            }
        )
    return rows


register(
    COLUMNS, lambda name, request: create(request.con, name, COLUMN_FIELDS, _column_rows(request))
)
register(
    TABLES, lambda name, request: create(request.con, name, TABLE_FIELDS, _table_rows(request))
)

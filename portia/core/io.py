"""Canonical data loading — THE one way to get a table from a path.

Every tool and check loads data through here. Nothing else calls ``pd.read_csv``
or names a DuckDB reader directly. New formats are registered in ``_FORMATS``,
**once**, so support grows in a single place instead of a dozen ad-hoc readers
drifting apart (different NA handling, dtype coercion, encodings).

A format now declares *two* ways to be read, side by side in one entry: the
pandas loader and the DuckDB table function. That pairing is the point — it is
what stops someone adding Parquet support to one tier and not the other.

Two entry points, for two different callers:

- :func:`load_frame` — the whole file, in memory, as pandas. Correct for fixtures,
  tests, and small reads, and **wrong for anything at scale**: pandas needs ~2.4×
  a CSV's size to hold it and ~4.8× to profile it (`docs/DUCKDB_MIGRATION.md` §1).
- :func:`load_table` — a lazy :class:`~portia.core.table.Table` over the file.
  Nothing is read until something asks for a number.

:func:`load_table` reads the file **in place**, and since 2026-07-30 that is the
only way portia reads anything. A project used to ingest each source into a
private ``.portia/store.duckdb`` first, on the argument that columnar storage is
~20× faster on column-scoped reads; the store is gone (`docs/PIPELINE.md` §2.7).
Two things retired it. The hot paths never used it — ``run_spec``, every agent
check and every CLI tool went to the file anyway — and portia now sources **only
from files already inside the repo**, where a hidden second copy of the user's
data is a worse trade than a re-parse. If reads get slow, the answer is parquet
in the repo: columnar, typed, already registered below, and still one copy you
can see.

:func:`connect` is the other half of that: a table needs a connection to be read
on, and with no store to open there is one obvious kind — a fresh in-memory
database that reads the repo's files. Since `docs/CONNECTOR.md` there is a second
kind, a warehouse session, and :func:`connect` asks `core/backend.py` which one
the project is on. A spec's ``sources:`` entry can name a **table** as well as a
file (:func:`source_table`), and that is the whole of what the seam needed.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from portia.core import backend, cancel
from portia.core import dialect as dialects
from portia.core.table import Table, quote_literal

#: The text a CSV uses to mean "missing". This is **pandas' default set**, spelled
#: out here so DuckDB can be told the same thing: left alone, DuckDB nulls only
#: the empty string, so a column of ``N/A`` would read as 40 present values on one
#: tier and 39 on the other — and a null rate that depends on which reader ran is
#: exactly the quiet disagreement `core.present` exists to prevent.
#:
#: Kept as a literal rather than imported from `pandas.io.parsers.readers`, which
#: is private. `tests/test_io.py` asserts pandas still nulls precisely these and
#: nothing else, so the copy cannot drift without something going red.
NA_TOKENS = (
    "",
    "#N/A",
    "#N/A N/A",
    "#NA",
    "-1.#IND",
    "-1.#QNAN",
    "-NaN",
    "-nan",
    "1.#IND",
    "1.#QNAN",
    "<NA>",
    "N/A",
    "NA",
    "NULL",
    "NaN",
    "None",
    "n/a",
    "nan",
    "null",
)


@dataclass(frozen=True)
class Format:
    """How one file format is read and written.

    ``sql_reader`` is a DuckDB table function taking a path — ``read_csv``,
    ``read_parquet`` — and ``sql_options`` are the settings that make it agree
    with the pandas loader beside it. ``copy_options`` is what ``COPY … TO``
    needs to write the format back. Registering a format means filling in all of
    it; a format that only fills in the reader is one you can get data into and
    not out of.

    ``rescans`` says whether every query against this reader re-reads the file
    from the start. A text format has to: ``read_csv`` re-parses all of it to
    answer a question about one column. Columnar formats do not, which is why a
    per-column read is nearly free on Parquet and a full parse on CSV — see
    :func:`portia.checks.profiling.profile_path`, which is where that difference
    was costing two orders of magnitude.
    """

    read_frame: Callable[..., pd.DataFrame]
    sql_reader: str
    sql_options: dict[str, Any] = field(default_factory=dict)
    copy_options: str = ""
    rescans: bool = False


def write_table(table: Table, path: str | Path) -> Path:
    """**The one way to write a table out**, dispatching on the target's extension.

    ``COPY … TO``, so a 5 GB result is written without being held: the same
    reason nothing else in the engine materialises. Reading and writing are
    registered together in :data:`_FORMATS` — a format you can load and not save
    is a trap you find at the end of a long run.
    """
    if backend.is_remote(table.con):
        # The one write that would be a pull. On a warehouse project the model
        # tables are what Build created there (`docs/CONNECTOR.md` §2.7), and a
        # file on this laptop is exactly the second copy that mode exists to
        # not make. Refused with the reason, rather than with COPY's own error.
        raise ValueError(
            f"{table.name} lives in the warehouse and is not written to a file — "
            "Build creates the model tables there instead (docs/CONNECTOR.md §2.7)"
        )
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    table.copy_to(path, options=_format(path).copy_options)
    return path


def load_frame(path: str | Path, **kwargs: Any) -> pd.DataFrame:
    """Load a whole data file into memory as a DataFrame.

    Raises a clear error for unsupported formats rather than silently guessing.
    """
    return _format(path).read_frame(Path(path), **kwargs)


def connect() -> Any:
    """A connection to run the project's queries on — from the active backend.

    Locally that is a DuckDB connection, in-memory and empty: portia keeps no
    database of its own any more, so a connection is scratch space for one piece
    of work rather than a handle on stored data (`docs/PIPELINE.md` §2.7). It has
    DuckDB's default external access, because reading the project's files is its
    whole job — the agent's SQL hatch opens its own restricted connection and
    always did (`ops/sql.py`). On a project that names a warehouse connection it
    is a session there instead (`core/backend.py`, `docs/CONNECTOR.md`), and the
    caller cannot tell: a `Table` is the same handle either way.

    **Not thread-safe.** A thread that runs a query takes its own handle with
    ``con.cursor()`` and rebinds through :meth:`Table.using`.

    Registered with the ambient cancel scope, if a surface opened one: this is
    the one way a connection is made, so it is the one place that has to
    remember, and Stop can then reach a query nobody kept a handle on
    (`core/cancel.py`). Outside a scope it costs a `ContextVar.get`.
    """
    con = backend.active().open()
    cancel.watch(con)
    return con


def load_table(path: str | Path, con: Any, *, name: str | None = None) -> Table:
    """A lazy :class:`Table` reading ``path`` directly, without copying it.

    The one way a file becomes a table. There is no ingest step to prefer
    instead any more — see the module docstring.
    """
    path = Path(path)
    return Table(name=name or path.stem, query=read_query(path), con=con)


#: A source reference that names a **table** rather than a file:
#: ``{table: DATABASE.SCHEMA.NAME}``. What a spec's ``sources:`` block holds on a
#: warehouse project (`docs/CONNECTOR.md` §2.5), and what a catalog entry with a
#: ``remote`` block resolves to. The dict shape rather than a ``snowflake://``
#: string, because a spec is read in a diff and a key says what the value is.
TABLE_REF = "table"


def is_table_ref(ref: Any) -> bool:
    return isinstance(ref, dict) and TABLE_REF in ref


def source_table(ref: Any, con: Any, *, base: str | Path = ".", name: str | None = None) -> Table:
    """A lazy :class:`Table` for a source reference — a path, or a table.

    The one dispatch, so `spec.run_spec`, the agent's tools and the app all
    resolve a spec's ``sources:`` entry the same way. A string is a path
    relative to ``base``, read through :func:`load_table`; a
    :data:`TABLE_REF` is a table the connection can already see.
    """
    if is_table_ref(ref):
        qualified = str(ref[TABLE_REF])
        query = table_query(qualified, dialect=dialects.of(con))
        return Table(name=name or qualified.split(".")[-1], query=query, con=con)
    return load_table(Path(base) / str(ref), con, name=name)


def source_query(
    ref: Any,
    *,
    base: str | Path = ".",
    absolute: bool = True,
    dialect: dialects.Dialect = dialects.DUCKDB,
) -> str:
    """The ``SELECT`` that reads a source reference. `read_query`'s dispatching twin.

    ``absolute`` means what it means there, and only applies to a path: a table
    reference is already the name the warehouse knows it by. ``dialect`` is
    how that name is quoted, and only applies to a table: a file is read by
    DuckDB whatever the project runs on.
    """
    if is_table_ref(ref):
        return table_query(str(ref[TABLE_REF]), dialect=dialect)
    return read_query(Path(base) / str(ref) if absolute else Path(str(ref)), absolute=absolute)


def table_query(qualified: str, *, dialect: dialects.Dialect = dialects.DUCKDB) -> str:
    """``SELECT * FROM "DB"."SCHEMA"."TABLE"`` — each part quoted, the engine's way.

    Quoted, because the names come from the warehouse's own listing and are
    kept as it spelled them. An unquoted identifier folds to upper case on
    Snowflake and to lower case in `sqlglot` (`docs/SQL_LINEAGE.md` §10), and
    a name that means something different depending on who reads it is the
    exact bug that took every `DERIVES_FROM` edge off the AQN project.
    """
    return "SELECT * FROM " + ".".join(dialect.quote(part) for part in qualified.split("."))


def rescans(path: str | Path) -> bool:
    """Does every query against this file re-read it from the start?

    True for text formats, false for columnar ones. Asked by anything that runs
    *many* queries over one file and would otherwise pay for the parse each time
    (`checks.profiling.profile_path`). It lives here because it is a property of
    the reader, and the readers are registered in one place.
    """
    return _format(path).rescans


def read_query(path: str | Path, *, absolute: bool = True) -> str:
    """The ``SELECT`` that reads ``path`` in DuckDB. The one place a reader is named.

    ``absolute=False`` leaves the path as given, for SQL that will be **written to
    a file** rather than executed here: a compiled pipeline (`portia/pipeline.py`)
    is run from the repo root and has to work on a machine other than this one, so
    an absolute path would pin it to one laptop. The reader and its options are
    identical either way — which is the point of asking here rather than spelling
    a ``read_csv`` somewhere else. A generated file that disagreed with the engine
    about which tokens mean null would be the exact class of bug `core/present.py`
    exists to prevent.
    """
    path = Path(path)
    fmt = _format(path)
    args = [quote_literal(str(path.resolve() if absolute else path))]
    args += [f"{key}={_sql_value(value)}" for key, value in fmt.sql_options.items()]
    return f"SELECT * FROM {fmt.sql_reader}({', '.join(args)})"


def _sql_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_sql_value(v) for v in value) + "]"
    if isinstance(value, str):
        return quote_literal(value)
    return str(value)


def supported_suffixes() -> tuple[str, ...]:
    """Extensions this module can read — useful for CLIs and file panels."""
    return tuple(sorted(_FORMATS))


def find_data_files(target: str | Path) -> list[Path]:
    """Every supported data file at ``target`` — a file, a directory, or a glob.

    Lives here rather than in a CLI because both human edges need it: the
    ``index`` command resolves what to index, and the app's "add by path" field
    resolves the same thing. What counts as a data file is decided by
    :data:`_FORMATS`, so the answer stays one fact rather than two lists that
    drift apart.
    """
    path = Path(target)
    if path.is_file():
        return [path]

    suffixes = supported_suffixes()
    if path.is_dir():
        found = sorted(p for p in path.iterdir() if p.suffix.lower() in suffixes)
    else:
        found = sorted(p for p in _glob(path) if p.suffix.lower() in suffixes)

    if not found:
        raise ValueError(f"no supported data files at {str(target)!r} ({', '.join(suffixes)})")
    return found


def _format(path: str | Path) -> Format:
    suffix = Path(path).suffix.lower()
    fmt = _FORMATS.get(suffix)
    if fmt is None:
        supported = ", ".join(sorted(_FORMATS))
        raise ValueError(
            f"unsupported data format {suffix!r} for {Path(path).name} (supported: {supported})"
        )
    return fmt


def _glob(pattern: Path):
    """Match a glob, absolute or relative.

    ``Path().glob("/data/*.csv")`` raises on an absolute pattern, so an absolute
    one is anchored at the root and matched from there. Someone adding files by
    path types the path they have, and it is usually the absolute one.
    """
    if pattern.is_absolute():
        root = Path(pattern.anchor)
        return root.glob(str(pattern.relative_to(root)))
    return Path().glob(str(pattern))


def _load_csv(path: Path, **kwargs: Any) -> pd.DataFrame:
    # Let pandas infer dtypes: "numeric stored as text" must remain a *reportable*
    # signal, not something we normalize away at the door. DuckDB's sniffer is
    # left to do the same on its side, for the same reason.
    return pd.read_csv(path, **kwargs)


def _load_parquet(path: Path, **kwargs: Any) -> pd.DataFrame:
    """Read a Parquet file into pandas — **through DuckDB**, not pyarrow.

    ``pd.read_parquet`` needs pyarrow, which is a large dependency to add for a
    function the engine no longer calls: `load_frame` is now only the small-read
    convenience, and everything that matters goes through `load_table`. DuckDB
    reads Parquet natively and is already a core dependency, so this costs
    nothing and keeps both halves of the format honest.
    """
    import duckdb

    con = duckdb.connect(":memory:")
    try:
        return con.execute(read_query(path)).fetch_df()
    finally:
        con.close()


# Register new formats here, once — reader, options, and how to write it back.
_FORMATS: dict[str, Format] = {
    ".csv": Format(
        read_frame=_load_csv,
        sql_reader="read_csv",
        # `read_csv` still sniffs types with options set — this only tells it what
        # "missing" looks like, so both tiers agree on a null rate.
        sql_options={"nullstr": list(NA_TOKENS)},
        copy_options="HEADER, DELIMITER ','",
        # Text: answering a question about one column means parsing every column
        # of every row again.
        rescans=True,
    ),
    # Parquet needs no null tokens and no sniffing: it carries its own schema.
    # That is most of why it is worth converting to — the CSV reader's guesses
    # stop being part of the answer, and a column that was text stays text.
    #
    # ZSTD rather than DuckDB's default SNAPPY. Measured on an 867 MB extract:
    # SNAPPY 411 MB (2.1x), ZSTD 266 MB (3.3x), for one extra second. Both are
    # read transparently, so the only thing the choice costs is that second.
    ".parquet": Format(
        read_frame=_load_parquet,
        sql_reader="read_parquet",
        copy_options="FORMAT PARQUET, COMPRESSION ZSTD",
    ),
}

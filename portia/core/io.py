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
database that reads the repo's files.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

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
    """

    read_frame: Callable[..., pd.DataFrame]
    sql_reader: str
    sql_options: dict[str, Any] = field(default_factory=dict)
    copy_options: str = ""


def write_table(table: Table, path: str | Path) -> Path:
    """**The one way to write a table out**, dispatching on the target's extension.

    ``COPY … TO``, so a 5 GB result is written without being held: the same
    reason nothing else in the engine materialises. Reading and writing are
    registered together in :data:`_FORMATS` — a format you can load and not save
    is a trap you find at the end of a long run.
    """
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
    """A DuckDB connection to read the repo's files on.

    In-memory and empty: portia keeps no database of its own any more, so a
    connection is scratch space for one piece of work rather than a handle on
    stored data (`docs/PIPELINE.md` §2.7). It has DuckDB's default external
    access, because reading the project's files is its whole job — the agent's
    SQL hatch opens its own restricted connection and always did (`ops/sql.py`).

    **Not thread-safe.** A thread that runs a query takes its own handle with
    ``con.cursor()`` and rebinds through :meth:`Table.using`.
    """
    import duckdb

    return duckdb.connect(":memory:")


def load_table(path: str | Path, con: Any, *, name: str | None = None) -> Table:
    """A lazy :class:`Table` reading ``path`` directly, without copying it.

    The one way a file becomes a table. There is no ingest step to prefer
    instead any more — see the module docstring.
    """
    path = Path(path)
    return Table(name=name or path.stem, query=read_query(path), con=con)


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

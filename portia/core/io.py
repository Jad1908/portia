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

**A CSV's layout is guessed once per process, and the guess is replayed**
*(2026-10-09)*. Before every statement that names a CSV, DuckDB sniffs it: the
dialect and every column's type, from a sample. On a 1,579-column file that was
7 s on one core, against 0.28 s to scan all 166 MB once the layout is known
(`docs/DUCKDB_MIGRATION.md` §17), and portia names a file many times per tool
call. So the first statement that reads a file asks DuckDB for its guess
(``sniff_csv``), and every later one passes it back with ``auto_detect=false``:
the same names, types and values, and no second guess. Only the guess is kept,
never the data, only in memory, and only while the file is the one it was made
from. A table's query still names the plain reader; the guess is put in by the
connection, as each statement is sent (:func:`connect`).
"""

from __future__ import annotations

import threading
import time
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

    ``sniffer`` is the table function that makes the reader's own guess about a
    file, for a reader that guesses: ``read_csv`` works out the dialect and every
    column's type before each statement, and ``sniff_csv`` is that same guess
    asked as a query. Set, the guess is made once per file and spelled into every
    later read of it (:func:`connect`). Parquet carries its schema and has none.
    """

    read_frame: Callable[..., pd.DataFrame]
    sql_reader: str
    sql_options: dict[str, Any] = field(default_factory=dict)
    copy_options: str = ""
    rescans: bool = False
    sniffer: str = ""


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

    **A local connection reads each CSV with DuckDB's guess about it**
    *(2026-10-09)*: every statement is sent with each CSV reader in it replaced
    by the same reader with the guess spelled out (:class:`_Replaying`). Here
    rather than in the query a `Table` holds, because a table outlives the
    moment it was built — the window keeps a run's step results for *Write
    outputs* — and a guess must be checked against the file when it is read,
    not when the query was written. A warehouse session never reads a file and
    is handed back as it was opened.
    """
    con = backend.active().open()
    if not backend.is_remote(con):
        con = _Replaying(con)
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


def relative(path: str | Path, root: str | Path) -> str:
    """``path`` relative to ``root``, **as portia writes it down: forward slashes**.

    A relative path ends up in files that are committed and opened on another
    machine (a catalog entry, a spec's ``sources:``, ``_sources.sql``), and in
    keys the window splits on ``/``. ``str(Path)`` spells it with the machine's
    own separator, so on Windows it wrote ``data\\orders.csv``, which a Mac
    reads as one file name. Every system portia runs on opens a forward-slash
    path, DuckDB on Windows included, so that is the one spelling. Raises
    `ValueError` when ``path`` is not under ``root``, as ``relative_to`` does.
    """
    return Path(path).relative_to(root).as_posix()


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
    return f"SELECT * FROM {read_relation(path, absolute=absolute)}"


def read_relation(path: str | Path, *, absolute: bool = True) -> str:
    """The reader call `read_query` selects from, ``read_parquet('…')``, to write after ``FROM``.

    For a caller asking one file many small questions (`checks.profiling`):
    through ``SELECT *`` DuckDB binds every column of the file for each one and
    prunes back to the one asked about, which on a 1,579-column file was most
    of what each question cost (2026-10-08).

    Always the plain reader, which leaves DuckDB to guess a CSV's layout. A
    reader for this machine (``absolute``) is also remembered, so that a local
    connection can recognise it in a statement and send it with the guess
    instead (:func:`connect`); a reader written to a file never carries one, and
    reads the file as it is on the day it runs.
    """
    path = Path(path)
    fmt = _format(path)
    # A path that is written to a file is spelled the portable way (`relative`);
    # one that is executed here is this machine's own.
    args = [quote_literal(str(path.resolve()) if absolute else path.as_posix())]
    args += [f"{key}={_sql_value(value)}" for key, value in fmt.sql_options.items()]
    reader = f"{fmt.sql_reader}({', '.join(args)})"
    if absolute and fmt.sniffer:
        with _LOCK:
            _READERS[reader] = (path.resolve(), fmt)
    return reader


# --- a CSV's guess: made once, replayed ------------------------------------------

#: How long a file must have been left alone before a guess about it is kept, in
#: seconds. A file system stamps a write with a clock that ticks: APFS and NTFS
#: in nanoseconds or near it, ext4 every few milliseconds, HFS+ every second, FAT
#: every two. A guess made within one tick of the last write could be followed by
#: a rewrite of the same size inside that same tick, and no stamp would move. Kept
#: only once the file is older than the coarsest of those ticks, any later write
#: lands on a later stamp. Git calls the same hole *racily clean* and closes it the
#: same way. A file younger than this is guessed on every read, as it always was.
SETTLE_SECONDS = 2.0

_NS_PER_SECOND = 1_000_000_000


@dataclass(frozen=True)
class _Guess:
    """What DuckDB guessed about one version of a file."""

    #: The file as it was when guessed (:func:`_identity`).
    identity: tuple[int, ...]
    #: The reader call that replays the guess, or ``None`` when DuckDB could not
    #: guess this version — read it the plain way, without asking again.
    reader: str | None


#: One guess per file, by resolved path. Memory only, never written anywhere: a
#: restart or a new DuckDB guesses again.
_GUESSES: dict[str, _Guess] = {}
#: One lock per file, so two threads reading a file for the first time make one
#: guess between them. Guarded by `_LOCK`, as `_GUESSES` is.
_GUESSING: dict[str, threading.Lock] = {}
#: Every plain reader :func:`read_relation` has written for this machine, and the
#: file and format it reads: what a local connection looks for in a statement.
#: One entry per file, since the text is the same every time it is written.
_READERS: dict[str, tuple[Path, Format]] = {}
_LOCK = threading.Lock()


class _Replaying:
    """A local DuckDB connection that sends each CSV reader with the file's guess.

    Every statement passes through :func:`_replayed` on its way in, so the guess
    is checked against the file as it is at that statement, whenever the query
    was written. Everything else is the DuckDB connection's own: a result, a
    relation and ``description`` come straight back from it, and ``cursor``
    gives a thread its own handle that does the same.
    """

    __slots__ = ("_con",)

    def __init__(self, con: Any) -> None:
        self._con = con

    def execute(self, query: Any, *args: Any, **kwargs: Any) -> Any:
        return self._con.execute(_replayed(query), *args, **kwargs)

    def sql(self, query: Any, *args: Any, **kwargs: Any) -> Any:
        return self._con.sql(_replayed(query), *args, **kwargs)

    def cursor(self) -> _Replaying:
        return _Replaying(self._con.cursor())

    def __enter__(self) -> _Replaying:
        return self

    def __exit__(self, *exc: Any) -> None:
        self._con.close()

    def __getattr__(self, name: str) -> Any:
        if name == "_con":  # not set yet, as when copied: never recurse looking for it
            raise AttributeError(name)
        return getattr(self._con, name)


def _replayed(query: Any) -> Any:
    """``query`` with each plain CSV reader in it carrying the file's guess, as it is now.

    A reader whose file has no guess to give — missing, unguessable, or written a
    moment ago — is left as it is, and DuckDB guesses at bind as it always did.
    A statement that names no reader costs one substring test per format.
    """
    if not isinstance(query, str) or not any(call in query for call in _READER_CALLS):
        return query
    with _LOCK:
        known = list(_READERS.items())
    for plain, (path, fmt) in known:
        if plain in query:
            guessed = _guessed(path, fmt)
            if guessed is not None:
                query = query.replace(plain, guessed)
    return query


def _identity(path: Path) -> tuple[Any, tuple[int, ...]] | None:
    """``(stat, identity)`` of ``path`` now, or ``None`` when it cannot be stat'ed.

    The identity is everything cheap one ``stat`` says about which file this is
    and whether it was written: the device and inode (another file moved into
    its place), the size and modification time (`catalog.STALENESS_FACTS`, at
    nanosecond precision), and the change time, which nothing but the kernel
    sets, so a tool that restores a modification time after writing still moves
    it.
    """
    try:
        st = path.stat()
    except OSError:
        return None
    return st, (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)


def _kept(key: str, identity: tuple[int, ...]) -> _Guess | None:
    with _LOCK:
        kept = _GUESSES.get(key)
    return kept if kept is not None and kept.identity == identity else None


def _guessed(path: Path, fmt: Format) -> str | None:
    """The reader that replays DuckDB's guess about ``path`` as it is now, or ``None``.

    **A kept guess is only ever handed back for the file it was made from.** It is
    stored with the file's identity and used only while one ``stat`` taken now
    matches it exactly; any difference, and the file is guessed again. A guess is
    made between two ``stat`` calls and thrown away when they differ, because it
    then describes neither version; and it is kept only for a file that had been
    still for `SETTLE_SECONDS` when the guess began, so no later write can share
    its stamps. ``None`` means the plain reader: DuckDB guesses at bind, as before.
    """
    key = str(path)
    seen = _identity(path)
    if seen is None:
        return None  # DuckDB says what is wrong with it, when the read runs
    kept = _kept(key, seen[1])
    if kept is not None:
        return kept.reader
    with _LOCK:
        lock = _GUESSING.setdefault(key, threading.Lock())
    with lock:
        started = time.time_ns()
        seen = _identity(path)  # again: it may have changed while another thread guessed
        if seen is None:
            return None
        stat, identity = seen
        kept = _kept(key, identity)
        if kept is not None:
            return kept.reader
        reader = _sniff(path, fmt)
        after = _identity(path)
        if after is None or after[1] != identity:
            return None
        last_written = max(stat.st_mtime_ns, stat.st_ctime_ns)
        if started - last_written > SETTLE_SECONDS * _NS_PER_SECOND:
            with _LOCK:
                _GUESSES[key] = _Guess(identity=identity, reader=reader)
        return reader


def _sniff(path: Path, fmt: Format) -> str | None:
    """Ask DuckDB once what it would guess about ``path``, as a reader call that replays it.

    The guess is asked with the format's own options (``nullstr``), because which
    strings mean missing changes what type a column looks like. ``None`` when
    DuckDB cannot guess the file, or the replay does not bind to exactly the
    names and types it guessed: the plain reader then fails, or reads, as it did.

    On its own connection, watched by the ambient cancel scope like every other:
    Stop interrupts a guess, and an interrupted guess is not a failed one, so it
    is raised rather than remembered.
    """
    import duckdb

    con = backend.LOCAL.open()
    cancel.watch(con)
    try:
        options = "".join(f", {key}={_sql_value(v)}" for key, v in fmt.sql_options.items())
        cursor = con.execute(f"SELECT * FROM {fmt.sniffer}({quote_literal(str(path))}{options})")
        names = [d[0] for d in cursor.description]
        row = cursor.fetchone()
        if row is None:
            return None
        sniffed = dict(zip(names, row, strict=True))
        reader = _replay(str(path), fmt, sniffed)
        if reader is None or not _binds_as_sniffed(con, reader, sniffed["Columns"]):
            return None
        return reader
    except duckdb.InterruptException:
        raise
    except duckdb.Error:
        return None
    finally:
        con.close()


def _replay(path: str, fmt: Format, sniffed: dict[str, Any]) -> str | None:
    """DuckDB's own replay of its guess (``Prompt``), with what it took from the file quoted.

    ``sniff_csv`` hands back the ``read_csv`` call that reads the file the way it
    guessed, every sniffed option fixed, but it writes the path and each column
    name into that call raw: a folder named ``o'brien`` or a header holding an
    apostrophe gives a call that does not parse. So the path and the names are
    put back quoted, the options DuckDB chose are kept as it wrote them, and the
    format's own options are spelled as the plain reader spells them. Anything
    not where this expects it is not replayed: ``None``, and DuckDB guesses again.
    """
    prompt = str(sniffed.get("Prompt") or "").strip()
    columns = sniffed.get("Columns") or []
    head = f"FROM {fmt.sql_reader}('{path}', "
    tail = ");"
    if not prompt.startswith(head) or not prompt.endswith(tail):
        return None
    raw = "columns={" + ", ".join(f"'{c['name']}': '{c['type']}'" for c in columns) + "}"
    options = prompt[len(head) : -len(tail)]
    if options.count(raw) != 1:
        return None
    before, after = options.split(raw)
    echoed = str(sniffed.get("UserArguments") or "")
    if echoed:
        if not after.endswith(f", {echoed}"):
            return None
        after = after[: -len(f", {echoed}")]
    quoted = ", ".join(f"{quote_literal(c['name'])}: {quote_literal(c['type'])}" for c in columns)
    own = "".join(f", {key}={_sql_value(value)}" for key, value in fmt.sql_options.items())
    return f"{fmt.sql_reader}({quote_literal(path)}, {before}columns={{{quoted}}}{after}{own})"


def _binds_as_sniffed(con: Any, reader: str, columns: list[dict[str, Any]]) -> bool:
    """Does the replay parse, and bind to exactly the names and types DuckDB guessed?

    One bind, which with the columns given reads nothing to guess: about 30 ms
    on 1,579 columns. What it protects against is this module spelling the
    replay wrong, which would otherwise surface as every later read failing.
    """
    bound = con.sql(f"SELECT * FROM {reader}")
    return list(bound.columns) == [c["name"] for c in columns] and [
        str(t) for t in bound.types
    ] == [c["type"] for c in columns]


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
        # And guessing the dialect and every type before it, which is asked once
        # and replayed (`connect`).
        sniffer="sniff_csv",
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

#: How a statement that reads a guessing format begins, for :func:`_replayed`'s
#: first look: most statements read a parsed copy or a table, and name no file.
_READER_CALLS = tuple(f"{fmt.sql_reader}(" for fmt in _FORMATS.values() if fmt.sniffer)

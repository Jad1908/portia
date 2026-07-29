"""The project's data store — where an indexed source actually lives.

Sibling of `catalog`, and the division is worth stating once: **the store holds
the data, the catalog holds what we know about it.** One is a DuckDB file, the
other is hand-editable YAML you read in a diff.

**Why a store and not views over the files** (`docs/DUCKDB_MIGRATION.md` §3).
Reading the CSVs in place copies nothing, which is appealing until you look at
what it costs. Every query re-parses the file — measured 0.21 s against 0.01 s
for the same column-scoped read once ingested, and that gap widens with width.
And fatally: the agent's SQL declares its inputs, so if an input *is* a
``read_csv`` call, the escape hatch needs file-reading rights. That contradicts
`ops/sql.py`, whose whole posture is that reading files is forbidden.

Ingesting inverts it. The data lives *inside* the database, so the agent's
connection needs no external access at all — the sandbox gets **simpler, not
weaker**, and ``read_csv`` stays on the forbidden list because nothing legitimate
needs it any more.

**The cost is a second copy on disk, and it is stated rather than hidden.** For
the CSVs measured, the store was 5.6× smaller than the source files; it is still
duplication. ``.portia/`` is gitignored, so it never reaches a repository.

**Ingest is typed**, using DuckDB's sniffer, per §6.3: typed is smaller and
faster, and the columns portia raises flags about — numbers stored as text, with
stray whitespace — stay ``VARCHAR`` under the sniffer anyway, so the signal
survives. `tests/test_store.py` pins that rather than trusting it.

This connection reads files, so it has DuckDB's default external access. That is
portia's own handle. The agent never touches it: `ops/sql.py` opens its own
restricted connection, and keeps doing so (§6.1).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from portia.core.io import read_query
from portia.core.table import Table, quote_ident

#: The store, inside the catalog's directory. One file per project: a project is
#: the unit a user opens, closes, and throws away, and its data should go with it.
STORE_FILE = "store.duckdb"


def store_path(portia_dir: str | Path = ".portia") -> Path:
    return Path(portia_dir) / STORE_FILE


def connect(portia_dir: str | Path = ".portia", *, read_only: bool = False) -> Any:
    """Open the project's store, creating it if this is the first source.

    One connection per open project, held for as long as the project is open.
    **Not thread-safe** — a thread that runs a query must take its own handle
    with ``con.cursor()`` and rebind through :meth:`Table.using` (§4). Getting
    that wrong produces intermittent corruption rather than an error, which is
    why it is said here as well as there.
    """
    import duckdb

    path = store_path(portia_dir)
    if not read_only:
        path.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(path), read_only=read_only)


def memory() -> Any:
    """A store with no project behind it — fixtures, tests, one-off CLI reads."""
    import duckdb

    return duckdb.connect(":memory:")


def ingest(con: Any, path: str | Path, *, name: str | None = None) -> dict:
    """Read a data file into the store, replacing any previous copy under ``name``.

    Returns the facts worth remembering about the ingestion — when it happened,
    and the size and mtime of the file it read. The catalog records those so a
    file that changed on disk afterwards is **detectable rather than silently
    stale** (see :func:`is_stale`); before the store existed, every read went
    back to the file and the question could not come up.
    """
    path = Path(path)
    name = name or path.stem
    con.execute(f"CREATE OR REPLACE TABLE {quote_ident(name)} AS {read_query(path)}")
    return {**_file_facts(path), "ingested_at": _now()}


def table(con: Any, name: str) -> Table:
    """The ingested source, as a lazy handle. Raises if it was never ingested."""
    if not has(con, name):
        known = ", ".join(table_names(con)) or "(nothing ingested)"
        raise ValueError(f"{name!r} is not in the store — have: {known}")
    return Table.from_name(name, con)


def has(con: Any, name: str) -> bool:
    return name in table_names(con)


def table_names(con: Any) -> list[str]:
    """Every table in the store, in a stable order."""
    rows = con.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'main' ORDER BY table_name"
    ).fetchall()
    return [r[0] for r in rows]


def forget(con: Any, name: str) -> None:
    """Drop a source's data. Called when a source is un-indexed.

    The **file on disk is not touched** — same line `catalog.remove_source` draws.
    Un-indexing says "portia should stop knowing about this".
    """
    con.execute(f"DROP TABLE IF EXISTS {quote_ident(name)}")


def is_stale(ingestion: dict | None, path: str | Path | None) -> bool:
    """Whether the file has changed since it was ingested.

    Compares the recorded size and mtime against the file now. Says nothing about
    what to *do* about it — re-indexing refreshes facts and preserves prose and
    roles, exactly as it always has (`catalog`, the update rule).

    A source whose file has been moved or deleted counts as stale: whatever the
    store holds is no longer backed by anything on disk, and that is worth
    saying out loud rather than treating as fresh.

    ``path`` is passed in rather than recorded, so there is exactly one place a
    source's location is written down. The caller's path is resolved the way the
    caller resolves everything else — relative to the working directory, as the
    catalog's own ``source`` field already is.
    """
    if not ingestion or not path:
        return False  # nothing was recorded; there is no claim to contradict
    target = Path(path)
    if not target.exists():
        return True
    now = _file_facts(target)
    return any(ingestion.get(k) != now[k] for k in now)


def _file_facts(path: Path) -> dict:
    stat = path.stat()
    # mtime to the microsecond, not the second: a file rewritten quickly with the
    # same length would otherwise read as unchanged, which is the one case this
    # is here to catch.
    return {"size": int(stat.st_size), "mtime": round(stat.st_mtime, 6)}


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")

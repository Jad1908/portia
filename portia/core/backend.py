"""Where the engine's compute runs — the seam `docs/CONNECTOR.md` §3 names.

A `Backend` is three things: a **kind** (``duckdb``, ``snowflake``), a
**dialect** for the few expressions the checks spell differently
(`core/dialect.py`), and a way to **open** a connection. `LOCAL` is DuckDB in
memory reading the repo's files, and it is the default; `core/io.connect` asks
:func:`active` for whichever one the process is on, so the ten call sites that
say ``connect()`` did not change when a warehouse became possible.

**One active backend per process, set explicitly.** The alternative was reading
``project.yaml`` on every ``connect()`` to see whether the project names a
connection, which is implicit twice over: it depends on the working directory,
and it would send a test suite to a real warehouse the day someone set a
connection on the repo's own ``.portia``. So whoever *opens* a project installs
its backend — the app in `ui/engine.open_project`, a CLI at the top of ``main``
— and nothing else reads a file to find out. `using` is the test-shaped form: it
installs one for a block and puts the previous one back.

**Why not a `ContextVar`, which is what `core/cancel.py` uses.** A cancel scope
wraps one piece of work and is installed by the code that runs it. A backend is
the state of the whole process for the life of a project, and a `ContextVar` set
inside one NiceGUI handler is not visible to the next: each handler runs in its
own task with a copy of the context it was created from. A module global is the
honest shape for something the process has one of.

**Nothing here imports a driver.** `LOCAL.open` imports DuckDB, which is a core
dependency; a remote backend is built by `portia/connectors/` with the driver
behind its extra, and handed in here as a value. That is the same line
`knowledge/store.py` draws around Neo4j.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

from portia.core import dialect as dialects


@dataclass(frozen=True)
class Backend:
    """One place compute can run."""

    kind: str
    dialect: dialects.Dialect
    #: A fresh connection, ready to run queries on. Not registered with the
    #: cancel scope: `core/io.connect` does that, once, for every kind.
    open: Callable[[], Any]
    #: Whether the data lives somewhere portia cannot copy it from. Decides the
    #: SQL hatch's path (`ops/sql.py`), whether Build writes a table, and whether
    #: `write_outputs` is refused (`CONNECTOR.md` §2.7, §2.8).
    remote: bool = False
    #: Whether `record_step` creates a model's table where the spec says as it
    #: records (`CONNECTOR.md` §2.7.1) — the hand-off. Off, only Run and Build
    #: write. **There is no target here**: where a table goes is each spec's
    #: own ``target``, chosen per table, and nothing in the process defaults it.
    agent_writes: bool = False
    #: Where the connection opens, ``DATABASE`` or ``DATABASE.SCHEMA``, when the
    #: user's registry entry names one. Prose only: the brief offers it as the
    #: place to start, and nothing writes there because of it.
    opens_on: str | None = None
    #: The connection's name in the user's registry, for the pane and the prompt.
    label: str = ""


def _open_duckdb() -> Any:
    import duckdb

    return duckdb.connect(":memory:")


#: DuckDB in memory, reading files in place. What every project was before
#: `docs/CONNECTOR.md`, and what every project without a ``connection:`` still is.
LOCAL = Backend(kind="duckdb", dialect=dialects.DUCKDB, open=_open_duckdb)

_active: Backend = LOCAL


def active() -> Backend:
    """The backend this process is on."""
    return _active


def use(backend: Backend) -> Backend:
    """Install ``backend`` for the process. Returns the one it replaced."""
    global _active
    previous, _active = _active, backend
    return previous


@contextlib.contextmanager
def using(backend: Backend) -> Iterator[Backend]:
    """Install ``backend`` for a block and restore the previous one after."""
    previous = use(backend)
    try:
        yield backend
    finally:
        use(previous)


def is_remote(con: Any) -> bool:
    """Whether this connection reaches data portia must not copy.

    Read off the connection, as the dialect is (`dialect.of`): the connection
    is what a `Table` carries, and a check holding one should not have to ask
    the process which backend made it.
    """
    return bool(getattr(con, "remote", False))


#: How many queries a connection is asked at once when nothing says otherwise:
#: one. DuckDB has no per-query toll to recover, and one at a time is what every
#: golden file was measured at.
SEQUENTIAL = 1


def parallel_queries(con: Any) -> int:
    """How many small queries the profiler may keep in flight on this connection.

    Read off the connection, as `is_remote` and the dialect are. A connector
    that pays a fixed toll per query sets it on its session (BigQuery: about
    two seconds of job creation and polling, whatever the query scans, so a
    37-column profile of 75 queries took six minutes one at a time on
    2026-09-06 and is about twenty seconds eight wide). **The default is one**,
    because on Snowflake the eight would share one warehouse's compute and
    nothing has measured a gain there; turning it on for a warehouse is one
    attribute plus a measurement, never a rule about "remote".
    """
    return max(int(getattr(con, "parallel_queries", SEQUENTIAL) or SEQUENTIAL), SEQUENTIAL)

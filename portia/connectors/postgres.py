"""PostgreSQL, as the connection `core.table.Table` expects (`docs/CONNECTORS.md` §9).

The third connector, and the first whose server is often on the same machine.
That changes nothing here: a Postgres on ``localhost`` is reached by a host and
a port like any other, so there is one connector and no local variant of it.
`Session` answers the six things `Table` asks (``execute``, ``sql``,
``cursor``, ``interrupt``, ``close``, and ``register`` for fixtures only) and
carries ``dialect`` and ``remote`` like the other two.

**What is different from the warehouses, and where each difference went:**

- **A session reaches one database.** Postgres cannot query across databases,
  so `databases()` lists the one the connection names and a three-part name is
  valid SQL only because its first part is that database. Portia still
  qualifies fully (§2.5); a connection per database is the way to a second one.
- **The dialect depends on the server's version**, so it is an attribute set
  when the session opens and not a class constant: 16 has
  ``pg_input_is_valid`` and older servers get `dialect.POSTGRES_BEFORE_16`.
  Older than `OLDEST` is refused at sign-in with both numbers.
- **The schema of a query comes from running it with ``LIMIT 0``.** The
  executor asks the plan for no rows, so nothing is scanned. Type names are
  the server's own (``format_type``), looked up once per type and remembered.
- **Stop is the driver's cancel**, a second short-lived connection that asks
  the server to cancel the first one's statement. Throttled per §2.10.
- **One statement at a time.** Every handle shares one connection and the
  driver serializes them. A second connection per handle was the alternative
  and is deferred: handles are not reliably closed by their callers, and a
  leak there is somebody's ``max_connections``.
- **Nothing records when a table last changed.** ``mtime`` is the table's
  cumulative write counters instead (`table_facts`), which `STALENESS_FACTS`
  only ever compares for equality.
- **The free row count is the planner's estimate**, and the facts say so as a
  field. An exact one is a scan, which §2.6 leaves to a profile.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd

from portia.connectors import pool as pools
from portia.connectors.pool import (  # noqa: F401 — reached through this module by the window and the tests
    SecretRequired,
    pool_of,
    rewrite,
    split,
)
from portia.connectors.registry import PGPASS, Connection
from portia.core import dialect
from portia.core.backend import Backend
from portia.core.table import quote_literal, subquery

#: What ``pg_stat_activity`` shows portia as.
APPLICATION = "portia"

#: The oldest server this is written for, as ``server_version_num``. 12 is where
#: the catalogs this module reads settled; nothing older is still maintained.
OLDEST = 120000
#: Where ``pg_input_is_valid`` arrived (`core/dialect.Postgres.try_cast`).
EXACT_CAST_FROM = 160000

#: Seconds a sign-in may take before it is a failure. A wrong host otherwise
#: hangs on the operating system's own timeout, which is minutes.
CONNECT_TIMEOUT = 10

DEFAULT_PORT = "5432"

#: ``pg_class.relkind`` as portia's two kinds. Ordinary, partitioned and foreign
#: tables are tables; views and materialized views are views.
_KINDS = {"r": "table", "p": "table", "f": "table", "v": "view", "m": "view"}

_SYSTEM_SCHEMAS = ("information_schema",)


class ConnectorMissing(RuntimeError):
    """The `postgres` extra is not installed. One sentence, at the edge."""


def _driver():
    try:
        import psycopg as driver
    except ImportError as exc:
        raise ConnectorMissing(
            "the PostgreSQL driver is not installed — `uv sync --extra postgres` "
            "(or `pip install 'portia[postgres]'`)"
        ) from exc
    return driver


# --- the adapter --------------------------------------------------------------


class _Result:
    """What ``execute`` hands back: the rows, already on this side, plus ``fetch_df``."""

    def __init__(self, cursor: Any) -> None:
        self._cursor = cursor

    def fetchone(self) -> tuple | None:
        if self._cursor.description is None:
            return None
        row = self._cursor.fetchone()
        return tuple(row) if row is not None else None

    def fetchall(self) -> list[tuple]:
        if self._cursor.description is None:
            return []
        return [tuple(r) for r in self._cursor.fetchall()]

    def fetch_df(self) -> pd.DataFrame:
        """A frame, built from rows, and only ever behind a ``LIMIT``."""
        names = [d[0] for d in self._cursor.description or []]
        return pd.DataFrame(self.fetchall(), columns=names)

    @property
    def description(self):
        return self._cursor.description


class _Relation:
    """What ``sql(query)`` hands back: the schema, from a run that returned no rows."""

    def __init__(self, columns: list[str], types: list[str]) -> None:
        self.columns = columns
        self.types = types


class Session:
    """A handle on one PostgreSQL connection, in the shape `Table` reads."""

    remote = True
    #: One (`core/backend.parallel_queries`): the handles share a connection, so
    #: eight in flight would be eight waiting on one.
    parallel_queries = 1

    def __init__(
        self,
        raw: Any,
        *,
        owner: bool,
        shared: pools.InFlight | None = None,
        type_names: dict[tuple[int, int], str] | None = None,
    ) -> None:
        self._raw = raw
        self._owner = owner
        self._shared = shared or pools.InFlight()
        self._type_names = type_names if type_names is not None else {}
        self.dialect = dialect_for(server_version(raw))

    # --- what Table asks ------------------------------------------------------

    def execute(self, sql: str) -> _Result:
        cursor = self._raw.cursor()
        with self._shared.running():
            cursor.execute(sql)
        return _Result(cursor)

    def sql(self, query: str) -> _Relation:
        """The schema of ``query``. ``LIMIT 0``, so the plan is made and no row is read."""
        cursor = self._raw.cursor()
        cursor.execute(f"SELECT * FROM {subquery(query)} LIMIT 0")
        described = cursor.description or []
        wanted = [(int(c.type_code), _typmod(cursor, i)) for i, c in enumerate(described)]
        return _Relation([str(c.name) for c in described], self._names_of(wanted))

    def _names_of(self, wanted: list[tuple[int, int]]) -> list[str]:
        """``format_type`` for each ``(type, modifier)``, asked once and remembered."""
        missing = sorted({w for w in wanted if w not in self._type_names})
        if missing:
            pairs = ", ".join(f"({oid}, {mod})" for oid, mod in missing)
            rows = self._raw.execute(
                f"SELECT o, m, format_type(o, m) FROM (VALUES {pairs}) AS t(o, m)"
            ).fetchall()
            for oid, mod, name in rows:
                self._type_names[(int(oid), int(mod))] = str(name)
        return [self._type_names.get(w, "unknown") for w in wanted]

    def cursor(self) -> Session:
        """A sibling handle over the same connection, for a worker thread."""
        return Session(self._raw, owner=False, shared=self._shared, type_names=self._type_names)

    def interrupt(self) -> None:
        """Cancel the statement this connection is running — throttled, and only if one is."""
        if not self._shared.busy or not self._shared.due():
            return
        try:
            self._raw.cancel()
        except Exception:  # noqa: BLE001 — a cancel that fails has nothing to stop
            pass

    def close(self) -> None:
        """Close the connection if this handle owns it; a sibling closing is a no-op."""
        if self._owner:
            self._raw.close()

    def register(self, name: str, frame: pd.DataFrame) -> None:
        raise NotImplementedError(
            "a pandas frame cannot be registered on a database session — fixtures are local"
        )

    unregister = register

    @property
    def closed(self) -> bool:
        return bool(self._raw.closed)

    @property
    def database(self) -> str:
        return str(self._raw.info.dbname)

    # --- browsing, for the scope picker (§2.5) ---------------------------------

    def databases(self) -> list[str]:
        """The one database this session is in. Postgres reaches no other from here."""
        return [self.database]

    def schemas(self, database: str) -> list[str]:
        self._same_database(database)
        rows = self.execute(
            "SELECT nspname FROM pg_namespace "
            "WHERE nspname NOT LIKE 'pg\\_%' AND has_schema_privilege(oid, 'USAGE') "
            "ORDER BY 1"
        ).fetchall()
        return [str(r[0]) for r in rows if str(r[0]) not in _SYSTEM_SCHEMAS]

    def tables(self, database: str, schema: str) -> list[tuple[str, str]]:
        """``(name, kind)`` for every table and view in the schema. Catalog reads only."""
        self._same_database(database)
        rows = self.execute(
            "SELECT c.relname, c.relkind FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            f"WHERE n.nspname = {quote_literal(schema)} "
            f"AND c.relkind IN ({', '.join(quote_literal(k) for k in _KINDS)}) "
            "AND NOT c.relispartition ORDER BY 1"
        ).fetchall()
        return [(str(name), _KINDS[_relkind(kind)]) for name, kind in rows]

    # --- what the catalog records without a scan (§2.6) ------------------------

    def columns(self, qualified: str) -> list[tuple[str, str]]:
        """``(name, type)`` per column, as ``psql``'s ``\\d`` would show them."""
        oid = self._oid(qualified)
        rows = self.execute(
            "SELECT attname, format_type(atttypid, atttypmod) FROM pg_attribute "
            f"WHERE attrelid = {oid} AND attnum > 0 AND NOT attisdropped ORDER BY attnum"
        ).fetchall()
        return [(str(name), str(dtype)) for name, dtype in rows]

    def table_facts(self, qualified: str) -> dict:
        """Row count, bytes and a change marker, as `catalog.STALENESS_FACTS` spells them.

        ``size`` is the table with its indexes and TOAST. **``mtime`` is not a
        time**: Postgres keeps none for a table, so it is the cumulative
        insert, update and delete counters, which move when the rows do. The
        catalog, the graph and the findings only compare it for equality
        (§2.11). The counters restart with the server's statistics, which reads
        as *changed*, the safe direction. A view has neither a size nor
        counters, so its ``mtime`` is ``None`` and nothing can say it is fresh.

        ``rows`` is ``pg_class.reltuples``, what the planner believes since the
        last ``ANALYZE``: free, and an estimate, so ``approximate`` names it.
        It is ``None`` on a table never analyzed and on a view.
        """
        oid = self._oid(qualified)
        row = self.execute(
            "SELECT c.relkind, c.reltuples, pg_total_relation_size(c.oid), "
            "s.n_tup_ins + s.n_tup_upd + s.n_tup_del "
            "FROM pg_class c LEFT JOIN pg_stat_all_tables s ON s.relid = c.oid "
            f"WHERE c.oid = {oid}"
        ).fetchone()
        kind, estimate, size, writes = row or ("r", None, 0, None)
        rows = int(estimate) if estimate is not None and estimate >= 0 else None
        return {
            "size": int(size or 0),
            "mtime": f"writes:{int(writes)}" if writes is not None else None,
            "rows": rows,
            **({"approximate": ["rows"]} if rows is not None else {}),
            "kind": _KINDS.get(_relkind(kind), "table"),
            "at": datetime.now().astimezone().isoformat(timespec="seconds"),
        }

    def whoami(self) -> str:
        """Who the session is, for ``connect test``: user, database, host and version."""
        info = self._raw.info
        version = ".".join(str(p) for p in _version_parts(server_version(self._raw)))
        return (
            f"connected as {info.user}, database {info.dbname}, "
            f"{info.host}:{info.port}, PostgreSQL {version}"
        )

    # --- helpers --------------------------------------------------------------

    def _same_database(self, database: str) -> None:
        if database != self.database:
            raise ValueError(
                f"this connection is in the database {self.database!r}; PostgreSQL cannot "
                f"read {database!r} from it. Add a connection for that database."
            )

    def _oid(self, qualified: str) -> int:
        database, schema, table = split(qualified)
        self._same_database(database)
        row = self.execute(
            "SELECT c.oid FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            f"WHERE n.nspname = {quote_literal(schema)} AND c.relname = {quote_literal(table)}"
        ).fetchone()
        if row is None:
            raise ValueError(f"no table {qualified} — or the role cannot see it")
        return int(row[0])


def _relkind(value: Any) -> str:
    """``relkind`` is the one-byte ``"char"`` type, which the driver hands back as bytes."""
    return value.decode() if isinstance(value, bytes | bytearray) else str(value)


def _typmod(cursor: Any, index: int) -> int:
    """The type modifier of one result column: the ``(10,2)`` in ``numeric(10,2)``."""
    result = getattr(cursor, "pgresult", None)
    return int(result.fmod(index)) if result is not None else -1


def server_version(raw: Any) -> int:
    """``server_version_num``: 160004 is 16.4."""
    return int(raw.info.server_version)


def _version_parts(number: int) -> tuple[int, int]:
    return number // 10000, number % 10000


def dialect_for(version: int) -> dialect.Dialect:
    """The spellings a server of this version accepts (`core/dialect.Postgres`)."""
    return dialect.POSTGRES if version >= EXACT_CAST_FROM else dialect.POSTGRES_BEFORE_16


# --- the pool and the backend ------------------------------------------------


class Pool(pools.Pool):
    """`connectors/pool.py`'s pool, opening PostgreSQL connections."""

    def __init__(self, connection: Connection) -> None:
        super().__init__(connection, open_session)


def open_session(connection: Connection, secret: str | None = None) -> Session:
    return Session(open_raw(connection, secret), owner=True)


def open_raw(connection: Connection, secret: str | None = None) -> Any:
    """The driver's own connection, signed in the way the connection says.

    **Autocommit**, because every handle shares it: inside a transaction one
    failed statement would refuse every statement after it, on every handle,
    until somebody rolled back. `login_fields` is the pure half, so a test can
    read what would be sent without a driver.
    """
    connection.check()
    driver = _driver()
    fields = login_fields(connection, secret)
    try:
        raw = driver.connect(**fields, autocommit=True)
    except driver.OperationalError as exc:
        raise ValueError(plain_failure(str(exc), fields["host"], fields["port"])) from exc
    version = server_version(raw)
    if version < OLDEST:
        raw.close()
        found, oldest = _version_parts(version)[0], _version_parts(OLDEST)[0]
        raise ValueError(f"this server is PostgreSQL {found}; portia needs {oldest} or newer")
    return raw


def plain_failure(text: str, host: str, port: int) -> str:
    """libpq's sign-in failure as one sentence.

    It reports once per address it tried (``::1``, then ``127.0.0.1``) and then
    lists them all again, so a wrong password arrives as five lines saying the
    same thing. The first line's last clause is the server's own sentence.
    Done here and not in the window, so the terminal reads the same one.
    """
    first = text.strip().splitlines()[0] if text.strip() else ""
    reason = first.rsplit("failed: ", 1)[-1].removeprefix("FATAL:").strip()
    if not reason or "refused" in reason.lower() or "could not receive data" in reason.lower():
        return f"nothing is listening at {host}:{port}. Is the PostgreSQL server running?"
    return reason


def login_fields(connection: Connection, secret: str | None = None) -> dict[str, Any]:
    """What ``connect`` is called with. The secret is in here and nowhere durable.

    Under `registry.PGPASS` no password is sent, and libpq finds one the way
    ``psql`` does: ``~/.pgpass``, ``PGPASSWORD``, or none on a server that
    trusts the socket.
    """
    fields: dict[str, Any] = {
        "host": connection.host,
        "port": int(connection.port or DEFAULT_PORT),
        "dbname": connection.database,
        "user": connection.user,
        "application_name": APPLICATION,
        "connect_timeout": CONNECT_TIMEOUT,
    }
    if connection.auth != PGPASS:
        fields["password"] = secret
    if connection.sslmode:
        fields["sslmode"] = connection.sslmode
    return fields


def backend(connection: Connection, *, agent_writes: bool = False) -> Backend:
    """A `Backend` over one pooled connection for ``connection``."""
    return pools.build(
        Pool(connection), kind="postgres", dialect=dialect.POSTGRES, agent_writes=agent_writes
    )


def facts_now(qualified: str, con: Any) -> dict:
    """`Session.table_facts` off whatever handle the caller holds."""
    return con.table_facts(qualified)

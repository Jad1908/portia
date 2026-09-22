"""Snowflake, as the connection `core.table.Table` expects (`docs/CONNECTOR.md`).

`Table` asks six things of a connection: ``execute(sql)`` returning something
with ``fetchone``/``fetchall``/``fetch_df``, ``sql(query)`` returning something
with ``columns`` and ``types``, ``cursor()`` for a thread's own handle,
``interrupt()`` for Stop, ``close()``, and — for fixtures only — ``register``.
`Session` answers those over one Snowflake connection, and adds two attributes
the rest of the engine reads off a connection rather than off the process:
``dialect`` (`core/dialect.of`) and ``remote`` (`core/backend.is_remote`).

**One session per backend, many handles** (`connectors/pool.py`). Opening a
session may open a browser (§2.4), so the pool opens it once when the project
is activated and `Backend.open` hands out siblings over it. The connector's
connection object is thread-safe; its cursors are not, and each `Session`
makes a cursor per statement, so a sibling on a worker thread is safe by
construction.

**Stop is a cancel of the session's queries** (§2.10). ``interrupt`` is called by
`core/cancel.py` every 50 ms once Stop is pressed, and each call here would be a
round trip, so it fires only while a statement is in flight and at most once a
second. ``SYSTEM$CANCEL_ALL_QUERIES`` on the session id is the one call that
needs no query id, which the executing thread does not have until the server
answers.

**Types are Snowflake's own names.** ``describe`` reports a type code and a
scale, and this renders them as ``NUMBER(38,0)``, ``TEXT``, ``TIMESTAMP_NTZ``,
which `checks.profiling.kind_of` reads. The catalog then shows the type the
warehouse would show, not a DuckDB approximation of it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pandas as pd

from portia.connectors import pool as pools
from portia.connectors.pool import (  # noqa: F401 — reached through this module by the window and the tests
    SecretRequired,
    pool_of,
    rewrite,
)
from portia.connectors.registry import FILE, Connection
from portia.core import dialect
from portia.core.backend import Backend
from portia.core.table import quote_literal

#: What the account's query history shows portia as.
APPLICATION = "portia"

#: The connector's type codes, by name, for the few that need a spelled-out
#: rendering. Everything else is reported under the connector's own name.
_FIXED, _REAL, _TEXT = "FIXED", "REAL", "TEXT"


class ConnectorMissing(RuntimeError):
    """The `snowflake` extra is not installed. One sentence, at the edge."""


def _driver():
    try:
        import snowflake.connector as driver
    except ImportError as exc:
        raise ConnectorMissing(
            "the Snowflake connector is not installed — `uv sync --extra snowflake` "
            "(or `pip install 'portia[snowflake]'`)"
        ) from exc
    return driver


# --- the adapter --------------------------------------------------------------


class _Result:
    """What ``execute`` hands back: the connector's cursor, plus ``fetch_df``."""

    def __init__(self, cursor: Any) -> None:
        self._cursor = cursor

    def fetchone(self) -> tuple | None:
        row = self._cursor.fetchone()
        return tuple(row) if row is not None else None

    def fetchall(self) -> list[tuple]:
        return [tuple(r) for r in self._cursor.fetchall()]

    def fetch_df(self) -> pd.DataFrame:
        """A frame, built from rows: no pyarrow, and only ever behind a ``LIMIT``."""
        names = [d[0] for d in self._cursor.description or []]
        return pd.DataFrame(self.fetchall(), columns=names)

    @property
    def description(self):
        return self._cursor.description


@dataclass(frozen=True)
class _Relation:
    """What ``sql(query)`` hands back: the schema, without running the query."""

    columns: list[str]
    types: list[str]


class Session:
    """A handle on one Snowflake session, in the shape `Table` reads."""

    dialect = dialect.SNOWFLAKE
    remote = True
    #: One at a time, on purpose (`core/backend.parallel_queries`). The driver
    #: would allow more and so would the server, but eight concurrent queries
    #: share one warehouse's compute, a warehouse queues past about eight, and
    #: the per-query toll here is a few hundred milliseconds rather than
    #: BigQuery's two seconds. Nothing has measured a gain; raise it against a
    #: real account, not by analogy.
    parallel_queries = 1

    def __init__(self, raw: Any, *, owner: bool, shared: pools.InFlight | None = None) -> None:
        self._raw = raw
        self._owner = owner
        self._shared = shared or pools.InFlight()

    # --- what Table asks ------------------------------------------------------

    def execute(self, sql: str) -> _Result:
        cursor = self._raw.cursor()
        with self._shared.running():
            cursor.execute(sql)
        return _Result(cursor)

    def sql(self, query: str) -> _Relation:
        """The schema of ``query``, from ``describe`` — nothing is executed."""
        described = self._raw.cursor().describe(query) or []
        return _Relation(
            columns=[str(m.name) for m in described], types=[type_name(m) for m in described]
        )

    def cursor(self) -> Session:
        """A sibling handle over the same session, for a worker thread."""
        return Session(self._raw, owner=False, shared=self._shared)

    def interrupt(self) -> None:
        """Cancel whatever this session is running — throttled, and only if something is."""
        if not self._shared.busy or not self._shared.due():
            return
        try:
            self._raw.cursor().execute(
                f"SELECT SYSTEM$CANCEL_ALL_QUERIES({int(self._raw.session_id)})"
            )
        except Exception:  # noqa: BLE001 — a cancel that fails has nothing to stop
            pass

    def close(self) -> None:
        """Close the session if this handle owns it; a sibling closing is a no-op."""
        if self._owner:
            self._raw.close()

    def register(self, name: str, frame: pd.DataFrame) -> None:
        raise NotImplementedError(
            "a pandas frame cannot be registered on a warehouse session — fixtures are local"
        )

    unregister = register

    @property
    def session_id(self) -> int:
        return int(self._raw.session_id)

    @property
    def closed(self) -> bool:
        return bool(self._raw.is_closed())

    # --- browsing, for the scope picker (§2.5) ---------------------------------

    def databases(self) -> list[str]:
        return [str(r[1]) for r in self.execute("SHOW DATABASES").fetchall()]

    def schemas(self, database: str) -> list[str]:
        rows = self.execute(f"SHOW SCHEMAS IN DATABASE {self.dialect.quote(database)}").fetchall()
        return [str(r[1]) for r in rows if str(r[1]) != "INFORMATION_SCHEMA"]

    def tables(self, database: str, schema: str) -> list[tuple[str, str]]:
        """``(name, kind)`` for every table and view in the schema. Metadata only."""
        q = self.dialect.quote
        target = f"{q(database)}.{q(schema)}"
        found = [
            (str(r[1]), "table") for r in self.execute(f"SHOW TABLES IN SCHEMA {target}").fetchall()
        ]
        found += [
            (str(r[1]), "view") for r in self.execute(f"SHOW VIEWS IN SCHEMA {target}").fetchall()
        ]
        return sorted(found)

    # --- what the catalog records without a scan (§2.6) ------------------------

    def columns(self, qualified: str) -> list[tuple[str, str]]:
        """``(name, type)`` per column, as the warehouse states them."""
        q = self.dialect.quote
        database, schema, table = split(qualified)
        rows = self.execute(f"DESCRIBE TABLE {q(database)}.{q(schema)}.{q(table)}").fetchall()
        return [(str(r[0]), str(r[1])) for r in rows]

    def table_facts(self, qualified: str) -> dict:
        """Row count, bytes and last change, as `catalog.STALENESS_FACTS` spells them.

        ``size`` is bytes and ``mtime`` is ``LAST_ALTERED``, so `catalog.is_stale`,
        the graph's fingerprint and the findings' compare a warehouse table with
        the code they already have (§2.11). ``rows`` is beside them because it is
        the one number that makes a table readable before anyone profiles it.
        """
        database, schema, table = split(qualified)
        row = self.execute(
            f"SELECT row_count, bytes, last_altered, table_type "
            f"FROM {self.dialect.quote(database)}.information_schema.tables "
            f"WHERE table_schema = {quote_literal(schema)} AND table_name = {quote_literal(table)}"
        ).fetchone()
        if row is None:
            raise ValueError(f"no table {qualified} — or the role cannot see it")
        rows, size, altered, kind = row
        return {
            "size": int(size or 0),
            "mtime": altered.isoformat(timespec="seconds") if altered else None,
            "rows": int(rows) if rows is not None else None,
            "kind": "view" if str(kind or "").upper().endswith("VIEW") else "table",
            "at": datetime.now().astimezone().isoformat(timespec="seconds"),
        }

    def whoami(self) -> str:
        """Who the session is, for ``connect test``: user, role and warehouse."""
        row = self.execute("SELECT current_user(), current_role(), current_warehouse()").fetchone()
        user, role, warehouse = row or ("?", "?", "?")
        return f"connected as {user}, role {role}, warehouse {warehouse}"


#: The three-part name, shared with every connector (`connectors/pool.py`).
split = pools.split


def type_name(meta: Any) -> str:
    """The warehouse's name for a described column's type."""
    from snowflake.connector.constants import FIELD_ID_TO_NAME

    code = FIELD_ID_TO_NAME.get(getattr(meta, "type_code", -1), "")
    if code == _FIXED:
        precision, scale = getattr(meta, "precision", None), getattr(meta, "scale", None)
        if precision is not None and scale is not None:
            return f"NUMBER({precision},{scale})"
        return "NUMBER"
    if code == _REAL:
        return "FLOAT"
    return code or "UNKNOWN"


# --- the pool and the backend ------------------------------------------------


class Pool(pools.Pool):
    """`connectors/pool.py`'s pool, opening Snowflake sessions."""

    def __init__(self, connection: Connection) -> None:
        super().__init__(connection, open_session)


def open_session(connection: Connection, secret: str | None = None) -> Session:
    return Session(open_raw(connection, secret), owner=True)


def open_raw(connection: Connection, secret: str | None = None) -> Any:
    """The connector's own connection object, signed in the way the connection says.

    Browser SSO: ``client_store_temporary_credential`` asks the connector to
    keep the token in the system keyring so the popup is per token lifetime;
    without the ``secure-local-storage`` extra it silently does not, and the
    popup is per connect. A password goes to the default authenticator, which
    runs the account's MFA on top of it. A programmatic access token goes to
    the token authenticator. ``application`` is what the account's history
    shows. Under `registry.FILE` the connector is given the name of an entry in
    its own `connections.toml` and reads the rest itself, secret included.
    :func:`login_fields` is the pure half, so a test can read what would be sent
    without a driver.
    """
    connection.check()
    driver = _driver()
    return driver.connect(**login_fields(connection, secret))


def login_fields(connection: Connection, secret: str | None = None) -> dict[str, Any]:
    """What ``connect`` is called with. The secret is in here and nowhere durable."""
    if connection.auth == FILE:
        # **The name and nothing else.** The connector merges what is passed
        # here *over* the entry (`{**connections[name], **kwargs}`), so sending
        # the account, the user or the role portia recorded would quietly
        # overrule a file its owner has since edited. Under this method the
        # file is the truth and portia's copy of those fields is a label.
        return {
            "connection_name": connection.profile or connection.name,
            "application": APPLICATION,
        }
    fields: dict[str, Any] = {
        "account": connection.account,
        "user": connection.user,
        "warehouse": connection.warehouse,
        "application": APPLICATION,
    }
    if connection.auth == "password":
        fields["password"] = secret
    elif connection.auth == "token":
        fields["authenticator"] = "PROGRAMMATIC_ACCESS_TOKEN"
        fields["token"] = secret
    else:
        fields["authenticator"] = "externalbrowser"
        fields["client_store_temporary_credential"] = True
    for optional in ("role", "database", "schema"):
        value = getattr(connection, optional)
        if value:
            fields[optional] = value
    return fields


def backend(connection: Connection, *, agent_writes: bool = False) -> Backend:
    """A `Backend` over one pooled session for ``connection``."""
    return pools.build(
        Pool(connection), kind="snowflake", dialect=dialect.SNOWFLAKE, agent_writes=agent_writes
    )


def facts_now(qualified: str, con: Any) -> dict:
    """`Session.table_facts` off whatever handle the caller holds."""
    return con.table_facts(qualified)


def scope_from(picks: Sequence[tuple[str, str, str]]) -> list[str]:
    """``(database, schema, table)`` picks as the qualified names `project.yaml` stores."""
    return sorted({f"{d}.{s}.{t}" for d, s, t in picks})

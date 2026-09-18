"""BigQuery, as the connection `core.table.Table` expects (`docs/CONNECTORS.md` §8).

The second connector, and the first built from the contract rather than
before it. `Table` asks six things of a connection: ``execute(sql)`` returning
something with ``fetchone``/``fetchall``/``fetch_df``, ``sql(query)``
returning something with ``columns`` and ``types``, ``cursor()`` for a
thread's own handle, ``interrupt()`` for Stop, ``close()``, and — for fixtures
only — ``register``. `Session` answers those over one BigQuery client, and
adds the two attributes the rest of the engine reads off a connection rather
than off the process: ``dialect`` (`core/dialect.of`) and ``remote``
(`core/backend.is_remote`).

**What is different from Snowflake, and why each is here rather than a
special case elsewhere:**

- **Three levels are project, dataset, table.** Portia's ``database.schema``
  is BigQuery's ``project.dataset`` (§2.5), so `databases()` lists the
  connection's project and any it was told to also browse, and `schemas()`
  lists datasets. A project id can carry a hyphen, which is why every part is
  backtick-quoted (`core/dialect.BigQuery.quote`) before it reaches SQL.
- **The schema of a query comes from a dry run.** There is no ``describe``:
  a query job with ``dry_run=True`` is planned and not executed, bills
  nothing, and returns the result schema. That is how ``sql(query)`` stays
  free, which `Table.columns` relies on being.
- **Stop cancels by job id.** A query job has an id before its rows arrive,
  so the running job is what the in-flight state holds and ``interrupt``
  cancels each one, throttled the way §2.10 asks. Siblings share that state,
  so a cancel on any handle reaches every job the session has in flight,
  including the profiler's concurrent ones.
- **Eight queries at a time.** The profiler's per-column reads go out
  `PARALLEL_QUERIES` wide on this session, because the two-second toll is
  paid per job and jobs overlap; Snowflake and DuckDB stay at one.
- **The bill is bytes scanned.** Nothing here can lower it; the prompt
  (`prompts/backend/bigquery.md`) is where the copilot is told to name
  columns. What this module does is **label** every job ``application:
  portia``, which is BigQuery's own query tag and costs nothing — the thing
  `CONNECTOR.md` §8 deferred on Snowflake, free here.
- **Types are GoogleSQL's names.** The API reports legacy names
  (``INTEGER``, ``FLOAT``, ``BOOLEAN``, ``RECORD``); `type_name` renders the
  names the console shows (``INT64``, ``FLOAT64``, ``BOOL``, ``STRUCT``) and
  wraps a repeated field as ``ARRAY<…>``, so the catalog and the profiler read
  what a BigQuery user would write.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from portia.connectors import pool as pools
from portia.connectors.pool import (  # noqa: F401 — reached through this module by the window and the tests
    SecretRequired,
    pool_of,
    rewrite,
    split,
)
from portia.connectors.registry import ACCESS_TOKEN, ADC, SERVICE_ACCOUNT, Connection
from portia.core import dialect
from portia.core.backend import Backend

#: What the project's job history and audit logs show portia as: the client's
#: user agent, and a label on every job. The label is the query tag.
APPLICATION = "portia"
LABELS = {"application": APPLICATION}

#: The scopes Application Default Credentials are asked for. BigQuery alone;
#: portia reads no bucket and touches no other API.
SCOPES = ("https://www.googleapis.com/auth/bigquery",)

#: How many of the profiler's per-column queries stay in flight at once
#: (`core/backend.parallel_queries`). Every BigQuery query is its own job and
#: costs about two seconds of creation and polling whatever it scans, and jobs
#: from one project run side by side, so eight in flight cost the same bytes as
#: eight in a row and finish in an eighth of the wall time. Measured on
#: 2026-09-06: a 37-column table took 372 s one at a time. Well under the
#: project's concurrent-query quota, which is a hundred.
PARALLEL_QUERIES = 8

#: The API's legacy type names, and the GoogleSQL name the console shows.
_LEGACY = {"INTEGER": "INT64", "FLOAT": "FLOAT64", "BOOLEAN": "BOOL", "RECORD": "STRUCT"}


class ConnectorMissing(RuntimeError):
    """The `bigquery` extra is not installed. One sentence, at the edge."""


def _driver():
    try:
        from google.cloud import bigquery as driver
    except ImportError as exc:
        raise ConnectorMissing(
            "the BigQuery client is not installed — `uv sync --extra bigquery` "
            "(or `pip install 'portia[bigquery]'`)"
        ) from exc
    return driver


# --- the adapter --------------------------------------------------------------


class _Result:
    """What ``execute`` hands back: the job's rows, read once, plus ``fetch_df``."""

    def __init__(self, rows: Any) -> None:
        self._schema = list(getattr(rows, "schema", None) or [])
        self._rows = iter(rows)

    def fetchone(self) -> tuple | None:
        row = next(self._rows, None)
        return tuple(row.values()) if row is not None else None

    def fetchall(self) -> list[tuple]:
        return [tuple(r.values()) for r in self._rows]

    def fetch_df(self) -> pd.DataFrame:
        """A frame, built from rows: no pyarrow, and only ever behind a ``LIMIT``."""
        return pd.DataFrame(self.fetchall(), columns=[f.name for f in self._schema])

    @property
    def description(self) -> list[tuple]:
        return [(f.name,) for f in self._schema]


class _Relation:
    """What ``sql(query)`` hands back: the schema, from a dry run."""

    def __init__(self, fields: list[Any]) -> None:
        self.columns = [str(f.name) for f in fields]
        self.types = [type_name(f) for f in fields]


class Session:
    """A handle on one BigQuery client, in the shape `Table` reads."""

    dialect = dialect.BIGQUERY
    remote = True
    parallel_queries = PARALLEL_QUERIES

    def __init__(
        self,
        client: Any,
        connection: Connection,
        *,
        owner: bool,
        shared: pools.InFlight | None = None,
    ) -> None:
        self._client = client
        self._connection = connection
        self._owner = owner
        self._shared = shared or pools.InFlight()

    # --- what Table asks ------------------------------------------------------

    def execute(self, sql: str) -> _Result:
        driver = _driver()
        job = self._client.query(sql, job_config=driver.QueryJobConfig(labels=LABELS))
        with self._shared.running(job):
            rows = job.result()
        return _Result(rows)

    def sql(self, query: str) -> _Relation:
        """The schema of ``query``, from a dry run — planned, not executed, not billed."""
        driver = _driver()
        job = self._client.query(
            query, job_config=driver.QueryJobConfig(dry_run=True, use_query_cache=False)
        )
        return _Relation(list(job.schema or []))

    def cursor(self) -> Session:
        """A sibling handle over the same client, for a worker thread."""
        return Session(self._client, self._connection, owner=False, shared=self._shared)

    def interrupt(self) -> None:
        """Cancel every job this session is running — throttled, and only if there is one."""
        if not self._shared.busy or not self._shared.due():
            return
        for job in self._shared.handles:
            try:
                self._client.cancel_job(job.job_id, location=job.location)
            except Exception:  # noqa: BLE001 — a cancel that fails has nothing to stop
                pass

    def close(self) -> None:
        """Close the client if this handle owns it; a sibling closing is a no-op."""
        if self._owner and not self._shared.closed:
            self._shared.closed = True
            self._client.close()

    def register(self, name: str, frame: pd.DataFrame) -> None:
        raise NotImplementedError(
            "a pandas frame cannot be registered on a warehouse session — fixtures are local"
        )

    unregister = register

    @property
    def closed(self) -> bool:
        return self._shared.closed

    @property
    def project(self) -> str:
        return str(self._connection.project or self._client.project)

    # --- browsing, for the scope picker (§2.5) ---------------------------------

    def databases(self) -> list[str]:
        """The connection's project, then any it was told to also browse.

        Listing every project the account can see needs the Resource Manager
        API and a permission the account often lacks, and would list hundreds
        of projects the study is not about. The connection names them instead.
        """
        others = [p.strip() for p in str(self._connection.projects or "").split(",")]
        return [self.project, *(p for p in others if p and p != self.project)]

    def schemas(self, database: str) -> list[str]:
        return sorted(d.dataset_id for d in self._client.list_datasets(database))

    def tables(self, database: str, schema: str) -> list[tuple[str, str]]:
        """``(name, kind)`` for every table and view in the dataset. Metadata only."""
        found = [
            (str(t.table_id), _kind(t.table_type))
            for t in self._client.list_tables(f"{database}.{schema}")
        ]
        return sorted(found)

    # --- what the catalog records without a scan (§2.6) ------------------------

    def columns(self, qualified: str) -> list[tuple[str, str]]:
        """``(name, type)`` per column, as the warehouse states them."""
        split(qualified)
        return [(str(f.name), type_name(f)) for f in self._client.get_table(qualified).schema]

    def table_facts(self, qualified: str) -> dict:
        """Row count, bytes and last change, as `catalog.STALENESS_FACTS` spells them.

        ``size`` is bytes and ``mtime`` is the table's ``modified``, so
        `catalog.is_stale`, the graph's fingerprint and the findings' compare a
        warehouse table with the code they already have (§2.11). ``rows`` is
        ``None`` on a view, which has no count until it runs. The byte count
        is also what a full scan would bill, which the prompt says.
        """
        split(qualified)
        try:
            table = self._client.get_table(qualified)
        except Exception as exc:  # noqa: BLE001 — the driver's own class is not portia's to name
            raise ValueError(f"no table {qualified} — or the account cannot see it: {exc}") from exc
        modified = table.modified
        return {
            "size": int(table.num_bytes or 0),
            "mtime": modified.isoformat(timespec="seconds") if modified else None,
            "rows": int(table.num_rows) if table.num_rows is not None else None,
            "kind": _kind(table.table_type),
            "at": datetime.now().astimezone().isoformat(timespec="seconds"),
        }

    def whoami(self) -> str:
        """Who the session is, for ``connect test``: the account and the project."""
        row = self.execute("SELECT SESSION_USER()").fetchone()
        (user,) = row or ("?",)
        return f"connected as {user}, project {self.project}"


def _kind(table_type: Any) -> str:
    """``table`` or ``view``, off BigQuery's ``TABLE``/``VIEW``/``MATERIALIZED_VIEW``/``EXTERNAL``/``SNAPSHOT``."""
    return "view" if str(table_type or "").upper().endswith("VIEW") else "table"


def type_name(field: Any) -> str:
    """The GoogleSQL name for a schema field's type, as the console shows it."""
    legacy = str(getattr(field, "field_type", "") or "").upper()
    name = _LEGACY.get(legacy, legacy or "UNKNOWN")
    if str(getattr(field, "mode", "") or "").upper() == "REPEATED":
        return f"ARRAY<{name}>"
    return name


# --- the pool and the backend ------------------------------------------------


class Pool(pools.Pool):
    """`connectors/pool.py`'s pool, opening BigQuery clients."""

    def __init__(self, connection: Connection) -> None:
        super().__init__(connection, open_session)


def open_session(connection: Connection, secret: str | None = None) -> Session:
    return Session(open_client(connection, secret), connection, owner=True)


def open_client(connection: Connection, secret: str | None = None) -> Any:
    """The driver's own client, signed in the way the connection says.

    ``adc`` asks `google.auth.default` for whatever the machine holds — the
    SDK's user login, a service account the environment names, a metadata
    server — and takes the project it names when the connection names none.
    ``service_account`` reads the key file at the recorded path. ``token``
    wraps the access token typed this session. :func:`login_plan` is the pure
    half, so a test can read what would be done without a driver or a network.
    """
    connection.check()
    driver = _driver()
    from google.api_core.client_info import ClientInfo

    plan = login_plan(connection, secret)
    project = plan["project"]
    if plan["auth"] == SERVICE_ACCOUNT:
        from google.oauth2 import service_account

        credentials = service_account.Credentials.from_service_account_file(
            plan["keyfile"], scopes=SCOPES
        )
        project = project or credentials.project_id
    elif plan["auth"] == ACCESS_TOKEN:
        from google.oauth2.credentials import Credentials

        credentials = Credentials(token=secret)
    else:
        import google.auth

        credentials, default_project = google.auth.default(scopes=SCOPES)
        project = project or default_project
    return driver.Client(
        project=project,
        credentials=credentials,
        location=plan["location"],
        client_info=ClientInfo(user_agent=APPLICATION),
    )


def login_plan(connection: Connection, secret: str | None = None) -> dict[str, Any]:
    """What ``open_client`` would do: the method, the project, the location, the key file.

    The secret itself is not in here; it goes to the credentials object and
    nowhere durable. The key file path is expanded, because a registry entry
    written from the form says ``~/keys/…`` and the driver does not read ``~``.
    """
    keyfile = connection.keyfile
    return {
        "auth": connection.auth or ADC,
        "project": connection.project or None,
        "location": connection.location or None,
        "keyfile": str(Path(keyfile).expanduser()) if keyfile else None,
        "has_secret": bool(secret),
    }


def backend(connection: Connection, *, agent_writes: bool = False) -> Backend:
    """A `Backend` over one pooled client for ``connection``."""
    return pools.build(
        Pool(connection), kind="bigquery", dialect=dialect.BIGQUERY, agent_writes=agent_writes
    )


def facts_now(qualified: str, con: Any) -> dict:
    """`Session.table_facts` off whatever handle the caller holds."""
    return con.table_facts(qualified)

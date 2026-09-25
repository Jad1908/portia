"""Shared fixtures — one way to get a `Table` in a test, and one way to get a graph.

The engine's currency is `core.table.Table` and the fixtures' is `DataFrame`
(deliberately: they are tiny, and they are the readable definition of the test
data — `docs/DUCKDB_MIGRATION.md` §9). This is the bridge, in one place, so five
test modules don't each grow their own connection fixture and drift apart on
when it gets closed.

`neo4j_session` is the same argument for the knowledge graph, plus one of its
own: it is the **one** place that decides when a graph test skips, so "the
container isn't running" can never be mistaken for "the feature is broken".
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from portia.core.io import connect, load_table
from portia.core.table import Table

#: The tracked mock CSVs, for tests that want the on-disk path rather than a frame.
MOCK = Path(__file__).resolve().parents[1] / "data" / "mock"


@pytest.fixture(autouse=True, scope="session")
def _never_the_working_graph():
    """Point the **whole session** at the test server, before any test runs.

    `neo4j_session` already redirects the process, and that was not enough,
    because the tests that damage the working graph are the ones that never ask
    for it: `record_step`, `cli.index` and `knowledge.sync` all refresh the graph
    **best-effort**, so with `NEO4J_PASSWORD` set in the environment they connect
    to the default URI, succeed, and quietly write fixture nodes into whatever
    project you were actually using. Two full-suite runs left 596 nodes from
    `pytest-of-…/tmp` in it.

    Best-effort is what made it silent: nothing failed, so nothing said so.
    Redirecting here means a test cannot reach the working graph even by
    accident — the connection it opens goes to a server whose whole contents are
    disposable.
    """
    previous = os.environ.get("NEO4J_URI")
    os.environ["NEO4J_URI"] = TEST_URI
    yield
    if previous is None:
        del os.environ["NEO4J_URI"]
    else:
        os.environ["NEO4J_URI"] = previous


@pytest.fixture(autouse=True)
def _never_the_users_window_files(tmp_path_factory, monkeypatch):
    """Point the window's per-user preferences at a temp folder, for every test.

    `engine.open_project` writes the recent projects, and a test that opens a
    project without redirecting it writes a pytest temp folder into the list a
    real person opens the app on. The list keeps eight, so one run of the suite
    pushed every real project off the opening screen (found 2026-09-18: eight
    of eight entries were `pytest-of-…` paths). Same shape as the fixture above
    and the same reason: nothing failed, so nothing said so.

    **The three files `prefs` replaced are redirected too**, because it reads
    them when its own file does not exist, which in a test is always: a test
    would otherwise start from the real person's recent projects.
    """
    try:
        from portia.ui import prefs
    except ImportError:  # the `ui` extra is not installed
        yield
        return
    home = tmp_path_factory.mktemp("portia-config")
    monkeypatch.setattr(prefs, "HOME", home)
    monkeypatch.setattr(prefs, "FILE", home / "prefs.json")
    monkeypatch.setattr(prefs, "UNREADABLE", home / "prefs.unreadable.json")
    for name in ("RECENTS", "VIEWS", "LAYOUTS"):
        monkeypatch.setattr(prefs, f"LEGACY_{name}", home / f"{name.lower()}.json")
    yield


@pytest.fixture
def con():
    """A store with no project behind it, closed when the test ends."""
    connection = connect()
    yield connection
    connection.close()


@pytest.fixture
def table(con):
    """``table(frame, "name")`` — a fixture frame as a real table in the store.

    Named tables rather than one reused name, because a test that builds two
    inputs needs them to coexist; the default counter keeps that from being
    something every test has to think about.
    """
    counter = iter(range(1000))

    def make(frame, name: str | None = None) -> Table:
        return Table.from_frame(frame, name or f"t{next(counter)}", con)

    return make


@pytest.fixture
def ingested(con):
    """``ingested("reservations")`` — a tracked mock CSV, read the way a project reads one.

    Named for the ingest step that no longer exists: sources are read in place now
    (`docs/PIPELINE.md` §2.7). Kept under the old name because the *point* of the
    fixture is unchanged — a source arriving through the real loading path rather
    than a frame handed straight to DuckDB.
    """

    def make(name: str) -> Table:
        return load_table(MOCK / f"{name}.csv", con, name=name)

    return make


@pytest.fixture
def neo4j_session():
    """A real Neo4j session, emptied first — or a skip. ``docker compose up -d neo4j``.

    Everything the knowledge graph *decides* is tested without a database
    (`knowledge/schema.py`, `knowledge/build.py`, and the statement builders in
    `store.py`). What needs one is whether the Cypher is right, and that cannot
    be faked: a stub session that answered queries would be a second, wrong
    implementation of Neo4j, and the test would pass against it.

    So these skip rather than mock, and the skip is loud about why.
    """
    from portia.knowledge import store

    pytest.importorskip("neo4j", reason="the graph extra is not installed")
    if not os.environ.get("NEO4J_PASSWORD"):
        pytest.skip("no NEO4J_PASSWORD — see docker-compose.yml")

    # The **whole process** is redirected, not just this session: the code under
    # test opens its own connections (`handlers.measure_overlaps`,
    # `record_step`, `knowledge.sync`) through `store.settings()`, so pointing
    # only the fixture at the test server would have the test read one database
    # while the thing it is testing writes another.
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("NEO4J_URI", TEST_URI)
    try:
        driver = store.connect()
    except store.GraphUnavailable as exc:
        monkeypatch.undo()
        pytest.skip(f"{exc}  (start it: docker compose up -d neo4j-test)")
    with driver.session(database=store.settings()["database"]) as live:
        live.run("MATCH (n) DETACH DELETE n")
        yield live
    driver.close()
    monkeypatch.undo()


#: The **test** server, which is a different one from the working graph on
#: purpose. This fixture empties whatever it connects to, so pointing it at the
#: graph you are using deletes the project you were looking at and leaves fixture
#: nodes behind — which is how `data/orders.csv` appeared in a project with no
#: such file. Neo4j Community allows one user database per server, so the
#: separation has to be a second server (`docker compose up -d neo4j-test`).
TEST_URI = os.environ.get("NEO4J_TEST_URI", "bolt://localhost:7688")


@pytest.fixture
def snowflake_session():
    """A real Snowflake session — or a skip. ``PORTIA_TEST_CONNECTION=<name>``.

    The graph's argument, applied to the warehouse (`docs/CONNECTOR.md` §6):
    everything the connector *decides* is tested without one — the registry,
    the dialect's text, the parse check, the catalog entry's shape — and what
    needs a session is whether the adapter's questions are the ones Snowflake
    answers, which a stub cannot say. The name is one of the user's own
    connections (`connectors.registry`), so the test spends their credits and
    may open their browser; that is why it is opt-in by environment variable.
    """
    pytest.importorskip("snowflake.connector", reason="the snowflake extra is not installed")
    name = os.environ.get("PORTIA_TEST_CONNECTION")
    if not name:
        pytest.skip("no PORTIA_TEST_CONNECTION — name a connection from connections.yaml")
    from portia.connectors import registry, snowflake

    try:
        connection = registry.get(name)
    except KeyError:
        pytest.skip(f"no connection {name!r} in {registry.CONNECTIONS}")
    pool = snowflake.Pool(connection)
    session = pool.connect()
    yield session
    pool.close()


@pytest.fixture
def bigquery_session():
    """A real BigQuery session — or a skip. ``PORTIA_TEST_BIGQUERY=<name>``.

    The Snowflake fixture's argument (`docs/CONNECTORS.md` §4), applied to the
    second connector: a stub answering Google's questions would be a second,
    wrong BigQuery. The name is one of the user's own connections and the
    session bills their bytes, so it is opt-in by environment variable.
    """
    pytest.importorskip("google.cloud.bigquery", reason="the bigquery extra is not installed")
    name = os.environ.get("PORTIA_TEST_BIGQUERY")
    if not name:
        pytest.skip("no PORTIA_TEST_BIGQUERY — name a connection from connections.yaml")
    from portia.connectors import bigquery, registry

    try:
        connection = registry.get(name)
    except KeyError:
        pytest.skip(f"no connection {name!r} in {registry.CONNECTIONS}")
    pool = bigquery.Pool(connection)
    session = pool.connect()
    yield session
    pool.close()


@pytest.fixture
def postgres_session():
    """A real PostgreSQL session — or a skip. ``PORTIA_TEST_POSTGRES=<name>``.

    The other two fixtures' argument (`docs/CONNECTORS.md` §4): a stub answering
    a server's questions would be a second, wrong PostgreSQL. The name is one of
    the user's own connections; a password is read from ``PGPASSWORD``, which is
    also where libpq looks, so a connection that signs in from a password file
    needs nothing here. The tests write to one schema of their own.
    """
    pytest.importorskip("psycopg", reason="the postgres extra is not installed")
    name = os.environ.get("PORTIA_TEST_POSTGRES")
    if not name:
        pytest.skip("no PORTIA_TEST_POSTGRES — name a connection from connections.yaml")
    from portia.connectors import postgres, registry

    try:
        connection = registry.get(name)
    except KeyError:
        pytest.skip(f"no connection {name!r} in {registry.CONNECTIONS}")
    pool = postgres.Pool(connection)
    session = pool.connect(os.environ.get("PGPASSWORD"))
    yield session
    pool.close()

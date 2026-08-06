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
    """``ingested("otb")`` — a tracked mock CSV, read the way a project reads one.

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

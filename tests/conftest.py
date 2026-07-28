"""Shared fixtures — one way to get a `Table` in a test.

The engine's currency is `core.table.Table` and the fixtures' is `DataFrame`
(deliberately: they are tiny, and they are the readable definition of the test
data — `docs/DUCKDB_MIGRATION.md` §9). This is the bridge, in one place, so five
test modules don't each grow their own connection fixture and drift apart on
when it gets closed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from portia.core import store
from portia.core.table import Table

#: The tracked mock CSVs, for tests that want the on-disk path rather than a frame.
MOCK = Path(__file__).resolve().parents[1] / "data" / "mock"


@pytest.fixture
def con():
    """A store with no project behind it, closed when the test ends."""
    connection = store.memory()
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
    """``ingested("otb")`` — a tracked mock CSV read in through the real ingest path."""

    def make(name: str) -> Table:
        store.ingest(con, MOCK / f"{name}.csv", name=name)
        return store.table(con, name)

    return make

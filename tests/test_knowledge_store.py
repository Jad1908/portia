"""What portia sends to Neo4j — checked without Neo4j.

The statements are built by pure functions (`store.node_writes` and friends), so
the rule that matters most here is testable with the container stopped: **a
rebuild owns the structural half and nothing else.** Measurements cost a query
and are not re-derivable from the repo; deleting one would return the graph to
the ambiguity §4.4 exists to remove.

The one test that does need a server says so and skips when there isn't one.
"""

from __future__ import annotations

import pytest

from portia.knowledge import store
from portia.knowledge.schema import (
    COLUMN,
    HAS_COLUMN,
    KEY_PROPERTY,
    LABELS,
    OVERLAPS,
    SOURCE,
    STRUCTURAL,
    Graph,
    column_key,
)


@pytest.fixture
def graph() -> Graph:
    """One source, two columns, and a measurement no rebuild would ever write."""
    g = Graph()
    table = g.add_node(SOURCE, "data/orders.csv", name="orders", summary="the shop's orders")
    columns = [g.add_node(*_column(name), name=name) for name in ("order_id", "customer_id")]
    for column in columns:
        g.add_edge(HAS_COLUMN, table, column)
    g.add_edge(OVERLAPS, columns[0], columns[1], left_coverage=0.9)
    return g


def _column(name: str) -> tuple[str, str]:
    return COLUMN, column_key(SOURCE, "data/orders.csv", name)


def test_every_label_gets_a_uniqueness_constraint():
    statements = store.constraint_statements()
    assert len(statements) == len(LABELS)
    for label in LABELS:
        assert any(f"(n:{label})" in s and f"n.{KEY_PROPERTY[label]}" in s for s in statements)


def test_a_node_carries_its_key_as_a_property_and_the_build_that_wrote_it(graph):
    """The key is `KEY_PROPERTY`'s property in the database; `Node.key` is the
    one statement of it in python, and this is where the two meet."""
    writes = store.node_writes(graph, "b1")
    rows = [row for _, params in writes for row in params["rows"]]
    orders = next(r for r in rows if r["key"] == "data/orders.csv")
    assert orders["properties"]["path"] == "data/orders.csv"
    assert orders["properties"][store.BUILD_PROPERTY] == "b1"
    assert orders["properties"]["summary"] == "the shop's orders"


def test_node_properties_are_replaced_not_merged(graph):
    """A `role` someone cleared in the catalog must not survive on the node."""
    assert all(
        "SET n = row.properties" in statement for statement, _ in store.node_writes(graph, "b1")
    )


def test_a_rebuild_never_writes_a_measured_edge(graph):
    """`OVERLAPS` is in the graph here and must not appear in a single statement."""
    statements = [s for s, _ in store.edge_writes(graph, "b1")]
    assert statements and all(OVERLAPS not in s for s in statements)
    assert any(HAS_COLUMN in s for s in statements)


def test_the_prune_only_deletes_structural_edges(graph):
    edge_delete, node_delete = store.prune_writes("b1")
    assert edge_delete[1]["structural"] == list(STRUCTURAL)
    assert OVERLAPS not in STRUCTURAL
    # A node that still carries a measurement is not cruft, whatever the files say.
    assert "NOT (n)--()" in node_delete[0]


def test_a_missing_driver_says_what_to_install(monkeypatch):
    """§6.6 — a stopped container or an uninstalled extra must not read as a bug.

    `GraphUnavailable` and not `ImportError`: the caller's right response is to
    carry on without the graph, and that has to be distinguishable from "no such
    table", which is a failure of the question rather than of the database.
    """
    import builtins

    real_import = builtins.__import__

    def refuse(name, *args, **kwargs):
        if name == "neo4j":
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", refuse)
    with pytest.raises(store.GraphUnavailable, match=r"portia\[graph\]"):
        store.connect()


# --- the half that needs a server -------------------------------------------
#
# `neo4j_session` lives in conftest.py: one place decides when a graph test
# skips, so "the container isn't running" is never read as "this is broken".


def test_a_rebuild_drops_what_the_files_stopped_saying_and_keeps_a_measurement(
    neo4j_session, graph
):
    session = neo4j_session
    store.write(graph, session)
    # Written by hand because no rebuild writes one — which is the point: phase C
    # puts it there, and phase A must not be able to take it away.
    session.run(
        f"MATCH (a:Column {{key: $a}}), (b:Column {{key: $b}}) MERGE (a)-[:{OVERLAPS}]->(b)",
        a=_column("order_id")[1],
        b=_column("customer_id")[1],
    )

    smaller = Graph()
    table = smaller.add_node(SOURCE, "data/orders.csv", name="orders")
    smaller.add_edge(HAS_COLUMN, table, smaller.add_node(*_column("order_id"), name="order_id"))
    store.write(smaller, session)

    assert session.run(f"MATCH ()-[r:{HAS_COLUMN}]->() RETURN count(r) AS n").single()["n"] == 1
    # The measurement survives: it cost a query and no file can restate it (§5.2).
    assert session.run(f"MATCH ()-[r:{OVERLAPS}]->() RETURN count(r) AS n").single()["n"] == 1
    # …and so does the column it hangs off, even though the catalog dropped it.
    assert session.run("MATCH (n:Column) RETURN count(n) AS n").single()["n"] == 2

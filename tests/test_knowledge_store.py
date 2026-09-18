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
    PROJECT,
    SOURCE,
    STRUCTURAL,
    Graph,
    column_key,
)

#: Two projects that hold the same file path, which is the whole point: one
#: server holds every project, node keys are project-relative, and before
#: `PROJECT` these two were one node.
PROJECT_A = "/tmp/projects/a"
PROJECT_B = "/tmp/projects/b"


@pytest.fixture
def graph() -> Graph:
    """One source, two columns, and a measurement no rebuild would ever write."""
    g = Graph(project=PROJECT_A)
    table = g.add_node(SOURCE, "data/orders.csv", name="orders", summary="the shop's orders")
    columns = [g.add_node(*_column(name), name=name) for name in ("order_id", "customer_id")]
    for column in columns:
        g.add_edge(HAS_COLUMN, table, column)
    g.add_edge(OVERLAPS, columns[0], columns[1], left_coverage=0.9)
    return g


def _column(name: str) -> tuple[str, str]:
    return COLUMN, column_key(SOURCE, "data/orders.csv", name)


def test_every_label_is_unique_on_its_key_within_a_project():
    """Not on the key alone: `data/orders.csv` is a different table in each
    project, and the old single-property constraint said it was one."""
    statements = store.constraint_statements()
    for label in LABELS:
        created = next(s for s in statements if s.startswith("CREATE") and f"(n:{label})" in s)
        assert f"(n.{PROJECT}, n.{KEY_PROPERTY[label]}) IS UNIQUE" in created


def test_the_old_unscoped_constraint_is_dropped_first():
    """It forbids the thing the composite one has to allow, so the two cannot
    coexist and the drop has to run before the create."""
    statements = store.constraint_statements()
    for label in LABELS:
        drop = statements.index(f"DROP CONSTRAINT {label.lower()}_key IF EXISTS")
        create = next(i for i, s in enumerate(statements) if f"{label.lower()}_project_key" in s)
        assert drop < create


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
    edge_delete, node_delete = store.prune_writes("b1", PROJECT_A)
    assert edge_delete[1]["structural"] == list(STRUCTURAL)
    assert OVERLAPS not in STRUCTURAL
    # A node that still carries a measurement is not cruft, whatever the files say.
    assert "NOT (n)--()" in node_delete[0]


def test_the_prune_names_its_project(graph):
    """The one pair of statements here that deletes what it does *not* name, so
    an unscoped `MATCH` reaches every project on the server."""
    for statement, params in store.prune_writes("b1", PROJECT_A):
        assert f"{PROJECT} = $project" in statement
        assert params["project"] == PROJECT_A


def test_every_write_merges_on_the_project_as_well_as_the_key(graph):
    for statement, params in [*store.node_writes(graph, "b1"), *store.edge_writes(graph, "b1")]:
        assert f"{PROJECT}: $project" in statement
        assert params["project"] == PROJECT_A


def test_a_graph_with_no_project_is_refused(neo4j_session):
    """Rather than defaulting to one: a graph stored under the wrong project is
    invisible to the project it belongs to and prunes one it does not."""
    with pytest.raises(ValueError, match="must name its project"):
        store.write(Graph(), neo4j_session)


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
    _measure(session, PROJECT_A, "order_id", "customer_id")

    smaller = Graph(project=PROJECT_A)
    table = smaller.add_node(SOURCE, "data/orders.csv", name="orders")
    smaller.add_edge(HAS_COLUMN, table, smaller.add_node(*_column("order_id"), name="order_id"))
    store.write(smaller, session)

    assert session.run(f"MATCH ()-[r:{HAS_COLUMN}]->() RETURN count(r) AS n").single()["n"] == 1
    # The measurement survives: it cost a query and no file can restate it (§5.2).
    assert session.run(f"MATCH ()-[r:{OVERLAPS}]->() RETURN count(r) AS n").single()["n"] == 1
    # …and so does the column it hangs off, even though the catalog dropped it.
    assert session.run("MATCH (n:Column) RETURN count(n) AS n").single()["n"] == 2


def _measure(session, project: str, left: str, right: str) -> None:
    """An `OVERLAPS` written by hand, because no rebuild writes one — which is
    the point: phase C puts it there and phase A must not take it away."""
    session.run(
        f"MATCH (a:Column {{{PROJECT}: $p, key: $a}}), (b:Column {{{PROJECT}: $p, key: $b}}) "
        f"MERGE (a)-[:{OVERLAPS} {{{PROJECT}: $p}}]->(b)",
        p=project,
        a=_column(left)[1],
        b=_column(right)[1],
    )


def _counts(session) -> dict:
    rows = session.run(f"MATCH (n) RETURN n.{PROJECT} AS project, count(n) AS n")
    return {r["project"]: r["n"] for r in rows}


def test_building_one_project_leaves_another_alone(neo4j_session, graph):
    """The bug this property exists for.

    Both projects hold `data/orders.csv`, so before `PROJECT` they were one set
    of nodes — and the second build's prune, which names no project, deleted
    every structural edge the first had written. What was left was the first
    project's columns with no table attached, still wired to each other by an
    `OVERLAPS` the prune is forbidden to touch.
    """
    session = neo4j_session
    store.write(graph, session)
    _measure(session, PROJECT_A, "order_id", "customer_id")

    other = Graph(project=PROJECT_B)
    table = other.add_node(SOURCE, "data/orders.csv", name="orders")
    other.add_edge(HAS_COLUMN, table, other.add_node(*_column("order_id"), name="order_id"))
    store.write(other, session)

    # Two tables, not one: the same path in two projects is two things.
    assert _counts(session) == {PROJECT_A: 3, PROJECT_B: 2}
    kept = session.run(
        f"MATCH (t:Source {{{PROJECT}: $p}})-[r:{HAS_COLUMN}]->() RETURN count(r) AS n",
        p=PROJECT_A,
    ).single()["n"]
    assert kept == 2  # A's structural half is untouched by B's build
    assert session.run(f"MATCH ()-[r:{OVERLAPS}]->() RETURN count(r) AS n").single()["n"] == 1


def test_a_build_that_found_nothing_prunes_nothing(neo4j_session, graph):
    """A deleted `.portia` makes `build_graph` return an empty graph, and the
    next refresh used to read that as *this project has no tables* — deleting
    the structural half and orphaning every measurement under it."""
    session = neo4j_session
    store.write(graph, session)
    _measure(session, PROJECT_A, "order_id", "customer_id")

    store.write(Graph(project=PROJECT_A), session)

    assert _counts(session) == {PROJECT_A: 3}
    assert session.run(f"MATCH ()-[r:{HAS_COLUMN}]->() RETURN count(r) AS n").single()["n"] == 2


def test_forget_unscoped_removes_what_predates_the_property(neo4j_session, graph):
    """The migration, and the reason it is a flag: nothing on these says which
    project they were, so no build can work it out on the project's behalf."""
    session = neo4j_session
    store.write(graph, session)
    session.run(f"CREATE (:{COLUMN} {{key: 'legacy::x', name: 'x'}})")

    assert store.forget_unscoped(session) == 1
    assert _counts(session) == {PROJECT_A: 3}

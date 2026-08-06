"""The project, read into a graph — structural nodes, edges and column lineage.

`docs/KNOWLEDGE_GRAPH.md` §9.4 phase A: the write path, with no agent anywhere
near it. What these tests pin is that the graph is a *restatement* of the
catalog and the specs — nothing measured, nothing inferred — and that a model
column points at the source column it actually came from, through however many
steps, without running anything.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest
import yaml

from portia import catalog, spec
from portia.knowledge import build_graph, sqllineage
from portia.knowledge.schema import (
    COLUMN,
    DERIVATION,
    DERIVATION_UNKNOWN,
    DERIVES_FROM,
    GROUP,
    HAS_COLUMN,
    IN_GROUP,
    MODEL,
    READS,
    SOURCE,
    Ref,
)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """Two indexed sources and nothing else — the state after `cli.index`."""
    data = tmp_path / "data"
    data.mkdir()
    with open(data / "orders.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["order_id", "customer_id", "amount", "note"])
        w.writerows([[1, " C1 ", 10, "a"], [2, "C2", 20, "b"], [3, "C1", 5, "c"]])
    with open(data / "customers.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["customer_id", "name", "note"])
        w.writerows([["C1", "Ann", "x"], ["C2", "Bo", "y"]])

    portia_dir = tmp_path / catalog.DEFAULT_DIR
    catalog.init_project("two tables", portia_dir=portia_dir)
    for name in ("orders", "customers"):
        catalog.index_source(data / f"{name}.csv", portia_dir=portia_dir)
    return tmp_path


def _write_spec(project: Path, name: str, doc: dict, *, subdir: str = "") -> Path:
    directory = project / spec.SPECS_DIR / subdir if subdir else project / spec.SPECS_DIR
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.yaml"
    path.write_text(yaml.safe_dump(doc, sort_keys=False))
    return path


ORDERS = Ref(SOURCE, "data/orders.csv")
CUSTOMERS = Ref(SOURCE, "data/customers.csv")


# --- the catalog half -------------------------------------------------------


def test_a_source_is_a_node_keyed_by_its_path(project):
    graph = build_graph(project).graph
    node = graph.node(SOURCE, "data/orders.csv")
    assert node is not None
    assert node.properties["name"] == "orders"
    # §4.5 — a measurement has to be able to say what it was taken against, and
    # these are the numbers `catalog.is_stale` already compares.
    assert set(catalog.STALENESS_FACTS) <= set(node.properties)


def test_every_column_is_its_own_node(project):
    """Two tables with a `note` column produce two nodes, never one (§4)."""
    graph = build_graph(project).graph
    columns = [n for n in graph.nodes.values() if n.label == COLUMN]
    assert sorted(n.properties["name"] for n in columns) == [
        "amount",
        "customer_id",
        "customer_id",
        "name",
        "note",
        "note",
        "order_id",
    ]
    assert {e.end for e in graph.edges_of(HAS_COLUMN)} == {n.ref for n in columns}


def test_column_facts_come_from_the_catalog_and_nothing_else(project):
    graph = build_graph(project).graph
    amount = graph.node(COLUMN, ORDERS.column("amount").key)
    assert amount is not None
    assert amount.properties["n_distinct"] == 3
    assert amount.properties["null_rate"] == 0.0
    # Nothing has interpreted this source, so there is no role to restate.
    assert "role" not in amount.properties


def test_a_group_is_restated_with_its_members(project):
    catalog.set_group(
        "sales",
        context="the two tables the shop exports",
        sources=["orders", "customers"],
        portia_dir=project / catalog.DEFAULT_DIR,
    )
    graph = build_graph(project).graph
    group = graph.node(GROUP, "sales")
    assert group is not None and group.properties["context"].startswith("the two")
    assert {e.start for e in graph.edges_of(IN_GROUP)} == {ORDERS, CUSTOMERS}


def test_nothing_measured_is_written(project):
    """Phase A writes structure only. An `OVERLAPS` here would be a fabricated
    fact — nothing in this path compares two columns' values (§4.2)."""
    graph = build_graph(project).graph
    assert {e.kind for e in graph.edges.values()} <= {HAS_COLUMN, IN_GROUP, READS, DERIVES_FROM}


def test_an_empty_project_builds_an_empty_graph(tmp_path):
    result = build_graph(tmp_path)
    assert result.graph.nodes == {} and result.unresolved == {}


# --- the spec half ----------------------------------------------------------


def _join_spec(project: Path, name: str = "mart_orders") -> Path:
    """normalize then join — the shape a real spec has."""
    return _write_spec(
        project,
        name,
        {
            "version": 1,
            "sources": {"orders": "data/orders.csv", "customers": "data/customers.csv"},
            "steps": [
                {
                    "id": "clean",
                    "op": "normalize",
                    "input": "orders",
                    "transforms": [{"column": "customer_id", "op": "strip"}],
                },
                {
                    "id": "joined",
                    "op": "join",
                    "left": "clean",
                    "right": "customers",
                    "keys": ["customer_id"],
                    "how": "inner",
                },
            ],
        },
    )


def test_a_model_reads_the_sources_its_steps_name(project):
    _join_spec(project)
    graph = build_graph(project).graph
    model = Ref(MODEL, "mart_orders")
    assert graph.node(MODEL, "mart_orders").properties["spec"] == "specs/mart_orders.yaml"
    assert {e.end for e in graph.edges_of(READS)} == {ORDERS, CUSTOMERS}
    assert all(e.start == model for e in graph.edges_of(READS))


def test_a_models_output_columns_are_nodes_with_the_names_the_join_gives_them(project):
    """§4.1 — and the names are the op's, not this module's (`join_columns`)."""
    _join_spec(project)
    graph = build_graph(project).graph
    model = Ref(MODEL, "mart_orders")
    produced = [e.end for e in graph.edges_of(HAS_COLUMN) if e.start == model]
    assert [graph.node(COLUMN, r.key).properties["name"] for r in produced] == [
        "order_id",
        "customer_id",
        "amount",
        "note_x",
        "name",
        "note_y",
    ]


def _derives(graph, model_column: str) -> dict[str, dict]:
    """``{origin column key: edge properties}`` for one model column."""
    start = Ref(MODEL, "mart_orders").column(model_column)
    return {e.end.key: e.properties for e in graph.edges_of(DERIVES_FROM) if e.start == start}


def test_a_carried_column_points_at_the_source_column_it_came_from(project):
    _join_spec(project)
    graph = build_graph(project).graph
    assert list(_derives(graph, "amount")) == [ORDERS.column("amount").key]


def test_a_transform_outranks_the_join_that_carried_it(project):
    """The pointer names the step that best explains the values (module docstring).

    `customer_id` was stripped at `#clean` and then coalesced at `#joined`. The
    coalesce is a real derivation too — it is the one column reading both sides —
    so each origin keeps the step that explains *it*.
    """
    _join_spec(project)
    graph = build_graph(project).graph
    edges = _derives(graph, "customer_id")
    assert edges[ORDERS.column("customer_id").key] == {
        "via": "normalize",
        "step": "mart_orders#clean",
    }
    assert edges[CUSTOMERS.column("customer_id").key] == {
        "via": "join",
        "step": "mart_orders#joined",
    }


def test_a_suffixed_column_says_which_side_it_came_from(project):
    _join_spec(project)
    graph = build_graph(project).graph
    assert list(_derives(graph, "note_x")) == [ORDERS.column("note").key]
    assert list(_derives(graph, "note_y")) == [CUSTOMERS.column("note").key]
    assert _derives(graph, "note_x")[ORDERS.column("note").key]["via"] == "join"


def test_lineage_stops_at_the_upstream_models_own_column(project):
    """One hop per model, so the middle of the path is a node you can stand on."""
    _write_spec(
        project,
        "stg_orders",
        {
            "version": 1,
            "sources": {"orders": "data/orders.csv"},
            "steps": [
                {
                    "id": "cleaned",
                    "op": "normalize",
                    "input": "orders",
                    "transforms": [{"column": "customer_id", "op": "strip"}],
                }
            ],
        },
        subdir="staging",
    )
    _write_spec(
        project,
        "mart_orders",
        {
            "version": 1,
            "sources": {"customers": "data/customers.csv"},
            "steps": [
                {
                    "id": "joined",
                    "op": "join",
                    "left": "stg_orders",
                    "right": "customers",
                    "keys": ["customer_id"],
                }
            ],
        },
    )
    graph = build_graph(project).graph
    assert Ref(MODEL, "stg_orders") in {e.end for e in graph.edges_of(READS)}
    assert list(_derives(graph, "amount")) == [Ref(MODEL, "stg_orders").column("amount").key]
    # …and that column carries on back to the file it came from.
    onward = [
        e
        for e in graph.edges_of(DERIVES_FROM)
        if e.start == Ref(MODEL, "stg_orders").column("amount")
    ]
    assert [e.end.key for e in onward] == [ORDERS.column("amount").key]


def _sql_spec(project: Path, sql: str, *, name: str = "agg_orders", step: str = "totals"):
    return _write_spec(
        project,
        name,
        {
            "version": 1,
            "sources": {"orders": "data/orders.csv"},
            "steps": [{"id": step, "op": "sql", "inputs": ["orders"], "sql": sql}],
        },
    )


def _sql_derives(graph, model: str, column: str) -> dict[str, dict]:
    start = Ref(MODEL, model).column(column)
    return {e.end.key: e.properties for e in graph.edges_of(DERIVES_FROM) if e.start == start}


def test_a_sql_step_gets_its_columns_and_its_lineage(project):
    """`docs/SQL_LINEAGE.md` — the hatch used to cost the model every column.

    The rank is read off the parse tree: `customer_id` travelled as a bare
    reference and was *carried*; `total` went through a `sum` and was *changed*.
    """
    _sql_spec(project, "SELECT customer_id, sum(amount) AS total FROM orders GROUP BY 1")
    result = build_graph(project)

    assert result.unresolved == {}
    assert [
        e.end.key for e in result.graph.edges_of(HAS_COLUMN) if e.start == Ref(MODEL, "agg_orders")
    ] == [
        Ref(MODEL, "agg_orders").column("customer_id").key,
        Ref(MODEL, "agg_orders").column("total").key,
    ]
    assert _sql_derives(result.graph, "agg_orders", "customer_id") == {
        ORDERS.column("customer_id").key: {"via": "sql", "step": "agg_orders#totals"}
    }
    assert _sql_derives(result.graph, "agg_orders", "total") == {
        ORDERS.column("amount").key: {"via": "sql", "step": "agg_orders#totals"}
    }


def test_a_star_select_is_expanded_from_the_inputs_the_build_already_holds(project):
    """The schema is what makes this cheap here — nothing else has to supply it."""
    _sql_spec(project, "SELECT * FROM orders")
    graph = build_graph(project).graph
    assert [
        e.end.key for e in graph.edges_of(HAS_COLUMN) if e.start == Ref(MODEL, "agg_orders")
    ] == [
        Ref(MODEL, "agg_orders").column(c).key
        for c in ("order_id", "customer_id", "amount", "note")
    ]


def test_a_column_with_nothing_underneath_it_is_marked_rather_than_left_bare(project):
    """§5.5 — *no outgoing `DERIVES_FROM`* already means *this is where the data
    came from*, so a `count(*)` left unmarked would read as a source column."""
    _sql_spec(project, "SELECT customer_id, count(*) AS n FROM orders GROUP BY 1")
    result = build_graph(project)

    counted = result.graph.node(COLUMN, Ref(MODEL, "agg_orders").column("n").key)
    assert counted is not None
    assert counted.properties[DERIVATION] == DERIVATION_UNKNOWN
    assert _sql_derives(result.graph, "agg_orders", "n") == {}
    # The model itself resolved — one line about one column, not a vanished table.
    assert list(result.unresolved) == ["agg_orders.n"]
    carried = result.graph.node(COLUMN, Ref(MODEL, "agg_orders").column("customer_id").key)
    assert DERIVATION not in carried.properties


def test_sql_that_cannot_be_read_leaves_the_model_unresolved_with_the_reason(project):
    """Unparseable, and unresolvable against the declared inputs — two different
    next moves, so two different reasons rather than one 'sql step' catch-all."""
    _sql_spec(project, "SELECT FROM WHERE")
    assert "could not be parsed" in build_graph(project).unresolved["agg_orders"]

    _sql_spec(project, "SELECT nope FROM orders")
    assert "could not be resolved" in build_graph(project).unresolved["agg_orders"]


def test_without_the_parser_a_sql_step_behaves_exactly_as_it_did_before(project, monkeypatch):
    """§6.6 — a missing optional dependency costs what a stopped container costs.

    The coarse answer `SQL_LINEAGE.md` §3 could find no honest home for as a
    design stage is this: what happens when `sqlglot` is not installed.
    """

    def missing():
        raise sqllineage.LineageUnreadable("sqlglot is not installed")

    monkeypatch.setattr(sqllineage, "_sqlglot", missing)
    _sql_spec(project, "SELECT customer_id FROM orders")
    result = build_graph(project)

    assert "sqlglot is not installed" in result.unresolved["agg_orders"]
    assert result.graph.edges_of(DERIVES_FROM) == []
    assert {e.end for e in result.graph.edges_of(READS)} == {ORDERS}
    assert [
        e.end for e in result.graph.edges_of(HAS_COLUMN) if e.start == Ref(MODEL, "agg_orders")
    ] == []


def test_a_later_step_keeps_walking_through_a_sql_step(project):
    """The failure this fixes was not confined to the hatch step: the walk is
    sequential, so an unresolved step used to blank every step after it."""
    _write_spec(
        project,
        "enriched",
        {
            "version": 1,
            "sources": {"orders": "data/orders.csv", "customers": "data/customers.csv"},
            "steps": [
                {
                    "id": "clean",
                    "op": "sql",
                    "inputs": ["orders"],
                    "sql": "SELECT order_id, trim(customer_id) AS customer_id FROM orders",
                },
                {
                    "id": "joined",
                    "op": "join",
                    "left": "clean",
                    "right": "customers",
                    "keys": ["customer_id"],
                },
            ],
        },
    )
    result = build_graph(project)

    assert result.unresolved == {}
    # The trim is what explains the key, so it outranks the join that carried it.
    assert _sql_derives(result.graph, "enriched", "customer_id") == {
        ORDERS.column("customer_id").key: {"via": "sql", "step": "enriched#clean"},
        CUSTOMERS.column("customer_id").key: {"via": "join", "step": "enriched#joined"},
    }
    # …and a column the hatch only carried still reaches its file.
    assert list(_sql_derives(result.graph, "enriched", "name")) == [CUSTOMERS.column("name").key]


def test_an_unindexed_source_is_a_node_with_no_columns(project):
    """A spec may name a file nobody indexed. The path is true; the columns are
    unknown, and unknown is said rather than guessed."""
    _write_spec(
        project,
        "mart_other",
        {
            "version": 1,
            "sources": {"other": "data/other.csv"},
            "steps": [{"id": "clean", "op": "normalize", "input": "other", "transforms": []}],
        },
    )
    result = build_graph(project)
    assert result.graph.node(SOURCE, "data/other.csv") is not None
    assert "not indexed" in result.unresolved["mart_other"]


def test_the_build_needs_no_data_and_no_connection(project):
    """Nothing here opens a data file, so the graph survives the data moving.

    That is the property that makes this cheap enough to rebuild on every index
    — and it is why lineage comes off `join_columns` rather than off a run.
    """
    _join_spec(project)
    for csv_file in (project / "data").iterdir():
        csv_file.unlink()
    assert build_graph(project).graph.edges_of(DERIVES_FROM)


def test_building_twice_produces_the_same_graph(project):
    _join_spec(project)
    first, second = build_graph(project).graph, build_graph(project).graph
    assert list(first.nodes) == list(second.nodes)
    assert list(first.edges) == list(second.edges)

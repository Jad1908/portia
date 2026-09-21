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
    with open(data / "orders.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["order_id", "customer_id", "amount", "note"])
        w.writerows([[1, " C1 ", 10, "a"], [2, "C2", 20, "b"], [3, "C1", 5, "c"]])
    with open(data / "customers.csv", "w", newline="", encoding="utf-8") as f:
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
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
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


def test_a_columns_shape_facts_are_split_by_kind(project):
    """A range for a number, a modal value for a category, and never both.

    The two halves describe different things: `min`/`max` says nothing about
    `C1` against `C2`, and a modal value says nothing about a range. The split
    is `checks.profiling`'s own, restated by `schema.shape_facts` rather than
    re-derived.
    """
    graph = build_graph(project).graph

    amount = graph.node(COLUMN, ORDERS.column("amount").key)
    assert amount is not None
    assert (amount.properties["min"], amount.properties["max"]) == (5, 20)
    assert "top" not in amount.properties, "a range is not a modal value"

    customer = graph.node(COLUMN, ORDERS.column("customer_id").key)
    assert customer is not None
    # And this is why a value earns a place on the node: the modal value is
    # ' C1 ', whitespace and all. Nothing about the *name* `customer_id` says
    # its values are padded, and that is exactly the kind of thing that decides
    # whether it lines up with a `customer_id` somewhere else.
    assert customer.properties["top"] == " C1 "
    assert customer.properties["top_freq"] == 1
    assert "min" not in customer.properties, "a modal value is not a range"


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


def test_a_model_column_carries_no_shape_facts_until_it_is_built(project):
    """Nothing has profiled the table portia *would* build, so there is nothing
    honest to put here — the same reason a model column carries no `null_rate`.
    An invented range would be the graph asserting a number nobody measured."""
    _join_spec(project)
    graph = build_graph(project).graph
    column = graph.node(COLUMN, Ref(MODEL, "mart_orders").column("amount").key)
    assert column is not None
    assert not {"min", "max", "top", "top_freq"} & set(column.properties)


def test_building_a_model_gives_its_columns_the_facts_a_source_has(project):
    """The gap this closes: every answer about a table portia built used to be
    structure with no measurements in it, so the facts that make an overlap
    readable were missing on exactly the tables portia produces itself."""
    from portia import pipeline

    _join_spec(project)
    pipeline.build_project(project)

    graph = build_graph(project).graph
    amount = graph.node(COLUMN, Ref(MODEL, "mart_orders").column("amount").key)
    assert amount is not None
    assert amount.properties["n_distinct"] == 3
    assert (amount.properties["min"], amount.properties["max"]) == (5, 20)


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


# --- reading the walk back out ----------------------------------------------


def test_inputs_of_groups_a_models_columns_by_where_they_came_from(project):
    """What an arrow on the app's canvas *means*, read off the same walk that
    fills the graph — so the canvas and the browser cannot tell two stories."""
    from portia.knowledge.build import inputs_of

    _join_spec(project)
    found = inputs_of(build_graph(project).graph, "mart_orders")

    assert [t.name for t in found.tables] == ["customers", "orders"], "name order, nothing ranked"
    by_name = {t.name: t for t in found.tables}
    assert by_name["orders"].kind == SOURCE
    assert ("customer_id", "customer_id") in {
        (c.column, c.origin) for c in by_name["orders"].columns
    }


def test_inputs_of_carries_the_step_that_explains_each_column(project):
    """A pointer, never a copy of the expression: the spec is the one place that
    says what a step did."""
    from portia.knowledge.build import inputs_of

    _join_spec(project)
    found = inputs_of(build_graph(project).graph, "mart_orders")
    steps = {c.step for t in found.tables for c in t.columns}
    assert steps, "every traced column names the step responsible"
    assert all(s.startswith("mart_orders#") for s in steps if s)


def test_inputs_of_names_a_column_with_no_single_origin_rather_than_dropping_it(project):
    """A `count(*)` that vanished would read as a column with a *missing* origin
    rather than one with no single origin to have."""
    from portia.knowledge.build import inputs_of

    pytest.importorskip("sqlglot")
    _write_spec(
        project,
        "rollup",
        {
            "version": 1,
            "sources": {"orders": "data/orders.csv"},
            "steps": [
                {
                    "id": "counted",
                    "op": "sql",
                    "inputs": ["orders"],
                    "sql": "select customer_id, count(*) as n from orders group by 1",
                }
            ],
        },
    )
    found = inputs_of(build_graph(project).graph, "rollup")
    assert found.untraced == ("n",)


def test_inputs_of_a_model_that_is_not_there_says_nothing(project):
    from portia.knowledge.build import inputs_of

    found = inputs_of(build_graph(project).graph, "no_such_model")
    assert found.tables == () and found.untraced == ()


def test_inputs_of_reads_a_model_that_reads_another_model(project):
    """A cross-spec reference is an arrow like any other, and its columns are
    named the same way."""
    from portia.knowledge.build import inputs_of

    _join_spec(project, "stg_orders")
    _write_spec(
        project,
        "mart_final",
        {
            "version": 1,
            "sources": {},
            "steps": [
                {
                    "id": "tidy",
                    "op": "normalize",
                    "input": "stg_orders",
                    "transforms": [{"column": "customer_id", "op": "strip"}],
                }
            ],
        },
    )
    found = inputs_of(build_graph(project).graph, "mart_final")
    assert [t.name for t in found.tables] == ["stg_orders"]
    assert found.tables[0].kind == MODEL


def test_a_spec_over_upper_case_columns_still_gets_its_lineage(tmp_path):
    """The bug every fixture in this file was blind to, at the level it was seen.

    Every source here is lower case and every real extract is upper case, so this
    walk traced **zero** columns on the AQN project — 0 `DERIVES_FROM` edges
    against 37 after the fix — while the app's model card drew *"columns not
    known — index this source"* about sources that were fully indexed.

    `sqlglot`'s `qualify` folds an unquoted identifier to lower case because
    DuckDB does, and `sqllineage._origins` then looked for it in a catalog that
    holds the original. See `docs/SQL_LINEAGE.md` §10.
    """
    data = tmp_path / "data"
    data.mkdir()
    with open(data / "EVENTS.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ID", "TITLE", "RANK"])
        w.writerows([["a", "One", 90], ["b", "Two", 100]])

    portia_dir = tmp_path / catalog.DEFAULT_DIR
    catalog.init_project("upper-case extract", portia_dir=portia_dir)
    catalog.index_source(data / "EVENTS.csv", portia_dir=portia_dir)
    _write_spec(
        tmp_path,
        "top_events",
        {
            "sources": {"EVENTS": "data/EVENTS.csv"},
            "steps": [
                {
                    "id": "top",
                    "op": "sql",
                    "inputs": ["EVENTS"],
                    "sql": "SELECT ID, TITLE FROM EVENTS WHERE RANK > 95",
                }
            ],
        },
    )

    from portia.knowledge.build import inputs_of

    found = inputs_of(build_graph(tmp_path).graph, "top_events")

    assert found.untraced == (), "nothing should be untraceable here"
    assert [t.name for t in found.tables] == ["EVENTS"]
    assert {(c.column, c.origin) for c in found.tables[0].columns} == {
        ("ID", "ID"),
        ("TITLE", "TITLE"),
    }, "reported in the catalog's spelling, not sqlglot's"

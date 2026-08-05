"""Reading the graph — the router, against a real Neo4j.

These need a database and skip without one (`conftest.neo4j_session` says why).
That is deliberate and it is the honest place to draw the line: what `query.py`
contains is Cypher, and a stub session that answered Cypher would be a second,
wrong implementation of Neo4j that every one of these tests would pass against.

What they pin is `docs/KNOWLEDGE_GRAPH.md` §9.1 and §7 — **ask about a table and
you get tables back**, not column pairs, because a router that returns fifty
things has not routed; and nothing comes back ranked (§6.1).
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest
import yaml

from portia import catalog
from portia.knowledge import build_graph, query, store


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """Two sources, a group, and two specs — one reading the other."""
    data = tmp_path / "data"
    data.mkdir()
    with open(data / "orders.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["order_id", "customer_id", "amount"])
        w.writerows([[1, " C1 ", 10], [2, "C2", 20]])
    with open(data / "customers.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["customer_id", "name"])
        w.writerows([["C1", "Ann"], ["C2", "Bo"]])

    portia_dir = tmp_path / catalog.DEFAULT_DIR
    catalog.init_project("a shop", portia_dir=portia_dir)
    for name in ("orders", "customers"):
        catalog.index_source(data / f"{name}.csv", portia_dir=portia_dir)
    catalog.set_group(
        "sales",
        context="what the shop exports",
        sources=["orders", "customers"],
        portia_dir=portia_dir,
    )

    specs = tmp_path / "specs"
    specs.mkdir()
    (specs / "stg_orders.yaml").write_text(
        yaml.safe_dump(
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
            sort_keys=False,
        )
    )
    (specs / "mart_orders.yaml").write_text(
        yaml.safe_dump(
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
            sort_keys=False,
        )
    )
    return tmp_path


@pytest.fixture
def filled(neo4j_session, project):
    """The project, in the database — the state after `cli.knowledge --write`."""
    store.write(build_graph(project).graph, neo4j_session)
    return neo4j_session


# --- asking about a table ---------------------------------------------------


def test_a_source_answers_with_the_models_that_read_it(filled):
    answer = query.lookup(filled, "orders")
    assert answer["table"]["kind"] == "Source"
    assert [t["name"] for t in answer["read_by"]] == ["stg_orders"]
    assert answer["reads"] == []
    assert answer["columns"] == ["amount", "customer_id", "order_id"]


def test_a_model_answers_with_what_it_reads_including_another_model(filled):
    answer = query.lookup(filled, "mart_orders")
    assert answer["table"]["kind"] == "Model"
    assert [(t["kind"], t["name"]) for t in answer["reads"]] == [
        ("Source", "customers"),
        ("Model", "stg_orders"),
    ]


def test_a_source_can_be_named_by_its_path(filled):
    """A path is what identifies a Source, and what a spec writes down."""
    assert query.lookup(filled, "data/orders.csv")["table"]["name"] == "orders"


def test_a_group_comes_back_with_its_members(filled):
    groups = query.lookup(filled, "orders")["groups"]
    assert [g["name"] for g in groups] == ["sales"]
    assert sorted(groups[0]["members"]) == ["customers", "orders"]


def test_asking_about_a_table_never_returns_column_pairs(filled):
    """§9.1 — the router narrows the field; it does not dump the neighbourhood."""
    answer = query.lookup(filled, "orders")
    assert set(answer) == {"table", "columns", "reads", "read_by", "groups", "overlaps"}
    # And nothing of the pipeline's own vocabulary: `layer` groups the project
    # canvas and says nothing about what a table is *to* another table.
    assert set(answer["table"]) == {"kind", "name", "path", "summary"}
    # `overlaps` is table-granular and counts pairs; it never lists them.
    assert all(
        set(row) <= {"kind", "name", "path", "n_measured_pairs"} for row in answer["overlaps"]
    )


def test_nothing_is_measured_yet_so_there_is_nothing_to_overlap(filled):
    """Phase B reads; phase C measures. An overlap here would be fabricated."""
    assert query.lookup(filled, "orders")["overlaps"] == []


# --- asking about a column --------------------------------------------------


def test_a_column_says_which_column_it_came_from_and_which_step_explains_it(filled):
    answer = query.lookup(filled, "stg_orders", "customer_id")
    assert answer["derives_from"] == [
        {
            "kind": "Source",
            "table": "orders",
            "path": "data/orders.csv",
            "column": "customer_id",
            "via": "normalize",
            "step": "stg_orders#cleaned",
        }
    ]


def test_lineage_walks_through_a_model_to_the_file_underneath(filled):
    """One hop is what it derives from; `origins` is where it bottoms out."""
    answer = query.lookup(filled, "mart_orders", "amount")
    assert [r["table"] for r in answer["derives_from"]] == ["stg_orders"]
    assert answer["origins"] == [
        {"kind": "Source", "table": "orders", "path": "data/orders.csv", "column": "amount"}
    ]


def test_a_shared_key_shows_both_origins(filled):
    """`coalesce(l.k, r.k)` — two edges, each with the step explaining its side."""
    edges = query.lookup(filled, "mart_orders", "customer_id")["derives_from"]
    assert {(r["table"], r["via"]) for r in edges} == {
        ("stg_orders", "join"),
        ("customers", "join"),
    }
    assert {r["table"] for r in query.lookup(filled, "mart_orders", "customer_id")["origins"]} == {
        "orders",
        "customers",
    }


def test_a_source_column_says_what_is_built_from_it(filled):
    """The forward direction — what §4.5 needs to answer 'this file changed, so what'."""
    feeds = query.lookup(filled, "orders", "amount")["feeds"]
    assert [(r["table"], r["column"]) for r in feeds] == [("stg_orders", "amount")]


def test_a_column_carries_the_catalogs_facts_and_not_a_new_measurement(filled):
    column = query.lookup(filled, "orders", "amount")["column"]
    assert column["n_distinct"] == 2
    assert column["role"] is None


# --- misses -----------------------------------------------------------------


def test_an_unknown_table_names_what_there_is(filled):
    with pytest.raises(ValueError, match="mart_orders"):
        query.lookup(filled, "nope")


def test_an_unknown_column_names_the_columns_there_are(filled):
    with pytest.raises(ValueError, match="amount"):
        query.lookup(filled, "orders", "nope")


def test_an_empty_graph_says_to_build_it_rather_than_that_the_table_is_missing(neo4j_session):
    """Two very different misses, and they ask for different next moves."""
    with pytest.raises(ValueError, match="is empty"):
        query.lookup(neo4j_session, "orders")

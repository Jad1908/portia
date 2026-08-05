"""The measured half — the only thing in `knowledge/` that is not a restatement.

Three rules from the design, and every one of them is about what happens to a
**zero**:

- the edge holds both directional numbers, not two edges (§4.3);
- it carries **why the agent asked**, so the zero keeps its hypothesis (§4.4);
- it records what it was measured against, so it can go stale without being
  deleted (§4.5) — because a deleted edge is indistinguishable from one nobody
  measured.

The end-to-end half needs a live Neo4j and skips without one.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest
import yaml

from portia import catalog
from portia.agent import handlers
from portia.knowledge import build_graph, measure, query, store
from portia.knowledge.schema import HAS_COLUMN, OVERLAPS, SOURCE, Graph, Ref, column_key

ORDERS = Ref(SOURCE, "data/orders.csv")
CUSTOMERS = Ref(SOURCE, "data/customers.csv")

MEASUREMENT = {
    "n_shared_values": 0,
    "left_coverage": 0.0,
    "right_coverage": 0.0,
    "comparable_types": True,
    # Per-side counts are re-derivable from the catalog and deliberately do not
    # go on the edge — a second copy is a second thing to go stale.
    "left": {"n_rows": 2},
    "right": {"n_rows": 2},
}


def _pair(reason: str = "both look like they identify a country") -> measure.Pair:
    return measure.Pair(ORDERS, "country_name", CUSTOMERS, "country_code", reason)


# --- the edge, with no database ---------------------------------------------


def test_the_edge_carries_the_reason_beside_the_numbers():
    """§4.4 — same measurement, and the sentence is what makes it readable.

    Without it the zero reads as a dead end; with it, as a work item. It is
    labelled as the agent's words and is never generated from the numbers.
    """
    edge = measure.overlap_edge(_pair(), MEASUREMENT)
    assert edge.properties["asked_because"] == "both look like they identify a country"
    assert edge.properties["n_shared_values"] == 0


def test_the_edge_carries_no_per_side_counts():
    """Only the measurement itself. Everything re-derivable stays derivable."""
    properties = measure.overlap_edge(_pair(), MEASUREMENT).properties
    assert set(properties) == {
        *measure.MEASURED,
        "asked_because",
        "measured_at",
        "left_fingerprint",
        "right_fingerprint",
    }


def test_the_edge_records_what_it_was_measured_against():
    """§4.5 — the fingerprints portia already computes, on both ends."""
    edge = measure.overlap_edge(
        _pair(), MEASUREMENT, left_fingerprint="12:34.5", right_fingerprint="99:1.0"
    )
    assert edge.properties["left_fingerprint"] == "12:34.5"
    assert edge.properties["right_fingerprint"] == "99:1.0"


def test_direction_is_what_says_which_coverage_is_which():
    edge = measure.overlap_edge(_pair(), MEASUREMENT)
    assert edge.start == ORDERS.column("country_name")
    assert edge.end == CUSTOMERS.column("country_code")


def test_a_measured_edge_is_written_without_a_build_stamp():
    """A stamp is what makes something a rebuild's to delete (§5.2)."""
    statements = store.measured_writes([measure.overlap_edge(_pair(), MEASUREMENT)])
    rows = [row for _, params in statements for row in params["rows"]]
    assert store.BUILD_PROPERTY not in rows[0]["properties"]
    assert all(OVERLAPS in statement for statement, _ in statements)


def test_writing_a_structural_edge_as_a_measurement_is_refused():
    graph = Graph()
    source = graph.add_node(SOURCE, "data/a.csv")
    column = graph.add_node(*_column_ref("a"))
    with pytest.raises(ValueError, match="structural"):
        store.measured_writes([graph.add_edge(HAS_COLUMN, source, column)])


def _column_ref(name: str):
    from portia.knowledge.schema import COLUMN

    return COLUMN, column_key(SOURCE, "data/a.csv", name)


# --- end to end, against a real database ------------------------------------


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """Two sources whose country columns mean the same thing and share nothing."""
    data = tmp_path / "data"
    data.mkdir()
    with open(data / "orders.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["order_id", "customer_id", "country_name"])
        w.writerows([[1, "C1", "France"], [2, "C2", "Germany"]])
    with open(data / "customers.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["customer_id", "country_code"])
        w.writerows([["C1", "FRA"], ["C2", "DEU"]])

    portia_dir = tmp_path / catalog.DEFAULT_DIR
    catalog.init_project("a shop", portia_dir=portia_dir)
    for name in ("orders", "customers"):
        catalog.index_source(data / f"{name}.csv", portia_dir=portia_dir)
    return tmp_path


@pytest.fixture
def measured(neo4j_session, project, monkeypatch):
    """The state after the copilot measured two pairs while indexing."""
    monkeypatch.chdir(project)
    handlers.measure_overlaps(
        [
            {
                "left": "orders",
                "left_column": "customer_id",
                "right": "customers",
                "right_column": "customer_id",
                "reason": "the same key by name, and orders has no customer detail",
            },
            {
                "left": "orders",
                "left_column": "country_name",
                "right": "customers",
                "right_column": "country_code",
                "reason": "both identify a country, one by name and one by code",
            },
        ]
    )
    return neo4j_session


def test_measuring_writes_both_the_numbers_and_the_reason(measured):
    answer = query.lookup(measured, "orders", "country_name")
    assert len(answer["overlaps"]) == 1
    overlap = answer["overlaps"][0]
    assert overlap["table"] == "customers" and overlap["column"] == "country_code"
    assert overlap["measured"]["n_shared_values"] == 0
    assert "one by name and one by code" in overlap["measured"]["asked_because"]


def test_a_measured_zero_is_kept_because_absence_would_mean_nobody_looked(measured):
    """§4.4 — the edge exists precisely *because* it measured nothing."""
    stored = measured.run(
        f"MATCH ()-[r:{OVERLAPS}]->() WHERE r.n_shared_values = 0 RETURN count(r) AS n"
    ).single()["n"]
    assert stored == 1


def test_the_neighbourhood_now_names_the_other_table(measured):
    """The router earns its keep only once something has been measured."""
    overlaps = query.lookup(measured, "orders")["overlaps"]
    assert [(o["name"], o["n_measured_pairs"]) for o in overlaps] == [("customers", 2)]


def test_a_fresh_measurement_is_not_stale(measured):
    assert query.lookup(measured, "orders", "customer_id")["overlaps"][0]["stale"] is False


def test_rewriting_the_file_makes_the_measurement_stale_without_deleting_it(
    measured, project, monkeypatch
):
    """§4.5 — mark, never delete. The number and its doubt arrive together."""
    monkeypatch.chdir(project)
    (project / "data" / "orders.csv").write_text(
        "order_id,customer_id,country_name\n1,C1,France\n2,C2,Germany\n3,C3,Spain\n"
    )
    catalog.index_source(project / "data" / "orders.csv", portia_dir=project / catalog.DEFAULT_DIR)
    store.write(build_graph(project).graph, measured)

    overlap = query.lookup(measured, "orders", "customer_id")["overlaps"][0]
    assert overlap["stale"] is True
    assert overlap["measured"]["n_shared_values"] == 2  # still there, still readable


def test_a_rebuild_does_not_delete_a_measurement(measured, project):
    store.write(build_graph(project).graph, measured)
    assert measured.run(f"MATCH ()-[r:{OVERLAPS}]->() RETURN count(r) AS n").single()["n"] == 2


def test_a_pair_without_a_reason_is_refused(neo4j_session, project, monkeypatch):
    """Not paperwork: without it nobody can interpret the zero a year later."""
    monkeypatch.chdir(project)
    with pytest.raises(ValueError, match="reason"):
        handlers.measure_overlaps(
            [
                {
                    "left": "orders",
                    "left_column": "customer_id",
                    "right": "customers",
                    "right_column": "customer_id",
                }
            ]
        )


def test_the_numbers_come_back_even_when_they_cannot_be_stored(project, monkeypatch):
    """§6.6 — a stopped container must not cost the caller what it just paid for."""
    monkeypatch.chdir(project)
    monkeypatch.setenv("NEO4J_URI", "bolt://localhost:1")
    result = handlers.measure_overlaps(
        [
            {
                "left": "orders",
                "left_column": "customer_id",
                "right": "customers",
                "right_column": "customer_id",
                "reason": "the same key by name",
            }
        ]
    )
    assert result["stored"] is False
    assert result["measured"][0]["n_shared_values"] == 2


def test_the_graph_is_refreshed_before_a_measurement_attaches_to_it(
    neo4j_session, project, monkeypatch
):
    """A measurement can only hang off columns the graph knows about, and a spec
    written since the last build would otherwise have none."""
    monkeypatch.chdir(project)
    specs = project / "specs"
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
                        "transforms": [{"column": "country_name", "op": "lower"}],
                    }
                ],
            },
            sort_keys=False,
        )
    )
    handlers.measure_overlaps(
        [
            {
                "left": "stg_orders",
                "left_column": "country_name",
                "right": "customers",
                "right_column": "country_code",
                "reason": "the cleaned name against the code",
            }
        ]
    )
    assert query.lookup(neo4j_session, "stg_orders", "country_name")["overlaps"]

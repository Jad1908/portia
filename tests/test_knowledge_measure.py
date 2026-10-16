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
from portia.knowledge import build_graph, measure, query, sqllineage, store
from portia.knowledge.schema import HAS_COLUMN, OVERLAPS, SOURCE, Graph, Ref, column_key

ORDERS = Ref(SOURCE, "data/orders.csv")
CUSTOMERS = Ref(SOURCE, "data/customers.csv")

MEASUREMENT = {
    "n_shared_values": 0,
    "left_coverage": 0.0,
    "right_coverage": 0.0,
    "comparable_types": True,
    "left_repeats": 1,
    "right_repeats": 1,
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
        "origin",
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
    statements = store.measured_writes([measure.overlap_edge(_pair(), MEASUREMENT)], "/tmp/p")
    rows = [row for _, params in statements for row in params["rows"]]
    assert store.BUILD_PROPERTY not in rows[0]["properties"]
    assert all(OVERLAPS in statement for statement, _ in statements)


def test_writing_a_structural_edge_as_a_measurement_is_refused():
    graph = Graph(project="/tmp/p")
    source = graph.add_node(SOURCE, "data/a.csv")
    column = graph.add_node(*_column_ref("a"))
    with pytest.raises(ValueError, match="structural"):
        store.measured_writes([graph.add_edge(HAS_COLUMN, source, column)], graph.project)


def _column_ref(name: str):
    from portia.knowledge.schema import COLUMN

    return COLUMN, column_key(SOURCE, "data/a.csv", name)


# --- end to end, against a real database ------------------------------------


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """Two sources whose country columns mean the same thing and share nothing."""
    data = tmp_path / "data"
    data.mkdir()
    with open(data / "orders.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["order_id", "customer_id", "country_name"])
        w.writerows([[1, "C1", "France"], [2, "C2", "Germany"]])
    with open(data / "customers.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["customer_id", "country_code"])
        w.writerows([["C1", "FRA"], ["C2", "DEU"]])

    portia_dir = tmp_path / catalog.DEFAULT_DIR
    catalog.init_project("a shop", portia_dir=portia_dir)
    for name in ("orders", "customers"):
        catalog.index_source(data / f"{name}.csv", portia_dir=portia_dir)
    return tmp_path


@pytest.fixture
def pid(project) -> str:
    """Which project these questions are asked of — see `schema.PROJECT`."""
    from portia.knowledge import schema

    return schema.project_id(project)


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


def test_measuring_writes_both_the_numbers_and_the_reason(measured, pid):
    answer = query.lookup(measured, "orders", "country_name", project=pid)
    (overlap,) = answer["compared_with"]
    assert overlap["table"] == "customers" and overlap["other_column"] == "country_code"
    # Flat, rather than a nested `measured` bag: the numbers, the reason and
    # what the far column looks like are one row the reader takes in at once.
    assert overlap["n_shared_values"] == 0
    assert overlap["origin"] == "measured"
    assert "one by name and one by code" in overlap["asked_because"]


def test_a_measured_zero_is_kept_because_absence_would_mean_nobody_looked(measured):
    """§4.4 — the edge exists precisely *because* it measured nothing."""
    stored = measured.run(
        f"MATCH ()-[r:{OVERLAPS}]->() WHERE r.n_shared_values = 0 RETURN count(r) AS n"
    ).single()["n"]
    assert stored == 1


def test_the_neighbourhood_hands_back_the_pairs_it_used_to_count(measured, pid):
    """The router earns its keep only once something has been measured — and it
    used to report the number two here rather than the two measurements."""
    (neighbour,) = query.lookup(measured, "orders", project=pid)["neighbours"]
    assert neighbour["name"] == "customers"
    assert [(p["column"], p["other_column"]) for p in neighbour["pairs"]] == [
        ("country_name", "country_code"),
        ("customer_id", "customer_id"),
    ]
    # The zero and the sentence that stops it reading as a dead end, together.
    zero = next(p for p in neighbour["pairs"] if p["n_shared_values"] == 0)
    assert "one by name and one by code" in zero["asked_because"]


def test_a_fresh_measurement_is_not_stale(measured, pid):
    assert (
        query.lookup(measured, "orders", "customer_id", project=pid)["compared_with"][0]["stale"]
        is False
    )


def test_rewriting_the_file_makes_the_measurement_stale_without_deleting_it(
    measured, project, monkeypatch, pid
):
    """§4.5 — mark, never delete. The number and its doubt arrive together."""
    monkeypatch.chdir(project)
    (project / "data" / "orders.csv").write_text(
        "order_id,customer_id,country_name\n1,C1,France\n2,C2,Germany\n3,C3,Spain\n",
        encoding="utf-8",
    )
    catalog.index_source(project / "data" / "orders.csv", portia_dir=project / catalog.DEFAULT_DIR)
    store.write(build_graph(project).graph, measured)

    overlap = query.lookup(measured, "orders", "customer_id", project=pid)["compared_with"][0]
    assert overlap["stale"] is True
    assert overlap["n_shared_values"] == 2  # still there, still readable


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
    fields = result["pairs"]["fields"]
    (row,) = result["pairs"]["rows"]
    assert row[fields.index("n_shared_values")] == 2
    assert row[fields.index("from")] == "query"


def test_the_graph_is_refreshed_before_a_measurement_attaches_to_it(
    neo4j_session, project, monkeypatch, pid
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
        ),
        encoding="utf-8",
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
    assert query.lookup(neo4j_session, "stg_orders", "country_name", project=pid)["compared_with"]


def test_recording_a_step_puts_the_new_table_in_the_graph(neo4j_session, project, monkeypatch, pid):
    """§5's third write moment. A spec written mid-conversation used to be
    invisible to `graph_lookup` until something else triggered a rebuild — so the
    graph could be confidently wrong about which tables exist."""
    monkeypatch.chdir(project)
    handlers.record_step(
        "specs/stg_orders.yaml",
        {
            "id": "cleaned",
            "op": "normalize",
            "input": "orders",
            "transforms": [{"column": "country_name", "op": "lower"}],
        },
    )

    answer = query.lookup(neo4j_session, "stg_orders", project=pid)
    assert answer["table"]["kind"] == "Model"
    assert [n["name"] for n in answer["neighbours"] if n["built_from"]] == ["orders"]
    # …and the lineage of the table it just built is there too.
    assert query.lookup(neo4j_session, "stg_orders", "country_name", project=pid)["upstream"] == [
        {
            "hops": 1,
            "kind": "Source",
            "table": "orders",
            "path": "data/orders.csv",
            "column": "country_name",
            "via": "normalize",
            "step": "stg_orders#cleaned",
        }
    ]


def test_a_step_is_still_recorded_when_the_graph_cannot_be_reached(project, monkeypatch):
    """The step is on disk before this runs; failing the write would lose it."""
    monkeypatch.chdir(project)
    monkeypatch.setenv("NEO4J_URI", "bolt://localhost:1")
    written = handlers.record_step(
        "specs/stg_orders.yaml",
        {
            "id": "cleaned",
            "op": "normalize",
            "input": "orders",
            "transforms": [{"column": "country_name", "op": "lower"}],
        },
    )
    assert written["step_id"] == "cleaned"
    assert written["graph"].startswith("not updated")
    assert (project / "specs" / "stg_orders.yaml").exists()


def test_a_measurement_that_could_not_be_attached_is_not_reported_as_stored(
    neo4j_session, project, monkeypatch
):
    """`docs/SQL_LINEAGE.md` §1.5 — the one write that could lose what it cost.

    The write matches both ends, so a pair on a column the graph does not hold
    is dropped by Neo4j without an error. This used to come back `stored: True`,
    which is worse than the zero §4.4 is about: there, nobody had spent
    anything. A model whose columns could not be read is how you get here, and
    an absent parser is the reproducible way to make one.
    """
    monkeypatch.chdir(project)
    monkeypatch.setattr(sqllineage, "_sqlglot", _no_parser)
    (project / "specs").mkdir(exist_ok=True)
    (project / "specs" / "agg.yaml").write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "sources": {"orders": "data/orders.csv"},
                "steps": [
                    {
                        "id": "picked",
                        "op": "sql",
                        "inputs": ["orders"],
                        "sql": "SELECT customer_id, country_name FROM orders",
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    answer = handlers.measure_overlaps(
        [
            {
                "left": "agg",
                "left_column": "country_name",
                "right": "customers",
                "right_column": "country_code",
                "reason": "both name a country, one spelled out and one as a code",
            }
        ]
    )

    # The numbers were paid for and they are still handed back.
    fields = answer["pairs"]["fields"]
    assert answer["pairs"]["rows"][0][fields.index("n_shared_values")] == 0
    assert answer["stored"] is False
    assert "0 of 1" in answer["not_stored_because"]


def _no_parser():
    raise sqllineage.LineageUnreadable(sqllineage.NO_PARSER, "sqlglot is not installed")


def test_write_measured_counts_what_the_database_kept(neo4j_session):
    """Not what it was handed — the distinction the test above exists for."""
    edge = measure.overlap_edge(_pair(), MEASUREMENT)
    assert store.write_measured([edge], neo4j_session, "/tmp/p") == 0  # neither end exists yet


# --- the graph first, and the join check's measurement ----------------------


def test_a_pair_measured_once_comes_back_from_the_graph_the_second_time(
    measured, project, monkeypatch, pid
):
    """`measure_overlaps` reads before it measures (2026-10-13): the same pair
    asked again costs no query and says when it was measured."""
    monkeypatch.chdir(project)
    again = handlers.measure_overlaps(
        [
            {
                "left": "orders",
                "left_column": "customer_id",
                "right": "customers",
                "right_column": "customer_id",
                "reason": "asked again",
            },
            # The same measurement asked from the other end: found either way
            # round, its coverages turned to face the request.
            {
                "left": "customers",
                "left_column": "country_code",
                "right": "orders",
                "right_column": "country_name",
                "reason": "asked again, the other way",
            },
        ]
    )
    assert (again["n_from_graph"], again["n_measured"]) == (2, 0)
    fields = again["pairs"]["fields"]
    assert {row[fields.index("from")] for row in again["pairs"]["rows"]} == {"graph"}
    # Nothing was written, so the reasons stored are the first ones.
    (pair,) = query.lookup(measured, "orders", "country_name", project=pid)["compared_with"]
    assert "one by name and one by code" in pair["asked_because"]


def _joined(session) -> list[dict]:
    """Every join record in the graph, as plain rows."""
    rows = session.run(
        "MATCH (a)-[r:JOINED]->(b) RETURN a.name AS left, b.name AS right, "
        "r.left_columns AS left_columns, r.right_columns AS right_columns, "
        "r.relationship AS relationship, r.origin AS origin, r.asked_because AS reason, "
        "r.found_in AS found_in, r.n_shared_keys AS n_shared_keys ORDER BY origin, left"
    )
    return [dict(r) for r in rows]


def test_a_join_check_lands_in_the_graph_as_the_join_it_checked(
    neo4j_session, project, monkeypatch
):
    """25 join checks and 5 measurements in the logs, and the join checks kept nothing."""
    monkeypatch.chdir(project)
    found = handlers.join_findings(
        "orders", "customers", keys=["customer_id"], reason="orders should name a customer"
    )
    assert found["graph"] == {"kept": True}
    (record,) = _joined(neo4j_session)
    assert (record["left"], record["right"]) == ("orders", "customers")
    assert record["left_columns"] == record["right_columns"] == ["customer_id"]
    assert (record["origin"], record["reason"]) == ("join_check", "orders should name a customer")
    assert record["n_shared_keys"] == found["report"]["overlap"]["n_shared_keys"]
    assert record["relationship"] == found["report"]["relationship"]


def test_a_join_check_on_a_model_the_parser_cannot_read_is_kept(
    neo4j_session, project, monkeypatch
):
    """The real case: a recorded unpivot, then a join check between it and a source.

    A join record is between tables, so the model needs no traced columns: it
    used to need the column node the measurement attached to.
    """
    monkeypatch.chdir(project)
    handlers.record_step(
        "specs/long_orders.yaml",
        {
            "id": "unpivot",
            "op": "sql",
            "inputs": ["orders"],
            "sql": "SELECT order_id, field, value FROM (UNPIVOT (SELECT * FROM orders) "
            "ON COLUMNS(* EXCLUDE (order_id)) INTO NAME field VALUE value)",
            "rationale": "One row per order and field.",
        },
    )
    found = handlers.join_findings(
        "long_orders",
        "customers",
        left_on=["value"],
        right_on=["customer_id"],
        reason="the long table's values include each order's customer",
    )
    assert found["graph"] == {"kept": True}
    (record,) = _joined(neo4j_session)
    assert (record["left"], record["right"], record["n_shared_keys"]) == (
        "long_orders",
        "customers",
        2,
    )


def test_a_join_check_is_not_a_comparison(neo4j_session, project, monkeypatch):
    """Two kinds of record: a join that was done, and two columns compared. Measuring
    the pair after a join check measures it, and keeps both."""
    monkeypatch.chdir(project)
    handlers.join_findings("orders", "customers", keys=["customer_id"], reason="the key")
    again = handlers.measure_overlaps(
        [
            {
                "left": "orders",
                "left_column": "customer_id",
                "right": "customers",
                "right_column": "customer_id",
                "reason": "the key, measured",
            }
        ]
    )
    assert (again["n_from_graph"], again["n_measured"]) == (0, 1)
    assert len(_joined(neo4j_session)) == 1


# --- joins kept from recorded steps and kept findings (2026-10-15) ---------------------------


_JOIN_STEP = {
    "id": "with_customers",
    "op": "join",
    "left": "orders",
    "right": "customers",
    "keys": ["customer_id"],
    "how": "left",
    "rationale": "Every order with the customer it names.",
}


def test_recording_a_step_that_joins_keeps_the_join(neo4j_session, project, monkeypatch):
    monkeypatch.chdir(project)
    card = handlers.record_step("specs/orders_customers.yaml", dict(_JOIN_STEP))
    assert card["joins"]["kept"] == ["orders JOIN customers ON customer_id"]
    (record,) = _joined(neo4j_session)
    assert (record["left"], record["right"], record["origin"]) == ("orders", "customers", "step")
    assert record["found_in"] == "specs/orders_customers.yaml#with_customers"
    assert record["reason"] == "Every order with the customer it names."


def test_a_step_superseded_by_one_that_no_longer_joins_takes_its_join_with_it(
    neo4j_session, project, monkeypatch
):
    monkeypatch.chdir(project)
    handlers.record_step("specs/orders_customers.yaml", dict(_JOIN_STEP))
    replacement = {
        "id": "orders_only",
        "op": "sql",
        "inputs": ["orders"],
        "sql": "SELECT * FROM orders",
    }
    card = handlers.record_step(
        "specs/orders_customers.yaml", replacement, supersedes="with_customers"
    )
    assert "joins" not in card
    assert _joined(neo4j_session) == []


def test_a_sql_step_keeps_a_join_between_its_inputs_and_says_why_not_one_inside_it(
    neo4j_session, project, monkeypatch
):
    monkeypatch.chdir(project)
    step = {
        "id": "joined",
        "op": "sql",
        "inputs": ["orders", "customers"],
        "sql": "WITH o AS (SELECT * FROM orders) "
        "SELECT o.order_id, c.country_code FROM o JOIN customers c ON o.customer_id = c.customer_id",
    }
    card = handlers.record_step("specs/inner.yaml", step)
    assert card["joins"]["kept"] == []
    (missed,) = card["joins"]["not_kept"]
    assert missed["why"] == "side_is_a_step"

    step["id"], step["sql"] = (
        "joined",
        "SELECT o.order_id, c.country_code FROM orders o "
        "JOIN customers c ON o.customer_id = c.customer_id",
    )
    card = handlers.record_step("specs/direct.yaml", step)
    assert len(card["joins"]["kept"]) == 1
    (record,) = _joined(neo4j_session)
    assert record["found_in"] == "specs/direct.yaml#joined"


def test_keeping_a_finding_keeps_the_joins_in_its_queries(neo4j_session, project, monkeypatch):
    from portia import runlog
    from portia.agent import events

    monkeypatch.chdir(project)
    log = runlog.start(kind=runlog.CHAT, portia_dir=str(project / catalog.DEFAULT_DIR))
    log.event(
        events.Event(
            events.TOOL_CALL,
            {
                "name": f"{events.TOOL_PREFIX}query_data",
                "id": "toolu_0",
                "input": {
                    "question": "how many orders have a known customer?",
                    "sql": "SELECT count(*) FROM orders o "
                    "JOIN customers c ON o.customer_id = c.customer_id",
                    "inputs": ["orders", "customers"],
                },
            },
        )
    )
    log.event(
        events.Event(
            events.TOOL_RESULT, {"id": "toolu_0", "text": '{"n_rows":1}', "is_error": False}
        )
    )
    kept = handlers.record_finding(
        "Do orders name known customers?",
        "All of them.",
        "Join on customer_id.",
        ["orders", "customers"],
        [1],
    )
    assert len(kept["joins"]["kept"]) == 1
    (record,) = _joined(neo4j_session)
    assert (record["origin"], record["reason"]) == ("finding", "Do orders name known customers?")
    assert record["found_in"] == kept["finding"]


def _stale(card: dict) -> list:
    """Each pair's ``stale`` on a neighbourhood card, as the copilot reads it."""
    (row,) = card["neighbours"]["rows"]
    fields = card["compared_fields"]
    return [pair[fields.index("stale")] for pair in row[-1]]


def test_a_file_edited_since_the_rebuild_makes_its_measurements_stale(measured, project):
    """Not re-indexed and not rebuilt: the measurements on it used to read as current,
    because a table's fingerprint was the catalog's record and only a rebuild set it."""
    assert _stale(handlers.graph_lookup("orders")) == [False, False]
    _add_order(project)
    assert _stale(handlers.graph_lookup("orders")) == [True, True]


def test_measuring_again_after_the_file_changed_measures_rather_than_reuses(measured, project):
    """`measure_overlaps` answered from the graph against the old fingerprint."""
    pair = {
        "left": "orders",
        "left_column": "customer_id",
        "right": "customers",
        "right_column": "customer_id",
        "reason": "the same key by name",
    }
    again = handlers.measure_overlaps([pair])
    assert again["n_from_graph"] == 1
    _add_order(project)
    after = handlers.measure_overlaps([pair])
    assert after["n_from_graph"] == 0 and after["n_measured"] == 1


def _add_order(project: Path) -> None:
    """One more row, written the way the fixture wrote the others."""
    with open(project / "data" / "orders.csv", "a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow([3, "C3", "Spain"])


# --- a join record (2026-10-15) ------------------------------------------------------------


REPORT = {
    "keys": {"left": ["site_id", "timestamp"], "right": ["site_id", "timestamp"]},
    "relationship": "many:1",
    "key_dtype_match": True,
    "overlap": {"n_shared_keys": 17544, "left_coverage": 1.0, "right_coverage": 0.98},
    "left": {"max_key_multiplicity": 7},
    "right": {"max_key_multiplicity": 1},
}


def test_a_join_record_carries_its_keys_its_shape_and_who_made_it():
    from portia.knowledge.schema import JOINED, MODEL, SOURCE, Ref

    edge = measure.join_edge(
        Ref(MODEL, "stg_readings"),
        Ref(SOURCE, "data/weather.csv"),
        REPORT,
        origin=measure.ORIGIN_STEP,
        asked_because="weather by site and hour",
        found_in="specs/mart.yaml#with_weather",
    )
    assert edge.kind == JOINED
    p = edge.properties
    assert (p["left_columns"], p["right_columns"]) == (["site_id", "timestamp"],) * 2
    assert (p["relationship"], p["left_repeats"], p["right_repeats"]) == ("many:1", 7, 1)
    assert (p["origin"], p["found_in"]) == ("step", "specs/mart.yaml#with_weather")
    with pytest.raises(ValueError, match="origin"):
        measure.join_edge(edge.start, edge.end, REPORT, origin="measured", asked_because="x")


def test_a_join_record_read_from_its_other_end_is_turned_whole():
    """Columns, repeats, coverage and the relationship all face the table you stand on."""
    seen = measure.seen_from(measure.join_numbers(REPORT), from_start=False)
    assert seen["relationship"] == "1:many"
    assert (seen["left_repeats"], seen["right_repeats"]) == (1, 7)
    assert (seen["left_coverage"], seen["right_coverage"]) == (0.98, 1.0)


def test_removing_a_spec_forgets_what_was_measured_about_its_model(
    neo4j_session, project, monkeypatch
):
    """A number about a table nobody builds is about nothing: the model's join records
    and the joins its steps made go with it, and a source's measurements stay."""
    from portia.cli import remove_spec

    monkeypatch.chdir(project)
    handlers.record_step("specs/orders_customers.yaml", dict(_JOIN_STEP))
    handlers.join_findings(
        "orders_customers", "customers", keys=["customer_id"], reason="back to customers"
    )
    handlers.join_findings("orders", "customers", keys=["customer_id"], reason="the key")
    assert len(_joined(neo4j_session)) == 3

    remove_spec.remove(remove_spec.plan(["orders_customers"], project), project)
    (left,) = _joined(neo4j_session)
    assert (left["left"], left["origin"]) == ("orders", "join_check")
    gone = neo4j_session.run("MATCH (m:Model {name: 'orders_customers'}) RETURN count(m) AS n")
    assert gone.single()["n"] == 0


def test_every_reason_a_join_is_not_kept_is_explained_to_the_agent():
    from portia.agent import prompts
    from portia.checks import sql_joins

    tools = Path(prompts.__file__).parent / "tools"
    said = "".join(
        (tools / f"{name}.md").read_text(encoding="utf-8")
        for name in ("join_findings", "record_step")
    )
    codes = [
        *handlers.JOIN_NOT_KEPT,
        sql_joins.SIDE_NOT_NAMED,
        sql_joins.CONDITION_NOT_EQUALITIES,
        sql_joins.COLUMN_SIDE_UNCLEAR,
        sql_joins.KIND_NOT_MEASURED,
        sql_joins.NOT_PARSED,
        sql_joins.NO_PARSER,
    ]
    assert [code for code in codes if f"`{code}`" not in said] == []


def test_a_finding_whose_queries_make_one_join_twice_measures_and_keeps_it_once(
    neo4j_session, project, monkeypatch
):
    from portia import runlog
    from portia.agent import events

    monkeypatch.chdir(project)
    log = runlog.start(kind=runlog.CHAT, portia_dir=str(project / catalog.DEFAULT_DIR))
    for i in range(2):
        log.event(
            events.Event(
                events.TOOL_CALL,
                {
                    "name": f"{events.TOOL_PREFIX}query_data",
                    "id": f"toolu_{i}",
                    "input": {
                        "question": f"question {i}",
                        "sql": "SELECT count(*) FROM orders o "
                        "JOIN customers c ON o.customer_id = c.customer_id",
                        "inputs": ["orders", "customers"],
                    },
                },
            )
        )
        log.event(
            events.Event(events.TOOL_RESULT, {"id": f"toolu_{i}", "text": "{}", "is_error": False})
        )
    kept = handlers.record_finding("q?", "a.", "so.", ["orders"], [1, 2])
    assert len(kept["joins"]["kept"]) == 1
    assert len(_joined(neo4j_session)) == 1

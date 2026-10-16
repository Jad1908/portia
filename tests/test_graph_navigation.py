"""The graph as the agent's map between tables — everything that needs no database.

`knowledge/query.py` is Cypher, and whether the Cypher is right is
`test_knowledge_query.py`'s, against a real Neo4j (`conftest.neo4j_session`
says why a stub would be a second, wrong Neo4j). What is decided in Python is
tested here: how a measured pair faces the table asked about, how neighbours
are gathered, how a route is searched and ordered, how each answer is cut into
a card and the cut said, and what `measure_overlaps` and `join_findings` do
around the store. The store's own calls are replaced with recorders where a
test needs the graph to be up; nothing here answers a query as Neo4j would.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import contextmanager
from pathlib import Path

import pytest

from portia import catalog
from portia.agent import graphcards, handlers
from portia.catalog import index_source, init_project
from portia.fixtures import sales_customers, sales_orders
from portia.knowledge import build_graph, measure, query, store
from portia.knowledge.query import Link, TableId

# --- measure: the edge, read from either end --------------------------------


def test_reading_an_edge_from_its_far_end_swaps_both_coverages_and_fingerprints():
    edge = {
        "left_coverage": 0.9,
        "right_coverage": 0.4,
        "left_fingerprint": "a",
        "right_fingerprint": "b",
        "n_shared_values": 7,
    }
    assert measure.seen_from(edge, from_start=True) == edge
    far = measure.seen_from(edge, from_start=False)
    assert (far["left_coverage"], far["right_coverage"]) == (0.4, 0.9)
    assert (far["left_fingerprint"], far["right_fingerprint"]) == ("b", "a")
    assert far["n_shared_values"] == 7


def test_a_stored_measurement_is_current_only_when_both_fingerprints_still_match():
    seen = {"left_fingerprint": "1:2", "right_fingerprint": "3:4"}
    assert measure.still_current(seen, "1:2", "3:4") is True
    assert measure.still_current(seen, "1:2", "3:5") is False
    # Cannot tell is not fine (§4.5): a caller reusing numbers treats it as moved.
    assert measure.still_current(seen, "1:2", None) is None
    assert measure.still_current({"left_fingerprint": "1:2"}, "1:2", "3:4") is None


def test_a_join_report_is_the_same_numbers_under_other_names():
    report = {
        "key_dtype_match": False,
        "overlap": {"n_shared_keys": 3, "left_coverage": 0.5, "right_coverage": 0.25},
        "left": {"max_key_multiplicity": 1},
        "right": {"max_key_multiplicity": 19},
    }
    assert measure.from_join_report(report) == {
        "n_shared_values": 3,
        "left_coverage": 0.5,
        "right_coverage": 0.25,
        "comparable_types": False,
        "left_repeats": 1,
        "right_repeats": 19,
    }


def test_an_edge_says_which_tool_took_it_from_a_closed_set():
    from portia.knowledge.schema import SOURCE, Ref

    pair = measure.Pair(Ref(SOURCE, "a.csv"), "x", Ref(SOURCE, "b.csv"), "y", "same key")
    assert measure.overlap_edge(pair, {}).properties["origin"] == "measured"
    joined = measure.overlap_edge(pair, {}, origin=measure.ORIGIN_JOIN_CHECK)
    assert joined.properties["origin"] == "join_check"
    with pytest.raises(ValueError, match="origin"):
        measure.overlap_edge(pair, {}, origin="guessed")


# --- query: a pair facing the table asked about -----------------------------


def _pair_row(*, from_here: bool, **measured) -> dict:
    return {
        "column": "customer_id",
        "other_column": "id",
        "near": {"n_distinct": 5, "role": "identifier", "null_rate": 0.1},
        "far": {"n_distinct": 9, "top": "C1", "top_freq": 3},
        "measured": {"left_coverage": 1.0, "right_coverage": 0.5, **measured},
        "from_here": from_here,
        "stale": False,
    }


def test_a_pair_reads_coverage_from_the_side_of_the_table_asked_about():
    """``coverage`` is this table's share whichever way the edge was measured."""
    forward = query.pair_from_row(_pair_row(from_here=True))
    assert (forward["coverage"], forward["other_coverage"]) == (1.0, 0.5)
    backward = query.pair_from_row(_pair_row(from_here=False))
    assert (backward["coverage"], backward["other_coverage"]) == (0.5, 1.0)


def test_a_pair_measured_before_origin_existed_says_measured():
    assert query.pair_from_row(_pair_row(from_here=True))["origin"] == "measured"
    checked = query.pair_from_row(_pair_row(from_here=True, origin="join_check"))
    assert checked["origin"] == "join_check"


def test_each_end_of_a_pair_carries_only_the_facts_that_read_a_zero():
    pair = query.pair_from_row(_pair_row(from_here=True))
    assert pair["column_facts"] == {"role": "identifier", "n_distinct": 5}
    assert pair["other_facts"] == {"n_distinct": 9, "top": "C1", "top_freq": 3}


def test_a_turned_pair_swaps_columns_coverages_and_facts():
    pair = query.pair_from_row(_pair_row(from_here=True))
    back = query.turned(pair)
    assert (back["column"], back["other_column"]) == ("id", "customer_id")
    assert (back["coverage"], back["other_coverage"]) == (0.5, 1.0)
    assert back["column_facts"] == pair["other_facts"]


# --- query: a join facing the table asked about -----------------------------


def _join_row(*, from_here: bool, **measured) -> dict:
    """A `JOINED` record as `query._JOIN_RETURN` returns it."""
    return {
        "measured": {
            "left_columns": ["site_id", "hour"],
            "right_columns": ["site_id", "timestamp"],
            "relationship": "many:1",
            "left_repeats": 7,
            "right_repeats": 1,
            "left_coverage": 1.0,
            "right_coverage": 0.5,
            "n_shared_keys": 30,
            "origin": "step",
            "asked_because": "weather by site and hour",
            "found_in": "specs/m.yaml#w",
            **measured,
        },
        "from_here": from_here,
        "stale": False,
    }


def test_a_join_reads_its_columns_repeats_and_relationship_from_this_side():
    near = query.join_from_row(_join_row(from_here=True))
    assert (near["columns"], near["other_columns"]) == (
        ["site_id", "hour"],
        ["site_id", "timestamp"],
    )
    assert (near["relationship"], near["repeats"], near["other_repeats"]) == ("many:1", 7, 1)
    far = query.join_from_row(_join_row(from_here=False))
    assert (far["columns"], far["relationship"]) == (["site_id", "timestamp"], "1:many")
    assert (far["repeats"], far["coverage"]) == (1, 0.5)
    assert query.turned_join(near) == {**far, "stale": False}


# --- query: gathering neighbours --------------------------------------------


def _row(kind: str, name: str, path: str | None = None, **extra) -> dict:
    return {"kind": kind, "name": name, "path": path, **extra}


def test_neighbours_a_pipeline_went_through_come_first_then_the_rest_by_name():
    """By kind of link, never by a number: built from, building or joined first."""
    reads = [
        _row("Model", "mart", link="read_by"),
        _row("Source", "zones", "data/zones.csv", link="reads"),
    ]
    grouped = [
        _row("Source", "customers", "data/customers.csv", group_name="sales"),
        _row("Source", "customers", "data/customers.csv", group_name="crm"),
        _row("Source", "returns", "data/returns.csv", group_name="sales"),
    ]
    pairs = [
        {**_row("Source", "customers", "data/customers.csv"), **_pair_row(from_here=True)},
    ]
    joins = [{**_row("Source", "weather", "data/weather.csv"), **_join_row(from_here=True)}]
    neighbours = query.gather_neighbours(reads, grouped, pairs, joins)

    assert [n["name"] for n in neighbours] == ["mart", "weather", "zones", "customers", "returns"]
    mart, weather, zones, customers = neighbours[:4]
    assert mart["builds"] and not mart["built_from"]
    assert zones["built_from"]
    assert weather["joins"][0]["columns"] == ["site_id", "hour"]
    assert customers["groups"] == ["crm", "sales"]
    assert [p["column"] for p in customers["pairs"]] == ["customer_id"]


def test_a_source_and_a_model_of_one_name_stay_two_neighbours():
    reads = [
        _row("Model", "orders", link="read_by"),
        _row("Source", "orders", "data/orders.csv", link="reads"),
    ]
    neighbours = query.gather_neighbours(reads, [], [])
    assert [(n["kind"], n["name"]) for n in neighbours] == [
        ("Model", "orders"),
        ("Source", "orders"),
    ]


# --- query: routes, searched in Python --------------------------------------

A = TableId("a", "Source", "data/a.csv")
B = TableId("b", "Source", "data/b.csv")
C = TableId("c", "Model", "c")
D = TableId("d", "Source", "data/d.csv")
E = TableId("e", "Source", "data/e.csv")


def _joined(start: TableId, end: TableId, column: str = "k", other: str = "k") -> Link:
    join = query.join_from_row(
        _join_row(
            from_here=True,
            left_columns=[column],
            right_columns=[other],
            left_coverage=0.9,
            right_coverage=0.6,
        )
    )
    return Link(start, end, join=join)


def _names(found: list[query.Route]) -> list[list[str]]:
    return [[t.name for t in route.tables] for route in found]


def test_routes_come_fewest_steps_first_then_by_name():
    links = [
        _joined(A, D),
        _joined(A, B),
        _joined(B, D),
        Link(C, A, reads=True),
        Link(C, D, reads=True),
    ]
    found, n = query.routes(links, A, D)
    assert _names(found) == [["a", "d"], ["a", "b", "d"], ["a", "c", "d"]]
    assert n == 3


def test_a_route_never_visits_a_table_twice_and_stops_at_the_step_limit():
    chain = [_joined(A, B), _joined(B, C), _joined(C, D), _joined(D, E)]
    assert _names(query.routes(chain, A, D)[0]) == [["a", "b", "c", "d"]]
    assert query.routes(chain, A, E) == ([], 0), "four steps is past ROUTE_HOPS"
    assert _names(query.routes(chain, A, E, max_steps=4)[0]) == [["a", "b", "c", "d", "e"]]


def test_the_limit_cuts_the_list_and_the_count_says_how_many_there_were():
    links = [_joined(A, t) for t in (B, C, D)] + [_joined(t, E) for t in (B, C, D)]
    found, n = query.routes(links, A, E, limit=2)
    assert _names(found) == [["a", "b", "e"], ["a", "c", "e"]]
    assert n == 3


def test_two_tables_joined_several_ways_are_one_step_carrying_all_of_them():
    links = [_joined(A, B, "z", "z"), _joined(A, B, "id", "a_id"), Link(B, A, reads=False)]
    (route,), n = query.routes(links, A, B)
    assert n == 1
    assert [j["columns"] for j in route.steps[0].joins] == [["id"], ["z"]]


def test_a_step_walked_against_the_join_is_turned_and_says_which_is_built_from_which():
    links = [_joined(B, A, "b_col", "a_col"), Link(C, B, reads=True)]
    (route,), _ = query.routes(links, A, C)
    first, second = route.steps
    (join,) = first.joins
    assert (join["columns"], join["other_columns"]) == (["a_col"], ["b_col"])
    assert (join["coverage"], join["other_coverage"]) == (0.6, 0.9)
    assert (join["relationship"], join["repeats"]) == ("1:many", 1)
    # The model `c` is built from `b`, so from `b` to `c` the step builds it.
    assert second.builds and not second.built_from


def test_a_table_has_no_route_to_itself():
    assert query.routes([_joined(A, B)], A, A) == ([], 0)


def test_a_link_row_keys_a_source_by_path_and_a_model_by_name():
    row = {
        "link": "joined",
        "a_kind": "Source",
        "a_name": "orders",
        "a_path": "data/orders.csv",
        "b_kind": "Model",
        "b_name": "mart",
        "b_path": None,
        **_join_row(from_here=True),
    }
    link = query.link_from_row(row)
    assert link.start == TableId("orders", "Source", "data/orders.csv")
    assert link.end == TableId("mart", "Model", "mart")
    assert link.join is not None and link.join["other_columns"] == ["site_id", "timestamp"]
    assert query.link_from_row({**row, "link": "reads"}).reads


# --- graphcards: every cut said ---------------------------------------------


def _header(name: str) -> dict:
    return {"kind": "Source", "name": name, "path": f"data/{name}.csv", "n_columns": 4}


def _pair(column: str) -> dict:
    return query.pair_from_row({**_pair_row(from_here=True), "column": column})


def _join(column: str) -> dict:
    return query.join_from_row(_join_row(from_here=True, left_columns=[column]))


def _neighbour(name: str, n_pairs: int, n_joins: int = 0) -> dict:
    return {
        "kind": "Source",
        "name": name,
        "path": f"data/{name}.csv",
        "built_from": False,
        "builds": False,
        "groups": [],
        "joins": [_join(f"j{i}") for i in range(n_joins)],
        "pairs": [_pair(f"c{i}") for i in range(n_pairs)],
    }


def _neighbourhood(neighbours: list[dict], **extra) -> dict:
    return {"table": _header("hub"), "neighbours": neighbours, "groups": [], **extra}


def test_the_card_names_the_row_fields_once_before_the_neighbours():
    card = graphcards.card(_neighbourhood([_neighbour("n1", 1, 1), _neighbour("n2", 0)]))
    assert list(card)[:4] == ["table", "join_fields", "compared_fields", "neighbours"]
    rows = card["neighbours"]["rows"]
    fields = graphcards.NEIGHBOUR_FIELDS
    assert rows[0][fields.index("joins")][0][0] == ["j0"]
    assert rows[0][fields.index("compared")][0][0] == "c0"
    assert rows[1][fields.index("n_compared")] == 0
    assert "more" not in card["neighbours"] and "rows_more" not in card
    json.dumps(card)


def test_a_card_with_nothing_measured_carries_no_row_fields():
    card = graphcards.card(_neighbourhood([_neighbour("n1", 0)]))
    assert not {"join_fields", "compared_fields", "groups"} & set(card)


def test_when_the_rows_do_not_all_fit_every_neighbour_shows_the_same_number_joins_first(
    monkeypatch,
):
    """Cut by size, evenly, joins before comparisons: never by coverage (§6.1)."""
    monkeypatch.setattr(graphcards, "CARD_ROWS", 8)
    card = graphcards.card(_neighbourhood([_neighbour(f"n{i}", 3, 1) for i in range(4)]))
    fields = graphcards.NEIGHBOUR_FIELDS
    rows = card["neighbours"]["rows"]
    assert [(len(r[fields.index("joins")]), len(r[fields.index("compared")])) for r in rows] == [
        (1, 1)
    ] * 4
    assert "4 of these links" in card["rows_more"]


def test_every_neighbour_shown_has_room_for_a_row():
    """At 50 neighbours against 40 rows, every neighbour showed none (2026-10-15)."""
    neighbours = [_neighbour(f"n{i:02}", 3) for i in range(60)]
    card = graphcards.card(_neighbourhood(neighbours))
    index = graphcards.NEIGHBOUR_FIELDS.index("compared")
    assert all(len(row[index]) >= 1 for row in card["neighbours"]["rows"])
    assert card["neighbours"]["n"] == 60 and "more" in card["neighbours"]


def test_neighbours_past_the_cap_are_counted_and_the_narrower_question_named(monkeypatch):
    monkeypatch.setattr(graphcards, "NEIGHBOURS", 2)
    card = graphcards.card(_neighbourhood([_neighbour(f"n{i}", 0) for i in range(3)]))
    assert card["neighbours"]["n"] == 3 and card["neighbours"]["shown"] == "1-2"
    assert "1 more of the 3" in card["neighbours"]["more"]
    assert 'neighbour="<name>"' in card["neighbours"]["more"]


def test_groups_are_a_list_cut_like_any_other():
    group = {"name": "sales", "context": "what the shop exports", "n_members": 3}
    card = graphcards.card(_neighbourhood([], groups=[group]))
    assert card["groups"]["rows"] == [["sales", "what the shop exports", 3]]


def test_between_two_tables_every_join_and_each_comparison_with_both_columns_facts(monkeypatch):
    monkeypatch.setattr(graphcards, "PAIRS", 1)
    answer = {
        "table": _header("orders"),
        "neighbour": _header("customers"),
        "built_from": False,
        "builds": True,
        "groups": ["sales"],
        "joins": [_join("customer_id")],
        "pairs": [_pair("a"), _pair("b")],
    }
    card = graphcards.card(answer)
    assert card["joins"]["fields"][-2:] == ["found_in", "measured_at"]
    assert card["joins"]["rows"][0][0] == ["customer_id"]
    assert card["compared"]["fields"][-2:] == ["column_facts", "other_facts"]
    assert card["compared"]["rows"][0][-1] == {"n_distinct": 9, "top": "C1", "top_freq": 3}
    assert "between `orders` and `customers`" in card["compared"]["more"]


def test_a_column_card_lists_its_lineage_its_joins_and_cuts_each_list(monkeypatch):
    monkeypatch.setattr(graphcards, "COLUMN_ROWS", 1)
    walked = {"hops": 1, "kind": "Source", "table": "orders", "path": None, "column": "k"}
    answer = {
        "column": {"table": "mart", "name": "k"},
        "upstream": [{**walked, "via": "join", "step": "mart#j"}, {**walked, "hops": 2}],
        "downstream": [],
        "origins": [{"kind": "Source", "table": "orders", "path": "o.csv", "column": "k"}],
        "joins": [{"table": "weather", "kind": "Source", **_join("k")}],
        "compared_with": [],
    }
    card = graphcards.card(answer)
    assert card["upstream"]["rows"] == [[1, "orders", "Source", "k", "join", "mart#j"]]
    assert "1 more of 2 columns" in card["upstream"]["more"]
    assert card["origins"]["rows"] == [["orders", "Source", "k", None]]
    assert card["joins"]["rows"][0][:3] == ["weather", "Source", ["k"]]
    assert card["downstream"] == {
        "n": 0,
        "shown": "none",
        "fields": card["downstream"]["fields"],
        "rows": [],
    }


def _route(n_joins: int) -> dict:
    return {
        "tables": ["a", "c", "b"],
        "steps": [
            {"built_from": False, "builds": True, "joins": []},
            {
                "built_from": False,
                "builds": False,
                "joins": [_join(f"j{i}") for i in range(n_joins)],
            },
        ],
    }


def test_a_route_card_says_each_steps_links_and_cuts_its_joins(monkeypatch):
    monkeypatch.setattr(graphcards, "STEP_JOINS", 1)
    answer = {"table": _header("a"), "to": _header("b"), "routes": [_route(2)], "n_routes": 3}
    card = graphcards.card(answer)
    (row,) = card["routes"]["rows"]
    assert row[:2] == [2, ["a", "c", "b"]]
    first, second = row[2]
    assert first == {"builds": True}
    assert second["n_joins"] == 2 and len(second["joins"]) == 1
    assert card["join_fields"] == list(graphcards.ROUTE_JOIN_FIELDS)
    assert "asked_because" not in card["join_fields"]
    assert "2 more of the 3 routes" in card["routes"]["more"]
    assert "rows_more" in card


def test_no_route_is_an_empty_list_with_what_was_compared_on_either_table():
    compared = [{"table": "a", "other_table": "x", **_pair("k")}]
    answer = {
        "table": _header("a"),
        "to": _header("b"),
        "routes": [],
        "n_routes": 0,
        "compared": compared,
    }
    card = graphcards.card(answer)
    assert card["routes"]["n"] == 0 and card["routes"]["shown"] == "none"
    assert card["compared"]["rows"][0][:4] == ["a", "k", "x", "id"]


def test_a_receipt_lists_a_line_a_pair_and_says_how_to_read_the_rest(monkeypatch):
    monkeypatch.setattr(graphcards, "RECEIPT_ROWS", 2)
    rows = [{"left": f"a.c{i}", "right": "b.k", "from": "query"} for i in range(3)]
    receipt = graphcards.overlaps_receipt(rows, n_from_graph=0, n_measured=3, kept={"stored": True})
    assert (receipt["n_pairs"], receipt["n_measured"], receipt["stored"]) == (3, 3, True)
    assert receipt["pairs"]["rows"][0][:2] == ["a.c0", "b.k"]
    assert "1 more of the 3 pairs" in receipt["pairs"]["more"]


# --- the handlers, around a store that records instead of sending -----------


@pytest.fixture
def sales(tmp_path, monkeypatch):
    """Two indexed sources sharing `customer_id`, run from their folder."""
    monkeypatch.chdir(tmp_path)
    sales_orders().to_csv("orders.csv", index=False)
    sales_customers().to_csv("customers.csv", index=False)
    d = tmp_path / catalog.DEFAULT_DIR
    init_project("order reconciliation", portia_dir=d)
    index_source("orders.csv", portia_dir=d)
    index_source("customers.csv", portia_dir=d)
    return str(d)


@pytest.fixture
def recorded(monkeypatch):
    """The store, up, keeping what it is handed: a session, a write, a measured write.

    ``attach`` is how many measured edges the write claims; by default all.
    """
    calls: dict = {"edges": [], "writes": 0, "attach": None, "fingerprints": []}

    @contextmanager
    def session(**_):
        yield "a session"

    def write(graph, live, *, build=None):
        calls["writes"] += 1
        return "build"

    def write_measured(edges, live, project):
        calls["edges"].extend(edges)
        return len(edges) if calls["attach"] is None else calls["attach"]

    def write_fingerprints(fingerprints, live, project):
        calls["fingerprints"].append(fingerprints)

    def prune_step_joins(live, project, spec_ref, keep):
        calls.setdefault("pruned", []).append((spec_ref, list(keep)))

    monkeypatch.setattr(store, "session", session)
    monkeypatch.setattr(store, "write", write)
    monkeypatch.setattr(store, "write_measured", write_measured)
    monkeypatch.setattr(store, "write_fingerprints", write_fingerprints)
    monkeypatch.setattr(store, "prune_step_joins", prune_step_joins)
    return calls


def _receipt_rows(answer: dict) -> list[dict]:
    fields = answer["pairs"]["fields"]
    return [dict(zip(fields, row, strict=True)) for row in answer["pairs"]["rows"]]


def _pairs(*columns: tuple[str, str]) -> list[dict]:
    return [
        {
            "left": "orders",
            "left_column": left,
            "right": "customers",
            "right_column": right,
            "reason": "orders should name a customer",
        }
        for left, right in columns
    ]


def _held(sales: str, **numbers) -> dict:
    """A measurement of orders.customer_id against customers.customer_id, as the graph holds it."""
    graph = build_graph(".", portia_dir=sales).graph
    fingerprints = handlers._table_fingerprints(graph)
    orders, customers = (
        handlers._graph_ref("orders", graph),
        handlers._graph_ref("customers", graph),
    )
    return {
        "n_shared_values": 4,
        "left_coverage": 0.75,
        "right_coverage": 0.83,
        "comparable_types": True,
        "measured_at": "2026-10-01T10:00:00+02:00",
        "left_fingerprint": fingerprints[orders],
        "right_fingerprint": fingerprints[customers],
        "left_distinct": 4,
        "right_distinct": 5,
        **numbers,
    }


def test_a_pair_the_graph_holds_is_answered_from_it_and_only_the_rest_measured(
    sales, recorded, monkeypatch
):
    asked: list = []

    def stored(live, keys, *, project):
        asked.extend(keys)
        return {0: _held(sales)}

    monkeypatch.setattr(query, "stored_overlaps", stored)
    answer = handlers.measure_overlaps(
        _pairs(("customer_id", "customer_id"), ("amount", "customer_id")), sales
    )

    assert asked[0] == ("source:orders.csv::customer_id", "source:customers.csv::customer_id")
    first, second = _receipt_rows(answer)
    assert first["from"] == "graph" and first["measured_at"] == "2026-10-01T10:00:00+02:00"
    assert first["n_shared_values"] == 4 and first["right_distinct"] == 5
    assert second["from"] == "query" and second["left"] == "orders.amount"
    assert (answer["n_from_graph"], answer["n_measured"], answer["stored"]) == (1, 1, True)
    # Only what was measured now is written.
    (edge,) = recorded["edges"]
    assert edge.start.key == "source:orders.csv::amount"
    assert edge.properties["origin"] == "measured"


def test_a_held_pair_taken_against_a_changed_table_is_measured_again(sales, recorded, monkeypatch):
    moved = _held(sales, left_fingerprint="0:0")
    monkeypatch.setattr(query, "stored_overlaps", lambda live, keys, *, project: {0: moved})
    answer = handlers.measure_overlaps(_pairs(("customer_id", "customer_id")), sales)
    (row,) = _receipt_rows(answer)
    assert row["from"] == "query" and row["n_shared_values"] == 4
    assert len(recorded["edges"]) == 1


def test_everything_from_the_graph_writes_nothing(sales, recorded, monkeypatch):
    monkeypatch.setattr(query, "stored_overlaps", lambda live, keys, *, project: {0: _held(sales)})
    answer = handlers.measure_overlaps(_pairs(("customer_id", "customer_id")), sales)
    assert answer["n_measured"] == 0 and "stored" not in answer
    assert recorded["edges"] == [] and recorded["writes"] == 0


def test_a_column_typed_in_another_case_is_kept_on_the_column_it_names(
    sales, recorded, monkeypatch
):
    monkeypatch.setattr(query, "stored_overlaps", lambda live, keys, *, project: {})
    handlers.measure_overlaps(_pairs(("CUSTOMER_ID", "Customer_Id")), sales)
    (edge,) = recorded["edges"]
    assert edge.start.key == "source:orders.csv::customer_id"
    assert edge.end.key == "source:customers.csv::customer_id"


@pytest.fixture
def graph_down(monkeypatch):
    """No graph to reach, whether or not a test server happens to be running.

    These tests are about the answer when Neo4j is down. Without this they
    passed only because no server was up, and failed on a machine where the
    test server was (`tests/test_cli_knowledge.py` points at the same port).
    """
    monkeypatch.setenv("NEO4J_URI", "bolt://localhost:1")


def test_with_no_graph_every_pair_is_measured_and_the_receipt_says_none_was_kept(sales, graph_down):
    answer = handlers.measure_overlaps(_pairs(("customer_id", "customer_id")), sales)
    (row,) = _receipt_rows(answer)
    assert row["from"] == "query" and row["n_shared_values"] == 4
    assert answer["stored"] is False and answer["not_stored_because"]


def test_a_write_that_fails_half_way_still_answers_with_the_numbers(sales, recorded, monkeypatch):
    """A database that drops mid-write is not one never reached, and the receipt
    stands either way: the pairs were measured."""
    monkeypatch.setattr(query, "stored_overlaps", lambda live, keys, *, project: {})

    def dropped(edges, live, project):
        raise RuntimeError("connection reset")

    monkeypatch.setattr(store, "write_measured", dropped)
    answer = handlers.measure_overlaps(_pairs(("customer_id", "customer_id")), sales)
    (row,) = _receipt_rows(answer)
    assert row["n_shared_values"] == 4
    assert answer["stored"] is False and "connection reset" in answer["not_stored_because"]


def test_a_missing_reason_is_refused_before_anything_is_measured(sales, monkeypatch):
    def never(*args, **kwargs):
        raise AssertionError("measured a pair before every pair was checked")

    monkeypatch.setattr(handlers, "column_overlap", never)
    pairs = _pairs(("customer_id", "customer_id"), ("amount", "customer_id"))
    pairs[1]["reason"] = " "
    with pytest.raises(ValueError, match="reason"):
        handlers.measure_overlaps(pairs, sales)


def test_a_join_check_needs_a_reason(sales):
    with pytest.raises(ValueError, match="reason"):
        handlers.join_findings(
            "orders", "customers", keys=["customer_id"], portia_dir=sales, reason=""
        )


def _checks(recorded) -> list:
    """The join records a join check wrote, apart from those a recorded step did."""
    return [e for e in recorded["edges"] if e.properties.get("origin") == "join_check"]


def test_a_join_check_is_kept_whole_as_a_join_between_two_tables(sales, recorded):
    """It used to be four numbers on a column pair; now it is the join, as checked."""
    found = handlers.join_findings(
        "orders",
        "customers",
        keys=["customer_id"],
        portia_dir=sales,
        reason="orders name customers",
    )
    assert found["graph"] == {"kept": True}
    (edge,) = recorded["edges"]
    report = found["report"]
    assert edge.kind == "JOINED"
    assert (edge.start.key, edge.end.key) == ("orders.csv", "customers.csv")
    p = edge.properties
    assert (p["origin"], p["asked_because"]) == ("join_check", "orders name customers")
    assert (p["left_columns"], p["right_columns"]) == (["customer_id"], ["customer_id"])
    assert p["relationship"] == report["relationship"]
    assert p["left_repeats"] == report["left"]["max_key_multiplicity"]
    assert p["right_repeats"] == report["right"]["max_key_multiplicity"]
    assert p["n_shared_keys"] == report["overlap"]["n_shared_keys"]
    assert p["left_fingerprint"] and p["right_fingerprint"]


def test_a_join_check_on_two_columns_a_side_is_kept_too(sales, recorded):
    """A composite key was not kept at all: no pair of single columns measures it."""
    found = handlers.join_findings(
        "orders",
        "customers",
        left_on=["customer_id", "customer_id"],
        right_on=["customer_id", "name"],
        portia_dir=sales,
        reason="customer within region",
    )
    assert found["graph"] == {"kept": True}
    (edge,) = recorded["edges"]
    assert edge.properties["right_columns"] == ["customer_id", "name"]


def test_a_join_check_whose_table_the_graph_lacks_says_it_was_not_kept(sales, recorded):
    recorded["attach"] = 0
    found = handlers.join_findings(
        "orders", "customers", keys=["customer_id"], portia_dir=sales, reason="r"
    )
    assert found["graph"] == {"kept": False, "why": "not_in_graph"}


def test_a_table_joined_to_itself_is_not_kept(sales, recorded):
    found = handlers.join_findings(
        "orders", "orders", keys=["order_id"], portia_dir=sales, reason="r"
    )
    assert found["graph"] == {"kept": False, "why": "same_table"}
    assert recorded["edges"] == []


_FIRST = {
    "id": "first",
    "op": "join",
    "left": "orders",
    "right": "customers",
    "keys": ["customer_id"],
    "how": "left",
}


def test_a_join_check_on_an_earlier_steps_table_is_measured_and_not_kept(sales, recorded):
    """A step before the spec's last is a table no build made: no node to keep it on."""
    handlers.record_step("specs/chain.yaml", dict(_FIRST), portia_dir=sales)
    handlers.record_step(
        "specs/chain.yaml",
        {"id": "second", "op": "sql", "inputs": ["first"], "sql": "SELECT * FROM first"},
        portia_dir=sales,
    )
    found = handlers.join_findings(
        "specs/chain.yaml#first", "customers", keys=["customer_id"], portia_dir=sales, reason="r"
    )
    assert found["report"]["left"]["n_rows"] == 10
    assert found["graph"] == {"kept": False, "why": "side_is_a_step"}
    assert _checks(recorded) == []


def test_a_join_check_on_a_built_specs_last_step_is_kept_against_its_model(sales, recorded):
    """The case a real session found (2026-10-14): the copilot named the unpivot staging
    table it had just built by its step reference, and the measurement never reached the
    graph. The last step of a spec built as it stands is the model's table."""
    handlers.record_step("specs/chain.yaml", dict(_FIRST), portia_dir=sales)
    found = handlers.join_findings(
        "specs/chain.yaml#first", "customers", keys=["customer_id"], portia_dir=sales, reason="r"
    )
    assert found["graph"] == {"kept": True}
    (edge,) = _checks(recorded)
    assert edge.start.key == "chain"


def test_recording_a_step_whose_lineage_cannot_be_read_says_so(sales, recorded):
    """It answered ``graph: updated`` alone, and the copilot took the columns for traced."""
    step = {
        "id": "long",
        "op": "sql",
        "inputs": ["orders"],
        "sql": "SELECT * FROM orders UNPIVOT (v FOR field IN (amount))",
    }
    card = handlers.record_step("specs/long.yaml", step, portia_dir=sales)
    assert card["lineage"] == {"unread": "pivot", "step": "long"}
    traced = handlers.record_step("specs/chain.yaml", dict(_FIRST), portia_dir=sales)
    assert "lineage" not in traced


def test_a_spec_edited_since_its_build_is_not_its_model(sales, recorded):
    handlers.record_step("specs/chain.yaml", dict(_FIRST), portia_dir=sales)
    path = Path(sales).parent / "specs" / "chain.yaml"
    path.write_text(
        path.read_text(encoding="utf-8").replace("how: left", "how: inner"), encoding="utf-8"
    )
    assert handlers._built_model_of("specs/chain.yaml#first", sales) is None


def test_a_join_check_never_fails_because_the_graph_is_down(sales, graph_down):
    found = handlers.join_findings(
        "orders", "customers", keys=["customer_id"], portia_dir=sales, reason="r"
    )
    assert found["report"]["overlap"]["n_shared_keys"] == 4
    assert found["graph"]["kept"] is False and found["graph"]["why"] == "graph_unavailable"
    assert found["graph"]["error"]


def test_graph_lookup_answers_one_question_at_a_time(sales):
    with pytest.raises(ValueError, match="one question at a time"):
        handlers.graph_lookup("orders", column="customer_id", to="customers", portia_dir=sales)


def test_graph_lookup_with_the_graph_down_names_the_tools_that_still_work(sales, graph_down):
    with pytest.raises(ValueError, match="describe_source"):
        handlers.graph_lookup("orders", portia_dir=sales)


def test_graph_lookup_hands_back_a_card(sales, recorded, monkeypatch):
    seen: dict = {}

    def lookup(live, table, column=None, *, neighbour=None, to=None, project):
        seen.update(table=table, to=to)
        return {"table": _header("orders"), "to": _header("customers"), "routes": [], "n_routes": 0}

    monkeypatch.setattr(query, "lookup", lookup)
    card = handlers.graph_lookup("orders", to="customers", portia_dir=sales)
    assert seen == {"table": "orders", "to": "customers"}
    assert card["routes"]["n"] == 0
    # Every table's fingerprint is set to what it is now before the read.
    (written,) = recorded["fingerprints"]
    assert set(written["Source"]) == {"orders", "customers"}


# --- a graph read waits for the graph writes of its own message (2026-10-14) ---------------


def test_a_graph_read_waits_for_a_join_check_already_running(monkeypatch):
    """A harness runs one message's read-only calls together, so a route asked beside a join
    check was read before the check's measurement landed. The read now waits for it."""
    import threading
    import time

    from portia.agent import tools

    seen: list[float] = []
    monkeypatch.setattr(
        handlers, "graph_lookup", lambda *a, **k: seen.append(time.monotonic()) or {"table": {}}
    )
    running = tools._register("join_findings", {"left": "a", "right": "b"})
    started = time.monotonic()

    def finish() -> None:
        time.sleep(0.3)
        tools._retire(running)

    threading.Thread(target=finish).start()
    result = asyncio.run(tools.graph_lookup.handler({"table": "a", "to": "b"}))
    assert "is_error" not in result
    assert seen and seen[0] - started >= 0.25


def test_a_graph_read_with_no_write_running_does_not_wait(monkeypatch):
    import time

    from portia.agent import tools

    monkeypatch.setattr(handlers, "graph_lookup", lambda *a, **k: {"table": {}})
    started = time.monotonic()
    asyncio.run(tools.graph_lookup.handler({"table": "a"}))
    assert time.monotonic() - started < tools.GRAPH_WRITE_POLL_S * 5


# --- a measurement rewrites the structure only when it must (2026-10-15) -------------------


def test_a_measurement_that_attaches_rebuilds_nothing(sales, recorded, monkeypatch):
    """Every measurement used to rewrite the whole project's structure first."""
    monkeypatch.setattr(query, "stored_overlaps", lambda live, keys, *, project: {})
    handlers.measure_overlaps(_pairs(("customer_id", "customer_id")), sales)
    handlers.join_findings(
        "orders", "customers", keys=["customer_id"], portia_dir=sales, reason="r"
    )
    assert recorded["writes"] == 0
    assert len(recorded["edges"]) == 2


def test_a_measurement_on_a_table_new_to_the_graph_rebuilds_once_and_attaches(
    sales, recorded, monkeypatch
):
    tries: list[int] = []

    def write_measured(edges, live, project):
        tries.append(len(edges))
        return 0 if len(tries) == 1 else len(edges)

    monkeypatch.setattr(store, "write_measured", write_measured)
    monkeypatch.setattr(query, "stored_overlaps", lambda live, keys, *, project: {})
    answer = handlers.measure_overlaps(_pairs(("customer_id", "customer_id")), sales)
    assert recorded["writes"] == 1 and tries == [1, 1]
    assert answer["stored"] is True

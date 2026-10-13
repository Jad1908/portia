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

import json
from contextlib import contextmanager

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


def test_a_join_report_is_the_same_four_numbers_under_other_names():
    report = {
        "key_dtype_match": False,
        "overlap": {"n_shared_keys": 3, "left_coverage": 0.5, "right_coverage": 0.25},
    }
    assert measure.from_join_report(report) == {
        "n_shared_values": 3,
        "left_coverage": 0.5,
        "right_coverage": 0.25,
        "comparable_types": False,
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


# --- query: gathering neighbours --------------------------------------------


def _row(kind: str, name: str, path: str | None = None, **extra) -> dict:
    return {"kind": kind, "name": name, "path": path, **extra}


def test_every_link_to_one_table_is_one_neighbour_in_name_order():
    reads = [
        _row("Model", "mart", link="read_by"),
        _row("Source", "customers", "data/customers.csv", link="reads"),
    ]
    grouped = [
        _row("Source", "customers", "data/customers.csv", group_name="sales"),
        _row("Source", "customers", "data/customers.csv", group_name="crm"),
        _row("Source", "returns", "data/returns.csv", group_name="sales"),
    ]
    pairs = [
        {**_row("Source", "customers", "data/customers.csv"), **_pair_row(from_here=True)},
    ]
    neighbours = query.gather_neighbours(reads, grouped, pairs)

    assert [n["name"] for n in neighbours] == ["customers", "mart", "returns"]
    customers = neighbours[0]
    assert customers["reads"] and not customers["read_by"]
    assert customers["groups"] == ["crm", "sales"]
    assert [p["column"] for p in customers["pairs"]] == ["customer_id"]
    assert neighbours[1]["read_by"] and neighbours[1]["pairs"] == []


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


def _measured(start: TableId, end: TableId, column: str = "k", other: str = "k") -> Link:
    pair = {
        "column": column,
        "other_column": other,
        "n_shared_values": 3,
        "coverage": 0.9,
        "other_coverage": 0.6,
        "comparable_types": True,
        "origin": "measured",
        "stale": False,
    }
    return Link(start, end, pair=pair)


def _names(found: list[query.Route]) -> list[list[str]]:
    return [[t.name for t in route.tables] for route in found]


def test_routes_come_fewest_steps_first_then_by_name():
    links = [
        _measured(A, D),
        _measured(A, B),
        _measured(B, D),
        Link(C, A, reads=True),
        Link(C, D, reads=True),
    ]
    found, n = query.routes(links, A, D)
    assert _names(found) == [["a", "d"], ["a", "b", "d"], ["a", "c", "d"]]
    assert n == 3


def test_a_route_never_visits_a_table_twice_and_stops_at_the_step_limit():
    chain = [_measured(A, B), _measured(B, C), _measured(C, D), _measured(D, E)]
    assert _names(query.routes(chain, A, D)[0]) == [["a", "b", "c", "d"]]
    assert query.routes(chain, A, E) == ([], 0), "four steps is past ROUTE_HOPS"
    assert _names(query.routes(chain, A, E, max_steps=4)[0]) == [["a", "b", "c", "d", "e"]]


def test_the_limit_cuts_the_list_and_the_count_says_how_many_there_were():
    links = [_measured(A, t) for t in (B, C, D)] + [_measured(t, E) for t in (B, C, D)]
    found, n = query.routes(links, A, E, limit=2)
    assert _names(found) == [["a", "b", "e"], ["a", "c", "e"]]
    assert n == 3


def test_two_tables_linked_several_ways_are_one_step_carrying_all_of_them():
    links = [_measured(A, B, "z", "z"), _measured(A, B, "id", "a_id"), Link(B, A, reads=False)]
    (route,), n = query.routes(links, A, B)
    assert n == 1
    assert [p["column"] for p in route.steps[0].pairs] == ["id", "z"]


def test_a_step_walked_against_the_edge_is_turned_and_says_who_reads_whom():
    links = [_measured(B, A, "b_col", "a_col"), Link(C, B, reads=True)]
    (route,), _ = query.routes(links, A, C)
    first, second = route.steps
    assert (first.pairs[0]["column"], first.pairs[0]["other_column"]) == ("a_col", "b_col")
    assert (first.pairs[0]["coverage"], first.pairs[0]["other_coverage"]) == (0.6, 0.9)
    # The model `c` reads `b`, so from `b` to `c` the step is read by.
    assert second.read_by and not second.reads


def test_a_table_has_no_route_to_itself():
    assert query.routes([_measured(A, B)], A, A) == ([], 0)


def test_a_link_row_keys_a_source_by_path_and_a_model_by_name():
    row = {
        "link": "overlaps",
        "a_kind": "Source",
        "a_name": "orders",
        "a_path": "data/orders.csv",
        "b_kind": "Model",
        "b_name": "mart",
        "b_path": None,
        "a_column": "customer_id",
        "b_column": "id",
        **{key: None for key in query.LINK_FIELDS},
    }
    link = query.link_from_row(row)
    assert link.start == TableId("orders", "Source", "data/orders.csv")
    assert link.end == TableId("mart", "Model", "mart")
    assert link.pair is not None and link.pair["other_column"] == "id"
    assert query.link_from_row({**row, "link": "reads"}).reads


# --- graphcards: every cut said ---------------------------------------------


def _header(name: str) -> dict:
    return {"kind": "Source", "name": name, "path": f"data/{name}.csv", "n_columns": 4}


def _pair(column: str) -> dict:
    return query.pair_from_row({**_pair_row(from_here=True), "column": column})


def _neighbour(name: str, n_pairs: int) -> dict:
    return {
        "kind": "Source",
        "name": name,
        "path": f"data/{name}.csv",
        "reads": False,
        "read_by": False,
        "groups": [],
        "pairs": [_pair(f"c{i}") for i in range(n_pairs)],
    }


def _neighbourhood(neighbours: list[dict], **extra) -> dict:
    return {
        "table": _header("hub"),
        "neighbours": neighbours,
        "groups": [],
        "precedents": [],
        **extra,
    }


def test_the_card_names_the_pair_fields_once_before_the_neighbours():
    card = graphcards.card(_neighbourhood([_neighbour("n1", 1), _neighbour("n2", 0)]))
    assert list(card)[:3] == ["table", "pair_fields", "neighbours"]
    rows = card["neighbours"]["rows"]
    assert rows[0][graphcards.NEIGHBOUR_FIELDS.index("pairs")][0][0] == "c0"
    assert rows[1][graphcards.NEIGHBOUR_FIELDS.index("n_pairs")] == 0
    assert "more" not in card["neighbours"] and "pairs_more" not in card
    json.dumps(card)


def test_a_card_with_no_measured_pair_carries_no_pair_fields():
    card = graphcards.card(_neighbourhood([_neighbour("n1", 0)]))
    assert "pair_fields" not in card and "groups" not in card and "precedents" not in card


def test_when_the_pairs_do_not_all_fit_every_neighbour_shows_the_same_number(monkeypatch):
    """Cut by size, evenly, in column order: never by coverage (§6.1)."""
    monkeypatch.setattr(graphcards, "CARD_PAIRS", 6)
    card = graphcards.card(_neighbourhood([_neighbour(f"n{i}", 3) for i in range(4)]))
    index = graphcards.NEIGHBOUR_FIELDS.index("pairs")
    assert [len(row[index]) for row in card["neighbours"]["rows"]] == [1, 1, 1, 1]
    assert "4 of these links" in card["pairs_more"]


def test_neighbours_past_the_cap_are_counted_and_the_narrower_question_named(monkeypatch):
    monkeypatch.setattr(graphcards, "NEIGHBOURS", 2)
    card = graphcards.card(_neighbourhood([_neighbour(f"n{i}", 0) for i in range(3)]))
    assert card["neighbours"]["n"] == 3 and card["neighbours"]["shown"] == "1-2"
    assert "1 more of the 3" in card["neighbours"]["more"]
    assert 'neighbour="<name>"' in card["neighbours"]["more"]


def test_precedents_and_groups_are_lists_cut_like_any_other(monkeypatch):
    monkeypatch.setattr(graphcards, "PRECEDENTS", 1)
    precedent = {
        "column": "customer_id",
        "against_kind": "Source",
        "against_table": "customers",
        "against_column": "customer_id",
        "by_table": "orders",
        "n_shared_values": 2,
        "asked_because": "same key",
    }
    group = {"name": "sales", "context": "what the shop exports", "n_members": 3}
    card = graphcards.card(
        _neighbourhood([], groups=[group], precedents=[precedent, {**precedent, "by_table": "x"}])
    )
    assert card["groups"]["rows"] == [["sales", "what the shop exports", 3]]
    assert card["precedents"]["fields"] == list(graphcards.PRECEDENT_FIELDS)
    assert card["precedents"]["n"] == 2 and "1 more of 2" in card["precedents"]["more"]


def test_between_two_tables_each_pair_carries_both_columns_facts(monkeypatch):
    monkeypatch.setattr(graphcards, "PAIRS", 1)
    answer = {
        "table": _header("orders"),
        "neighbour": _header("customers"),
        "reads": False,
        "read_by": True,
        "groups": ["sales"],
        "pairs": [_pair("a"), _pair("b")],
    }
    card = graphcards.card(answer)
    assert card["pairs"]["fields"][-2:] == ["column_facts", "other_facts"]
    assert card["pairs"]["rows"][0][-1] == {"n_distinct": 9, "top": "C1", "top_freq": 3}
    assert "between `orders` and `customers`" in card["pairs"]["more"]


def test_a_column_card_lists_its_lineage_and_cuts_each_list(monkeypatch):
    monkeypatch.setattr(graphcards, "COLUMN_ROWS", 1)
    walked = {"hops": 1, "kind": "Source", "table": "orders", "path": None, "column": "k"}
    answer = {
        "column": {"table": "mart", "name": "k"},
        "upstream": [{**walked, "via": "join", "step": "mart#j"}, {**walked, "hops": 2}],
        "downstream": [],
        "origins": [{"kind": "Source", "table": "orders", "path": "o.csv", "column": "k"}],
        "compared_with": [],
        "precedents": [],
    }
    card = graphcards.card(answer)
    assert card["upstream"]["rows"] == [[1, "orders", "Source", "k", "join", "mart#j"]]
    assert "1 more of 2 columns" in card["upstream"]["more"]
    assert card["origins"]["rows"] == [["orders", "Source", "k", None]]
    assert card["downstream"] == {
        "n": 0,
        "shown": "none",
        "fields": card["downstream"]["fields"],
        "rows": [],
    }
    assert "precedents" not in card


def _route(n_pairs: int) -> dict:
    return {
        "tables": ["a", "c", "b"],
        "steps": [
            {"reads": False, "read_by": True, "pairs": []},
            {"reads": False, "read_by": False, "pairs": [_pair(f"p{i}") for i in range(n_pairs)]},
        ],
    }


def test_a_route_card_says_each_steps_links_and_cuts_its_pairs(monkeypatch):
    monkeypatch.setattr(graphcards, "STEP_PAIRS", 1)
    answer = {"table": _header("a"), "to": _header("b"), "routes": [_route(2)], "n_routes": 3}
    card = graphcards.card(answer)
    (row,) = card["routes"]["rows"]
    assert row[:2] == [2, ["a", "c", "b"]]
    first, second = row[2]
    assert first == {"read_by": True}
    assert second["n_pairs"] == 2 and len(second["pairs"]) == 1
    assert card["pair_fields"] == list(graphcards.ROUTE_PAIR_FIELDS)
    assert "asked_because" not in card["pair_fields"]
    assert "2 more of the 3 routes" in card["routes"]["more"]
    assert "pairs_more" in card


def test_no_route_is_an_empty_list_not_a_refusal():
    answer = {"table": _header("a"), "to": _header("b"), "routes": [], "n_routes": 0}
    card = graphcards.card(answer)
    assert card["routes"]["n"] == 0 and card["routes"]["shown"] == "none"


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
    calls: dict = {"edges": [], "writes": 0, "attach": None}

    @contextmanager
    def session(**_):
        yield "a session"

    def write(graph, live, *, build=None):
        calls["writes"] += 1
        return "build"

    def write_measured(edges, live, project):
        calls["edges"].extend(edges)
        return len(edges) if calls["attach"] is None else calls["attach"]

    monkeypatch.setattr(store, "session", session)
    monkeypatch.setattr(store, "write", write)
    monkeypatch.setattr(store, "write_measured", write_measured)
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


def test_a_join_check_keeps_its_key_measurement_in_the_graph(sales, recorded):
    found = handlers.join_findings(
        "orders",
        "customers",
        keys=["customer_id"],
        portia_dir=sales,
        reason="orders name customers",
    )
    assert found["graph"] == {"kept": True}
    (edge,) = recorded["edges"]
    overlap = found["report"]["overlap"]
    assert edge.start.key == "source:orders.csv::customer_id"
    assert edge.properties["origin"] == "join_check"
    assert edge.properties["asked_because"] == "orders name customers"
    assert edge.properties["n_shared_values"] == overlap["n_shared_keys"]
    assert edge.properties["left_coverage"] == overlap["left_coverage"]
    assert edge.properties["right_coverage"] == overlap["right_coverage"]
    assert edge.properties["left_fingerprint"] and edge.properties["right_fingerprint"]


def test_a_join_check_whose_column_the_graph_lacks_says_it_was_not_kept(sales, recorded):
    recorded["attach"] = 0
    found = handlers.join_findings(
        "orders", "customers", keys=["customer_id"], portia_dir=sales, reason="r"
    )
    assert found["graph"]["kept"] is False and "no column" in found["graph"]["why"]


@pytest.mark.parametrize(
    ("left", "right", "keys", "says"),
    [
        ("orders", "orders", ["order_id", "customer_id"], "more than one column"),
        ("orders", "orders", ["order_id"], "same table"),
    ],
)
def test_what_a_join_check_does_not_keep_it_says(sales, recorded, left, right, keys, says):
    found = handlers.join_findings(left, right, keys=keys, portia_dir=sales, reason="r")
    assert found["graph"]["kept"] is False and says in found["graph"]["why"]
    assert recorded["edges"] == []


def test_a_join_check_on_a_steps_table_is_measured_and_not_kept(sales, recorded):
    handlers.record_step(
        "specs/chain.yaml",
        {
            "id": "first",
            "op": "join",
            "left": "orders",
            "right": "customers",
            "keys": ["customer_id"],
            "how": "left",
        },
        portia_dir=sales,
    )
    found = handlers.join_findings(
        "specs/chain.yaml#first", "customers", keys=["customer_id"], portia_dir=sales, reason="r"
    )
    assert found["report"]["left"]["n_rows"] == 10
    assert found["graph"]["kept"] is False and "step produced" in found["graph"]["why"]
    assert recorded["edges"] == []


def test_a_join_check_never_fails_because_the_graph_is_down(sales, graph_down):
    found = handlers.join_findings(
        "orders", "customers", keys=["customer_id"], portia_dir=sales, reason="r"
    )
    assert found["report"]["overlap"]["n_shared_keys"] == 4
    assert found["graph"]["kept"] is False and "could not be written" in found["graph"]["why"]


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

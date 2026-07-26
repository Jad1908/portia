"""The workflow graph: what the YAML already encodes, turned into coordinates.

`ui/graph.py` deliberately imports no NiceGUI, so the answer to "does this spec
read as a DAG" is testable without a browser — which is the point, since the
reason to render the graph at all is to find out whether cards-are-steps reads
correctly (docs/VISION.md, open question).
"""

from __future__ import annotations

from portia.ui import graph

TWO_HOP = {
    "sources": {"orders": "orders.csv", "customers": "customers.csv", "regions": "regions.csv"},
    "steps": [
        {
            "id": "joined",
            "op": "join",
            "left": "orders",
            "right": "customers",
            "keys": ["customer_id"],
        },
        {"id": "enriched", "op": "join", "left": "joined", "right": "regions", "keys": ["region"]},
    ],
}


def test_an_empty_spec_places_nothing():
    assert graph.layout(None).empty
    assert graph.layout({}).empty


def test_every_source_and_step_becomes_a_node():
    placed = graph.layout(TWO_HOP)
    kinds = {n.id: n.kind for n in placed.nodes}
    assert kinds == {
        "orders": graph.SOURCE,
        "customers": graph.SOURCE,
        "regions": graph.SOURCE,
        "joined": graph.STEP,
        "enriched": graph.STEP,
    }


def test_an_edge_means_this_steps_output_is_that_steps_input():
    placed = graph.layout(TWO_HOP)
    assert {(e.src, e.dst) for e in placed.edges} == {
        ("orders", "joined"),
        ("customers", "joined"),
        ("joined", "enriched"),
        ("regions", "enriched"),
    }


def test_each_hop_is_one_column_to_the_right():
    placed = graph.layout(TWO_HOP)
    x = {n.id: n.x for n in placed.nodes}
    assert x["orders"] == x["customers"] == 0
    assert x["joined"] > x["orders"]
    assert x["enriched"] > x["joined"]


def test_a_source_sits_one_column_left_of_the_step_that_reads_it():
    """`regions` is first read at hop two, so it doesn't strand at the margin."""
    placed = graph.layout(TWO_HOP)
    x = {n.id: n.x for n in placed.nodes}
    assert x["regions"] == x["joined"]
    assert x["regions"] < x["enriched"]


def test_steps_keep_spec_order_within_a_column():
    """Nothing is re-sorted by anything measured — the order is the decisions."""
    spec = {
        "sources": {"a": "a.csv", "b": "b.csv"},
        "steps": [
            {"id": "second", "op": "normalize", "input": "b", "transforms": []},
            {"id": "first", "op": "normalize", "input": "a", "transforms": []},
        ],
    }
    placed = graph.layout(spec)
    same_column = [n for n in placed.nodes if n.kind == graph.STEP]
    assert [n.id for n in same_column] == ["second", "first"]
    assert same_column[0].y < same_column[1].y


def test_a_sql_step_reads_every_table_it_declares():
    spec = {
        "sources": {"a": "a.csv", "b": "b.csv"},
        "steps": [{"id": "rolled", "op": "sql", "inputs": ["a", "b"], "sql": "SELECT 1"}],
    }
    placed = graph.layout(spec)
    assert {(e.src, e.dst) for e in placed.edges} == {("a", "rolled"), ("b", "rolled")}


def test_a_table_named_twice_gets_one_edge():
    """A self-join names one table on both sides; two identical arrows is noise."""
    spec = {
        "sources": {"a": "a.csv"},
        "steps": [{"id": "self", "op": "join", "left": "a", "right": "a", "keys": ["k"]}],
    }
    assert len(graph.layout(spec).edges) == 1


def test_a_step_carries_its_own_dict_for_the_card_to_render():
    node = next(n for n in graph.layout(TWO_HOP).nodes if n.id == "joined")
    assert node.op == "join"
    assert node.step is not None and node.step["keys"] == ["customer_id"]


def test_cards_are_a_uniform_size_whatever_is_in_them():
    """A card never grows with its numbers (DESIGN.md → flag-badge, step-card)."""
    heights = {n.h for n in graph.layout(TWO_HOP).nodes if n.kind == graph.STEP}
    assert heights == {graph.STEP_H}


def test_the_canvas_is_big_enough_for_everything_placed():
    placed = graph.layout(TWO_HOP)
    assert placed.width >= max(n.x + n.w for n in placed.nodes)
    assert placed.height >= max(n.y + n.h for n in placed.nodes)


def test_an_arrowhead_is_never_bigger_than_the_design_allows():
    assert graph.ARROW <= 6

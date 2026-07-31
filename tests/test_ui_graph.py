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


# --- three node kinds -------------------------------------------------------

CROSS_SPEC = {
    "sources": {"regions": "regions.csv"},
    "steps": [
        {"id": "joined", "op": "join", "left": "stg_orders", "right": "regions", "keys": ["r"]}
    ],
}


def test_another_specs_output_is_a_model_not_a_source():
    """The bug the pipeline overhaul left behind: a table portia built drew
    identically to a CSV somebody dropped in, so the graph could not say which
    of its inputs it was responsible for."""
    placed = graph.layout(CROSS_SPEC, models={"stg_orders"})
    kinds = {n.id: n.kind for n in placed.nodes}
    assert kinds["stg_orders"] == graph.MODEL
    assert kinds["regions"] == graph.SOURCE


def test_with_no_other_specs_every_input_is_a_file():
    """A project of one spec has no cross-spec references to classify."""
    placed = graph.layout(CROSS_SPEC)
    assert {n.kind for n in placed.nodes if n.id != "joined"} == {graph.SOURCE}


def test_steps_alone_omits_what_the_spec_reads():
    """What an expanded model card holds: its incoming edges are already drawn
    around it, and repeating them inside would state them twice."""
    placed = graph.layout(CROSS_SPEC, include_inputs=False)
    assert [n.id for n in placed.nodes] == ["joined"]
    assert placed.edges == []


# --- uniform cards, sized to what they must show ----------------------------


def test_every_card_is_the_same_height_whatever_it_carries():
    placed = graph.layout(TWO_HOP, badges={"joined": 3})
    heights = {n.h for n in placed.nodes if n.kind == graph.STEP}
    assert len(heights) == 1, "one card with three flags must not be the tall one"


def test_the_uniform_height_grows_to_fit_the_busiest_card():
    """Uniformity is the rule; clipping a blocking flag was never the point."""
    quiet = graph.layout(TWO_HOP, badges={"joined": 1})
    busy = graph.layout(TWO_HOP, badges={"joined": 4})
    assert graph.step_height(4) > graph.step_height(1)
    assert max(n.h for n in busy.nodes) > max(n.h for n in quiet.nodes)


def test_no_badges_still_gets_a_readable_card():
    assert graph.step_height(0) == graph.STEP_H


# --- the project graph ------------------------------------------------------

PROJECT = {
    "stg_orders": {
        "layer": "staging",
        "sources": {"orders": "data/orders.csv"},
        "steps": [{"id": "clean", "op": "normalize", "input": "orders", "transforms": []}],
    },
    "stg_customers": {
        "layer": "staging",
        "sources": {"customers": "data/customers.csv"},
        "steps": [{"id": "tidy", "op": "normalize", "input": "customers", "transforms": []}],
    },
    "orders_enriched": {
        "layer": "mart",
        "sources": {},
        "steps": [
            {
                "id": "joined",
                "op": "join",
                "left": "stg_orders",
                "right": "stg_customers",
                "keys": ["customer_id"],
            }
        ],
    },
}


def test_an_empty_project_places_nothing():
    assert graph.project_layout({}).empty


def test_every_model_and_every_file_it_reads_becomes_a_node():
    placed = graph.project_layout(PROJECT)
    kinds = {n.id: n.kind for n in placed.nodes}
    assert kinds == {
        "orders": graph.SOURCE,
        "customers": graph.SOURCE,
        "stg_orders": graph.MODEL,
        "stg_customers": graph.MODEL,
        "orders_enriched": graph.MODEL,
    }


def test_an_edge_is_one_model_reading_another():
    placed = graph.project_layout(PROJECT)
    assert {(e.src, e.dst) for e in placed.edges} == {
        ("orders", "stg_orders"),
        ("customers", "stg_customers"),
        ("stg_orders", "orders_enriched"),
        ("stg_customers", "orders_enriched"),
    }


def test_a_model_sits_right_of_everything_it_reads():
    """Dependency order — `spec.run_order`'s answer, drawn."""
    x = {n.id: n.x for n in graph.project_layout(PROJECT).nodes}
    assert x["stg_orders"] == x["stg_customers"]
    assert x["orders_enriched"] > x["stg_orders"]
    assert x["orders"] < x["stg_orders"]


def test_a_model_carries_its_layer_and_its_step_count():
    node = next(n for n in graph.project_layout(PROJECT).nodes if n.id == "orders_enriched")
    assert node.layer == "mart"
    assert node.steps == 1


def test_the_layer_never_moves_a_card():
    """staging/intermediate/mart is a label a human typed; nothing measured it,
    so it may colour a card and must never position one. Build order is the only
    ordering here, and it comes from what the specs read."""
    unlabelled = {name: {**doc, "layer": None} for name, doc in PROJECT.items()}
    relabelled = {name: {**doc, "layer": "mart"} for name, doc in PROJECT.items()}
    boxes = lambda project: {  # noqa: E731
        n.id: (n.x, n.y, n.w, n.h) for n in graph.project_layout(project).nodes
    }
    assert boxes(PROJECT) == boxes(unlabelled) == boxes(relabelled)


def test_specs_that_read_each_other_do_not_hang_the_window():
    """`spec.run_order` refuses a cycle; a graph is the surface most likely to be
    open while someone works out that they have one."""
    cyclic = {
        "a": {"sources": {}, "steps": [{"id": "s", "op": "normalize", "input": "b"}]},
        "b": {"sources": {}, "steps": [{"id": "t", "op": "normalize", "input": "a"}]},
    }
    assert len(graph.project_layout(cyclic).nodes) == 2


# --- expanding a model in place ---------------------------------------------


def test_a_collapsed_model_has_no_inner_graph():
    node = next(n for n in graph.project_layout(PROJECT).nodes if n.id == "stg_orders")
    assert node.inner is None and not node.expanded
    assert (node.w, node.h) == (graph.MODEL_W, graph.MODEL_H)


def test_expanding_a_model_opens_it_onto_its_own_steps():
    placed = graph.project_layout(PROJECT, expanded={"orders_enriched"})
    node = next(n for n in placed.nodes if n.id == "orders_enriched")
    assert node.expanded
    assert [n.id for n in node.inner.nodes] == ["joined"]
    assert node.h > graph.MODEL_H


def test_only_the_expanded_card_opens():
    placed = graph.project_layout(PROJECT, expanded={"orders_enriched"})
    closed = [n for n in placed.nodes if n.kind == graph.MODEL and not n.expanded]
    assert {n.id for n in closed} == {"stg_orders", "stg_customers"}


def test_expanding_pushes_the_column_beside_it_rather_than_overlapping_it():
    """Per-column widths, not one global stride: an open card is wider than a
    closed one, and a fixed stride would put it underneath its neighbour.

    The model opened here is a two-hop chain, because that is what makes the card
    genuinely wider — a one-step model opens to something a collapsed card already
    had room for, and asserting on that would be asserting on nothing.
    """
    multi_hop = {
        "sources": {},
        "steps": [
            {
                "id": "joined",
                "op": "join",
                "left": "stg_orders",
                "right": "stg_customers",
                "keys": ["customer_id"],
            },
            {"id": "tidied", "op": "normalize", "input": "joined", "transforms": []},
        ],
    }
    wide = {
        **PROJECT,
        "orders_enriched": multi_hop,
        "downstream": {
            "sources": {},
            "steps": [{"id": "z", "op": "normalize", "input": "orders_enriched", "transforms": []}],
        },
    }
    shut = {n.id: n.x for n in graph.project_layout(wide).nodes}
    open_ = {n.id: n.x for n in graph.project_layout(wide, expanded={"orders_enriched"}).nodes}
    assert open_["downstream"] > shut["downstream"]


def test_expanding_something_that_is_not_a_model_is_ignored():
    placed = graph.project_layout(PROJECT, expanded={"not_a_model"})
    assert not any(n.expanded for n in placed.nodes)

"""The workflow graph: what the YAML already encodes, turned into coordinates.

`ui/graph.py` deliberately imports no NiceGUI, so the answer to "does this project
read as a DAG" is testable without a browser.

**A card is a table, always** (2026-08-15). The per-spec layout these tests used
to cover — one card per *step*, with its own arrows, drawn inside an opened model
— is gone: a step compiles to a named block inside one SQL file and never becomes
a table, so it is a row in a list the renderer draws and not a thing with
coordinates. What is left here is one graph, and the ancestry rule the visibility
filter and the highlight are both built on.
"""

from __future__ import annotations

from portia.ui import graph

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


def test_a_step_is_never_a_node():
    """The 2026-08-15 rule, asserted directly: `joined` builds `orders_enriched`
    and is a row inside its card, so nothing on the canvas is named after it."""
    ids = {n.id for n in graph.project_layout(PROJECT, expanded={"orders_enriched"}).nodes}
    assert "joined" not in ids
    assert "clean" not in ids and "tidy" not in ids


def test_an_edge_is_one_table_reading_another():
    placed = graph.project_layout(PROJECT)
    assert {(e.src, e.dst) for e in placed.edges} == {
        ("orders", "stg_orders"),
        ("customers", "stg_customers"),
        ("stg_orders", "orders_enriched"),
        ("stg_customers", "orders_enriched"),
    }


def test_a_table_named_twice_gets_one_edge():
    """A self-join names one table on both sides; two identical arrows is noise."""
    project = {
        "selfish": {
            "sources": {"a": "a.csv"},
            "steps": [{"id": "s", "op": "join", "left": "a", "right": "a", "keys": ["k"]}],
        }
    }
    assert len(graph.project_layout(project).edges) == 1


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
    so it may name a card and must never position one. Build order is the only
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


def test_the_canvas_is_big_enough_for_everything_placed():
    placed = graph.project_layout(PROJECT)
    assert placed.width >= max(n.x + n.w for n in placed.nodes)
    assert placed.height >= max(n.y + n.h for n in placed.nodes)


def test_an_arrowhead_is_never_bigger_than_the_design_allows():
    assert graph.ARROW <= 6


# --- opening a card ----------------------------------------------------------


def test_a_shut_card_is_the_uniform_size():
    node = next(n for n in graph.project_layout(PROJECT).nodes if n.id == "stg_orders")
    assert not node.open
    assert (node.w, node.h) == (graph.MODEL_W, graph.MODEL_H)


def test_opening_a_card_makes_room_for_the_rows_it_will_draw():
    placed = graph.project_layout(
        PROJECT, expanded={"orders_enriched"}, rows={"orders_enriched": 5}
    )
    node = next(n for n in placed.nodes if n.id == "orders_enriched")
    assert node.open
    assert node.h == graph.open_height(5) > graph.MODEL_H


def test_only_the_opened_card_opens():
    placed = graph.project_layout(PROJECT, expanded={"orders_enriched"})
    shut = [n for n in placed.nodes if n.kind == graph.MODEL and not n.open]
    assert {n.id for n in shut} == {"stg_orders", "stg_customers"}


def test_every_open_card_is_the_same_width():
    """Uniform: a card is never wider *because* it has more to say (DESIGN.md)."""
    placed = graph.project_layout(
        PROJECT, expanded={"stg_orders", "orders_enriched"}, rows={"orders_enriched": 20}
    )
    widths = {n.w for n in placed.nodes if n.kind == graph.MODEL and n.open}
    assert widths == {graph.MODEL_OPEN_W}


def test_opening_pushes_the_column_beside_it_rather_than_overlapping_it():
    """Per-column widths, not one global stride: an open card is wider than a
    shut one, and a fixed stride would put it underneath its neighbour."""
    wide = {
        **PROJECT,
        "downstream": {
            "sources": {},
            "steps": [{"id": "z", "op": "normalize", "input": "orders_enriched", "transforms": []}],
        },
    }
    shut = {n.id: n.x for n in graph.project_layout(wide).nodes}
    open_ = {n.id: n.x for n in graph.project_layout(wide, expanded={"orders_enriched"}).nodes}
    assert open_["downstream"] > shut["downstream"]


def test_opening_something_that_is_not_a_model_is_ignored():
    placed = graph.project_layout(PROJECT, expanded={"not_a_model"})
    assert not any(n.open for n in placed.nodes)


# --- ancestry: what "show this table" has to bring with it -------------------


def test_ancestry_is_a_table_and_everything_upstream_of_it():
    assert graph.ancestry(PROJECT, ["orders_enriched"]) == {
        "orders_enriched",
        "stg_orders",
        "stg_customers",
    }
    assert graph.ancestry(PROJECT, ["stg_orders"]) == {"stg_orders"}


def test_ancestry_of_a_cycle_terminates():
    cyclic = {
        "a": {"sources": {}, "steps": [{"id": "s", "op": "normalize", "input": "b"}]},
        "b": {"sources": {}, "steps": [{"id": "t", "op": "normalize", "input": "a"}]},
    }
    assert graph.ancestry(cyclic, ["a"]) == {"a", "b"}


def test_upstream_ids_reaches_the_files_the_data_entered_through():
    """The highlight traces the flow back to where the data arrived; stopping at
    the first model would leave arrows crossing the left edge unexplained."""
    assert graph.upstream_ids(PROJECT, "orders_enriched") == {
        "orders_enriched",
        "stg_orders",
        "stg_customers",
        "orders",
        "customers",
    }


# --- descendants: what un-choosing a table has to take with it ---------------


def test_descendants_is_a_table_and_everything_built_from_it():
    assert graph.descendants(PROJECT, ["stg_orders"]) == {"stg_orders", "orders_enriched"}
    assert graph.descendants(PROJECT, ["orders_enriched"]) == {"orders_enriched"}


def test_descendants_of_a_cycle_terminates():
    cyclic = {
        "a": {"sources": {}, "steps": [{"id": "s", "op": "normalize", "input": "b"}]},
        "b": {"sources": {}, "steps": [{"id": "t", "op": "normalize", "input": "a"}]},
    }
    assert graph.descendants(cyclic, ["a"]) == {"a", "b"}


# --- the visibility filter ---------------------------------------------------


def test_showing_one_table_shows_everything_it_is_built_from():
    placed = graph.project_layout(PROJECT, visible={"orders_enriched"})
    assert {n.id for n in placed.nodes if n.kind == graph.MODEL} == {
        "orders_enriched",
        "stg_orders",
        "stg_customers",
    }


def test_hiding_a_table_hides_it_and_leaves_the_rest():
    placed = graph.project_layout(PROJECT, visible={"stg_orders"})
    assert {n.id for n in placed.nodes if n.kind == graph.MODEL} == {"stg_orders"}
    assert "customers" not in {n.id for n in placed.nodes}


def test_a_narrowed_canvas_says_what_it_left_out():
    """A count is not enough — "showing 1 of 3" has to be checkable against the
    names, or a narrowed project reads as tables having disappeared."""
    placed = graph.project_layout(PROJECT, visible={"stg_orders"})
    assert placed.hidden == ("orders_enriched", "stg_customers")


def test_nothing_chosen_draws_everything_and_choosing_nothing_draws_nothing():
    """**Two values, opposite meanings** (2026-08-16). `None` is *nothing has been
    chosen*, which is the whole project — a filter you have to defeat before the
    app draws anything is not a focused view. The empty **set** is *you turned
    everything off*, which the select-all toggle makes reachable."""
    everything = {n.id for n in graph.project_layout(PROJECT).nodes}
    assert {n.id for n in graph.project_layout(PROJECT, visible=None).nodes} == everything
    assert graph.project_layout(PROJECT, visible=set()).empty


def test_a_choice_naming_only_tables_that_are_gone_falls_back_to_everything():
    """The set is restored from disk and may name tables the project no longer
    has. Blanking the canvas because every remembered name was deleted would say
    the project is empty; turning everything off deliberately is the empty set,
    which is a different value and still draws nothing."""
    placed = graph.project_layout(PROJECT, visible={"not_a_model"})
    assert not placed.hidden
    assert len([n for n in placed.nodes if n.kind == graph.MODEL]) == 3


def test_an_edge_to_a_hidden_table_is_not_drawn():
    placed = graph.project_layout(PROJECT, visible={"stg_orders"})
    assert {(e.src, e.dst) for e in placed.edges} == {("orders", "stg_orders")}


# --- the direction the highlight flows --------------------------------------


def test_upstream_hops_counts_the_distance_to_the_selected_table():
    """The animation's phase: the table itself at 0, then each hop upstream."""
    assert graph.upstream_hops(PROJECT, "orders_enriched") == {
        "orders_enriched": 0,
        "stg_orders": 1,
        "stg_customers": 1,
        "orders": 2,
        "customers": 2,
    }


def test_a_node_reachable_two_ways_takes_the_shorter_one():
    """Breadth-first: a file feeding both a staging model and the mart directly
    is one hop away, and lighting it as three would make the animation disagree
    with the arrow you can see."""
    project = {
        **PROJECT,
        "orders_enriched": {
            "sources": {"orders": "data/orders.csv"},
            "steps": [
                {"id": "j", "op": "sql", "inputs": ["stg_orders", "orders"], "sql": "select 1"}
            ],
        },
    }
    assert graph.upstream_hops(project, "orders_enriched")["orders"] == 1


def test_hops_reach_the_files_and_stop_there():
    hops = graph.upstream_hops(PROJECT, "stg_orders")
    assert hops == {"stg_orders": 0, "orders": 1}


def test_hops_of_a_table_that_is_not_there_is_empty():
    assert graph.upstream_hops(PROJECT, "no_such_table") == {}


def test_hops_of_a_cycle_terminates():
    cyclic = {
        "a": {"sources": {}, "steps": [{"id": "s", "op": "normalize", "input": "b"}]},
        "b": {"sources": {}, "steps": [{"id": "t", "op": "normalize", "input": "a"}]},
    }
    assert graph.upstream_hops(cyclic, "a") == {"a": 0, "b": 1}


# --- the reader's arrangement (2026-09-07) -----------------------------------


def _node(placed, name):
    return next(n for n in placed.nodes if n.id == name)


def test_a_dragged_card_keeps_its_offset_and_its_arrows_follow():
    """`graph.moved`: the grid places, the reader shifts, and the edges are drawn
    from the shifted card — so an arrow follows the card it touches and still
    says what it said."""
    plain = graph.project_layout(PROJECT)
    moved = graph.project_layout(PROJECT, offsets={"orders_enriched": (40, 30)})
    a, b = _node(plain, "orders_enriched"), _node(moved, "orders_enriched")
    assert (b.x - a.x, b.y - a.y) == (40, 30)
    assert _node(plain, "stg_orders") == _node(moved, "stg_orders"), "only the dragged card"

    into = {e.src: e for e in moved.edges if e.dst == "orders_enriched"}
    was = {e.src: e for e in plain.edges if e.dst == "orders_enriched"}
    for src, edge in into.items():
        assert (edge.x2 - was[src].x2, edge.y2 - was[src].y2) == (40, 30)
        assert (edge.x1, edge.y1) == (was[src].x1, was[src].y1), "the other end stayed"


def test_an_offset_rides_on_top_of_the_grid_rather_than_replacing_it():
    """A delta, not a position: opening a neighbour still pushes the column, and
    the dragged card goes with its column."""
    shut = graph.project_layout(PROJECT, offsets={"orders_enriched": (25, 0)})
    opened = graph.project_layout(
        PROJECT, expanded={"stg_orders"}, offsets={"orders_enriched": (25, 0)}
    )
    grid_shut = _node(graph.project_layout(PROJECT), "orders_enriched").x
    grid_open = _node(graph.project_layout(PROJECT, expanded={"stg_orders"}), "orders_enriched").x
    assert grid_open > grid_shut, "the grid moved"
    assert _node(shut, "orders_enriched").x == grid_shut + 25
    assert _node(opened, "orders_enriched").x == grid_open + 25


def test_an_offset_for_a_table_the_layout_has_no_node_for_is_ignored():
    """The offsets outlive a spec that was removed; a stale name changes nothing."""
    plain = graph.project_layout(PROJECT)
    moved = graph.project_layout(PROJECT, offsets={"gone": (100, 100)})
    assert plain.nodes == moved.nodes and plain.edges == moved.edges


def test_two_cards_never_stack():
    """`graph.overlapping`: a strict rectangle test, so flush is allowed and over is not."""
    placed = graph.project_layout(PROJECT)
    assert graph.overlapping(placed.nodes, "orders_enriched") == [], "the grid never stacks"
    a = _node(placed, "stg_orders")
    b = _node(placed, "stg_customers")
    onto = graph.project_layout(PROJECT, offsets={"stg_orders": (0, b.y - a.y + 10)})
    assert graph.overlapping(onto.nodes, "stg_orders") == ["stg_customers"]
    flush = graph.project_layout(PROJECT, offsets={"stg_orders": (0, b.y + b.h - a.y)})
    assert graph.overlapping(flush.nodes, "stg_orders") == []
    assert graph.overlapping(placed.nodes, "not_here") == []

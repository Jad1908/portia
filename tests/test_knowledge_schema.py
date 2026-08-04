"""The graph's vocabulary — closed, and identified by one property per label.

Nothing here needs a database, which is the point of `schema.py` existing apart
from `store.py`: the rules that decide whether two writers agree about what a
node *is* are testable with no server running.
"""

from __future__ import annotations

import pytest

from portia.knowledge.schema import (
    COLUMN,
    DERIVES_FROM,
    HAS_COLUMN,
    KEY_PROPERTY,
    LABELS,
    MODEL,
    OVERLAPS,
    SOURCE,
    STRUCTURAL,
    Graph,
    Ref,
    column_key,
    render_text,
)


def test_every_label_says_what_identifies_it():
    """A node whose key nobody agreed on becomes two nodes for one thing (§4.8)."""
    assert set(KEY_PROPERTY) == set(LABELS)


def test_the_measured_kind_is_named_but_not_structural():
    """`store.write` rebuilds the structural half and must leave `OVERLAPS` alone
    — a rebuild off the files may not delete a measurement (§5.2)."""
    assert OVERLAPS not in STRUCTURAL
    assert DERIVES_FROM in STRUCTURAL


def test_an_unknown_label_or_kind_is_refused():
    graph = Graph()
    with pytest.raises(ValueError, match="unknown node label"):
        graph.add_node("Entity", "customer")
    a = graph.add_node(SOURCE, "data/a.csv")
    b = graph.add_node(COLUMN, column_key(SOURCE, "data/a.csv", "x"))
    with pytest.raises(ValueError, match="unknown edge kind"):
        graph.add_edge("MENTIONS", a, b)


def test_an_edge_to_a_node_that_isnt_here_is_refused():
    graph = Graph()
    a = graph.add_node(SOURCE, "data/a.csv")
    with pytest.raises(ValueError, match="isn't here"):
        graph.add_edge(HAS_COLUMN, a, Ref(COLUMN, "nope"))


def test_the_same_column_name_on_two_tables_is_two_nodes():
    """The question `OVERLAPS` exists to answer must not be assumed here (§4)."""
    assert column_key(SOURCE, "data/orders.csv", "id") != column_key(MODEL, "orders", "id")


def test_adding_a_node_twice_merges_rather_than_replaces():
    """A source is described by the catalog and referenced by a spec, and neither
    knows everything about it."""
    graph = Graph()
    graph.add_node(SOURCE, "data/a.csv", name="a", summary="what it is")
    graph.add_node(SOURCE, "data/a.csv", size=12)
    node = graph.node(SOURCE, "data/a.csv")
    assert node.properties == {"name": "a", "summary": "what it is", "size": 12}


def test_the_same_edge_twice_is_one_edge():
    """Two records of one fact is what §4.3 refuses; the first write wins."""
    graph = Graph()
    a = graph.add_node(SOURCE, "data/a.csv")
    b = graph.add_node(COLUMN, column_key(SOURCE, "data/a.csv", "x"))
    graph.add_edge(HAS_COLUMN, a, b)
    graph.add_edge(HAS_COLUMN, a, b)
    assert len(graph.edges_of(HAS_COLUMN)) == 1


def test_render_lists_kinds_in_schema_order_never_by_size():
    """`DESIGN.md`'s rule reaches this far: ordering by count reads as a ranking."""
    graph = Graph()
    graph.add_node(MODEL, "mart")
    assert render_text(graph).splitlines()[1:5] == [
        f"  {label:<8} {1 if label == MODEL else 0}" for label in LABELS
    ]

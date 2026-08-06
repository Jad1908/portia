"""`docs/GRAPH_SCHEMA.md` is a reference, so it has to still be true.

A schema document nobody checks is a schema document that describes last month's
graph, and the failure is silent: you read it, believe it, write a Cypher query
against a property that was renamed, and get an empty result rather than an
error. These tests are cheap and they make the doc's claims falsifiable.

They check **coverage, not prose** — that every label, edge kind and op portia
can write is mentioned. What each one *means* is the document's job and no
test's.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from portia.knowledge import measure
from portia.knowledge.schema import (
    KEY_PROPERTY,
    LABELS,
    OVERLAPS,
    STRUCTURAL,
    VIA_OPS,
)

DOC = Path(__file__).resolve().parents[1] / "docs" / "GRAPH_SCHEMA.md"


@pytest.fixture(scope="module")
def text() -> str:
    return DOC.read_text()


def test_every_node_label_is_documented(text):
    for label in LABELS:
        assert f":{label}" in text, f"{label} is in the schema and not in the reference"


def test_every_label_says_what_identifies_it(text):
    """The identifying property is the one thing a reader cannot guess, and the
    one that produces two nodes for one thing when two writers disagree."""
    for label, key in KEY_PROPERTY.items():
        assert f"**`{key}`**" in text, f"{label} is identified by {key}, unsaid in the reference"


def test_every_edge_kind_is_documented(text):
    for kind in (*STRUCTURAL, OVERLAPS):
        assert f"`{kind}`" in text


def test_every_op_that_can_appear_on_a_lineage_edge_is_documented(text):
    """`via` comes from a closed set in `ops/`; the doc names it as closed, so
    the set had better be the one it names."""
    for op in VIA_OPS:
        assert f"`{op}`" in text


def test_every_property_a_measurement_writes_is_documented(text):
    """The measured edge is the one whose properties a reader will actually
    query, and the one whose numbers mean nothing without their names."""
    written = {
        *measure.MEASURED,
        "asked_because",
        "measured_at",
        "left_fingerprint",
        "right_fingerprint",
    }
    for name in written:
        assert f"`{name}`" in text, f"{name} lands on an OVERLAPS edge and is not documented"


def test_the_reference_does_not_claim_entity_exists(text):
    """It is deferred, and a reference that lists it would send someone looking
    for nodes that are not there."""
    assert "does not exist" in text

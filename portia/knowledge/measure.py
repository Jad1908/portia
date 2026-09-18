"""A measured overlap, as an edge — the half of the graph that costs a query.

`docs/KNOWLEDGE_GRAPH.md` §4.3, §4.4 and §4.5. Everything else in this package
restates files; this is the only thing in it that is a *measurement*, and the
three rules that follow from that are all here.

**One edge holds both numbers** (§4.3). Overlap is not symmetric — 98% of orders'
customer ids may exist in customers while only 40% of customers appear in orders
— and both matter. Two edges facing opposite ways would be two records of one
measurement, and they would drift.

**The edge carries the reason the agent asked for it** (§4.4), and that is the
one thing here that is not a number. A measured zero means *these two columns
share no values*; it does not mean *unrelated*, and `France` against `FRA` is
both a true zero and the most important pair in the project. Without the
hypothesis attached, the zero reads as a dead end; with it, it reads as a work
item. So `asked_because` sits beside the numbers, **labelled as the agent's
words**, and the rule that keeps it honest is `spec.py`'s: the sentence may never
be generated from the numbers, and the numbers may never be adjusted to fit the
sentence.

**The edge records what it was measured against** (§4.5). Both ends' fingerprints
at the moment of measurement, so a number taken against a file that has since
been re-issued is *detectable* — and marked rather than deleted, because a
deleted edge is indistinguishable from one nobody ever measured, which is the
ambiguity this whole design exists to remove.

**Samples stay off it** (§4.4). Seeing `France, Germany` beside `FRA, DEU` is what
makes a zero investigable, and that is what the disclosure ladder is for: the
edge says which pair and why, and the agent climbs to `profile_source` for
values. Copying them here would put data in a metadata store and create a second
copy to go stale.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from portia.knowledge.schema import OVERLAPS, Edge, Ref

#: Which measured numbers go on the edge. A subset of what `checks.join
#: .column_overlap` returns, and deliberately a small one: the per-side row and
#: distinct counts are re-derivable from the catalog and would be a second copy
#: to go stale, while these four are the measurement itself.
MEASURED = (
    "n_shared_values",
    "left_coverage",
    "right_coverage",
    "comparable_types",
)


@dataclass(frozen=True)
class Pair:
    """Two columns the agent chose to compare, and why it chose them.

    ``asked_because`` is required, not optional. Under a code sweep there is no
    reason to record because nothing had one (§4.4); the agent picking the pairs
    is what makes the sentence available, and a pair arriving without one is a
    pair nobody can interpret the zero of.
    """

    left: Ref
    left_column: str
    right: Ref
    right_column: str
    asked_because: str


def overlap_edge(
    pair: Pair,
    measurement: dict,
    *,
    left_fingerprint: str | None = None,
    right_fingerprint: str | None = None,
    when: datetime | None = None,
) -> Edge:
    """One `OVERLAPS` edge: the numbers, the reason, and what it was taken against.

    Direction is *left to right*, and it is the only thing that says which
    coverage is which. `query.py` reads it back with `startNode(r)` for exactly
    that reason — an undirected read of an edge holding two directional numbers
    would be a coin flip.
    """
    stamp = (when or datetime.now()).astimezone().isoformat(timespec="seconds")
    return Edge(
        OVERLAPS,
        pair.left.column(pair.left_column),
        pair.right.column(pair.right_column),
        {
            **{key: measurement[key] for key in MEASURED if key in measurement},
            # The agent's words, named so nobody mistakes them for a measurement.
            "asked_because": pair.asked_because,
            "measured_at": stamp,
            "left_fingerprint": left_fingerprint,
            "right_fingerprint": right_fingerprint,
        },
    )

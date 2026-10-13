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

#: Which tool took a measurement, on the edge as ``origin``. A **closed** set,
#: like `schema.VIA_OPS`, and for the same reason: a vocabulary that grows per
#: run cannot be queried.
#:
#: ``measured`` is a pair the agent chose and `measure_overlaps` compared.
#: ``join_check`` is the key of a join `join_findings` checked
#: *(2026-10-13)*: that check measures exactly these four numbers for its
#: key, and used to throw them away. The logs held 25 join checks and 5
#: measurements, so most of what the agent learned about how tables connect
#: never reached the graph. The two are the same measurement with a different
#: occasion, and the occasion is worth keeping: a join check's reason is why
#: the agent was about to *join*, which is a stronger claim than *compare*.
#:
#: An edge written before this property existed has none, and was written by
#: `measure_overlaps`, the only writer there was; reads say ``measured`` for it.
ORIGIN = "origin"
ORIGIN_MEASURED = "measured"
ORIGIN_JOIN_CHECK = "join_check"
ORIGINS = (ORIGIN_MEASURED, ORIGIN_JOIN_CHECK)

#: The properties that name a side, in left/right pairs. Turning an edge round
#: swaps each pair and nothing else (:func:`seen_from`).
SIDED = (("left_coverage", "right_coverage"), ("left_fingerprint", "right_fingerprint"))


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
    origin: str = ORIGIN_MEASURED,
    left_fingerprint: str | None = None,
    right_fingerprint: str | None = None,
    when: datetime | None = None,
) -> Edge:
    """One `OVERLAPS` edge: the numbers, the reason, and what it was taken against.

    Direction is *left to right*, and it is the only thing that says which
    coverage is which. `query.py` reads it back with `startNode(r)` for exactly
    that reason — an undirected read of an edge holding two directional numbers
    would be a coin flip.

    ``origin`` says which tool took it (:data:`ORIGINS`).
    """
    if origin not in ORIGINS:
        raise ValueError(f"unknown origin {origin!r} — have: {', '.join(ORIGINS)}")
    stamp = (when or datetime.now()).astimezone().isoformat(timespec="seconds")
    return Edge(
        OVERLAPS,
        pair.left.column(pair.left_column),
        pair.right.column(pair.right_column),
        {
            **{key: measurement[key] for key in MEASURED if key in measurement},
            # The agent's words, named so nobody mistakes them for a measurement.
            "asked_because": pair.asked_because,
            ORIGIN: origin,
            "measured_at": stamp,
            "left_fingerprint": left_fingerprint,
            "right_fingerprint": right_fingerprint,
        },
    )


def from_join_report(report: dict) -> dict:
    """The four numbers of :data:`MEASURED`, read off a `checks.join` report.

    **The same measurement, not a lookalike.** `join_report` and
    `column_overlap` share their CTEs, their kind rule and their coverage
    arithmetic (`checks/join.py`), so a one-column join's shared keys *are*
    the column pair's shared values, its coverages are the same two shares of
    rows, and ``key_dtype_match`` is ``comparable_types``. Only the names
    differ, and this is where they are translated, once.
    """
    overlap = report["overlap"]
    return {
        "n_shared_values": overlap["n_shared_keys"],
        "left_coverage": overlap["left_coverage"],
        "right_coverage": overlap["right_coverage"],
        "comparable_types": report["key_dtype_match"],
    }


def seen_from(properties: dict, *, from_start: bool) -> dict:
    """An edge's properties with *left* meaning the end you are standing on.

    The edge points from the column it was measured from, and its two
    coverages and two fingerprints are named for that direction (§4.3). A
    reader standing on the other end gets them swapped here, once, rather than
    every caller remembering which is which: a coverage read from the wrong
    end is a true number about the other table.
    """
    seen = dict(properties)
    if not from_start:
        for left, right in SIDED:
            seen[left], seen[right] = properties.get(right), properties.get(left)
    return seen


def still_current(
    seen: dict, left_fingerprint: str | None, right_fingerprint: str | None
) -> bool | None:
    """Whether a stored measurement was taken against the tables as they are now.

    ``seen`` is the edge as :func:`seen_from` turns it, so *left* is the
    caller's left. ``True`` when both recorded fingerprints match the current
    ones, ``False`` when either moved, and ``None`` when any of the four is
    missing: *cannot tell* is not *fine* (§4.5), and a caller deciding whether
    to reuse a number treats it as moved.
    """
    recorded = (seen.get("left_fingerprint"), seen.get("right_fingerprint"))
    current = (left_fingerprint, right_fingerprint)
    if None in recorded or None in current:
        return None
    return recorded == current

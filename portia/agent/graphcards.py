"""The knowledge graph's answers as the agent reads them: bounded cards, every cut said.

**Why a module of its own** *(2026-10-13, the tool revamp,
`sandbox/limits/REVAMP_SPEC.md` §E)*. `knowledge/query.py` answers a question
about the graph whole, because the terminal can print all of it and because a
cap is not a fact about the graph. A cap is a decision about what an answer may
cost a context window, and the sentence saying how to ask for the rest is
prompt text, which `knowledge/` does not load. So the facts come from there and
the cards are cut here, with each cut announced through `core/listing` and a
`prompts/more/` sentence.

**The graph is the agent's map between tables**: how to get from one to
another, what is known about how a table's columns link to other tables, and
where a column came from. The source tools treat one table on its own. So the
card for a table is its neighbours and nothing about its own columns but the
ones a neighbour is linked by; the user called the old answer's list of every
connected column a naive dump, and it was gone before this module was written
(`query.py`).

**Every cut here is by size, in an order that carries no claim** (§6.1).
Neighbours are cut in name order, a neighbour's pairs in column order, and
when the pairs of every neighbour together are more than a card holds, each
neighbour shows the same number of them, the most that fits (`query.fair_share`,
the rule the picture already shares its columns by). Nothing is cut by
coverage or by shared values: that would be deciding which link matters.

**Lists are a header once and rows as lists** (`core/listing.py`). A
neighbour's measured pairs are rows inside its row, and their fields are named
once for the whole card, as ``pair_fields``, before the list that uses them.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from portia.agent import prompts
from portia.core import listing
from portia.knowledge import query

#: Neighbouring tables one card lists, in name order. **A placeholder**, as all
#: the revamp's caps are. A group puts every member beside every other, so this
#: is the number that bites first on a project of one big group; a neighbour
#: with no pairs costs a few dozen characters.
NEIGHBOURS = 50

#: Measured pairs one neighbour shows on a table's card, before
#: `graph_lookup(table, neighbour=…)` lists them all. A placeholder.
NEIGHBOUR_PAIRS = 3

#: Measured pairs one table's card shows in all. When the neighbours' pairs
#: together are more, each neighbour shows the same number, the most that fits.
#: A placeholder: 40 rows at their widest, a long reason on each, is about
#: 13,000 characters.
CARD_PAIRS = 40

#: Pairs between two tables that `graph_lookup(table, neighbour=…)` lists, each
#: with both columns' facts, before pointing at one column. A placeholder.
PAIRS = 50

#: Pairings this project made elsewhere that a table's card replays. A placeholder.
PRECEDENTS = 10

#: Groups a table's card names. More than a handful is unusual.
GROUPS = 10

#: Rows each list of a column's card shows: upstream, downstream, origins, what
#: it was compared with, precedents. A placeholder.
COLUMN_ROWS = 25

#: Measured pairs one step of a route shows. A placeholder.
STEP_PAIRS = 3

#: Pairs a `measure_overlaps` receipt lists, in the order they were sent. A
#: placeholder: at their widest, two long names a side, this is under 20,000
#: characters, and `tools._overlaps_receipt` stands behind it.
RECEIPT_ROWS = 50

#: Where a pair in a receipt was answered from: the graph, which held it and
#: still matched both tables, or a query run now.
FROM_GRAPH = "graph"
FROM_QUERY = "query"

NEIGHBOUR_FIELDS = ("table", "kind", "reads", "read_by", "groups", "n_pairs", "pairs")

#: A measured pair as the table asked about sees it: ``coverage`` is the share
#: of its rows whose value is on the other side, ``other_coverage`` the reverse.
PAIR_FIELDS = (
    "column",
    "other_column",
    "n_shared_values",
    "coverage",
    "other_coverage",
    "comparable_types",
    "origin",
    "asked_because",
    "measured_at",
    "stale",
)

#: Between two tables, each pair carries what both columns look like
#: (`query.PAIR_FACTS`): a zero is unreadable without them (§9.1).
BETWEEN_FIELDS = (*PAIR_FIELDS, "column_facts", "other_facts")

GROUP_FIELDS = ("name", "context", "n_members")
PRECEDENT_FIELDS = (
    "column",
    "against_table",
    "against_column",
    "by_table",
    "n_shared_values",
    "asked_because",
)
LINEAGE_FIELDS = ("hops", "table", "kind", "column", "via", "step")
ORIGIN_FIELDS = ("table", "kind", "column", "derivation")
COMPARED_FIELDS = (
    "table",
    "kind",
    "other_column",
    "n_shared_values",
    "coverage",
    "other_coverage",
    "comparable_types",
    "origin",
    "asked_because",
    "measured_at",
    "stale",
    "other_facts",
)
ROUTE_FIELDS = ("n_steps", "tables", "steps")

#: A step's pairs in a route: the link's numbers without its reason, which is
#: one `neighbour` call away and would be most of a route's size.
ROUTE_PAIR_FIELDS = ("column", "other_column", *query.LINK_FIELDS)

RECEIPT_FIELDS = (
    "left",
    "right",
    "n_shared_values",
    "left_coverage",
    "right_coverage",
    "comparable_types",
    "left_distinct",
    "right_distinct",
    "from",
    "measured_at",
)


def vocabulary() -> dict[str, int]:
    """The numbers `tools/graph_lookup.md` states, filled from the code that holds them."""
    return {
        "route_hops": query.ROUTE_HOPS,
        "routes": query.ROUTES,
        "lineage_hops": query.LINEAGE_HOPS,
    }


def card(answer: dict) -> dict:
    """The card for one of `query.lookup`'s four answers, told apart by their keys."""
    if "routes" in answer:
        return _routes(answer)
    if "neighbour" in answer:
        return _between(answer)
    if "upstream" in answer:
        return _lineage(answer)
    return _neighbourhood(answer)


def _rows(items: Sequence[dict], fields: Sequence[str]) -> list[list[Any]]:
    return [[item.get(f) for f in fields] for item in items]


def _listed(
    fields: Sequence[str],
    items: Sequence[dict],
    cap: int,
    more: Callable[[int, int], str],
) -> dict:
    """The first ``cap`` of ``items`` as a listing, and ``more(left_out, n)`` when cut."""
    shown = items[:cap]
    left_out = len(items) - len(shown)
    return listing.listing(
        fields,
        _rows(shown, fields),
        n=len(items),
        more=more(left_out, len(items)) if left_out else None,
    )


# --- a table ----------------------------------------------------------------


def _neighbourhood(answer: dict) -> dict:
    """Its neighbours, each with how it is linked and a fair share of its pairs."""
    table = answer["table"]
    name = table["name"]
    neighbours = answer["neighbours"]
    shown = neighbours[:NEIGHBOURS]
    share = query.fair_share([min(len(n["pairs"]), NEIGHBOUR_PAIRS) for n in shown], CARD_PAIRS)
    rows, cut = [], 0
    for neighbour in shown:
        pairs = neighbour["pairs"][: min(NEIGHBOUR_PAIRS, share)]
        cut += len(pairs) < len(neighbour["pairs"])
        rows.append(
            [
                neighbour["name"],
                neighbour["kind"],
                neighbour["reads"],
                neighbour["read_by"],
                neighbour["groups"],
                len(neighbour["pairs"]),
                _rows(pairs, PAIR_FIELDS),
            ]
        )
    left_out = len(neighbours) - len(shown)
    out: dict[str, Any] = {"table": table}
    if any(n["pairs"] for n in shown):
        out["pair_fields"] = list(PAIR_FIELDS)
    out["neighbours"] = listing.listing(
        NEIGHBOUR_FIELDS,
        rows,
        n=len(neighbours),
        more=prompts.more("graph_neighbours", left_out=left_out, n=len(neighbours), table=name)
        if left_out
        else None,
    )
    if cut:
        out["pairs_more"] = prompts.more("graph_pairs", n_cut=cut)
    if answer["groups"]:
        out["groups"] = _listed(
            GROUP_FIELDS,
            answer["groups"],
            GROUPS,
            lambda left, n: prompts.more("graph_groups", left_out=left, n=n),
        )
    if answer["precedents"]:
        out["precedents"] = _listed(
            PRECEDENT_FIELDS,
            answer["precedents"],
            PRECEDENTS,
            lambda left, n: prompts.more("graph_precedents", left_out=left, n=n, table=name),
        )
    return out


# --- two tables -------------------------------------------------------------


def _between(answer: dict) -> dict:
    """Everything linking two tables, the pairs with both columns' facts."""
    table, neighbour = answer["table"]["name"], answer["neighbour"]["name"]
    return {
        "table": answer["table"],
        "neighbour": answer["neighbour"],
        "reads": answer["reads"],
        "read_by": answer["read_by"],
        "groups": answer["groups"],
        "pairs": _listed(
            BETWEEN_FIELDS,
            answer["pairs"],
            PAIRS,
            lambda left, n: prompts.more(
                "graph_neighbour_pairs", left_out=left, n=n, table=table, neighbour=neighbour
            ),
        ),
    }


# --- one column -------------------------------------------------------------


def _lineage(answer: dict) -> dict:
    """A column's lineage each way, where it bottoms out, and what it was compared with."""
    column = answer["column"]
    table = column["table"]

    def walked(key: str, fields: Sequence[str]) -> dict:
        return _listed(
            fields,
            answer[key],
            COLUMN_ROWS,
            lambda left, n: prompts.more("graph_lineage", left_out=left, n=n),
        )

    out: dict[str, Any] = {
        "column": column,
        "upstream": walked("upstream", LINEAGE_FIELDS),
        "downstream": walked("downstream", LINEAGE_FIELDS),
        "origins": walked("origins", ORIGIN_FIELDS),
        "compared_with": _listed(
            COMPARED_FIELDS,
            answer["compared_with"],
            COLUMN_ROWS,
            lambda left, n: prompts.more("graph_compared", left_out=left, n=n, table=table),
        ),
    }
    if answer["precedents"]:
        out["precedents"] = _listed(
            PRECEDENT_FIELDS[1:],
            answer["precedents"],
            COLUMN_ROWS,
            lambda left, n: prompts.more("graph_precedents", left_out=left, n=n, table=table),
        )
    return out


# --- routes -----------------------------------------------------------------


def _routes(answer: dict) -> dict:
    """The routes between two tables, each step with how it links and its pairs."""
    rows, cut, any_pairs = [], 0, False
    for route in answer["routes"]:
        steps = []
        for step in route["steps"]:
            said: dict[str, Any] = {}
            if step["reads"]:
                said["reads"] = True
            if step["read_by"]:
                said["read_by"] = True
            if step["pairs"]:
                any_pairs = True
                said["n_pairs"] = len(step["pairs"])
                said["pairs"] = _rows(step["pairs"][:STEP_PAIRS], ROUTE_PAIR_FIELDS)
                cut += len(step["pairs"]) > STEP_PAIRS
            steps.append(said)
        rows.append([len(route["steps"]), route["tables"], steps])

    n = answer["n_routes"]
    table, to = answer["table"]["name"], answer["to"]["name"]
    out: dict[str, Any] = {"table": answer["table"], "to": answer["to"]}
    if any_pairs:
        out["pair_fields"] = list(ROUTE_PAIR_FIELDS)
    out["routes"] = listing.listing(
        ROUTE_FIELDS,
        rows,
        n=n,
        more=prompts.more(
            "graph_routes",
            left_out=n - len(rows),
            n=n,
            hops=query.ROUTE_HOPS,
            table=table,
            to=to,
        )
        if n > len(rows)
        else None,
    )
    if cut:
        out["pairs_more"] = prompts.more("graph_pairs", n_cut=cut)
    return out


# --- what measure_overlaps did ----------------------------------------------


def overlaps_receipt(
    rows: Sequence[dict], *, n_from_graph: int, n_measured: int, kept: dict
) -> dict:
    """`measure_overlaps`' answer: counts, whether what it measured was kept, a line a pair.

    **A receipt, because it writes** (the revamp's rule, and `record_step`'s
    since 2026-10-08). The old answer was every measurement in full with both
    sides' counts, and about 78 pairs in one call were refused for size after
    every one of them had been written. One short row a pair, cut at
    :data:`RECEIPT_ROWS` with the cut said, is bounded however many pairs come
    in; what both columns look like is `graph_lookup`'s, one call away.

    ``kept`` is ``{"stored": …, "not_stored_because"?: …}``, empty when nothing
    was measured and so nothing needed keeping.
    """
    return {
        "n_pairs": len(rows),
        "n_from_graph": n_from_graph,
        "n_measured": n_measured,
        **kept,
        "pairs": _listed(
            RECEIPT_FIELDS,
            rows,
            RECEIPT_ROWS,
            lambda left, n: prompts.more("measured_pairs", left_out=left, n=n),
        ),
    }

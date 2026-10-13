"""Reading the graph — a fixed set of questions, not a query language.

`docs/KNOWLEDGE_GRAPH.md` §9.4 phase B: *one tool, fixed queries.* The whole
stack was chosen so the agent **can** write its own Cypher (§3.4), and §7 leaves
open whether it should; that is a separate decision from whether traversal helps
at all, and this is the half that answers the second question first.

**What the graph is for is navigation between tables** (§9.1, and the tool
revamp of 2026-10-13, `sandbox/limits/REVAMP_SPEC.md`). The disclosure ladder
is depth on one source — `describe_source` → `profile_source` →
`join_findings`, every rung answering *tell me more about this table*. The
graph answers *which* table, how to get from one to another, and where a
column came from. It sits **before** L2, not above L4.

Four questions, one per answer below:

- **A table**: its neighbourhood. One entry per table it is linked to — it
  reads it, it is read by it, they share a group, or a pair of their columns
  was measured — with the measured pairs inside the entry they belong to.
- **A table and a neighbour**: every measured pair between the two, with what
  both columns look like beside each number.
- **A table and a column**: that column's lineage, a few hops each way, where
  it bottoms out, and what it was compared with.
- **A table and another to reach**: the routes between them
  (:func:`routes`), found in Python over one query's table-to-table links.

**This module returns facts, whole; the agent's cards are cut elsewhere**
(`agent/graphcards.py`). A cap is a decision about what an answer may cost a
context window, and the sentence that says how to ask for the rest is prompt
text, which this layer does not load (`catalog` and `ops/sql.py` sit below
the prompts for the same reason). The terminal (`cli/knowledge.py`) reads
these whole.

*The table answer's history, kept because both halves were argued.* Its
original rule was **tables back, never column pairs**, on the grounds that a
router returning fifty things has not routed. Measured on the AQN project,
`stations` had **four** measured pairs and the whole 23-source project
**eighteen**, at 3,973 characters; what the rule bought was `overlaps
AQN_TR_LOCMAP (2 measured pair(s))`, four measurements reduced to the number
four. So the pairs came back, grouped under the neighbour each connects to.
**And `connected_columns` is gone** *(2026-10-13)*: the names of this table's
columns that carry any relationship, which on a table whose columns are all
lineage is every column. The user called it a naive dump, and it was: a
column that matters to a neighbour is in that neighbour's pairs, and the
column list is `describe_source`'s.

**It says which pairings this project made that this table has not**
(:func:`_precedents`). Not a shelf of candidates: three of those were tried
against the real graph and all three fail, which that function records. What
this replays is a measurement somebody already took, where the same column name
elsewhere has been compared to something and this table's has not.

**Nothing here ranks** (§6.1). Every list comes back in name order, a route in
order of how many steps it takes and then by name, which carry no claim; there
is no "best candidate", no sort by coverage, no score. The numbers are reported
and the agent decides which matter.

**Every question is asked of one project** (`schema.PROJECT`). One server holds
them all, so the anchor of each query names its project; the traversals from
there do not have to, because an edge never leaves the project it was written
in. Unscoped, `lookup` could resolve a table this project does not have and the
picture drew every project at once.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from difflib import get_close_matches
from typing import Any

from portia.knowledge import measure
from portia.knowledge.measure import MEASURED, ORIGIN, ORIGIN_MEASURED
from portia.knowledge.schema import (
    COLUMN,
    DERIVATION,
    DERIVES_FROM,
    GROUP,
    HAS_COLUMN,
    IN_GROUP,
    KEY_PROPERTY,
    MODEL,
    OVERLAPS,
    POSITION,
    PROJECT,
    READS,
    SHAPE_FACTS,
    SOURCE,
)

#: How far a lineage walk follows `DERIVES_FROM` looking for the files a column
#: ultimately came from. A pipeline deeper than this is a project that should be
#: asked about a nearer model instead — and an uncapped variable-length match is
#: the one way a graph query on a small graph can still be slow.
MAX_HOPS = 6

#: How many hops each way a column's lineage lists, upstream and downstream.
#: Three is a staging, an intermediate and a mart: the whole of an ordinary
#: build, and a short enough walk that a composite multiplying its paths stays
#: a handful of rows. Anything further is one more question, asked of a column
#: the answer already named.
LINEAGE_HOPS = 3

#: The most steps a route between two tables takes. **A placeholder**, as the
#: revamp's caps are: three is *A joins B joins C joins D*, already more hops
#: than a join anyone builds on without stopping to look.
ROUTE_HOPS = 3

#: The most routes one answer lists, fewest steps first. Also a placeholder.
ROUTES = 10

#: Longest list an error names. Reached only by something unusual, and when it
#: is reached the message **says so** rather than quietly returning a slice.
MAX_ROWS = 25

#: How many near misses an unknown column's message names.
CLOSE_MATCHES = 3


def lookup(
    session: Any,
    table: str,
    column: str | None = None,
    *,
    neighbour: str | None = None,
    to: str | None = None,
    project: str,
) -> dict:
    """One of four answers about ``table``, within one project.

    ``table`` may be a source's name, a source's path, or a model's name, and so
    may ``neighbour`` and ``to``. With none of the three optional arguments the
    answer is the table's neighbourhood; ``neighbour`` narrows it to one
    neighbouring table, ``column`` moves to one column's lineage, and ``to``
    asks for the routes between two tables. One at a time: they are different
    questions, and an answer to two at once would be two answers.

    ``project`` is keyword-only and has no default: the server holds every
    project on the machine, and a default here would be a question about all of
    them that reads like a question about this one.
    """
    asked = [
        name for name, value in (("column", column), ("neighbour", neighbour), ("to", to)) if value
    ]
    if len(asked) > 1:
        raise ValueError(f"one question at a time: {', '.join(asked)} were all given")
    found = _resolve(session, table, project)
    if to:
        return _routes_answer(session, found, _resolve(session, to, project), project)
    if neighbour:
        return _between_answer(session, found, _resolve(session, neighbour, project), project)
    if column:
        return _column_answer(session, found, column, project)
    return _table_answer(session, found, project)


# --- resolving --------------------------------------------------------------


#: A Source or a Model, found by whichever of its two names the caller used. A
#: model has only a name; a source has a name *and* the path that identifies it,
#: and both are things a person or an agent will reasonably type.
_TABLE_BY_NAME = (
    f"MATCH (t) WHERE (t:{SOURCE} OR t:{MODEL}) AND t.{PROJECT} = $project "
    "AND $name IN [t.name, t.path]"
)

#: What every answer needs to know about the table it is about. Counted rather
#: than listed: a 1,579-column table's names were fetched on every question to
#: be counted.
_TABLE_FIELDS = (
    "labels(t)[0] AS kind, t.name AS name, t.path AS path, "
    f"size([(t)-[:{HAS_COLUMN}]->(c) | c]) AS n_columns"
)


def _resolve(session: Any, table: str, project: str) -> dict:
    """Which node this name means, or an error naming what there is instead.

    A source and a model may share a name. The source wins, as it does in
    `handlers._entry`: it is the one with a file behind it. ``kind`` sorts
    ``Source`` after ``Model``, hence descending.
    """
    rows = _run(
        session,
        f"{_TABLE_BY_NAME} RETURN {_TABLE_FIELDS} ORDER BY kind DESC",
        name=table,
        project=project,
    )
    if not rows:
        raise ValueError(_unknown_table(session, table, project))
    found = dict(rows[0])
    found["key"] = found["path"] if found["kind"] == SOURCE else found["name"]
    return found


def _unknown_table(session: Any, table: str, project: str) -> str:
    """The message a miss produces — and it has to distinguish two very different
    misses, because "no such table" and "the graph was never built" ask for
    completely different next moves."""
    known = [
        r["name"]
        for r in _run(
            session,
            f"MATCH (t) WHERE (t:{SOURCE} OR t:{MODEL}) AND t.{PROJECT} = $project "
            f"RETURN t.name AS name ORDER BY name LIMIT {MAX_ROWS}",
            project=project,
        )
        if r["name"]
    ]
    if not known:
        return _EMPTY_GRAPH
    return f"no table {table!r} in the knowledge graph — have: {', '.join(known)}"


_EMPTY_GRAPH = (
    "the knowledge graph is empty — build it with 'python -m portia.cli.knowledge --write'"
)


def _unknown_column(session: Any, found: dict, column: str, project: str) -> str:
    """A miss on a column: how many the table has, and the near misses.

    **Not every name**, which is what it used to say: on a 1,579-column table
    that was one error longer than the harness passes whole. The names that
    differ only in case come first, because a typed name folded the wrong way is
    the commonest miss.
    """
    names = [
        row["name"]
        for row in _run(
            session,
            f"MATCH {_match(found)}-[:{HAS_COLUMN}]->(c:{COLUMN}) RETURN c.name AS name",
            key=found["key"],
            project=project,
        )
    ]
    folded = [name for name in names if name.lower() == column.lower()]
    close = [*folded, *get_close_matches(column, names, n=CLOSE_MATCHES)]
    near = list(dict.fromkeys(close))[:CLOSE_MATCHES]
    where = f"no column {column!r} on {found['name']!r}, which has {len(names)}"
    return f"{where} — closest: {', '.join(near)}" if near else f"{where}, none named like it"


def _match(found: dict, var: str = "t", param: str = "key") -> str:
    """``(t:Model {project: $project, name: $key})`` — the node, by its identity.

    Which is the pair, not the key alone: two projects may each hold a
    ``data/orders.csv`` and they are two different tables (`schema.PROJECT`).
    ``var`` and ``param`` let one query anchor two tables.
    """
    label = found["kind"]
    return f"({var}:{label} {{{PROJECT}: $project, {KEY_PROPERTY[label]}: ${param}}})"


def _header(found: dict) -> dict:
    """A table as an answer names it: its kind, its two names, how wide it is.

    ``n_columns`` is there so nobody reads a short list of pairs as a small
    table.
    """
    return {key: found[key] for key in ("kind", "name", "path", "n_columns")}


# --- a measured pair, as a reader on one end sees it ------------------------


#: What travels with each end of a measured pair, beyond the column's name.
#:
#: **These are what make the measurement readable, and without them a zero is
#: unreadable.** Four measured zeros on `stations` look identical as bare numbers.
#: With the distinct counts beside them, `LOCATION_CODE` (**4** distinct)
#: against `AQN_TR_LOCMAP.VALUE` (56) is close to a non-event, while
#: `GEOGRAPHIC_COUNTRY` (**179** country names) against
#: `..._READING_DETAILS.COUNTRY` (**16**) sharing nothing is the mapping job and
#: the most valuable finding in that project. They are the same line without
#: this.
#:
#: `null_rate` and `flags` are deliberately **not** here: `profile_source` is
#: one call away.
PAIR_FACTS = ("role", "n_distinct", *SHAPE_FACTS)

#: What travels with the measurement itself, as the edge holds it. The
#: fingerprints are **not** here: they exist so `stale` can be computed, `stale`
#: is in the same answer, and two opaque `"{size}:{mtime}"` strings per edge are
#: machinery the reader cannot act on. Nor is `project`, which every row of
#: every answer would repeat.
MEASUREMENT_FACTS = (*MEASURED, "asked_because", ORIGIN, "measured_at")


def _pick(properties: dict, keys: tuple[str, ...]) -> dict:
    """The named properties that are actually there.

    Absent rather than null: a `top` of ``None`` on every numeric column is
    tokens spent saying *not applicable*, and the shape facts are absent by
    kind on purpose (`schema.SHAPE_FACTS`).
    """
    return {key: properties[key] for key in keys if properties.get(key) is not None}


def pair_from_row(row: dict) -> dict:
    """One measured pair, turned to face the table asked about.

    The edge's two coverages are named for the direction it was measured in
    (`measure.seen_from`). A reader standing on a table wants *this column's*
    share and *the other's*, so ``coverage`` is the share of this table's rows
    whose value is found on the other side, and ``other_coverage`` the reverse,
    whichever way the edge points. An edge written before ``origin`` existed was
    written by `measure_overlaps`, the only writer there was.

    ``row`` is what :data:`_PAIR_RETURN` returns. Pure, so it is tested without
    a database.
    """
    seen = measure.seen_from(row["measured"] or {}, from_start=bool(row["from_here"]))
    return {
        "column": row["column"],
        "other_column": row["other_column"],
        "n_shared_values": seen.get("n_shared_values"),
        "coverage": seen.get("left_coverage"),
        "other_coverage": seen.get("right_coverage"),
        "comparable_types": seen.get("comparable_types"),
        ORIGIN: seen.get(ORIGIN) or ORIGIN_MEASURED,
        "asked_because": seen.get("asked_because"),
        "measured_at": seen.get("measured_at"),
        "stale": row["stale"],
        "column_facts": _pick(row.get("near") or {}, PAIR_FACTS),
        "other_facts": _pick(row.get("far") or {}, PAIR_FACTS),
    }


def turned(pair: dict) -> dict:
    """A pair as the table on its other end sees it: each column and coverage swapped."""
    return {
        **pair,
        "column": pair["other_column"],
        "other_column": pair["column"],
        "coverage": pair.get("other_coverage"),
        "other_coverage": pair.get("coverage"),
        "column_facts": pair.get("other_facts", {}),
        "other_facts": pair.get("column_facts", {}),
    }


#: Whether a measurement is still backed by the data it was taken from (§4.5),
#: worked out at **read** time by comparing the fingerprints the edge recorded
#: against the ones its two tables carry now. Nothing has to re-walk the graph
#: when a file changes, and nothing has to be invalidated: the edge is **marked,
#: never deleted**, because a deleted edge is indistinguishable from one nobody
#: ever measured, which is the ambiguity §4.4 exists to remove.
#:
#: The `CASE` is because the edge's two fingerprints are *left* and *right* while
#: the query's two tables are *this one* (``t``) and *the other one* (``o``), and
#: which is which depends on the direction the measurement was taken in. `null`
#: when a fingerprint is missing, which honestly reads as "cannot tell" rather
#: than "fine".
def _moved(near: str, far: str) -> str:
    """Either end no longer matching what it was measured against.

    ``t`` is the table asked about and ``o`` the one on the other end of the
    edge; ``near``/``far`` say which of the edge's two recorded fingerprints
    belongs to which of them.
    """
    return f"r.{near}_fingerprint <> t.fingerprint OR r.{far}_fingerprint <> o.fingerprint"


_STALE = (
    "CASE WHEN startNode(r).key = c.key "
    f"THEN {_moved('left', 'right')} "
    f"ELSE {_moved('right', 'left')} END AS stale"
)

#: One measured pair, for :func:`pair_from_row`: ``c`` is this table's column,
#: ``d`` the other's, ``r`` the edge between them and ``o`` the other table.
_PAIR_RETURN = (
    "c.name AS column, properties(c) AS near, d.name AS other_column, properties(d) AS far, "
    f"properties(r) AS measured, startNode(r).key = c.key AS from_here, {_STALE}"
)

#: The table on the other end of a link, as ``o``.
_OTHER_RETURN = "labels(o)[0] AS kind, o.name AS name, o.path AS path"


# --- a table's neighbourhood ------------------------------------------------


def _table_answer(session: Any, found: dict, project: str) -> dict:
    """Every table this one is linked to, with what links them, and its groups."""
    node = _match(found)
    args = {"key": found["key"], "project": project}
    reads = _run(
        session,
        f"MATCH {node}-[:{READS}]->(o) RETURN 'reads' AS link, {_OTHER_RETURN} UNION ALL "
        f"MATCH {node}<-[:{READS}]-(o) RETURN 'read_by' AS link, {_OTHER_RETURN}",
        **args,
    )
    grouped = _run(
        session,
        f"MATCH {node}-[:{IN_GROUP}]->(g:{GROUP})<-[:{IN_GROUP}]-(o) WHERE o <> t "
        f"RETURN g.name AS group_name, {_OTHER_RETURN}",
        **args,
    )
    pairs = _run(
        session,
        f"MATCH {node}-[:{HAS_COLUMN}]->(c:{COLUMN})-[r:{OVERLAPS}]-"
        f"(d:{COLUMN})<-[:{HAS_COLUMN}]-(o) WHERE o <> t "
        f"RETURN {_OTHER_RETURN}, {_PAIR_RETURN}",
        **args,
    )
    groups = _run(
        session,
        f"MATCH {node}-[:{IN_GROUP}]->(g:{GROUP}) RETURN g.name AS name, g.context AS context, "
        f"size([(m)-[:{IN_GROUP}]->(g) | m]) AS n_members ORDER BY name",
        **args,
    )
    return {
        "table": _header(found),
        "neighbours": gather_neighbours(reads, grouped, pairs),
        "groups": groups,
        "precedents": _precedents(session, node, args),
    }


def gather_neighbours(reads: list[dict], grouped: list[dict], pairs: list[dict]) -> list[dict]:
    """One entry per neighbouring table, whatever links them, in name order.

    **Grouped by neighbour because the neighbour is the relationship.** The
    question the agent is standing in the middle of is *how does this table
    connect to that one*, and the reads, the groups and the column pairs are
    the answer to it rather than three lists beside each other. A table is
    identified as the store identifies it, a source by its path and a model by
    its name, so a source and a model that share a name stay two neighbours.

    ``reads`` rows carry ``link`` (``reads`` or ``read_by``), ``grouped`` rows
    the ``group_name`` they share, ``pairs`` rows what :func:`pair_from_row`
    reads. Pure, so the shape is tested without a database. Nothing here is
    ordered by a number (§6.1): neighbours by name, groups by name, pairs by
    their two column names.
    """
    found: dict[tuple[str, str], dict] = {}

    def entry(row: dict) -> dict:
        key = row["path"] if row["kind"] == SOURCE else row["name"]
        return found.setdefault(
            (row["kind"], key),
            {
                "kind": row["kind"],
                "name": row["name"] or row["path"],
                "path": row["path"],
                "reads": False,
                "read_by": False,
                "groups": [],
                "pairs": [],
            },
        )

    for row in reads:
        entry(row)[row["link"]] = True
    for row in grouped:
        groups = entry(row)["groups"]
        if row["group_name"] not in groups:
            groups.append(row["group_name"])
    for row in pairs:
        entry(row)["pairs"].append(pair_from_row(row))
    for neighbour in found.values():
        neighbour["groups"].sort()
        neighbour["pairs"].sort(key=lambda p: (p["column"], p["other_column"]))
    return sorted(found.values(), key=lambda n: (n["name"], n["kind"], n["path"] or ""))


def _precedents(session: Any, node: str, args: dict, *, column: bool = False) -> list[dict]:
    """Pairings this project already committed to that this table has not.

    **Why this shape and not a shelf of candidates.** Three shelves a code
    prefilter could build were tried against the AQN graph and all three fail,
    for reasons worth keeping:

    - *by shared column name*: standing on `AQN_READINGS`, **19 of the other
      24 tables** come back, led by `UPDATED`, a timestamp present in 14 of them.
    - *by role*: worse. `identifier` on both sides returns every table, and
      `stations` alone contributes **69 candidate pairs**.
    - *by name and role together*: the noise clears, and it is **blind to the
      single most important relationship in the project**. Standing on
      `AQN_VENDOR_LABELS.READING_ID` it returns 12 tables and not
      `AQN_READINGS`, because the event catalog calls its key `ID`. That is
      `France` against `FRA` wearing a different costume, and it is why §5.1
      puts pair-picking on the agent: no comparison of metadata gets from
      `READING_ID` to `ID`.

    What *does* get there is a measurement somebody already took.
    `ALTERNATE_NAMES.READING_ID` was measured against `AQN_READINGS.ID`, with a
    reason, at coverage 1.0. Three other tables carry `READING_ID` and were never
    checked — all three unmeasured in the 2026-08-31 indexing run. So this
    replays a judgment the project has already made and recorded, rather than
    forming one. It is not the code prefilter §6.5 rejected: that one *decided*
    which pairs were worth measuring, and this states which pairs exist.

    Only for columns carrying no measurement of their own — a column you have
    already looked at is not a gap. One row per (column, what it is compared
    to, who compared it), flat, so a card can cut it like any list.
    ``column`` narrows it to ``$column``, for the column answer.
    """
    anchor = f"MATCH {node}-[:{HAS_COLUMN}]->(c:{COLUMN}" + (" {name: $column})" if column else ")")
    return _run(session, f"{anchor} {_PRECEDENT_MATCH} {_PRECEDENT_RETURN}", **args)


#: Every measurement of a column with the same name elsewhere, from a column of
#: this table that has none. ``e`` is the namesake, ``f`` what it was compared
#: to, ``et`` and ``ft`` their tables.
_PRECEDENT_MATCH = (
    f"WHERE NOT (c)-[:{OVERLAPS}]-() MATCH (e:{COLUMN})-[r:{OVERLAPS}]-(f:{COLUMN}) "
    f"WHERE r.{PROJECT} = $project AND e.name = c.name AND e.key <> c.key "
    f"MATCH (et)-[:{HAS_COLUMN}]->(e) MATCH (ft)-[:{HAS_COLUMN}]->(f)"
)

#: What the namesake was compared to, and who compared it with what result.
_PRECEDENT_AGAINST = (
    "c.name AS column, labels(ft)[0] AS against_kind, ft.name AS against_table, "
    "f.name AS against_column"
)
_PRECEDENT_BY = (
    "et.name AS by_table, r.n_shared_values AS n_shared_values, r.asked_because AS asked_because"
)
_PRECEDENT_RETURN = (
    f"RETURN {_PRECEDENT_AGAINST}, {_PRECEDENT_BY} "
    "ORDER BY column, against_table, against_column, by_table"
)


# --- between two tables -----------------------------------------------------


def _between_answer(session: Any, found: dict, other: dict, project: str) -> dict:
    """Everything that links two tables, every measured pair in full."""
    if (found["kind"], found["key"]) == (other["kind"], other["key"]):
        raise ValueError(f"{found['name']!r} is the table asked about; name a different neighbour")
    node, far = _match(found), _match(other, "o", "other")
    args = {"key": found["key"], "other": other["key"], "project": project}
    links = _run(
        session,
        f"MATCH {node} MATCH {far} "
        f"RETURN EXISTS {{ MATCH (t)-[:{READS}]->(o) }} AS reads, "
        f"EXISTS {{ MATCH (o)-[:{READS}]->(t) }} AS read_by, "
        f"[(t)-[:{IN_GROUP}]->(g:{GROUP})<-[:{IN_GROUP}]-(o) | g.name] AS groups",
        **args,
    )
    pairs = _run(
        session,
        f"MATCH {node}-[:{HAS_COLUMN}]->(c:{COLUMN})-[r:{OVERLAPS}]-"
        f"(d:{COLUMN})<-[:{HAS_COLUMN}]-{far} RETURN {_PAIR_RETURN}",
        **args,
    )
    link = links[0] if links else {}
    return {
        "table": _header(found),
        "neighbour": _header(other),
        "reads": bool(link.get("reads")),
        "read_by": bool(link.get("read_by")),
        "groups": sorted(link.get("groups") or []),
        "pairs": sorted(
            (pair_from_row(row) for row in pairs), key=lambda p: (p["column"], p["other_column"])
        ),
    }


# --- one column -------------------------------------------------------------


def _column_answer(session: Any, found: dict, column: str, project: str) -> dict:
    """One column's lineage, a few hops each way, and what it was compared with."""
    node = _match(found)
    args = {"key": found["key"], "column": column, "project": project}
    facts = _run(
        session,
        f"MATCH {node}-[:{HAS_COLUMN}]->(c:{COLUMN} {{name: $column}}) "
        "RETURN c.role AS role, c.inferred AS inferred, c.null_rate AS null_rate, "
        f"c.n_distinct AS n_distinct, c.flags AS flags, c.{DERIVATION} AS {DERIVATION}",
        **args,
    )
    if not facts:
        raise ValueError(_unknown_column(session, found, column, project))

    reached = f"MATCH {node}-[:{HAS_COLUMN}]->(c:{COLUMN} {{name: $column}})"
    compared = _run(
        session,
        f"{reached}-[r:{OVERLAPS}]-(d:{COLUMN})<-[:{HAS_COLUMN}]-(o) "
        f"RETURN {_OTHER_RETURN}, {_PAIR_RETURN}",
        **args,
    )
    return {
        "column": {"table": found["name"], "name": column, **dict(facts[0])},
        "upstream": _walk(session, reached, args, upstream=True),
        "downstream": _walk(session, reached, args, upstream=False),
        # Where it bottoms out: the columns nothing else derives from, which on a
        # fully-resolved chain are the files themselves.
        #
        # **`derivation` is what stops that sentence being a lie** (§5.5 of
        # `docs/SQL_LINEAGE.md`). A trail can also end at a model column portia
        # could name nothing underneath — `count(*)`, a literal — and that has
        # the same shape as a file's column: no outgoing edge. It is returned
        # rather than filtered out, because an origins list that silently loses
        # its last rung reads as "this came from nowhere"; carrying the marker
        # says *the trail ends here and portia could not read past it*, which is
        # the honest answer and the same "mark, never delete" rule §4.5 applies
        # to a stale measurement.
        "origins": [
            # Absent rather than null on the ordinary row: this is read by a
            # model, and a key carrying `None` on every origin in the project
            # is tokens spent saying nothing.
            {k: v for k, v in row.items() if k != DERIVATION or v}
            for row in _run(
                session,
                f"{reached}-[:{DERIVES_FROM}*1..{MAX_HOPS}]->(o:{COLUMN})<-[:{HAS_COLUMN}]-(p) "
                f"WHERE NOT (o)-[:{DERIVES_FROM}]->() "
                "RETURN DISTINCT labels(p)[0] AS kind, p.name AS `table`, p.path AS path, "
                f"o.name AS column, o.{DERIVATION} AS {DERIVATION} ORDER BY `table`, column",
                **args,
            )
        ],
        "compared_with": sorted(
            (
                {"table": row["name"] or row["path"], "kind": row["kind"], **pair_from_row(row)}
                for row in compared
            ),
            key=lambda p: (p["table"], p["kind"], p["other_column"]),
        ),
        "precedents": _precedents(session, node, args, column=True),
    }


def _walk(session: Any, reached: str, args: dict, *, upstream: bool) -> list[dict]:
    """The columns ``LINEAGE_HOPS`` steps up or down the lineage, nearest first.

    **A variable-length match, and a safe one**: anchored on one column,
    following one directed edge kind, at most :data:`LINEAGE_HOPS` long. It is
    a chain that widens only where a step reads several columns into one, and
    that widening is the composite the answer exists to show. The match the
    route search is forbidden is the other kind: unanchored, across measured
    edges that go both ways.

    Each column once, at its nearest. ``via`` and ``step`` are the edge that
    reached it: upstream, the step that read this column on the way to the one
    asked about; downstream, the step that built it.
    """
    arrow = (
        f"-[:{DERIVES_FROM}*1..{LINEAGE_HOPS}]->"
        if upstream
        else f"<-[:{DERIVES_FROM}*1..{LINEAGE_HOPS}]-"
    )
    rows = _run(
        session,
        f"{reached} MATCH path = (c){arrow}(o:{COLUMN}) MATCH (p)-[:{HAS_COLUMN}]->(o) "
        f"WITH p, o, length(path) AS hops, last(relationships(path)) AS r {_WALK_RETURN}",
        **args,
    )
    seen: set[tuple] = set()
    nearest = []
    for row in rows:
        place = (row["kind"], row["table"], row["path"], row["column"])
        if place not in seen:
            seen.add(place)
            nearest.append(row)
    return nearest


_WALK_RETURN = (
    "RETURN hops, labels(p)[0] AS kind, p.name AS `table`, p.path AS path, o.name AS column, "
    "r.via AS via, r.step AS step ORDER BY hops, `table`, column, step"
)


# --- routes between two tables ----------------------------------------------


@dataclass(frozen=True, order=True)
class TableId:
    """One table a route passes through: what to call it, its kind, its identity.

    Ordered by name first, which is the only order a route list may take after
    its length (§6.1); kind and key break a tie between a source and a model
    that share a name.
    """

    name: str
    kind: str
    key: str


@dataclass(frozen=True)
class Link:
    """One table-to-table link a route may take: a model reading a table, or a
    measured pair with shared values, facing from ``start`` to ``end``."""

    start: TableId
    end: TableId
    reads: bool = False
    pair: dict | None = field(default=None, compare=False)


@dataclass
class Step:
    """How one table of a route reaches the next: every link between the two.

    ``reads`` when the first reads the second, ``read_by`` when the second
    reads the first, and every measured pair with shared values, facing
    forward, by column name.
    """

    reads: bool = False
    read_by: bool = False
    pairs: list[dict] = field(default_factory=list)


@dataclass(frozen=True)
class Route:
    tables: tuple[TableId, ...]
    steps: tuple[Step, ...]


def routes(
    links: Iterable[Link],
    start: TableId,
    goal: TableId,
    *,
    max_steps: int = ROUTE_HOPS,
    limit: int = ROUTES,
) -> tuple[list[Route], int]:
    """The routes from ``start`` to ``goal``: the first ``limit`` and how many there are.

    **Searched in Python, over links one query fetched** (*2026-10-13*). A route
    is a question about tables, and the graph's links between tables are a few
    hundred at most: a model reading a table, and a measured pair whose columns
    share values. Fetched once, they are searched here, where a cap on steps
    is a loop bound rather than a variable-length match across column nodes
    that the server would have to expand both ways from every measured column.

    A route visits a table at most once and takes at most ``max_steps`` steps.
    Two tables linked several ways are one step carrying all of them, so a
    route is a sequence of tables and not a choice among their column pairs.
    **Fewest steps first, then by the names along the way**: how many steps a
    route takes is a fact about it, and nothing here orders by coverage or
    shared values, which would be deciding which route is better (§6.1).

    A measured zero is not a link. It is a fact about two columns, and it is
    kept (§4.4); it is not a way from one table to the other.
    """
    ways = _ways(links)
    found: list[tuple[TableId, ...]] = []

    def walk(path: tuple[TableId, ...]) -> None:
        for after in sorted(ways.get(path[-1], {})):
            if after in path:
                continue
            if after == goal:
                found.append((*path, after))
            elif len(path) < max_steps:
                walk((*path, after))

    if start != goal:
        walk((start,))
    found.sort(key=lambda path: (len(path), path))
    shown = [
        Route(path, tuple(ways[a][b] for a, b in zip(path, path[1:], strict=False)))
        for path in found[:limit]
    ]
    return shown, len(found)


def _ways(links: Iterable[Link]) -> dict[TableId, dict[TableId, Step]]:
    """Each table's next tables, and the step to each, from both ends of every link."""
    ways: dict[TableId, dict[TableId, Step]] = {}

    def step(a: TableId, b: TableId) -> Step:
        return ways.setdefault(a, {}).setdefault(b, Step())

    for link in links:
        if link.start == link.end:
            continue
        forward, backward = step(link.start, link.end), step(link.end, link.start)
        if link.reads:
            forward.reads = backward.read_by = True
        if link.pair is not None:
            forward.pairs.append(link.pair)
            backward.pairs.append(turned(link.pair))
    for nexts in ways.values():
        for each in nexts.values():
            each.pairs.sort(key=lambda p: (p["column"], p["other_column"]))
    return ways


#: Both ends of a link, as ``a`` and ``b``.
_ENDS = (
    "labels(a)[0] AS a_kind, a.name AS a_name, a.path AS a_path, "
    "labels(b)[0] AS b_kind, b.name AS b_name, b.path AS b_path"
)

#: A measured link's two columns: ``c`` on ``a``, ``d`` on ``b``.
_LINK_COLUMNS = "c.name AS a_column, d.name AS b_column"

#: Whether either end has moved since the link was measured, as `_STALE` asks,
#: here without a `CASE`: the edge is read in its own direction.
_LINK_STALE = "r.left_fingerprint <> a.fingerprint OR r.right_fingerprint <> b.fingerprint AS stale"

#: The numbers on a measured link, facing from ``a`` to ``b`` as the edge does.
_LINK_NUMBERS = (
    "r.n_shared_values AS n_shared_values, r.left_coverage AS coverage, "
    "r.right_coverage AS other_coverage, r.comparable_types AS comparable_types, "
    f"coalesce(r.{ORIGIN}, '{ORIGIN_MEASURED}') AS origin, {_LINK_STALE}"
)

#: The same columns, empty, for a model reading a table.
_NO_NUMBERS = (
    "null AS a_column, null AS b_column, null AS n_shared_values, null AS coverage, "
    "null AS other_coverage, null AS comparable_types, null AS origin, null AS stale"
)

#: Measured pairs whose columns share at least one value, started from the edges
#: and filtered on their project, so nothing walks out from every table.
_MEASURED_LINKS = (
    f"MATCH (c:{COLUMN})-[r:{OVERLAPS}]->(d:{COLUMN}) "
    f"WHERE r.{PROJECT} = $project AND r.n_shared_values > 0 "
    f"MATCH (a)-[:{HAS_COLUMN}]->(c) MATCH (b)-[:{HAS_COLUMN}]->(d) WHERE a <> b "
    f"RETURN 'overlaps' AS link, {_ENDS}, {_LINK_COLUMNS}, {_LINK_NUMBERS}"
)

#: Models reading tables, the same columns with no numbers.
_READ_LINKS = (
    f"MATCH (a:{MODEL})-[:{READS}]->(b) WHERE a.{PROJECT} = $project "
    f"RETURN 'reads' AS link, {_ENDS}, {_NO_NUMBERS}"
)

#: **Every table-to-table link in the project, in one statement**, which is all
#: :func:`routes` searches.
_LINKS = f"{_MEASURED_LINKS} UNION ALL {_READ_LINKS}"

#: The fields of a measured link that a route's step carries, facing forward.
LINK_FIELDS = (
    "n_shared_values",
    "coverage",
    "other_coverage",
    "comparable_types",
    ORIGIN,
    "stale",
)


def link_from_row(row: dict) -> Link:
    """One row of :data:`_LINKS` as a `Link`. Pure, for the tests."""
    a, b = (
        TableId(
            row[f"{end}_name"] or row[f"{end}_path"],
            row[f"{end}_kind"],
            row[f"{end}_path"] if row[f"{end}_kind"] == SOURCE else row[f"{end}_name"],
        )
        for end in ("a", "b")
    )
    if row["link"] == "reads":
        return Link(a, b, reads=True)
    pair = {"column": row["a_column"], "other_column": row["b_column"]}
    return Link(a, b, pair={**pair, **{key: row[key] for key in LINK_FIELDS}})


def _table_id(found: dict) -> TableId:
    return TableId(found["name"] or found["path"], found["kind"], found["key"])


def _routes_answer(session: Any, found: dict, goal: dict, project: str) -> dict:
    """The routes between two tables, fewest steps first, with how many there are."""
    start, end = _table_id(found), _table_id(goal)
    if start == end:
        raise ValueError(f"a route needs two tables, and {found['name']!r} was named twice")
    links = [link_from_row(row) for row in _run(session, _LINKS, project=project)]
    shown, n = routes(links, start, end)
    return {
        "table": _header(found),
        "to": _header(goal),
        "routes": [
            {
                "tables": [t.name for t in route.tables],
                "steps": [
                    {"reads": s.reads, "read_by": s.read_by, "pairs": list(s.pairs)}
                    for s in route.steps
                ],
            }
            for route in shown
        ],
        "n_routes": n,
    }


# --- what measure_overlaps already has --------------------------------------


#: The measurements already stored for a list of column pairs, by position.
#: Both ends are named by key and project, which the store constrains, so each
#: is one index lookup; the edge is matched either way round.
_STORED_MATCH = (
    "UNWIND $pairs AS p "
    f"MATCH (a:{COLUMN} {{{PROJECT}: $project, {KEY_PROPERTY[COLUMN]}: p.left}})"
    f"-[r:{OVERLAPS}]-(b:{COLUMN} {{{PROJECT}: $project, {KEY_PROPERTY[COLUMN]}: p.right}})"
)
_STORED = (
    f"{_STORED_MATCH} RETURN p.i AS i, startNode(r).key = a.key AS from_left, "
    "properties(r) AS measured, a.n_distinct AS left_distinct, b.n_distinct AS right_distinct"
)


def stored_overlaps(session: Any, pairs: list[tuple[str, str]], *, project: str) -> dict[int, dict]:
    """What the graph already holds for each pair of column keys, by its position in ``pairs``.

    `measure_overlaps` asks this before it measures anything (*2026-10-13*): a
    pair measured once is read back rather than measured again, which is what
    "the next session starts with them" always promised. Each found edge comes
    back turned so *left* is the pair's left (`measure.seen_from`), with both
    columns' ``n_distinct`` as the catalog recorded them. Whether it is still
    current is the caller's question, against fingerprints it has and this
    module does not.

    Two pairs measured both ways round are two edges; the later measurement
    is the one returned, as a re-measure in one direction replaces the edge.
    """
    if not pairs:
        return {}
    rows = _run(
        session,
        _STORED,
        pairs=[{"i": i, "left": left, "right": right} for i, (left, right) in enumerate(pairs)],
        project=project,
    )
    found: dict[int, dict] = {}
    for row in rows:
        seen = {
            **measure.seen_from(row["measured"] or {}, from_start=bool(row["from_left"])),
            "left_distinct": row["left_distinct"],
            "right_distinct": row["right_distinct"],
        }
        earlier = found.get(row["i"])
        if earlier is None or str(seen.get("measured_at") or "") > str(
            earlier.get("measured_at") or ""
        ):
            found[row["i"]] = seen
    return found


# --- running them -----------------------------------------------------------


def _run(session: Any, statement: str, **params: Any) -> list[dict]:
    return [record.data() for record in session.run(statement, **params)]


# --- for a terminal ---------------------------------------------------------


def render_text(answer: dict) -> str:
    """Human-readable, for the CLI. The same numbers, never re-ordered, and whole."""
    if "routes" in answer:
        return _render_routes(answer)
    if "neighbour" in answer:
        return _render_between(answer)
    if "upstream" in answer:
        return _render_column(answer)
    return _render_table(answer)


def _title(table: dict) -> str:
    where = table["path"] or table["name"]
    return f"{table['kind']} {table['name']}  ({where})  {table['n_columns']} columns"


def _links(entry: dict) -> str:
    """How a neighbour is linked, in words: reads, read by, the groups shared."""
    words = [
        word for word, on in (("reads it", entry["reads"]), ("read by it", entry["read_by"])) if on
    ]
    words += [f"group {group}" for group in entry["groups"]]
    return ", ".join(words)


def _render_table(answer: dict) -> str:
    lines = [_title(answer["table"])]
    for group in answer["groups"]:
        lines.append(f"  group {group['name']} ({group['n_members']} members): {group['context']}")
    for neighbour in answer["neighbours"]:
        how = _links(neighbour)
        lines.append(f"  {neighbour['kind']} {neighbour['name']}" + (f"  — {how}" if how else ""))
        lines += _render_pairs(neighbour["pairs"], indent="    ")
    for row in answer["precedents"]:
        lines.append(
            f"  {row['column']} has never been measured — elsewhere it is compared to "
            f"{row['against_table']}.{row['against_column']} by {row['by_table']}: "
            f"n_shared_values {row['n_shared_values']}"
        )
    return "\n".join(lines)


def _render_between(answer: dict) -> str:
    lines = [_title(answer["table"]), f"  and {_title(answer['neighbour'])}"]
    how = _links(answer)
    lines.append(f"  linked: {how or '(no reads, no group)'}")
    lines += _render_pairs(answer["pairs"], indent="  ", facts=True)
    return "\n".join(lines)


def _render_pairs(pairs: list[dict], *, indent: str, facts: bool = False) -> list[str]:
    lines = []
    for pair in pairs:
        near = _side(pair["column"], pair["column_facts"]) if facts else pair["column"]
        far = _side(pair["other_column"], pair["other_facts"]) if facts else pair["other_column"]
        lines.append(f"{indent}{near}  vs  {far}")
        lines.append(f"{indent}  {_numbers(pair)}")
        if pair.get("asked_because"):
            lines.append(f'{indent}  "{pair["asked_because"]}"')
    return lines


def _side(name: str, facts: dict) -> str:
    """One end of a pair: its name, then whichever facts it has.

    The facts are what make the number beside them readable, so they are on the
    same line as the column rather than in a block of their own
    (:data:`PAIR_FACTS`).
    """
    shown = [
        f"{key} {facts[key]!r}" if key == "top" else f"{key} {facts[key]}"
        for key in PAIR_FACTS
        if key in facts
    ]
    return name + (f"  [{', '.join(shown)}]" if shown else "")


def _numbers(pair: dict) -> str:
    """The measurement, and `stale` said as a word rather than as a boolean.

    A zero is printed as a zero and never as a verdict — `docs/KNOWLEDGE_GRAPH.md`
    §4.4 — which is why the reason the agent asked is printed under it.
    """
    keys = ("n_shared_values", "coverage", "other_coverage", "comparable_types", ORIGIN)
    parts = [f"{key} {pair[key]}" for key in keys if pair.get(key) is not None]
    if pair.get("stale"):
        parts.append("STALE — a file has changed since this was measured")
    return "  ".join(parts) or "(no numbers on this edge)"


def _render_column(answer: dict) -> str:
    column = answer["column"]
    lines = [f"{column['table']}.{column['name']}"]
    if column.get(DERIVATION):
        # Said on the column itself as well as on an origin row, because asking
        # about this column directly is the commoner way to arrive at it — and
        # an empty lineage with no explanation reads as *nobody built this*.
        lines.append("  (nothing readable underneath it)")
    for heading, key in (("from", "upstream"), ("feeds", "downstream")):
        for row in answer[key]:
            pointer = f"  [{row.get('via')} at {row.get('step')}]" if row.get("via") else ""
            lines.append(f"  {heading} ({row['hops']}): {row['table']}.{row['column']}{pointer}")
    for row in answer["origins"]:
        # Said as a trail that stops rather than as an origin, because that is
        # what it is — see the query.
        unread = "  (nothing readable underneath it)" if row.get(DERIVATION) else ""
        lines.append(f"  origin: {row['table']}.{row['column']}{unread}")
    for row in answer["compared_with"]:
        lines.append(f"  compared with {row['table']}.{row['other_column']}: {_numbers(row)}")
    for row in answer["precedents"]:
        lines.append(
            f"  never measured; elsewhere compared to {row['against_table']}."
            f"{row['against_column']} by {row['by_table']}"
        )
    return "\n".join(lines)


def _render_routes(answer: dict) -> str:
    lines = [
        f"routes from {answer['table']['name']} to {answer['to']['name']}: {answer['n_routes']}"
    ]
    for route in answer["routes"]:
        lines.append(f"  {' → '.join(route['tables'])}")
        for a, b, step in zip(route["tables"], route["tables"][1:], route["steps"], strict=False):
            how = [w for w, on in (("reads", step["reads"]), ("read by", step["read_by"])) if on]
            how += [
                f"{p['column']} = {p['other_column']} ({p['n_shared_values']} shared)"
                for p in step["pairs"]
            ]
            lines.append(f"    {a} → {b}: {'; '.join(how)}")
    return "\n".join(lines)


# --- the whole thing, for a picture -----------------------------------------

#: How many nodes a picture may hold before it stops being one. Not the same
#: number as `MAX_ROWS`, and not for the same reason: that one protects a
#: **context window**, where fifty edges is noise the agent has to read. This
#: protects an **eye**, which copes with far more at once and copes with none of
#: it past a few hundred. Two audiences, two limits, stated separately so
#: neither gets tuned on the other's behalf.
MAX_GRAPH_NODES = 600

#: The fewest and the most columns the Columns view gives each table before the
#: rest are one `+N more` node, beyond the columns that connect to something
#: (:func:`choose_columns`). Between the two, the number is whatever room the cap
#: leaves, shared evenly. Five is enough to see what kind of table it is; past
#: fifteen a table's spray of columns says nothing the next five did not, and
#: the full list is one press away.
MIN_COLUMNS_SHOWN = 5
MAX_COLUMNS_SHOWN = 15

#: The kinds a picture is about, and draws whatever the cap does to columns.
TABLE_KINDS = (SOURCE, MODEL, GROUP)

#: **The picture's own kinds: drawn, never stored.** Nothing writes either to
#: Neo4j, which is why they are here and not in `schema.py`.
#:
#: `MORE` is a table's columns the picture left out, as one node hanging from
#: the table, labelled with how many; pressing it opens the table's whole list
#: in the window. `FINDING` is a line between two tables one finding is about,
#: composed by `ui/engine.knowledge_subgraph` from `findings.py` rather than
#: copied into the graph, so the finding is still one fact in one place
#: (`docs/FINDINGS.md` §3 says why it is not an edge in here).
MORE = "More"
FINDING = "FINDING"


@dataclass(frozen=True)
class ColumnRow:
    """One column, as much as choosing it needs and nothing more.

    ``table`` is the id of the table it hangs from, or ``None`` for a column no
    table lists any more: one a measurement kept alive after its file dropped it
    (`store.py` — a node goes only when nothing points at it). ``position`` is
    ``None`` on a graph built before :data:`schema.POSITION` existed.
    ``connected`` is whether anything besides `HAS_COLUMN` touches it.
    """

    id: str
    table: str | None
    key: str
    position: int | None = None
    connected: bool = False


@dataclass(frozen=True)
class ColumnChoice:
    """Which columns a picture draws, and what its caption needs to say so."""

    #: The columns to draw, a table at a time and in file order within one.
    ids: list[str]
    #: How many columns the project has, and how many of them connect to something.
    total: int
    connected: int
    #: ``N``: how many columns a table shows when it has them — its connected
    #: ones, then others in file order until it shows this many.
    per_table: int
    #: How many columns each table holds, and, for a table that lost some, how many.
    sizes: dict[str, int] = field(default_factory=dict)
    left_out: dict[str, int] = field(default_factory=dict)
    #: How many connected columns made it, and the most any one table could
    #: draw when they did not all fit; ``None`` when they did.
    connected_shown: int = 0
    connected_per_table: int | None = None

    @property
    def connected_cut(self) -> bool:
        return self.connected_shown < self.connected


def choose_columns(
    tables: list[str], columns: list[ColumnRow], *, cap: int = MAX_GRAPH_NODES
) -> ColumnChoice:
    """Which columns the Columns view draws: every connected one, then file order.

    **The rule** *(2026-10-08)*. Every table is drawn. Every column with a
    relationship besides `HAS_COLUMN` is drawn, because a measured overlap or a
    lineage edge is what this view exists to show. Then each table's other
    columns, in file order, until the table shows ``N``: the room the cap
    leaves after the tables and the connected columns, shared evenly across the
    tables and held between :data:`MIN_COLUMNS_SHOWN` and
    :data:`MAX_COLUMNS_SHOWN`. A table with no more than ``N`` columns shows
    them all.

    **What it replaced.** The cap took columns in key order, and a key starts
    with its table's path, so the first table alphabetically took the picture.
    On a project of 18 sources and 6,164 columns one table drew 550, the next
    26, and sixteen drew none, under a caption reading *5582 more not drawn*.

    **When the connected columns alone do not fit**, they are shared the same
    way: every table may draw the same number of them, the most that fits, and
    a table with fewer leaves its spare to the rest. ``connected_per_table``
    says what that number was, so the caption can say it in words.

    **Nothing here ranks** (§6.1). A table's columns are taken in file order,
    which is a fact about the table, and never by a number measured on them. A
    graph built before columns carried a position falls back to the key, which
    within one table is the column's name.

    ``tables`` is every table node the picture draws, in the order it draws
    them. Pure, so it is tested without a database: Cypher fetches, this
    chooses.
    """
    by_table: dict[str | None, list[ColumnRow]] = {}
    for row in columns:
        by_table.setdefault(row.table, []).append(row)
    # Tables in drawing order, then the columns no table lists. A column whose
    # table the cap did not draw has nothing to hang from and is not drawn.
    groups = [(t, sorted(by_table[t], key=_file_order)) for t in [*tables, None] if t in by_table]

    linked = {table: [row for row in rows if row.connected] for table, rows in groups}
    n_connected = sum(len(rows) for rows in linked.values())
    room = cap - len(tables)
    per_table = _clamp((room - n_connected) // len(tables)) if tables else MIN_COLUMNS_SHOWN
    # What each table would draw: every connected column, then its others in
    # file order up to N. **When that does not fit, every table gets the same
    # most** (2026-10-08): the floor of N used to be added on top of a share of
    # the connected columns, so two models whose 1,579 columns were all lineage
    # drew 698 nodes past a cap of 600, and a picture that size never comes to
    # rest. The share is taken over what each table wants, so the picture
    # stays inside the cap and a table with columns still shows some.
    wants = {table: max(len(linked[table]), min(per_table, len(rows))) for table, rows in groups}
    share = fair_share(list(wants.values()), room) if sum(wants.values()) > room else None

    ids: list[str] = []
    sizes: dict[str, int] = {}
    left_out: dict[str, int] = {}
    connected_shown = 0
    for table, rows in groups:
        budget = wants[table] if share is None else min(wants[table], share)
        drawn = linked[table][:budget]
        rest = [row for row in rows if not row.connected][: budget - len(drawn)]
        keep = {row.id for row in (*drawn, *rest)}
        ids += [row.id for row in rows if row.id in keep]
        connected_shown += len(drawn)
        if table is None:
            continue
        sizes[table] = len(rows)
        if len(rows) > len(keep):
            left_out[table] = len(rows) - len(keep)
    return ColumnChoice(
        ids=ids,
        total=len(columns),
        connected=n_connected,
        per_table=per_table,
        sizes=sizes,
        left_out=left_out,
        connected_shown=connected_shown,
        connected_per_table=share if connected_shown < n_connected else None,
    )


def _file_order(row: ColumnRow) -> tuple:
    """A column's place in its table; the key's order on a graph that has none."""
    return (row.position is None, row.position or 0, row.key)


def _clamp(n: int) -> int:
    return max(MIN_COLUMNS_SHOWN, min(MAX_COLUMNS_SHOWN, n))


def fair_share(counts: list[int], room: int) -> int:
    """The most each table may draw so that all of them together fit in ``room``.

    The same number for every table, which is what makes it even; a table with
    fewer than that draws what it has, and what it did not use is what lets the
    number be as high as it is.
    """
    low, high = 0, max(counts, default=0)
    while low < high:
        middle = (low + high + 1) // 2
        if sum(min(count, middle) for count in counts) <= room:
            low = middle
        else:
            high = middle - 1
    return low


#: How a node comes back for a picture: its kind, what to call it, its id, and
#: everything it carries.
_NODE_RETURN = (
    "RETURN labels(n)[0] AS kind, coalesce(n.name, n.key, n.path) AS label, "
    "elementId(n) AS id, properties(n) AS properties"
)

#: What :func:`choose_columns` needs about every column of the project, a table
#: at a time. Cheap on purpose: six thousand columns come back as eighteen rows
#: of short lists, and only the few hundred chosen come back with their
#: properties. Measured on that project: 70 ms collected per table, against 180
#: ms as a row per column and 1.2 s for the first draft's whole picture. A
#: column no table lists is collected under a null table.
_COLUMNS_BY_TABLE = (
    f"MATCH (n:{COLUMN}) WHERE n.{PROJECT} = $project "
    f"OPTIONAL MATCH (t)-[:{HAS_COLUMN}]->(n) "
    "RETURN elementId(t) AS table, collect([elementId(n), n.key, "
    f"n.{POSITION}, EXISTS {{ MATCH (n)-[r]-() WHERE type(r) <> '{HAS_COLUMN}' }}]) AS columns"
)

#: Every edge between two nodes the picture draws. Asked by id rather than by
#: project and filtered afterwards, which sent all six thousand `HAS_COLUMN`
#: edges to draw two hundred and fifty (250 ms against 15).
_EDGES_BETWEEN = (
    "MATCH (a)-[r]->(b) WHERE elementId(a) IN $ids AND elementId(b) IN $ids "
    "RETURN elementId(a) AS `from`, elementId(b) AS `to`, type(r) AS kind, "
    "properties(r) AS properties"
)


def subgraph(session: Any, *, project: str, columns: bool = False) -> dict:
    """The whole graph as nodes and edges — for drawing, not for reading.

    **The only function here that deliberately returns a lot.** Every other one
    is shaped by §9.1's rule that a router which returns fifty things has not
    routed; that rule is about what an *agent* can act on. A picture is read by a
    person, who is much better at a hundred things at once and much worse at a
    paragraph of JSON — so the constraint is different and the function is
    separate rather than the router being loosened.

    ``columns`` off is the legible view: tables, groups, what reads what, and one
    edge per pair of tables that share a measured overlap. On, it adds columns
    and their lineage, chosen by :func:`choose_columns` when they do not all
    fit, with one :data:`MORE` node per table that lost some and a ``columns``
    summary saying what was chosen and how.

    Nodes and edges come back in portia's own vocabulary — no library's field
    names — so swapping what draws them is a change in one JavaScript file.
    """
    # **Tables first, whatever the cap cuts** *(2026-09-23)*. Ordered by kind,
    # `Column` sorted before `Group`, `Model` and `Source`, so a project with
    # more columns than `MAX_GRAPH_NODES` kept every column and dropped every
    # table: the explorer drew six hundred columns attached to nothing (the
    # user's report, on a warehouse of 39 tables). The tables are their own
    # query now, and the cap reaches them only on a project with more tables
    # than a picture can hold.
    where = f"WHERE {_any_label('n', TABLE_KINDS)} AND n.{PROJECT} = $project "
    nodes = _run(
        session,
        f"MATCH (n) {where}{_NODE_RETURN} "
        f"ORDER BY kind, coalesce(n.key, n.name, n.path) LIMIT {MAX_GRAPH_NODES}",
        project=project,
    )
    total = _run(session, f"MATCH (n) {where}RETURN count(n) AS n", project=project)[0]["n"]
    picture: dict = {
        "nodes": nodes,
        "edges": [],
        "truncated": total > len(nodes),
        # How many tables the cap left out, so the caption can count them
        # rather than say *truncated* and leave the reader to guess at what.
        "omitted": total - len(nodes),
        # Both counts, for the Columns view to say when the cap cut tables.
        "tables": {"shown": len(nodes), "total": total},
    }
    if columns:
        _add_columns(session, picture, project)

    drawn = [n["id"] for n in picture["nodes"] if n["kind"] != MORE]
    picture["edges"] += _run(session, _EDGES_BETWEEN, ids=drawn)
    if not columns:
        known = set(drawn)
        # Table-to-table overlap, **derived** rather than stored. §4.1 rejected a
        # summary source-to-source edge in the schema because two things would
        # then state one fact and could disagree — and said to derive it in the
        # query instead. This is that query, and it is the only place the graph
        # is redrawn at a coarser grain than it is written.
        picture["edges"] += [
            e
            for e in _run(
                session,
                f"MATCH (a)-[:{HAS_COLUMN}]->(:{COLUMN})-[r:{OVERLAPS}]->"
                f"(:{COLUMN})<-[:{HAS_COLUMN}]-(b) WHERE a <> b "
                f"AND a.{PROJECT} = $project AND b.{PROJECT} = $project "
                "RETURN elementId(a) AS `from`, elementId(b) AS `to`, "
                f"'{OVERLAPS}' AS kind, {{n_measured_pairs: count(r)}} AS properties",
                project=project,
            )
            if e["from"] in known and e["to"] in known
        ]
    return picture


def _add_columns(session: Any, picture: dict, project: str) -> None:
    """The chosen columns, and a `MORE` node for every table that lost some.

    Two queries, so the properties of six thousand columns are never sent to
    choose a few hundred: the first fetches what choosing needs, and the second
    the chosen columns whole.
    """
    tables = picture["nodes"]
    rows = [
        ColumnRow(column, group["table"], key, position, connected)
        for group in _run(session, _COLUMNS_BY_TABLE, project=project)
        for column, key, position, connected in group["columns"]
    ]
    choice = choose_columns([t["id"] for t in tables], rows, cap=MAX_GRAPH_NODES)
    order = {column: n for n, column in enumerate(choice.ids)}
    drawn = _run(
        session,
        f"MATCH (n:{COLUMN}) WHERE n.{PROJECT} = $project AND elementId(n) IN $ids {_NODE_RETURN}",
        project=project,
        ids=choice.ids,
    )
    picture["nodes"] = [*tables, *sorted(drawn, key=lambda n: order[n["id"]])]

    by_id = {t["id"]: t for t in tables}
    for table, n in choice.left_out.items():
        more = f"{MORE}:{table}"
        picture["nodes"].append(
            {
                "id": more,
                "kind": MORE,
                "label": f"+{n:,} more",
                # What a press needs to open the table in the window, which
                # names a table the way the catalog does, and the two counts
                # its hover card states.
                "properties": {
                    "table": by_id[table]["properties"].get("name"),
                    "table_kind": by_id[table]["kind"],
                    "n_more": n,
                    "n_columns": choice.sizes[table],
                },
            }
        )
        picture["edges"].append({"from": table, "to": more, "kind": HAS_COLUMN, "properties": {}})

    picture["columns"] = {
        "total": choice.total,
        "shown": len(choice.ids),
        "connected": choice.connected,
        "connected_shown": choice.connected_shown,
        "connected_cut": choice.connected_cut,
        "connected_per_table": choice.connected_per_table,
        "per_table": choice.per_table,
    }


def _any_label(variable: str, labels: tuple[str, ...]) -> str:
    """``(n:Source OR n:Model OR n:Group)`` — the kinds a picture is drawing."""
    return "(" + " OR ".join(f"{variable}:{label}" for label in labels) + ")"

"""Reading the graph — a fixed set of questions, not a query language.

`docs/KNOWLEDGE_GRAPH.md` §9.4 phase B: *one tool, fixed queries.* The whole
stack was chosen so the agent **can** write its own Cypher (§3.4), and §7 leaves
open whether it should; that is a separate decision from whether traversal helps
at all, and this is the half that answers the second question first.

**What the graph is for is routing, and routing decides the shape of the answer**
(§9.1). The disclosure ladder is depth on one source — `describe_source` →
`profile_source` → `join_findings`, every rung answering *tell me more about this
table*. The graph answers *which* table, and where a column came from. It sits
**before** L2, not above L4.

That framing is also this module's answer to §7's open question — *what a query
is allowed to return at once* — which §9 sharpens rather than softens: a router
that returns fifty things has not routed. So:

**Ask about a table and you get its neighbourhood, measurements included.** What
it reads, what reads it, which groups it is in, and — grouped under each
neighbour — every measured overlap with the numbers, the reason the agent asked
and whether it still matches the files.

*That last part reverses this module's original rule*, which was **tables back,
never column pairs**, on the grounds that a router returning fifty things has
not routed. The rule was defending against a volume that does not exist.
Measured on the AQN project: `stations` has **four** measured pairs and the entire
23-source project has **eighteen**, at 3,973 characters. What the rule actually
bought was an answer reading `overlaps AQN_TR_LOCMAP (2 measured pair(s))` —
four measurements reduced to the number four, with no way to reach them without
already knowing which columns to name. So `measure_overlaps` could tell the
model it "keeps the answers ... so the next session starts with them", and the
next session could not get them.

What replaces the rule is a cap per neighbour (:data:`MAX_ROWS`), which bites on
a table somebody has measured twenty-five pairs against and leaves the ordinary
case whole.

**The columns come back only where they carry something** (:func:`_connected`).
`stations` has 191 columns and 4 that have an `OVERLAPS` or a `DERIVES_FROM`; the
answer names those 4 and states `n_columns` so nobody reads it as a 4-column
table. The full list is `describe_source`'s, with roles and flags, and printing
it here spent **5,091 of that answer's 7,015 characters** restating another
tool.

**And it says which pairings this project made that this table has not**
(:func:`_precedents`). Not a shelf of candidates: three of those were tried
against the real graph and all three fail, which that function records. What
this replays is a measurement somebody already took, where the same column name
elsewhere has been compared to something and this table's has not.

**Ask about a column and you get that column's lineage.** One hop each way with
the `via`/`step` pointer, plus the files it ultimately comes from. Not every path
— a composite column would multiply them — and never the whole subgraph.

**Nothing here ranks** (§6.1). Every list comes back in name order, which carries
no claim; there is no "best candidate", no sort by coverage, no score. The
numbers are reported and the agent decides which matter.

**Every question is asked of one project** (`schema.PROJECT`). One server holds
them all, so the anchor of each query names its project; the traversals from
there do not have to, because an edge never leaves the project it was written
in. Unscoped, `lookup` could resolve a table this project does not have and the
picture drew every project at once.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from portia.knowledge.measure import MEASURED
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

#: Longest list any single answer returns. Reached only by something unusual —
#: a table read by thirty models — and when it is reached the answer **says so**
#: rather than quietly returning a slice.
MAX_ROWS = 25


def lookup(session: Any, table: str, column: str | None = None, *, project: str) -> dict:
    """One table's neighbourhood, or one column's lineage, within one project.

    ``table`` may be a source's name, a source's path, or a model's name. Give a
    ``column`` to move from *which table* to *where this column came from* —
    which is the same climb the ladder makes, made inside the graph.

    ``project`` is keyword-only and has no default: the server holds every
    project on the machine, and a default here would be a question about all of
    them that reads like a question about this one.
    """
    found = _resolve(session, table, project)
    if column is None:
        return _table_answer(session, found, project)
    return _column_answer(session, found, column, project)


# --- resolving --------------------------------------------------------------


#: A Source or a Model, found by whichever of its two names the caller used. A
#: model has only a name; a source has a name *and* the path that identifies it,
#: and both are things a person or an agent will reasonably type.
_TABLE_BY_NAME = (
    f"MATCH (t) WHERE (t:Source OR t:Model) AND t.{PROJECT} = $project "
    "AND $name IN [t.name, t.path]"
)

#: What every answer needs to know about the table it is about. Named rather than
#: spelled twice, the way `checks/join.py` names its overlap expressions.
_TABLE_FIELDS = "labels(t)[0] AS kind, t.name AS name, t.path AS path, t.summary AS summary"


def _resolve(session: Any, table: str, project: str) -> dict:
    """Which node this name means, or an error naming what there is instead."""
    rows = _run(
        session,
        f"{_TABLE_BY_NAME} RETURN {_TABLE_FIELDS}, [(t)-[:{HAS_COLUMN}]->(c) | c.name] AS columns",
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
            f"MATCH (t) WHERE (t:Source OR t:Model) AND t.{PROJECT} = $project "
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


def _match(found: dict) -> str:
    """``(t:Model {project: $project, name: $key})`` — the node, by its identity.

    Which is the pair, not the key alone: two projects may each hold a
    ``data/orders.csv`` and they are two different tables (`schema.PROJECT`).
    """
    label = found["kind"]
    return f"(t:{label} {{{PROJECT}: $project, {KEY_PROPERTY[label]}: $key}})"


# --- the two answers --------------------------------------------------------


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
#: Inline on each end rather than in a section of their own, because that is
#: their whole job: detached from the pair they describe they are just more
#: numbers. `null_rate` and `flags` are deliberately **not** here — four facts
#: on both ends of every pair rebuilds the wall this answer just tore down, and
#: `profile_source` is one call away.
PAIR_FACTS = ("role", "n_distinct", *SHAPE_FACTS)

#: What travels with the measurement itself. The fingerprints are **not** here:
#: they exist so `stale` can be computed, `stale` is in the same answer, and two
#: opaque `"{size}:{mtime}"` strings per edge are machinery the reader cannot
#: act on. Nor is `project`, which every row of every answer would repeat.
MEASUREMENT_FACTS = (*MEASURED, "asked_because", "measured_at")


def _pick(properties: dict, keys: tuple[str, ...]) -> dict:
    """The named properties that are actually there.

    Absent rather than null: a `top` of ``None`` on every numeric column is
    tokens spent saying *not applicable*, and the shape facts are absent by
    kind on purpose (`schema.SHAPE_FACTS`).
    """
    return {key: properties[key] for key in keys if properties.get(key) is not None}


def _table_answer(session: Any, found: dict, project: str) -> dict:
    """The neighbourhood, with the measurements in it rather than counted."""
    key, node = found["key"], _match(found)
    return {
        "table": {
            "kind": found["kind"],
            "name": found["name"],
            "path": found["path"],
            "summary": found["summary"],
            # Stated, so `connected_columns` reads as the subset it is rather
            # than as the whole table.
            "n_columns": len(found["columns"] or []),
        },
        "connected_columns": _connected(session, node, key, project),
        "reads": _tables(session, f"MATCH {node}-[:{READS}]->(o) {_TABLE_RETURN}", key, project),
        "read_by": _tables(session, f"MATCH {node}<-[:{READS}]-(o) {_TABLE_RETURN}", key, project),
        "groups": _cap(
            _run(
                session,
                f"MATCH {node}-[:{IN_GROUP}]->(g:Group) "
                f"RETURN g.name AS name, g.context AS context, "
                f"[(m)-[:{IN_GROUP}]->(g) | m.name] AS members ORDER BY name",
                key=key,
                project=project,
            )
        ),
        "overlaps": _overlaps(session, node, key, project),
        "precedents": _precedents(session, node, key, project),
    }


def _connected(session: Any, node: str, key: str, project: str) -> list[str]:
    """This table's columns that carry a relationship, and only those.

    A column with no `OVERLAPS` and no `DERIVES_FROM` has nothing to say in a
    graph answer, and `stations` has 187 of them. Naming the other four is the
    difference between an answer about relationships and a copy of
    `describe_source`.
    """
    return [
        row["name"]
        for row in _run(
            session,
            f"MATCH {node}-[:{HAS_COLUMN}]->(c:{COLUMN}) "
            f"WHERE (c)-[:{OVERLAPS}]-() OR (c)-[:{DERIVES_FROM}]-() "
            "RETURN c.name AS name ORDER BY name",
            key=key,
            project=project,
        )
    ]


def _precedents(session: Any, node: str, key: str, project: str) -> list[dict]:
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
    already looked at is not a gap — and grouped by the target, because five
    tables pointing at one catalog key is one fact, not five.
    """
    rows = _run(
        session,
        f"MATCH {node}-[:{HAS_COLUMN}]->(c:{COLUMN}) WHERE NOT (c)-[:{OVERLAPS}]-() "
        f"MATCH (e:{COLUMN})-[r:{OVERLAPS}]-(f:{COLUMN}) "
        f"WHERE r.{PROJECT} = $project AND e.name = c.name AND e.key <> c.key "
        f"MATCH (et)-[:{HAS_COLUMN}]->(e) MATCH (ft)-[:{HAS_COLUMN}]->(f) "
        f"{_PRECEDENT_RETURN} ORDER BY column, `table`, other_column, by",
        key=key,
        project=project,
    )

    grouped: dict[tuple, dict] = {}
    for row in rows:
        head = (row["column"], row["kind"], row["table"], row["other_column"])
        entry = grouped.setdefault(
            head,
            {
                "column": row["column"],
                "against": {
                    "kind": row["kind"],
                    "table": row["table"],
                    "column": row["other_column"],
                    **_pick(row["far"], PAIR_FACTS),
                },
                "measured_elsewhere_by": [],
            },
        )
        entry["measured_elsewhere_by"].append(
            {
                "table": row["by"],
                "n_shared_values": row["n_shared_values"],
                "asked_because": row["asked_because"],
            }
        )
    for entry in grouped.values():
        entry["measured_elsewhere_by"] = _cap(entry["measured_elsewhere_by"])
    return _cap(list(grouped.values()))


_PRECEDENT_RETURN = (
    "RETURN c.name AS column, labels(ft)[0] AS kind, ft.name AS `table`, "
    "f.name AS other_column, properties(f) AS far, et.name AS by, "
    "r.n_shared_values AS n_shared_values, r.asked_because AS asked_because"
)


def _overlaps(session: Any, node: str, key: str, project: str) -> list[dict]:
    """Every measured pair, grouped under the table on the other end.

    **Grouped by neighbour because the neighbour is the relationship.** The
    question the agent is standing in the middle of is *how does this table
    connect to that one*, and the column pairs are the answer to it rather than
    a list beside it.

    Capped per neighbour, not overall: a truncated list has to announce itself
    (:func:`_cap`), and announcing it inside the pair it belongs to says which
    relationship you are only seeing part of.
    """
    rows = _run(
        session,
        f"MATCH {node}-[:{HAS_COLUMN}]->(c:{COLUMN})-[r:{OVERLAPS}]-"
        f"(d:{COLUMN})<-[:{HAS_COLUMN}]-(p) WHERE p <> t "
        "RETURN labels(p)[0] AS kind, p.name AS `table`, p.path AS path, "
        "c.name AS column, properties(c) AS near, "
        "d.name AS other_column, properties(d) AS far, "
        f"properties(r) AS measured, {_STALE} "
        "ORDER BY `table`, column, other_column",
        key=key,
        project=project,
    )

    grouped: dict[tuple, dict] = {}
    for row in rows:
        head = (row["kind"], row["table"], row["path"])
        entry = grouped.setdefault(
            head, {"kind": head[0], "table": head[1], "path": head[2], "pairs": []}
        )
        entry["pairs"].append(
            {
                "column": {"name": row["column"], **_pick(row["near"], PAIR_FACTS)},
                "other_column": {"name": row["other_column"], **_pick(row["far"], PAIR_FACTS)},
                **_pick(row["measured"], MEASUREMENT_FACTS),
                "stale": row["stale"],
            }
        )
    for entry in grouped.values():
        entry["pairs"] = _cap(entry["pairs"])
    return list(grouped.values())


_TABLE_RETURN = "RETURN labels(o)[0] AS kind, o.name AS name, o.path AS path ORDER BY name"


def _column_answer(session: Any, found: dict, column: str, project: str) -> dict:
    """One column's lineage: one hop each way, plus the files underneath it."""
    key, node = found["key"], _match(found)
    facts = _run(
        session,
        f"MATCH {node}-[:{HAS_COLUMN}]->(c:{COLUMN} {{name: $column}}) "
        "RETURN c.role AS role, c.inferred AS inferred, c.null_rate AS null_rate, "
        f"c.n_distinct AS n_distinct, c.flags AS flags, c.{DERIVATION} AS {DERIVATION}",
        key=key,
        column=column,
        project=project,
    )
    if not facts:
        have = ", ".join(sorted(found["columns"] or [])) or "(none in the graph)"
        raise ValueError(f"no column {column!r} on {found['name']!r} — have: {have}")

    reached = f"MATCH {node}-[:{HAS_COLUMN}]->(c:{COLUMN} {{name: $column}})"
    args = {"key": key, "column": column, "project": project}
    return {
        "column": {"table": found["name"], "name": column, **dict(facts[0])},
        # One hop, with the pointer that says which step explains it. The chain
        # beyond this is `origins`; returning every path would multiply on a
        # composite and is what §7 warns a router must not do.
        "derives_from": _cap(
            _run(
                session,
                f"{reached}-[r:{DERIVES_FROM}]->(o:{COLUMN})<-[:{HAS_COLUMN}]-(p) "
                f"{_LINEAGE_RETURN}, r.via AS via, r.step AS step ORDER BY `table`, column",
                **args,
            )
        ),
        "feeds": _cap(
            _run(
                session,
                f"{reached}<-[r:{DERIVES_FROM}]-(o:{COLUMN})<-[:{HAS_COLUMN}]-(p) "
                f"{_LINEAGE_RETURN}, r.via AS via, r.step AS step ORDER BY `table`, column",
                **args,
            )
        ),
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
        "origins": _cap(
            [
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
            ]
        ),
        # The same trim as the table answer: `properties(r)` wholesale shipped
        # the project path and both fingerprints to the model on every edge,
        # and `stale` is the only thing the fingerprints were for.
        "overlaps": _cap(
            [
                _measurement(row)
                for row in _run(
                    session,
                    f"{reached}-[r:{OVERLAPS}]-(o:{COLUMN})<-[:{HAS_COLUMN}]-(p) "
                    f"{_LINEAGE_RETURN}, properties(o) AS far, properties(r) AS measured, "
                    f"startNode(r).key = c.key AS measured_from_here, {_STALE} "
                    "ORDER BY `table`, column",
                    **args,
                )
            ]
        ),
    }


_LINEAGE_RETURN = "RETURN labels(p)[0] AS kind, p.name AS `table`, p.path AS path, o.name AS column"

#: The two raw property bags a measurement row carries, unpacked by
#: :func:`_measurement` and never returned as they came.
_RAW = ("far", "measured")


def _measurement(row: dict) -> dict:
    """One measured overlap as the agent should read it.

    The far column's own facts sit beside the numbers for the reason
    :data:`PAIR_FACTS` exists, and `properties(r)` is trimmed for the reason
    :data:`MEASUREMENT_FACTS` gives.
    """
    return {
        **{key: value for key, value in row.items() if key not in _RAW},
        **_pick(row["far"], PAIR_FACTS),
        **_pick(row["measured"], MEASUREMENT_FACTS),
    }


#: Whether a measurement is still backed by the data it was taken from (§4.5),
#: worked out at **read** time by comparing the fingerprints the edge recorded
#: against the ones its two tables carry now. Nothing has to re-walk the graph
#: when a file changes, and nothing has to be invalidated: the edge is **marked,
#: never deleted**, because a deleted edge is indistinguishable from one nobody
#: ever measured, which is the ambiguity §4.4 exists to remove.
#:
#: The `CASE` is because the edge's two fingerprints are *left* and *right* while
#: the query's two tables are *this one* and *the other one*, and which is which
#: depends on the direction the measurement was taken in. `null` when a
#: fingerprint is missing, which honestly reads as "cannot tell" rather than
#: "fine".
def _moved(near: str, far: str) -> str:
    """Either end no longer matching what it was measured against.

    ``t`` is the table asked about and ``p`` the one on the other end of the
    edge; ``near``/``far`` say which of the edge's two recorded fingerprints
    belongs to which of them.
    """
    return f"r.{near}_fingerprint <> t.fingerprint OR r.{far}_fingerprint <> p.fingerprint"


_STALE = (
    "CASE WHEN startNode(r).key = c.key "
    f"THEN {_moved('left', 'right')} "
    f"ELSE {_moved('right', 'left')} END AS stale"
)


# --- running them -----------------------------------------------------------


def _run(session: Any, statement: str, **params: Any) -> list[dict]:
    return [record.data() for record in session.run(statement, **params)]


def _tables(session: Any, statement: str, key: str, project: str) -> list[dict]:
    return _cap(_run(session, statement, key=key, project=project))


def _cap(rows: list) -> list:
    """Cut a list to :data:`MAX_ROWS`, saying so in the list itself when it bites.

    A truncated answer that doesn't announce it is worse than a long one: the
    agent reads a short list as *complete* and stops looking.
    """
    if len(rows) <= MAX_ROWS:
        return rows
    return [*rows[:MAX_ROWS], {"truncated": f"{len(rows) - MAX_ROWS} more not shown"}]


def render_text(answer: dict) -> str:
    """Human-readable, for the CLI. The same numbers, never re-ordered."""
    if "column" in answer:
        return _render_column(answer)
    return _render_table(answer)


def _render_table(answer: dict) -> str:
    table = answer["table"]
    where = table["path"] or table["name"]
    lines = [f"{table['kind']} {table['name']}  ({where})  {table['n_columns']} columns"]
    connected = answer["connected_columns"]
    lines.append(f"  connected columns: {', '.join(connected) or '(none)'}")
    for heading, key in (("reads", "reads"), ("read by", "read_by")):
        for row in answer[key]:
            lines.append(f"  {heading}: {row.get('name') or row.get('truncated')}")
    for group in answer["groups"]:
        lines.append(f"  group {group['name']}: {', '.join(group.get('members') or [])}")
    for row in answer["precedents"]:
        against = row["against"]
        lines.append(
            f"  {row['column']} has never been measured — elsewhere it is compared to "
            f"{against['table']}.{against['column']}"
        )
        for by in row["measured_elsewhere_by"]:
            if "truncated" in by:
                lines.append(f"    ({by['truncated']})")
                continue
            lines.append(f"    by {by['table']}: n_shared_values {by['n_shared_values']}")
    for neighbour in answer["overlaps"]:
        lines.append(f"  measured against {neighbour['table']}")
        for pair in neighbour["pairs"]:
            if "truncated" in pair:
                lines.append(f"    ({pair['truncated']})")
                continue
            lines.append(f"    {_side(pair['column'])}")
            lines.append(f"      vs {_side(pair['other_column'])}")
            lines.append(f"      {_numbers(pair)}")
            if pair.get("asked_because"):
                lines.append(f'      "{pair["asked_because"]}"')
    return "\n".join(lines)


def _side(column: dict) -> str:
    """One end of a pair: its name, then whichever facts it has.

    The facts are what make the number above them readable, so they are on the
    same line as the column rather than in a block of their own
    (:data:`PAIR_FACTS`).
    """
    facts = [
        f"{key} {column[key]!r}" if key == "top" else f"{key} {column[key]}"
        for key in PAIR_FACTS
        if key in column
    ]
    return f"{column['name']}" + (f"  [{', '.join(facts)}]" if facts else "")


def _numbers(pair: dict) -> str:
    """The measurement, and `stale` said as a word rather than as a boolean.

    A zero is printed as a zero and never as a verdict — `docs/KNOWLEDGE_GRAPH.md`
    §4.4 — which is why the reason the agent asked is printed under it.
    """
    parts = [f"{key} {pair[key]}" for key in MEASURED if key in pair]
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
    for heading, key in (("derives from", "derives_from"), ("feeds", "feeds")):
        for row in answer[key]:
            pointer = f"  [{row.get('via')} at {row.get('step')}]" if row.get("via") else ""
            lines.append(f"  {heading}: {row['table']}.{row['column']}{pointer}")
    for row in answer["origins"]:
        # Said as a trail that stops rather than as an origin, because that is
        # what it is — see the query.
        unread = "  (nothing readable underneath it)" if row.get(DERIVATION) else ""
        lines.append(f"  origin: {row['table']}.{row['column']}{unread}")
    for row in answer["overlaps"]:
        lines.append(f"  overlaps {row['table']}.{row['column']}: {row['measured']}")
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
    share = _fair_share(list(wants.values()), room) if sum(wants.values()) > room else None

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


def _fair_share(counts: list[int], room: int) -> int:
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

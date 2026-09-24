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


def subgraph(session: Any, *, project: str, columns: bool = False) -> dict:
    """The whole graph as nodes and edges — for drawing, not for reading.

    **The only function here that deliberately returns a lot.** Every other one
    is shaped by §9.1's rule that a router which returns fifty things has not
    routed; that rule is about what an *agent* can act on. A picture is read by a
    person, who is much better at a hundred things at once and much worse at a
    paragraph of JSON — so the constraint is different and the function is
    separate rather than the router being loosened.

    ``columns`` off is the legible view: tables, groups, what reads what, and one
    edge per pair of tables that share a measured overlap. On, it is the full
    thing including every column and every lineage edge, which at a real project
    is a hairball and is sometimes exactly what you want to see.

    Nodes and edges come back in portia's own vocabulary — no library's field
    names — so swapping what draws them is a change in one JavaScript file.
    """
    labels = (SOURCE, MODEL, GROUP, COLUMN) if columns else (SOURCE, MODEL, GROUP)
    scoped = f"n.{PROJECT} = $project"
    # **Tables before columns, whatever the cap cuts** *(2026-09-23)*. The
    # nodes were ordered by kind, and `Column` sorts before `Group`, `Model`
    # and `Source`, so on a project with more columns than `MAX_GRAPH_NODES`
    # the cap kept every column and dropped every table. The `HAS_COLUMN`
    # edges were then filtered out below as pointing at unknown nodes, and the
    # explorer drew six hundred columns attached to nothing, joined only by the
    # overlaps measured between them (the user's report, on a warehouse of 39
    # tables). The kinds a picture is *about* come first, and the columns are
    # ordered by their key, which starts with the table's, so what the cap
    # takes is whole tables at the end of the list rather than a scatter of
    # columns from every table.
    where = f"WHERE {_any_label('n', labels)} AND {scoped} "
    nodes = _run(
        session,
        f"MATCH (n) {where}"
        "RETURN labels(n)[0] AS kind, coalesce(n.name, n.key, n.path) AS label, "
        "elementId(n) AS id, properties(n) AS properties "
        f"ORDER BY n:{COLUMN}, kind, coalesce(n.key, n.name, n.path) LIMIT {MAX_GRAPH_NODES}",
        project=project,
    )
    total = _run(session, f"MATCH (n) {where}RETURN count(n) AS n", project=project)[0]["n"]
    known = {n["id"] for n in nodes}
    edges = [
        e
        for e in _run(
            session,
            f"MATCH (a)-[r]->(b) WHERE {_any_label('a', labels)} AND {_any_label('b', labels)} "
            f"AND a.{PROJECT} = $project AND b.{PROJECT} = $project "
            "RETURN elementId(a) AS `from`, elementId(b) AS `to`, type(r) AS kind, "
            "properties(r) AS properties",
            project=project,
        )
        if e["from"] in known and e["to"] in known
    ]
    if not columns:
        # Table-to-table overlap, **derived** rather than stored. §4.1 rejected a
        # summary source-to-source edge in the schema because two things would
        # then state one fact and could disagree — and said to derive it in the
        # query instead. This is that query, and it is the only place the graph
        # is redrawn at a coarser grain than it is written.
        edges += [
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
    return {
        "nodes": nodes,
        "edges": edges,
        "truncated": total > len(nodes),
        # How many the cap left out, so the caption can count them rather than
        # say *truncated* and leave the reader to guess at what.
        "omitted": total - len(nodes),
    }


def _any_label(variable: str, labels: tuple[str, ...]) -> str:
    """``(n:Source OR n:Model OR n:Group)`` — the kinds a picture is drawing."""
    return "(" + " OR ".join(f"{variable}:{label}" for label in labels) + ")"

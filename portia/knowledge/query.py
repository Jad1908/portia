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

**Ask about a table and you get tables back.** Not column pairs. What it reads,
what reads it, which groups it is in, which other tables share a measured
overlap with it and how many column pairs that covers. At 23 sources there are at
most 22 neighbours, so the answer is small by construction rather than by a cap
that hides things.

**Ask about a column and you get that column's lineage.** One hop each way with
the `via`/`step` pointer, plus the files it ultimately comes from. Not every path
— a composite column would multiply them — and never the whole subgraph.

**Nothing here ranks** (§6.1). Every list comes back in name order, which carries
no claim; there is no "best candidate", no sort by coverage, no score. The
numbers are reported and the agent decides which matter.
"""

from __future__ import annotations

from typing import Any

from portia.knowledge.schema import (
    COLUMN,
    DERIVES_FROM,
    HAS_COLUMN,
    IN_GROUP,
    KEY_PROPERTY,
    OVERLAPS,
    READS,
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


def lookup(session: Any, table: str, column: str | None = None) -> dict:
    """One table's neighbourhood, or one column's lineage.

    ``table`` may be a source's name, a source's path, or a model's name. Give a
    ``column`` to move from *which table* to *where this column came from* —
    which is the same climb the ladder makes, made inside the graph.
    """
    found = _resolve(session, table)
    if column is None:
        return _table_answer(session, found)
    return _column_answer(session, found, column)


# --- resolving --------------------------------------------------------------


#: A Source or a Model, found by whichever of its two names the caller used. A
#: model has only a name; a source has a name *and* the path that identifies it,
#: and both are things a person or an agent will reasonably type.
_TABLE_BY_NAME = "MATCH (t) WHERE (t:Source OR t:Model) AND $name IN [t.name, t.path]"

#: What every answer needs to know about the table it is about. Named rather than
#: spelled twice, the way `checks/join.py` names its overlap expressions.
_TABLE_FIELDS = "labels(t)[0] AS kind, t.name AS name, t.path AS path, t.summary AS summary"


def _resolve(session: Any, table: str) -> dict:
    """Which node this name means, or an error naming what there is instead."""
    rows = _run(
        session,
        f"{_TABLE_BY_NAME} RETURN {_TABLE_FIELDS}, [(t)-[:{HAS_COLUMN}]->(c) | c.name] AS columns",
        name=table,
    )
    if not rows:
        raise ValueError(_unknown_table(session, table))
    found = dict(rows[0])
    found["key"] = found["path"] if found["kind"] == SOURCE else found["name"]
    return found


def _unknown_table(session: Any, table: str) -> str:
    """The message a miss produces — and it has to distinguish two very different
    misses, because "no such table" and "the graph was never built" ask for
    completely different next moves."""
    known = [
        r["name"]
        for r in _run(
            session,
            "MATCH (t) WHERE t:Source OR t:Model RETURN t.name AS name "
            f"ORDER BY name LIMIT {MAX_ROWS}",
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
    """``(t:Model {name: $key})`` — the node, by the property that identifies it."""
    label = found["kind"]
    return f"(t:{label} {{{KEY_PROPERTY[label]}: $key}})"


# --- the two answers --------------------------------------------------------


def _table_answer(session: Any, found: dict) -> dict:
    """The neighbourhood: **tables**, never column pairs."""
    key, node = found["key"], _match(found)
    return {
        "table": {
            "kind": found["kind"],
            "name": found["name"],
            "path": found["path"],
            "summary": found["summary"],
        },
        "columns": sorted(found["columns"] or []),
        "reads": _tables(session, f"MATCH {node}-[:{READS}]->(o) {_TABLE_RETURN}", key),
        "read_by": _tables(session, f"MATCH {node}<-[:{READS}]-(o) {_TABLE_RETURN}", key),
        "groups": _cap(
            _run(
                session,
                f"MATCH {node}-[:{IN_GROUP}]->(g:Group) "
                f"RETURN g.name AS name, g.context AS context, "
                f"[(m)-[:{IN_GROUP}]->(g) | m.name] AS members ORDER BY name",
                key=key,
            )
        ),
        "overlaps": _cap(
            _run(
                session,
                f"MATCH {node}-[:{HAS_COLUMN}]->(:{COLUMN})-[r:{OVERLAPS}]-"
                f"(:{COLUMN})<-[:{HAS_COLUMN}]-(o) WHERE o <> t "
                "RETURN labels(o)[0] AS kind, o.name AS name, o.path AS path, "
                "count(r) AS n_measured_pairs ORDER BY name",
                key=key,
            )
        ),
    }


_TABLE_RETURN = "RETURN labels(o)[0] AS kind, o.name AS name, o.path AS path ORDER BY name"


def _column_answer(session: Any, found: dict, column: str) -> dict:
    """One column's lineage: one hop each way, plus the files underneath it."""
    key, node = found["key"], _match(found)
    facts = _run(
        session,
        f"MATCH {node}-[:{HAS_COLUMN}]->(c:{COLUMN} {{name: $column}}) "
        "RETURN c.role AS role, c.inferred AS inferred, c.null_rate AS null_rate, "
        "c.n_distinct AS n_distinct, c.flags AS flags",
        key=key,
        column=column,
    )
    if not facts:
        have = ", ".join(sorted(found["columns"] or [])) or "(none in the graph)"
        raise ValueError(f"no column {column!r} on {found['name']!r} — have: {have}")

    reached = f"MATCH {node}-[:{HAS_COLUMN}]->(c:{COLUMN} {{name: $column}})"
    args = {"key": key, "column": column}
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
        "origins": _cap(
            _run(
                session,
                f"{reached}-[:{DERIVES_FROM}*1..{MAX_HOPS}]->(o:{COLUMN})<-[:{HAS_COLUMN}]-(p) "
                f"WHERE NOT (o)-[:{DERIVES_FROM}]->() "
                "RETURN DISTINCT labels(p)[0] AS kind, p.name AS `table`, p.path AS path, "
                "o.name AS column ORDER BY `table`, column",
                **args,
            )
        ),
        "overlaps": _cap(
            _run(
                session,
                f"{reached}-[r:{OVERLAPS}]-(o:{COLUMN})<-[:{HAS_COLUMN}]-(p) "
                f"{_LINEAGE_RETURN}, properties(r) AS measured, "
                f"startNode(r).key = c.key AS measured_from_here, {_STALE} "
                "ORDER BY `table`, column",
                **args,
            )
        ),
    }


_LINEAGE_RETURN = "RETURN labels(p)[0] AS kind, p.name AS `table`, p.path AS path, o.name AS column"


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


def _tables(session: Any, statement: str, key: str) -> list[dict]:
    return _cap(_run(session, statement, key=key))


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
    lines = [f"{table['kind']} {table['name']}  ({where})"]
    lines.append(f"  columns: {', '.join(answer['columns']) or '(none)'}")
    for heading, key in (("reads", "reads"), ("read by", "read_by")):
        for row in answer[key]:
            lines.append(f"  {heading}: {row.get('name') or row.get('truncated')}")
    for group in answer["groups"]:
        lines.append(f"  group {group['name']}: {', '.join(group.get('members') or [])}")
    for row in answer["overlaps"]:
        lines.append(f"  overlaps {row['name']}  ({row['n_measured_pairs']} measured pair(s))")
    return "\n".join(lines)


def _render_column(answer: dict) -> str:
    column = answer["column"]
    lines = [f"{column['table']}.{column['name']}"]
    for heading, key in (("derives from", "derives_from"), ("feeds", "feeds")):
        for row in answer[key]:
            pointer = f"  [{row.get('via')} at {row.get('step')}]" if row.get("via") else ""
            lines.append(f"  {heading}: {row['table']}.{row['column']}{pointer}")
    for row in answer["origins"]:
        lines.append(f"  origin: {row['table']}.{row['column']}")
    for row in answer["overlaps"]:
        lines.append(f"  overlaps {row['table']}.{row['column']}: {row['measured']}")
    return "\n".join(lines)

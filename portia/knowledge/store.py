"""Neo4j — the only module in this package that knows what Cypher is.

`docs/KNOWLEDGE_GRAPH.md` §3 settles the store and §3.3 accepts what it costs:
portia stops being one command, and the app has to behave sensibly when the
database is down. §6.6 is the consequence — this is an **optional dependency**,
behind the ``graph`` extra, and nothing outside this module imports ``neo4j``.

**The Cypher is built by pure functions and executed by three lines.**
:func:`node_writes`, :func:`edge_writes` and :func:`prune_writes` return
``(statement, parameters)`` pairs and touch nothing; :func:`write` runs them. So
what gets sent is testable with no server running, which is the same reason
`schema.py` exists apart from this file.

**A rebuild owns the structural half and nothing else** (§5.2 — Neo4j is a
store, not a cache). Every node and structural edge is stamped with the build
that wrote it; edges of a *structural* kind carrying an older stamp are deleted,
because they are a restatement of files and the files no longer say them.
`OVERLAPS` is never touched: it cost a query, it is not re-derivable from the
repo, and deleting it would return the graph to the ambiguity §4.4 exists to
remove — an absent edge must mean *nobody measured*, and nothing else.

The same rule decides node properties: they are **replaced**, not merged, so a
`role` someone cleared in the catalog does not survive on the node. Anything
measured or asserted belongs on an edge, which is where §4.4 puts it anyway.
A node left with no relationships at all *and* an old stamp is cruft and goes;
one that still carries a measurement stays, whatever the files now say.

**Everything here is scoped to one project** (`schema.PROJECT`). One server
holds every project on the machine, so a statement that names no project reaches
all of them: every `MERGE` matches on ``(project, key)``, and the prune — which
deletes on *absence* rather than presence — filters on it before anything else.
Two failures came from not doing this, and both are worth naming because neither
looked like a graph bug: indexing one project silently deleted every structural
edge of every other, and two projects that each had a ``data/customers.csv``
merged into one node.

**And a build that wrote nothing prunes nothing.** An empty graph is not the
statement *this project has no tables*; it is what `build_graph` returns when
the catalog is missing — a deleted `.portia`, a wrong root, a half-written
project — and pruning against it deletes the whole project including
measurements no file can restate (§5.2).
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Any
from uuid import uuid4

from portia.knowledge.schema import (
    KEY_PROPERTY,
    LABELS,
    PROJECT,
    STRUCTURAL,
    Edge,
    Graph,
    Node,
)

#: Where a local Neo4j listens. Overridden by ``NEO4J_URI``.
DEFAULT_URI = "bolt://localhost:7687"
DEFAULT_USER = "neo4j"

#: The property every node and structural edge carries, naming the build that
#: wrote it. Leading underscore so it reads as portia's bookkeeping rather than
#: as something about the data.
BUILD_PROPERTY = "_build"


def settings() -> dict[str, str]:
    """Connection details, from the environment. One place, so the CLI and any
    later surface cannot disagree about which database they are talking to."""
    return {
        "uri": os.environ.get("NEO4J_URI", DEFAULT_URI),
        "user": os.environ.get("NEO4J_USER", DEFAULT_USER),
        "password": os.environ.get("NEO4J_PASSWORD", ""),
        "database": os.environ.get("NEO4J_DATABASE", "neo4j"),
    }


class GraphUnavailable(RuntimeError):
    """The graph could not be reached — no driver, or nothing listening.

    Its own type because **it is not a failure of the question being asked**, and
    the caller's right response is different: a surface should say the database
    is down and carry on with what it can answer without it (§3.5). A
    `ValueError` here would be indistinguishable from "no such table".
    """


def connect(**overrides: str) -> Any:
    """A Neo4j driver, verified. Raises :class:`GraphUnavailable` if it can't.

    The import is here rather than at module scope so importing this package
    costs nothing without the extra installed — §6.6's "if a stopped container
    breaks the whole product, a design decision has leaked into a requirement".
    """
    try:
        from neo4j import GraphDatabase
    except ImportError:
        raise GraphUnavailable(_NO_DRIVER) from None

    config = settings() | overrides
    try:
        driver = GraphDatabase.driver(config["uri"], auth=(config["user"], config["password"]))
        driver.verify_connectivity()
    except Exception as exc:
        raise GraphUnavailable(f"no Neo4j at {config['uri']}: {exc}") from None
    return driver


@contextmanager
def session(**overrides: str) -> Iterator[Any]:
    """A session on the configured database, closed on the way out.

    One place that knows a read needs a driver *and* a session and that both get
    closed, so every surface that asks the graph a question opens it the same
    way. `docs/PIPELINE.md`'s argument about `plan()` being called by both edges,
    applied to a connection.
    """
    driver = connect(**overrides)
    config = settings() | overrides
    try:
        with driver.session(database=config["database"]) as live:
            yield live
    finally:
        driver.close()


_NO_DRIVER = (
    "the knowledge graph needs the neo4j driver: pip install 'portia[graph]' "
    "(see docs/KNOWLEDGE_GRAPH.md §3)"
)


# --- the statements, as data ------------------------------------------------


def new_build_id() -> str:
    """One build's stamp: readable, and unique even twice in a second."""
    return f"{datetime.now().strftime('%Y%m%dT%H%M%S')}-{uuid4().hex[:8]}"


def _legacy_constraint(label: str) -> str:
    """Drop the constraint each label carried before projects existed: unique on
    the key alone. It has to go before the composite one is created, because the
    two cannot coexist — the old one forbids two projects from each having a
    ``data/orders.csv``, which is the thing that was wrong."""
    return f"DROP CONSTRAINT {label.lower()}_key IF EXISTS"


def constraint_statements() -> list[str]:
    """One uniqueness constraint per label, on what actually identifies a node.

    This is `KEY_PROPERTY` **plus the project** enforced by the database rather
    than by convention: §4.8's failure mode is two nodes for one thing, and its
    mirror image — one node for two things, which is what a project-relative
    path was doing across projects — is even harder to notice, because nothing
    errors and the graph just quietly says something false.

    Composite property uniqueness is Community Edition in Neo4j 5, so this needs
    no licence the project doesn't already have.
    """
    return [
        statement
        for label in LABELS
        for statement in (
            _legacy_constraint(label),
            f"CREATE CONSTRAINT {label.lower()}_project_key IF NOT EXISTS "
            f"FOR (n:{label}) REQUIRE (n.{PROJECT}, n.{KEY_PROPERTY[label]}) IS UNIQUE",
        )
    ]


def node_writes(graph: Graph, build: str) -> list[tuple[str, dict]]:
    """One statement per label, each merging that label's nodes in one pass.

    Merged on ``(project, key)``, and `PROJECT` is in the written properties too
    — `SET n = row.properties` *replaces* the node, so a property left only in
    the `MERGE` pattern would be wiped on the write that just matched it.
    """
    by_label: dict[str, list[Node]] = {label: [] for label in LABELS}
    for node in graph.nodes.values():
        by_label[node.label].append(node)

    writes = []
    for label, nodes in by_label.items():
        if not nodes:
            continue
        key = KEY_PROPERTY[label]
        rows = [
            {
                "key": n.key,
                "properties": {
                    **n.properties,
                    key: n.key,
                    PROJECT: graph.project,
                    BUILD_PROPERTY: build,
                },
            }
            for n in nodes
        ]
        writes.append(
            (
                f"UNWIND $rows AS row MERGE (n:{label} {{{PROJECT}: $project, {key}: row.key}}) "
                "SET n = row.properties",
                {"rows": rows, "project": graph.project},
            )
        )
    return writes


def edge_writes(graph: Graph, build: str) -> list[tuple[str, dict]]:
    """One statement per (kind, start label, end label), for the same reason.

    Grouped by both ends' labels because a `MATCH` has to name a label to use
    the index — an unlabelled match over a graph of a few thousand nodes is fine
    and would still be the wrong habit to write down.
    """
    groups: dict[tuple[str, str, str], list[Edge]] = {}
    for edge in graph.edges.values():
        if edge.kind not in STRUCTURAL:
            continue  # measured and asserted edges are not a rebuild's to write
        groups.setdefault((edge.kind, edge.start.label, edge.end.label), []).append(edge)

    writes = []
    for (kind, start_label, end_label), edges in groups.items():
        start_key, end_key = KEY_PROPERTY[start_label], KEY_PROPERTY[end_label]
        rows = [
            {
                "start": e.start.key,
                "end": e.end.key,
                "properties": {**e.properties, PROJECT: graph.project, BUILD_PROPERTY: build},
            }
            for e in edges
        ]
        writes.append(
            (
                "UNWIND $rows AS row "
                f"MATCH (a:{start_label} {{{PROJECT}: $project, {start_key}: row.start}}) "
                f"MATCH (b:{end_label} {{{PROJECT}: $project, {end_key}: row.end}}) "
                f"MERGE (a)-[r:{kind}]->(b) SET r = row.properties",
                {"rows": rows, "project": graph.project},
            )
        )
    return writes


def measured_writes(edges: list[Edge], project: str) -> list[tuple[str, dict]]:
    """The measured half, written **without a build stamp** — and that is the point.

    A stamp is what makes something a rebuild's to delete. These cost a query,
    no file restates them, and §5.2 is explicit that Neo4j is a store rather than
    a cache: losing a measurement costs time, and losing it *silently* costs the
    distinction between "we looked and found nothing" and "nobody looked" (§4.4).

    Directed left-to-right, because the edge holds two directional coverages and
    which is which is carried by nothing else (§4.3).

    It carries `PROJECT` like everything else — a measurement is *between two
    columns of one project*, and an unscoped one would be reachable from a
    traversal that started somewhere else.
    """
    groups: dict[tuple[str, str, str], list[Edge]] = {}
    for edge in edges:
        if edge.kind in STRUCTURAL:
            raise ValueError(f"{edge.kind} is structural — a rebuild owns it, not a measurement")
        groups.setdefault((edge.kind, edge.start.label, edge.end.label), []).append(edge)

    writes = []
    for (kind, start_label, end_label), grouped in groups.items():
        start_key, end_key = KEY_PROPERTY[start_label], KEY_PROPERTY[end_label]
        rows = [
            {
                "start": e.start.key,
                "end": e.end.key,
                "properties": {**e.properties, PROJECT: project},
            }
            for e in grouped
        ]
        writes.append(
            (
                "UNWIND $rows AS row "
                f"MATCH (a:{start_label} {{{PROJECT}: $project, {start_key}: row.start}}) "
                f"MATCH (b:{end_label} {{{PROJECT}: $project, {end_key}: row.end}}) "
                f"MERGE (a)-[r:{kind}]->(b) SET r = row.properties "
                # A row whose `MATCH` finds nothing never reaches here, which is
                # exactly why the caller counts this instead of trusting how
                # many edges it handed over.
                "RETURN count(r) AS written",
                {"rows": rows, "project": project},
            )
        )
    return writes


def write_measured(edges: list[Edge], session: Any, project: str) -> int:
    """Store measured edges. Returns how many were **actually** written.

    Not how many it was handed, which is what it used to return and what made
    this the one write in portia that could lose something already paid for.
    The statement `MATCH`es both ends, so a measurement whose column is not in
    the graph — every column of a model portia could not read (`docs/
    SQL_LINEAGE.md` §1.5) — is skipped by Neo4j without an error. Counting the
    rows the write claims is how the caller finds out.
    """
    written = 0
    for statement, params in measured_writes(edges, project):
        result = session.run(statement, **params)
        written += sum(record["written"] for record in result)
    return written


def prune_writes(build: str, project: str) -> list[tuple[str, dict]]:
    """What this build did not restate, and is therefore no longer true.

    Structural edges only. A node goes only if nothing points at it at all —
    which is what keeps a column that carries a measurement alive even after the
    file it described has changed underneath it (§4.5: mark stale, never delete).

    **Scoped to one project, and this is the statement that most needed it.**
    Every other one here deletes what it names; these two delete everything they
    *don't*, so the blast radius is whatever the `MATCH` reaches — which, with no
    project named, was every project on the server. Indexing one of them left the
    rest as bare columns wired together by their old measurements: `HAS_COLUMN`
    gone with the rest of the structural half, the columns themselves surviving
    the second statement precisely because `OVERLAPS` is never pruned.
    """
    params = {"build": build, "project": project, "structural": list(STRUCTURAL)}
    return [
        (
            f"MATCH ()-[r]->() WHERE type(r) IN $structural AND r.{PROJECT} = $project "
            f"AND r.{BUILD_PROPERTY} <> $build DELETE r",
            params,
        ),
        (
            f"MATCH (n) WHERE n.{PROJECT} = $project AND n.{BUILD_PROPERTY} IS NOT NULL "
            f"AND n.{BUILD_PROPERTY} <> $build AND NOT (n)--() DELETE n",
            params,
        ),
    ]


def forget_unscoped_writes() -> list[tuple[str, dict]]:
    """Delete everything written before nodes carried a project — a **migration**.

    Its own function, and a CLI flag rather than something a build does, because
    it is the one delete in this module that cannot be justified from the files:
    a node with no `PROJECT` predates the property, and nothing on it says which
    project it belonged to. That is exactly why it has to go by hand — guessing
    from a relative path would re-create the ambiguity the property removes, and
    doing it automatically inside `write` would put an irreversible delete on a
    path that runs on every index.
    """
    return [
        (f"MATCH (n) WHERE n.{PROJECT} IS NULL DETACH DELETE n", {}),
        (f"MATCH ()-[r]->() WHERE r.{PROJECT} IS NULL DELETE r", {}),
    ]


# --- running them -----------------------------------------------------------


def write(graph: Graph, session: Any, *, build: str | None = None) -> str:
    """Put a graph into Neo4j, and return the build id that stamped it.

    Nodes, then edges, then the prune — in that order, because an edge needs
    both its ends and the prune has to see what this build wrote.

    **A graph with no nodes writes nothing and prunes nothing.** `build_graph`
    returns one when the catalog is unreadable — a deleted `.portia`, a wrong
    ``--root``, a project half-way through being set up — and none of those are
    the assertion *this project has no tables*. Pruning against it deleted the
    project's whole structural half and left its measurements as orphan columns,
    which is a lot of damage for a mistyped path; and it is unrecoverable in the
    one way that matters, because a measurement is the only thing in here no
    file can restate (§5.2). Emptying a project on purpose is what
    `forget_unscoped_writes`' sibling gesture — deleting it in the browser — is
    for; a build is not asked to do it.
    """
    build = build or new_build_id()
    if not graph.project:
        raise ValueError("a graph must name its project before it can be stored")
    for statement in constraint_statements():
        session.run(statement)
    if not graph.nodes:
        return build
    for statement, params in [
        *node_writes(graph, build),
        *edge_writes(graph, build),
        *prune_writes(build, graph.project),
    ]:
        session.run(statement, **params)
    return build


def forget_unscoped(session: Any) -> int:
    """Run the migration, and say how much it removed."""
    removed = 0
    for statement, params in forget_unscoped_writes():
        summary = session.run(statement, **params).consume()
        removed += summary.counters.nodes_deleted + summary.counters.relationships_deleted
    return removed

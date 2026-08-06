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
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Any
from uuid import uuid4

from portia.knowledge.schema import KEY_PROPERTY, LABELS, STRUCTURAL, Edge, Graph, Node

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


def constraint_statements() -> list[str]:
    """One uniqueness constraint per label, on the property that identifies it.

    This is `KEY_PROPERTY` enforced by the database rather than by convention:
    §4.8's failure mode is two nodes for one thing, and it is much easier to
    prevent than to notice.
    """
    return [
        f"CREATE CONSTRAINT {label.lower()}_key IF NOT EXISTS "
        f"FOR (n:{label}) REQUIRE n.{KEY_PROPERTY[label]} IS UNIQUE"
        for label in LABELS
    ]


def node_writes(graph: Graph, build: str) -> list[tuple[str, dict]]:
    """One statement per label, each merging that label's nodes in one pass."""
    by_label: dict[str, list[Node]] = {label: [] for label in LABELS}
    for node in graph.nodes.values():
        by_label[node.label].append(node)

    writes = []
    for label, nodes in by_label.items():
        if not nodes:
            continue
        key = KEY_PROPERTY[label]
        rows = [
            {"key": n.key, "properties": {**n.properties, key: n.key, BUILD_PROPERTY: build}}
            for n in nodes
        ]
        writes.append(
            (
                f"UNWIND $rows AS row MERGE (n:{label} {{{key}: row.key}}) SET n = row.properties",
                {"rows": rows},
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
                "properties": {**e.properties, BUILD_PROPERTY: build},
            }
            for e in edges
        ]
        writes.append(
            (
                "UNWIND $rows AS row "
                f"MATCH (a:{start_label} {{{start_key}: row.start}}) "
                f"MATCH (b:{end_label} {{{end_key}: row.end}}) "
                f"MERGE (a)-[r:{kind}]->(b) SET r = row.properties",
                {"rows": rows},
            )
        )
    return writes


def prune_writes(build: str) -> list[tuple[str, dict]]:
    """What this build did not restate, and is therefore no longer true.

    Structural edges only. A node goes only if nothing points at it at all —
    which is what keeps a column that carries a measurement alive even after the
    file it described has changed underneath it (§4.5: mark stale, never delete).
    """
    params = {"build": build, "structural": list(STRUCTURAL)}
    return [
        (
            f"MATCH ()-[r]->() WHERE type(r) IN $structural AND r.{BUILD_PROPERTY} <> $build "
            "DELETE r",
            params,
        ),
        (
            f"MATCH (n) WHERE n.{BUILD_PROPERTY} IS NOT NULL AND n.{BUILD_PROPERTY} <> $build "
            "AND NOT (n)--() DELETE n",
            params,
        ),
    ]


# --- running them -----------------------------------------------------------


def write(graph: Graph, session: Any, *, build: str | None = None) -> str:
    """Put a graph into Neo4j, and return the build id that stamped it.

    Nodes, then edges, then the prune — in that order, because an edge needs
    both its ends and the prune has to see what this build wrote.
    """
    build = build or new_build_id()
    for statement in constraint_statements():
        session.run(statement)
    for statement, params in [
        *node_writes(graph, build),
        *edge_writes(graph, build),
        *prune_writes(build),
    ]:
        session.run(statement, **params)
    return build

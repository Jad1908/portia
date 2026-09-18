"""The graph's vocabulary, and a graph as plain python.

`docs/KNOWLEDGE_GRAPH.md` §4 is the argument; this is the vocabulary table from
it, in code. Nothing here talks to a database — a `Graph` is a set of nodes and
edges that can be built, asserted on and printed with no server running, and
`store.py` is the only module that knows what Cypher is.

**Two rules from the design are enforced by this file's shape.**

*The vocabulary is closed* (§4.2). Node labels and edge kinds are constants, not
strings passed in by callers, because an edge vocabulary that grows per run
cannot be queried — you cannot write a Cypher pattern without knowing which
relationship names exist. New kinds arrive by editing this file, in a diff.

*Every node is identified by one property, and two sessions must agree on it*
(§4.8). A graph written incrementally merges on that key, so an identifier two
writers disagree about produces two nodes for one thing. :data:`KEY_PROPERTY`
says which property that is per label, and it is what `store.py` constrains and
merges on.

**A Column belongs to exactly one table and is never shared** (§4). Two tables
that both have ``customer_id`` produce two Column nodes; whether they are the
same thing is the question `OVERLAPS` exists to answer, so merging them here
would assume the answer. :func:`column_key` is what keeps them apart.

**And a table belongs to exactly one project** (:data:`PROJECT`). One Neo4j
server holds every project on the machine — Community Edition allows one user
database per server, so "a database per project" means *a container* per
project — which made a node key that is a project-relative path ambiguous
across projects, and a prune that named no project destructive across them.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# --- node labels ------------------------------------------------------------

#: A data file that arrived from outside portia. Identified by its path relative
#: to the project root — the same currency `catalog.source_ref` records, so a
#: cloned project produces the same node.
SOURCE = "Source"

#: A table portia built. One spec, one table, one node; identified by the spec's
#: name, which `spec.discover_specs` already requires to be unique.
MODEL = "Model"

#: One named column belonging to exactly one Source or one Model.
COLUMN = "Column"

#: A set of sources someone said belong together. A restatement of
#: `catalog.set_group`, which is what keeps its names convergent (§4.8).
GROUP = "Group"

#: Every label this schema knows. **Entity is deliberately absent** — §4.8 defers
#: it as the one node kind with nothing underneath it and the one whose names
#: diverge by default, because there is no list to read before coining one.
LABELS = (SOURCE, MODEL, COLUMN, GROUP)

#: The property each label is identified by. `store.py` puts a uniqueness
#: constraint on each of these and merges on nothing else.
KEY_PROPERTY = {SOURCE: "path", MODEL: "name", COLUMN: "key", GROUP: "name"}

# --- edge kinds -------------------------------------------------------------

#: Source/Model → Column. This table has this column.
HAS_COLUMN = "HAS_COLUMN"

#: Source → Group. Restates an assertion the catalog already holds.
IN_GROUP = "IN_GROUP"

#: Model → Source/Model. This model's spec declares that table as an input. The
#: table-level half of lineage, which `pipeline` already resolves; here so it can
#: be traversed with everything else (§4.2).
READS = "READS"

#: Column → Column, pointing **backwards**: output column → the input column(s)
#: its values came from. Count a column's outgoing edges and the shape is free —
#: one is a rename or a transform, several is a composite. Carries ``via`` (which
#: op, from a closed set) and ``step`` (a ``<spec>#<step id>`` pointer), never a
#: vocabulary of transform names and never a copy of the expression (§4.2).
DERIVES_FROM = "DERIVES_FROM"

#: Column — Column. Two columns were compared; here is how far their values
#: coincide. **Nothing in this package writes one yet** — it is the measured half
#: (§5.1, phase C). It is named here because the writer has to know which kinds
#: are structural in order to leave everything else alone: a rebuild off the
#: files must never delete a measurement (§5.2 — Neo4j is a store, not a cache).
OVERLAPS = "OVERLAPS"

#: The kinds that are a restatement of files, and therefore the kinds a rebuild
#: owns. Everything outside this set was measured or asserted and is not
#: re-derivable from the repo, so `store.write` will not touch it.
STRUCTURAL = (HAS_COLUMN, IN_GROUP, READS, DERIVES_FROM)

#: Which op produced a derived column. A **closed** set, because it is a constant
#: in `ops/` — this vocabulary physically cannot grow without a code change,
#: which is the whole reason `DERIVES_FROM` carries it instead of a label per
#: shape of derivation (§4.2).
VIA_OPS = ("join", "normalize", "sql")


#: On a Model column: portia could not name a single column its values came
#: from. `docs/SQL_LINEAGE.md` §5.5 — and it exists because *no outgoing
#: `DERIVES_FROM`* already means something else. `query.origins` reads a column
#: with no outgoing edges as the place the data came from, which is true of a
#: file's column and a lie about `count(*) AS n`. Without this property the two
#: are the same shape, and the graph would report a computed column as a source.
#:
#: **Two states, never a vocabulary.** It is present or it is absent, which is
#: what keeps it clear of §4.8's fragmentation risk: a set of names for *why* a
#: derivation is unknown would diverge per session exactly as Entity's would.
#: The reason lives in `BuildResult.unresolved`, which is a build report rather
#: than a store.
DERIVATION = "derivation"
DERIVATION_UNKNOWN = "unknown"


#: The counts every Column node carries, whatever kind of column it is. Facts
#: the catalog already holds, restated so a graph answer can report a
#: measurement without a second call to fetch what the two ends look like.
COUNT_FACTS = ("inferred", "null_rate", "n_distinct", "flags")

#: The **value-shaped** facts, and they are split by kind because the two halves
#: describe different things and neither substitutes for the other: a numeric
#: column's shape is its range, a categorical column's is its commonest value.
#: `checks.profiling._column` already makes exactly this split when it measures,
#: and this restates it rather than inventing a second rule.
#:
#: **Why a value is on the node at all.** Pair-picking is the agent's job
#: (§5.1), and until now it was done from column names and roles alone: the
#: agent had to decide whether `COUNTRY` in one table was the same thing as
#: `GEOGRAPHIC_COUNTRY` in another with nothing in front of it but the two
#: names. That is the `France` against `FRA` problem at the moment of *choosing*
#: rather than at the moment of reading the result, and one example value from
#: each side settles it instantly. §4.4 keeps samples off the `OVERLAPS`
#: **edge**, which is a different thing: a measurement should not carry a copy
#: of the data it measured. One modal value on the column it describes is the
#: same restatement of the catalog as `null_rate` beside it.
NUMERIC_SHAPE = ("min", "max")
CATEGORICAL_SHAPE = ("top", "top_freq")
SHAPE_FACTS = (*NUMERIC_SHAPE, *CATEGORICAL_SHAPE)


def shape_facts(facts: dict) -> dict:
    """Whichever of :data:`SHAPE_FACTS` this column actually has.

    Absent rather than null, on both halves. A `top` of ``None`` on every
    numeric column in the project is tokens spent saying *not applicable*, and
    Neo4j stores a null property by not storing it anyway — so the honest
    encoding and the cheap one are the same one here.
    """
    return {key: facts[key] for key in SHAPE_FACTS if facts.get(key) is not None}


#: What a table looked like when something was measured against it (§4.5). Every
#: node carries one and every measured edge records the two it was taken
#: against, so "is this number still backed by the data it came from" is a
#: string comparison rather than new machinery.
#:
#: The values come from fingerprints portia already computes for other reasons —
#: `catalog.STALENESS_FACTS` for a file, `pipeline.fingerprint` for a spec — which
#: is the whole point of §4.5: nothing new has to be invented or kept in step.
FINGERPRINT = "fingerprint"


def source_fingerprint(size: Any, mtime: Any) -> str | None:
    """A file's fingerprint, as one comparable string.

    One property rather than two, because the comparison happens **in Cypher**
    against what an edge recorded, and comparing one string is something the
    query language does without arithmetic.
    """
    if size is None or mtime is None:
        return None
    return f"{size}:{mtime}"


#: Which project a node or an edge belongs to. **Every node and every edge
#: carries one**, and every query is scoped by it.
#:
#: One server holds every project, and that is not a preference: Neo4j Community
#: allows exactly one user database per server (`docker-compose.yml` says so
#: about the test server for the same reason), so a database per project would
#: be a *container* per project — a JVM, a volume and a port pair each, to hold
#: a few thousand nodes. The standard shape for many small tenants in one graph
#: is a tenant property, and this is it.
#:
#: It is not part of :data:`KEY_PROPERTY` but it **is** part of a node's
#: identity: `store.py` merges on ``(project, key)`` and constrains the pair.
#: Keeping it out of the key is what leaves the key readable — a Column still
#: reads as ``source:data/orders.csv::customer_id`` in the browser — and what
#: keeps `column_key` a pure function of the table it belongs to.
PROJECT = "project"


def project_id(root: str | Path) -> str:
    """Which project a graph is about: its root directory, resolved.

    An absolute path rather than a name, because there is no project name in the
    catalog to read and a bare directory name collides on the first two
    ``sandbox/demo`` projects — which is the failure this whole property exists
    to stop. Resolved, so ``.`` and ``sandbox/../sandbox/aqn`` are one project
    rather than three.

    Unlike a node's key this is deliberately *machine-local*: the graph is a
    local store that is never committed, so a project moved on disk is a
    different project until it is rebuilt, and rebuilding is free.
    """
    return Path(root).resolve().as_posix()


def column_key(table_label: str, table_key: str, column: str) -> str:
    """The identity of one column: which table it belongs to, and its name.

    The table's label is part of it because a Source is keyed by a path and a
    Model by a bare name, and nothing structurally stops a project from having
    both ``orders`` the spec and ``orders`` the file. Readable on purpose — this
    string is what someone reads in the Neo4j browser.
    """
    return f"{table_label.lower()}:{table_key}::{column}"


# --- the graph --------------------------------------------------------------


@dataclass(frozen=True)
class Ref:
    """A node, as something an edge can point at: its label and its key."""

    label: str
    key: str

    def column(self, name: str) -> Ref:
        """The Ref of one of this table's columns."""
        return Ref(COLUMN, column_key(self.label, self.key, name))


@dataclass
class Node:
    """One node: what kind it is, what identifies it, and what it carries.

    ``properties`` never includes the identifying property — :attr:`key` is it,
    and `store.py` writes it under :data:`KEY_PROPERTY`. Two statements of one
    identifier is how two writers end up disagreeing about it.
    """

    label: str
    key: str
    properties: dict[str, Any] = field(default_factory=dict)

    @property
    def ref(self) -> Ref:
        return Ref(self.label, self.key)


@dataclass(frozen=True)
class Edge:
    """One relationship, and the properties it carries.

    ``properties`` is excluded from equality: an edge *is* its kind and its two
    ends, so :meth:`Graph.add_edge` writing the same pair twice is one edge with
    the later properties, not two records of one fact. §4.3 makes the same
    argument about `OVERLAPS` holding two numbers rather than being two edges.
    """

    kind: str
    start: Ref
    end: Ref
    properties: dict[str, Any] = field(default_factory=dict, compare=False)


@dataclass
class Graph:
    """Nodes and edges, deduplicated by identity. Deterministic in, structured out.

    Insertion-ordered, so a build over the same project produces the same graph
    in the same order — which is what makes a rendered graph diffable and a test
    able to assert on more than a count.

    ``project`` says which project every node and edge in here belongs to
    (:data:`PROJECT`). It sits on the graph rather than on each node because a
    `Graph` *is* one project's — nothing builds a graph spanning two — and
    stamping it once is what stops half a build carrying it.
    """

    nodes: dict[tuple[str, str], Node] = field(default_factory=dict)
    edges: dict[tuple[str, Ref, Ref], Edge] = field(default_factory=dict)
    project: str = ""

    def add_node(self, label: str, key: str, **properties: Any) -> Ref:
        """Merge a node in. Properties given here win; ones already there stay.

        Merging rather than replacing, because a Source is described by the
        catalog and referenced by a spec, and neither knows everything about it.
        """
        if label not in LABELS:
            raise ValueError(f"unknown node label {label!r} — have: {', '.join(LABELS)}")
        node = self.nodes.setdefault((label, key), Node(label, key))
        node.properties.update({k: v for k, v in properties.items() if v is not None})
        return node.ref

    def add_edge(self, kind: str, start: Ref, end: Ref, **properties: Any) -> Edge:
        """Merge an edge in. Both ends must already be nodes."""
        if kind not in STRUCTURAL and kind != OVERLAPS:
            raise ValueError(f"unknown edge kind {kind!r}")
        for ref in (start, end):
            if (ref.label, ref.key) not in self.nodes:
                raise ValueError(f"edge {kind} refers to a node that isn't here: {ref}")
        edge = Edge(kind, start, end, {k: v for k, v in properties.items() if v is not None})
        return self.edges.setdefault((kind, start, end), edge)

    def node(self, label: str, key: str) -> Node | None:
        return self.nodes.get((label, key))

    def edges_of(self, kind: str) -> list[Edge]:
        return [e for e in self.edges.values() if e.kind == kind]

    def counts(self) -> dict[str, dict[str, int]]:
        """How many of each kind — the compact answer to "what got built"."""
        return {
            "nodes": {
                label: sum(1 for n in self.nodes.values() if n.label == label) for label in LABELS
            },
            "edges": dict(Counter(e.kind for e in self.edges.values())),
        }


def render_text(graph: Graph) -> str:
    """Human-readable counts, for the CLI. Kinds, in the order the schema declares
    them — never sorted by size, which would read as a ranking (`DESIGN.md`)."""
    counts = graph.counts()
    lines = ["nodes:"]
    lines += [f"  {label:<8} {counts['nodes'][label]}" for label in LABELS]
    lines.append("edges:")
    lines += [f"  {kind:<13} {counts['edges'].get(kind, 0)}" for kind in STRUCTURAL]
    return "\n".join(lines)

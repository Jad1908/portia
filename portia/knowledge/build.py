"""The project, read into a graph — the free half (`docs/KNOWLEDGE_GRAPH.md` §5).

Everything here is **translation, not inference**: the catalog already holds
sources, columns, column facts and groups; the specs already hold which table
reads which, and — via `ops.join.join_columns` — which column came from which.
Reading that into nodes and edges makes no decisions, costs nothing, and asks
nobody anything. It is the same status as `HAS_COLUMN` itself (§4.2).

**Nothing runs.** No connection, no data file is opened, no spec is executed. A
model's output columns are derived from its inputs' columns and the naming rule
the op already owns, which is why `join_columns` was pulled out of the SELECT
builder rather than reimplemented here. That matters beyond speed: the graph can
be built for a project whose data has moved, and it says the same thing it said
when the data was there.

**Column lineage, and how one edge carries a multi-step chain.** A Column belongs
to exactly one Source or Model (§4.1), so steps inside a spec are not nodes and a
model column points straight at the source column it came from. The chain in
between is compressed into `via` + `step`, and the rule is:

    a step that **changes the values** (a `normalize` transform) outranks one
    that only **renames** (a join's `_x`/`_y` suffix), which outranks one that
    merely **carries** the column. Ties go to the later step, and a column no
    step ever claimed keeps the step that first read it in.

So a column lowercased at ``#clean`` and carried through ``#j`` points at
``#clean``. The graph holds a pointer and never a copy of the expression: the
spec is the one place that says what a step does, and following the pointer is
what tells you which columns that step transformed (§4.2).

A shared join key is the one output column that reads *both* inputs. It gets two
edges, and each carries the step that explains its own side — because a
`coalesce` picks a value rather than changing one. That the column is a
composite needs no property: it is free from the edge count (§4.2).

**Where it stops: the `sql` hatch.** A `sql` step declares *table* names as its
inputs and nothing about columns, so its output columns cannot be named from the
spec at all. Rather than guess them, a model downstream of one is reported as
unresolved: it gets its Model node and its `READS` edges, which are true, and no
Column nodes. §7 keeps "a parser (`sqlglot`) for real column-level lineage" open
and says to start coarse and decide on evidence; this is the coarse start, and
:attr:`BuildResult.unresolved` is the evidence.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path

from portia import catalog, pipeline, spec
from portia.checks.join import resolve_keys
from portia.knowledge.schema import (
    COLUMN,
    DERIVES_FROM,
    FINGERPRINT,
    GROUP,
    HAS_COLUMN,
    IN_GROUP,
    MODEL,
    READS,
    SOURCE,
    Graph,
    Ref,
    column_key,
    source_fingerprint,
)
from portia.ops.join import join_columns

#: How much a step explains about where a column's values came from. The
#: pointer on a `DERIVES_FROM` edge is the highest-ranked step that touched it,
#: ties going to the later one — see the module docstring.
CARRIED, RENAMED, CHANGED = 0, 1, 2


@dataclass(frozen=True)
class _Trace:
    """One column's origin, as far back as the spec can see it.

    ``table``/``column`` name a Source or Model column — the far end of the
    `DERIVES_FROM` edge. ``via``/``step`` are the pointer that edge carries.
    """

    table: Ref
    column: str
    via: str | None = None
    step: str | None = None
    rank: int = -1

    def touched(self, via: str, step: str, rank: int) -> _Trace:
        """This trace after a step touched it — see the ranking rule."""
        if rank < self.rank:
            return self
        return replace(self, via=via, step=step, rank=rank)


@dataclass
class _Relation:
    """A named table inside a spec, mid-walk: its columns and where they came from.

    ``columns`` is ``None`` when the spec cannot say what they are — a `sql`
    step, or a source nobody has indexed. ``reason`` is why, carried forward so
    the report names the actual cause rather than the step that inherited it.
    """

    columns: list[str] | None
    traces: dict[str, list[_Trace]] = field(default_factory=dict)
    reason: str = ""


@dataclass
class BuildResult:
    """The graph, and what the project could not be read into it.

    ``unresolved`` is one line per model whose output columns the spec cannot
    name. It is deliberately part of the result rather than a warning printed
    somewhere: it is the measure of how much the `sql` hatch costs column
    lineage, which is the evidence §7 asks for before buying a SQL parser.
    """

    graph: Graph
    unresolved: dict[str, str] = field(default_factory=dict)


def build_graph(root: str | Path = ".", *, portia_dir: str | Path | None = None) -> BuildResult:
    """Read a whole project — catalog and specs — into one `Graph`."""
    root = Path(root)
    portia_dir = Path(portia_dir) if portia_dir else root / catalog.DEFAULT_DIR

    graph = Graph()
    result = BuildResult(graph)

    source_columns = _add_catalog(graph, catalog.load_catalog(portia_dir))
    _add_models(graph, result, root, source_columns)
    return result


# --- the catalog half -------------------------------------------------------


def _add_catalog(graph: Graph, data: dict) -> dict[str, list[str]]:
    """Sources, their columns and their groups. Returns each source's columns by path.

    The Source node carries the file fingerprint the catalog already records
    (`catalog.STALENESS_FACTS`), because §4.5 needs a measurement to be able to
    say what it was taken against, and this is where that number comes from.
    """
    paths: dict[str, str] = {}
    columns: dict[str, list[str]] = {}

    for name, entry in (data.get("sources") or {}).items():
        path = entry.get("source")
        if not path:
            continue
        indexed = entry.get("indexed") or {}
        facts = {fact: indexed.get(fact) for fact in catalog.STALENESS_FACTS}
        table = graph.add_node(
            SOURCE,
            path,
            name=name,
            # **Only a real read.** `catalog._auto_summary` drafts a restatement
            # of the profile so the YAML is never empty, and carrying that into
            # the graph would put a placeholder in the prose slot where it is
            # indistinguishable from an interpretation — the same mistake the
            # source inspector's `not-read` state exists to undo. Absent means
            # nobody has read this source, which is a fact worth being able to
            # see. The facts it restated are on the columns, measured.
            summary=entry.get("summary") if catalog.is_interpreted(entry) else None,
            candidate_keys=entry.get("candidate_keys") or [],
            # The same two numbers, also as one comparable string: a measured
            # edge records what it was taken against, and comparing that in
            # Cypher wants one property rather than two (§4.5).
            **{FINGERPRINT: source_fingerprint(**facts)},
            **facts,
        )
        paths[name] = path
        columns[path] = [c["name"] for c in entry.get("columns") or []]
        for col in entry.get("columns") or []:
            graph.add_edge(HAS_COLUMN, table, _add_column(graph, table, col["name"], col))

    for group in data.get("groups") or []:
        node = graph.add_node(GROUP, group["name"], context=group.get("context"))
        for member in group.get("sources") or []:
            if member in paths:
                graph.add_edge(IN_GROUP, Ref(SOURCE, paths[member]), node)

    return columns


def _add_column(graph: Graph, table: Ref, name: str, facts: dict | None = None) -> Ref:
    """One column node. Facts when the catalog has them; a name when it doesn't.

    A model's columns arrive with no facts at all, and that is honest — nothing
    has profiled the table portia would build. `role` is judgment the catalog
    holds and the graph restates; it is never invented here.
    """
    facts = facts or {}
    return graph.add_node(
        COLUMN,
        column_key(table.label, table.key, name),
        name=name,
        table=table.key,
        role=facts.get("role"),
        inferred=facts.get("inferred"),
        null_rate=facts.get("null_rate"),
        n_distinct=facts.get("n_distinct"),
        flags=facts.get("flags"),
    )


# --- the spec half ----------------------------------------------------------


def _add_models(graph: Graph, result: BuildResult, root: Path, source_columns) -> None:
    """Every spec as a Model, in dependency order so an upstream's columns exist."""
    models = spec.discover_specs(root)
    if not models:
        return
    docs = {name: spec.load_spec(root / path) for name, path in models.items()}

    outputs: dict[str, _Relation] = {}
    for name in spec.run_order(models, base_dir=root):
        doc = docs[name]
        # `spec` and `fingerprint` are **pointers** into the pipeline — where to
        # read what this table does, and what it looked like when something was
        # measured against it (§4.5). The pipeline's own vocabulary is not here:
        # `layer` (staging/intermediate/mart) groups and orders the project
        # canvas and says nothing about what a table *is to* another table,
        # which is the only question this graph answers.
        node = graph.add_node(
            MODEL, name, spec=str(models[name]), fingerprint=pipeline.fingerprint(doc)
        )
        outputs[name] = _add_model(graph, result, node, doc, models, outputs, source_columns)


def _add_model(
    graph: Graph,
    result: BuildResult,
    node: Ref,
    doc: dict,
    models: dict[str, Path],
    outputs: dict[str, _Relation],
    source_columns: dict[str, list[str]],
) -> _Relation:
    """One spec: its `READS` edges, its output columns, and their lineage."""
    declared = doc.get("sources") or {}
    relations: dict[str, _Relation] = {
        name: _source_relation(graph, path, source_columns) for name, path in declared.items()
    }

    steps = doc.get("steps") or []
    for step in steps:
        for ref in spec.step_inputs(step):
            # Sources first, exactly as `run_spec` resolves them: a name is a
            # declared source, then another model, then an earlier step.
            if ref in declared:
                graph.add_edge(READS, node, Ref(SOURCE, declared[ref]))
            elif ref in models:
                upstream = Ref(MODEL, ref)
                graph.add_edge(READS, node, upstream)
                relations.setdefault(ref, _model_relation(upstream, outputs.get(ref)))
        relations[step["id"]] = _step_relation(step, relations, node.key)

    if not steps:
        return _Relation(None, reason=_NO_STEPS)

    # One spec produces one table, and it is the last step's — the same rule
    # `spec.model_table` applies when another spec reads this one.
    produced = relations[steps[-1]["id"]]
    if produced.columns is None:
        result.unresolved[node.key] = produced.reason
        return produced

    for name in produced.columns:
        column = _add_column(graph, node, name)
        graph.add_edge(HAS_COLUMN, node, column)
        for trace in produced.traces.get(name, []):
            origin = trace.table.column(trace.column)
            if graph.node(COLUMN, origin.key) is None:
                continue  # its table's columns are not in the graph; say nothing
            graph.add_edge(DERIVES_FROM, column, origin, via=trace.via, step=trace.step)
    return produced


_UNBUILT = "reads a model whose own columns could not be resolved"
_NO_STEPS = "the spec has no steps, so it produces no table"


def _model_relation(model: Ref, produced: _Relation | None) -> _Relation:
    """Another spec's table, as this spec sees it: its columns, and **its** nodes.

    The lineage restarts here rather than continuing back to that model's own
    sources. A model's columns are nodes (§4.1), so the chain is a hop per model
    — B's column derives from A's column, which derives from the source column.
    Reaching past A would skip the node that makes the middle of the path
    visible, which is the half of §2 that lineage is for.
    """
    if produced is None:
        return _Relation(None, reason=_UNBUILT)
    if produced.columns is None:
        return produced
    return _Relation(
        list(produced.columns),
        {name: [_Trace(model, name)] for name in produced.columns},
    )


def _source_relation(graph: Graph, path: str, source_columns: dict[str, list[str]]) -> _Relation:
    """A declared source as a relation. Unknown columns if nobody indexed it."""
    table = graph.add_node(SOURCE, path)
    names = source_columns.get(path)
    if names is None:
        return _Relation(None, reason=f"source {path!r} is not indexed, so its columns are unknown")
    return _Relation(names, {name: [_Trace(table, name)] for name in names})


def _step_relation(step: dict, relations: dict[str, _Relation], model: str) -> _Relation:
    """What one step produces: its output columns, and each one's traces."""
    op, pointer = step.get("op"), f"{model}#{step.get('id')}"
    if op == "normalize":
        return _normalize_relation(step, relations, pointer)
    if op == "join":
        return _join_relation(step, relations, pointer)
    if op == "sql":
        return _Relation(None, reason=f"step {step.get('id')!r} is a sql step — see §7")
    return _Relation(None, reason=f"unknown op {op!r} in step {step.get('id')!r}")


def _normalize_relation(step: dict, relations: dict[str, _Relation], pointer: str) -> _Relation:
    """`normalize` keeps every column and its name; only the transformed ones change."""
    source = relations.get(step["input"]) or _Relation(None, reason=_MISSING)
    if source.columns is None:
        return source

    transformed = {t["column"] for t in step.get("transforms") or []}
    traces = {
        name: [
            trace.touched("normalize", pointer, CHANGED if name in transformed else CARRIED)
            for trace in source.traces.get(name, [])
        ]
        for name in source.columns
    }
    return _Relation(list(source.columns), traces)


def _join_relation(step: dict, relations: dict[str, _Relation], pointer: str) -> _Relation:
    """`join` decides its own output names, so the lineage comes off `join_columns`."""
    left = relations.get(step["left"]) or _Relation(None, reason=_MISSING)
    right = relations.get(step["right"]) or _Relation(None, reason=_MISSING)
    if left.columns is None:
        return left
    if right.columns is None:
        return right

    lkeys, rkeys = resolve_keys(step.get("keys"), step.get("left_on"), step.get("right_on"))
    shared = step.get("keys") is not None

    columns, traces = [], {}
    for out in join_columns(left.columns, right.columns, lkeys, rkeys, shared_names=shared):
        # A shared key is `coalesce(l.k, r.k)` and so reads both inputs, but it
        # changes neither side's values — it picks one. That the column is a
        # composite is already free from its edge count (§4.2), so it needs no
        # rank of its own here; each origin keeps the step that explains *it*.
        found: list[_Trace] = []
        for side, column in ((left, out.left), (right, out.right)):
            if column is None:
                continue
            rank = RENAMED if out.name != column else CARRIED
            found += [t.touched("join", pointer, rank) for t in side.traces.get(column, [])]
        columns.append(out.name)
        traces[out.name] = _dedupe(found)
    return _Relation(columns, traces)


_MISSING = "a step reads a table this spec does not declare"


def _dedupe(traces: list[_Trace]) -> list[_Trace]:
    """One trace per origin column, keeping the step that explains it best.

    Two sides of a join can carry the same origin — a column that was already
    joined once upstream. Two edges between the same pair would be two records
    of one fact, which is what §4.3 refuses for `OVERLAPS` and refuses here too.
    """
    best: dict[tuple[Ref, str], _Trace] = {}
    for trace in traces:
        seen = best.get((trace.table, trace.column))
        if seen is None or trace.rank >= seen.rank:
            best[(trace.table, trace.column)] = trace
    return list(best.values())


def render_text(result: BuildResult) -> str:
    """The build, for a human: what got read, and what could not be."""
    from portia.knowledge.schema import render_text as render_graph

    lines = [render_graph(result.graph)]
    if result.unresolved:
        lines.append("columns not resolved:")
        lines += [f"  {name}: {why}" for name, why in sorted(result.unresolved.items())]
    return "\n".join(lines)

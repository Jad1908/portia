"""The project and its specs as DAGs — positions only, no rendering, no NiceGUI.

The graph is free: every step has an ``id`` and names the tables it reads, and
every spec names the other specs it reads, so both shapes are already fully
determined by the YAML. This module turns that into coordinates; `workflow.py`
draws them.

**There are two graphs and they are two zoom levels of one picture.** Across the
project, a node is a *table* — one spec, one table (`docs/PIPELINE.md` §2.1) — and
an edge is one model reading another. Inside a spec, a node is a *step*. That is
`VISION.md`'s oldest open question — *are cards steps or tables?* — answered as
**both, at different levels**, which is what expanding a model card in place shows:
the same canvas, one card opened to reveal the chain that builds it.

Three rules from DESIGN.md are enforced here rather than left to the renderer:

- **Three node kinds, not two.** A `SOURCE` is a file, a `MODEL` is another
  spec's table, a `STEP` is a step. Before the pipeline overhaul the first two
  were one bucket and drew identically, so a table portia had built looked exactly
  like a CSV somebody dropped in — the graph could not say which of its inputs it
  was responsible for.
- **Left-to-right in dependency order.** Columns come from dependency depth, and
  the order within a column is the order the spec records. Nothing is re-sorted by
  a number, because the sequence *is* the recorded sequence of decisions.
  Dependency order is a **derived fact** — it is what `spec.run_order` computes —
  and is the only ordering here. A ``layer`` never contributes to a position: it
  is a label a human typed, nothing measured it, and staging→mart is build order
  rather than a quality ladder.
- **Cards are uniform, and uniform means tall enough for the most any of them
  carries.** `step_height` sizes every card in a graph to the same height, derived
  from the largest number of badges on any one of them. No card is bigger *because*
  it has more to say — which is the rule — but nothing is clipped either, and a
  card that hides a blocking flag is the bug DESIGN.md's uniformity was never
  meant to license.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass, field, replace

from portia.spec import step_inputs

STEP = "step"
SOURCE = "source"
MODEL = "model"

#: Card geometry. Uniform by design — a card never grows with its numbers.
NODE_W = 196
SOURCE_H = 34
COL_GAP = 64
ROW_GAP = 16

#: A step card with no badges: the id line, the op chip, the padding and the gaps
#: between them. Every badge row adds ``BADGE_ROW``; see `step_height`.
STEP_H_BASE = 70
BADGE_ROW = 22

#: The floor, and what a card with a single badge row comes to. Kept as a name
#: because it is the height the graph had before badges were measured at all.
STEP_H = STEP_H_BASE + BADGE_ROW

#: A collapsed model card is exactly its header row: the name, the layer, and how
#: many steps build it. Close in size to a source node on purpose — both are
#: tables, and the difference between them is what they are, not how much they
#: matter.
MODEL_W = 268
MODEL_HEADER_H = 48
MODEL_H = MODEL_HEADER_H

#: An expanded one keeps that header and insets its step graph below it.
MODEL_PAD = 14

#: Half-width of the arrowhead at an edge's target. DESIGN.md caps it at 6px.
ARROW = 6


def step_height(badges: int) -> int:
    """How tall every step card in a graph is, given the most badges any one has.

    One height for the whole graph, not one per card: the card that carries three
    blocking flags is the same size as the card that carries none, so size never
    says "look here". Reading the max rather than fixing a constant is what stops
    the third badge falling off the bottom of a 92px box, which is what happened
    before this was measured.
    """
    return max(STEP_H, STEP_H_BASE + BADGE_ROW * max(badges, 1))


@dataclass(frozen=True)
class Node:
    id: str
    kind: str  # STEP, SOURCE or MODEL
    x: int
    y: int
    w: int
    h: int
    step: dict | None = None  # the step verbatim, for a STEP node
    #: For a MODEL: the layer its spec declares, and how many steps build it.
    layer: str | None = None
    steps: int = 0
    #: For an expanded MODEL: its own step graph, in coordinates relative to the
    #: card's content origin. ``None`` when the card is collapsed.
    inner: Layout | None = None

    @property
    def op(self) -> str | None:
        return (self.step or {}).get("op")

    @property
    def expanded(self) -> bool:
        return self.inner is not None


@dataclass(frozen=True)
class Edge:
    src: str
    dst: str
    x1: int
    y1: int
    x2: int
    y2: int

    def path(self) -> str:
        """A cubic bezier leaving the source rightward and arriving leftward."""
        bend = max(24, (self.x2 - self.x1) // 2)
        return (
            f"M {self.x1} {self.y1} "
            f"C {self.x1 + bend} {self.y1}, {self.x2 - bend} {self.y2}, "
            f"{self.x2 - ARROW} {self.y2}"
        )

    def arrowhead(self) -> str:
        """Points for the ≤6px triangle at the target end."""
        return (
            f"{self.x2},{self.y2} "
            f"{self.x2 - ARROW},{self.y2 - ARROW // 2 - 1} "
            f"{self.x2 - ARROW},{self.y2 + ARROW // 2 + 1}"
        )


@dataclass(frozen=True)
class Layout:
    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    width: int = 0
    height: int = 0

    @property
    def empty(self) -> bool:
        return not self.nodes


# --- one spec: cards are steps ----------------------------------------------


def layout(
    spec: dict | None,
    *,
    models: Collection[str] = (),
    badges: dict[str, int] | None = None,
    include_inputs: bool = True,
) -> Layout:
    """Place one spec's steps — and, by default, what they read — on a grid.

    ``models`` names the project's other specs, which is the whole of how a
    reference is classified: a name in there is a `MODEL` (a table portia built,
    which you can open), anything else is a `SOURCE` (a file). Pass nothing and
    every input is a file, which is what a project of one spec means.

    ``badges`` maps a step id to how many badges its card will show, so every card
    can be sized to the largest. ``include_inputs=False`` places the steps alone —
    what an expanded model card holds, where the inputs are already drawn as the
    card's own incoming edges and repeating them inside would say it twice.
    """
    steps = list((spec or {}).get("steps") or [])
    known = {s["id"] for s in steps if s.get("id")}
    inputs = {s["id"]: step_inputs(s) for s in steps if s.get("id")}

    depths = _step_depths(steps, inputs)
    height = step_height(max((badges or {}).values(), default=0))

    columns: dict[int, list[Node]] = {}
    if include_inputs:
        names = _input_names(spec, inputs, known)
        depths.update(_input_depths(names, inputs, depths))
        model_names = set(models)
        for name in names:
            kind = MODEL if name in model_names else SOURCE
            columns.setdefault(depths[name], []).append(Node(name, kind, 0, 0, NODE_W, SOURCE_H))
    for step in steps:
        if step.get("id"):
            columns.setdefault(depths[step["id"]], []).append(
                Node(step["id"], STEP, 0, 0, NODE_W, height, step)
            )

    nodes = _place(columns)
    return _finish(nodes, _edges(inputs, nodes))


def _step_depths(steps: list[dict], inputs: dict[str, list[str]]) -> dict[str, int]:
    """One column per hop, in the order the spec records the steps.

    Steps are append-only within a spec, so one forward pass suffices and there is
    no cycle to guard against. A reference to another spec's output is not a step
    and lands at depth 0 via `_input_depths`, which is correct: from inside this
    spec it is an input like any other.
    """
    depths: dict[str, int] = {}
    for step in steps:
        step_id = step.get("id")
        if step_id:
            depths[step_id] = 1 + max((depths.get(ref, 0) for ref in inputs[step_id]), default=0)
    return depths


def _input_names(spec: dict | None, inputs: dict[str, list[str]], known: set[str]) -> list[str]:
    """Every table this spec reads that isn't one of its own steps.

    The spec's registry plus anything a step references, because a spec lists its
    sources *and* a step can name another spec's output, which the registry never
    holds. Order is first-appearance, which is spec order.
    """
    names = list((spec or {}).get("sources") or {})
    for refs in inputs.values():
        names += [ref for ref in refs if ref not in known]
    return list(dict.fromkeys(n for n in names if n not in known))


def _input_depths(
    names: list[str], inputs: dict[str, list[str]], depths: dict[str, int]
) -> dict[str, int]:
    """Sit an input one column left of the earliest step that reads it.

    Structural, not a ranking: it shortens the edge to a table first consumed at
    hop three instead of stranding it against the left margin.
    """
    placed = {}
    for name in names:
        consumers = [depths[sid] for sid, refs in inputs.items() if name in refs and sid in depths]
        placed[name] = max(0, min(consumers) - 1) if consumers else 0
    return placed


# --- the project: cards are tables ------------------------------------------


def project_layout(
    docs: dict[str, dict],
    *,
    expanded: Collection[str] = (),
    badges: dict[str, int] | None = None,
) -> Layout:
    """Place every model in the project, and the files they read, on one grid.

    ``docs`` is ``model name -> its spec``, which is `spec.discover_specs` with the
    YAML loaded. Edges come from what the specs say they read — nothing declares an
    order, exactly as `spec.run_order` derives the build order rather than being
    told it.

    A model in ``expanded`` is laid out with its own step graph inside it and sized
    to fit; the rest are uniform cards. Columns are sized to their widest member,
    so opening one card pushes its neighbours aside rather than overlapping them.
    """
    if not docs:
        return Layout()

    open_models = set(expanded) & set(docs)
    sources = {name: list(doc.get("sources") or {}) for name, doc in docs.items()}
    deps = {name: _model_deps(doc, docs) for name, doc in docs.items()}
    depths = _dependency_depths(deps)

    # Files need a column to the left of the models, and a model with no upstream
    # model is already at depth 0 — so everything shifts right by one to make room.
    # Without the shift a raw CSV shared a column with the staging table built from
    # it, which is the one relationship this graph most has to get right.
    files = list(dict.fromkeys(name for names in sources.values() for name in names))
    offset = 1 if files else 0
    file_depths = {
        name: min((depths[m] for m, names in sources.items() if name in names), default=0)
        + offset
        - 1
        for name in files
    }

    columns: dict[int, list[Node]] = {}
    for name in sorted(files):
        columns.setdefault(file_depths[name], []).append(Node(name, SOURCE, 0, 0, NODE_W, SOURCE_H))
    for name in sorted(docs):
        node = _model_node(name, docs[name], open_models, badges)
        columns.setdefault(depths[name] + offset, []).append(node)

    nodes = _place(columns)
    reads = {name: sorted(deps[name]) + sources[name] for name in docs}
    return _finish(nodes, _edges(reads, nodes))


def _model_node(name: str, doc: dict, open_models: set[str], badges: dict[str, int] | None) -> Node:
    """One model's card — collapsed, or opened onto the steps that build it."""
    steps = len(doc.get("steps") or [])
    layer = doc.get("layer")
    if name not in open_models:
        return Node(name, MODEL, 0, 0, MODEL_W, MODEL_H, layer=layer, steps=steps)

    inner = layout(doc, badges=badges, include_inputs=False)
    return Node(
        name,
        MODEL,
        0,
        0,
        max(MODEL_W, inner.width + 2 * MODEL_PAD),
        MODEL_HEADER_H + inner.height + 2 * MODEL_PAD,
        layer=layer,
        steps=steps,
        inner=inner,
    )


def _model_deps(doc: dict, docs: dict[str, dict]) -> set[str]:
    """The other models this spec reads, by plain name (`PIPELINE.md` §2.4)."""
    own = {s["id"] for s in (doc.get("steps") or []) if s.get("id")}
    refs = {ref for step in (doc.get("steps") or []) for ref in step_inputs(step)}
    return (refs & set(docs)) - own


def _dependency_depths(deps: dict[str, set[str]]) -> dict[str, int]:
    """One column per hop through the project's DAG — longest path from a root.

    `spec.run_order` refuses a cycle and this is downstream of it, but the guard
    stays: a malformed project should draw something wrong rather than hang the
    window, and a graph is the surface most likely to be looked at while
    diagnosing exactly that.
    """
    depths: dict[str, int] = {}

    def depth(name: str, trail: frozenset[str]) -> int:
        if name in depths:
            return depths[name]
        if name in trail:
            return 0
        found = 1 + max((depth(p, trail | {name}) for p in deps.get(name, ())), default=-1)
        depths[name] = found
        return found

    for name in deps:
        depth(name, frozenset())
    return depths


# --- shared placement --------------------------------------------------------


def _place(columns: dict[int, list[Node]]) -> dict[str, Node]:
    """Lay columns out left to right, each as wide as its widest member.

    Per-column widths rather than one global stride: an expanded model card is
    wider than a collapsed one, and a fixed stride would put it underneath its
    neighbour the moment it opened.
    """
    placed: dict[str, Node] = {}
    x = 0
    for column in sorted(columns):
        members = columns[column]
        y = 0
        for node in members:
            placed[node.id] = replace(node, x=x, y=y)
            y += node.h + ROW_GAP
        x += max(n.w for n in members) + COL_GAP
    return placed


def _edges(inputs: dict[str, list[str]], nodes: dict[str, Node]) -> list[Edge]:
    return [
        _edge(nodes[ref], nodes[target])
        for target, refs in inputs.items()
        for ref in dict.fromkeys(refs)  # a self-join names one table twice
        if ref in nodes and target in nodes
    ]


def _edge(src: Node, dst: Node) -> Edge:
    return Edge(
        src.id,
        dst.id,
        src.x + src.w,
        src.y + src.h // 2,
        dst.x,
        dst.y + dst.h // 2,
    )


def _finish(nodes: dict[str, Node], edges: list[Edge]) -> Layout:
    placed = list(nodes.values())
    width = max((n.x + n.w for n in placed), default=0)
    height = max((n.y + n.h for n in placed), default=0)
    return Layout(placed, edges, width, height)

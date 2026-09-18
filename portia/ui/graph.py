"""The project as a DAG of tables — positions only, no rendering, no NiceGUI.

The graph is free: every spec names the tables it reads, so the shape is already
fully determined by the YAML. This module turns that into coordinates;
`workflow.py` draws them.

**A card is a table. Always.** *(2026-08-15)* This reverses the answer this module
used to give to `VISION.md`'s oldest open question — *are cards steps or tables?*
— which was *both, at different zoom levels*: the project drew one card per spec,
and opening one revealed a second flow chart of cards, one per step, with its own
arrows. That put two different kinds of thing on one canvas at the same time, and
the bigger a spec got the less either of them read as anything. The demo project
is one table built by ten steps, four of which are competing attempts at the same
aggregation; drawn as a box of ten boxes it is unreadable.

What settled it is a fact about how a spec compiles rather than a preference
about drawing: **every step is a named block inside one SQL file, and a spec
produces exactly one materialized table** (`docs/PIPELINE.md` §3). So a step is
never a table, and the rule "one card, one table" places it with no judgment call
left over — steps are an ordered list inside the card, which is what `workflow`
draws, and nothing here needs to know they exist beyond how many rows they cost.

Three rules from DESIGN.md are enforced here rather than left to the renderer:

- **Two node kinds.** A `SOURCE` is a file that arrived, a `MODEL` is a table
  portia built. That difference is the one thing this canvas most has to say, and
  it is the only distinction of kind left on it.
- **Left-to-right in dependency order.** Columns come from dependency depth, and
  the order within a column is name order. Nothing is re-sorted by a number,
  because there is no number here that means anything. Dependency order is a
  **derived fact** — it is what `spec.run_order` computes — and is the only
  ordering here. A ``layer`` never contributes to a position: it is a label a
  human typed, nothing measured it, and staging→mart is build order rather than a
  quality ladder.
- **Cards are uniform.** Every collapsed model card is one size and every open one
  is one size. A card is never bigger *because* it has more to say; an open card
  is taller than a shut one because it is showing more, which is a statement about
  what you asked for and not about the table.

**Visibility and ancestry.** `ancestry` is the one rule the filter is built on:
choosing a table means choosing everything it is built from, because a flow chart
missing an input is not a simplified flow chart, it is a wrong one. It is also
what the renderer highlights with, so the two can never disagree about what
"upstream of this" means.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass, field, replace

from portia.spec import step_inputs

SOURCE = "source"
MODEL = "model"

#: Card geometry. Uniform by design — a card never grows with its numbers.
NODE_W = 196
SOURCE_H = 34
COL_GAP = 64
ROW_GAP = 16

#: A collapsed model card is exactly its header row: the name, the layer, and how
#: many steps build it. Close in size to a source node on purpose — both are
#: tables, and the difference between them is what they are, not how much they
#: matter.
MODEL_W = 268
MODEL_HEADER_H = 48
MODEL_H = MODEL_HEADER_H

#: An open one is wider, and one row taller per line of body it draws. Wider by a
#: fixed amount rather than by its content: a card that sized itself to its
#: longest step id would make the busiest table the biggest thing on the canvas,
#: which is the rank-by-size rule DESIGN.md forbids.
MODEL_OPEN_W = 340
MODEL_PAD = 12
BODY_ROW_H = 24

#: Half-width of the arrowhead at an edge's target. DESIGN.md caps it at 6px.
ARROW = 6


def open_height(rows: int) -> int:
    """How tall an open card is, given how many body rows it draws.

    One row unit for section headings and list rows alike, so the renderer can
    count what it is about to draw without this module knowing what any of it
    says.
    """
    return MODEL_HEADER_H + 2 * MODEL_PAD + BODY_ROW_H * max(rows, 0)


@dataclass(frozen=True)
class Node:
    id: str
    kind: str  # SOURCE or MODEL
    x: int
    y: int
    w: int
    h: int
    #: For a MODEL: the layer its spec declares, and how many steps build it.
    layer: str | None = None
    steps: int = 0
    #: Whether this card is drawn open, showing its inputs and its steps.
    open: bool = False
    #: A card drawn as a **preview**: not on the canvas, shown translucent to say
    #: what a spec you picked would need. Never counted as drawn, never run, and
    #: never in `Layout.hidden` — it is on screen precisely to say it is not.
    ghost: bool = False


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
    #: Models the filter left out, so the renderer can say the canvas is showing
    #: part of the project. A count would do, but the names are what makes
    #: "showing 3 of 7" checkable rather than something to take on trust.
    hidden: tuple[str, ...] = ()

    @property
    def empty(self) -> bool:
        return not self.nodes


# --- what a table is built from ---------------------------------------------


def model_deps(doc: dict, docs: dict[str, dict]) -> set[str]:
    """The other models this spec reads, by plain name (`PIPELINE.md` §2.4)."""
    own = {s["id"] for s in (doc.get("steps") or []) if s.get("id")}
    refs = {ref for step in (doc.get("steps") or []) for ref in step_inputs(step)}
    return (refs & set(docs)) - own


def model_sources(doc: dict) -> list[str]:
    """The files this spec reads, in the order it registers them."""
    return list(doc.get("sources") or {})


def ancestry(docs: dict[str, dict], names: Iterable[str]) -> set[str]:
    """Those models and every model upstream of them.

    **The rule the visibility filter is built on**, and the same set the renderer
    highlights: picking a table to look at means picking everything it is built
    from, because a flow chart with an input missing is not a shorter answer to
    the same question — it is an answer to a different one. Nothing here can be
    un-picked individually for that reason.

    Guards against a cycle rather than trusting `spec.run_order` to have refused
    one: a malformed project should draw something wrong rather than hang the
    window, and this canvas is the surface most likely to be open while
    diagnosing exactly that.
    """
    found: set[str] = set()

    def walk(name: str) -> None:
        if name in found or name not in docs:
            return
        found.add(name)
        for parent in model_deps(docs[name], docs):
            walk(parent)

    for name in names:
        walk(name)
    return found


def descendants(docs: dict[str, dict], names: Iterable[str]) -> set[str]:
    """Those models and every model built **from** them — the mirror of `ancestry`.

    What un-choosing a table has to take with it. A table cannot be drawn without
    the tables it is built from, so removing an input removes everything that
    reads it: the alternative is a canvas holding a card whose incoming arrow
    points at nothing, which is not a simplified picture but a wrong one.

    Guards against a cycle for the same reason `ancestry` does.
    """
    wanted = set(names)
    found: set[str] = set()
    frontier = wanted & set(docs)
    while frontier:
        found |= frontier
        frontier = {
            name
            for name, doc in docs.items()
            if name not in found and model_deps(doc, docs) & found
        }
    return found


def upstream_hops(docs: dict[str, dict], name: str) -> dict[str, int]:
    """How many hops each upstream node is from ``name`` — the table itself at 0.

    **The direction the highlight flows along.** Lighting the path into a table
    says *these are involved*; saying it in hop order says *and this is the way
    the data travels*, which is the thing a flow chart is for. The renderer turns
    each hop into an animation delay, so the pulse leaves the files and arrives at
    the table you picked rather than everything blinking at once.

    Breadth-first, so a node reachable by two routes takes the **shorter** one —
    a table feeding both a staging model and the mart directly is one hop away,
    and lighting it as three would make the animation disagree with the arrow you
    can see.
    """
    if name not in docs:
        return {}
    hops = {name: 0}
    frontier = [name]
    while frontier:
        nxt = []
        for current in frontier:
            parents = model_deps(docs[current], docs) | set(model_sources(docs[current]))
            for parent in parents:
                if parent not in hops:
                    hops[parent] = hops[current] + 1
                    if parent in docs:
                        nxt.append(parent)
        frontier = nxt
    return hops


def upstream_ids(docs: dict[str, dict], name: str) -> set[str]:
    """Every node id feeding ``name``, itself included — models and files alike.

    What the renderer marks when a table is selected. Files are in it because the
    highlight traces the flow back to where the data entered, and stopping at the
    first model would leave the arrows that cross the left edge of the picture
    unexplained.
    """
    models = ancestry(docs, [name])
    files = {src for model in models for src in model_sources(docs[model])}
    return models | files


# --- the project: every card is a table --------------------------------------


def project_layout(
    docs: dict[str, dict],
    *,
    expanded: Collection[str] = (),
    rows: dict[str, int] | None = None,
    visible: Collection[str] | None = None,
    preview: Collection[str] = (),
    offsets: Mapping[str, tuple[int, int]] | None = None,
) -> Layout:
    """Place the project's tables, and the files they read, on one grid.

    ``docs`` is ``model name -> its spec``, which is `spec.discover_specs` with the
    YAML loaded. Edges come from what the specs say they read — nothing declares an
    order, exactly as `spec.run_order` derives the build order rather than being
    told it.

    ``visible`` narrows the canvas to a chosen set of models **and their
    ancestry** (`ancestry`); ``None`` means the whole project. A model in
    ``expanded`` is drawn open and sized by ``rows``, which is how many body rows
    the renderer is about to put in it. Columns are sized to their widest member,
    so opening one card pushes its neighbours aside rather than overlapping them.

    ``offsets`` is where the reader dragged each card, as a delta in layout
    pixels **on top of** the place the grid gives it (`moved`). A delta rather
    than a position, so a card the reader moved keeps its distance from the
    grid when the grid moves under it: opening a neighbour still pushes the
    column aside, and a dragged card goes with its column instead of staying
    behind at coordinates that meant something in the last layout.
    """
    if not docs:
        return Layout()

    shown = _visible(docs, visible)
    # A previewed model is laid out with the rest — it has to sit where it *would*
    # sit, or the preview answers a different question from the one asked — but it
    # stays in `hidden`, because it is genuinely not on the canvas.
    ghosts = (set(preview) & set(docs)) - shown
    hidden = tuple(sorted(set(docs) - shown))
    docs = {name: doc for name, doc in docs.items() if name in shown | ghosts}
    if not docs:
        return Layout(hidden=hidden)

    # **A ghost is never open.** It is not on the canvas, so there is nothing to
    # read inside it — an open preview card offers its columns and its steps as
    # if you were looking at the table, which is the one thing it is there to say
    # you are not.
    open_models = (set(expanded) & set(docs)) - ghosts
    sources = {name: model_sources(doc) for name, doc in docs.items()}
    deps = {name: model_deps(doc, docs) for name, doc in docs.items()}
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

    # A file is a ghost when **every** model reading it is one: a file the canvas
    # already draws for a model it already draws is not part of what is missing.
    ghost_files = {
        name for name in files if {m for m, names in sources.items() if name in names} <= ghosts
    }

    columns: dict[int, list[Node]] = {}
    for name in sorted(files):
        columns.setdefault(file_depths[name], []).append(
            Node(name, SOURCE, 0, 0, NODE_W, SOURCE_H, ghost=name in ghost_files)
        )
    for name in sorted(docs):
        node = _model_node(name, docs[name], open_models, rows or {})
        columns.setdefault(depths[name] + offset, []).append(
            replace(node, ghost=True) if name in ghosts else node
        )

    nodes = moved(_place(columns), offsets or {})
    reads = {name: sorted(deps[name]) + sources[name] for name in docs}
    return replace(_finish(nodes, _edges(reads, nodes)), hidden=hidden)


def overlapping(nodes: Iterable[Node], name: str) -> list[str]:
    """Which other cards the named one sits on top of, by rectangle.

    **Two cards never stack** *(2026-09-07)*. A card may be dragged across
    another, and a drop that would leave it there is refused (`workflow.move_card`)
    — a card under a card is a table you cannot see, and an arrow into it points
    at the wrong thing. Touching edges is not overlapping; the test is a strict
    intersection, so cards may sit flush.
    """
    placed = {n.id: n for n in nodes}
    me = placed.get(name)
    if me is None:
        return []
    return sorted(
        other.id
        for other in placed.values()
        if other.id != name
        and me.x < other.x + other.w
        and other.x < me.x + me.w
        and me.y < other.y + other.h
        and other.y < me.y + me.h
    )


def moved(nodes: dict[str, Node], offsets: Mapping[str, tuple[int, int]]) -> dict[str, Node]:
    """Shift each node by the reader's offset for it. Applied after the grid, before the edges.

    **The grid is still the layout** *(2026-09-07)*. The arrows are drawn from
    the shifted nodes, so an edge follows the card it touches and keeps saying
    what it said — *this table reads that one* is in the specs, and no amount of
    dragging changes which way an arrow points. What the reader arranges is
    where the cards sit on the surface, which is theirs; the derived order is
    one Reset away (`workflow.reset_layout`). A name the layout has no node for
    is ignored rather than refused: the offsets outlive a spec that was removed.
    """
    return {
        name: replace(node, x=node.x + offsets[name][0], y=node.y + offsets[name][1])
        if name in offsets
        else node
        for name, node in nodes.items()
    }


def drawn_models(docs: dict[str, dict], visible: Collection[str] | None) -> set[str]:
    """Which models the canvas is actually drawing, for that choice.

    The same answer `project_layout` places, without the coordinates — so a
    handler asking *is this table already on screen* and the renderer drawing it
    cannot disagree about what the filter means.
    """
    return _visible(docs, visible)


def _visible(docs: dict[str, dict], visible: Collection[str] | None) -> set[str]:
    """Which models to draw: everything, or a choice closed over its ancestry.

    **``None`` and the empty set are different** *(2026-08-16)*. ``None`` is
    *nothing has been chosen*, which draws the whole project — a canvas that
    started blank and stayed blank until you found the control would not be a
    focused view of anything. An empty **set** is *you turned everything off*,
    which draws nothing, and is reachable because the filter's select-all is a
    toggle. They read the same on disk and mean opposite things, so they are two
    values rather than one falsy one.
    """
    if visible is None:
        return set(docs)
    chosen = set(visible) & set(docs)
    if visible and not chosen:
        # **A stale choice is not a choice to see nothing.** The set is restored
        # from disk and may name tables a project no longer has; blanking the
        # canvas because every remembered name was renamed or deleted would tell
        # you the project is empty. Turning everything off deliberately is the
        # empty set, which is a different value and still draws nothing.
        return set(docs)
    return ancestry(docs, chosen)


def _model_node(name: str, doc: dict, open_models: set[str], rows: dict[str, int]) -> Node:
    """One model's card — shut, or open onto what it reads and how it is built."""
    steps = len(doc.get("steps") or [])
    layer = doc.get("layer")
    if name not in open_models:
        return Node(name, MODEL, 0, 0, MODEL_W, MODEL_H, layer=layer, steps=steps)
    return Node(
        name,
        MODEL,
        0,
        0,
        MODEL_OPEN_W,
        open_height(rows.get(name, steps)),
        layer=layer,
        steps=steps,
        open=True,
    )


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

    Per-column widths rather than one global stride: an open model card is wider
    than a shut one, and a fixed stride would put it underneath its neighbour the
    moment it opened.
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

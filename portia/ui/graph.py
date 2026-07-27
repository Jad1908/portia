"""The spec as a DAG — positions only, no rendering and no NiceGUI import.

The graph is free: every step has an ``id`` and names the tables it reads, so
the shape is already fully determined by the YAML (docs/VISION.md → V0). This
module turns that into coordinates; `workflow.py` draws them.

Two rules from DESIGN.md are enforced here rather than left to the renderer:

- **Nodes are steps; an edge means "this step's output is that step's input".**
  Derived from ``spec.step_inputs``, never from anything measured.
- **Left-to-right in spec order.** Columns come from dependency depth and the
  order within a column is the order the spec records. Nothing is re-sorted by a
  number, because the sequence *is* the recorded sequence of decisions and
  re-ordering it would be the ranking this system forbids.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from portia.spec import step_inputs

STEP = "step"
SOURCE = "source"

#: Card geometry. Uniform by design — a card never grows with its numbers.
NODE_W = 196
STEP_H = 92
SOURCE_H = 34
COL_GAP = 64
ROW_GAP = 16

#: Half-width of the arrowhead at an edge's target. DESIGN.md caps it at 6px.
ARROW = 6


@dataclass(frozen=True)
class Node:
    id: str
    kind: str  # STEP or SOURCE
    x: int
    y: int
    w: int
    h: int
    step: dict | None = None  # the step verbatim, for a STEP node

    @property
    def op(self) -> str | None:
        return (self.step or {}).get("op")


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


def layout(spec: dict | None) -> Layout:
    """Place every source and step of ``spec`` on a left-to-right grid."""
    steps = list((spec or {}).get("steps") or [])
    step_ids = [s["id"] for s in steps if s.get("id")]
    known = set(step_ids)

    inputs = {s["id"]: step_inputs(s) for s in steps if s.get("id")}
    depths = _depths(steps, inputs)
    source_names = _source_names(spec, inputs, known)
    depths.update(_source_depths(source_names, inputs, depths))

    columns: dict[int, list[tuple[str, str, dict | None]]] = {}
    for name in source_names:
        columns.setdefault(depths[name], []).append((name, SOURCE, None))
    for step in steps:
        if step.get("id"):
            columns.setdefault(depths[step["id"]], []).append((step["id"], STEP, step))

    nodes: dict[str, Node] = {}
    for column, members in sorted(columns.items()):
        x = column * (NODE_W + COL_GAP)
        y = 0
        for name, kind, step in members:
            height = STEP_H if kind == STEP else SOURCE_H
            nodes[name] = Node(name, kind, x, y, NODE_W, height, step)
            y += height + ROW_GAP

    edges = [
        _edge(nodes[ref], nodes[step_id])
        for step_id, refs in inputs.items()
        for ref in dict.fromkeys(refs)  # a self-join names one table twice
        if ref in nodes and step_id in nodes
    ]

    placed = list(nodes.values())
    width = max((n.x + n.w for n in placed), default=0)
    height = max((n.y + n.h for n in placed), default=0)
    return Layout(placed, edges, width, height)


def _edge(src: Node, dst: Node) -> Edge:
    return Edge(
        src.id,
        dst.id,
        src.x + src.w,
        src.y + src.h // 2,
        dst.x,
        dst.y + dst.h // 2,
    )


def _depths(steps: list[dict], inputs: dict[str, list[str]]) -> dict[str, int]:
    """One column per hop. Steps are append-only, so one forward pass suffices.

    A step can only chain from an *earlier* step (``handlers._bare_step_id``), so
    every reference is already placed by the time it is read and there is no
    cycle to guard against.
    """
    depths: dict[str, int] = {}
    for step in steps:
        step_id = step.get("id")
        if step_id:
            depths[step_id] = 1 + max((depths.get(ref, 0) for ref in inputs[step_id]), default=0)
    return depths


def _source_names(spec: dict | None, inputs: dict[str, list[str]], known: set[str]) -> list[str]:
    """Every table that isn't a step: the spec's registry plus anything referenced.

    Both, because a spec lists its sources *and* a step could name something the
    registry hasn't caught up with. Order is first-appearance, which is spec order.
    """
    names = list((spec or {}).get("sources") or {})
    for refs in inputs.values():
        names += [ref for ref in refs if ref not in known]
    return list(dict.fromkeys(n for n in names if n not in known))


def _source_depths(
    names: list[str], inputs: dict[str, list[str]], depths: dict[str, int]
) -> dict[str, int]:
    """Sit a source one column left of the earliest step that reads it.

    Structural, not a ranking: it shortens the edge to a table first consumed at
    hop three instead of stranding it against the left margin.
    """
    placed = {}
    for name in names:
        consumers = [depths[sid] for sid, refs in inputs.items() if name in refs and sid in depths]
        placed[name] = max(0, min(consumers) - 1) if consumers else 0
    return placed

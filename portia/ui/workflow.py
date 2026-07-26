"""Middle pane — the spec as a graph over its run report.

The top half draws what the YAML already encodes: cards are **steps**, an edge
means *this step's output is that step's input*. Clicking a card shows the step
verbatim, and **an acknowledged blocking flag is impossible to miss there** —
Run 5 buried one mid-dict in a terminal confirmation and shipped a 3.85%-inflated
table (docs/EVALUATION.md). This screen is the second chance at that.

The bottom half is `cli/run.py`'s output with the table attached: per step, what
``StepResult`` already carries — provenance, drift against ``expect``, the
``outcome`` post-conditions, blocking flags — plus a preview of what came out.

**Provenance and outcome are separate blocks and stay separate.** They answer
different questions: a correct prediction about a broken join is still a broken
join, and collapsing them into one "status" is the mistake this project spent
three runs unlearning.

Nothing here computes. Every number on screen came out of `checks`/`ops`/`spec`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from nicegui import ui

from portia.checks.outcome import BLOCKING_FLAGS
from portia.ui import components as c
from portia.ui import engine, graph
from portia.ui.state import APP, OUTPUT, SOURCE

#: How tall the graph half sits by default, as a percentage. The report half is
#: the taller of the two — it is where the evidence is (DESIGN.md → Layout).
GRAPH_SPLIT = 38


@ui.refreshable
async def pane() -> None:
    kind, name = APP.selection or (None, "")
    if kind == SOURCE:
        _source_inspector(name)
    elif kind == OUTPUT:
        await _output_inspector(name)
    else:
        _workflow()


# --- the workflow -----------------------------------------------------------


def _workflow() -> None:
    with ui.splitter(horizontal=True, value=GRAPH_SPLIT).classes("w-full h-full p-splitter") as sp:
        with sp.before:
            _graph_half()
        with sp.after:
            _report_half()


def _graph_half() -> None:
    with ui.element("div").classes("p-pane"):
        _graph_header()
        with ui.element("div").classes("p-scroll graph-canvas"):
            placed = graph.layout(APP.spec)
            if placed.empty:
                c.empty_note(_NO_STEPS if APP.spec_path else _NO_SPEC)
            else:
                _graph(placed)
            _step_detail()


def _graph_header() -> None:
    with ui.element("div").classes("row-gap-sm px-4 pt-3"):
        name = APP.spec_path.name if APP.spec_path else "no spec open"
        ui.label(name).classes("t-heading-sm")
        steps = len((APP.spec or {}).get("steps") or [])
        c.caption(c.count(steps, "step"))


def _graph(placed: graph.Layout) -> None:
    style = f"position:relative;width:{placed.width}px;height:{placed.height}px"
    with ui.element("div").style(style):
        ui.html(_edges_svg(placed))
        for node in placed.nodes:
            _node(node)


def _edges_svg(placed: graph.Layout) -> str:
    """One overlay for every edge. 1px hairline-strong, small arrowheads, no labels."""
    parts = [
        f'<path d="{edge.path()}"/><polygon points="{edge.arrowhead()}"/>' for edge in placed.edges
    ]
    return (
        f'<svg class="graph-edges" width="{placed.width}" height="{placed.height}">'
        f"{''.join(parts)}</svg>"
    )


def _node(node: graph.Node) -> None:
    style = f"left:{node.x}px;top:{node.y}px;width:{node.w}px;height:{node.h}px"
    with ui.element("div").classes("graph-node").style(style):
        if node.kind == graph.SOURCE:
            ui.label(node.id).classes("source-node")
        else:
            _step_card(node)


def _step_card(node: graph.Node) -> None:
    result = _result(node.id)
    step = node.step or {}
    # Before a run the card shows what the spec *claims*; after one, what the
    # engine measured. An acknowledgement is visible either way.
    blocking = result.blocking if result else []
    acknowledged = list(result.acknowledged) if result else list(step.get("acknowledge") or [])
    drifted = sorted(result.drift) if result else []

    classes = "step-card"
    if blocking:
        classes += " step-card--blocked"
    elif APP.selected_step == node.id:
        classes += " step-card--selected"

    with ui.element("div").classes(classes) as card:
        ui.label(node.id).classes("step-id")
        with ui.element("div").classes("row-gap-xs"):
            c.chip(node.op or "?")
        with ui.element("div").classes("row-gap-xs"):
            for flag in blocking:
                c.flag_badge(flag, c.BLOCKING)
            for flag in acknowledged:
                c.flag_badge(flag, c.ACKNOWLEDGED)
            for field in drifted:
                c.flag_badge(field, c.DRIFT)
    card.on("click", lambda n=node.id: _select_step(n))


def _step_detail() -> None:
    """The selected step, verbatim. Its acknowledgement sits above everything."""
    step = _step(APP.selected_step)
    if step is None:
        return
    with ui.element("div").classes("stack-md mt-4"):
        c.rule()
        with ui.element("div").classes("row-gap-sm pt-3"):
            ui.label(step["id"]).classes("t-heading-md")
            c.chip(step.get("op", "?"))
        if step.get("acknowledge"):
            result = _result(step["id"])
            c.acknowledged_banner(
                list(step["acknowledge"]),
                rationale=step.get("rationale"),
                measured=result.outcome if result else None,
            )
        c.payload_view({k: v for k, v in step.items() if k not in ("id", "op")})


# --- the run report ---------------------------------------------------------


def _report_half() -> None:
    with ui.element("div").classes("p-pane"):
        with ui.element("div").classes("p-scroll p-pad stack-md"):
            if APP.run_error:
                _run_error()
            elif APP.results is None:
                c.empty_note(_NO_RUN)
            else:
                _run_header()
                for result in APP.results:
                    _report_block(result)


def _run_error() -> None:
    with ui.element("div").classes("stack-sm"):
        ui.label("the run failed").classes("t-heading-sm c-error")
        c.code_block(APP.run_error or "")


def _run_header() -> None:
    results = APP.results or []
    blocking = sorted({flag for r in results for flag in r.blocking})
    with ui.element("div").classes("row-gap-sm"):
        ui.label(c.count(len(results), "step")).classes("t-heading-sm")
        for flag in blocking:
            c.flag_badge(flag, c.BLOCKING)
        if not blocking:
            c.caption("no blocking flag")
    c.rule()


def _report_block(result: Any) -> None:
    classes = "report-block" + (" report-block--selected" if APP.selected_step == result.id else "")
    with ui.element("div").classes(classes) as block:
        with ui.element("div").classes("row-gap-sm"):
            ui.label(result.id).classes("t-mono c-ink")
            c.chip(result.op)

        if result.acknowledged:
            c.acknowledged_banner(
                list(result.acknowledged),
                rationale=result.rationale,
                measured=result.outcome,
            )

        _group("provenance", lambda: _provenance(result.provenance))
        _group("outcome", lambda: _outcome(result.outcome, result.acknowledged))
        if result.drift:
            _group("drift", lambda: _drift(result.drift))
        if result.rationale and not result.acknowledged:
            _group("rationale", lambda: c.text(result.rationale))
        if result.frame is not None:
            _group("table", lambda: c.table_preview(result.frame))
    block.on("click", lambda i=result.id: _select_step(i))


def _group(label: str, body) -> None:
    with ui.element("div").classes("report-group"):
        ui.label(label).classes("report-group-label")
        body()


def _provenance(provenance: dict) -> None:
    """What the op did. Never merged with what came out."""
    for key, value in provenance.items():
        if key == "flags":
            _uncoloured_flags(value)
        elif key != "op":
            c.kv(key, value)


def _outcome(outcome: dict, acknowledged: list[str]) -> None:
    """What came out. A correct prediction about a broken join is still broken."""
    if not outcome:
        c.caption("not measured")
        return
    c.kv("rows × cols", f"{outcome.get('n_rows')} × {outcome.get('n_cols')}")
    for key in ("newly_all_null_columns", "all_null_columns", "null_rates"):
        if outcome.get(key):
            c.kv(key, outcome[key])
    for name, contribution in (outcome.get("contribution") or {}).items():
        c.kv(name, contribution)
    if outcome.get("grain"):
        c.kv("grain", outcome["grain"])
    for flag in outcome.get("flags") or []:
        c.flag_badge(flag, c.flag_variant(flag, acknowledged))


def _drift(drift: dict) -> None:
    """One row per failed prediction. Never truncated to a tick."""
    for field, values in drift.items():
        with ui.element("div").classes("drift-row"):
            ui.label(field)
            ui.label(f"expected {values['expected']}")
            ui.label(f"actual {values['actual']}")


def _uncoloured_flags(flags: list[str]) -> None:
    """An op's own flags. Visible, named exactly, and not ranked."""
    with ui.element("div").classes("row-gap-xs"):
        for flag in flags:
            c.flag_badge(flag, c.BLOCKING if flag in BLOCKING_FLAGS else "")


# --- inspectors -------------------------------------------------------------


def _source_inspector(name: str) -> None:
    """A source's catalog entry — the prose read, the roles, the check facts."""
    entry = APP.sources.get(name)
    with ui.element("div").classes("p-scroll p-pad stack-lg"):
        _inspector_header(name, "the catalog's entry for this source")
        if entry is None:
            c.empty_note("no catalog entry")
            return
        c.kv("file", entry.get("source", ""))
        c.kv("candidate keys", entry.get("candidate_keys") or "(none)")
        _group("summary", lambda: c.text(entry.get("summary", "")))
        _group("columns", lambda: _columns(entry.get("columns") or []))


def _columns(columns: list[dict]) -> None:
    for col in columns:
        with ui.element("div").classes("report-block"):
            with ui.element("div").classes("row-gap-sm"):
                ui.label(col["name"]).classes("t-mono c-ink")
                c.chip(str(col.get("inferred", "")))
            c.kv("role", col.get("role") or "—")
            c.kv("null rate", col.get("null_rate"))
            c.kv("distinct", col.get("n_distinct"))
            if col.get("flags"):
                _uncoloured_flags(col["flags"])


async def _output_inspector(name: str) -> None:
    path = APP.root / engine.OUT_DIR / name
    with ui.element("div").classes("p-scroll p-pad stack-lg"):
        _inspector_header(name, "a table a run wrote")
        if not path.exists():
            c.empty_note("that file is gone")
            return
        c.table_preview(await engine.read_frame(path))


def _inspector_header(name: str, note: str) -> None:
    with ui.element("div").classes("stack-xs"):
        with ui.element("div").classes("row-gap-sm"):
            ui.label(name).classes("t-heading-md")
            c.button("Back to workflow", _back, micro=True)
        c.caption(note)
    c.rule()


def _back() -> None:
    from portia.ui import artifacts

    APP.select(None)
    artifacts.pane.refresh()
    pane.refresh()


# --- selection --------------------------------------------------------------


def _select_step(step_id: str) -> None:
    APP.selected_step = None if APP.selected_step == step_id else step_id
    pane.refresh()


def _step(step_id: str | None) -> dict | None:
    if not step_id:
        return None
    for step in (APP.spec or {}).get("steps") or []:
        if step.get("id") == step_id:
            return step
    return None


def _result(step_id: str) -> Any | None:
    for result in APP.results or []:
        if result.id == step_id:
            return result
    return None


def spec_label(path: Path | None) -> str:
    return path.name if path else "no spec"


_NO_SPEC = "No spec open. The copilot writes one as it records steps; pick one on the left."
_NO_STEPS = "This spec has no steps yet."
_NO_RUN = "No run yet. Press Run in the toolbar to execute this spec."

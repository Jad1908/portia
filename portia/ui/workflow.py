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

from functools import partial
from pathlib import Path
from typing import Any

from nicegui import ui

from portia.checks.outcome import BLOCKING_FLAGS, describe_contribution, describe_grain
from portia.core.present import format_rate
from portia.ui import components as c
from portia.ui import engine, graph, state
from portia.ui.state import APP, OUTPUT, RUN, SOURCE

#: How tall the graph half sits by default, as a percentage. The report half is
#: the taller of the two — it is where the evidence is (DESIGN.md → Layout).
GRAPH_SPLIT = 38


@ui.refreshable
async def pane() -> None:
    kind, name = APP.selection or (None, "")
    if kind == SOURCE:
        await _source_inspector(name)
    elif kind == OUTPUT:
        await _output_inspector(name)
    elif kind == RUN:
        await _run_inspector(name)
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

        if result.drift:
            _group("drift", lambda: _drift(result.drift))
        _group("provenance", lambda: _provenance(result.provenance))
        _group("outcome", lambda: _outcome(result.outcome, result.acknowledged))
        if result.rationale and not result.acknowledged:
            _group("rationale", lambda: c.text(result.rationale))
        if result.frame is not None:
            _table(result.frame)
    block.on("click", lambda i=result.id: _select_step(i))


def _group(label: str, body) -> None:
    with ui.element("div").classes("report-group"):
        ui.label(label).classes("report-group-label")
        body()


def _table(frame) -> None:
    """The produced table, one click away.

    Inline it pushed everything below it off the screen, and the point of the
    report is that the four groups can be read at a glance. The label carries
    the shape, so the table is never a surprise you have to open to size up.
    """
    label = f"preview · {c.count(len(frame), 'row')} × {c.count(frame.shape[1], 'column')}"
    c.collapsed(label, lambda: c.table_preview(frame))


def _provenance(provenance: dict) -> None:
    """What the op did. Never merged with what came out."""
    with c.kv_list():
        for key, value in provenance.items():
            if key == "flags":
                c.kv(key, body=partial(_uncoloured_flags, value))
            elif key != "op":  # already the chip in the header
                c.kv(key, value)


def _outcome(outcome: dict, acknowledged: list[str]) -> None:
    """What came out. A correct prediction about a broken join is still broken."""
    if not outcome:
        c.caption("not measured")
        return
    with c.kv_list():
        c.kv("produced", f"{outcome.get('n_rows')} × {outcome.get('n_cols')}")
        for key in ("newly_all_null_columns", "all_null_columns"):
            if outcome.get(key):
                c.kv(key, outcome[key])
        if outcome.get("null_rates"):
            c.kv("null_rates", _rates(outcome["null_rates"]))
        for name, contribution in (outcome.get("contribution") or {}).items():
            c.kv(name, describe_contribution(contribution))
        if outcome.get("grain"):
            c.kv("grain", describe_grain(outcome["grain"]))
        if outcome.get("flags"):
            c.kv("flags", body=partial(_outcome_flags, outcome["flags"], acknowledged))


def _rates(rates: dict) -> str:
    """`customer_id 12% · notes 65%`, formatted the way the terminal formats it."""
    return " · ".join(f"{col} {format_rate(rate)}" for col, rate in rates.items())


def _outcome_flags(flags: list[str], acknowledged: list[str]) -> None:
    with ui.element("div").classes("row-gap-xs"):
        for flag in flags:
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


async def _source_inspector(name: str) -> None:
    """A source's catalog entry — the prose read, the roles, the check facts, the rows.

    The catalog is what the *copilot* sees, and it never sees the rows. A person
    reading the same screen usually wants to, so the data is here too — the one
    place in the app where the difference between the two views is deliberate.
    """
    entry = APP.sources.get(name)
    frame = await _source_frame(entry) if entry else None
    editing = APP.editing == name
    with ui.element("div").classes("p-scroll p-pad stack-lg"):
        _inspector_header(name, "the catalog's entry for this source")
        if entry is None:
            c.empty_note("no catalog entry")
            return
        with c.kv_list():
            c.kv("file", entry.get("source", ""))
            c.kv("candidate_keys", entry.get("candidate_keys") or "(none)")

        columns = entry.get("columns") or []
        if editing:
            _edit_interpretation(name, entry, columns)
        else:
            _group("summary", lambda: c.text(entry.get("summary", "")))
            _group("columns", lambda: _columns(columns))
            _interpretation_actions(name)
        if frame is not None:
            _group("preview", lambda: c.table_preview(frame))


# --- correcting what the catalog says ----------------------------------------


def _interpretation_actions(name: str) -> None:
    """Two ways to fix a read: write it yourself, or tell the copilot what it missed.

    The prose and the roles are **judgment**, and judgment is the half of a
    catalog entry a human is allowed to overwrite — `catalog.set_interpretation`
    writes exactly that and never touches a measured fact, so both routes land in
    the same place and survive a re-index.
    """
    with ui.element("div").classes("row-gap-sm"):
        c.button("Edit", lambda: _start_editing(name), icon="edit", micro=True)
        c.button("Ask the copilot", lambda: _start_asking(name), icon="forum", micro=True)
        ui.element("div").classes("flex-1")
        c.button("Remove", lambda: _start_removing(name), icon="delete_outline", micro=True)
    if APP.asking == name:
        _ask_form(name)
    if APP.removing == name:
        _remove_confirm(name)


def _remove_confirm(name: str) -> None:
    """Un-indexing is reversible; say so, and say what it does not do."""
    with ui.element("div").classes("write-confirm"):
        ui.label(f"Stop indexing {name}?").classes("t-heading-sm")
        c.text(_REMOVE_SCOPE, color="c-mute")
        c.text(_REMOVE_UNDO, color="c-mute")
        with ui.element("div").classes("row-gap-sm"):
            c.button("Remove", lambda: _remove(name))
            c.button("Cancel", _stop_removing, kind="secondary")


def _start_removing(name: str) -> None:
    APP.removing, APP.editing, APP.asking = name, None, None
    pane.refresh()


def _stop_removing() -> None:
    APP.removing = None
    pane.refresh()


def _remove(name: str) -> None:
    from portia.ui import artifacts

    engine.remove_source(name, APP)
    APP.removing = None
    APP.select(None)
    pane.refresh()
    artifacts.pane.refresh()
    ui.notify(f"{name} is no longer indexed")


def _ask_form(name: str) -> None:
    """Say what the copilot got wrong; it re-reads with that in hand."""
    with ui.element("div").classes("question-form"):
        ui.label(_ASK_HEADING).classes("t-heading-sm")
        c.caption(_ASK_WHY)
        note = (
            ui.textarea(placeholder=_ASK_PLACEHOLDER)
            .classes("p-field p-editor w-full")
            .props("borderless autogrow autofocus")
        )
        with ui.element("div").classes("row-gap-sm"):
            c.button("Send", lambda: _ask_copilot(name, note.value), enabled=not APP.busy)
            c.button("Cancel", _stop_asking, kind="secondary")
            c.caption(_spend())


def _edit_interpretation(name: str, entry: dict, columns: list[dict]) -> None:
    """The summary and the roles, editable in place, with the facts still visible."""
    with ui.element("div").classes("report-group"):
        ui.label("summary").classes("report-group-label")
        summary = (
            ui.textarea(value=entry.get("summary", ""))
            .classes("p-field p-editor w-full")
            .props("borderless autogrow")
        )
    roles: dict[str, Any] = {}
    with ui.element("div").classes("report-group"):
        ui.label("columns").classes("report-group-label")
        with ui.element("div").classes("column-list"):
            _column_headings()
            for col in columns:
                roles[col["name"]] = _editable_column_row(col)
    with ui.element("div").classes("row-gap-sm"):
        c.button(
            "Save",
            lambda: _save_interpretation(name, summary.value, roles),
            kind="primary",
            icon="check",
        )
        c.button("Cancel", _stop_editing, kind="secondary")
        c.caption(_EDIT_SCOPE)


def _editable_column_row(col: dict):
    """One column, with its role as a field and its facts still beside it."""
    with ui.element("div").classes("column-row"):
        ui.label(col["name"]).classes("column-name").tooltip(col["name"])
        with ui.element("div"):
            c.chip(str(col.get("inferred", "")))
        role = (
            ui.input(value=col.get("role") or "")
            .classes("p-field p-field-mono w-full")
            .props("borderless dense")
        )
        c.mono(_null_rate(col), small=True)
        c.mono(str(col.get("n_distinct", "—")), small=True)
        with ui.element("div").classes("row-gap-xs"):
            for flag in col.get("flags") or []:
                c.flag_badge(flag)
    return role


def _start_editing(name: str) -> None:
    APP.editing, APP.asking = name, None
    pane.refresh()


def _stop_editing() -> None:
    APP.editing = None
    pane.refresh()


def _start_asking(name: str) -> None:
    APP.asking, APP.editing = name, None
    pane.refresh()


def _stop_asking() -> None:
    APP.asking = None
    pane.refresh()


def _save_interpretation(name: str, summary: str, roles: dict) -> None:
    from portia.ui import artifacts

    engine.set_interpretation(
        name,
        summary=summary.strip() or None,
        roles={col: field.value.strip() for col, field in roles.items() if field.value.strip()},
        app=APP,
    )
    APP.editing = None
    pane.refresh()
    artifacts.pane.refresh()
    ui.notify(f"saved · {name}")


async def _ask_copilot(name: str, note: str) -> None:
    from portia.agent import prompts
    from portia.ui import turn

    if not (note or "").strip() or APP.busy:
        return
    APP.asking = None
    pane.refresh()
    await turn.start(
        prompts.task("reinterpret", source=name, note=note.strip()),
        model=APP.model or _default_model(),
        effort=APP.effort,
        kind=state.REREAD,
        label=name,
    )


def _default_model() -> str:
    from portia.agent.session import DEFAULT_MODEL

    return DEFAULT_MODEL


def _spend() -> str:
    effort = f" · effort {APP.effort}" if APP.effort else ""
    return f"costs a turn on {APP.model or _default_model()}{effort}"


async def _source_frame(entry: dict):
    """The source's rows, or None if the file has moved since it was indexed."""
    path = APP.root / str(entry.get("source", ""))
    if not path.exists():
        return None
    try:
        return await engine.read_frame(path)
    except Exception:  # noqa: BLE001 — a missing preview must not blank the pane
        return None


#: The per-column facts, each as (icon, heading). The **heading names the fact in
#: words, once, at the top of the list**, and the icon repeats down the rows as
#: the thing your eye tracks. Icons alone would be a legend nobody was given;
#: words on every row would be the wall of labels this replaced.
COLUMN_HEADINGS = (
    ("table_rows", "column"),
    ("data_object", "type"),
    ("label", "role"),
    ("opacity", "null"),
    ("fingerprint", "distinct"),
    ("flag", "flags"),
)


def _columns(columns: list[dict]) -> None:
    """A real table: headings once, values aligned under them.

    A source with thirty columns is the normal case, and a labelled line per fact
    made three of them a screenful. Every fact the cards showed is still here.
    """
    with ui.element("div").classes("column-list"):
        _column_headings()
        for col in columns:
            _column_row(col)


def _column_headings() -> None:
    with ui.element("div").classes("column-row column-head"):
        for icon, heading in COLUMN_HEADINGS:
            with ui.element("div").classes("column-heading"):
                ui.icon(icon).classes("fact-icon")
                ui.label(heading)


def _column_row(col: dict) -> None:
    with ui.element("div").classes("column-row"):
        ui.label(col["name"]).classes("column-name").tooltip(col["name"])
        with ui.element("div"):
            c.chip(str(col.get("inferred", "")))
        c.mono(col.get("role") or "—", small=True)
        c.mono(_null_rate(col), small=True)
        c.mono(str(col.get("n_distinct", "—")), small=True)
        with ui.element("div").classes("row-gap-xs"):
            for flag in col.get("flags") or []:
                c.flag_badge(flag)


def _null_rate(col: dict) -> str:
    """Formatted exactly as `catalog.render_source` formats it for the terminal.

    Same number, same rounding, both edges — the day the two disagree about a
    rate is the day someone has to work out which one to believe.
    """
    return format_rate(col.get("null_rate"))


async def _run_inspector(name: str) -> None:
    """A saved run report, as it was written to disk.

    Rendered from the file rather than re-derived from state: what this shows has
    to be exactly what a reviewer sees in the diff, or saving it was pointless.
    """
    path = APP.root / engine.RUNS_DIR / name
    with ui.element("div").classes("p-scroll p-pad stack-lg"):
        _inspector_header(name, str(path))
        if not path.exists():
            c.empty_note("that report is gone")
            return
        c.markdown(await engine.read_text(path))


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
_EDIT_SCOPE = "writes the prose and the roles; the measured facts are untouched"
_ASK_HEADING = "What did it miss?"
_ASK_WHY = "It re-reads this source with your note in hand, and asks if the two disagree."
_ASK_PLACEHOLDER = "e.g. this id is a legacy code, not a customer reference…"
_REMOVE_SCOPE = (
    "Drops its catalog entry, its roles and its summary. The data file stays where it is."
)
_REMOVE_UNDO = (
    "Add it again from the same path to re-index it — the facts come back, the prose doesn't."
)

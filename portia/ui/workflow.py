"""Middle pane — the project as a graph over the open spec's run report.

The top half draws what the YAML already encodes, at two zoom levels on one
canvas. Across the project a card is a **table** — one spec, one table — and an
edge is one model reading another; open a card and the cards inside it are the
**steps** that build that table. That is `VISION.md`'s oldest open question,
*are cards steps or tables?*, answered as **both, at different levels**.

A third kind of card is the point of the distinction: a `SOURCE` is a file that
arrived, a `MODEL` is a table portia built. They drew identically until the
pipeline overhaul, which left the graph unable to say which of its inputs it was
responsible for.

Clicking a step shows it verbatim, and **an acknowledged blocking flag is
impossible to miss there** — Run 5 buried one mid-dict in a terminal confirmation
and shipped a 3.85%-inflated table (docs/EVALUATION.md). This screen is the
second chance at that.

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

from portia import catalog
from portia.checks.outcome import BLOCKING_FLAGS, describe_contribution, describe_grain
from portia.core.present import format_rate
from portia.ui import components as c
from portia.ui import engine, graph, state
from portia.ui.state import APP, BRIEF, MODEL, OUTPUT, RUN, SOURCE, SPEC, TURN, UNINDEXED

#: How tall the graph half sits by default, as a percentage. The report half is
#: the taller of the two — it is where the evidence is (DESIGN.md → Layout).
GRAPH_SPLIT = 44


@ui.refreshable
def pane() -> None:
    """What the middle pane is showing, drawn in one pass.

    **Synchronous, deliberately.** A refresh deletes this pane's elements and
    only then runs the function; with an `await` in between, the delete and the
    rebuild go out in two batches and the browser paints the gap — a blank middle
    pane, intermittently, on every click. Drawing in one pass puts both in one
    batch, so a click swaps the content rather than blinking it.

    Nothing is given up by that. The reads here are a `.sql`, a markdown report,
    a turn's JSONL — local files a few kilobytes long — and the heaviest thing on
    screen, the row count behind a table preview, was already being executed
    synchronously inside `c.table_preview`. The work that genuinely blocks —
    profiling a source, executing a spec — still goes to a thread in
    `engine.py`; it just isn't happening in here.
    """
    kind, name = APP.selection or (None, "")
    if kind == SOURCE:
        _source_inspector(name)
    elif kind == UNINDEXED:
        _unindexed_inspector(name)
    elif kind == MODEL:
        _model_inspector(name)
    elif kind == OUTPUT:
        _output_inspector(name)
    elif kind == RUN:
        _run_inspector(name)
    elif kind == TURN:
        _turn_inspector(name)
    elif kind == BRIEF:
        _brief_inspector()
    else:
        _workflow()


def _inspector_scroll() -> ui.element:
    """The inspector's scroll region, keyed to the artifact it is showing.

    One key per artifact rather than one for "the inspector": two saved runs are
    two things to keep a place in, and a shared key would drop you into the
    second at the first one's offset (`c.scroll_area`).
    """
    kind, name = APP.selection or ("", "")
    return c.scroll_area(f"{kind}:{name}", classes="p-pad stack-lg")


# --- the workflow -----------------------------------------------------------


def _workflow() -> None:
    with ui.splitter(horizontal=True, value=GRAPH_SPLIT).classes("w-full h-full p-splitter") as sp:
        with sp.before:
            _graph_half()
        with sp.after:
            _report_half()


def _graph_half() -> None:
    """The project as a DAG of tables, with the open spec's card opened onto its steps.

    One canvas, two zoom levels: a card here is a **table** (one spec, one table),
    and a card inside an opened one is a **step**. `VISION.md`'s oldest open
    question — are cards steps or tables? — is answered as *both, at different
    levels*, and this is where you can see both at once.
    """
    docs = engine.project_docs(APP)
    placed = graph.project_layout(docs, expanded=APP.expanded, badges=_badge_counts(docs))
    with ui.element("div").classes("p-pane"):
        _graph_header(docs)
        with ui.element("div").classes("graph-canvas"):
            if placed.empty:
                c.empty_note(_NO_SPECS)
            else:
                _graph(placed)
        _step_detail()


def _graph_header(docs: dict) -> None:
    with ui.element("div").classes("row-gap-sm px-4 pt-3"):
        ui.label("Pipeline").classes("t-heading-sm")
        c.caption(c.count(len(docs), "model"))
        stale = engine.stale_models(APP)
        if stale:
            # A `.sql` that no longer matches its spec is a fact about the
            # deliverable, so it belongs where the deliverable is drawn — not only
            # in `build --check`, where you find it after the fact.
            c.flag_badge(f"{c.count(len(stale), 'model')} stale", c.DRIFT).tooltip(
                f"{', '.join(stale)} — the .sql no longer matches the spec. Build to regenerate."
            )
        ui.element("div").classes("flex-1")
        _view_controls()


def _view_controls() -> None:
    """Zoom, and the way back from having moved.

    The gestures come first — drag to move, two fingers up and down to zoom — and
    these are for the times a gesture isn't available or isn't precise enough. The readout between
    them is written by `canvas.js`, because where the canvas is panned and zoomed
    to is the one piece of state in this app the client owns; a round trip per
    wheel tick would make the only directly-manipulated surface the laggiest.

    The canvas pans and zooms with no bound, which is what makes it a surface
    rather than a picture — and is exactly why Recenter has to exist. It undoes
    both at once, as does double-clicking the canvas.
    """
    with ui.element("div").classes("row-gap-xs"):
        c.button("", _zoom_out, icon="remove", micro=True).tooltip(_ZOOM_OUT_TIP)
        ui.label("100%").classes("zoom-level")
        c.button("", _zoom_in, icon="add", micro=True).tooltip(_ZOOM_IN_TIP)
    c.button("Recenter", _recenter, icon="filter_center_focus", micro=True).tooltip(_RECENTER_TIP)


def _zoom_in() -> None:
    ui.run_javascript("portiaZoomIn()")


def _zoom_out() -> None:
    ui.run_javascript("portiaZoomOut()")


def _recenter() -> None:
    ui.run_javascript("portiaRecenter()")


def _badge_counts(docs: dict) -> dict[str, int]:
    """How many badges each step card will carry, so every card can fit the most.

    Counting what is about to be drawn, not measuring anything: the flags
    themselves come from `StepResult`, and before a run from what the spec
    acknowledges. Cards stay uniform — this only decides what that one size is.
    """
    counts: dict[str, int] = {}
    for doc in docs.values():
        for step in doc.get("steps") or []:
            step_id = step.get("id")
            if not step_id:
                continue
            result = _result(step_id)
            if result is None:
                counts[step_id] = len(step.get("acknowledge") or [])
            else:
                counts[step_id] = (
                    len(result.blocking) + len(result.acknowledged) + len(result.drift)
                )
    return counts


def _graph(placed: graph.Layout) -> None:
    """Draw the laid-out graph, marking the card the canvas should move to.

    The mark is **declarative on purpose**, and it took two goes to get there.
    Picking a spec navigates the canvas rather than replacing it; the first
    version did the moving with a `run_javascript` from inside this render, which
    raced the DOM patch and reliably landed on the canvas that was about to be
    discarded — so the pan never moved at all. The second marked the node and
    cleared the request as the render consumed it, which failed for a quieter
    reason: this pane renders more than once per click, so the first render ate
    the request and the render that reached the screen had nothing to mark.

    So the request carries a token and the client acts on each one once. A
    repeated render is then simply harmless, rather than something the server has
    to get right.
    """
    style = f"width:{placed.width}px;height:{placed.height}px"
    with ui.element("div").classes("graph-content").style(style):
        ui.html(_edges_svg(placed))
        for node in placed.nodes:
            _node(node, focused=node.id == APP.focus_model)


def _edges_svg(placed: graph.Layout, *, inner: bool = False) -> str:
    """One overlay for every edge. 1px hairline-strong, small arrowheads, no labels."""
    parts = [
        f'<path d="{edge.path()}"/><polygon points="{edge.arrowhead()}"/>' for edge in placed.edges
    ]
    cls = "graph-edges graph-edges--inner" if inner else "graph-edges"
    return (
        f'<svg class="{cls}" width="{placed.width}" height="{placed.height}">{"".join(parts)}</svg>'
    )


def _node(node: graph.Node, *, focused: bool = False) -> None:
    style = f"left:{node.x}px;top:{node.y}px;width:{node.w}px;height:{node.h}px"
    classes = "graph-node" + (" graph-node--focus" if focused else "")
    box = ui.element("div").classes(classes).style(style)
    if focused:
        box.props(f"data-focus-token={APP.focus_token}")
    with box:
        if node.kind == graph.SOURCE:
            _source_node(node)
        elif node.kind == graph.MODEL:
            _model_card(node)
        else:
            _step_card(node)


def _source_node(node: graph.Node) -> None:
    """A file. Deliberately the quietest thing on the canvas — it is what arrived,
    not what was decided."""
    label = ui.label(node.id).classes("source-node")
    label.on("click", lambda n=node.id: _select_source(n))


def _model_card(node: graph.Node) -> None:
    """Another spec's table — a thing portia built, and can open.

    Distinct from a source on purpose. Until now every input that was not a step
    drew as the same grey box, so a table with its own spec, steps and rationale
    was indistinguishable from a CSV somebody dropped in the folder.

    The layer is shown as its **name**, with no colour and no size of its own.
    staging/intermediate/mart is build order, not a quality ladder, and it is the
    one field on this card that nothing measured — so it may say what kind of
    table this is and must never make one card louder than another.
    """
    selected = APP.spec_path is not None and APP.spec_path.stem == node.id
    classes = "model-card"
    if node.expanded:
        classes += " model-card--open"
    if selected:
        classes += " model-card--selected"

    with ui.element("div").classes(classes):
        with ui.element("div").classes("model-head") as head:
            ui.icon("expand_more" if node.expanded else "chevron_right").classes("model-caret")
            ui.label(node.id).classes("model-name").tooltip(node.id)
            ui.element("div").classes("flex-1")
            if node.layer:
                c.chip(node.layer)
            c.caption(c.count(node.steps, "step"))
        head.on("click", lambda n=node.id: _open_model(n))
        if node.inner is not None:
            _model_body(node.inner)


def _model_body(inner: graph.Layout) -> None:
    """The steps that build this table, on the same canvas one level in."""
    style = f"width:{inner.width}px;height:{inner.height}px"
    with ui.element("div").classes("model-body").style(style):
        ui.html(_edges_svg(inner, inner=True))
        for node in inner.nodes:
            _node(node)


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
    """The selected step, verbatim. Its acknowledgement sits above everything.

    Below the canvas rather than on it: it describes the selection, so panning
    the graph should not carry it off screen.
    """
    step = _step(APP.selected_step)
    if step is None:
        return
    with ui.element("div").classes("graph-detail stack-md"):
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
        # Keyed to the spec it reports on: selecting a step rebuilds this to move
        # one highlight, and reading a twelve-step report should not mean being
        # returned to step one every time you click a card (`c.scroll_area`).
        with c.scroll_area(f"report:{spec_label(APP.spec_path)}", classes="p-pad stack-md"):
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
    """What the run did — including the models below this one that it had to build.

    Run executes the open spec's upstreams too, so a header that named only this
    spec would understate what just happened. The upstream names are stated, never
    summarised into "and 2 others": which tables were rebuilt is the kind of thing
    you need to be able to check rather than trust.
    """
    results = APP.results or []
    blocking = sorted({flag for r in results for flag in r.blocking})
    with ui.element("div").classes("row-gap-sm"):
        ui.label(c.count(len(results), "step")).classes("t-heading-sm")
        for flag in blocking:
            c.flag_badge(flag, c.BLOCKING)
        if not blocking:
            c.caption("no blocking flag")
    upstream = [
        m.name for m in APP.built if m.name != (APP.spec_path.stem if APP.spec_path else "")
    ]
    if upstream:
        c.caption(f"also built · {' · '.join(upstream)}")
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
        if result.table is not None:
            _table(result.table)
    block.on("click", lambda i=result.id: _select_step(i))


def _group(label: str, body) -> None:
    with ui.element("div").classes("report-group"):
        ui.label(label).classes("report-group-label")
        body()


def _table(table) -> None:
    """The produced table, one click away.

    Inline it pushed everything below it off the screen, and the point of the
    report is that the four groups can be read at a glance. The label carries
    the shape, so the table is never a surprise you have to open to size up.
    """
    total, head = c.table_shape(table)
    label = f"preview · {c.count(total, 'row')} × {c.count(head.shape[1], 'column')}"
    c.collapsed(label, lambda: c.table_preview(table))


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


def _source_inspector(name: str) -> None:
    """A source's catalog entry — the prose read, the roles, the check facts, the rows.

    The catalog is what the *copilot* sees, and it never sees the rows. A person
    reading the same screen usually wants to, so the data is here too — the one
    place in the app where the difference between the two views is deliberate.
    """
    entry = APP.sources.get(name)
    frame = _source_table(entry) if entry else None
    editing = APP.editing == name
    with _inspector_scroll():
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
            _group("summary", lambda: _summary(entry))
            _group("columns", lambda: _columns(name, columns))
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


def _source_table(entry: dict):
    """The source's rows, or None if the file has moved since it was indexed."""
    path = APP.root / str(entry.get("source", ""))
    if not path.exists():
        return None
    try:
        return engine.read_table(path)
    except Exception:  # noqa: BLE001 — a missing preview must not blank the pane
        return None


#: The per-column facts, each as (icon, heading). The **heading names the fact in
#: words, once, at the top of the list**, and the icon repeats down the rows as
#: the thing your eye tracks. Icons alone would be a legend nobody was given;
#: words on every row would be the wall of labels this replaced.
#: How many column rows the source inspector draws before folding the rest away.
#: Enough that a narrow source is never folded at all, and that a wide one still
#: shows what its first columns look like before you decide to open it.
COLUMNS_FOLDED = 8

COLUMN_HEADINGS = (
    ("table_rows", "column"),
    ("data_object", "type"),
    ("label", "role"),
    ("opacity", "null"),
    ("fingerprint", "distinct"),
    ("flag", "flags"),
)


def _summary(entry: dict) -> None:
    """The prose read — or, when nobody has written one, the fact that nobody has.

    `catalog._auto_summary` drafts a restatement of the profile ("47 rows, 12
    columns. Watch-outs: …") so the YAML is never empty, and this pane used to
    print it in the summary's place. Read on screen it is indistinguishable from
    a read of the data: it is prose, in the prose slot, saying true things — and
    what it is *actually* saying is that no one has looked yet. That is the one
    thing the operator needs to know here, so it is said in words. The facts it
    restated are all in the columns table below, measured, where they belong.
    """
    if catalog.is_interpreted(entry):
        c.text(entry.get("summary", ""))
        return
    with ui.element("div").classes("not-read"):
        ui.icon("pending").classes("not-read-icon")
        with ui.element("div"):
            ui.label(_NOT_READ).classes("t-body c-ink")
            c.caption(_NOT_READ_WHY)


def _columns(name: str, columns: list[dict]) -> None:
    """A real table: headings once, values aligned under them.

    A source with thirty columns is the normal case, and a labelled line per fact
    made three of them a screenful. Every fact the cards showed is still here.

    **Folded to the first few, because this is not the only thing on the pane.**
    The rows, the actions and the preview all sit below it, and a wide extract's
    column list pushed every one of them off the screen. Unfolding is one click
    and the count is on the button, so nothing is hidden without saying so.
    """
    shown = _shown_columns(name, columns)
    with ui.element("div").classes("column-list"):
        _column_headings()
        for col in shown:
            _column_row(col)
    if len(columns) > COLUMNS_FOLDED:
        _columns_toggle(name, len(columns), len(shown))


def _shown_columns(name: str, columns: list[dict]) -> list[dict]:
    """The first few, unless this source is the one that was unfolded."""
    return columns if APP.columns_open == name else columns[:COLUMNS_FOLDED]


def _columns_toggle(name: str, total: int, shown: int) -> None:
    open_now = shown >= total
    label = _COLUMNS_FEWER.format(n=COLUMNS_FOLDED) if open_now else _COLUMNS_ALL.format(n=total)
    with ui.element("div").classes("column-more"):
        c.button(
            label,
            lambda: _toggle_columns(name),
            kind="secondary",
            micro=True,
            icon="expand_less" if open_now else "expand_more",
        )
        if not open_now:
            c.caption(_COLUMNS_HIDDEN.format(n=total - shown))


def _toggle_columns(name: str) -> None:
    APP.columns_open = None if APP.columns_open == name else name
    pane.refresh()


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


def _run_inspector(name: str) -> None:
    """A saved run report, as it was written to disk.

    Rendered from the file rather than re-derived from state: what this shows has
    to be exactly what a reviewer sees in the diff, or saving it was pointless.
    """
    path = APP.root / engine.RUNS_DIR / name
    with _inspector_scroll():
        _inspector_header(name, str(path))
        if not path.exists():
            c.empty_note("that report is gone")
            return
        c.markdown(engine.read_text(path))


def _turn_inspector(name: str) -> None:
    """A logged copilot turn, replayed (`portia/runlog.py`).

    Here rather than in the right pane on purpose. The right pane is the *live*
    copilot, and reading a past turn should not cost you the turn you are in the
    middle of; this is also the widest pane, which is what a transcript wants.

    The counts above it are the engine's — `engine.turn_summary`, the same
    function `cli.runs` prints — because the day the window and the terminal
    disagree about how many times the copilot asked, someone has to work out
    which to believe.
    """
    from portia.ui import transcript

    path = engine.turn_path(APP, name)
    with _inspector_scroll():
        _inspector_header(name, "a copilot turn, as it happened")
        if not path.exists():
            c.empty_note("that turn is gone")
            return
        run = engine.read_turn(path)
        _turn_facts(engine.turn_summary(run))
        c.rule()
        transcript.replay(run)


def _turn_facts(summary: dict) -> None:
    """What the turn was and what it did — counts, in uniform badges.

    Every one of these is a cost-and-behaviour descriptor and none of them is a
    verdict: "asked three times" is neither good nor bad without knowing whether
    it should have (docs/EVALUATION.md). So they are the same size, in a fixed
    order, with no colour to say one matters more — `DESIGN.md`'s rule that
    prominence communicates kind and never rank.
    """
    with ui.element("div").classes("stack-sm"):
        with ui.element("div").classes("row-gap-sm"):
            c.mono(str(summary.get("model") or "?"), color="c-ink")
            if summary.get("effort"):
                c.caption(f"effort {summary['effort']}")
            c.caption(str(summary.get("started") or ""))
            if summary.get("portia_sha"):
                c.caption(f"portia {summary['portia_sha']}")
        ui.label(str(summary.get("prompt") or "")).classes("t-body c-body pre-wrap")
        with ui.element("div").classes("row-gap-sm"):
            c.fact("build", summary.get("tools"), "tool calls")
            c.fact("help_outline", summary.get("questions"), "questions asked")
            c.fact("edit_note", summary.get("approved"), "writes allowed")
            c.fact("block", summary.get("refused"), "writes refused")
            c.fact("swap_vert", _tokens(summary), "tokens in / out")
            c.fact("payments", _turn_cost(summary), "estimated cost")
        if summary.get("sequence"):
            c.collapsed(
                "tools, in the order they were called",
                lambda: c.mono(" → ".join(summary["sequence"]), color="c-mute", small=True),
            )


def _tokens(summary: dict) -> str:
    """Whole input, not the SDK's uncached field — see `runlog._tokens`."""
    sent, got = summary.get("input_tokens"), summary.get("output_tokens")
    return "—" if sent is None or got is None else f"{sent:,} / {got:,}"


def _turn_cost(summary: dict) -> str:
    cost = summary.get("cost_usd")
    return "—" if not cost else f"~${cost:.4f}"


def _model_inspector(rel: str) -> None:
    """A compiled model, as it sits on disk — the deliverable, read verbatim.

    Rendered from the file rather than recompiled from the spec, for the same
    reason `_run_inspector` reads its markdown off disk: what this shows has to be
    exactly what a reviewer sees in the diff, or committing it was pointless. That
    is also what makes the staleness banner meaningful — it is the difference
    between this file and what the spec would produce now.
    """
    path = APP.root / rel
    with _inspector_scroll():
        _inspector_header(path.name, str(path))
        if not path.exists():
            c.empty_note("that model is gone — press Build to write it again")
            return
        if path.stem in engine.stale_models(APP):
            _stale_banner(path.stem)
        c.code_block(engine.read_text(path))


def _stale_banner(name: str) -> None:
    """The `.sql` no longer matches its spec. A fact, stated where it matters.

    Drift-coloured rather than blocking: nothing is broken, the file is simply
    describing an older version of the decision record. `build --check` is the
    same fact in CI; this is it in the window, which is the point — you should not
    have to run a terminal command to find out the deliverable is out of date.
    """
    with ui.element("div").classes("stale-banner"):
        with ui.element("div").classes("row-gap-sm"):
            c.flag_badge("stale", c.DRIFT)
            ui.label(f"{name}.sql no longer matches {name}.yaml").classes("t-body-strong c-ink")
        c.text(_STALE_WHY, color="c-body")


def _unindexed_inspector(rel: str) -> None:
    """A file the tree can see and the catalog has never read.

    The tree shows every file in a format `core.io` registers a reader for, not
    only the ones in the catalog — otherwise a CSV sitting in the repo is
    invisible until you go looking for it through a dialog. Showing it means
    answering the obvious next question here rather than sending you elsewhere,
    so this is the profiling half of indexing and nothing else: deterministic,
    free, and no model turn. What each source *is* stays the copilot's job.
    """
    path = APP.root / rel
    with _inspector_scroll():
        _inspector_header(path.name, rel)
        if not path.exists():
            c.empty_note("that file is gone")
            return
        c.text(_UNINDEXED_WHY, color="c-body")
        c.button("Index it", partial(_index, path), kind="primary", icon=c.INDEX_ICON)
        c.caption(_INDEX_SCOPE)
        c.rule()
        c.table_preview(engine.read_table(path))


async def _index(path: Path) -> None:
    from portia.ui import artifacts

    await engine.index([path], APP)
    APP.select(SOURCE, path.stem)
    artifacts.pane.refresh()
    pane.refresh()
    ui.notify(f"profiled {path.stem}")


def _brief_inspector() -> None:
    """The project brief, edited where the rest of the project is read.

    **The most consequential text box in the product** — the context is what makes
    a column's meaning decidable, and a generic brief yields generic judgment
    (`PLAN.md`). It was a dialog behind a toolbar button, which is a lot of chrome
    for something you should be able to sit and rewrite with the sources on
    screen beside it. It is a pane now, opened from the row at the top of the
    tree, and it writes through `catalog.init_project` — the same call the gate in
    `screens.project_context` makes, so there is one way the brief gets written.
    """
    from portia.ui import screens

    with _inspector_scroll():
        _inspector_header("Project brief", str(APP.catalog_dir / "project.yaml"))
        c.text(screens.CONTEXT_WHY, color="c-mute")
        box = (
            ui.textarea(placeholder=screens.CONTEXT_PLACEHOLDER, value=APP.project_context)
            .classes("p-field p-editor w-full")
            .props("borderless")
            .style("min-height:220px")
        )
        screens.context_guidance()
        with ui.element("div").classes("row-gap-sm"):
            c.button("Save", lambda: _save_brief(box.value), kind="primary")
            c.button("Cancel", _back, kind="secondary")


def _save_brief(text: str) -> None:
    from portia.ui import artifacts

    if not (text or "").strip():
        ui.notify("the brief cannot be empty")
        return
    engine.set_context(text, APP)
    artifacts.pane.refresh()
    pane.refresh()
    ui.notify("brief saved")


def _output_inspector(name: str) -> None:
    path = APP.root / engine.OUT_DIR / name
    with _inspector_scroll():
        _inspector_header(name, "a table a run wrote")
        if not path.exists():
            c.empty_note("that file is gone")
            return
        c.table_preview(engine.read_table(path))


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


def _open_model(name: str) -> None:
    """Open a table's card, and make its spec the one the report half is about.

    Clicking the card of the spec that is already open closes it again, so the
    same gesture opens and collapses. Clicking a different one always opens it —
    you asked to look inside that table, and having to click twice because the
    last click was on something else is the kind of state the canvas should hide.
    """
    from portia.ui import app as app_module
    from portia.ui import artifacts

    already_open = APP.spec_path is not None and APP.spec_path.stem == name
    if already_open and name in APP.expanded:
        APP.expanded = APP.expanded - {name}
    else:
        APP.expanded = APP.expanded | {name}

    path = engine.spec_path_for(APP, name)
    if path is not None and not already_open:
        engine.select_spec(path, APP)
        APP.select(SPEC, path.name)
    pane.refresh()
    artifacts.pane.refresh()
    app_module.run_controls.refresh()


def _select_source(name: str) -> None:
    """A file node opens its catalog entry, exactly as the left panel does."""
    from portia.ui import artifacts

    APP.select(SOURCE, name)
    pane.refresh()
    artifacts.pane.refresh()


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


_NO_SPECS = (
    "No specs yet. The copilot writes one as it records steps, and each one becomes a table."
)
_NO_RUN = "No run yet. Press Run in the toolbar to execute this spec."
_ZOOM_IN_TIP = "Zoom in · or two fingers up on the canvas"
_ZOOM_OUT_TIP = "Zoom out · or two fingers down on the canvas"
_RECENTER_TIP = "Back to 100%, centred · or double-click the canvas. Drag to move around."
_STALE_WHY = (
    "The spec has changed since this file was generated. Run the spec, or Build the "
    "project, to regenerate it — the .sql is a build output and is never hand-edited."
)
_UNINDEXED_WHY = (
    "portia can read this file but has never profiled it, so nothing in the project "
    "knows what is in it. Indexing measures it and writes a catalog entry."
)
_INDEX_SCOPE = "profiling only — deterministic and free. The copilot reads it on its next turn."
_NOT_READ = "The copilot has not read this source yet."
_NOT_READ_WHY = (
    "It was profiled, so the facts below are measured and real — but no one has written what "
    "this data means. Ask the copilot, or write the read yourself."
)
_COLUMNS_ALL = "Show all {n} columns"
_COLUMNS_FEWER = "Show the first {n}"
_COLUMNS_HIDDEN = "{n} more"
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

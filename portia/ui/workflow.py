"""Middle pane — the project as a flow chart of tables over the open spec's run report.

The top half draws what the YAML already encodes. **A card is one table**, with
no exception: a `SOURCE` is a file that arrived, a `MODEL` is a table portia
built, and an edge is one table reading another. `VISION.md`'s oldest open
question — *are cards steps or tables?* — used to be answered *both, at different
zoom levels*, with a spec's steps drawn as a second flow chart inside its card;
**that was reversed on 2026-08-15** and `graph.py` holds the argument. The short
of it: a step compiles to a named block inside one SQL file and never becomes a
table, so it is a row in an ordered list, not a card.

Open a card and it answers the two questions a card raises — **what the arrows
arriving at it mean** (each input, unfoldable onto the columns that came along
it) and **how it is built** (its steps, in order). Both are inside the card
because both are about that one table.

Clicking a step opens its block below, and **an acknowledged blocking flag is
impossible to miss** — Run 5 buried one mid-dict in a terminal confirmation
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

import asyncio
from functools import partial
from pathlib import Path
from typing import Any

from nicegui import ui

from portia import catalog
from portia.checks.outcome import BLOCKING_FLAGS, describe_contribution, describe_grain
from portia.core import feedback as core_feedback
from portia.core.present import format_rate
from portia.core.serialize import to_json
from portia.ui import charts, engine, graph, state
from portia.ui import components as c
from portia.ui.state import (
    APP,
    BRIEF,
    BUILT,
    CANVAS,
    FIGURES,
    KNOWLEDGE,
    MODEL,
    OUTPUT,
    RUN,
    SOURCE,
    SPEC,
    UNINDEXED,
)

#: How tall the graph half sits by default, as a percentage. The report half is
#: the taller of the two — it is where the evidence is (DESIGN.md → Layout).
GRAPH_SPLIT = 44

#: Where the tab split opens, as a percentage of the middle pane's width
#: (`docs/VISUALIZATION.md` §3.7). **Down the middle**, because neither half is
#: the more important one: you split the pane to read two things at once, and
#: a default that favoured one of them would be the window deciding which
#: (`DESIGN.md` — kind, never rank). The bar drags, so the case where one half
#: does want more room is one gesture away.
TAB_SPLIT = 50


@ui.refreshable
def pane() -> None:
    """What the middle pane is showing, drawn in one pass.

    **Synchronous, deliberately.** A refresh deletes this pane's elements and
    only then runs the function; with an `await` in between, the delete and the
    rebuild go out in two batches and the browser paints the gap — a blank middle
    pane, intermittently, on every click. Drawing in one pass puts both in one
    batch, so a click swaps the content rather than blinking it.

    Nothing is given up by that. The reads here are a `.sql`, a markdown report,
    a chat's JSONL — local files a few kilobytes long — and the heaviest thing on
    screen, the row count behind a table preview, was already being executed
    synchronously inside `c.table_preview`. The work that genuinely blocks —
    profiling a source, executing a spec — still goes to a thread in
    `engine.py`; it just isn't happening in here.
    """
    if not APP.is_split:
        # Not split means the left group holds everything: `state._after_close`
        # collapses an empty half rather than leaving a gap, so there is never a
        # right group beside nothing.
        _half(state.LEFT)
        return
    # **Two groups at once** (§3.7). A vertical splitter, the same control the
    # three panes are made of, so there is one idea of *a draggable divider* in
    # this window rather than two — and its position is the client's, like every
    # other splitter here: nothing measures it and nothing persists it.
    with ui.splitter(value=TAB_SPLIT).classes("w-full h-full p-splitter") as sp:
        with sp.before:
            _half(state.LEFT)
        with sp.after:
            _half(state.RIGHT)


def _half(group: str) -> None:
    """One half of the middle pane: its own strip, and whatever it is showing.

    The strip belongs to the half rather than to the pane *(2026-09-03)*. It was
    one strip across the top with a single tab pinned beside it, which is one
    drag short of what an editor does — you could put a second chart next to a
    first and then not keep both.
    """
    keys = APP.keys(group)
    showing = APP.active.get(group, "")
    if showing not in keys:
        # A group always draws something it actually holds. `state` keeps this in
        # step; the fallback is here because a half drawing a tab that is not on
        # its own strip is the bug that would be hardest to see.
        showing = keys[0] if keys else CANVAS
    with ui.element("div").classes(f"split-half split-half--{group}"):
        charts.strip(group)
        _tab(showing, group)


def _tab(key: str, group: str = "") -> None:
    """One tab's contents — a chart, a figure, or whatever tab zero is about.

    Tab zero is the selection dispatch this pane has always been, which is phase
    1's deliberate half-measure: charts and figures are tabs, everything else is
    still one selection drawn in the first one (§3.1).

    A chart tab is **not** a selection. The left pane and the tab strip are two
    ways of saying what is on screen, and picking a chart must not silently
    unpick the spec you were reading, or closing the tab would drop you somewhere
    you never chose (§3.2 — the canvas is where you land).
    """
    chart = APP.chart(key)
    if chart is not None:
        charts.pane(chart, group)
        return
    kind, name = APP.selection or (None, "")
    if kind == SOURCE:
        _source_inspector(name)
    elif kind == UNINDEXED:
        _unindexed_inspector(name)
    elif kind == MODEL:
        _model_inspector(name)
    elif kind == BUILT:
        _built_inspector(name)
    elif kind == OUTPUT:
        _output_inspector(name)
    elif kind == RUN:
        _run_inspector(name)
    elif kind == BRIEF:
        _brief_inspector()
    elif kind == KNOWLEDGE:
        _knowledge_inspector()
    elif kind == FIGURES:
        charts.gallery_pane()
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
    """The canvas, its report, and the actions that act on them.

    **The run actions are drawn here, by the canvas itself.** They used to be a
    sibling of this pane, drawn by `app._middle` on every screen — so selecting a
    file or a saved chat left Run and Save hanging over an inspector they have
    nothing to do with. Guarding them on "is the canvas up?" was the first fix and
    it was the wrong shape: the answer lived in one place and the nine handlers
    that change the selection each had to remember to refresh a bar they do not
    otherwise touch, and one of them already didn't. Drawing them where the canvas
    is drawn makes it structural — there is no state to keep in step, because the
    bar cannot outlive the thing it acts on.
    """
    from portia.ui import app as app_module

    app_module.run_controls()
    if not APP.report_open:
        # Shut, the canvas takes the whole pane and the report is one rail at the
        # foot of it — the same shape a closed side pane leaves behind, so there
        # is one idea of *closed* in this window rather than two.
        _graph_half()
        _report_rail()
        return
    with ui.splitter(horizontal=True, value=GRAPH_SPLIT).classes("w-full h-full p-splitter") as sp:
        with sp.before:
            _graph_half()
        with sp.after:
            _report_half()


@ui.refreshable
def _report_rail() -> None:
    """The way back to a closed report: what it is about, and a control to open it.

    It names the spec rather than saying "report", because the thing you shut is
    *this table's* decision record and the canvas beside it has several tables on
    it. A bare chevron would make reopening a guess about which one you would get.
    """
    with ui.element("div").classes("p-rail-bottom") as rail:
        ui.icon("expand_less").classes("model-caret")
        c.caption(_REPORT_RAIL.format(spec=spec_label(APP.spec_path)))
    rail.on("click", _open_report)
    rail.tooltip(_REPORT_OPEN_TIP)


def _graph_half() -> None:
    """The project as a flow chart of tables. **Every card is one table.**

    *(2026-08-15 — see `graph.py` for the argument.)* A card used to be a table at
    the project level and a step one level in, both drawn at once. Steps are an
    ordered list inside the card now, because a step is a named block inside one
    SQL file and never a table of its own — so "one card, one table" is a rule
    with no exception rather than a zoom level.

    An open card answers the two questions a card raises: **what do the arrows
    arriving here mean** (its inputs, and the columns that came along each), and
    **how was it built** (its steps, in order). Both nested in the card, because
    both are about that table and a panel elsewhere on screen is a second place to
    look for one thing.
    """
    docs, placed, reads = _layout()
    hops = _highlit(docs)
    with ui.element("div").classes("p-pane"):
        _graph_header(docs, placed)
        with ui.element("div").classes("graph-canvas"):
            _canvas(placed, docs, reads, hops)


@ui.refreshable
def _canvas(placed: graph.Layout, docs: dict, reads: dict, hops: dict[str, int]) -> None:
    """What the canvas draws, inside the `.graph-canvas` that keeps the pan and zoom.

    Its own refreshable for the table filter, whose ticks change what is drawn
    and nothing else on the pane (`_set_visible_in_place`).
    """
    _STEP_ROWS.clear()
    if placed.empty:
        c.empty_note(_NO_SPECS)
    else:
        _graph(placed, docs, reads, hops)


def _canvas_changed() -> None:
    """A press on the canvas changed what it draws: its contents redraw, and nothing around them.

    **A card press redrew the whole middle pane** *(until 2026-09-23)*: the
    header, the filter, the zoom controls and the report with the one card
    that opened, and `artifacts.pane` too, for the wash on one row. Opening a
    card does move the grid, because cards are laid out by height, so the
    canvas's contents redraw; the `.graph-canvas` around them keeps the pan and
    zoom (`canvas.js`). Whatever else a press changes is its caller's to say.
    """
    _canvas.prune()
    if not _canvas.targets:
        pane.refresh()
        return
    docs, placed, reads = _layout()
    _canvas.refresh(placed, docs, reads, _highlit(docs))
    button = _VIEW_MENU.get("button")
    if button is not None and not button.is_deleted:
        button.set_text(_view_label(len(docs), len(placed.hidden)))
    reset = _VIEW_MENU.get("reset")
    if reset is not None and not reset.is_deleted:
        reset.set_enabled(bool(APP.card_offsets))


def _report_changed() -> None:
    """Another spec picked: the report is about it now, open or shut."""
    _report.refresh()
    _report_rail.refresh()


def _spec_changed() -> None:
    """A card press picked another spec: the canvas, the report, the left pane's wash, the run bar."""
    from portia.ui import app as app_module
    from portia.ui import artifacts

    _canvas_changed()
    _report_changed()
    artifacts.show_selection()
    app_module.run_controls.refresh()


def _layout(offsets: dict[str, tuple[int, int]] | None = None) -> tuple[dict, graph.Layout, dict]:
    """The canvas as it would be drawn now: the specs, the placed graph, and each open card's reads.

    One function because two callers need the same answer — the render, and
    `move_card`, which has to know where every card *would* sit under a
    candidate arrangement before it records one. ``offsets`` defaults to the
    arrangement on record.
    """
    docs = engine.project_docs(APP)
    open_now = frozenset(APP.expanded) & set(docs)
    reads = {name: engine.column_lineage(APP, name) for name in open_now}
    placed = graph.project_layout(
        docs,
        expanded=open_now,
        rows=_body_rows(docs, reads),
        visible=APP.visible,
        preview=_preview(docs),
        offsets=APP.card_offsets if offsets is None else offsets,
    )
    return docs, placed, reads


def _preview(docs: dict) -> frozenset[str]:
    """What picking a spec on the left would need the canvas to draw.

    Its whole ancestry — the layout subtracts what is already there. Empty unless
    a spec is being previewed, so a canvas already drawing everything the picked
    spec needs shows nothing extra and the click reads as a pure highlight.
    """
    name = APP.previewing
    if name is None or name not in docs:
        return frozenset()
    return frozenset(graph.ancestry(docs, [name]))


def _highlit(docs: dict) -> dict[str, int]:
    """Every node on the path into the selected table, and how far along it sits.

    **Selecting highlights; it never hides.** Picking a spec used to open its card
    and pan to it, which is a change of what is drawn; tracing its ancestry says
    *this is the flow you asked about* while leaving the rest of the project where
    it is. Narrowing what is drawn is a separate, explicit control (`_view_menu`),
    and keeping the two apart is what stops a click quietly removing tables from
    the picture.

    The keys are `graph.ancestry`'s answer plus the files it reaches, so a
    highlight and the filter can never disagree about what "upstream of this"
    means. The **values** are the hop count the animation runs on: a static
    outline says *these are involved*, and running it in hop order also says *the
    data travels this way*, which is what a flow chart is for.

    `DESIGN.md`'s rule holds through it. Every lit element gets the same
    animation and only its **phase** differs, so nothing is louder for being
    nearer the table — the movement carries direction, which is a fact about the
    pipeline, and not rank, which would be a judgment.
    """
    name = _selected_model(docs)
    return graph.upstream_hops(docs, name) if name else {}


def _selected_model(docs: dict) -> str | None:
    """Which table the middle pane is about, if it is about one."""
    name = APP.spec_path.stem if APP.spec_path else None
    return name if name in docs else None


def _graph_header(docs: dict, placed: graph.Layout) -> None:
    """The canvas's header: what is shown on the left, the view on the right.

    **Two groups, neither of which wraps internally** (`DESIGN.md` → Layout
    stability). As one flat wrapping row, a pane drag below ~520px re-slotted
    the controls one at a time — Recenter jumped lines while Reset stayed, and
    the canvas under them moved 32px per re-slot. The view group drops its
    button labels below a width breakpoint instead (the tooltips already carry
    the names), and past the point where even that cannot fit, the whole group
    moves to a second line at once.
    """
    with ui.element("div").classes("graph-header row-gap-sm px-4 pt-3"):
        with ui.element("div").classes("graph-header-side"):
            ui.label("Pipeline").classes("t-heading-sm")
            _view_menu(docs, placed)
            stale = engine.stale_models(APP)
            if stale:
                # A `.sql` that no longer matches its spec is a fact about the
                # deliverable, so it belongs where the deliverable is drawn — not
                # only in `build --check`, where you find it after the fact.
                c.flag_badge(f"{c.count(len(stale), 'model')} stale", c.DRIFT).tooltip(
                    f"{', '.join(stale)}: the .sql no longer matches the spec. Run to regenerate."
                )
        ui.element("div").classes("flex-1")
        _view_controls()


def _view_menu(docs: dict, placed: graph.Layout) -> None:
    """Which tables the canvas is showing, and the way to change it.

    **The count is on screen whether or not anything is filtered**, and it names
    the whole as well as the part: a canvas showing three of seven tables has to
    say so where you are already looking, or coming back to a narrowed project
    reads as tables having disappeared. That is the one thing the "remember where
    I was" behaviour owes you.

    **Tinted rows, not checkboxes** *(2026-08-16)*. A row that is on carries the
    accent at low opacity across its whole width; a row that is off carries no
    colour at all. It is the same statement a checkbox made and it makes it with
    the thing you are already reading — and a column of ticks in a list of nine
    tables is nine widgets to say one thing each. Colour here names a **state**,
    which is what `DESIGN.md` allows: on and off are not a ranking.

    The list scrolls and the two controls do not, so a project with forty tables
    keeps *Select all* and the count reachable instead of pushing them off the
    end of a menu.
    """
    label = _view_label(len(docs), len(placed.hidden))
    button = c.button(label, None, icon="filter_list", micro=True)
    button.tooltip(_VIEW_TIP)
    _VIEW_MENU.clear()
    _VIEW_MENU["button"] = button
    with button:
        # **A trailing caret, because this one is easy to read as a caption.**
        # Every other control on this header is a verb or a glyph; this one is a
        # count, and a count sitting in a toolbar reads as a readout of what is
        # on screen rather than the way to change it — which is exactly how it
        # got missed. The caret is the one mark that says *this opens something*.
        ui.icon("arrow_drop_down").classes("btn-caret")
    with button, ui.menu().classes("p-menu"):
        with ui.element("div").classes("p-menu-panel"):
            _select_all_row(docs)
            with ui.element("div").classes("p-menu-scroll"):
                drawn = set(graph.ancestry(docs, APP.visible)) if APP.visible is not None else None
                for name in sorted(docs):
                    _view_row(name, docs[name], drawn)


#: The filter menu as drawn, so a tick turns its rows and relabels its button
#: in place and the menu stays open (`_set_visible_in_place`): the button, the
#: head row's label, and per table ``row:<name>`` and ``tip:<name>``.
_VIEW_MENU: dict[str, Any] = {}


def _view_label(total: int, hidden: int) -> str:
    return (
        _SHOWING_ALL.format(n=total) if not hidden else _SHOWING.format(n=total - hidden, of=total)
    )


def _all_on(docs: dict) -> bool:
    return APP.visible is None or set(APP.visible) >= set(docs)


def _row_state(name: str, docs: dict) -> tuple[bool, bool]:
    """``(on, chosen)``: drawn on the canvas, and ticked for itself rather than pulled in."""
    if APP.visible is None:
        return True, True
    return name in graph.ancestry(docs, APP.visible), name in APP.visible


def _select_all_row(docs: dict) -> None:
    """One toggle at the top: everything on, or everything off.

    It is a **toggle**, not a button that only ever adds: with every table already
    on there is nothing for an "all on" press to do, so the useful press at that
    moment is the opposite one. Pinned above the scroll region because on a forty
    table project it is the row you most want to be able to reach.
    """
    with ui.element("div").classes("menu-row menu-row--head") as row:
        ui.icon("done_all").classes("menu-row-mark")
        _VIEW_MENU["head"] = ui.label(_SELECT_NONE if _all_on(docs) else _SELECT_ALL).classes(
            "menu-row-name"
        )
    # Read at the press, not at the draw: the rows change under an open menu.
    row.on("click", lambda: _select_all(not _all_on(engine.project_docs(APP))))


def _view_row(name: str, doc: dict, drawn: set[str] | None) -> None:
    """One table: on if the canvas is drawing it, off if it is not.

    A table pulled in because something else needs it is **on** — there is no
    third state, because there is nothing useful to do about it separately. Its
    tooltip says why it is there; un-choosing the table that needs it is what
    takes it away, and that is `_toggle_visible`'s job rather than a locked row's.
    """
    chosen = drawn is None or name in (APP.visible or set())
    on = drawn is None or name in drawn
    classes = "menu-row" + (" menu-row--on" if on else "")
    with ui.element("div").classes(classes) as row:
        ui.element("div").classes("menu-row-mark")
        ui.label(name).classes("menu-row-name").tooltip(name)
        ui.element("div").classes("flex-1")
        if doc.get("layer"):
            c.chip(doc["layer"])
        # Always there, and silenced unless it applies, so a tick can turn it
        # on or off in place.
        tip = ui.tooltip(_VIEW_REQUIRED)
    _silence(tip, not (on and not chosen))
    _VIEW_MENU[f"row:{name}"] = row
    _VIEW_MENU[f"tip:{name}"] = tip
    row.on(
        "click", lambda n=name: _toggle_visible(n, not _row_state(n, engine.project_docs(APP))[0])
    )


def _silence(tip: ui.tooltip, quiet: bool) -> None:
    """A tooltip that no hover opens, or one that it does."""
    if quiet:
        tip.props("no-parent-event")
    else:
        tip.props(remove="no-parent-event")


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

    **Reset layout is the same way back for the cards** *(2026-09-07)*. Recenter
    moves the view and leaves the cards where the reader dragged them; Reset
    puts every card back where the grid places it and leaves the view alone.
    Two controls because they undo two different things, and a reader who has
    only done one of them should not lose the other. Disabled while nothing has
    been moved, so it also says whether anything has.
    """
    with ui.element("div").classes("row-gap-xs view-controls"):
        c.button("", _zoom_out, icon="remove", micro=True).tooltip(_ZOOM_OUT_TIP)
        ui.label("100%").classes("zoom-level")
        c.button("", _zoom_in, icon="add", micro=True).tooltip(_ZOOM_IN_TIP)
        c.button("Recenter", _recenter, icon="filter_center_focus", micro=True).tooltip(
            _RECENTER_TIP
        )
        _VIEW_MENU["reset"] = c.button(
            "Reset layout",
            reset_layout,
            icon="grid_on",
            micro=True,
            enabled=bool(APP.card_offsets),
        )
        _VIEW_MENU["reset"].tooltip(_RESET_LAYOUT_TIP)


def _zoom_in() -> None:
    ui.run_javascript("portiaZoomIn()")


def _zoom_out() -> None:
    ui.run_javascript("portiaZoomOut()")


def _recenter() -> None:
    ui.run_javascript("portiaRecenter()")


def move_card(name: str, dx: int, dy: int) -> None:
    """A card was dropped ``(dx, dy)`` layout pixels from where it was drawn.

    From `canvas.js`, once, at the end of the gesture: the client moved the card
    and its arrows for the length of the drag, so the server hears about a drop
    and never about a pointer. The delta is added to whatever offset the card
    already had, because the client measured its movement from where this
    render put it — which was the grid position plus the last offset.

    Written per project as it changes (`engine.remember_layout`), like the
    canvas filter, so the arrangement is there tomorrow.

    **Two cards never stack.** A drop that would leave this card over another
    is refused: nothing is recorded and the pane redraws, which puts the card
    back where the last render had it. The client already knows
    (`canvas.js` snaps it back without asking); this is the rule's home.
    """
    ox, oy = APP.card_offsets.get(name, (0, 0))
    offsets = {**APP.card_offsets, name: (ox + dx, oy + dy)}
    if offsets[name] == (0, 0):
        del offsets[name]
    _, placed, _ = _layout(offsets)
    if graph.overlapping(placed.nodes, name):
        _canvas_changed()
        return
    APP.card_offsets = offsets
    engine.remember_layout(APP.root, offsets)
    _canvas_changed()


def reset_layout() -> None:
    """Every card back where the grid places it. The view is left where it is."""
    APP.card_offsets = {}
    engine.remember_layout(APP.root, {})
    _canvas_changed()


# --- the canvas --------------------------------------------------------------


def _body_rows(docs: dict, reads: dict) -> dict[str, int]:
    """How many rows each open card will draw, so `graph` can size it.

    Counting what is about to be drawn, not measuring anything — the same
    arrangement the step cards' badge counting used, and for the same reason: the
    renderer knows what it is about to put on screen and the layout module should
    not have to know what any of it says.

    **It counts what `_reads_section` counts**, off the same two calls — the
    inputs come from what the spec declares rather than from what the column walk
    resolved, because a card whose height was measured against a shorter list
    clips the rows the list actually has.
    """
    rows = {}
    open_model, open_input = APP.open_input or ("", "")
    for name, inputs in reads.items():
        doc = docs.get(name) or {}
        names = set(graph.model_deps(doc, docs)) | set(graph.model_sources(doc))
        steps = len(doc.get("steps") or [])
        # A heading for each half, one row per input, one per step, and one more
        # for the columns nothing could be traced to when there are any.
        count = 2 + (len(names) or 1) + steps + (1 if inputs.untraced else 0)
        if open_model == name:
            # An unfolded input adds a row per column — or one row saying nobody
            # has profiled it, which is what that row draws instead.
            found = next((t for t in inputs.tables if t.name == open_input), None)
            count += len(found.columns) if found and found.columns else 1
        rows[name] = count
    return rows


def _graph(placed: graph.Layout, docs: dict, reads: dict, hops: dict[str, int]) -> None:
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
        ui.html(_edges_svg(placed, hops))
        for node in placed.nodes:
            _node(node, docs, reads, focused=node.id == APP.focus_model, hop=hops.get(node.id))


def _edges_svg(placed: graph.Layout, hops: dict[str, int]) -> str:
    """One overlay for every edge. 1px hairline-strong, small arrowheads, no labels.

    Each edge is drawn twice: the hairline you see, and a fat transparent stroke
    over it carrying ``data-edge``. A 1px bezier is not a thing anyone can click,
    and widening the visible line to make it clickable would make an arrow shout
    — so the hit area is invisible and the line stays a hairline. `canvas.js`
    turns a click on one into an event; the geometry lives here because this is
    where the geometry is.

    **Each edge is one ``<g>`` naming its two ends** *(2026-09-07)*, so that
    while a card is being dragged the client can redraw the arrows touching it
    from the cards' current positions. That is the one place the bezier is
    spelled a second time (`canvas.js`'s ``edgePath``), and it is there for the
    length of a drag only: the drop comes back here and this draws the edge the
    server believes in. Change `Edge.path` and change that function with it.
    """
    ghosts = {n.id for n in placed.nodes if n.ghost}
    parts = []
    for edge in placed.edges:
        path, head = edge.path(), edge.arrowhead()
        # An edge touching a ghost is drawn as one: a solid arrow arriving at a
        # card that is not there reads as a card that failed to draw.
        if edge.src in ghosts or edge.dst in ghosts:
            cls = " class='is-ghost'"
        elif edge.src in hops and edge.dst in hops:
            # **The phase is the source's hop count**, so the dashes on the
            # arrow leaving a file start moving before the ones on the arrow
            # leaving the table it feeds — the flow reads as travelling toward
            # the table you picked rather than as every edge crawling at once.
            cls = f" class='is-lit' style='--hop:{hops[edge.src]}'"
        else:
            cls = ""
        parts.append(
            f"<g data-src='{edge.src}' data-dst='{edge.dst}'>"
            f"<path d='{path}'{cls}/><polygon points='{head}'{cls}/>"
            f"<path class='graph-hit' data-edge='{edge.dst}|{edge.src}' d='{path}'/>"
            "</g>"
        )
    return (
        f'<svg class="graph-edges" width="{placed.width}" height="{placed.height}">'
        f"{''.join(parts)}</svg>"
    )


def _node(
    node: graph.Node, docs: dict, reads: dict, *, focused: bool = False, hop: int | None = None
) -> None:
    style = f"left:{node.x}px;top:{node.y}px;width:{node.w}px;height:{node.h}px"
    classes = "graph-node"
    if focused:
        classes += " graph-node--focus"
    if hop is not None:
        # How far upstream this card sits, as the animation's phase. The CSS turns
        # it into a delay, so the pulse leaves the files and arrives at the table
        # you picked. Every lit card gets the same animation — only *when* it
        # starts differs, which is why this is direction and not rank.
        classes += " graph-node--lit"
        style += f";--hop:{hop}"
    box = ui.element("div").classes(classes).style(style)
    # Its name, for the drag: `canvas.js` moves the card by this and reports the
    # drop against it (`move_card`), and finds the arrows touching it by the
    # same name on their ``<g>``. A ghost is not draggable — it is not on the
    # canvas — and the client reads that off the same box.
    box.props(f'data-node="{node.id}"' + (" data-ghost" if node.ghost else ""))
    # A table the copilot just added settles onto the canvas; the dozens of
    # rebuilds a live exchange used to fire redraw the same nodes under the
    # same keys and move nothing (`c.enters`).
    c.enters(box, f"node:{node.id}")
    if focused:
        box.props(f"data-focus-token={APP.focus_token}")
    with box:
        if node.kind == graph.SOURCE:
            _source_node(node)
        else:
            _model_card(node, docs, reads.get(node.id))


def _source_node(node: graph.Node) -> None:
    """A file. Deliberately the quietest thing on the canvas — it is what arrived,
    not what was decided."""
    classes = "source-node" + (" source-node--ghost" if node.ghost else "")
    label = ui.label(node.id).classes(classes)
    if node.ghost:
        label.tooltip(_GHOST_WHY)
        label.on("click", lambda: _reveal_preview())
        return
    label.on("click", lambda n=node.id: _select_source(n))


def _model_card(node: graph.Node, docs: dict, inputs) -> None:
    """A table portia built — and, opened, what it reads and how.

    Distinct from a source on purpose: a table with its own spec, steps and
    rationale must not look like a CSV somebody dropped in the folder.

    The layer is shown as its **name**, with no colour and no size of its own.
    staging/intermediate/mart is build order, not a quality ladder, and it is the
    one field on this card that nothing measured — so it may say what kind of
    table this is and must never make one card louder than another.
    """
    classes = "model-card"
    if node.ghost:
        classes += " model-card--ghost"
    if node.open:
        classes += " model-card--open"
    if APP.spec_path is not None and APP.spec_path.stem == node.id:
        classes += " model-card--selected"

    with ui.element("div").classes(classes):
        with ui.element("div").classes("model-head") as head:
            ui.icon("expand_more" if node.open else "chevron_right").classes("model-caret")
            ui.label(node.id).classes("model-name").tooltip(node.id)
            ui.element("div").classes("flex-1")
            if node.layer:
                c.chip(node.layer)
            c.caption(c.count(node.steps, "step"))
        # A ghost is not a card you can open — it is not on the canvas yet, so
        # the one thing to do with it is put it there.
        head.on("click", lambda n=node.id: _reveal_preview() if node.ghost else _open_model(n))
        if node.ghost:
            head.tooltip(_GHOST_WHY)
        if node.open:
            _model_body(node, docs.get(node.id) or {}, docs, inputs)


def _model_body(node: graph.Node, doc: dict, docs: dict, inputs) -> None:
    """What this table reads, and the ordered list of operations that build it."""
    # No stacking gap: every child here is one row of a fixed height, and that
    # height is what `_body_rows` counts and `graph.open_height` reserves. A 4px
    # gap between sixteen rows is 60px the card was never sized for, and the rows
    # at the bottom fall out of it.
    with ui.element("div").classes("model-body"):
        _reads_section(node.id, doc, docs, inputs)
        _steps_section(doc)


def _reads_section(model: str, doc: dict, docs: dict, inputs) -> None:
    """One row per incoming arrow, unfoldable onto the columns that came along it.

    **The list is the arrows, and only then the columns.** It is built from what
    the spec declares it reads — `graph.model_deps` and `graph.model_sources`, the
    same two calls the canvas draws its edges from — so a row here and an arrow
    out there cannot disagree. The columns are then attached where they are known.

    That order matters because they are known **less often than the arrows are**.
    Column lineage needs the input's columns, which come from the catalog, so a
    source nobody has indexed yields nothing — and driving the list off the walk
    made a table with three arrows into it draw an empty section that read as
    *this reads nothing*. An arrow you can see and a row that denies it is the
    worst version of this card.

    The columns themselves come from `knowledge.build`'s walk — the same one the
    knowledge graph is built from, so the canvas and the graph cannot end up
    telling two stories about where a column came from — and it runs against an
    in-memory graph, so no database needs to be up for a card to answer.

    A column nothing could be traced to is **named, not dropped**: a `count(*)`
    that quietly vanished would read as a column with a missing origin rather
    than one with no single origin to have.
    """
    ui.label(_READS).classes("model-section")
    names = sorted(graph.model_deps(doc, docs)) + graph.model_sources(doc)
    if not names:
        c.caption(_READS_NOTHING)
        return
    columns = {table.name: table for table in (inputs.tables if inputs else ())}
    for name in dict.fromkeys(names):
        _input_row(model, name, columns.get(name))
    if inputs is not None and inputs.untraced:
        c.caption(_UNTRACED.format(cols=", ".join(inputs.untraced))).classes("model-note")


def _input_row(model: str, name: str, table) -> None:
    """One input table, and its columns when it is the one you asked about.

    A row whose columns are unknown still opens and still says so. *Nobody has
    profiled this yet* and *this contributed nothing* are different facts, and a
    row that silently refused to open would be indistinguishable from the second.
    """
    open_now = APP.open_input == (model, name)
    known = table.columns if table else ()
    with ui.element("div").classes("model-row") as row:
        ui.icon("expand_more" if open_now else "chevron_right").classes("model-caret")
        ui.label(name).classes("model-row-name")
        ui.element("div").classes("flex-1")
        c.caption(c.count(len(known), "column") if known else _COLUMNS_UNKNOWN)
    row.on("click", lambda m=model, n=name: _open_input(m, n))
    if not open_now:
        return
    if known:
        for column in known:
            _lineage_row(column)
    else:
        c.caption(_COLUMNS_UNKNOWN_WHY).classes("model-note")


def _lineage_row(column) -> None:
    """``this column ← the column it came from``, and which step is responsible.

    Named apart from `_column_row`, which is the source inspector's row for a
    profiled column. Two different facts about a column — what it *is* and where
    it *came from* — and they are drawn on two different surfaces.

    The step is a pointer rather than a description of what it did: the spec is
    the one place that says that, and restating it here would be a second
    account of a transform that could drift from the one that runs.
    """
    with ui.element("div").classes("model-column"):
        c.mono(column.column, small=True)
        ui.icon("arrow_back").classes("model-from")
        c.mono(column.origin, small=True, color="c-mute")
        ui.element("div").classes("flex-1")
        if column.step:
            c.caption(column.step.split("#")[-1]).tooltip(_VIA.format(step=column.step))


def _steps_section(doc: dict) -> None:
    """The operations that build this table, in the order the spec records them.

    **An ordered list, not a flow chart.** Every step compiles to a named block
    inside one SQL file, so the sequence is the whole of the relationship between
    them and arrows between list items would be drawing the numbers again. The
    order is the recorded sequence of decisions and nothing re-sorts it.

    A blocking flag is on the row that carries it, which is the promise this
    screen makes: Run 5 buried an acknowledged zero mid-dict in a terminal
    confirmation and shipped a 3.85%-inflated table (docs/EVALUATION.md).
    """
    steps = [s for s in (doc.get("steps") or []) if s.get("id")]
    ui.label(_STEPS).classes("model-section")
    if not steps:
        c.caption(_NO_STEPS_YET)
        return
    for position, step in enumerate(steps, start=1):
        _step_row(position, step)


def _step_row(position: int, step: dict) -> None:
    """One operation: where it comes in the sequence, what it is, and its flags.

    **The `unused` chip is gone** *(2026-09-02, `docs/FINDINGS.md` §7)*. It drew
    `spec.unreachable_steps`, and it was right to draw it while an unread step
    arrived honestly: `record_step` was append-only and was the only way to ask
    the data a question, so a spec collected the questions asked on the way to
    it — seven of eleven recorded steps in the 2026-08-17 AQN run, five of them
    pure counting queries.

    Both causes are gone. Questions go to `query_data` and their answers to
    `findings/`, and a superseded attempt is *replaced* rather than left behind.
    So an unread step is no longer a fact worth explaining to a reader; it is a
    bug, and `pipeline.unread_steps` reports it from `cli/build --check` where a
    bug of that shape belongs. Asking *why does this spec look like this* is what
    the journal below is for.
    """
    step_id = step["id"]
    result = _result(step_id)
    # Before a run the row shows what the spec *claims*; after one, what the
    # engine measured. An acknowledgement is visible either way.
    blocking = result.blocking if result else []
    acknowledged = list(result.acknowledged) if result else list(step.get("acknowledge") or [])
    drifted = sorted(result.drift) if result else []

    classes = "model-row model-step"
    if blocking:
        classes += " model-step--blocked"
    elif APP.selected_step == step_id:
        classes += " model-step--selected"

    with ui.element("div").classes(classes) as row:
        if not blocking:
            # A blocked row is never drawn selected, so it is never moved.
            _STEP_ROWS.append((step_id, row))
        c.caption(str(position)).classes("model-step-n")
        ui.label(step_id).classes("model-row-name").tooltip(step_id)
        ui.element("div").classes("flex-1")
        for flag in blocking:
            c.flag_badge(flag, c.BLOCKING)
        for flag in acknowledged:
            c.flag_badge(flag, c.ACKNOWLEDGED)
        for field in drifted:
            c.flag_badge(field, c.DRIFT)
        c.chip(step.get("op") or "?")
    row.on("click", lambda s=step_id: _select_step(s))


# --- the run report ---------------------------------------------------------


def _report_half() -> None:
    """One list of steps: what was decided, and what came out of it.

    **It is one region, and that is the change** *(2026-08-13)*. Selecting a step
    used to open a second panel under the canvas, so the lower part of the middle
    pane became two stacked panels about the same step — the decision in one, the
    result in the other, a divider apart, with nothing saying which was which,
    one divider that resized the wrong pair, and no way to close either and keep
    the other. They are two halves of one step, so they are one block now and the
    selected one opens in place. Nothing is hidden that was not hidden before:
    every block is still drawn, in spec order, with its own four groups.

    **The list comes from the spec, not from the run.** A recorded step is a
    decision on the record whether or not it has been executed, and a pane that
    said "no run yet" was putting the whole decision record behind one sentence —
    on the one screen where a step is reviewed before it is trusted.
    """
    with ui.element("div").classes("p-pane"):
        _report()


@ui.refreshable
def _report() -> None:
    """The report's head and its blocks: what picking another spec on the canvas redraws."""
    _BLOCKS.clear()
    _report_head()
    # Keyed to the spec it reports on, so a redraw of this for another
    # reason keeps where you were reading (`c.scroll_area`).
    with c.scroll_area(f"report:{spec_label(APP.spec_path)}", classes="p-pad stack-md"):
        if APP.running:
            # The same mark the action bar carries, and the same instance of
            # it: `progress_note` is one refreshable drawn in both places, so
            # the two cannot end up a second apart on the same run.
            from portia.ui import app as app_module

            app_module.progress_note()
        if APP.run_error:
            _run_error()
        blocks = _blocks()
        if not blocks:
            c.empty_note(_NO_STEPS)
            return
        if APP.results:
            _run_header()
        elif not APP.running and not APP.run_error:
            c.caption(_NOT_RUN)
        for step, result in blocks:
            # A slot per block, so picking a step redraws the block it
            # shuts and the one it opens and nothing else (`_select_step`).
            with ui.element("div").classes("contents") as slot:
                _step_block(step, result)
            _BLOCKS[step["id"]] = (slot, step, result)
        _journal_section()


#: The report's blocks as drawn, by step id: the slot each sits in and what drew it.
_BLOCKS: dict[str, tuple[ui.element, dict, Any]] = {}
#: The canvas cards' step rows as drawn, by step id, for the selected wash. Several
#: cards can name the same step, and each is lit as it always was.
_STEP_ROWS: list[tuple[str, ui.element]] = []
_STEP_SELECTED = "model-step--selected"


def _journal_section() -> None:
    """The journal: what was asked on the way to this table, oldest first.

    **The journal is a human surface** (`docs/FINDINGS.md` §6.3). The agent reads
    findings through `describe_source`, one table at a time; this answers the
    other question — *why does this pipeline look like this* — which is what a
    person asks and what a compiled `.sql` cannot carry.

    It is here rather than on the canvas card for a layout reason worth writing
    down: an open card's height is *counted* by `_body_rows` rather than measured,
    so a section whose length nobody can predict would clip its own last rows. The
    report region already scrolls.

    **One list, folded twice** *(2026-09-07, the user's call, revising the same
    day's first draw)*. The first version drew two headed lists — what was
    recorded for this model and *findings about its inputs* — each finding a
    card with every field open. Two things were wrong with it. The second
    heading was a distinction the reader did not need to make, since a finding
    about an input **is** part of why this table looks like it does, so both
    halves of `engine.journal_for` are one chronological list now. And a page
    of open cards under a page of step blocks was a wall: the journal is a
    disclosure, open it and you see the questions, open a question and you see
    what the data said. Which rows are open rides `App.open_rows`, the
    transcript's mechanism, so selecting a step does not shut what you were
    reading.

    The same fields `findings.render_finding` prints in the terminal, and no
    number that is not in the record: the pane draws the strings the file
    holds and computes nothing (`portia/core/present.py` for the rule).
    """
    # The **model** name, not the spec's filename: a finding names the table it
    # was in service of, and `spec_label` is what a human reads on the head.
    model = Path(APP.spec_path).stem if APP.spec_path else ""
    journal, around = engine.journal_for(APP, model) if model else ([], [])
    records = sorted(journal + around, key=lambda f: str(f.get("at") or ""))

    def header() -> None:
        ui.label(_JOURNAL).classes("report-group-label")
        c.caption(str(len(records)) if records else _NO_FINDINGS)

    if not records:
        with ui.element("div").classes("journal-head"):
            header()
        return

    def body() -> None:
        for finding in records:
            _finding(finding, model)

    key = f"journal:{model}"
    c.disclosure(
        header, body, open=key in APP.open_rows, on_toggle=partial(APP.toggle_row, key)
    ).classes("journal")


def _finding(finding: dict, model: str) -> None:
    """One finding: its question as the row, and the record under it.

    Shut, it is the question, the date, and — because it is a fact worth
    seeing before you open anything — whether a table it measured has moved
    since. Open, it is what the data said, what that changed, the tables it is
    about, the spec it was recorded for when that is not this one, and the
    queries it rests on with their SQL and results verbatim from the log: the
    sentence and the measured numbers side by side, as `expect` and `outcome`
    are.
    """
    key = f"finding:{finding.get('path', '')}"
    when = str(finding.get("at") or "")

    def header() -> None:
        ui.label(finding.get("question", "")).classes("finding-question")
        if finding.get("stale"):
            # Named, never a verdict: what changed is a fact, whether the
            # finding still holds is for whoever is reading it.
            c.flag_badge(_STALE_MARK.format(tables=", ".join(finding["stale"])))
        if when:
            c.caption(when[:10]).classes("finding-when").tooltip(when)

    def body() -> None:
        with ui.element("div").classes("finding-body"):
            with ui.element("div").classes("finding-grid"):
                ui.label("answer").classes("kv-key")
                c.text(finding.get("answer", ""))
                ui.label("so").classes("kv-key")
                c.text(finding.get("so", ""))
            _finding_meta(finding, model)
            _finding_queries(finding.get("queries") or [])

    row = c.disclosure(
        header, body, open=key in APP.open_rows, on_toggle=partial(APP.toggle_row, key)
    )
    row.classes("finding")
    c.enters(row, key)


def _finding_meta(finding: dict, model: str) -> None:
    """The tables a finding is about, and the spec it was recorded for when that is another one."""
    with ui.element("div").classes("finding-meta"):
        for ref in finding.get("about") or []:
            c.chip(str(ref))
        spec = finding.get("spec")
        if spec and spec != model:
            c.caption(_RECORDED_FOR.format(spec=spec))


def _finding_queries(queries: list[dict]) -> None:
    """The `query_data` calls a finding rests on, SQL and result verbatim from the log.

    The result is pretty-printed when it parses as JSON and shown as it came
    back otherwise — a shape we did not produce is not one to reformat.
    """
    if not queries:
        return
    with ui.element("div").classes("finding-queries"):
        ui.label(c.count(len(queries), "query")).classes("kv-key")
        for query in queries:
            if query.get("question"):
                c.caption(str(query["question"]))
            if query.get("sql"):
                c.code_block(str(query["sql"]))
            result = query.get("result")
            if result:
                c.code_block(_pretty(str(result)))


def _pretty(text: str) -> str:
    import json

    try:
        return to_json(json.loads(text))
    except (TypeError, ValueError):
        return text


def _report_head() -> None:
    """What this report is about, and the way to shut it.

    **A button, not only a drag.** A side pane closes by dragging its edge past
    the width it stops being readable at (`DESIGN.md`), and that gesture exists
    here too — but the report is the half you shut *often*, because opening a card
    is how you read the canvas and the canvas is what you want the room for. A
    gesture you have to discover is the wrong cost for the thing you do most.

    It names the spec, so the pane says which table's decisions these are; the
    canvas above it has several.
    """
    with ui.element("div").classes("row-gap-sm px-4 pt-3"):
        ui.label(spec_label(APP.spec_path)).classes("t-heading-sm")
        ui.element("div").classes("flex-1")
        c.button("", _close_report, icon="close_fullscreen", micro=True).tooltip(_REPORT_SHUT_TIP)


def _close_report() -> None:
    APP.report_open = False
    pane.refresh()


def _open_report() -> None:
    APP.report_open = True
    pane.refresh()


def _blocks() -> list[tuple[dict, Any]]:
    """The open spec's steps in spec order, each with its result if it has one.

    **Spec order, and the spec is what supplies it.** The order is the recorded
    sequence of decisions (`DESIGN.md` → the graph), so deriving it from whatever
    the last run happened to produce would make the list change shape depending
    on how far the run got. A result whose step is no longer in the spec is still
    drawn, at the end: it is a leftover rather than a gap in the sequence, and
    dropping it would be the pane quietly deciding a measurement did not happen.
    """
    steps = [s for s in ((APP.spec or {}).get("steps") or []) if s.get("id")]
    blocks = [(step, _result(step["id"])) for step in steps]
    named = {step["id"] for step, _ in blocks}
    for result in APP.results or []:
        if result.id not in named:
            blocks.append(({"id": result.id, "op": result.op}, result))
    return blocks


def _run_error() -> None:
    with ui.element("div").classes("stack-sm"):
        ui.label("Run failed").classes("t-heading-sm c-error")
        c.code_block(APP.run_error or "")
        if APP.run_problem is not None:
            from portia.ui import feedback

            feedback.report_button(APP.run_problem)


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


def _step_block(step: dict, result: Any) -> None:
    """One step — the decision it records, and what running it produced.

    Open, it gains the step **verbatim** above its measurements. That is the
    panel that used to sit under the canvas, and it is here because a step's
    decision and its outcome are two halves of one thing: reading them meant
    looking at two panels and knowing which was which.

    **The acknowledgement is drawn once, at the top, and never collapses** —
    from the run when there has been one, from the spec before that, so an
    override is visible on this screen the moment it is recorded rather than the
    moment it is executed. Run 5 approved one as fifteen characters mid-dict in a
    terminal and shipped a 3.85%-inflated table (`docs/EVALUATION.md`).

    **Only the head is clickable.** The block used to toggle wherever you pressed
    it, so opening the table preview inside one shut the block you opened it in.
    """
    step_id = step["id"]
    opened = APP.selected_step == step_id
    acknowledged = list(result.acknowledged) if result else list(step.get("acknowledge") or [])
    rationale = result.rationale if result else step.get("rationale")

    classes = "report-block" + (" report-block--selected" if opened else "")
    with ui.element("div").classes(classes) as block:
        # A step the copilot just recorded arrives; the block redrawn on every
        # selection keeps its key and keeps still. Keyed by the spec too — two
        # specs can both name a step `dedupe`, and switching between them would
        # otherwise read as the same block (`c.enters`).
        c.enters(block, f"step:{APP.spec_path or ''}:{step_id}")
        if opened:
            # Brought into view once per pick, declaratively — the same shape as
            # the canvas's focus mark, and for the same reason (`scroll.js`).
            # Opening a block twenty rows down and leaving it off screen is the
            # click doing nothing as far as the reader can tell.
            block.props(f"data-scroll-to={APP.step_token}")
        _block_head(step, result, opened=opened)
        if acknowledged:
            c.acknowledged_banner(
                acknowledged,
                rationale=rationale,
                measured=result.outcome if result else None,
            )
        if opened:
            _group("decided", lambda: _decided(step, acknowledged))
        if result is not None:
            if result.drift:
                _group("drift", lambda: _drift(result.drift))
            _group("provenance", lambda: _provenance(result.provenance))
            _group("outcome", lambda: _outcome(result.outcome, acknowledged))
            if rationale and not acknowledged:
                _group("rationale", lambda: c.text(rationale))
            if result.table is not None:
                _table(result)


def _block_head(step: dict, result: Any, *, opened: bool) -> None:
    """Which step this is, and whether it has been run. The whole row opens it."""
    with ui.element("div").classes("report-block-head") as head:
        ui.icon("expand_more" if opened else "chevron_right").classes("report-caret")
        ui.label(step["id"]).classes("t-mono c-ink")
        c.chip(step.get("op") or "?")
        ui.element("div").classes("flex-1")
        if result is None:
            c.caption(_STEP_NOT_RUN)
    head.on("click", lambda i=step["id"]: _select_step(i))


def _decided(step: dict, acknowledged: list[str]) -> None:
    """The step exactly as the spec records it.

    **Every field is said once** (`DESIGN.md` → `write-confirm`). An
    acknowledged step's flags and its rationale are in the banner above, so they
    are not repeated here — a second, quieter copy of an override is the one
    place in this app where that reads as a second, lesser thing.
    """
    said = {"id", "op"} | ({"acknowledge", "rationale"} if acknowledged else set())
    c.payload_view({k: v for k, v in step.items() if k not in said})


def _group(label: str, body) -> None:
    with ui.element("div").classes("report-group"):
        ui.label(label).classes("report-group-label")
        body()


def _table(result: Any) -> None:
    """The produced table, one click away.

    Inline it pushed everything below it off the screen, and the point of the
    report is that the four groups can be read at a glance. The label carries
    the shape, so the table is never a surprise you have to open to size up.

    The shape comes off `engine.shape_of`, measured on the run's own thread
    (`engine.warm_shapes`) — this block is drawn for every step on every
    redraw of the pane, and asking the relation here cost one scan per step per
    click.
    """
    _table_preview(engine.result_shape_key(result), result.table, report=True)


@ui.refreshable
def _table_preview(key: tuple, table: Any, *, report: bool = False) -> None:
    """A table's rows and count, drawn once the count is known.

    **Nothing here runs a query on the loop.** A missing shape draws *counting…*
    and asks `engine.measure_shape_later` for it on a thread; the answer refreshes
    this and nothing else. Every preview in the middle pane goes through here —
    a source's file, a built table on the warehouse, an output, a step's
    result — because each of them was a ``count(*)`` in the render pass, and on a
    real extract that is seconds of a window that does not answer clicks.

    ``report`` is the run report's shape: a shut row whose label carries the
    count, rather than an open group.
    """
    if table is None:
        return
    shape = engine.shape_of(key)
    if shape is None:
        shape = _count_later(key, table)
    if shape is None:
        if report:
            c.caption(_COUNTING)
        else:
            _group("preview", lambda: c.caption(_COUNTING))
        return
    if report:
        total, head = shape
        if total is None:
            c.caption(c.UNCOUNTED)
            return
        label = f"preview · {c.count(total, 'row')} × {c.count(head.shape[1], 'column')}"
        c.collapsed(label, lambda: c.table_preview(table, shape=shape))
        return
    _group("preview", lambda: c.table_preview(table, shape=shape))


def _count_later(key: tuple, table: Any) -> tuple | None:
    """Start the measurement off the loop; return it now only when there is no loop.

    Rendered under a running window there is always a loop, and the count lands
    a moment later through `_table_preview.refresh`. Without one — a test drawing the
    pane in a bare context — the honest thing is to measure inline, as the pane
    always did, rather than draw a *counting…* that nothing will replace.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        try:
            return engine.measure_shape(key, table)
        except Exception:  # noqa: BLE001 — an unreadable table is drawn as such
            return (None, None)
    from nicegui import background_tasks

    background_tasks.create(
        engine.measure_shape_later(key, table, _table_preview.refresh), name="shape"
    )
    return None


_COUNTING = "counting rows…"


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
    remote = bool(entry and entry.get(catalog.REMOTE))
    key = _source_key(entry) if entry else ()
    with _inspector_scroll():
        _inspector_header(name, "the catalog's entry for this source")
        if entry is None:
            c.empty_note("no catalog entry")
            return
        with c.kv_list():
            if remote:
                c.kv("table", entry.get("source", ""))
                c.kv("connection", APP.connection or "")
                rows = (entry.get("indexed") or {}).get("rows")
                if rows is not None:
                    c.kv(_rows_label(entry), c.count(int(rows), "row"))
            else:
                c.kv("file", entry.get("source", ""))
        if remote:
            _profile_state(name, entry)

        columns = entry.get("columns") or []
        if editing:
            _edit_interpretation(name, entry, columns)
        else:
            _group("summary", lambda: _summary(entry))
            if entry.get(catalog.NOTES):
                _group("notes", lambda: _notes(entry))
            _group("columns", lambda: _columns(name, columns))
            _interpretation_actions(name)
        _table_preview(key, frame)


_NOT_PROFILED = "Indexed as metadata only. Nothing has measured this table yet."
_NOT_PROFILED_COST = (
    "Profiling scans the whole table on the connection's warehouse, once. "
    "Here, or with Index on the Indexing tab."
)
_PROFILED_AT = "profiled {at}"
_PROFILE_NOW = "Profile now"
_PROFILING = "Profiling {name}…"
_NOT_CONNECTED_FOR_PROFILE = "Connect to the warehouse first."


def _rows_label(entry: dict) -> str:
    """``rows``, or ``rows (estimate)`` when the engine's free count is one."""
    return "rows (estimate)" if catalog.rows_estimated(entry.get("indexed")) else "rows"


def _profile_state(name: str, entry: dict) -> None:
    """Whether measured facts stand behind the columns below — and the opt-in scan.

    `docs/CONNECTOR.md` §2.6: a scoped table indexes as metadata because a
    profile is a scan on someone's meter, and this is where a person asks for
    it. The state is stated either way, so absent facts read as *not profiled*
    rather than as a profile with nothing in it.
    """
    profiled = entry.get("profiled")
    if profiled:
        c.caption(_PROFILED_AT.format(at=profiled.get("at", "")))
        return
    with ui.element("div").classes("not-read"):
        ui.icon("pending").classes("not-read-icon")
        with ui.element("div"):
            ui.label(_NOT_PROFILED).classes("t-body c-ink")
            c.caption(_NOT_PROFILED_COST)
    with ui.element("div").classes("row-gap-sm"):
        c.button(
            _PROFILE_NOW,
            partial(_profile_now, name),
            icon=c.INDEX_ICON,
            micro=True,
            enabled=APP.connected,
        )
        if not APP.connected:
            c.caption(_NOT_CONNECTED_FOR_PROFILE)


async def _profile_now(name: str) -> None:
    from portia.ui import artifacts

    ui.notify(_PROFILING.format(name=name))
    try:
        await engine.profile_remote(APP, name)
    except Exception as exc:  # noqa: BLE001 — shown, not swallowed
        core_feedback.remember(exc, "profiling")
        ui.notify(f"{type(exc).__name__}: {exc}")
        return
    pane.refresh()
    artifacts.pane.refresh()
    ui.notify(f"Profiled {name}.")


# --- correcting what the catalog says ----------------------------------------


def _interpretation_actions(name: str, *, removable: bool = True) -> None:
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
        if removable:
            # A built table is not un-indexed from here: it is a spec's
            # product, and `cli/remove_spec` is the door that removes a spec.
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
    ui.notify(f"{name} is no longer indexed.")


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
            # The measured kind, or the warehouse's own type on a table nobody
            # has profiled — the one fact a metadata index has about a column.
            c.chip(str(col.get("inferred") or col.get("dtype") or ""))
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
    ui.notify(f"Saved {name}.")


async def _ask_copilot(name: str, note: str) -> None:
    from portia.agent import prompts
    from portia.ui import exchange

    if not (note or "").strip() or APP.busy:
        return
    APP.asking = None
    pane.refresh()
    await exchange.start(
        prompts.task("reinterpret", source=name, note=note.strip()),
        model=APP.model or _default_model(),
        effort=APP.effort,
        kind=state.REREAD,
        label=name,
    )


def _default_model() -> str:
    """The provider's default, not one global one (`state.App.default_model`)."""
    return APP.default_model


def _spend() -> str:
    effort = f" · effort {APP.effort}" if APP.effort else ""
    return f"costs a turn on {APP.model or _default_model()}{effort}"


def _source_key(entry: dict) -> tuple:
    """What a source's count is derived from: the file as it is, or the table's name."""
    if entry.get(catalog.REMOTE):
        return engine.remote_shape_key(str(entry.get("source", "")), APP)
    return engine.file_shape_key(APP.root / str(entry.get("source", "")))


def _source_table(entry: dict):
    """The source's rows, or None — the file has moved, or the warehouse is not open."""
    try:
        return engine.read_source(entry, APP)
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
            c.caption(_NOT_READ_WHY if catalog.is_profiled(entry) else _NOT_READ_WHY_METADATA)


def _notes(entry: dict) -> None:
    """What was learned about this table after it was read, in the order it was.

    Dated and never rewritten (`catalog.set_interpretation`), which is why it is
    its own group rather than a paragraph appended to the summary: the summary
    is one read that gets replaced whole, and a note is a sentence from one chat
    that the next has to see. Drawn only when there is one; the group's absence
    says nothing has been learned, and the summary already says whether the
    table has been read at all.
    """
    for note in entry.get(catalog.NOTES) or []:
        with ui.element("div").classes("note-row"):
            c.caption(str(note.get("at", ""))[:10])
            c.text(note.get("text", ""))


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
            # The measured kind, or the warehouse's own type on a table nobody
            # has profiled — the one fact a metadata index has about a column.
            c.chip(str(col.get("inferred") or col.get("dtype") or ""))
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
            c.empty_note("This report no longer exists.")
            return
        c.markdown(engine.read_text(path))


def _built_inspector(name: str) -> None:
    """A table the project built, as the catalog knows it — read like a source.

    The entry `catalog.index_model` writes on every recorded step and every
    build: what the table is (the copilot's read), its notes, its columns with
    whatever was measured, the rows where the session can reach them. **Every
    read a source gets** (2026-09-07): a built table carries as much context for
    the next piece of work as a source does, and until this the window showed a
    source's entry in full and a model's not at all — the Warehouse row opened
    the spec, and the one button that asks for a profile lived on the source
    inspector alone.
    """
    entry = catalog.load_models(APP.portia_dir).get(name)
    with _inspector_scroll():
        _inspector_header(name, _BUILT_NOTE)
        if entry is None:
            c.empty_note("Nothing has built this table yet. Record a step on its spec, or Run.")
            return
        _built_entry(name, entry)


def _built_entry(name: str, entry: dict) -> None:
    """The catalog's entry for a built table: the same groups a source's has."""
    frame = _model_table(entry)
    editing = APP.editing == name
    spec_path = engine.spec_path_for(APP, name)
    with c.kv_list():
        if entry.get("table"):
            c.kv("table", str(entry["table"]))
            c.kv("connection", APP.connection or "")
        rows = (entry.get("indexed") or {}).get("rows")
        if rows is not None:
            c.kv(_rows_label(entry), c.count(int(rows), "row"))
        built = (entry.get("built") or {}).get("at")
        if built:
            c.kv("built", str(built)[:16].replace("T", " "))
    if spec_path is not None:
        c.button("Open the spec", lambda p=spec_path: _open_spec_from_inspector(p), micro=True)
    if not catalog.is_profiled(entry):
        _profile_state(name, entry)
    columns = entry.get("columns") or []
    if editing:
        _edit_interpretation(name, entry, columns)
    else:
        _group("summary", lambda: _summary(entry))
        if entry.get(catalog.NOTES):
            _group("notes", lambda: _notes(entry))
        _group("columns", lambda: _columns(name, columns))
        _interpretation_actions(name, removable=False)
    _table_preview(engine.remote_shape_key(str(entry.get("table") or name), APP), frame)


def _model_table(entry: dict):
    """The built table's rows, or None — nothing written, or the warehouse not open."""
    try:
        return engine.read_model(entry, APP)
    except Exception:  # noqa: BLE001 — a missing preview must not blank the pane
        return None


def _open_spec_from_inspector(path: Path) -> None:
    from portia.ui import artifacts

    artifacts._open_spec(path)


_BUILT_NOTE = "the catalog's entry for this built table"


def _model_inspector(rel: str) -> None:
    """A compiled model, as it sits on disk — the deliverable, read verbatim.

    Rendered from the file rather than recompiled from the spec, for the same
    reason `_run_inspector` reads its markdown off disk: what this shows has to be
    exactly what a reviewer sees in the diff, or committing it was pointless. That
    is also what makes the staleness banner meaningful — it is the difference
    between this file and what the spec would produce now.

    **Under the SQL, what portia knows about the table it produces** — the same
    entry the Warehouse row opens (`_built_inspector`), here so a local or a
    Snowflake project reaches it from the tree.
    """
    path = APP.root / rel
    with _inspector_scroll():
        _inspector_header(path.name, str(path))
        if not path.exists():
            c.empty_note("This model no longer exists. Run to write it again.")
            return
        if path.stem in engine.stale_models(APP):
            _stale_banner(path.stem)
        c.code_block(engine.read_text(path))
        entry = catalog.load_models(APP.portia_dir).get(path.stem)
        if entry is not None:
            c.rule()
            c.caption(_BUILT_NOTE)
            _built_entry(path.stem, entry)


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
            c.empty_note("This file no longer exists.")
            return
        c.text(_UNINDEXED_WHY, color="c-body")
        c.button("Index it", partial(_index, path), kind="primary", icon=c.INDEX_ICON)
        c.caption(_INDEX_SCOPE)
        c.rule()
        _table_preview(engine.file_shape_key(path), engine.read_table(path))


async def _index(path: Path) -> None:
    from portia.ui import artifacts

    ran = await engine.index([path], APP)
    if ran.failed:
        # `engine.index` keeps a failure rather than raising it, so *Profiled*
        # here would be a toast about a file nothing could read.
        ui.notify(
            f"Could not index {path.stem}. {APP.indexing_failed.get(path.stem, '')}",
            type="negative",
        )
        return
    APP.select(SOURCE, path.stem)
    artifacts.pane.refresh()
    pane.refresh()
    ui.notify(f"Profiled {path.stem}.")


#: The explorer's own element id, and its height. A fixed height rather than a
#: flex fill because vis-network measures its container once, on construction,
#: and a container that is still growing when it does gets a canvas of zero.
KNOWLEDGE_CANVAS = "portia-knowledge"
KNOWLEDGE_HEIGHT = "calc(100vh - 220px)"


def _knowledge_inspector() -> None:
    """The knowledge graph, drawn by the library that already does this.

    **It is not the workflow canvas and does not reuse it** (`KNOWLEDGE_GRAPH.md`
    §6.9). That one lays out a DAG of specs by dependency order; this is a graph
    explorer over what the data is to itself, where force layout and hairball
    management are the whole job. The data comes from `engine.knowledge_subgraph`
    and the drawing from `assets/knowledge.js`, so nothing in here computes and
    nothing in the browser holds a database password.

    Two views, and the default is the legible one: tables, groups, what reads
    what, and one edge per pair of tables that share a measured overlap. Columns
    are a toggle because at a real project they are several hundred nodes — worth
    seeing, and not worth seeing first.
    """
    show_columns = APP.knowledge_columns
    data = engine.knowledge_subgraph(APP, columns=show_columns)

    with ui.element("div").classes("stack-sm p-pad w-full"):
        with ui.element("div").classes("row-between"):
            c.pane_title("Knowledge graph")
            with ui.element("div").classes("row-gap-sm"):
                c.segmented(
                    KNOWLEDGE_VIEWS,
                    KNOWLEDGE_VIEWS[1] if show_columns else KNOWLEDGE_VIEWS[0],
                    _pick_knowledge_view,
                )
                # The window must not send anyone to a terminal to see its own
                # graph (`ui/__init__` — the no-terminal bar). This is the same
                # `knowledge.sync` that indexing and `record_step` call.
                c.button("Refresh", _refresh_knowledge, icon="autorenew")
        if data.get("unavailable"):
            # §3.5 — the window has to behave sensibly when the database is down,
            # and sensibly means saying so rather than drawing an empty canvas
            # that reads as "there is nothing here".
            c.empty_note(f"The graph is not reachable: {data['unavailable']}")
            return
        if not data["nodes"]:
            c.empty_note(KNOWLEDGE_EMPTY)
            return

        c.caption(_knowledge_counts(data))
        # The height goes on the div itself, not on NiceGUI's wrapper: vis-network
        # measures its container once, on construction, and a container with no
        # height of its own gets a canvas a few pixels tall.
        ui.html(
            f'<div id="{KNOWLEDGE_CANVAS}" class="p-knowledge" '
            f'style="height:{KNOWLEDGE_HEIGHT}"></div>'
        )
        ui.timer(
            0.05,
            lambda: ui.run_javascript(
                f"window.portiaKnowledge.draw({KNOWLEDGE_CANVAS!r}, {to_json(data)})"
            ),
            once=True,
        )


#: The two views, in the order they are offered. Not a rank: one is fewer nodes,
#: not better ones.
KNOWLEDGE_VIEWS = ("Tables", "Columns")

KNOWLEDGE_EMPTY = "Nothing in the graph yet. Index a source, or press Refresh."


async def _refresh_knowledge() -> None:
    """Rebuild the structural half from the catalog and the specs, and redraw.

    The same `knowledge.sync` indexing and `record_step` call, reachable from
    the window — because a pane whose empty state tells you to open a terminal
    is the bug `ui/__init__` says it is.
    """
    problem = await asyncio.to_thread(engine.sync_knowledge, APP)
    if problem:
        ui.notify(problem, type="warning")
    pane.refresh()


def _pick_knowledge_view(choice: str) -> None:
    APP.knowledge_columns = choice == KNOWLEDGE_VIEWS[1]
    pane.refresh()


def _knowledge_counts(data: dict) -> str:
    """What is on screen, counted. Kinds in schema order, never by size."""
    kinds: dict[str, int] = {}
    for node in data["nodes"]:
        kinds[node["kind"]] = kinds.get(node["kind"], 0) + 1
    shown = " · ".join(f"{n} {kind}" for kind, n in kinds.items())
    edges = f"{len(data['edges'])} edge(s)"
    cut = "  (truncated)" if data.get("truncated") else ""
    return f"{shown} · {edges}{cut}"


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
        box = (
            ui.textarea(placeholder=screens.CONTEXT_PLACEHOLDER, value=APP.project_context)
            .classes("p-field p-editor p-editor--tall w-full")
            .props("borderless")
        )
        screens.context_guidance()
        with ui.element("div").classes("row-gap-sm"):
            c.button("Save", lambda: _save_brief(box.value), kind="primary")
            c.button("Cancel", _back, kind="secondary")


def _save_brief(text: str) -> None:
    from portia.ui import artifacts

    if not (text or "").strip():
        ui.notify("The brief cannot be empty.")
        return
    engine.set_context(text, APP)
    artifacts.pane.refresh()
    pane.refresh()
    ui.notify("Brief saved.")


def _output_inspector(name: str) -> None:
    path = APP.root / engine.OUT_DIR / name
    with _inspector_scroll():
        _inspector_header(name, "a table a run wrote")
        if not path.exists():
            c.empty_note("This file no longer exists.")
            return
        _table_preview(engine.file_shape_key(path), engine.read_table(path))


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
    """Open a step's block, from its card on the canvas or from the block's head.

    One gesture, two places to make it, and the same step either way — which is
    the point of there being one panel now: a card and a block are two views of
    a step, not two things to keep in sync.
    """
    before = APP.selected_step
    APP.pick_step(step_id)
    for picked, row in list(_STEP_ROWS):
        if row.is_deleted:
            continue
        if picked == APP.selected_step:
            row.classes(add=_STEP_SELECTED)
        else:
            row.classes(remove=_STEP_SELECTED)
    for changed in {before, APP.selected_step} - {None}:
        drawn = _BLOCKS.get(str(changed))
        if drawn is None:
            continue
        slot, step, result = drawn
        if slot.is_deleted:
            pane.refresh()
            return
        # The block shutting and the block opening, each in its own slot: the
        # rest of the report, and the canvas, stay as they are.
        slot.clear()
        with slot:
            _step_block(step, result)


def _open_model(name: str) -> None:
    """Open a table's card, and make its spec the one the report half is about.

    Clicking the card of the spec that is already open closes it again, so the
    same gesture opens and collapses. Clicking a different one always opens it —
    you asked to look inside that table, and having to click twice because the
    last click was on something else is the kind of state the canvas should hide.
    """
    already_open = APP.spec_path is not None and APP.spec_path.stem == name
    if already_open and name in APP.expanded:
        APP.expanded = APP.expanded - {name}
    else:
        APP.expanded = APP.expanded | {name}
    if APP.open_input and APP.open_input[0] != name:
        # An unfolded arrow belongs to the card it was unfolded on. Carrying it
        # to the next card would leave a row open on a table nobody asked about.
        APP.open_input = None

    path = engine.spec_path_for(APP, name)
    if path is not None and not already_open:
        engine.select_spec(path, APP)
        APP.select(SPEC, path.name)
        _spec_changed()
    else:
        _canvas_changed()


def _select_source(name: str) -> None:
    """A file node opens its catalog entry, exactly as the left panel does."""
    from portia.ui import artifacts

    APP.select(SOURCE, name)
    # The middle pane is about the file now, so it is drawn whole; the tree
    # only moves its wash.
    pane.refresh()
    artifacts.show_selection()


def _open_input(model: str, table: str) -> None:
    """Unfold one incoming arrow onto the columns that came along it.

    One at a time: this answers *what does this arrow mean*, and that question is
    always about one arrow. Clicking the open one shuts it.
    """
    APP.open_input = None if APP.open_input == (model, table) else (model, table)
    _canvas_changed()


def open_edge(value: str) -> None:
    """A click on an arrow, from `canvas.js` — open its target card at that input.

    The **same answer either gesture reaches**: clicking the card and unfolding
    the row is the deliberate route, clicking the arrow is the direct one, and
    both end on one row of one card rather than on two surfaces that would have
    to be kept saying the same thing.

    The payload is ``target|source``, which is the pair of node ids the edge was
    drawn between. A source node's id is its registered name, which is what the
    column walk calls it too — so no translation is needed and none is done here.
    """
    target, _, source = str(value).partition("|")
    if not target or not source:
        return
    APP.expanded = APP.expanded | {target}
    APP.open_input = (target, source)
    path = engine.spec_path_for(APP, target)
    if path is not None and (APP.spec_path is None or APP.spec_path.stem != target):
        engine.select_spec(path, APP)
        APP.select(SPEC, path.name)
        _spec_changed()
    else:
        _canvas_changed()


def _toggle_visible(name: str, on: bool) -> None:
    """Show or hide one table. **Ancestors follow it in; descendants follow it out.**

    Turning one on adds it, and `graph.ancestry` pulls in what it is built from
    when the canvas is drawn. Turning one off takes its `graph.descendants` with
    it, because a table whose input is gone cannot be drawn honestly — its arrow
    would point at nothing. That is the rule stated once, in both directions:
    what a table needs comes with it, and what needs a table goes with it.
    """
    docs = engine.project_docs(APP)
    chosen = set(APP.visible) if APP.visible is not None else set(docs)
    if on:
        chosen |= {name}
    else:
        chosen -= graph.descendants(docs, [name])
    _set_visible_in_place(frozenset(chosen))


def _reveal_preview() -> None:
    """Put the previewed spec and everything it needs on the canvas.

    **All of it, from clicking any one ghost.** The ghosts are one answer to one
    question — *what would it take to draw this table* — and adding them one at a
    time would mean pressing four cards to see a picture that is only correct
    once all four are there.
    """
    name = APP.previewing
    docs = engine.project_docs(APP)
    if name not in docs:
        APP.previewing = None
        _canvas_changed()
        return
    chosen = set(APP.visible) if APP.visible is not None else set(docs)
    APP.previewing = None
    _set_visible_in_place(frozenset(chosen | graph.ancestry(docs, [name])))


def _select_all(on: bool) -> None:
    """Every table, or none.

    ``None`` rather than the full set for *all*, so the state stays *nothing has
    been chosen* and a table added to the project later is drawn without anyone
    having to re-tick anything (`graph._visible`).
    """
    _set_visible_in_place(None if on else frozenset())


def _set_visible_in_place(visible: frozenset[str] | None) -> None:
    """A tick in the table filter: the canvas redraws, and the open menu stays open.

    **It redrew the whole middle pane** *(until 2026-09-23)*, which rebuilt the
    button the menu hangs from, so every tick shut the menu it was made in and
    narrowing a forty-table canvas was forty trips to the button. Now the
    canvas's contents redraw (`_canvas`), and the menu's rows, its head row and
    the button's count are set in place.
    """
    APP.visible = visible
    engine.remember_view(APP.root, visible)
    button = _VIEW_MENU.get("button")
    if button is None or button.is_deleted:
        pane.refresh()
        return
    docs, placed, reads = _layout()
    _canvas.refresh(placed, docs, reads, _highlit(docs))
    button.set_text(_view_label(len(docs), len(placed.hidden)))
    head = _VIEW_MENU.get("head")
    if head is not None and not head.is_deleted:
        head.set_text(_SELECT_NONE if _all_on(docs) else _SELECT_ALL)
    for name in docs:
        row, tip = _VIEW_MENU.get(f"row:{name}"), _VIEW_MENU.get(f"tip:{name}")
        if row is None or row.is_deleted:
            continue
        on, chosen = _row_state(name, docs)
        row.classes(add="menu-row--on") if on else row.classes(remove="menu-row--on")
        if tip is not None and not tip.is_deleted:
            _silence(tip, not (on and not chosen))


def _result(step_id: str) -> Any | None:
    for result in APP.results or []:
        if result.id == step_id:
            return result
    return None


def spec_label(path: Path | None) -> str:
    return path.name if path else "no spec"


_NO_SPECS = "No specs yet. The copilot writes one as it records steps. Each spec becomes a table."
_NO_STEPS = "No steps yet. The copilot records one for each decision about the data."
#: Said once, above the list — not on every row. The rows carry the narrower
#: fact (`_STEP_NOT_RUN`), which is the one that can differ between them once a
#: spec has been edited since the last run.
_NOT_RUN = "Not run yet. Run the spec to measure these steps."
_STEP_NOT_RUN = "not run"
_ZOOM_IN_TIP = "Zoom in"
_ZOOM_OUT_TIP = "Zoom out"
_RECENTER_TIP = "Reset zoom and centre. Double-click the canvas does the same."
_RESET_LAYOUT_TIP = "Put every card back where the layout places it. Drag a card to move it."
#: The two halves of an open card. Headings, not labels for a control — the card
#: is answering *what arrives here* and *how is this built*, in that order,
#: because the inputs are what the arrows on the canvas are asking about.
_READS = "Reads"
_STEPS = "Steps"
#: Said whether or not anything is filtered, so a narrowed canvas cannot look
#: like a project that lost some tables.
#: Both forms name the filter's *state*, and the button carries a caret so it
#: reads as the way to change it. "9 tables" alone read as a readout.
_SHOWING_ALL = "all {n} tables"
_SHOWING = "showing {n} of {of}"
_VIEW_TIP = "Which tables are on the canvas. Showing one shows everything it is built from."
_VIEW_REQUIRED = "Shown because a table you picked is built from it."
_SELECT_ALL = "Select all"
_SELECT_NONE = "Select none"
_READS_NOTHING = "Reads nothing: no sources and no other tables."
_COLUMNS_UNKNOWN = "columns not known"
_COLUMNS_UNKNOWN_WHY = "Index this source to see which of its columns arrived here."
_NO_STEPS_YET = "No steps recorded on this spec yet."
#: The journal for this model — what was asked on the way to it.
_JOURNAL = "Journal"
_NO_FINDINGS = "nothing recorded for this table yet"
_RECORDED_FOR = "recorded for {spec}"
_STALE_MARK = "measured before {tables} changed"
#: Findings about the tables this model is built from, not recorded for it.
_UNTRACED = "no single input column underneath: {cols}"
_VIA = "recorded at {step}"
#: What a translucent card is. Said on hover because the transparency alone says
#: *not quite here* and not *why*, and the click it invites is not guessable.
_GHOST_WHY = "Not on the canvas. Click to add this table and everything it is built from."
_REPORT_SHUT_TIP = "Hide the report"
_REPORT_OPEN_TIP = "Show the report"
_REPORT_RAIL = "{spec}: report hidden"
_STALE_WHY = (
    "The spec changed after this file was generated. Run the spec to regenerate it. "
    "The .sql is a build output and is not edited by hand."
)
_UNINDEXED_WHY = (
    "This file is readable but not yet profiled. Indexing measures it and writes a catalog entry."
)
_INDEX_SCOPE = "Profiling only, which is free. The copilot reads it on its next exchange."
_NOT_READ = "The copilot has not read this source yet."
_NOT_READ_WHY = (
    "The facts below are measured. No one has written what this data means. "
    "Ask the copilot, or write it yourself."
)
#: The same state on a warehouse table nobody has profiled: the sentence above
#: would claim measurements that do not exist (found driving the window,
#: 2026-09-04). What is below is the warehouse's own types and a row count.
_NOT_READ_WHY_METADATA = (
    "The columns below are the warehouse's own types; nothing has measured them. "
    "No one has written what this data means either."
)
_COLUMNS_ALL = "Show all {n} columns"
_COLUMNS_FEWER = "Show the first {n}"
_COLUMNS_HIDDEN = "{n} more"
_EDIT_SCOPE = "Edits the summary and roles. Measured facts are not changed."
_ASK_HEADING = "What did it miss?"
_ASK_WHY = "The copilot re-reads this source with your note and asks if they disagree."
_ASK_PLACEHOLDER = "e.g. this id is a legacy code, not a customer reference…"
_REMOVE_SCOPE = "Removes the catalog entry, roles and summary. The data file is not touched."
_REMOVE_UNDO = "Re-index from the same path to restore the facts. The prose is not restored."

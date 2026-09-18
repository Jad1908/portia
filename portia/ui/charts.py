"""The middle pane's tab strip, and what a chart tab draws.

`docs/VISUALIZATION.md` §3. The middle pane used to be a dispatcher: read
`APP.selection`, draw one thing, one selection one pane. A chart cannot live in
that, because a chart is consulted **while** answering the copilot's question
(`VISION.md` → *Flow — from indexed to a pipeline*, phase 6) and anything that
replaces the canvas to show it, or hides behind a mode switch while the canvas is
up, breaks the loop at the moment it is meant to close.

So the pane is a strip of tabs. **Tab zero is the canvas and cannot be closed**
(§3.2), which is why it is not a member of `APP.charts`: putting it in the list
would make the empty middle pane representable, and an empty middle pane is a
state with nothing to say and no way out of. `APP.active_tab` is `state.CANVAS`
for it, which is the empty string — a name no chart and no path can take.

The narrower design — only charts get tabs, reached from a pinned row, the rest
of the pane untouched — **was turned down** and §3.1 keeps why. It was cheaper
and it gave the window two ideas of what is on screen, with an arbitrary seam
between them: clicking a saved run replaces what you are looking at while a chart
sits beside it. It also introduced a third tab style, after `pane-tabs` in the
transcript and in `ui/settings.py`. This module uses that same class rather than
growing one.

**The pane splits, and the split is a drag** (§3.7). One tab may be pinned
beside the one you are reading, tab zero included, and `assets/tabs.js` resolves
the whole gesture on the client before anything reaches the server — `pick.js`'s
rule for `pick.js`'s reason. The layout itself is `workflow.pane`'s, because the
half that is not a chart is the selection dispatch and that has always lived
there.

**Nothing here computes**, the rule the whole of `ui/` obeys. The rows arrived
from `ops/sql.py`'s sandbox by way of `ui/engine.py`; the encoding came from the
agent; the drawing is `assets/chart.js`. A panel that wants a number nobody
measured is a signal to add it to `checks`, not to work it out in a widget.
"""

from __future__ import annotations

from pathlib import Path

from nicegui import ui

from portia.core.serialize import to_json_compact
from portia.ui import components as c
from portia.ui.state import (
    APP,
    BRIEF,
    CANVAS,
    FIGURES,
    KNOWLEDGE,
    MODEL,
    OUTPUT,
    RIGHT,
    RUN,
    SOURCE,
    SPEC,
    UNINDEXED,
    Chart,
)

#: The shortest a chart may be dragged, in pixels. Below this the axis labels
#: are taller than the plot and there is nothing left to read.
MIN_CHART_HEIGHT = 140

#: How many tabs the strip draws before it stops growing and scrolls. Not a cap
#: on how many charts may exist (`docs/VISUALIZATION.md` §2.7 — nothing here
#: decides what is too much); the strip scrolls sideways, and this is only where
#: it starts doing so.
STRIP_SCROLLS_AT = 6


def strip(group: str) -> None:
    """One group's tabs, canvas first if it lives here.

    **Two strips, one per half** (§3.7). Each is a `pane-tabs`, the transcript's
    own tab style, rather than a third one — §3.1 turned the narrow design down
    partly for introducing one and that argument did not stop applying when the
    pane split.

    **Drawn only once there is a chart.** A one-tab strip is chrome that says
    nothing: it names the pane you are already looking at, above the pane you are
    already looking at.
    """
    keys = APP.keys(group)
    if not APP.tabs:
        return
    classes = "pane-tabs chart-strip" + (" chart-strip--focus" if APP.focus_group == group else "")
    with ui.element("div").classes(classes) as bar:
        for position, key in enumerate(keys):
            if key == CANVAS:
                _canvas_tab(group, position)
            else:
                chart = APP.chart(key)
                if chart is not None:
                    _chart_tab(chart, group, position)
        # The rest of the bar is a drop target too, so *put it at the end* has
        # somewhere to land that is not a tab. Without it the only way to reorder
        # to last is to aim at the right half of the last tab, which is a pixel
        # game (`assets/tabs.js` resolves the halves).
        ui.element("div").classes("chart-strip-rest")
        if group == RIGHT:
            _close_group()
    bar.props(f"data-strip={group}")


def _close_group() -> None:
    """The way back to one pane. It shuts a *half*, never a tab (§3.7).

    Kept when the split became two real groups, because the alternative is
    closing tabs one at a time until the half collapses — and the tabs are not
    what you wanted rid of. `state.App.unsplit` moves them, so nothing is lost.
    """
    close = ui.icon("close").classes("chart-tab-close split-close")
    close.tooltip(_UNSPLIT_TIP)
    close.on("click.stop", _unsplit)


def _tab_classes(key: str, group: str) -> str:
    """What a tab looks like given where, if anywhere, it is drawn.

    Three states and they are different questions. *Active* is the tab its group
    is drawing. *Focus* is which group a new chart would open in, and it is what
    the strip's own class says rather than the tab's. *Preview* is the italic
    one: opened by a single click and about to be replaced by the next
    (`state.App.open_tab`).
    """
    classes = "pane-tab"
    if APP.active.get(group) == key:
        classes += " pane-tab--active"
    chart = APP.chart(key)
    if chart is not None and chart.preview:
        classes += " pane-tab--preview"
    return classes


def _draggable(element, group: str, position: int) -> None:
    """Mark a tab as something you can drag — to reorder it, or to move it (§3.7).

    **The position, not the key.** A chart's name is a sentence the agent wrote
    and a figure's path is a path; either would have to be quoted through
    NiceGUI's prop parser and read back out of an attribute, which is the seam
    that has already cost this module two bugs. A group and a small integer have
    nothing to escape, and `state.App.tab_at` turns them back into a key at the
    far end.
    """
    element.props(f"draggable=true data-tab={position} data-tab-group={group}")


def _canvas_tab(group: str, position: int) -> None:
    """Tab zero, named for whatever it is currently holding.

    It is *not* always the canvas: phase 1 leaves the existing selection dispatch
    inside this tab, so it may be a source, a saved run or the brief. Labelling it
    "Pipeline" regardless would be the strip's one job done wrong. The selection's
    own name is what it says, and "Pipeline" is the answer when nothing is
    selected and the canvas is up.

    **No close control**, and that is the one way it differs from every other tab
    (§3.2). It can move between groups and take a position, and it cannot go
    away: an empty middle pane is a state with nothing to say and no way out of.
    """
    with ui.element("div").classes(_tab_classes(CANVAS, group)) as element:
        ui.icon(_canvas_icon()).classes("chart-tab-icon")
        ui.label(_canvas_label()).classes("chart-tab-name")
    _draggable(element, group, position)
    element.on("click", _show_canvas)


def _canvas_label() -> str:
    """What tab zero is about, in as few characters as say it.

    It is *not* always the canvas — phase 1 left the selection dispatch inside
    this tab — so a source, a saved run or the brief is what it says while one of
    those is open. A saved figure is no longer among them: a figure has a tab of
    its own now (§6.4).
    """
    _, name = APP.selection or ("", "")
    return name or _CANVAS_TAB


def _canvas_icon() -> str:
    """The glyph for whatever tab zero is holding, not for the canvas.

    **The label was already dynamic and the icon was not**, so a tab reading
    `orders` still carried the pipeline's own `account_tree` and read as the
    Pipeline tab with a strange name on it *(2026-09-03, reported from a real
    session)*. Half a dynamic control is worse than none: the label says one
    thing and the glyph beside it says another, and a glyph is what the eye
    reaches first.

    The mapping is `tree`'s, which is where the left pane gets the same icons —
    one idea of what a source looks like in this window rather than two.
    """
    kind, _ = APP.selection or ("", "")
    return _TAB_ICON.get(kind, "account_tree")


def _chart_tab(chart: Chart, group: str, position: int) -> None:
    """One chart's tab: what it is, how it is doing, and the way to shut it.

    The state mark is a *kind* and never a rank (`DESIGN.md`): a pending chart
    and a failed one are two different things that happened, and neither is drawn
    as worse than a chart that came out. It reuses the transcript's dot for the
    same reason the strip reuses `pane-tabs` — one idea of "this tab has
    something going on" in the window rather than two.

    **A saved figure is a tab like any other** (§6.4), and the only thing that
    says so is the glyph — a picture that is on disk and one that is not are the
    same picture, and drawing the unsaved one as lesser is the rank `DESIGN.md`
    forbids. Where it is kept is written under the chart, where it is a fact you
    can read rather than a badge you have to learn.

    A **double click pins a preview**, which is the editor gesture and the same
    one that opens a row for keeps on the left. There is no `dblclick` handler
    here for `components.artifact_row`'s reason — the pane is replaced between
    the two presses — so `assets/tabs.js` resolves it and sends one event.
    """
    with ui.element("div").classes(_tab_classes(chart.key, group)) as element:
        if chart.pending:
            ui.element("div").classes("pane-tab-dot pane-tab-dot--waiting").tooltip(_PENDING)
        elif chart.error:
            ui.icon("error_outline").classes("chart-tab-icon chart-tab-icon--failed")
        else:
            ui.icon(_SAVED_ICON if chart.saved else "insert_chart_outlined").classes(
                "chart-tab-icon"
            )
        ui.label(chart.name).classes("chart-tab-name").tooltip(chart.question or chart.name)
        close = ui.icon("close").classes("chart-tab-close")
        close.tooltip(_CLOSE_TIP)
        close.on("click.stop", lambda k=chart.key: _close(k))
    _draggable(element, group, position)
    # A tab the copilot just opened slides in once; a strip rebuilt by the next
    # refresh stays still (`c.enters`). Keyed by the chart, so moving a tab
    # between groups is a move, not an arrival.
    c.enters(element, f"tab:{chart.key}")
    element.on("click", lambda k=chart.key: _show(k))


def pane(chart: Chart, group: str = "") -> None:
    """A chart tab's contents: the question, the picture, and the query under it.

    Three states and each is drawn as itself. **Pending is not an empty chart**
    and **failed is not an error dialog** — a failed query is a failed tab that
    says what broke and stays open, because closing it would take the SQL with
    it, and the SQL is what you want to read when a query fails.

    The picture draws in one pass with the rest of the pane (§3.6). The rows are
    already here by then: `ui/engine.py` ran the query on a thread and refreshed
    the pane when they landed, which is the only place in `ui/` that knows there
    is a thread.

    **One function draws a fresh chart and a saved one**, which is what §6.4's
    single artifact means on screen. A figure off disk arrives here as a `Chart`
    carrying its rows (`open_figure`), so the only difference between the two is
    what a saved one has *extra*: the note somebody typed and the date it was
    written. Two renderers would be two opinions about what a chart looks like,
    and the one you would find out about is the one you use less.
    """
    with c.scroll_area(f"chart:{chart.key}", classes="p-pad stack-lg chart-scroll"):
        _chart_header(chart)
        if chart.notes:
            c.markdown(chart.notes)
        if chart.pending:
            c.caption(_RUNNING)
        elif chart.error:
            _failed(chart)
        else:
            _figure(chart, group)
        _keep(chart)
        _query(chart)


def _keep(chart: Chart) -> None:
    """Keeping a figure — the one durable thing this module produces (§6).

    **It writes a figure file, not a finding** *(2026-09-03)*. The journal keeps
    a conclusion; this keeps the picture, with the rows in it, so it opens again
    with nothing running.

    Three states, and the order matters. A chart that is only drawn shows one
    control. Pressing it opens the form. Once written, the control is replaced by
    where it went — because pressing Keep twice on one chart is a way to two
    copies of one picture, and the honest guard is that there is no second press.

    Nothing to keep on a pending or failed chart: there is no picture yet.
    """
    if not chart.drawable:
        return
    if chart.saved:
        with ui.element("div").classes("chart-kept"):
            ui.icon("check").classes("chart-kept-icon")
            # The date comes off the file and is empty on one saved a moment
            # ago, which is right: *when* is what you want about a picture you
            # are opening again, and nothing about one you just kept.
            c.caption(_KEPT.format(path=chart.path) + (_AT.format(at=chart.at) if chart.at else ""))
        return
    if not chart.keeping:
        c.button(_KEEP, lambda: _open_keep(chart), icon="bookmark_add", split=True).tooltip(
            _KEEP_TIP
        )
        return
    _keep_form(chart)


def _keep_form(chart: Chart) -> None:
    """A note, and — folded away — where to put it.

    **The folder was a second borderless box under the notes box and it read as a
    second thing you had to fill in** *(2026-09-03)*. Two identical-looking
    fields where one is a sentence and the other is a path is a form that makes
    you stop and work out which is which.

    So the note is the form, and *where* is a disclosure that is shut by default
    — the top level is the right answer nearly every time. Opened, it is a
    **picker of the folders that exist** plus a field for a new one, which is the
    shape this job actually has: usually you are choosing, occasionally you are
    naming.
    """
    from portia.ui import engine

    with ui.element("div").classes("chart-keep stack-md"):
        notes = (
            ui.textarea(placeholder=_NOTES_HINT)
            .classes("p-field p-editor w-full")
            .props("borderless autogrow autofocus")
        )
        notes.bind_value(chart, "keep_notes")
        _keep_where(chart, engine.figure_folders(APP))
        if chart.keep_error:
            ui.label(chart.keep_error).classes("t-body-sm c-error pre-wrap")
        with ui.element("div").classes("chart-keep-actions"):
            c.button(_SAVE, lambda: _save_keep(chart), kind="primary")
            c.button(_CANCEL, lambda: _close_keep(chart))


def _keep_where(chart: Chart, folders: list[str]) -> None:
    """Where it lands, as a line you can read and a control you can open.

    Shut, it is a sentence naming the destination — which is the only thing you
    need most of the time, and is a fact rather than a field. Open, it is the
    existing folders as a segmented choice, and one box for a name that does not
    exist yet.
    """
    with ui.element("div").classes("chart-where"):
        row = ui.element("div").classes("chart-where-line")
        with row:
            ui.icon("folder_open").classes("chart-where-icon")
            c.caption(_WHERE.format(folder=chart.keep_folder or _TOP_LEVEL))
            ui.element("div").classes("flex-1")
            ui.icon("expand_more" if chart.keep_where else "chevron_right").classes(
                "chart-where-icon"
            )
        row.on("click", lambda: _toggle_where(chart))
        if not chart.keep_where:
            return
        with ui.element("div").classes("chart-where-body stack-sm"):
            # Not `c.segmented`, which labels each option with `str(option)` —
            # the gallery's top level is the empty string and would render as a
            # button with nothing written on it.
            with ui.element("div").classes("row-gap-xs"):
                for folder in folders:
                    picked = folder == chart.keep_folder
                    button = c.button(
                        folder or _TOP_LEVEL,
                        lambda f=folder, ch=chart: _pick_folder(ch, f),
                        micro=True,
                    )
                    if picked:
                        button.classes("seg-active")
            new = (
                ui.input(placeholder=_NEW_FOLDER)
                .classes("p-field w-full")
                .props("borderless dense")
            )
            new.bind_value(chart, "keep_folder")


def _toggle_where(chart: Chart) -> None:
    chart.keep_where = not chart.keep_where
    _refresh()


def _pick_folder(chart: Chart, folder: str) -> None:
    chart.keep_folder = folder
    _refresh()


def open_figure(path: str, *, preview: bool = False) -> None:
    """Open a saved figure — in a tab, like every other picture (§6.4).

    **This is what the file buys.** A finding keeps the sentence and the SQL,
    which re-runs; a figure keeps the rows, so opening one costs a read and works
    when the source has moved, the database is down, or the chat that drew it
    ended three weeks ago.

    Reached twice: from the gallery, and by saving the chart you are reading —
    which does not come through here at all, because that chart is already on the
    strip and already holds its rows (`state.App.figure_saved`). Opening one that
    is already in a tab focuses it rather than reading the file again: two tabs
    on one file is the artifact-in-two-places problem §6.4 settled.
    """
    from portia.ui import artifacts, engine

    if APP.chart(path) is not None:
        APP.open_tab(path, preview=preview)
        _refresh()
        return
    try:
        saved = engine.load_figure(APP, path)
    except (OSError, ValueError):
        ui.notify(_FIGURE_GONE)
        artifacts.pane.refresh()
        return
    APP.show_chart(
        Chart(
            name=saved.get("name") or Path(path).stem,
            question=saved.get("question", ""),
            vega=saved.get("vega") or {},
            rows=saved.get("rows") or [],
            columns=saved.get("columns") or [],
            sql=saved.get("sql", ""),
            inputs=saved.get("inputs") or [],
            path=path,
            notes=saved.get("notes", ""),
            at=saved.get("at", ""),
            preview=preview,
        )
    )
    _refresh()


def gallery_pane() -> None:
    """What tab zero says when the gallery row itself is what is selected."""
    c.empty_note(_GALLERY_EMPTY)


def _chart_header(chart: Chart) -> None:
    """What was asked, above what came back.

    The question rather than the tab name, and both are on screen because they
    are different lengths of the same thing: the name is what fits on a tab, and
    the question is what the agent actually wanted to know. `query_data` requires
    one for the same reason (`FINDINGS.md` §4), and a chart with no question is a
    picture nobody can review.
    """
    with ui.element("div").classes("stack-xs"):
        c.pane_title(chart.name)
        if chart.question:
            c.caption(chart.question)


def _figure(chart: Chart, group: str = "") -> None:
    """The chart itself, handed to the client as rows plus portia's encoding.

    **Not as a renderer's spec** (§5). `assets/chart.js` composes the Vega-Lite
    spec from this, exactly as `assets/knowledge.js` composes a vis-network graph
    from ``kind`` and ``properties``, so swapping the library is one file and the
    server never learns a third-party schema.

    The payload is written into the DOM rather than pushed with a
    ``run_javascript`` during the render, which is the rule `canvas.js` and
    `scroll.js` document: driving the client from inside a render races the DOM
    patch NiceGUI is in the middle of applying.

    **It rides in a hidden child as text, not in a prop**, and both halves of
    that cost a bug. NiceGUI's prop parser keeps a quoted value whole only
    while the value holds no quote of its own kind (`components.prop_value`,
    which measured what an earlier version of this comment got wrong), and a
    JSON document is nothing but quoted values with quotes inside them.
    What arrived in the browser was the payload encoded twice, as a JSON *string*
    holding indented JSON, which `JSON.parse` refused at the first newline. The
    obvious fix, a ``<script type="application/json">``, is refused by
    `ui.html` outright. So it is a label: text content, escaped by Vue on the way
    out and read back verbatim with ``textContent``, with no length limit and no
    parser between here and `JSON.parse`.
    """
    if not chart.rows:
        c.empty_note(_NO_ROWS)
        return
    # **It fills the pane unless somebody dragged it** *(2026-09-03)*. A fixed
    # height left a tall window mostly empty and made the picture the smallest
    # thing on a screen that exists to show it; a dragged one is a decision and
    # is kept. `assets/chart.js` redraws on either, because a splitter drag never
    # reaches the server and a canvas painted at the old width keeps it.
    figure = ui.element("div").classes("chart-figure")
    if chart.height:
        figure.classes("chart-figure--fixed").style(f"height:{chart.height}px")
    # The picture arrives, once — the answer to "generated plots just pop onto
    # the screen". A redrawn figure keeps its key and keeps still; the blank
    # while Vega embeds is `chart.js`'s ghost, not this.
    c.enters(figure, f"figure:{chart.key}")
    with figure:
        ui.label(payload(chart)).classes("chart-data")
    grip = ui.element("div").classes("chart-grip")
    grip.props(f"data-grip={group or 'left'}")
    grip.tooltip(_GRIP_TIP)
    c.caption(_ROW_COUNT.format(rows=f"{len(chart.rows):,}"))


def payload(chart: Chart) -> str:
    """Everything `assets/chart.js` needs, as one JSON document.

    Its own function because it is the part that broke and the part a test can
    reach without a browser: what reached the client was JSON encoded twice, and
    `JSON.parse` refused it. Compact rather than indented for the reason
    `core.serialize` gives — nobody reads this — and because the newlines were
    half of what made the first version unparseable.
    """
    return to_json_compact(
        {
            # What the client sends back if it cannot draw this (§11.1). In the
            # payload rather than as a `data-` attribute because the payload is
            # already here and already parsed — and because a prop holding a
            # chart name would hit exactly the whitespace problem this function's
            # docstring is about.
            "key": chart.key,
            "vega": chart.vega,
            "columns": chart.columns,
            "rows": chart.rows,
        }
    )


def _failed(chart: Chart) -> None:
    """What broke, in the operator's terms, with the tab left open.

    There is no retry button (§8). Retrying an identical query that failed is a
    press with one possible outcome; the way forward is to tell the copilot, and
    it is sitting in the pane to the right.
    """
    with ui.element("div").classes("stack-sm"):
        ui.label(_FAILED).classes("t-heading-sm c-error")
        c.code_block(chart.error or "")


def _query(chart: Chart) -> None:
    """The SQL underneath, folded.

    Folded because the picture is the answer and the query is the evidence for
    it, which is the order every other surface in this app puts them in. On
    screen at all because §2.4 is only checkable if the ``GROUP BY`` is readable:
    a chart whose aggregate you cannot see is a number you have to take on trust.
    """
    if not chart.sql:
        return

    def body() -> None:
        c.code_block(chart.sql)
        if chart.inputs:
            c.caption(_INPUTS.format(inputs=", ".join(chart.inputs)))

    c.collapsed(_QUERY_LABEL, body)


# --- gestures ---------------------------------------------------------------


def _show(key: str) -> None:
    """A click on a tab: show it, where it is. Never move it, never un-split."""
    APP.focus_tab(key)
    _refresh()


def _show_canvas() -> None:
    APP.show_canvas()
    _refresh()


def _open_keep(chart: Chart) -> None:
    chart.keeping = True
    chart.keep_error = ""
    _refresh()


def _close_keep(chart: Chart) -> None:
    """Abandon the form, keeping what was typed.

    The text survives on the chart rather than being cleared, so shutting the
    form to look at the picture again is not the same gesture as throwing the
    note away. There is no discard control because there is nothing to discard
    from: nothing has been written.
    """
    chart.keeping = False
    chart.keep_error = ""
    _refresh()


def _save_keep(chart: Chart) -> None:
    """Write it, or say why not — in place, with what was typed still in the box."""
    error = keep(chart)
    if error:
        chart.keep_error = error
    _refresh()


def keep(chart: Chart) -> str:
    """Save a chart as a figure, with whatever its form holds. Returns the error, or "".

    **One save path for two presses** *(2026-09-04)*. The form's *Save* and the
    gallery row's quick save both end here, because the second was asked for
    and a second copy of what saving means is how the two drift: one would
    rekey the tab and the other would not. The two callers differ only in where
    they put the error: the form keeps it beside the box, the row has no box
    and says it in a notification.

    A quick save carries **no note and lands at the top level**, unless the form
    on that chart was opened and left half-filled, in which case what was typed
    is what is saved. Not a second setting, just the same fields read once.
    """
    from portia.ui import engine

    try:
        path = engine.save_figure(APP, chart, notes=chart.keep_notes, folder=chart.keep_folder)
    except Exception as exc:  # noqa: BLE001 - handed to the caller, not swallowed
        return str(exc)
    # **The chart becomes the file** (§6.4). One artifact, which is what
    # retiring the session chart was after — and the tab stays, which retiring it
    # got wrong: pressing *keep* on a picture is not a request to be taken away
    # from it. What the user typed goes onto the record it was written into, so
    # the pane below draws the note off the same field a figure opened from disk
    # carries.
    chart.notes = chart.keep_notes.strip()
    APP.figure_saved(chart, str(path.relative_to(APP.root)))
    return ""


def _close(key: str) -> None:
    """Shut a tab. The chart survives in the left pane's list (§3.5)."""
    APP.close_chart(key)
    _refresh()


def open_figure_tab(path: str, *, reveal: bool) -> None:
    """A click on a figure in the gallery. One press previews, two keep it.

    `assets/pick.js` decides which gesture it was, for `pick.js`'s reason: acting
    on the first press rebuilds the left pane and moves the rows under a
    stationary cursor, so the second press is about a different row.
    """
    if APP.chart(path) is None:
        open_figure(path, preview=not reveal)
        return
    APP.open_tab(path, preview=not reveal)
    _refresh()


def open_chart_tab(key: str, *, reveal: bool) -> None:
    """The same, for a chart this session drew and nobody has saved."""
    APP.open_tab(key, preview=not reveal)
    _refresh()


def move(source: str, index: int, target: str, at: int | None) -> None:
    """A tab was dropped somewhere — reorder, move, or open the other half (§3.7).

    Public because the event arrives at page level: `assets/tabs.js` emits one
    event with the gesture already resolved, and `app.py` registers the handler
    there for the reason every client event is registered there — inside a
    refreshable it would be registered again on every refresh.

    **One entry point for three gestures**, because they are one gesture with
    three destinations. The client knows which strip the cursor was over and
    between which two tabs it let go; this does not have to.

    The index is the tab's position on its strip rather than its key, and
    `state.App.tab_at` is where that is argued.
    """
    key = APP.tab_at(source, index)
    if key is None:
        return
    APP.move_tab(key, target, at)
    _refresh()


def pin(group: str, index: int) -> None:
    """A double click on a tab keeps it — VS Code's rule for a preview tab."""
    key = APP.tab_at(group, index)
    if key is None:
        return
    APP.pin_tab(key)
    _refresh()


def resized(group: str, height: int) -> None:
    """The grip under a chart was dragged. One event, at the end of the drag.

    The height is the **client's** while the drag is happening — `assets/chart.js`
    sets it on the element every frame, because a round trip per pixel would make
    the one directly-manipulated thing in the pane the laggiest. What reaches
    here is where it was let go, so the number survives the next pane refresh.
    """
    chart = APP.showing(group)
    if chart is None:
        return
    chart.height = max(MIN_CHART_HEIGHT, int(height))
    _refresh()


def render_failed(key: str, message: str) -> None:
    """The browser could not draw a chart portia published (§11).

    Three readers, and this hands the fact to each of them. **The chart** gets
    it as its `error`, so the tab and the gallery row agree with what the figure
    has been saying on its own since §8 — a failure the middle pane knew about
    and the left pane did not was the same silence one pane further out.
    **The log** gets it because `runlog` tees at the edges and a reply written
    over a chart nobody saw is exactly what `devtools.audit` exists to make
    visible. **The copilot** gets it on its next receipt, via `drawn`, which is
    the one place that can hold a fact arriving after the tool call it belongs to
    (§11.2).

    Idempotent by construction: a tab is a chart (§3.3), so a second failure for
    the same key overwrites the first rather than accumulating.
    """
    from portia.agent import drawn
    from portia.ui import exchange

    chart = APP.chart(key)
    if chart is None:
        return
    chart.error = message
    chart.pending = False
    drawn.report_failure(key, message)
    exchange.note_chart_failed(chart)
    _refresh()


def _unsplit() -> None:
    APP.unsplit()
    _refresh()


def refresh() -> None:
    """Redraw both panes the strip speaks for — public for `artifacts._quick_save`."""
    _refresh()


def _refresh() -> None:
    """Redraw both panes the strip speaks for.

    **The left pane too, and leaving it out was a bug.** Every gesture here
    changes something the `Charts` rows are drawing: which chart is selected, and
    whether it still has a tab. Refreshing only the middle pane left a closed
    chart labelled *open* in the list that exists to say where it went, and left
    the `Pipeline` row unlit when a close had just put the canvas back on screen.

    Imported here rather than at module scope: `workflow` draws this strip and
    `artifacts` reaches back into it, so a top-level import is a cycle.
    """
    from portia.ui import artifacts, workflow

    workflow.pane.refresh()
    artifacts.pane.refresh()


#: What tab zero holds, as a glyph. Keyed by `state`'s selection kinds; the
#: fallback is the canvas, which is what an empty selection means.
_TAB_ICON = {
    SOURCE: "table_chart",
    UNINDEXED: "table_chart",
    MODEL: "code",
    OUTPUT: "description",
    RUN: "description",
    BRIEF: "notes",
    KNOWLEDGE: "hub",
    SPEC: "account_tree",
    FIGURES: "photo_library",
}

#: A picture that is on disk. A *kind* and not a rank (`DESIGN.md`): it says
#: this one is kept, not that it is better than the chart beside it, and it is
#: the same bookmark the control that keeps it carries.
_SAVED_ICON = "bookmark"

_CANVAS_TAB = "Pipeline"
_KEEP = "Keep"
_KEEP_TIP = "Save this figure to the project"
_NOTES_HINT = "Notes, optional"
_WHERE = "Saving to {folder}"
_TOP_LEVEL = "Figures"
_NEW_FOLDER = "New folder name"
_SAVE = "Save"
_CANCEL = "Cancel"
_KEPT = "Saved to {path}"
_AT = " · {at}"
_GALLERY_EMPTY = "No figures saved. Keep a chart to save it here."
_FIGURE_GONE = "This figure is no longer in the gallery."
_CLOSE_TIP = "Close tab. The chart stays listed under Figures."
_UNSPLIT_TIP = "Close the split"
_PENDING = "Running the query"
_RUNNING = "Running the query…"
_NO_ROWS = "The query returned no rows. Nothing to draw."
_ROW_COUNT = "{rows} rows"
_FAILED = "Query failed"
_QUERY_LABEL = "Query"
_GRIP_TIP = "Drag to resize the chart"
_INPUTS = "Reads {inputs}"

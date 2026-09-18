"""The window: the toolbar, the three panes, and which screen is showing.

This is an **edge, like the CLI**. It calls the engine only through `engine.py`
and never computes anything itself: if a pane wants a number the engine doesn't
expose, that is a signal to add it to `checks`/`spec`, not to calculate it in a
widget. `cli/` and `ui/` are two renderers of one engine, and the day they
disagree about a number is the day the seam broke.

The load-bearing surface is three panes — files & artifacts, workflow,
transcript — with the canvas running continuously behind all of them and a 1px
hairline where they meet. The screens before it (`screens.py`) are the only place
the layout is not three panes.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from nicegui import app as nicegui_app
from nicegui import context, ui
from nicegui.client import Client

from portia import pipeline
from portia.core import cancel, present
from portia.ui import artifacts, engine, screens, settings, state, theme, transcript, workflow
from portia.ui import components as c
from portia.ui.state import APP

TITLE = "portia"

#: How often the elapsed number moves. One second because that is the resolution
#: `present.duration` prints at above ten seconds, and a clock that redraws faster
#: than it can change is a refresh nobody sees.
TICK_SECONDS = 1.0


def _stop_local_server() -> None:
    """Stop the llama.cpp server this window started (`PROVIDERS.md` §4.9).

    Registered here, where every entry point that draws the window arrives,
    rather than only in `ui/__main__`: a script that calls `ui.run` itself
    (`sandbox/providerpickdrive.py`) never ran `__main__`'s hook and left a
    10 GB server behind (2026-09-14). And a NiceGUI shutdown hook rather than
    `atexit` alone, because uvicorn ends a SIGTERM by re-raising it with the
    default handler, which exits without running `atexit` at all.
    """
    from portia.agent.providers import llamacpp

    llamacpp.stop()


nicegui_app.on_shutdown(_stop_local_server)


@ui.page("/")
def page() -> None:
    theme.apply()
    ui.page_title(TITLE)
    # At page level, deliberately: a dialog built inside a refreshable is deleted
    # by the first refresh (see `screens.build_add_dialog`).
    screens.build_add_dialog()
    screens.build_decision_dialog()
    screens.build_connect_dialog()
    screens.build_server_dialog()
    settings.build_dialog()
    # `DESIGN.md` → Width behaviour, which cannot be done in CSS once the panes
    # are inside splitters: a splitter sets an inline pixel width on its panel, so
    # restyling the pane inside changes nothing about the space reserved beside it.
    ui.on("portia:viewport", _resized)
    # An arrow on the canvas is clickable, and the click comes back through the
    # same client→server seam the viewport does. At page level for the same
    # reason: a handler registered inside a refreshable is registered again on
    # every refresh.
    ui.on("portia:edge", _edge_clicked)
    # A click on a spec row, with the gesture already resolved by the client —
    # see `assets/pick.js` for why neither the browser nor Python can do it.
    ui.on("portia:spec", _spec_picked)
    # A click on a gallery row, with one press told from two (`assets/pick.js`).
    ui.on("portia:opens", _row_opened)
    # A figure dragged into a folder, likewise already resolved
    # (`assets/gallery.js`). At page level for the same reason as the rest.
    ui.on("portia:figure-move", _figure_moved)
    # A tab dropped somewhere — reordered, moved to the other half, or opening
    # one — likewise already resolved (`assets/tabs.js`).
    ui.on("portia:tab-move", _tab_moved)
    ui.on("portia:tab-pin", _tab_pinned)
    # The grip under a chart, let go of (`assets/chart.js`).
    ui.on("portia:chart-height", _chart_resized)
    # A card on the canvas, let go of somewhere else (`assets/canvas.js`). The
    # client moved it and its arrows live; the server records where it landed.
    ui.on("portia:card-move", _card_moved)
    # A chart the browser could not draw (`docs/VISUALIZATION.md` §11). The one
    # thing the client reports that is not a gesture: the renderer refused a spec
    # portia's own guard passed, and until this existed the failure lived in a
    # `<div>` and nowhere else — not the server, not the log, not the copilot,
    # which by then held a receipt saying the chart was drawn.
    ui.on("portia:chart-failed", _chart_failed)
    # At page level for the same reason the dialogs are: built inside a
    # refreshable it would be rebuilt — and left running — on every refresh.
    # It costs one predicate a second when nothing is running (`tick_progress`).
    ui.timer(TICK_SECONDS, tick_progress)
    # The same second, for the clock beside a running tool call
    # (`transcript.tool_clock`). One predicate a second when nothing runs.
    ui.timer(TICK_SECONDS, transcript.tick_clocks)
    # The copilot's charts, which do not arrive through the event stream: their
    # rows never reach the model, so they never reach the transcript either
    # (`docs/VISUALIZATION.md` §2.3). At page level for the reason the timer is —
    # inside a refreshable it would subscribe again on every refresh.
    from portia.ui import exchange

    exchange.listen_for_charts()
    # A project opened from `--project` names its warehouse before the loop
    # exists; the session is opened once it does (`docs/CONNECTOR.md` §2.4).
    ui.timer(0.2, artifacts.connect_in_background, once=True)
    # What each local provider serves, read once off the loop so the picker
    # has the list the moment someone switches to it (`docs/PROVIDERS.md` §5).
    ui.timer(0.3, transcript.list_models_in_background, once=True)
    shell()


def _edge_clicked(event) -> None:
    """A click on an arrow: open its target card at that input's columns."""
    workflow.open_edge(event.args or "")


def _card_moved(event) -> None:
    """A card was dragged and dropped. One event, the whole gesture."""
    payload = event.args or {}
    name = str(payload.get("id") or "")
    if name:
        workflow.move_card(name, int(payload.get("dx") or 0), int(payload.get("dy") or 0))


def _figure_moved(event) -> None:
    """A figure was dropped on a folder. One event, the whole gesture."""
    from portia.ui import artifacts

    payload = event.args or {}
    figure = str(payload.get("figure") or "")
    if figure:
        artifacts.move_figure(figure, str(payload.get("folder") or ""))


def _tab_moved(event) -> None:
    """A tab was dropped. One event, the whole gesture (`assets/tabs.js`)."""
    from portia.ui import charts

    args = event.args or {}
    source, target = str(args.get("from") or ""), str(args.get("to") or "")
    index, at = args.get("index"), args.get("at")
    if isinstance(index, (int, float)):
        charts.move(source, int(index), target, None if at is None else int(at))


def _tab_pinned(event) -> None:
    """A double click on a tab keeps a preview."""
    from portia.ui import charts

    args = event.args or {}
    index = args.get("index")
    if isinstance(index, (int, float)):
        charts.pin(str(args.get("group") or ""), int(index))


def _chart_resized(event) -> None:
    """The grip under a chart was let go of, at this height."""
    from portia.ui import charts

    args = event.args or {}
    height = args.get("height")
    if isinstance(height, (int, float)):
        charts.resized(str(args.get("group") or ""), int(height))


def _chart_failed(event) -> None:
    """Vega-Lite refused a spec in the browser. Tell the chart, the log and the agent."""
    from portia.ui import charts

    args = event.args or {}
    tab = str(args.get("tab") or "")
    if tab:
        charts.render_failed(tab, str(args.get("message") or ""))


def _row_opened(event) -> None:
    """A gallery row was clicked once (preview) or twice (keep it)."""
    from portia.ui import artifacts

    args = event.args or {}
    opens = str(args.get("opens") or "")
    kind, _, ident = opens.partition(":")
    if kind and ident:
        artifacts.opened(kind, ident, reveal=bool(args.get("reveal")))


def _spec_picked(event) -> None:
    """A click or a double click on a spec row, already told apart."""
    from portia.ui import artifacts

    args = event.args or {}
    spec = args.get("spec")
    if spec:
        artifacts.pick_spec(spec, reveal=bool(args.get("reveal")))


def _resized(event) -> None:
    """Apply the width band's defaults, and only when the band actually changes.

    Resizing within a band leaves the panes as you left them: a layout that keeps
    reopening a pane you just closed is worse than one that never adapts.
    """
    width = int(event.args or 0)
    if width and APP.resize(width):
        shell.refresh()


@ui.refreshable
def shell() -> None:
    """Which of the four screens is showing. The context panel is the one gate."""
    if not APP.opened:
        screens.project_open()
    elif not engine.has_context(APP):
        screens.project_context()
    elif APP.needs_data_choice:
        screens.choose_data()
    elif APP.on_add_data:
        screens.add_data()
    else:
        _window()


#: Pane sizes, **in pixels rather than percent**. A percentage minimum means the
#: floor moves with the window, and the transcript — which holds the question form
#: and the write confirmation, the two things this app exists for — could be
#: dragged down to a few characters wide. These minimums are the width at which
#: each pane is still worth having — and therefore the point at which dragging
#: further **closes** it, which is how you get rid of one (`_splitter`).
#:
#: **Lowered 2026-08-02, because the floor doubles as the close threshold and
#: they were closing under a drag that meant "make this narrower".** 200 and 330
#: were written when the only way to close a pane was a toolbar toggle, so being
#: generous cost nothing; once the floor became the gesture, a generous floor
#: reads as a pane that gives up. Both are still real floors — 150 holds a file
#: name at the tree's indent, and 260 holds the `question-form`'s option rows.
FILES_WIDTH, FILES_LIMITS = 260, (150, 520)
TRANSCRIPT_WIDTH, TRANSCRIPT_LIMITS = 400, (260, 780)

#: The width below which the workflow pane stops being worth having. It is the
#: one pane that never gives way (`DESIGN.md` → Width behaviour), so this is the
#: floor every other pane's ceiling is computed against.
WORKFLOW_MIN = 320


def _window() -> None:
    with ui.element("div").classes("p-window"):
        toolbar()
        with ui.element("div").classes("p-body"):
            if APP.show_files:
                with _splitter(FILES_WIDTH, _files_limits(), on_collapse=_close_files) as files:
                    with files.before:
                        _left()
                    with files.after:
                        _workflow_and_transcript()
            else:
                _rail("Files", "folder", "chevron_right", _open_files)
                _workflow_and_transcript()


def _workflow_and_transcript() -> None:
    if not APP.show_transcript:
        # A row, so the rail sits beside the workflow pane rather than under it.
        # `p-pane-row`, not `p-body`: this one has a splitter panel above it, which
        # does not stretch its children — measured at 1280px, the workflow pane
        # came out 404px wide inside a 1019px panel with the rail floating in the
        # middle of it. Same trap `.p-pane` documents.
        with ui.element("div").classes("p-pane-row"):
            _middle()
            _rail("Transcript", "forum", "chevron_left", _open_transcript)
        return
    # `reverse` so the pixel size applies to the transcript rather than to the
    # workflow: the pane with a real minimum is the one the number should govern.
    lower, upper = _transcript_limits()
    with _splitter(
        min(TRANSCRIPT_WIDTH, upper), (lower, upper), reverse=True, on_collapse=_close_transcript
    ) as split:
        with split.before:
            _middle()
        with split.after:
            _right()


def _rail(name: str, icon: str, arrow: str, reopen) -> None:
    """A closed pane, as the strip of edge it left behind.

    The toolbar used to carry a Files and a Transcript toggle, which is two
    controls at the top of the window for something you do at the side of it.
    Closing a pane is a drag now, and what stays is the edge: an arrow pointing
    the way the pane will come back from, and the pane's own icon under it so the
    strip says *which* pane rather than only that one is missing.

    It is deliberately not a sliver of the pane. A 28px stripe of a file tree
    reads as a rendering failure; a rail reads as a thing you press.
    """
    with ui.element("div").classes("p-rail"):
        c.button("", reopen, icon=arrow, micro=True).tooltip(_RAIL_TIP.format(name=name))
        ui.icon(icon).classes("p-rail-icon")


def _room_beside_files() -> int:
    """What is left once the left pane has taken as much as it is allowed to.

    Its **ceiling**, not its current width: the app does not track what a drag
    left the splitter at, and computing against the default would let the two
    side panes be dragged wide independently and together squeeze the workflow
    pane past its floor. Costing the worst case is a few pixels off the
    transcript's ceiling and needs no extra state to stay true.
    """
    return APP.width - (_files_limits()[1] if APP.show_files else 0)


def _files_limits() -> tuple[int, int]:
    """How wide the left pane may be dragged, given the window it is in.

    A splitter panel reserves real layout space, so a ceiling that ignores the
    window lets a drag squeeze the workflow pane past the width at which it stops
    working — and the pane inside, held up by its own `min-width`, then renders
    *underneath* the transcript. Measured at 820px before this: the workflow
    panel was 158px holding a 320px pane.
    """
    lower, upper = FILES_LIMITS
    room = APP.width - WORKFLOW_MIN - (TRANSCRIPT_LIMITS[0] if APP.show_transcript else 0)
    return lower, max(lower, min(upper, room))


def _transcript_limits() -> tuple[int, int]:
    """Same rule for the right pane, against whatever the left pane left behind."""
    lower, upper = TRANSCRIPT_LIMITS
    return lower, max(lower, min(upper, _room_beside_files() - WORKFLOW_MIN))


def _splitter(
    value: int, limits: tuple[int, int], *, reverse: bool = False, on_collapse=None
) -> ui.splitter:
    """A draggable pane edge that closes the pane when you drag past its floor.

    The floor is the width at which a pane stops being worth having, so it was
    also the honest place to close it — `DESIGN.md` says as much ("the honest
    move at that point is to close it rather than to squeeze it") and the
    splitter used to simply refuse to go further, which left the toolbar toggle
    as the only way to get rid of a pane.

    So the splitter's own lower limit drops to zero and the floor becomes a
    *threshold* instead: cross it and the pane closes, leaving a rail. The
    ceiling still holds — it is what keeps the workflow pane above its own floor,
    and that one never gives way.
    """
    lower, upper = limits
    split = (
        ui.splitter(value=value, limits=(0, upper), reverse=reverse)
        .props("unit=px")
        .classes("w-full h-full p-splitter")
    )
    if on_collapse is not None:
        split.on_value_change(lambda event: _past_the_floor(event.value, lower, on_collapse))
    return split


def _past_the_floor(width, floor: int, close) -> None:
    """Close the pane once a drag takes it under the width it is readable at."""
    if width is not None and width < floor:
        close()


def _left() -> None:
    with ui.element("div").classes("p-pane p-pane-left"):
        artifacts.pane()


def _middle() -> None:
    # `run_controls` is drawn by `workflow._workflow`, not here: the actions act
    # on the canvas, and drawn as a sibling of the pane they outlived it on every
    # screen that is not the canvas.
    with ui.element("div").classes("p-pane p-pane-mid"):
        workflow.pane()
        _split_target()


def _split_target() -> None:
    """Where a dragged tab lands (`docs/VISUALIZATION.md` §3.7).

    Drawn once, here, **outside the refreshable**: it is a fixed feature of the
    pane rather than something the current tab has, and an element rebuilt
    mid-gesture is the trap `assets/tabs.js` exists to avoid. It is invisible
    until a drag starts, which `tabs.js` says with a class on the body — a drop
    zone standing open over half the pane when nothing is being dragged would be
    chrome asking to be explained.
    """
    with ui.element("div").classes("tab-drop"):
        ui.icon("vertical_split").classes("tab-drop-icon")
        ui.label(_SPLIT_HINT).classes("tab-drop-label")


def _right() -> None:
    with ui.element("div").classes("p-pane p-pane-right"):
        transcript.pane()


# --- the toolbar ------------------------------------------------------------


@ui.refreshable
def toolbar() -> None:
    """Where you are, and the one control that is about none of the panes.

    It got very short, which is the point: the four actions moved onto the pane
    they act on, and every preference moved into Settings. What is left is the
    mark, the session name and the way into the settings panel.
    """
    with ui.element("div").classes("p-toolbar"):
        _project_label()
        ui.element("div").classes("flex-1")
        c.button("", settings.open_dialog, icon="settings", micro=True).tooltip(_SETTINGS_TIP)


def _project_label() -> None:
    """The session's name. A label, and only a label.

    The name of the open directory, and nothing else — the project brief is
    load-bearing but it is not chrome, and a paragraph of prose across the top of
    every screen is not what a toolbar is for.

    **It used to be the exit**, and that was the problem: the way to change
    projects was to notice that the thing telling you where you were could be
    clicked. Where you are and how to leave are two different statements, and the
    second one lives in Settings now, spelled out.
    """
    theme.logo(small=True)
    ui.label(APP.root.name or str(APP.root)).classes("p-session-name").tooltip(str(APP.root))


@ui.refreshable
def run_controls() -> None:
    """Run and Save — two actions, drawn only over the canvas they act on.

    **Run executes what the canvas is showing**, and everything it reads. That is
    one button where there were two *(2026-08-15)*: Run was the open spec and
    Build was the project, and they were always the same mechanism at two scopes
    (`pipeline.build_project`'s ``only``). Once the canvas itself can be narrowed
    to a set of tables, the scope is on screen — so naming it again in the choice
    of button was asking the same question twice, and "Build" was only ever the
    case where nothing was narrowed.

    It writes the ``.sql`` for what it ran, so the deliverable cannot silently
    fall behind the decision record. That is why it is not "Compile": compiling
    without running was never a thing portia could offer — the SQL comes out of an
    executed step (`PIPELINE.md` §3).

    **Save writes each table with its report.** Also one press where there were
    two, and the merge is the point rather than a tidy-up: a CSV in ``out/`` whose
    run report was never written is a number whose provenance died with the
    window (`engine.write_outputs`).

    **They are drawn by the canvas** (`workflow._workflow`) rather than beside it
    — over a file preview or a saved chat they were verbs with nothing in front of
    them to apply to, and making it structural means there is no "is the canvas
    up?" flag for a selection handler to forget to refresh.

    **They sit on the middle pane, at its right edge, not at the window's.** From
    the far corner of a toolbar they floated above the transcript — the pane they
    have nothing to do with. Putting them here is also the only way to keep them
    aligned to that edge: pane widths after a drag are never reported to the
    server (`_room_beside_files`), so chrome above the panes cannot know where
    the middle one ends. Drawn inside it, they track it for free.

    **Each says what it is on hover and nothing more.** The name is the whole
    tooltip: an icon needs to say which verb it is, and a paragraph explaining
    the verb is a paragraph nobody reads on a hover. What the actions actually do
    is documented here and in `DESIGN.md`, which is where a sentence belongs.
    """
    doing = APP.running
    running = doing is not None
    # **Whether there is anything to run is a question about the project, not
    # about the open spec.** Run executes what the canvas is showing, so a
    # project with specs is runnable even when the pane happens to be on a table
    # nobody has recorded a step on yet — `spec_has_steps` was the old, narrower
    # question, from when Run meant *this spec* and Build was a second button.
    # It is a directory read, which is what `stale_models` two lines away already
    # costs this pane.
    runnable = bool(engine.specs_in(APP))
    with ui.element("div").classes("p-actions"):
        # What the window is doing, beside the buttons that started it. Both
        # go to a thread, so the window stays live — which is exactly what made
        # the silence a problem: on a real extract a join is a minute of work
        # behind a button that looks the same as it did before it was pressed,
        # and that is indistinguishable from a press that missed. It is the same
        # pulsing mark the transcript uses for a tool in flight, not a second
        # vocabulary for the same statement.
        if doing:
            progress_note()
        # **The accent no longer waits on the transcript.** The scarcity rule is
        # per pane now (`DESIGN.md`, and `transcript._composer`): Run is this
        # pane's forward action and Send is the composer's, so a chat in flight
        # says nothing about what this button should look like. What does is
        # whether this pane is mid-run.
        kind = "primary" if runnable and not running else "tertiary"
        if APP.stop is not None:
            # **Stop takes Run's place rather than joining the row.** Same rule
            # and same word as the composer (`transcript._composer`): while work
            # is in flight the one thing to do about it is stop it, and a Stop
            # added *beside* a disabled button would widen the bar mid-press.
            # It is `tertiary` because stopping is not this pane's forward action
            # — nothing here is, until the run ends.
            c.button("Stop", _stop_run, kind="tertiary", icon="stop").tooltip(STOP_TIP)
        else:
            run = c.button(
                "Run",
                _run,
                kind=kind,
                icon="play_arrow",
                split=True,
                enabled=runnable and not running,
            )
            run.tooltip(_run_tip())
        # Run writes the pipeline, never the data. Save is how a *result* becomes
        # durable — one table per model that ran, each with the report of the run
        # that produced it — and it is armed by what was **built**, so a run that
        # never touched the spec you have open still produced tables worth
        # keeping. It stays a square icon: it is the quiet half and it is only
        # ever pressed after the other one.
        save = c.button("", _write, icon="save_alt", enabled=bool(APP.built) and not running)
        save.tooltip(SAVE_TIP)


@ui.refreshable
def progress_note() -> None:
    """What the window is doing, and where the run doing it has got to.

    **Its own refreshable, and that is the point.** The elapsed number ticks once
    a second, and the two panes this appears in are the action bar and the run
    report — rebuilding either of those every second to move one caption would
    repaint the whole middle pane under someone reading it. A refreshable
    instantiated in both places refreshes both and touches nothing else.

    Drawn only while `running` is set, so there is no resting state to design:
    the mark exists exactly as long as the work does (`DESIGN.md` → `in-flight`).
    """
    doing = APP.running
    if not doing:
        return
    progress = APP.progress
    if progress is None:
        c.in_flight(doing)
        return
    c.in_flight(doing, _where(progress), ident=progress.step)


def _where(progress: state.Progress) -> str:
    """`step 4 of 10 · 12s` — where the run is, and how long it has been there.

    **The model count appears only when it can move.** Run scopes to one spec
    *plus what it reads*, so a single-spec project would otherwise carry a
    permanent `model 1 of 1` beside the count that is actually changing.

    Elapsed is the number that answers the question the counts cannot: a step
    holding at `4 of 10` is either working or stuck, and on a spec whose steps
    differ by two orders of magnitude in cost, only the clock tells you which.
    """
    parts = []
    if progress.scoped:
        parts.append(f"model {progress.models_done + 1} of {progress.models_total}")
    if progress.steps_total:
        parts.append(f"step {progress.steps_done + 1} of {progress.steps_total}")
    parts.append(present.duration(progress.elapsed))
    if APP.connection:
        # A run on a warehouse is a run on someone's meter, and the mark is the
        # one place that says so while it is happening (`CONNECTOR.md` §2.7.1).
        parts.append(f"on {APP.connection}")
    return " · ".join(parts)


def _advance(reported: pipeline.BuildProgress) -> None:
    """One callback from the builder, already hopped onto the loop by `engine.build`.

    ``started`` is carried across rather than reset, because it times **the
    press** and not the step: a per-step clock restarting at every boundary would
    hide exactly the case worth seeing, which is one step holding for most of the
    build.
    """
    started = APP.progress.started if APP.progress else time.monotonic()
    APP.progress = state.Progress(
        model=reported.model,
        models_done=reported.models_done,
        models_total=reported.models_total,
        step=reported.step,
        steps_done=reported.steps_done,
        steps_total=reported.steps_total,
        started=started,
    )
    progress_note.refresh()


def tick_progress() -> None:
    """Move the elapsed number on, once a second, and only while something runs.

    Registered once for the page rather than from inside a refreshable: a timer
    created during a render is created again on every render, and four presses
    would leave four timers counting the same second.
    """
    if APP.running and APP.progress is not None:
        progress_note.refresh()


# --- actions ----------------------------------------------------------------


@asynccontextmanager
async def _in_flight(word: str) -> AsyncIterator[None]:
    """Say what the window is doing while it does it, and stop when it stops.

    The engine's work is on a thread, so the event loop is free to flush these
    refreshes before the await blocks — which is the whole reason the state can
    be a plain field rather than anything the engine reports.

    ``finally``, because a failure still has to give the buttons back.
    `engine.execute` catches its own exception into `run_error`, but the two
    write actions do not, and a run that raised would otherwise leave the pane
    claiming to be running forever. `progress` is cleared in the same place and
    for the same reason: it is a statement about a run in flight, and a finished
    run that left one on screen would be claiming to still be on step four.
    """
    APP.running = word
    run_controls.refresh()
    workflow.pane.refresh()
    try:
        yield
    finally:
        APP.running = None
        APP.progress = None
        # Dropped with the rest of the run's state: a scope that outlived its
        # work would leave a Stop button over a window with nothing to stop.
        APP.stop = None
        run_controls.refresh()
        workflow.pane.refresh()


@asynccontextmanager
async def _stoppable(word: str) -> AsyncIterator[cancel.Scope]:
    """`_in_flight`, plus the handle that lets the human end it.

    The two actions that execute a pipeline get this; the two that write a file
    get `_in_flight` alone. That is not an oversight — saving a report is a few
    kilobytes and offering to stop it would be offering a button that never
    finishes being drawn before the work is over.
    """
    async with _in_flight(word):
        # Both set before the first await, so the mark starts timing at the press
        # rather than at the first callback — which on a spec whose first step
        # reads a 143 MB CSV is three seconds of a clock that should already be
        # running — and so Stop is live for the whole of the wait, including the
        # part before the builder has said anything.
        APP.progress = state.Progress()
        stop = APP.stop = cancel.Scope()
        run_controls.refresh()
        try:
            yield stop
        finally:
            # The scope made it, so the scope closes it: a cancelled one holds a
            # thread that goes on interrupting connections the next run may reuse.
            stop.close()


def _stop_run() -> None:
    """Stop the run in flight. Synchronous on purpose — see `cancel.Scope.cancel`.

    It interrupts a database from the event loop thread and returns immediately;
    the awaiting action unwinds on its own. Making this `async` would suggest
    there is something to wait for, and the thing you are waiting for is the
    thing you just cancelled.
    """
    if APP.stop is not None:
        APP.stop.cancel()


def _say(client: Client, message: str) -> None:
    """Notify **through the client**, not through the ambient slot.

    Every one of these actions ends by refreshing `run_controls`, which deletes
    and rebuilds the button that is still the handler's slot context — so by the
    time the action has something to report, `ui.notify` cannot resolve a client
    from it and raises *The parent element this slot belongs to has been
    deleted*. NiceGUI swallows that into the log, which is why the four run
    actions have been silently unable to say anything at all: no "built 2
    model(s)", no "wrote 3 table(s)", no build failure.

    The client outlives any element in it, so holding one from the top of the
    action and entering it here is the fix. Predates the Stop button and is fixed
    with it, because a Stop whose only feedback is a notification is a Stop that
    reports nothing.
    """
    with client:
        ui.notify(message)


def _run_tip() -> str:
    """Name the scope in the tooltip, because the scope is the whole of the choice.

    One button now runs one table's flow or the whole project depending on what
    the canvas is showing, so what it is about to do is the one thing a hover has
    to say — and saying it here rather than in a second button is what makes the
    canvas, not the toolbar, the place the scope is chosen.
    """
    if not APP.visible:
        return RUN_ALL_TIP
    return RUN_SOME_TIP.format(tables=", ".join(sorted(APP.visible)))


async def _run() -> None:
    """Run what the canvas is showing, and everything it reads."""
    # Taken before the first refresh deletes the button this handler is standing
    # in — see `_say`.
    client = context.client
    async with _stoppable(state.RUNNING) as stop:
        built = await engine.run_scope(APP, scope=APP.visible, on_progress=_advance, stop=stop)
    # Checked before the other two: a stopped run returns nothing and clears no
    # error, which is indistinguishable from "there was nothing to run" unless
    # the stop is asked about first.
    if stop.cancelled:
        _say(client, _STOPPED)
    elif APP.run_error:
        _say(client, f"Run failed: {APP.run_error}")
    elif not built:
        _say(client, _NOTHING_TO_BUILD)
    else:
        _say(client, f"Ran {c.count(len(built), 'model')}. SQL written to models/.")
    artifacts.pane.refresh()  # the .sql it just wrote, and what is no longer stale


async def _write() -> None:
    """Save every table the run produced, each with the report of the run.

    One press for both, because the pair is what makes a result durable: a CSV
    whose report was never written is a number with nothing behind it, and the
    press that would have written it was the one nobody made.
    """
    client = context.client
    async with _in_flight(state.WRITING):
        written = await engine.write_outputs(APP)
    _say(
        client,
        f"Wrote {c.count(len(written), 'table')} to {engine.OUT_DIR}/ "
        f"and {c.count(len(APP.reports), 'report')} to {engine.RUNS_DIR}/.",
    )
    artifacts.pane.refresh()


def _close_transcript() -> None:
    _set_panes(transcript=False)


def _open_transcript() -> None:
    _set_panes(transcript=True)


def _close_files() -> None:
    _set_panes(files=False)


def _open_files() -> None:
    _set_panes(files=True)


def _set_panes(*, files: bool | None = None, transcript: bool | None = None) -> None:
    """Show or hide a side pane, and redraw only if that changed something.

    The guard is not a micro-optimisation. A splitter reports its width
    continuously while it is dragged, so crossing the floor fires the close
    handler on every frame after it — and each one would refresh the shell,
    rebuilding all three panes under a mouse that is still held down.
    """
    before = (APP.show_files, APP.show_transcript)
    APP.show_files = before[0] if files is None else files
    APP.show_transcript = before[1] if transcript is None else transcript
    if (APP.show_files, APP.show_transcript) != before:
        shell.refresh()


#: What each icon is called, and the whole of what a hover says. An icon has to
#: name its verb; it does not have to explain it — a hover is read in the moment
#: before a click, and the sentence that used to be here (what the action does,
#: and the path it writes to) was three lines of prose in a floating box. The
#: sentences live in `run_controls`'s docstring and in `DESIGN.md`, which is
#: where they can be read at the speed prose is read at.
_SETTINGS_TIP = "Settings"
_SPLIT_HINT = "Drop to open beside"
#: Run names its scope, because the scope is what the canvas decides and the
#: button no longer does. Two forms rather than one vague word: "Run" over a
#: narrowed canvas and "Run" over the whole project are the same press with very
#: different costs, and which one it is about to be should not need working out.
RUN_ALL_TIP = "Run every table"
RUN_SOME_TIP = "Run {tables} and everything they read"
SAVE_TIP = "Save tables and reports"
STOP_TIP = "Stop this run"
ACTION_TIPS = (RUN_ALL_TIP, SAVE_TIP)
_RAIL_TIP = "Show {name}. Drag its edge past the minimum width to close it again."
_NOTHING_TO_BUILD = "No specs to build yet. The copilot writes one as it records steps."
#: **Says what survived, not what was lost.** A stopped build keeps the models it
#: finished — they are compiled from the specs on disk and are not provisional —
#: so the sentence a human needs is which half of the project is now current.
_STOPPED = "Stopped. Models built before the stop are in models/."


def open_at_start(path: str | Path) -> None:
    """Open a project before the server starts, so `--project` skips a screen."""
    engine.open_project(path, APP)
    APP.opened = True
    _pick_up_spec()


def _pick_up_spec() -> None:
    """Open the project's first spec, and open its card on the canvas.

    Collapsed, the graph would say a project has three tables and show nothing of
    how any of them is built — so whichever spec is selected arrives expanded.
    """
    specs = engine.specs_in(APP)
    if specs:
        engine.select_spec(specs[0], APP)
        APP.expanded = frozenset({specs[0].stem})

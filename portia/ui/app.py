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

from pathlib import Path

from nicegui import ui

from portia.ui import artifacts, engine, screens, settings, theme, transcript, workflow
from portia.ui import components as c
from portia.ui.state import APP

TITLE = "portia"


@ui.page("/")
def page() -> None:
    theme.apply()
    ui.page_title(TITLE)
    # At page level, deliberately: a dialog built inside a refreshable is deleted
    # by the first refresh (see `screens.build_add_dialog`).
    screens.build_add_dialog()
    screens.build_decision_dialog()
    settings.build_dialog()
    # `DESIGN.md` → Width behaviour, which cannot be done in CSS once the panes
    # are inside splitters: a splitter sets an inline pixel width on its panel, so
    # restyling the pane inside changes nothing about the space reserved beside it.
    ui.on("portia:viewport", _resized)
    shell()


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
    with ui.element("div").classes("p-pane p-pane-mid"):
        run_controls()
        workflow.pane()


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
    """Run and Build: one mechanism at two scopes, and each says which it is.

    **Run** executes the open spec *and everything it reads*, because a table is
    not built until its inputs are — that is what "run this spec" means once specs
    reference each other by name. **Build** does the whole project.

    Both write the ``.sql`` for what they ran, so the deliverable cannot silently
    fall behind the decision record. That is why neither is "Compile": compiling
    without running was never a thing portia could offer — the SQL comes out of an
    executed step (`PIPELINE.md` §3).

    Run keeps the single accent fill once a spec has steps; Build stays quiet
    beside it. Scope is not importance, and the whole project is not the more
    important button.

    **They sit on the middle pane, at its right edge, not at the window's.** All
    four act on the workflow pane and nothing else, and from the far corner of a
    toolbar they were four verbs floating above the transcript — the pane they
    have nothing to do with. Putting them here is also the only way to keep them
    aligned to that edge: pane widths after a drag are never reported to the
    server (`_room_beside_files`), so chrome above the panes cannot know where
    the middle one ends. Drawn inside it, they track it for free.

    **Run and Build carry their word; the two saves do not.** The pair that
    *executes* something is the pair worth naming on screen, so each is an icon
    ruled off from its label — one control doing one thing, not a glyph beside
    some text. Write outputs and Save report stay square icons: they are the
    quiet half, they are only ever pressed after one of the other two, and four
    labelled buttons is the row that made this a toolbar problem in the first
    place.

    **Each says what it is on hover and nothing more.** The name is the whole
    tooltip: an icon needs to say which verb it is, and a paragraph explaining
    the verb is a paragraph nobody reads on a hover. What the actions actually do
    is documented here and in `DESIGN.md`, which is where a sentence belongs.
    """
    with ui.element("div").classes("p-actions"):
        kind = "primary" if APP.spec_has_steps and not APP.busy else "tertiary"
        run = c.button(
            "Run", _run, kind=kind, icon="play_arrow", split=True, enabled=APP.spec_has_steps
        )
        run.tooltip(RUN_TIP)
        build = c.button("Build", _build, icon="construction", split=True, enabled=not APP.busy)
        build.tooltip(BUILD_TIP)
        # Run and Build write the pipeline, never the data — these two are how a
        # *result* becomes durable, and both are things you press rather than
        # things that happen to you. Either of the two above arms them.
        #
        # Write outputs is armed by what was **built**, not by the open spec's
        # results: it saves a table per model that ran, so a build that never
        # touched the spec you have open still produced tables worth keeping.
        # Save report is about the open spec, so that one stays on `results`.
        write = c.button("", _write, icon="save_alt", enabled=bool(APP.built))
        write.tooltip(WRITE_TIP)
        report = c.button("", _save_report, icon="description", enabled=bool(APP.results))
        report.tooltip(REPORT_TIP)


# --- actions ----------------------------------------------------------------


async def _run() -> None:
    await engine.run_spec(APP)
    workflow.pane.refresh()
    artifacts.pane.refresh()  # the .sql it just wrote, and what is no longer stale
    run_controls.refresh()


async def _build() -> None:
    """Compile the whole project — the app's half of `python -m portia.cli.build`.

    It goes through the same `engine.execute` as Run, so a build leaves the window
    in the same state a run does: the open spec's report on screen, and the two
    save buttons live. Before, Build discarded what it produced and the saves
    stayed greyed out, so the one press that ran *every* model looked like the one
    press that saved nothing.

    A project with no specs is the other half of that: `build_project` returns an
    empty list, and "built 0 model(s)" reads as a failure. It isn't one — there is
    simply nothing recorded to build yet — so it says that instead.
    """
    built = await engine.execute(APP)
    if APP.run_error:
        ui.notify(f"build failed — {APP.run_error}")
    elif not built:
        ui.notify(_NOTHING_TO_BUILD)
    else:
        ui.notify(f"built {len(built)} model(s) to models/")
    artifacts.pane.refresh()
    workflow.pane.refresh()
    run_controls.refresh()


async def _write() -> None:
    written = await engine.write_outputs(APP)
    ui.notify(f"wrote {len(written)} table(s) to {engine.OUT_DIR}/")
    artifacts.pane.refresh()


async def _save_report() -> None:
    path = await engine.write_report(APP)
    if path is not None:
        ui.notify(f"saved {path.name} to {engine.RUNS_DIR}/")
        artifacts.pane.refresh()
        run_controls.refresh()


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
RUN_TIP = "Run spec"
BUILD_TIP = "Build full pipeline"
WRITE_TIP = "Write outputs"
REPORT_TIP = "Save report"
ACTION_TIPS = (RUN_TIP, BUILD_TIP, WRITE_TIP, REPORT_TIP)
_RAIL_TIP = "Show {name} · drag its edge past the width it is readable at to close it again."
_NOTHING_TO_BUILD = "No specs to build yet — the copilot writes one as it records steps."


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

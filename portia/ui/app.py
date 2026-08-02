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

from portia.ui import artifacts, engine, screens, theme, transcript, workflow
from portia.ui import components as c
from portia.ui.state import APP

TITLE = "portia"

#: The page's light/dark control. One open project, one window's worth of state —
#: see `state.py` for why this app is a singleton rather than per-tab.
_DARK: ui.dark_mode | None = None


@ui.page("/")
async def page() -> None:
    global _DARK
    _DARK = theme.apply()
    ui.page_title(TITLE)
    # At page level, deliberately: a dialog built inside a refreshable is deleted
    # by the first refresh (see `screens.build_add_dialog`).
    screens.build_add_dialog()
    screens.build_brief_dialog()
    # `DESIGN.md` → Width behaviour, which cannot be done in CSS once the panes
    # are inside splitters: a splitter sets an inline pixel width on its panel, so
    # restyling the pane inside changes nothing about the space reserved beside it.
    ui.on("portia:viewport", _resized)
    await shell()


def _resized(event) -> None:
    """Apply the width band's defaults, and only when the band actually changes.

    Resizing within a band leaves the panes as you left them: a layout that keeps
    reopening a pane you just closed is worse than one that never adapts.
    """
    width = int(event.args or 0)
    if width and APP.resize(width):
        shell.refresh()


@ui.refreshable
async def shell() -> None:
    """Which of the four screens is showing. The context panel is the one gate."""
    if not APP.opened:
        screens.project_open()
    elif not engine.has_context(APP):
        screens.project_context()
    elif not APP.left_add_data and not APP.skipped_sources:
        screens.first_sources()
    else:
        await _window()


#: Pane sizes, **in pixels rather than percent**. A percentage minimum means the
#: floor moves with the window, and the transcript — which holds the question form
#: and the write confirmation, the two things this app exists for — could be
#: dragged down to a few characters wide. These minimums are the width at which
#: each pane is still worth having; the toolbar toggles are how you get rid of one.
FILES_WIDTH, FILES_LIMITS = 260, (200, 520)
TRANSCRIPT_WIDTH, TRANSCRIPT_LIMITS = 400, (330, 780)

#: The width below which the workflow pane stops being worth having. It is the
#: one pane that never gives way (`DESIGN.md` → Width behaviour), so this is the
#: floor every other pane's ceiling is computed against.
WORKFLOW_MIN = 320


async def _window() -> None:
    with ui.element("div").classes("p-window"):
        toolbar()
        with ui.element("div").classes("p-body"):
            if APP.show_files:
                with _splitter(FILES_WIDTH, _files_limits()) as files:
                    with files.before:
                        _left()
                    with files.after:
                        await _workflow_and_transcript()
            else:
                await _workflow_and_transcript()


async def _workflow_and_transcript() -> None:
    if not APP.show_transcript:
        await _middle()
        return
    # `reverse` so the pixel size applies to the transcript rather than to the
    # workflow: the pane with a real minimum is the one the number should govern.
    lower, upper = _transcript_limits()
    with _splitter(min(TRANSCRIPT_WIDTH, upper), (lower, upper), reverse=True) as split:
        with split.before:
            await _middle()
        with split.after:
            _right()


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


def _splitter(value: int, limits: tuple[int, int], *, reverse: bool = False) -> ui.splitter:
    return (
        ui.splitter(value=value, limits=limits, reverse=reverse)
        .props("unit=px")
        .classes("w-full h-full p-splitter")
    )


def _left() -> None:
    with ui.element("div").classes("p-pane p-pane-left"):
        artifacts.pane()


async def _middle() -> None:
    with ui.element("div").classes("p-pane p-pane-mid"):
        await workflow.pane()


def _right() -> None:
    with ui.element("div").classes("p-pane p-pane-right"):
        transcript.pane()


# --- the toolbar ------------------------------------------------------------


@ui.refreshable
def toolbar() -> None:
    with ui.element("div").classes("p-toolbar"):
        _project_label()
        ui.element("div").classes("flex-1")
        _run_controls()
        _view_controls()


def _project_label() -> None:
    """The session's name, and the way out of it.

    The name of the open directory, and nothing else — the project brief is
    load-bearing but it is not chrome, and a paragraph of prose across the top of
    every screen is not what a toolbar is for.

    It is also the only route back to the project picker, so it is a button and
    says so. Disabled mid-turn: switching would leave the copilot writing into a
    directory the window has stopped looking at.
    """
    theme.logo(small=True)
    c.button("Brief", screens.open_brief_dialog, icon="notes", micro=True).tooltip(
        APP.project_context or "no brief yet"
    )
    label = c.button(
        APP.root.name or str(APP.root),
        _switch_project,
        icon="folder_open",
        enabled=not APP.busy,
    )
    label.tooltip(_SWITCH_BUSY if APP.busy else f"{APP.root} — click to open another project")


def _switch_project() -> None:
    APP.opened = False
    shell.refresh()


def _run_controls() -> None:
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
    """
    kind = "primary" if APP.spec_has_steps and not APP.busy else "tertiary"
    run = c.button("Run", _run, kind=kind, icon="play_arrow", enabled=APP.spec_has_steps)
    run.tooltip(_run_tooltip())
    build = c.button("Build", _build, icon="construction", enabled=not APP.busy)
    build.tooltip(_BUILD_TIP.format(models=APP.root / "models"))
    # A run writes the pipeline, never the data — these two are how a *result*
    # becomes durable, and both are things you press rather than things that
    # happen to you.
    write = c.button("Write outputs", _write, icon="save_alt", enabled=bool(APP.results))
    write.tooltip(str(APP.root / engine.OUT_DIR))
    report = c.button("Save report", _save_report, icon="description", enabled=bool(APP.results))
    report.tooltip(str(APP.root / engine.RUNS_DIR))


def _run_tooltip() -> str:
    if APP.spec_path is None:
        return "no spec open"
    return _RUN_TIP.format(name=APP.spec_path.stem, path=APP.spec_path)


def _view_controls() -> None:
    """Both panes are collapsible; the workflow pane and Run never are."""
    c.button("Files", _toggle_files, icon="folder", micro=True)
    c.button("Transcript", _toggle_transcript, icon="forum", micro=True)
    mode = _DARK.value if _DARK else None
    c.button(theme.MODE_LABEL[mode], _cycle_theme, icon=theme.MODE_ICON[mode], micro=True)


# --- actions ----------------------------------------------------------------


async def _run() -> None:
    await engine.run_spec(APP)
    workflow.pane.refresh()
    artifacts.pane.refresh()  # the .sql it just wrote, and what is no longer stale
    toolbar.refresh()


async def _build() -> None:
    """Compile the whole project — the app's half of `python -m portia.cli.build`."""
    try:
        built = await engine.build(APP)
    except Exception as exc:  # noqa: BLE001 — shown to the operator, not swallowed
        ui.notify(f"build failed — {type(exc).__name__}: {exc}")
        return
    ui.notify(f"built {len(built)} model(s) to models/")
    artifacts.pane.refresh()
    workflow.pane.refresh()
    toolbar.refresh()


async def _write() -> None:
    written = await engine.write_outputs(APP)
    ui.notify(f"wrote {len(written)} table(s) to {engine.OUT_DIR}/")
    artifacts.pane.refresh()


async def _save_report() -> None:
    path = await engine.write_report(APP)
    if path is not None:
        ui.notify(f"saved {path.name} to {engine.RUNS_DIR}/")
        artifacts.pane.refresh()
        toolbar.refresh()


def _toggle_transcript() -> None:
    APP.show_transcript = not APP.show_transcript
    shell.refresh()


def _toggle_files() -> None:
    APP.show_files = not APP.show_files
    shell.refresh()


def _cycle_theme() -> None:
    if _DARK is None:
        return
    _DARK.value = theme.next_mode(_DARK.value)
    toolbar.refresh()


_SWITCH_BUSY = "Can't switch projects while a turn is running."
_RUN_TIP = (
    "Run {name} and every model it reads, then write their .sql. "
    "A table isn't built until its inputs are.\n{path}"
)
_BUILD_TIP = "Run every spec in the project and write the whole pipeline.\n{models}"


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

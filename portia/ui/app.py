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
from portia.ui.state import APP, SPEC

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
    await shell()


@ui.refreshable
async def shell() -> None:
    """Which of the four screens is showing. The context panel is the one gate."""
    if not APP.opened:
        screens.project_open()
    elif not engine.has_context(APP):
        screens.project_context()
    elif not APP.sources:
        screens.first_sources()
    else:
        await _window()


async def _window() -> None:
    with ui.element("div").classes("p-window"):
        toolbar()
        with ui.element("div").classes("p-body"):
            if APP.show_files:
                with ui.element("div").classes("p-pane p-pane-left"):
                    artifacts.pane()
            with ui.element("div").classes("p-pane p-pane-mid"):
                await workflow.pane()
            if APP.show_transcript:
                with ui.element("div").classes("p-pane p-pane-right"):
                    transcript.pane()


# --- the toolbar ------------------------------------------------------------


@ui.refreshable
def toolbar() -> None:
    with ui.element("div").classes("p-toolbar"):
        _project_label()
        _spec_switcher()
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


def _spec_switcher() -> None:
    specs = engine.specs_in(APP)
    if not specs:
        return
    names = [p.name for p in specs]
    current = APP.spec_path.name if APP.spec_path else names[0]
    ui.select(names, value=current, on_change=lambda e: _switch_spec(e.value)).props(
        "borderless dense options-dense"
    ).classes("p-field p-field-mono")


def _switch_spec(name: str) -> None:
    engine.select_spec(APP.root / "specs" / name, APP)
    APP.select(SPEC, name)
    artifacts.pane.refresh()
    workflow.pane.refresh()
    toolbar.refresh()


def _run_controls() -> None:
    """Run is the app's one accent action once a spec has steps to execute."""
    kind = "primary" if APP.spec_has_steps and not APP.busy else "tertiary"
    run = c.button("Run", _run, kind=kind, icon="play_arrow", enabled=APP.spec_has_steps)
    run.tooltip(str(APP.spec_path) if APP.spec_path else "no spec open")
    write = c.button("Write outputs", _write, icon="save_alt", enabled=bool(APP.results))
    write.tooltip(str(APP.root / engine.OUT_DIR))


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
    toolbar.refresh()


async def _write() -> None:
    written = await engine.write_outputs(APP)
    ui.notify(f"wrote {len(written)} table(s) to {engine.OUT_DIR}/")
    artifacts.pane.refresh()


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


def open_at_start(path: str | Path) -> None:
    """Open a project before the server starts, so `--project` skips a screen."""
    engine.open_project(path, APP)
    APP.opened = True
    specs = engine.specs_in(APP)
    if specs:
        engine.select_spec(specs[0], APP)

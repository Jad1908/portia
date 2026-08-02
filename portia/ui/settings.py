"""Settings — the one place a preference lives, and nothing else.

Everything here used to be chrome. The theme was a button in the toolbar, the
project switch was the session name pretending to be a label, the brief was a
third button beside them, and the model and effort were picked in two panes and
nowhere else. A toolbar is for saying where you are and for the actions on what
is in front of you; a preference is neither.

**It holds controls, not behaviour.** Every field here is bound to the same
``APP`` attribute the surface that spends it reads — ``import_destination`` is
the one the drop zone uses, ``model`` and ``effort`` are the ones a turn is
started with — so this is a second *place to change* a setting, never a second
setting. The one thing that is not a plain control is the project switch, which
refuses mid-turn: switching would leave the copilot writing into a directory the
window has stopped looking at.

Four groups, in the order they are worth changing: **Project** (where you are and
what it is about) · **Copilot** (what a turn spends) · **Data** (what arrives, and
where it lands) · **Appearance**.

The dialog is built once at page level — see `screens.build_add_dialog` for what
happens to one created inside a refreshable — and its *contents* are the
refreshable, so picking a theme can redraw the panel without rebuilding the
overlay it is in.
"""

from __future__ import annotations

from nicegui import ui

from portia.ui import components as c
from portia.ui import engine, screens, theme
from portia.ui.state import APP, BRIEF

TITLE = "Settings"

#: This page's panel. One per page, never rebuilt.
_DIALOG: ui.dialog | None = None

SWITCH_BUSY = "Can't switch projects while a turn is running."
DESTINATION_SCOPE = "inside the project — data lives in the repo"
INTERPRET_COST = "Profiling is free and always happens. Reading them costs a model turn."
SPEND_NOTE = "What a turn costs is the model and the effort, and nothing else."
BRIEF_WHY = "What makes a column's meaning decidable. It opens in the middle pane."
NO_PANEL = "The settings panel didn't load — reload the page."


def build_dialog() -> None:
    """Create the settings panel. **Called once per page, never from a pane.**"""
    global _DIALOG
    with ui.dialog().props("transition-duration=0") as dialog:
        _panel()
    _DIALOG = dialog


def open_dialog() -> None:
    """Show it. Says so if it isn't there, rather than doing nothing quietly."""
    if _DIALOG is None or _DIALOG.is_deleted:
        ui.notify(NO_PANEL)
        return
    _panel.refresh()
    _DIALOG.open()


@ui.refreshable
def _panel() -> None:
    with ui.element("div").classes("write-confirm").style(screens.DIALOG_WIDTH):
        ui.label(TITLE).classes("t-heading-md")
        _project()
        _copilot()
        _data()
        _appearance()
        with ui.element("div").classes("row-gap-sm"):
            c.button("Close", _close, kind="secondary")


def _group(label: str) -> ui.element:
    c.rule()
    c.section_header(label)
    return ui.element("div").classes("settings-group")


def _project() -> None:
    """Where you are, and the text the whole project is conditioned on.

    The session name used to be a button in the toolbar and the only route out of
    a project — a label that was secretly the exit. Here the name says where you
    are and the exit says what it does.
    """
    with _group("Project"):
        c.caption(str(APP.root))
        c.button("Open another project…", _switch_project, icon="folder_open").tooltip(
            SWITCH_BUSY if APP.busy else "back to the project picker"
        )
        c.button("Edit the project brief", _open_brief, icon="notes")
        c.caption(BRIEF_WHY)


def _copilot() -> None:
    """What a turn spends. The same two fields the goal box and the drop zone bind."""
    with _group("Copilot"):
        c.model_effort(APP, _set_effort)
        c.caption(SPEND_NOTE)


def _data() -> None:
    """What arrives, and where it lands."""
    with _group("Data"):
        c.button("Add data…", _add_data, icon="add")
        c.caption("Destination")
        (
            ui.input(placeholder=engine.DATA_DIR)
            .classes("p-field p-field-mono w-full")
            .props("borderless")
            .bind_value(APP, "import_destination")
        )
        c.caption(DESTINATION_SCOPE)
        ui.switch("Have the copilot read what each source is").classes("p-toggle").bind_value(
            APP, "interpret"
        )
        c.caption(INTERPRET_COST)


def _appearance() -> None:
    """Light and dark are equal first-class modes, with auto as the third.

    Three named options rather than the cycling button this replaces. A control
    that only shows the mode it is *in* cannot distinguish "dark" from "auto, and
    it is night", and a settings panel is exactly where that should be legible.
    """
    with _group("Appearance"):
        c.caption("Theme")
        c.segmented(
            [theme.MODE_LABEL[mode] for mode in theme.MODES],
            theme.MODE_LABEL[theme.mode()],
            _set_theme,
        )


# --- what the controls do ---------------------------------------------------


def _set_theme(label: str) -> None:
    theme.set_mode(theme.MODE_VALUE[label])
    _panel.refresh()


def _set_effort(effort: str) -> None:
    APP.effort = effort
    _panel.refresh()


def _switch_project() -> None:
    from portia.ui import app as app_module

    if APP.busy:
        ui.notify(SWITCH_BUSY)
        return
    _close()
    APP.opened = False
    app_module.shell.refresh()


def _open_brief() -> None:
    from portia.ui import artifacts, workflow

    _close()
    APP.select(BRIEF, "")
    artifacts.pane.refresh()
    workflow.pane.refresh()


def _add_data() -> None:
    _close()
    screens.open_add_dialog()


def _close() -> None:
    if _DIALOG is not None and not _DIALOG.is_deleted:
        _DIALOG.close()

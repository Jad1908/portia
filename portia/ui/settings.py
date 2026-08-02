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

Four **tabs**, in the order they are worth changing: **Project** (where you are
and what it is about) · **Copilot** (what a turn spends) · **Data** (what
arrives, and where it lands) · **Appearance**. Stacked down a column they were a
scroll through three things you are not changing to reach the one you are, and
they made the dialog tall enough to be the whole window.

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
STALE_PANEL = "Settings may be showing stale values ({why}) — reload the page to be sure."


def build_dialog() -> None:
    """Create the settings panel. **Called once per page, never from a pane.**"""
    global _DIALOG
    with ui.dialog().props("transition-duration=0") as dialog:
        _panel()
    _DIALOG = dialog


def open_dialog() -> None:
    """Show it. Says so if it isn't there, rather than doing nothing quietly.

    **The refresh must never be able to stop the open.** Redrawing first is a
    nicety — it picks up a project path or a busy flag that changed since the
    panel was built — but `refresh()` walks targets that a page reload, a second
    tab or a rebuilt slot may have invalidated, and a raise there used to mean
    the gear silently did nothing. Opening is the point; showing yesterday's
    project path is a far smaller failure than a settings panel that won't come
    up, so the refresh is attempted and its failure is reported rather than
    propagated.
    """
    if _DIALOG is None or _DIALOG.is_deleted:
        ui.notify(NO_PANEL)
        return
    try:
        _panel.refresh()
    except Exception as exc:  # noqa: BLE001 — never worth a dead settings panel
        ui.notify(STALE_PANEL.format(why=type(exc).__name__))
    _DIALOG.open()


#: The four tabs, in the order they are worth changing, and what draws each.
#: A tuple rather than a dict so the order is the declaration — a settings panel
#: whose tabs move when someone re-sorts a dict is a settings panel you have to
#: re-learn.
TABS = ("Project", "Copilot", "Data", "Appearance")

#: Which one is showing. Page state, not project state: it is where you are
#: looking inside a dialog, and it survives the panel being refreshed so that
#: picking a theme does not throw you back to the first tab.
_TAB = TABS[0]


@ui.refreshable
def _panel() -> None:
    """The panel: a title, one tab's worth of controls, and the way out.

    **Tabbed rather than stacked.** Four groups down a column is a scroll through
    three things you are not changing to reach the one you are, and it made the
    dialog tall enough to be the whole window. The tabs are the same `pane-tabs`
    the transcript uses — one tab vocabulary in the app, not two that have to be
    kept looking alike.
    """
    with ui.element("div").classes("write-confirm settings-panel").style(screens.DIALOG_WIDTH):
        ui.label(TITLE).classes("t-heading-md")
        _tabs()
        with ui.element("div").classes("settings-group"):
            _BODY[_TAB]()
        with ui.element("div").classes("row-gap-sm"):
            c.button("Close", _close, kind="secondary")


def _tabs() -> None:
    with ui.element("div").classes("pane-tabs"):
        for tab in TABS:
            classes = "pane-tab" + (" pane-tab--active" if tab == _TAB else "")
            element = ui.element("div").classes(classes)
            with element:
                ui.label(tab)
            element.on("click", lambda t=tab: _show_tab(t))


def _show_tab(tab: str) -> None:
    global _TAB
    _TAB = tab
    _panel.refresh()


def _project() -> None:
    """Where you are, and the text the whole project is conditioned on.

    The session name used to be a button in the toolbar and the only route out of
    a project — a label that was secretly the exit. Here the name says where you
    are and the exit says what it does.
    """
    c.caption(str(APP.root))
    c.button("Open another project…", _switch_project, icon="folder_open").tooltip(
        SWITCH_BUSY if APP.busy else "back to the project picker"
    )
    c.button("Edit the project brief", _open_brief, icon="notes")
    c.caption(BRIEF_WHY)


def _copilot() -> None:
    """What a turn spends. The same two fields the goal box and the drop zone bind."""
    c.model_effort(APP, _set_effort)
    c.caption(SPEND_NOTE)


def _data() -> None:
    """What arrives, and where it lands."""
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
    c.caption("Theme")
    c.segmented(
        [theme.MODE_LABEL[mode] for mode in theme.MODES],
        theme.MODE_LABEL[theme.mode()],
        _set_theme,
    )


# --- what the controls do ---------------------------------------------------


#: Tab name → what draws it. Defined after the four, and checked against `TABS`
#: by a test: a tab with no body is a tab that renders an empty panel.
_BODY = {"Project": _project, "Copilot": _copilot, "Data": _data, "Appearance": _appearance}


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

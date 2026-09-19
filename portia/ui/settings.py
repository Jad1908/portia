"""Settings — the one place a preference lives, and nothing else.

Everything here used to be chrome. The theme was a button in the toolbar, the
project switch was the session name pretending to be a label, the brief was a
third button beside them, and the model and effort were picked in two panes and
nowhere else. A toolbar is for saying where you are and for the actions on what
is in front of you; a preference is neither.

**It holds controls, not behaviour.** Every field here is bound to the same
``APP`` attribute the surface that spends it reads — ``interpret`` is the one the
add-data panel spends, ``model`` and ``effort`` are the ones a turn is started
with — so this is a second *place to change* a setting, never a second setting.
Two things are not plain controls. The project switch refuses mid-turn:
switching would leave the copilot writing into a directory the window has stopped
looking at. And the **data folder is stated, not edited** — picking it is a
browse through the repo with a count against each folder, and a second picker
here would be a second opinion about what counts as a data folder, so this tab
says what it is and hands you to the panel that sets it.

Five **sections**, in the order they are worth changing: **Project** (where you
are and what it is about) · **Copilot** (what a turn spends, and which writes
stop) · **Data** (what arrives, and where it lands) · **Appearance** · **Help**
(the way to report a problem, `ui/feedback.py`). A list down
the left and one section's settings on the right *(2026-09-04)* — an editor's
settings page rather than a tab strip over a stack, because the list can hold a
glyph and stays readable at eight sections where a strip is scrolling at five.
Each setting is one `c.setting` row: a title, what it does, and the control.

The dialog is built once at page level — see `screens.build_add_dialog` for what
happens to one created inside a refreshable — and its *contents* are the
refreshable, so picking a theme can redraw the panel without rebuilding the
overlay it is in.
"""

from __future__ import annotations

from nicegui import ui

from portia.agent import providers
from portia.core import feedback as core_feedback
from portia.ui import components as c
from portia.ui import engine, feedback, screens, state, theme
from portia.ui.state import APP, BRIEF

TITLE = "Settings"

#: This page's panel. One per page, never rebuilt.
_DIALOG: ui.dialog | None = None

SWITCH_BUSY = "Cannot switch projects while something is running."
SWITCH_TIP = "Back to the project picker"
PROJECT_WHAT = "Project"
PROJECT_WHY = "The directory this window reads and writes."
BRIEF_WHAT = "Project brief"
BRIEF_WHY = "The goal, the modelling and the data, in a few sentences. Read on every exchange."
BRIEF_OPEN = "Edit the brief"
SPEND_WHAT = "Provider, model and effort"
SPEND_WHY = "What an exchange costs. Effort is fixed for the life of a chat."
#: The approval mode, and what each value does. Stated, never recommended.
MODE_WHAT = "Writes"
MODE_WHY = {
    state.ASK: "Every write to a spec or the catalog stops for you first. Questions always stop.",
    state.AUTOPILOT: (
        "Every write goes through, recording a step included. Questions still stop. "
        "A step you did not pause for is one you read in the diff."
    ),
}
#: Not persisted, like every field on `App`: a mode that survived a restart is
#: one you can be in without having chosen to be (`CONVERSATION.md` §14.3).
MODE_NOT_SAVED = "Not saved. A new window starts by asking."
#: Said above the per-tool switches, and it states the facts rather than
#: recommending: 31 approvals in one indexing pass is what the count was, and
#: whether that is worth the interruption is the reader's call, not the panel's.
CONFIRM_WHAT = "Catalog writes that stop for you"
CONFIRM_WHY = (
    "Indexing 23 sources raised 31 confirmations; 25 were catalog prose you can "
    "correct in place afterwards. Recording a step always asks outside autopilot: "
    "it runs the step, then writes it."
)
DATA_WHAT = "Data folder"
NO_DATA_DIR = "not set, the whole repo"
DATA_DIR_WHY = "What the left pane draws as data, and where an import lands. Changed from Add data."
DATA_OPEN = "Add data"
INTERPRET_WHAT = "Read each new source"
INTERPRET_LABEL = "The copilot writes what a source means after it is profiled"
INTERPRET_WHY = "Profiling is free and always happens. Reading spends a model exchange."
WAREHOUSE_WHAT = "Warehouse"
WAREHOUSE_WHY = (
    "The connection this project runs on, named in project.yaml; the account behind it is "
    "yours, in ~/.config/portia/connections.yaml. Set from Add data."
)
NO_WAREHOUSE = "none, files in the repo"
WAREHOUSE_OPEN = "Connect a warehouse"
AGENT_WRITES_LABEL = "The copilot creates tables as it records steps"
AGENT_WRITES_WHY = (
    "Each recorded step becomes a table in the warehouse, in the schema the copilot chose for "
    "that spec, created if missing. Off, only Run and Build write."
)
THEME_WHAT = "Theme"
THEME_WHY = "Auto follows the system."
REPORT_WHAT = "Something broke, or could be better"
REPORT_WHY = "Opens a report you read and edit. portia sends nothing itself."
VERSION_WHAT = "Version"
NO_PANEL = "The settings panel did not load. Reload the page."
STALE_PANEL = "Settings may show stale values ({why}). Reload the page."


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


#: The sections, in the order they are worth changing, and their glyphs.
#: A tuple rather than a dict so the order is the declaration — a settings panel
#: whose sections move when someone re-sorts a dict is one you have to re-learn.
TABS = ("Project", "Copilot", "Data", "Appearance", "Help")
_ICONS = {
    "Project": "folder",
    "Copilot": "forum",
    "Data": "table_chart",
    "Appearance": "palette",
    "Help": "help_outline",
}

#: Which one is showing. Page state, not project state: it is where you are
#: looking inside a dialog, and it survives the panel being refreshed so that
#: picking a theme does not throw you back to the first section.
_TAB = TABS[0]


@ui.refreshable
def _panel() -> None:
    """The panel: a title, the section list, one section's settings, the way out.

    **A list beside the settings, not tabs above them** *(2026-09-04)*. The tab
    strip was the transcript's, borrowed so the app would have one tab
    vocabulary; what it cost was a settings panel with no room for a glyph, no
    room for a fifth section, and a `min-height` on the body so the dialog did
    not change size between tabs. The list is the left pane's `artifact_row`,
    so the app still has one row vocabulary rather than a second one — and the
    list is what every editor's settings page is.
    """
    with ui.element("div").classes("write-confirm settings-panel"):
        ui.label(TITLE).classes("t-heading-md")
        with ui.element("div").classes("settings-layout"):
            _nav()
            with ui.element("div").classes("settings-body"):
                _BODY[_TAB]()
        with ui.element("div").classes("settings-foot"):
            c.button("Close", _close, kind="secondary")


def _nav() -> None:
    with ui.element("div").classes("settings-nav"):
        for tab in TABS:
            c.artifact_row(
                name=tab,
                icon=_ICONS[tab],
                selected=tab == _TAB,
                on_click=lambda t=tab: _show_tab(t),
            )


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
    with c.setting(PROJECT_WHAT, PROJECT_WHY):
        c.path_row(APP.root, icon="folder")
        c.button("Open another project…", _switch_project, icon="folder_open").tooltip(
            SWITCH_BUSY if APP.busy else SWITCH_TIP
        )
    with c.setting(BRIEF_WHAT, BRIEF_WHY):
        c.button(BRIEF_OPEN, _open_brief, icon="notes")


def _copilot() -> None:
    """What an exchange spends, and which of its writes stop for you.

    The switches are **per tool** rather than one "confirm writes" flag, because
    the two kinds of write underneath the read/write line are not one decision
    (`state.AUTO_ALLOWABLE`). They bind `App.auto_allow`, which
    `ui/exchange.auto_allow` reads at the moment of each call — so this is a
    second place to *change* the setting and never a second setting.
    """
    with c.setting(SPEND_WHAT, SPEND_WHY):
        # `model_effort` draws no effort control on a provider that ignores it.
        c.model_effort(
            APP,
            _set_effort,
            on_provider=_set_provider,
            on_refresh=_list_models_clicked,
            on_start=screens.open_server_dialog,
        )
        # How a model is added: the command, shown and never run
        # (`docs/PROVIDERS.md` §4.4). Here and not in the composer, because
        # it is a thing you do once in a terminal, not per message.
        c.add_model_line(APP.provider)
    # The same picker the composer carries (`c.approval_mode`), bound to the
    # same field. What it does is said under it for the value it is on, so the
    # panel never describes a mode you are not in.
    with c.setting(MODE_WHAT, MODE_WHY[APP.mode]):
        c.approval_mode(APP, _mode_changed)
        c.caption(MODE_NOT_SAVED)
    with c.setting(CONFIRM_WHAT, CONFIRM_WHY):
        for tool in state.AUTO_ALLOWABLE:
            switch = ui.switch(state.WRITE_LABELS[tool]).classes("p-toggle")
            switch.value = tool not in APP.auto_allow
            switch.on_value_change(lambda e, name=tool: _set_confirm(name, bool(e.value)))


def _data() -> None:
    """Which folder is the data, what arrives, and what reading it costs.

    The data folder is **stated here and changed on the add-data panel**, and
    that is deliberate rather than an omission. Picking it is a browse through
    the repo with a count against each folder — a control, not a field — and
    building a second one here would be two pickers that have to agree about
    what counts as a data folder. So this says what the setting is and hands
    you to the one place that sets it.
    """
    with c.setting(DATA_WHAT, DATA_DIR_WHY):
        c.mono(APP.data_dir + "/" if APP.data_dir else NO_DATA_DIR)
        c.button(DATA_OPEN, _add_data, icon="add")
    with c.setting(WAREHOUSE_WHAT, WAREHOUSE_WHY):
        if APP.connection:
            with ui.element("div").classes("row-gap-sm"):
                c.status_light(c.ON if APP.connected else c.OFF)
                c.mono(f"{APP.connection}  ·  {APP.connection_status}")
            switch = ui.switch(AGENT_WRITES_LABEL).classes("p-toggle")
            switch.value = APP.agent_writes
            switch.on_value_change(lambda e: _set_agent_writes(bool(e.value)))
            c.caption(AGENT_WRITES_WHY)
        else:
            c.mono(NO_WAREHOUSE)
            # Drawn only while it can do what it says. It used to open the file
            # panel whatever the project held (2026-09-18).
            if engine.can_change_data(APP):
                c.button(WAREHOUSE_OPEN, _connect_warehouse, icon="cloud")
    with c.setting(INTERPRET_WHAT, INTERPRET_WHY):
        ui.switch(INTERPRET_LABEL).classes("p-toggle").bind_value(APP, "interpret")


def _appearance() -> None:
    """Light and dark are equal first-class modes, with auto as the third.

    Three named options rather than the cycling button this replaces. A control
    that only shows the mode it is *in* cannot distinguish "dark" from "auto, and
    it is night", and a settings panel is exactly where that should be legible.
    """
    with c.setting(THEME_WHAT, THEME_WHY):
        c.segmented(
            [theme.MODE_LABEL[mode] for mode in theme.MODES],
            theme.MODE_LABEL[theme.mode()],
            _set_theme,
        )


def _help() -> None:
    """The way to tell us something, and which build is doing the telling.

    The version is drawn because it is the first thing anybody fixing a bug
    asks, and because a report's box shows the same line (`feedback.version`).
    """
    with c.setting(REPORT_WHAT, REPORT_WHY):
        c.button(feedback.OPEN, _report, icon="outlined_flag")
    with c.setting(VERSION_WHAT):
        c.mono(core_feedback.version())


# --- what the controls do ---------------------------------------------------


#: Section name → what draws it. Defined after the four, and checked against
#: `TABS` by a test: a section with no body renders an empty panel.
_BODY = {
    "Project": _project,
    "Copilot": _copilot,
    "Data": _data,
    "Appearance": _appearance,
    "Help": _help,
}


def _set_confirm(tool: str, ask_first: bool) -> None:
    """Turn one write tool's confirmation on or off.

    Stored as the set of tools that **do not** ask, so the default — every write
    stops — is the empty set rather than a set someone has to remember to fill.
    """
    APP.auto_allow = APP.auto_allow - {tool} if ask_first else APP.auto_allow | {tool}


def _mode_changed() -> None:
    """The picker here moved. Redraw this panel, and the composer's copy of it.

    **Both, always** (`docs/CONVERSATION.md` §14.4): refreshing only this panel
    once left the composer showing the old mode until an unrelated event redrew
    it — a mode running invisibly, found in a browser because nothing in Python
    renders two panes. `exchange.auto_allow` reads the flag at the moment of
    each call, so switching applies to the very next write.
    """
    from portia.ui import transcript

    _panel.refresh()
    transcript.pane.refresh()


def refresh_if_open() -> None:
    """Redraw the panel when a setting it shows was changed somewhere else.

    The composer's picker and the write card's *autopilot* row both set the
    mode this panel displays. Cheap when the dialog is shut, and the reason it
    exists is the same as `_mode_changed`'s.
    """
    if _DIALOG is not None and not _DIALOG.is_deleted and _DIALOG.value:
        _panel.refresh()


def _set_theme(label: str) -> None:
    theme.set_mode(theme.MODE_VALUE[label])
    _panel.refresh()


def _set_effort(effort: str) -> None:
    APP.effort = effort
    _panel.refresh()


def _set_provider(kind: str) -> None:
    from nicegui import background_tasks

    APP.provider = kind
    APP.model = providers.get(kind).default_model
    _panel.refresh()
    background_tasks.create(_list_models(kind))


def _list_models_clicked(kind: str) -> None:
    from nicegui import background_tasks

    background_tasks.create(_list_models(kind))


async def _list_models(kind: str) -> None:
    from portia.ui import transcript

    await engine.list_models(APP, kind)
    _panel.refresh()
    transcript.pane.refresh()


async def _switch_project() -> None:
    """Back to the picker — closing any chat on the way out.

    A chat holds a live SDK subprocess (`docs/CONVERSATION.md` §4), so leaving a
    project without closing it leaks one for as long as the window lives. That
    is `KNOWLEDGE_GRAPH.md` §6.6's shape of bug, and this is the one place a
    project is left on purpose.
    """
    from portia.ui import app as app_module
    from portia.ui import exchange

    if APP.busy:
        ui.notify(SWITCH_BUSY)
        return
    await exchange.close_all()
    _close()
    APP.opened = False
    app_module.shell.refresh()


def _open_brief() -> None:
    from portia.ui import artifacts, workflow

    _close()
    APP.select(BRIEF, "")
    artifacts.pane.refresh()
    workflow.pane.refresh()


def _set_agent_writes(on: bool) -> None:
    """The hand-off (`engine.set_agent_writes`) — a second place to change it, never a second setting."""
    if on != APP.agent_writes:
        engine.set_agent_writes(on, APP)


def _report() -> None:
    _close()
    feedback.open_dialog()


def _add_data() -> None:
    _close()
    screens.open_add_dialog()


def _connect_warehouse() -> None:
    """Answer *where is the data* with a warehouse, then connect to one."""
    _close()
    engine.choose_data(state.WAREHOUSE_DATA, APP)
    screens.open_add_dialog()
    screens.open_connect_dialog()


def _close() -> None:
    if _DIALOG is not None and not _DIALOG.is_deleted:
        _DIALOG.close()

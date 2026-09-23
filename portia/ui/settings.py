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

Six **sections**, in the order they are worth changing: **Project** (where you
are and what it is about) · **Copilot** (what a turn spends, and which writes
stop) · **Providers** (where a model can come from, and how each is,
`ui/providers.py`) · **Data** (what arrives, and where it lands) ·
**Appearance** · **Help** (the way to report a problem, `ui/feedback.py`). A list down
the left and one section's settings on the right *(2026-09-04)* — an editor's
settings page rather than a tab strip over a stack, because the list can hold a
glyph and stays readable at eight sections where a strip is scrolling at five.
Each setting is one `c.setting` row: a title, what it does, and the control.

The dialog is built once at page level — see `screens.build_add_dialog` for what
happens to one created inside a refreshable — and its *contents* are the
refreshable, so opening it can redraw the panel without rebuilding the overlay
it is in.

**A click redraws the section it is in and nothing around it** *(2026-09-23)*.
Picking a section, a theme, an effort or a provider used to call
`_panel.refresh()`, which rebuilt the title, the list and the Close button with
the one section that changed: the whole card went out and came back for a
press that meant *show me the next one*. The section is its own refreshable
(`_section`), picking one moves the list's highlight on the rows that are
already there, and `_panel.refresh()` runs only when the dialog opens.
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
BRIEF_WHAT = "Project brief"
BRIEF_HELP = "The goal, the modelling and the data, in a few sentences. Read on every exchange."
BRIEF_OPEN = "Edit the brief"
SPEND_WHAT = "Provider, model and effort"
MODE_WHAT = "Writes"
#: The per-tool switches, folded behind one button: most people never change
#: them, and open they were the tallest thing in the section (the user,
#: 2026-09-23).
CUSTOMIZE = "Customize"
CONFIRM_WHAT = "Catalog writes that stop for you"
DATA_TITLE = "Data"
DATA_HELP = (
    "A project reads from one place: the files in its folder, or one database connection, "
    "never both. The first source you add fixes which, so pick before adding data. "
    "To use the other, open a new project."
)
DATA_KIND_WHAT = "Where the data lives"
DATA_FILES = "Files"
DATA_DATABASE = "Database"
DATA_UNCHOSEN = "not chosen yet"
DATA_LOCKED = "This project has sources, so where its data lives is fixed."
DATA_WHAT = "Data folder"
NO_DATA_DIR = "not set, the whole repository"
DATA_OPEN = "Add data"
INTERPRET_WHAT = "Read each new source"
INTERPRET_LABEL = "The copilot writes what a source means after it is profiled"
INTERPRET_HELP = "Profiling is free and always happens. Reading spends a model exchange."
WAREHOUSE_WHAT = "Connection"
NO_WAREHOUSE = "not connected"
WAREHOUSE_OPEN = "Connect a database"
AGENT_WRITES_LABEL = "The copilot creates tables as it records steps"
AGENT_WRITES_HELP = (
    "Each recorded step becomes a table in the warehouse, in the schema the copilot chose for "
    "that spec, created if missing. Off, only Run and Build write."
)
THEME_WHAT = "Theme"
#: Said on the dark card. Dark has not been audited screen by screen yet (the
#: user, 2026-09-23), and a mode offered as finished is one nobody reports.
BETA = "beta"
REPORT_WHAT = "Something broke, or could be better"
VERSION_WHAT = "Version"
NO_PANEL = "The settings panel did not load. Reload the page."
STALE_PANEL = "Settings may show stale values ({why}). Reload the page."

#: Whether the per-tool switches under *Writes* are showing. Page state, like
#: `_TAB`, so a redraw of the section does not fold them away under you.
_CUSTOMIZING = False

#: The spider the theme previews hang, read once. A file rather than a string
#: for the stylesheet's reason, and drawn inline so it takes each preview's ink.
SPIDER = theme.ASSETS / "art" / "spider.svg"


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
TABS = ("Project", "Copilot", "Providers", "Data", "Appearance", "Help")
_ICONS = {
    "Project": "folder",
    "Copilot": "forum",
    "Providers": c.GLYPH + "bot",
    "Data": "table_chart",
    "Appearance": "palette",
    "Help": "help_outline",
}

#: Which one is showing. Page state, not project state: it is where you are
#: looking inside a dialog, and it survives the panel being refreshed so that
#: picking a theme does not throw you back to the first section.
_TAB = TABS[0]

#: The list's rows as last drawn, so picking a section moves the highlight on
#: them rather than drawing the list again.
_NAV_ROWS: dict[str, ui.element] = {}
_SELECTED = "artifact-row--selected"


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
                _section()
        with ui.element("div").classes("settings-foot"):
            c.button("Close", _close, kind="secondary")


def _nav() -> None:
    """The sections as a rail of glyphs, each named on hover.

    **Glyphs only since 2026-09-23** (the user's call): the names took 168px
    from a body the Providers section needed for a list and a detail side by
    side, and a provider's own sentence was the thing being ellipsized. The
    row is still `artifact_row`, its name hidden by CSS and said by the
    tooltip, so the rail is the left pane's vocabulary at one more setting.
    """
    _NAV_ROWS.clear()
    with ui.element("div").classes("settings-nav"):
        for tab in TABS:
            row = c.artifact_row(
                name=tab,
                icon=_ICONS[tab],
                selected=tab == _TAB,
                on_click=lambda t=tab: _show_tab(t),
            )
            row.tooltip(tab)
            _NAV_ROWS[tab] = row


@ui.refreshable
def _section() -> None:
    """The showing section's settings: the one part of the panel a click redraws."""
    _BODY[_TAB]()


def _redraw() -> None:
    """After a control here changed something: this section, never the card around it."""
    _section.refresh()


def _show_tab(tab: str) -> None:
    """Show another section: the highlight moves, and only the body is drawn again."""
    global _TAB
    if tab == _TAB:
        return
    _TAB = tab
    for name, row in _NAV_ROWS.items():
        if not row.is_deleted:
            row.classes(add=_SELECTED) if name == tab else row.classes(remove=_SELECTED)
    _section.refresh()


def _project() -> None:
    """Where you are, and the text the whole project is conditioned on.

    The session name used to be a button in the toolbar and the only route out of
    a project — a label that was secretly the exit. Here the name says where you
    are and the exit says what it does.
    """
    with c.setting(PROJECT_WHAT):
        c.path_row(APP.root, icon="folder")
        c.button("Open another project…", _switch_project, icon="folder_open").tooltip(
            SWITCH_BUSY if APP.busy else SWITCH_TIP
        )
    with c.setting(BRIEF_WHAT, help=BRIEF_HELP):
        c.button(BRIEF_OPEN, _open_brief, icon="notes")


def _copilot() -> None:
    """What an exchange spends, and which of its writes stop for you.

    The switches are **per tool** rather than one "confirm writes" flag, because
    the two kinds of write underneath the read/write line are not one decision
    (`state.AUTO_ALLOWABLE`). They bind `App.auto_allow`, which
    `ui/exchange.auto_allow` reads at the moment of each call — so this is a
    second place to *change* the setting and never a second setting. They sit
    folded behind *Customize* (the user, 2026-09-23).
    """
    with c.setting(SPEND_WHAT):
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
    # same field.
    with c.setting(MODE_WHAT):
        c.approval_mode(APP, _mode_changed)
        c.button(CUSTOMIZE, _toggle_customize, icon="remove" if _CUSTOMIZING else "add", micro=True)
        if _CUSTOMIZING:
            with ui.element("div").classes("settings-customize"):
                ui.label(CONFIRM_WHAT).classes("setting-subtitle")
                for tool in state.AUTO_ALLOWABLE:
                    switch = ui.switch(state.WRITE_LABELS[tool]).classes("p-toggle")
                    switch.value = tool not in APP.auto_allow
                    switch.on_value_change(lambda e, name=tool: _set_confirm(name, bool(e.value)))


def _providers() -> None:
    """Every provider, how it is, and whether the picker offers it (`ui/providers.py`).

    Its own section rather than more rows under Copilot *(2026-09-22)*: the
    Copilot section is what one exchange spends, and this is the machine's
    account of where a model can come from at all, which is read when
    something is missing from the picker and edited once.
    """
    from portia.ui import providers as providers_ui

    with c.setting(providers_ui.TITLE):
        providers_ui.section()


def _data() -> None:
    """Where the data is, then what that place needs, then what reading it costs.

    **The kind comes first and is drawn as a choice with one side lit**
    *(2026-09-23, the user: "it should be clear from this menu in which case we
    are")*. A project reads from one place (`engine.can_change_data`), and the
    rule was nowhere in this section, which drew a data folder and a database
    row one above the other as if both applied. The other side is offered while
    nothing is indexed and disabled after, and the *?* beside the heading says
    why.

    The data folder is **stated here and changed on the add-data panel**, and
    that is deliberate rather than an omission. Picking it is a browse through
    the repo with a count against each folder — a control, not a field — and
    building a second one here would be two pickers that have to agree about
    what counts as a data folder.
    """
    changeable = engine.can_change_data(APP)
    with ui.element("div").classes("row-gap-xs settings-heading"):
        ui.label(DATA_TITLE).classes("settings-heading-title")
        c.help_tip(DATA_HELP)
    with c.setting(DATA_KIND_WHAT):
        with ui.element("div").classes("row-gap-sm"):
            with ui.element("div").classes("row-gap-xs segmented-control data-kind"):
                for mode, label, icon in (
                    (state.LOCAL_DATA, DATA_FILES, "folder"),
                    (state.WAREHOUSE_DATA, DATA_DATABASE, c.DATABASE_GLYPH),
                ):
                    active = APP.data_mode == mode
                    b = c.button(
                        label,
                        lambda m=mode: _choose_data(m),
                        icon=icon,
                        micro=True,
                        enabled=active or changeable,
                    )
                    if active:
                        b.classes("seg-active")
            if not APP.data_mode:
                c.state_pill(DATA_UNCHOSEN)
            elif not changeable:
                with ui.icon("lock").classes("help-tip"):
                    ui.tooltip(DATA_LOCKED).props("max-width=300px")
    if APP.data_mode == state.LOCAL_DATA:
        with c.setting(DATA_WHAT):
            if APP.data_dir:
                c.mono(APP.data_dir + "/")
            else:
                c.state_pill(NO_DATA_DIR)
            c.button(DATA_OPEN, _add_data, icon="add")
    elif APP.data_mode == state.WAREHOUSE_DATA:
        with c.setting(WAREHOUSE_WHAT):
            if APP.connection:
                with ui.element("div").classes("row-gap-sm"):
                    c.status_light(c.ON if APP.connected else c.OFF)
                    c.mono(f"{APP.connection}  ·  {APP.connection_status}")
                with ui.element("div").classes("row-gap-xs"):
                    switch = ui.switch(AGENT_WRITES_LABEL).classes("p-toggle")
                    switch.value = APP.agent_writes
                    switch.on_value_change(lambda e: _set_agent_writes(bool(e.value)))
                    c.help_tip(AGENT_WRITES_HELP)
            else:
                c.state_pill(NO_WAREHOUSE)
                c.button(WAREHOUSE_OPEN, _connect_warehouse, icon=c.DATABASE_GLYPH)
    with c.setting(INTERPRET_WHAT, help=INTERPRET_HELP):
        ui.switch(INTERPRET_LABEL).classes("p-toggle").bind_value(APP, "interpret")


def _appearance() -> None:
    """Light and dark are equal first-class modes, with auto as the third.

    **Three previews rather than three words** *(2026-09-23, the user: "this
    menu looks so sad and empty")*. Each is the window in miniature in that
    mode's palette, auto split corner to corner, with the spider from the
    website hanging on its dragline; a pointer over a card lowers it and it
    swings. The card is the control. Dark says *beta*, because nobody has
    audited it screen by screen yet.
    """
    spider = SPIDER.read_text(encoding="utf-8")
    current = theme.mode()
    with c.setting(THEME_WHAT):
        with ui.element("div").classes("theme-cards"):
            for mode in theme.MODES:
                _theme_card(mode, picked=mode == current, spider=spider)


def _theme_card(mode: bool | None, *, picked: bool, spider: str) -> None:
    """One mode, as a small window drawn in its palette, and its name under it."""
    label = theme.MODE_LABEL[mode]
    card = ui.element("div").classes("theme-card" + (" theme-card--picked" if picked else ""))
    with card:
        with ui.element("div").classes(f"theme-preview theme-preview--{label}"):
            # Auto is both palettes, one per half: the window it will be by day
            # and the one it will be by night.
            for palette in ("light", "dark") if mode is None else (label,):
                with ui.element("div").classes(f"tp-window tp-{palette}"):
                    ui.element("div").classes("tp-bar")
                    with ui.element("div").classes("tp-body"):
                        with ui.element("div").classes("tp-side"):
                            for _ in range(3):
                                ui.element("div").classes("tp-line")
                        with ui.element("div").classes("tp-main"):
                            ui.element("div").classes("tp-card")
                            ui.element("div").classes("tp-line tp-line--short")
                    with ui.element("div").classes("tp-hang"):
                        ui.element("div").classes("tp-silk")
                        ui.html(spider).classes("tp-spider")
        with ui.element("div").classes("row-gap-xs theme-card-name"):
            ui.label(label.capitalize())
            if mode is True:
                ui.label(BETA).classes("theme-beta")
    card.on("click", lambda m=label: _set_theme(m))


def _help() -> None:
    """The way to tell us something, and which build is doing the telling.

    The version is drawn because it is the first thing anybody fixing a bug
    asks, and because a report's box shows the same line (`feedback.version`).
    """
    with c.setting(REPORT_WHAT):
        c.button(feedback.OPEN, _report, icon="outlined_flag")
    with c.setting(VERSION_WHAT):
        c.mono(core_feedback.version())


# --- what the controls do ---------------------------------------------------


#: Section name → what draws it. Defined after the four, and checked against
#: `TABS` by a test: a section with no body renders an empty panel.
_BODY = {
    "Project": _project,
    "Copilot": _copilot,
    "Providers": _providers,
    "Data": _data,
    "Appearance": _appearance,
    "Help": _help,
}


def _toggle_customize() -> None:
    global _CUSTOMIZING
    _CUSTOMIZING = not _CUSTOMIZING
    _redraw()


def _choose_data(mode: str) -> None:
    """Answer *where is the data* from here: files at once, a database through its dialog."""
    if mode == APP.data_mode or not engine.can_change_data(APP):
        return
    if mode == state.WAREHOUSE_DATA:
        _connect_warehouse()
        return
    engine.choose_data(mode, APP)
    _redraw()


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

    _redraw()
    transcript.pane.refresh()


def refresh_if_open() -> None:
    """Redraw the panel when a setting it shows was changed somewhere else.

    The composer's picker and the write card's *autopilot* row both set the
    mode this panel displays. Cheap when the dialog is shut, and the reason it
    exists is the same as `_mode_changed`'s.
    """
    if _DIALOG is not None and not _DIALOG.is_deleted and _DIALOG.value:
        _redraw()


def _set_theme(label: str) -> None:
    theme.set_mode(theme.MODE_VALUE[label])
    _redraw()


def _set_effort(effort: str) -> None:
    APP.effort = effort
    _redraw()


def _set_provider(kind: str, model: str | None = None) -> None:
    from nicegui import background_tasks

    APP.provider = kind
    APP.model = model or providers.get(kind).default_model
    _redraw()
    background_tasks.create(_list_models(kind))


def _list_models_clicked(kind: str) -> None:
    from nicegui import background_tasks

    background_tasks.create(_list_models(kind))


async def _list_models(kind: str) -> None:
    from portia.ui import transcript

    await c.list_models_behind_picker(kind, _redraw, transcript.pane.refresh)


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

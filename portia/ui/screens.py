"""The screens before the three panes — and the one gate in the app.

These exist so a test run never needs a terminal (docs/VISION.md → "The
no-terminal audit"): creating the project, writing its brief, and adding the
data files are all in the window, or the bar is not met.

Three states, in order:

- **No project open.** A path field and Open, plus the recent projects. A path
  that doesn't exist yet is *created* — testing means a fresh directory per run,
  so that has to be one action rather than an error followed by a second one.
- **No context set.** The mandatory brief. No skip, no dismiss, no "later".
- **No sources.** Add data — rewritten 2026-08-02, and described below.

## Add data — two routes, in the order they are likely

portia plugs into a repo that **already holds the data** (`docs/PIPELINE.md`
§2.7), so the screen asks that question first and the other one second:

1. **Point at the data already here.** An in-page folder browser rooted at the
   project: sub-folders that have readable data under them, with a count, clicked
   into until you reach the one that is the data. Choosing it writes ``data_dir``
   to ``project.yaml`` — a durable project setting, not a one-off — and from then
   on the left pane draws un-indexed data files under that folder and nowhere
   else. Its files arrive **all ticked**, because "this folder is my data" is a
   statement about the folder; un-ticking is for the exceptions.
2. **Import external data**, folded away until it is wanted. One button: the
   native chooser, which plans on return. Its destination defaults to the folder
   chosen in (1), and to ``data/`` if nothing was chosen, because a file arriving
   somewhere other than where the rest of the data lives is a folder layout
   nobody asked for.

Both at once is the ordinary case, not an edge one: a repo with a `data/` folder
and one extract still sitting in `~/Downloads`.

**Bringing outside data in stays a deliberate step.** ``index`` only accepts
files already inside the repo, so this screen is the way one gets in: you choose
where it lands, portia states exactly what it is about to copy and to where, and
only then does it copy. The plan is shown in full rather than summarised — "3
files into data/" describes a plan, the list *is* one, and the difference is the
one time a wrong folder or a name collision is cheap to notice. It is a copy,
never a move.

**One button does the work, and it says what the work is.** Both routes converge
on a single *Index* — copy what was planned, then profile every ticked file —
because two buttons each half-doing it is what this screen was before, and the
one that planned an import did not index while the one that read "Continue" did.
When it finishes, the primary action **becomes the way into the workspace**, and
not before: a CTA offered beside unfinished work is a skip button wearing a
different word.

Indexing is deliberately shown as **two** things, because one is free and one is
not: profiling is deterministic and always happens, and interpretation is a model
turn that runs through the ordinary transcript with its own write confirmations.
Never one merged spinner.

**The read starts on its own, and the popup is how it reaches you** (2026-08-07).
The turn used to wait for *Open the workspace* to be pressed, which spent the
wait twice — profiling finished, the screen went quiet, and the minute of model
time only began once you noticed. Now it starts the moment profiling ends, with
this screen still up and saying so. That leaves one gap, which the popup closes:
the copilot can stop to ask, or to have a write allowed, while the form it needs
is on a screen you are not looking at. So a stop **invites** you through
(`state.prompt_for_decision`) rather than swapping the window under you, which is
what it used to do — moving screens is the human's move to make, and it was
taking the screen away mid-tick.

**The browser drop zone is gone** (2026-08-02). It was a third route that did the
same job as the other two while being the only one that streamed the file through
the browser — which meant a silent refusal on files the browser dislikes, a red
triangle on all twenty when one was rejected, and a copy that had already
happened by the time the plan could have been shown. Every path it served is
served by the folder picker (already in the repo) or the importer (not yet).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nicegui import context, ui

from portia.agent import prompts
from portia.core import cancel
from portia.ui import components as c
from portia.ui import engine, picktree, state, tree
from portia.ui.engine import DATA_DIR
from portia.ui.state import APP

# --- project-open -----------------------------------------------------------


def project_open() -> None:
    from portia.ui import theme

    with ui.element("div").classes("p-centered"):
        with ui.element("div").classes("p-centered-column"):
            with ui.element("div").classes("row-gap-md"):
                theme.logo()
                ui.label("portia").classes("t-display")
            c.text(_OPEN_SUBTITLE, color="c-mute")

            # Browse is the way in. Typing an absolute path is the fallback — for
            # a machine with no chooser, or for a directory that doesn't exist
            # yet, which the chooser cannot express and a fresh run needs — so it
            # is folded away rather than offered as the obvious thing to do.
            if engine.can_browse():
                with ui.element("div").classes("row-gap-sm"):
                    c.button("Browse…", _browse, kind="primary", icon="folder_open")
            _by_path(_open, placeholder=str(Path.home() / "portia-run1"), label=_OPEN_NEW)

            _recents()


def _by_path(submit, *, placeholder: str, label: str) -> None:
    """The folded-away path field: a link, then the field once it is wanted."""
    reveal = ui.element("div").classes("row-gap-sm")
    field = ui.element("div").classes("row-gap-sm w-full")
    field.set_visibility(False)

    with reveal:
        c.button(label, lambda: _reveal(reveal, field), kind="secondary", micro=True)
    with field:
        path = (
            ui.input(placeholder=placeholder)
            .classes("p-field p-field-mono flex-1")
            .props("borderless")
        )
        c.button("Open", lambda: submit(path.value), kind=_path_kind())


def _reveal(hide: ui.element, show: ui.element) -> None:
    hide.set_visibility(False)
    show.set_visibility(True)


def _path_kind() -> str:
    """One accent action: Browse where there is one, Open where there isn't."""
    return "tertiary" if engine.can_browse() else "primary"


async def _browse() -> None:
    chosen = await engine.browse_for_folder()
    if chosen is not None:
        _open(str(chosen))


def _recents() -> None:
    entries = engine.recents()
    if not entries:
        return
    c.rule()
    c.section_header("Recent")
    for root, opened in entries:
        # The directory name reads; the full path is what identifies it. Both,
        # rather than one truncated line that manages to be neither.
        c.artifact_row(
            name=root.name or str(root),
            icon="folder",
            note=str(root),
            meta=opened,
            on_click=lambda r=root: _open(str(r)),
        )


def _open(raw: str) -> None:
    from portia.ui import app as app_module

    if not (raw or "").strip():
        ui.notify("Enter a path first.")
        return
    try:
        engine.open_project(raw.strip(), APP)
    except OSError as exc:
        ui.notify(f"{type(exc).__name__}: {exc}")
        return
    APP.opened = True
    _pick_up_spec()
    # The warehouse session, if the project names one, opens off the loop and
    # the pane says how it went (`artifacts.connect_in_background`).
    from portia.ui import artifacts

    artifacts.connect_in_background()
    # The canvas view survives a pane refresh on purpose (`assets/canvas.js`), so
    # it would otherwise survive a change of project too — and a new project's
    # graph opening panned off-screen at 40% reads as a window that failed to draw.
    ui.run_javascript("portiaRecenter()")
    app_module.shell.refresh()


def _pick_up_spec() -> None:
    """Open the project's first spec, if it already has one — and open its card.

    Shared with `app.open_at_start`, which is the same moment reached from
    `--project` instead of from the picker.
    """
    from portia.ui import app as app_module

    app_module._pick_up_spec()


# --- project-context — the one gate -----------------------------------------


def project_context() -> None:
    """The most consequential text box in the product.

    The context is what makes a column's meaning decidable, and a generic brief
    yields generic judgment (`PLAN.md`). What it asks for is the **project**, not
    the data: the goal, how it is modelled, and roughly what sources exist —
    `VISION.md`'s "the *global* project, not necessarily the data".

    **The same card as add data** (`p-panel`, in `p-panel--prose` width): the
    question and the box, and the file it writes pinned in the footer beside the
    button that writes it. As a bare column on the canvas it was a heading, a
    text box and four paragraphs of loose text with a raw path trailing under
    them — a screen with nothing holding it together.

    **It says far less than it used to** (2026-08-04). The guidance had grown
    into the notes that described this screen while it was being designed: four
    shape lines, a rule about what not to write, and a four-sentence worked
    example. That is a briefing about writing a brief, and it dwarfed the box it
    was meant to introduce. One line of shape survives; the register is taught by
    the question, which is a short one.
    """
    with ui.element("div").classes("p-centered"):
        with ui.element("div").classes("p-panel p-panel--prose"):
            with ui.element("div").classes("p-panel-head"):
                ui.label("What is this project?").classes("t-heading-md")

            with ui.element("div").classes("p-panel-body"):
                box = (
                    ui.textarea(placeholder=CONTEXT_PLACEHOLDER)
                    .classes("p-field p-editor p-editor--tall w-full")
                    .props("borderless autofocus")
                )
                box.bind_value(APP, "goal")  # reused as scratch until it is saved
                if APP.editing_brief and not APP.goal:
                    # Back from a later screen: the brief as it was saved.
                    box.value = APP.project_context
                context_guidance()

            with ui.element("div").classes("p-panel-actions"):
                with ui.element("div").classes("row-gap-sm"):
                    c.button("Continue", lambda: _save_context(box.value), kind="primary")
                    # The gate has no skip, but it must have a way back: choosing
                    # the wrong folder is easy, and the only other exit was the
                    # process.
                    c.button("Back", _back_to_picker, kind="secondary")
                c.path_row(APP.catalog_dir / "project.yaml")


def context_guidance() -> None:
    """The one line saying what shape a brief takes.

    One function rather than a list each caller loops over, because the gate and
    the brief pane are the same box in two places and the day they teach it
    differently is the day one of them is wrong.
    """
    c.caption(CONTEXT_SHAPE, color="c-stone")


def _back_to_brief() -> None:
    """One screen back from *where is the data* or from add data: the brief.

    Both screens' Back went to the project picker *(until 2026-09-18, the
    user's report)*, which closed the project to fix a typo in it. The brief
    had no way back to it at all until the workspace was open. The shell draws
    the brief while `editing_brief` is set, Continue saves and clears it, and
    the screen after it is whichever one this project was already on: where
    the data is has been answered and is kept.
    """
    from portia.ui import app as app_module

    APP.editing_brief = True
    APP.goal = APP.project_context
    app_module.shell.refresh()


def _back_to_picker() -> None:
    """Return to the project picker without writing anything.

    A directory the picker created on the way in is left where it is — an empty
    folder is cheap, and deleting one on a cancel is the kind of helpfulness
    nobody asked for.
    """
    from portia.ui import app as app_module

    APP.opened = False
    APP.goal = ""
    APP.editing_brief = False
    app_module.shell.refresh()


def _save_context(text: str) -> None:
    from portia.ui import app as app_module

    if not (text or "").strip():
        ui.notify("The brief cannot be empty.")
        return
    engine.set_context(text, APP)
    APP.goal = ""
    APP.editing_brief = False
    app_module.shell.refresh()


# --- add data ---------------------------------------------------------------


def add_data() -> None:
    """The screen: a floating panel on the canvas, not a page.

    It is a **card of bounded height**, centred, with its own scroll — the same
    shape as the dialog it doubles as, because it is the same thing: a transient
    surface you are meant to finish and leave. Laid out as a full-height column
    it read as a web page, and the taller the file list got the more it read as
    one, with the heading and the button drifting to opposite ends of the window.
    """
    with ui.element("div").classes("p-centered"):
        with ui.element("div").classes("p-panel"):
            panel()


@ui.refreshable
def panel(*, in_dialog: bool = False) -> None:
    """Everything on the add-data surface, in one refreshable.

    **Both the screen and the dialog draw this**, with `in_dialog` deciding only
    what the way out is called — from the screen it opens the workspace, from the
    dialog the workspace is already behind you and it just closes. The two used
    to be separately-assembled stacks of the same four components, which is how
    the dialog ended up without the destination field for a week.

    **Three regions, and only the middle one scrolls.** What you are doing stays
    at the top and what finishes it stays at the bottom, however long the file
    list gets — a panel where the primary action is somewhere below the fold is a
    panel that looks like it did nothing when you press it.
    """
    if not APP.data_mode:
        # The dialog's copy of *where is the data*. The first-run screen asks it
        # as a screen of its own (`choose_data`); from the workspace the panel
        # is all there is, and **Change** in the head below lands here.
        _choice_in_panel(in_dialog=in_dialog)
        return
    remote = APP.data_mode == state.WAREHOUSE_DATA
    with ui.element("div").classes("p-panel-head p-panel-head--split"):
        with ui.element("div").classes("p-panel-head-text"):
            ui.label("Add data").classes("t-heading-md")
            ui.label(
                ADD_WHY_WAREHOUSE.format(name=APP.connection or "a warehouse")
                if remote
                else ADD_WHY.format(formats=_formats())
            ).classes("p-panel-sub")
        _data_kind(remote)
    with ui.element("div").classes("p-panel-body add-data-body"):
        # Two columns where there is room for two: the question you are almost
        # always answering on the left, the one you are usually not on the right.
        # A wide panel laid out as one stacked column is a wide panel that reads
        # as a narrow one with empty space beside it. **One kind of data per
        # screen** (`state.LOCAL_DATA`): the warehouse layout is the picker and
        # the read; the local one is the folder, the importer and the read.
        with ui.element("div").classes("add-data-col"):
            _warehouse_route() if remote else _in_repo()
        with ui.element("div").classes("add-data-col"):
            if remote:
                _profile_toggle()
            else:
                _from_outside()
            _interpret_toggle()
        with ui.element("div").classes("add-data-progress"):
            _progress()
    with ui.element("div").classes("p-panel-actions"):
        # No rule: the region's own top border is the divider, and two lines a
        # pixel apart is what a rule inside a bordered footer looks like.
        _actions(in_dialog=in_dialog)


def _data_kind(remote: bool) -> None:
    """Which kind of data this project reads, and the way to answer differently.

    **The answer was final from the press of a card** *(until 2026-09-18, the
    user's report)*: Back skipped the question and went to the brief, and the
    Settings row that said *Connect a warehouse* opened the file panel. It is
    open until something is indexed (`engine.can_change_data`), because a
    project reads from one place and the first source is what would have to be
    mixed with. After that the chip stays and the button is not drawn.
    """
    with ui.element("div").classes("data-kind"):
        ui.icon("cloud" if remote else "folder").classes("data-kind-icon")
        ui.label(CHOOSE_WAREHOUSE if remote else CHOOSE_LOCAL).classes("data-kind-name")
        if engine.can_change_data(APP):
            c.button("Change", _reopen_choice, kind="secondary", micro=True)


def _reopen_choice() -> None:
    from portia.ui import app as app_module

    engine.reopen_data_choice(APP)
    if APP.on_add_data:
        app_module.shell.refresh()
    else:
        _refresh()


def _choice_cards() -> None:
    with ui.element("div").classes("choice-grid"):
        c.choice_card(
            CHOOSE_LOCAL,
            CHOOSE_LOCAL_WHY,
            icon="folder",
            on_click=lambda: _choose(state.LOCAL_DATA),
        )
        c.choice_card(
            CHOOSE_WAREHOUSE,
            CHOOSE_WAREHOUSE_WHY.format(providers=_providers_sentence()),
            icon="cloud",
            on_click=lambda: _choose(state.WAREHOUSE_DATA),
        )


def _choice_in_panel(*, in_dialog: bool) -> None:
    with ui.element("div").classes("p-panel-head"):
        ui.label(CHOOSE_TITLE).classes("t-heading-md")
        ui.label(CHOOSE_WHY).classes("p-panel-sub")
    with ui.element("div").classes("p-panel-body"):
        _choice_cards()
    with ui.element("div").classes("p-panel-actions"):
        with ui.element("div").classes("row-gap-sm"):
            c.button(_leave_label(in_dialog), lambda: _leave(in_dialog), kind="secondary")


def _refresh() -> None:
    """Redraw the surface. Both instances; only one of them is ever on screen."""
    panel.refresh()


# --- the tick tree, which both routes draw ----------------------------------

#: The glyph per container kind. A leaf has none: its name is its box's label.
_TREE_ICONS = {tree.DATABASE: "storage", tree.SCHEMA: "schema", tree.FOLDER: "folder"}


@dataclass(frozen=True)
class _TreeView:
    """What one drawing of a tick tree needs besides its rows.

    ``whole`` is the tree before the filter, which the counts are read from: a
    container's **box** speaks for the rows on screen and its **count** for
    everything under it, so a tick the filter hid is still in the number
    (`picktree.py`). The callbacks are the route's own, because a file's tick
    is an exclusion and a table's is a selection.
    """

    name: str
    whole: tuple[picktree.Item, ...]
    ticks: frozenset[str]
    is_open: Callable[[picktree.Item], bool]
    toggle: Callable[[str], Any]
    tick_leaf: Callable[[str, bool], Any]
    tick_under: Callable[[str, bool], Any]
    unit: str
    fixed_word: str
    filtering: bool = False
    loading: str | None = None


#: Every box and count on screen, by tree and then by row key, so a tick can
#: change what the rows *say* without rebuilding them (`_sync_tree`). A list per
#: key because the panel is drawn twice, as the screen and as the dialog; an
#: element a redraw deleted is dropped the next time its key is read.
_DRAWN: dict[str, dict[str, list[tuple[ui.checkbox, ui.label | None]]]] = {}


def _drawn(view: _TreeView, key: str, box: ui.checkbox, meta: ui.label | None = None) -> None:
    _DRAWN.setdefault(view.name, {}).setdefault(key, []).append((box, meta))


def _sync_tree(view: _TreeView, items: tuple[picktree.Item, ...]) -> None:
    """Make every row on screen say what the ticks now are, **in place**.

    A tick changes boxes and counts and moves no row, so nothing is rebuilt:
    redrawing a 400-table schema per tick was 186 ms to the browser, measured,
    and threw the list back to its first row. Setting ``value`` from here
    raises no event, because the rows listen for Quasar's own
    ``update:model-value``, which only a person's press emits.
    """
    rows = _DRAWN.get(view.name, {})
    for item in items:
        live = [(box, meta) for box, meta in rows.get(item.key, []) if not box.is_deleted]
        rows[item.key] = live
        value: bool | None
        text: str | None = None
        if item.leaf:
            value = item.fixed or item.key in view.ticks
        else:
            shown = picktree.tally(item, view.ticks)
            value = _BOX_VALUE[shown.state]
            for box, _meta in live:
                box.set_enabled(_offers(shown))
            whole = picktree.find(view.whole, item.key) or item
            text = _tally_text(picktree.tally(whole, view.ticks), view.unit, view.fixed_word)
            _sync_tree(view, item.children or ())
        for box, meta in live:
            if box.value is not value:
                box.value = value
            if meta is not None and meta.text != text:
                meta.text = text


def _pressing(tick: Callable[[str, bool], Any], key: str) -> Callable[[bool], Any]:
    """One row's press, bound to its key."""

    def pressed(on: bool) -> Any:
        return tick(key, on)

    return pressed


def _offers(shown: picktree.Tally) -> bool:
    """Whether a container's box has anything to take. Only a fully listed one
    with nothing left under it does not: an unlisted one is ticked by listing it."""
    return bool(shown.of) or not shown.complete


def _on_press(box: ui.checkbox, handler: Callable[[bool], Any]) -> None:
    """Call ``handler`` when a **person** presses the box, with what it became.

    Quasar's own ``update:model-value`` rather than `on_value_change`: that one
    also fires when `_sync_tree` sets a value, and a folder told it is now
    empty would untick its files a second time. The event carries the value
    and the DOM event; ``[None]`` keeps the first, which is NiceGUI's own
    spelling for the same listener, and without it the payload is a list that
    is true whatever the box became (found by pressing a full schema off, in
    the browser, 2026-09-18).
    """
    box.on("update:model-value", lambda e: handler(bool(e.args)), [None])


#: What a container's box holds for each state. ``None`` is Quasar's dash.
_BOX_VALUE = {picktree.ALL: True, picktree.SOME: None, picktree.NONE: False}


def _tree_rows(view: _TreeView, items: tuple[picktree.Item, ...], depth: int = 0) -> None:
    """The rows, a level at a time. While a filter is on, every listed container
    is drawn open: a match inside a shut schema is a match nobody sees."""
    for item in items:
        if item.leaf:
            _tree_leaf(view, item, depth)
            continue
        opened = item.listed and (view.filtering or view.is_open(item))
        _tree_node(view, item, depth, opened)
        if opened:
            if item.children:
                _tree_rows(view, item.children, depth + 1)
            else:
                with (
                    ui.element("div")
                    .classes("tree-row tree-row--empty")
                    .style(f"--depth: {depth + 1}")
                ):
                    ui.label(SCOPE_EMPTY if view.unit == "table" else NOTHING_UNDER).classes(
                        "tree-row-meta"
                    )


def _tree_node(view: _TreeView, item: picktree.Item, depth: int, opened: bool) -> None:
    """A container: a twistie, a box with three states, the glyph, the name, the count.

    **The whole row unfolds it**, as a row does in an editor's file tree: the
    twistie is where the eye goes and 16px is not where a hand should have to.
    The box is the one part that does something else, so its click stops there.

    The box is ``None`` for *some*, which Quasar draws as the dash, and a press
    on a dash ticks the rest. It is disabled only when a fully listed container
    has nothing left to take (`_offers`): an unlisted one can be ticked, and
    the press lists it first (`_tick_schema`).
    """
    shown = picktree.tally(item, view.ticks)
    total = picktree.tally(picktree.find(view.whole, item.key) or item, view.ticks)
    with ui.element("div").classes("tree-row tree-row--node").style(f"--depth: {depth}") as row:
        if view.loading == item.key:
            ui.spinner(size="12px").classes("tree-twistie")
        else:
            ui.icon("chevron_right").classes("tree-twistie" + (" is-open" if opened else ""))
        box = _tree_box(_BOX_VALUE[shown.state])
        box.set_enabled(_offers(shown))
        _on_press(box, _pressing(view.tick_under, item.key))
        ui.icon(_TREE_ICONS[item.kind]).classes("tree-row-icon")
        ui.label(item.name).classes("tree-row-name")
        meta = ui.label(_tally_text(total, view.unit, view.fixed_word)).classes("tree-row-meta")
    row.on("click", lambda k=item.key: view.toggle(k))
    _drawn(view, item.key, box, meta)


def _tree_leaf(view: _TreeView, item: picktree.Item, depth: int) -> None:
    """One file or table to take, or one already in.

    **The whole row is the hit target**, not a 15px box beside text you cannot
    click. It was the checkbox's own label until the row got a glyph between
    the two; now the row takes the press and the box stops its own click, or
    both would fire on the box and cancel each other out.

    A leaf that is already in keeps its place, because it is still part of what
    is under this container, but its box is disabled and the row takes no
    press: it states a fact instead of offering an action.
    """
    classes = "tree-row tree-row--leaf" + (" tree-row--done" if item.fixed else "")
    with ui.element("div").classes(classes).style(f"--depth: {depth}") as row:
        ui.element("span").classes("tree-twistie")
        box = _tree_box(item.fixed or item.key in view.ticks)
        ui.icon(_LEAF_ICONS.get(item.detail or item.kind, "table_chart")).classes("tree-row-icon")
        ui.label(item.name).classes("tree-row-name")
        if item.fixed:
            box.props("disable")
            ui.label(item.note).classes("tree-row-meta")
        else:
            if item.detail:
                ui.label(item.detail).classes("tree-row-meta")
            _on_press(box, _pressing(view.tick_leaf, item.key))
            row.on("click", lambda k=item.key, b=box: view.tick_leaf(k, not b.value))
    _drawn(view, item.key, box)


#: The glyph per leaf, by its own kind where it has one (a view) and by what it
#: is otherwise. A file of rows and a table are the same glyph on purpose.
_LEAF_ICONS = {"view": "table_view", picktree.FILE: "table_chart", picktree.TABLE: "table_chart"}


def _tree_box(value: bool | None) -> ui.checkbox:
    """A row's box: small, label-less, and deaf to the row's own click handler."""
    box = ui.checkbox(value=value).classes("p-check tree-box").props("dense")
    box.on("click.stop", js_handler="() => {}")
    return box


def _tally_text(total: picktree.Tally, unit: str, fixed_word: str) -> str:
    """What a container holds, as counts: *3 of 12*, *12 tables*, *4 in scope*.

    Numbers of things, never sized or coloured by how big they are
    (`DESIGN.md`). A container that is not fully listed has no total, so it
    states the ticks alone; one with nothing listed says nothing.
    """
    parts = []
    if total.ticked:
        parts.append(
            TALLY_OF.format(n=total.ticked, of=total.of)
            if total.complete
            else TALLY_SOME.format(n=total.ticked)
        )
    elif total.complete and (total.of or not total.fixed):
        parts.append(c.count(total.of, unit))
    if total.fixed:
        parts.append(f"{total.fixed} {fixed_word}")
    return " · ".join(parts)


def _tree_filter(value: str, on_change: Callable[[str], Any]) -> None:
    """The filter box above a tree. **Outside the tree's refreshable**, so typing
    redraws the rows and never the box the caret is in."""
    box = ui.input(value=value, placeholder=FILTER_PLACEHOLDER)
    box.classes("p-field p-input w-full tree-filter")
    box.props("dense borderless hide-bottom-space clearable debounce=200")
    with box.add_slot("prepend"):
        ui.icon("search").classes("tree-filter-icon")
    box.on_value_change(lambda e: on_change(str(e.value or "")))


# --- route one: the data already in the repo --------------------------------


def _in_repo() -> None:
    """Which folder in this repo is the data — the first question, always.

    portia plugs into a project that already holds its data (`PIPELINE.md` §2.7),
    so the common case is that nothing needs importing at all and this section is
    the whole screen.

    **A titled card, not a caption over a stack.** The screen asks two unrelated
    questions and the only thing separating them used to be an 11px uppercase
    label — `p-section-header`, which is a *pane* label and far too quiet to
    divide a form. Each route is a bordered section with a real heading and a
    line saying what it is for.
    """
    with ui.element("div").classes("add-section"):
        _section_head(IN_REPO_HEADING, IN_REPO_WHY)
        if APP.data_dir and not APP.repicking:
            _chosen_folder()
        else:
            _picker()


def _section_head(title: str, hint: str) -> None:
    """A section's title and the one line saying what it is for."""
    with ui.element("div").classes("add-section-head"):
        ui.label(title).classes("add-section-title")
        ui.label(hint).classes("add-section-hint")


def _picker() -> None:
    """An in-page folder browser, rooted at the project and filtered to data.

    **Rooted at the project, and it cannot express anything else** — choosing the
    data folder is choosing a scope inside the repo, so an outside path is not a
    wrong answer this control can give. That is the same rule `index` enforces,
    and it is one error message that never has to be written.

    A folder is offered only if there is readable data somewhere under it, and it
    carries the count. Six identical folder rows is the shape this screen has to
    resolve in one look, and the count is what resolves it — it is a number of
    files, not a measurement of anything in them, so it ranks nothing
    (`DESIGN.md`).

    **It draws its own rows rather than reusing `artifact-row`.** That component
    is tuned for the 260px left pane — 12px mono, no border, hover as its only
    affordance, indent guides for a tree. Dropped into a 560px form it read as
    text that happened to be indented, and nothing on it said it could be
    clicked. These rows are a bordered list with a trailing chevron: the chevron
    is what says *this goes somewhere*.
    """
    choices = engine.folder_choices(APP, APP.browse_at)
    files = engine.data_files_in(APP, APP.browse_at)
    here = len(files)
    with ui.element("div").classes("picker"):
        _crumbs()
        for choice in choices:
            _folder_row(choice)
        if not choices:
            with ui.element("div").classes("picker-empty"):
                c.caption(NO_SUBFOLDERS)
        _picker_files(files)
    if not here and not any(choice.has_data for choice in choices):
        c.empty_note(NOTHING_HERE.format(formats=_formats()))
    with ui.element("div").classes("add-section-actions"):
        c.button(
            _use_label(),
            lambda: _choose_folder(APP.browse_at),
            kind="primary" if here else "tertiary",
            enabled=bool(here),
        )
        if APP.repicking:
            # Re-picking has to be abandonable, or "change the folder" is a
            # button that can only ever lose the setting you already had.
            c.button("Keep " + _folder_label(APP.data_dir), _keep_folder, kind="secondary")
    c.caption(_here_note(here))


def _folder_row(choice) -> None:
    """One folder: name, how much data is under it, and a chevron if it has any.

    **A folder with nothing readable under it is drawn, quietly, and does not
    open.** Leaving those out made the list look like the directory and silently
    not be it, so a folder you knew existed and could not see cost a trip to a
    terminal to explain. Drawn dim with an open outline, it answers the question
    instead: *this is here, and there is nothing in it portia can read.*

    Dimming here is not `DESIGN.md`'s forbidden ranking — it is not saying one
    folder is better than another, it is saying one of them is not a thing you
    can pick. That is a **kind**, and kind is exactly what prominence may say.
    """
    row = ui.element("div").classes("picker-row" if choice.has_data else "picker-row is-empty")
    with row:
        ui.icon("folder" if choice.has_data else "folder_open").classes("picker-row-icon")
        ui.label(choice.name).classes("picker-row-name")
        ui.label(c.count(choice.files, "file") if choice.has_data else NO_DATA_HERE).classes(
            "picker-row-meta"
        )
        if choice.has_data:
            ui.icon("chevron_right").classes("picker-row-go")
    if choice.has_data:
        row.on("click", lambda rel=choice.rel: _browse_to(rel))


#: How many of the eligible files the picker names before it stops listing and
#: starts counting. Enough to recognise the folder — you are answering "is this
#: the data", not reading an inventory.
PICKER_FILES_SHOWN = 6


def _picker_files(files: list[Path]) -> None:
    """The eligible files under here, **before** you commit to the folder.

    The count alone told you *how many* and never *which*, so picking between
    two plausible folders meant choosing one, looking, and going back. These are
    the files that would be profiled if you pressed the button, which is the
    thing the button is actually asking about.
    """
    if not files:
        return
    with ui.element("div").classes("picker-files"):
        for path in files[:PICKER_FILES_SHOWN]:
            with ui.element("div").classes("picker-file"):
                ui.icon("table_chart").classes("picker-file-icon")
                ui.label(_rel(path).as_posix()).classes("picker-file-name")
        if len(files) > PICKER_FILES_SHOWN:
            c.caption(MORE_FILES.format(n=len(files) - PICKER_FILES_SHOWN))


def _crumbs() -> None:
    """The way back up, as the path you came down.

    A back button would only undo one step; the trail says where you are, which
    on a screen whose whole job is "which folder" is the more useful of the two.

    The folder you are *in* is not a link. It was a button like the rest, which
    offered you a trip to where you already were and made the trail read as a row
    of chips rather than as a path.
    """
    trail = engine.crumbs(APP, APP.browse_at)
    with ui.element("div").classes("picker-crumbs"):
        for i, (rel, name) in enumerate(trail):
            if i:
                ui.icon("chevron_right").classes("crumb-sep")
            if i == len(trail) - 1:
                ui.label(name).classes("crumb crumb--current")
                continue
            crumb = ui.label(name).classes("crumb")
            crumb.on("click", lambda r=rel: _browse_to(r))


def _chosen_folder() -> None:
    """The folder, and every readable file under it, ticked.

    **All ticked, because "this folder is my data" is a statement about the
    folder.** Un-ticking is for the exception — the fixture, the export someone
    left behind — and a list that arrives empty would make the ordinary case
    thirty clicks.

    A file already in the catalog is **not offered at all** — greyed, un-tickable,
    and saying it is done. Re-profiling is idempotent (`catalog` → the update rule
    preserves prose and roles), so it is not *wrong* to run it again; it is a
    minute of work on real extracts that nobody asked for, and a tickable box is
    this screen suggesting you spend it. Re-indexing one file stays available
    where it belongs — on that source, in the workflow pane.
    """
    files = engine.data_files_in(APP, APP.data_dir)
    with ui.element("div").classes("chosen-folder"):
        ui.icon("folder").classes("chosen-folder-icon")
        with ui.element("div").classes("chosen-folder-body"):
            ui.label(_folder_label(APP.data_dir)).classes("chosen-folder-name")
            ui.label(c.count(len(files), "file")).classes("chosen-folder-meta")
        c.button("Change…", _repick, kind="secondary", micro=True)
    if not files:
        c.empty_note(NOTHING_HERE.format(formats=_formats()))
        return
    todo = picktree.leaves(_file_items())
    with ui.element("div").classes("pick-head"):
        ui.label(PICK_WHICH if todo else ALL_DONE).classes("pick-head-label")
        if todo:
            c.button("All", lambda: _tick_shown(True), micro=True)
            c.button("None", lambda: _tick_shown(False), micro=True)
    if len(files) >= FILTER_FROM:
        _tree_filter(APP.pick_filter, _filter_files)
    with c.scroll_area("pick-files", classes="pick-tree"):
        _file_tree()


#: How many files the data folder holds before the tree gets a filter box. Under
#: this the whole list is on screen and a box to search it is a control with
#: nothing to do.
FILTER_FROM = 12


def _file_items() -> tuple[picktree.Item, ...]:
    """The files under the data folder as a tree, the indexed ones marked.

    Recomputed from disk on every draw, for `_ticked`'s reason: a file imported
    into the middle of the folder is a row without a second click.
    """
    rels = [_rel(p).as_posix() for p in engine.data_files_in(APP, APP.data_dir)]
    notes = {rel: INDEXED_NOTE for rel in _indexed_paths()}
    return picktree.from_paths(rels, APP.data_dir, notes)


@ui.refreshable
def _file_tree() -> None:
    """Folders and files under the data folder, every box saying what is under it.

    **A folder is a box now** *(2026-09-18, `CONNECTOR.md` §2.5.1)*. The list
    was flat, so leaving out one year of a folder-per-year extract was a click
    per file, and nothing but reading every row said which folders were whole.

    What an indexed file's row says, and why its box is dead, is
    `_chosen_folder`'s to explain.
    """
    _tree_rows(*_file_view())


def _file_view() -> tuple[_TreeView, tuple[picktree.Item, ...]]:
    """The file tree's view and the rows its filter leaves, off the disk as it is now."""
    whole = _file_items()
    view = _TreeView(
        name="files",
        whole=whole,
        ticks=frozenset(picktree.leaves(whole)) - APP.unpicked,
        is_open=lambda item: item.key not in APP.pick_closed,
        toggle=_toggle_pick_folder,
        tick_leaf=_tick,
        tick_under=_tick_folder,
        unit="file",
        fixed_word=INDEXED_NOTE,
        filtering=bool(APP.pick_filter.strip()),
    )
    return view, picktree.shown(whole, APP.pick_filter)


def _toggle_pick_folder(rel: str) -> None:
    APP.toggle_pick_folder(rel)
    _file_tree.refresh()


def _filter_files(text: str) -> None:
    APP.pick_filter = text
    _file_tree.refresh()


def _tick_folder(rel: str, on: bool) -> None:
    """A folder's box: every file under it **that is on screen**, so a filter
    narrows what one press takes and never ticks a row nobody can see."""
    folder = picktree.find(_file_view()[1], rel)
    if folder is not None:
        APP.tick_all(picktree.leaves([folder]), on)
    _sync_tree(*_file_view())
    _actions.refresh()


def _tick_shown(on: bool) -> None:
    APP.tick_all(picktree.leaves(_file_view()[1]), on)
    _sync_tree(*_file_view())
    _actions.refresh()


def _indexed_paths() -> set[str]:
    """Which files already have a catalog entry, by their recorded path."""
    return {str(entry.get("source") or "") for entry in APP.sources.values()}


def _ticked() -> list[Path]:
    """The files this screen would profile — what is under the folder, less what
    was un-ticked and less what is already in the catalog.

    Recomputed from disk rather than remembered, so a file imported into the
    middle of the folder is included without a second click. Already-indexed
    files are excluded *here* as well as being un-tickable in the list, so the
    button's count can never disagree with what the rows offer.
    """
    if not APP.data_dir:
        return []
    files = engine.data_files_in(APP, APP.data_dir)
    skip = APP.unpicked | _indexed_paths()
    return [p for p in files if _rel(p).as_posix() not in skip]


def _tick(rel: str, on: bool) -> None:
    """One file's box. No row is rebuilt: the folders above it are told what
    they now hold (`_sync_tree`), and the button is told its count."""
    APP.tick(rel, on)
    _sync_tree(*_file_view())
    _actions.refresh()


def _browse_to(rel: str) -> None:
    APP.browse_at = rel
    _refresh()


def _choose_folder(rel: str) -> None:
    """Record the data folder — durably, in ``project.yaml``.

    Not a window setting: it decides what the left pane draws as data for every
    session after this one, and where an import lands by default. It is written
    where the brief is written, and read in the same diff. `engine.set_data_dir`
    is what normalises the project root to ``"."`` — see there for why the empty
    string is a different answer rather than the same one.
    """
    engine.set_data_dir(rel, APP)
    APP.repicking = False
    APP.indexed = None
    _seed_ticks()
    _refresh()


def _repick() -> None:
    APP.repicking = True
    APP.browse_at = str(Path(APP.data_dir).parent) if "/" in APP.data_dir else ""
    APP.browse_at = "" if APP.browse_at == "." else APP.browse_at
    _refresh()


def _keep_folder() -> None:
    APP.repicking = False
    _refresh()


def _seed_ticks() -> None:
    """Tick everything under the data folder except what is already profiled.

    Called when the folder is chosen and whenever the dialog is reopened — the
    two moments the list is being looked at fresh. Not on every render: a tick
    the operator cleared has to survive the next redraw, which is the whole
    reason the state is a set of *exclusions* rather than a set of selections.
    """
    indexed = _indexed_paths()
    APP.unpicked = frozenset(
        rel
        for rel in (_rel(p).as_posix() for p in engine.data_files_in(APP, APP.data_dir))
        if rel in indexed
    )


def _use_label() -> str:
    """The button names the folder it would choose, and at the top it says which.

    "Use this folder" at the project root is the one place the phrase is
    ambiguous — it reads as a default rather than as the decision it is, which
    is *the data for this project is everything readable in the repo*.
    """
    at = APP.browse_at
    return f"Use {Path(at).name}" if at else USE_ROOT


def _folder_label(rel: str) -> str:
    """A data folder as a path, or the words for the project root."""
    return f"{rel}/" if rel and rel != "." else PROJECT_ROOT


def _here_note(here: int) -> str:
    if not here:
        return PICKER_HINT
    return PICKER_COUNT.format(files=c.count(here, "file"), where=_folder_label(APP.browse_at))


# --- where the data is: the question before either route ----------------------

CHOOSE_TITLE = "Where is the data?"
CHOOSE_WHY = (
    "A project reads from one place. Pick it, and the next screen is about that place only."
)
CHOOSE_LOCAL = "Files in this repo"
CHOOSE_LOCAL_WHY = "CSV and Parquet, read in place. Profiled for free, nothing copied."
CHOOSE_WAREHOUSE = "A warehouse"
CHOOSE_WAREHOUSE_WHY = "{providers}. Tables read where they are, through a connection of yours. Nothing is copied down."


def _providers_sentence() -> str:
    """The warehouses portia can connect to, off the registry, so the card cannot lag a connector."""
    from portia.connectors import registry

    labels = [p.label for p in registry.PROVIDERS.values()]
    return " or ".join(labels) if len(labels) <= 2 else ", ".join(labels[:-1]) + f" or {labels[-1]}"


def choose_data() -> None:
    """The first question after the brief: files here, or a warehouse.

    One or the other, never both on one screen (`state.LOCAL_DATA`). The screen
    that offered a folder picker beside a connection form read as two half-forms
    and asked the reader to work out which one applied. Choosing sends you to a
    screen about that place only, and the answer can be changed from that
    screen's head until something is indexed (`_data_kind`).
    """
    with ui.element("div").classes("p-centered"):
        with ui.element("div").classes("p-panel p-panel--prose"):
            with ui.element("div").classes("p-panel-head"):
                ui.label(CHOOSE_TITLE).classes("t-heading-md")
                ui.label(CHOOSE_WHY).classes("p-panel-sub")
            with ui.element("div").classes("p-panel-body"):
                _choice_cards()
            with ui.element("div").classes("p-panel-actions"):
                with ui.element("div").classes("row-gap-sm"):
                    c.button("Back", _back_to_brief, kind="secondary")


def _choose(mode: str) -> None:
    from portia.ui import app as app_module

    engine.choose_data(mode, APP)
    if APP.on_add_data:
        app_module.shell.refresh()
    else:
        # From the workspace the question was asked inside the add-data dialog,
        # and redrawing the shell would rebuild the window behind it.
        _refresh()
    if mode == state.WAREHOUSE_DATA and not APP.connection:
        open_connect_dialog()


# --- the warehouse route (`docs/CONNECTOR.md`) ---------------------------------

WAREHOUSE_HEADING = "Tables in scope"
WAREHOUSE_WHY = (
    "The tables this project is about. Ticking adds a table; the switches on the right say "
    "whether it is also profiled and read."
)
CONNECT_TITLE = "Connect to a warehouse"
CONNECT_SUB = "Saved outside the repo with no secret in it. The project only names it."
PICK_SUB = "Pick a saved connection, or set up a new one."
PROVIDER_SUB = "Which warehouse? Each signs in its own way."
#: The one field every provider shares; the rest are the provider's
#: (`registry.Provider.fields`), and the sign-in methods and what each needs
#: typed are the provider's too (`CONNECTORS.md` §2.13).
NAME_FIELD = ("name", "Connection name", True, "work")
PROVIDER_LABEL = "Warehouse"
CHANGE_PROVIDER = "Change"
#: The dialog's project half, under the picked connection or the form
#: (`CONNECTOR.md` §2.7.1): whether the copilot may create tables as it
#: records. The project's, not the connection's. Where a table goes is not
#: asked here at all: it is each spec's own target, the copilot's call.
PROJECT_HEAD = "This project"
AGENT_WRITES_LABEL = "The copilot creates tables as it records steps"
AGENT_WRITES_WHY = (
    "Each recorded step becomes a table in the warehouse, in the schema the copilot chose for "
    "that spec, created if missing. The role is the limit of what it can do there. Off, only "
    "Run and Build write."
)
NEW_CONNECTION = "New connection"
SAVED_CONNECTIONS = "Saved connections"
CONNECT_GO = "Connect"
CONNECTING_GO = "Connecting…"
CONNECT_CANCEL = "Cancel"
CONNECTED_AS = "Connected through {name}."
CONNECTING_NOTE = "Connecting through {name}… a browser window may open."
NOT_CONNECTED_TO = "Not connected to {name} yet."
NO_CONNECTION = "This project names no connection yet."
CONNECT_TO = "Connect to {name}"
ADD_CONNECTION = "Add connection"
USE_EXISTING = "Use existing connection"
SCOPE_LOADING = "Listing…"
SCOPE_EMPTY = "Nothing here the role can see."
NOTHING_UNDER = "Nothing readable here."
FILTER_PLACEHOLDER = "Filter by name"
TALLY_OF = "{n} of {of}"
TALLY_SOME = "{n} selected"
IN_SCOPE_NOTE = "in scope"
BUILT_NOTE = "built by this project as {model}"
NO_CONNECT_DIALOG = "The connect dialog did not load. Reload the page."


def _warehouse_route() -> None:
    """The warehouse's half of the add-data screen: the connection's state, then the picker.

    The connection itself is made in its own dialog (`open_connect_dialog`), so
    this card is about the tables — the same question the folder picker asks of
    a repo, one level up (`CONNECTOR.md` §2.5). Ticked tables join the one
    Index button below, because scoping is indexing at the metadata tier.
    """
    with ui.element("div").classes("add-section"):
        _section_head(WAREHOUSE_HEADING, WAREHOUSE_WHY)
        _connection_state()
        if APP.connected:
            _scope_picker()


def _connection_state() -> None:
    """One line saying where the session stands, and the way to act on it."""
    from portia.ui import artifacts

    status = APP.connection_status
    if status == state.CONNECTED:
        with ui.element("div").classes("connect-state"):
            c.status_light(c.ON)
            ui.label(CONNECTED_AS.format(name=APP.connection))
            ui.element("div").classes("flex-1")
            c.button(
                USE_EXISTING, lambda: open_connect_dialog(new=False), kind="secondary", micro=True
            )
        return
    if status == state.CONNECTING:
        with ui.element("div").classes("connect-state"):
            ui.spinner(size="sm")
            ui.label(CONNECTING_NOTE.format(name=APP.connection))
        return
    if status.startswith("failed"):
        c.alert(status.removeprefix("failed: "), kind="error")
    else:
        with ui.element("div").classes("connect-state"):
            c.status_light(c.OFF)
            ui.label(
                NOT_CONNECTED_TO.format(name=APP.connection) if APP.connection else NO_CONNECTION
            )
    # **Every button says what it opens** *(2026-09-18, the user: "what am I
    # connecting to, nothing is shown?")*. It was *Connect* and *Use another
    # connection* whether or not the project named a connection. With none
    # named, Connect had nothing to connect to and *another* had nothing to be
    # other than. Now *Connect to <name>* exists only when there is a name, and
    # the other two open the dialog on the view their words promise.
    with ui.element("div").classes("row-gap-sm"):
        if APP.connection:
            c.button(
                CONNECT_TO.format(name=APP.connection),
                artifacts.connect_now,
                kind="primary",
                icon="cloud",
            )
        c.button(
            ADD_CONNECTION,
            lambda: open_connect_dialog(new=True),
            kind="secondary" if APP.connection else "primary",
            icon="add",
        )
        if engine.connection_suggestions():
            c.button(USE_EXISTING, lambda: open_connect_dialog(new=False), kind="secondary")


def _scope_picker() -> None:
    """Database → schema → table as **one tree**, listed lazily, every box ticking
    what is under it (`picktree.py`, `CONNECTOR.md` §2.5.1).

    It was the folder picker's shape, one level at a time, and that shape was
    wrong for this question. A folder is *chosen*, once; tables are *collected*,
    from several schemas, and a browser that draws one schema cannot show what
    was ticked in the last one. A schema row also had no box, so §2.5's
    *ticking a schema ticks its tables* was written and never built.

    The filter box and the scrolling region are outside the tree's refreshable:
    a tick redraws the rows, and the caret and the scroll offset stay put.
    """
    if "" not in APP.scope_listing:
        with ui.element("div").classes("picker"):
            if APP.scope_loading is not None:
                with ui.element("div").classes("connect-state p-2"):
                    ui.spinner(size="sm")
                    ui.label(SCOPE_LOADING)
            else:
                with ui.element("div").classes("p-2"):
                    c.button("List", _list_databases, micro=True, icon="refresh")
        return
    with ui.element("div").classes("pick-head"):
        _tree_filter(APP.scope_filter, _filter_scope)
        _scope_clear()
    with c.scroll_area("pick-tables", classes="pick-tree"):
        _scope_tree()


@ui.refreshable
def _scope_clear() -> None:
    if APP.scope_ticks:
        c.button("None", _clear_scope_ticks, micro=True)


def _scope_items() -> tuple[picktree.Item, ...]:
    """The warehouse as far as it has been listed, with what is already in marked.

    A table **this project built** is known already, under its model's name, and
    profiled from there (`catalog.profile_remote`); ticking it here would write
    a second entry for the same table (2026-09-07). So its row says what it is
    and cannot be ticked, as an in-scope one cannot.
    """
    notes = {qualified: IN_SCOPE_NOTE for qualified in APP.scope}
    notes |= {
        qualified: BUILT_NOTE.format(model=model)
        for qualified, model in (engine.written_tables(APP) or {}).items()
    }
    return picktree.from_listing(APP.scope_listing, notes)


@ui.refreshable
def _scope_tree() -> None:
    view, rows = _scope_view()
    if not view.whole:
        c.empty_note(SCOPE_EMPTY)
        return
    _tree_rows(view, rows)


def _scope_view() -> tuple[_TreeView, tuple[picktree.Item, ...]]:
    whole = _scope_items()
    view = _TreeView(
        name="tables",
        whole=whole,
        ticks=APP.scope_ticks,
        is_open=lambda item: item.key in APP.scope_open,
        toggle=_toggle_scope,
        tick_leaf=_tick_scope,
        tick_under=_tick_schema,
        unit="table",
        fixed_word=IN_SCOPE_NOTE,
        filtering=bool(APP.scope_filter.strip()),
        loading=APP.scope_loading,
    )
    return view, picktree.shown(whole, APP.scope_filter)


def _redraw_scope() -> None:
    """After a tick: the rows are told their new state in place, never rebuilt."""
    _sync_tree(*_scope_view())
    _scope_clear.refresh()
    _actions.refresh()


def _tick_scope(qualified: str, on: bool) -> None:
    APP.tick_scope(qualified, on)
    _redraw_scope()


def _clear_scope_ticks() -> None:
    APP.scope_ticks = frozenset()
    _redraw_scope()


def _filter_scope(text: str) -> None:
    APP.scope_filter = text
    _scope_tree.refresh()


async def _list_databases() -> None:
    """The first listing. Also what a finished connect calls, from a dialog that
    has just closed under it, so the client is taken off the page if there is
    one and the listing goes ahead either way."""
    client = _page_client()
    await _listing(client, engine.browse_remote(APP, "", _refresh))
    _refresh()


async def _toggle_scope(key: str) -> None:
    """Unfold or shut a row. The first unfolding is a query, and the row says so
    with a spinner where its caret was."""
    # Held first: the redraw below deletes the caret this handler stands in
    # (`app._say`'s rule), and a failed listing has to reach the screen.
    client = _page_client()
    if APP.toggle_scope_node(key) and key not in APP.scope_listing:
        await _listing(client, engine.browse_remote(APP, key, _scope_tree.refresh))
    _scope_tree.refresh()


async def _tick_schema(key: str, on: bool) -> None:
    """A schema's box, or a database's: list what is under it, then tick it.

    **A snapshot, never a rule** (`CONNECTOR.md` §2.5.1): the press ticks the
    tables that are there now, and `scope` stays a list of names. A table
    somebody creates in that schema next month joins nothing until a person
    ticks it, because with the profile switch on it would otherwise be scanned
    on this project's meter without anyone having chosen it.

    With a filter on, the press takes the rows on screen and no others.
    """
    client = _page_client()
    if on:
        # **No row is rebuilt for this listing.** Only a listed container can be
        # open, so what arrives is under a shut row and changes no row on
        # screen: the count beside the box says *Listing…* and then the number.
        await _listing(client, engine.list_under(APP, key, lambda: _say_listing(key)))
    node = picktree.find(_scope_view()[1], key)
    if node is not None:
        APP.tick_scope_all(picktree.leaves([node]), on)
    _redraw_scope()


def _say_listing(key: str) -> None:
    for _box, meta in _DRAWN.get("tables", {}).get(key, []):
        if meta is not None and not meta.is_deleted:
            meta.text = SCOPE_LOADING


def _page_client():
    """The page a handler was pressed on, or ``None`` where there is no page: a
    finished connect calls the first listing from a dialog that has just closed."""
    try:
        return context.client
    except RuntimeError:
        return None


async def _listing(client, listing) -> None:
    """Await one listing; a failure is a sentence on screen and the tree as it was."""
    try:
        await listing
    except Exception as exc:  # noqa: BLE001 — a listing that failed is a sentence on screen
        if client is not None:
            with client:
                ui.notify(f"{type(exc).__name__}: {exc}")


async def _scope_and_interpret(names: list[str], *, in_dialog: bool = False) -> None:
    """Scope the ticked tables, profile them if the switch says so, then read them.

    **The panel stays up until the free and the paid-by-scan halves are done**
    *(2026-09-07)*. Scoping is metadata and takes under a second a table, so
    the dialog used to close before the eye caught the status line, and the
    only trace of the press was a toast and rows in the left pane. Profiling
    is minutes, and the status line and its Stop are on this panel. The read
    is a model turn and runs in the chat list; the toast says so, because the
    add-data dialog is gone by then and nothing else on screen does.
    """
    from portia.ui import app as app_module
    from portia.ui import artifacts

    if not names:
        return

    def say(verb: str):
        def _say(done: int, total: int, name: str) -> None:
            APP.indexing_status = f"{verb} {name}, {done + 1} of {total}"
            _progress.refresh()

        return _say

    APP.indexing_status = f"Scoping {c.count(len(names), 'table')}…"
    stop = APP.indexing_stop = cancel.Scope()
    _progress.refresh()
    profiled: list[str] = []
    try:
        scoped = await engine.scope(APP, names, on_progress=say("Scoping"), stop=stop)
        if APP.profile_on_add and scoped and not stop.cancelled:
            profiled = await engine.profile_tables(
                APP, scoped, on_progress=say("Profiling"), stop=stop
            )
    finally:
        APP.indexing_status = ""
        APP.indexing_stop = None
        _pressed_done()
        stop.close()
        _progress.refresh()
    ui.notify(_scoped_note(len(scoped), len(profiled), reading=_will_read(stop)))
    APP.scope_ticks = APP.scope_ticks - set(names[: len(scoped)])
    APP.pending_interpret = [*APP.pending_interpret, *scoped]
    APP.indexed = (APP.indexed or 0) + len(scoped)
    if in_dialog:
        _close_dialog()
        app_module.shell.refresh()
        artifacts.pane.refresh()
    else:
        _refresh()
        _catch_up_workspace()
    if not stop.cancelled:
        await _interpret_pending()


def _will_read(stop: cancel.Scope) -> bool:
    """Whether the read is about to start, so the toast can say where it went."""
    return APP.interpret and not stop.cancelled and not APP.busy


def _scoped_note(scoped: int, profiled: int, *, reading: bool) -> str:
    """What the press did, in one sentence, and what happens next.

    Counts of what was *done*, never of what was ticked: a stopped run may
    have scoped six and profiled two, and the sentence says so.
    """
    tables = c.count(scoped, "table")
    if profiled:
        done = SCOPED_PROFILED.format(n=tables, profiled=profiled)
    else:
        done = SCOPED_METADATA.format(n=tables)
    if reading:
        after = READING_NEXT
    elif profiled == scoped:
        after = READ_TAB_NEXT
    else:
        after = INDEX_TAB_NEXT
    return f"{done} {after}"


# --- the connect dialog: its own floating window --------------------------------

#: Built once at page level, for the reason `build_add_dialog` documents.
_CONNECT_DIALOG: ui.dialog | None = None


def build_connect_dialog() -> None:
    """Create the connect dialog. **Called once per page, never from a pane.**"""
    global _CONNECT_DIALOG
    with ui.dialog().props("transition-duration=0 persistent") as dialog:
        _connect_panel()
    _CONNECT_DIALOG = dialog


def open_connect_dialog(note: str = "", *, new: bool | None = None) -> None:
    """Show it, on the saved list when there is one, with the project's connection picked.

    ``new`` is for a button that names its view: ``True`` opens on the list of
    providers, a new connection, and ``False`` on the saved ones. Unset, the
    dialog picks (`_initial_view`). With nothing saved it is the form whatever
    was asked, because an empty list is a view of nothing.

    ``note`` is the sentence an action opens it with, drawn *in the window it
    sends you to*: a toast that vanished while the dialog drew once left a
    password box as the only explanation of why Run did nothing (2026-09-06,
    the first remote session).
    """
    if _CONNECT_DIALOG is None or _CONNECT_DIALOG.is_deleted:
        ui.notify(NO_CONNECT_DIALOG)
        return
    names = [s["name"] for s in engine.connection_suggestions()]
    APP.connect_new, APP.connect_pick = _initial_view(names, APP.connection)
    if new and not APP.connect_new:
        APP.connect_new, APP.connect_pick = True, ""
    APP.connect_form = _blank_form() if APP.connect_new else {}
    APP.connect_agent_writes = APP.agent_writes
    APP.connect_note = note
    APP.connect_error = ""
    _connect_panel.refresh()
    _CONNECT_DIALOG.open()


def _initial_view(saved: list[str], current: str | None) -> tuple[bool, str]:
    """Which view the dialog opens on: ``(new, picked)``.

    The form only when nothing is saved; otherwise the list, with the project's
    own connection picked when it is in it, so *Use another connection* and a
    password prompt both open on the one already named.
    """
    if not saved:
        return True, ""
    return False, current if current in saved else ""


def _blank_form() -> dict[str, str]:
    """A new connection starts with no provider picked: the list is the first thing drawn."""
    return {}


def _provider_of(form: dict[str, str]):
    """The provider the form is for, or ``None`` while the list is still up."""
    from portia.connectors import registry

    return registry.PROVIDERS.get(form.get("kind") or "")


def _close_connect_dialog() -> None:
    if _CONNECT_DIALOG is not None and not _CONNECT_DIALOG.is_deleted:
        _CONNECT_DIALOG.close()


@ui.refreshable
def _connect_panel() -> None:
    """Two views in one window: the saved connections to pick from, or the form for a new one.

    Its own window rather than a card on the add-data screen (the user's call,
    2026-09-04): connecting is one act with one outcome, and the screen after it
    is about tables. A saved connection is a row, not a filled-in form (the
    user's call, 2026-09-06): picking one and pressing Connect opens the session
    the way it signs in, and the only thing the list ever asks for is the
    password or token that method needs. The form is for a new connection and
    is the first view only when nothing is saved. Each field says *required* or
    *optional* beside its label and nothing more; a failure is an `alert` above
    the button that failed.
    """
    busy = APP.connection_status == state.CONNECTING
    saved = engine.connection_suggestions()
    picked = next((s for s in saved if s["name"] == APP.connect_pick), None)
    with ui.element("div").classes("p-panel p-panel--prose"):
        with ui.element("div").classes("p-panel-head"):
            ui.label(CONNECT_TITLE).classes("t-heading-md")
            if not APP.connect_new:
                sub = PICK_SUB
            elif _provider_of(APP.connect_form) is None:
                sub = PROVIDER_SUB
            else:
                sub = CONNECT_SUB
            ui.label(sub).classes("p-panel-sub")
        with ui.element("div").classes("p-panel-body"):
            if APP.connect_note:
                c.alert(APP.connect_note, kind="info")
            if APP.connect_new:
                _connect_form()
            else:
                _connect_pick(saved, picked)
            if (APP.connect_new and _provider_of(APP.connect_form)) or picked is not None:
                _project_fields()
            if APP.connect_error:
                c.alert(APP.connect_error, kind="error")
            elif busy:
                with ui.element("div").classes("connect-state"):
                    ui.spinner(size="sm")
                    ui.label(CONNECTING_NOTE.format(name=_connect_target().get("name") or ""))
        with ui.element("div").classes("p-panel-actions"):
            with ui.element("div").classes("row-gap-sm"):
                c.button(
                    CONNECTING_GO if busy else CONNECT_GO,
                    _connect_now,
                    kind="primary",
                    icon="cloud",
                    enabled=not busy
                    and (
                        (APP.connect_new and _provider_of(APP.connect_form) is not None)
                        or picked is not None
                    ),
                )
                if APP.connect_new and saved:
                    c.button(SAVED_CONNECTIONS, _back_to_pick, kind="secondary", enabled=not busy)
                c.button(CONNECT_CANCEL, _cancel_connect, kind="secondary", enabled=not busy)


def _connect_pick(saved: list[dict[str, str]], picked: dict[str, str] | None) -> None:
    """The saved connections as rows, the last row starting a new one.

    A row carries the name, who into what account, and how it signs in. The
    picked row takes the accent wash (`artifact-row--selected`'s idiom). When
    the picked one signs in with a password or a token, that one box is drawn
    under the list; nothing else about a saved connection is asked again.
    """
    from portia.connectors import registry

    with ui.element("div").classes("connect-pick"):
        for s in saved:
            provider = registry.PROVIDERS.get(s.get("kind") or "")
            selected = picked is not None and s["name"] == picked["name"]
            row = ui.element("div").classes(
                "connect-pick-row" + (" connect-pick-row--selected" if selected else "")
            )
            with row:
                ui.icon(provider.icon if provider else "cloud").classes("connect-pick-icon")
                ui.label(s["name"]).classes("connect-pick-name")
                ui.label(s.get("summary", "")).classes("connect-pick-meta")
                ui.label(_auth_label(s)).classes("connect-pick-auth")
            row.on("click", lambda _e, n=s["name"]: _pick(n))
        with ui.element("div").classes("connect-pick-row connect-pick-row--new") as row:
            ui.icon("add").classes("connect-pick-icon")
            ui.label(NEW_CONNECTION).classes("connect-pick-name")
        row.on("click", lambda _e: _start_new())
    secret = _secret_label(picked) if picked is not None else None
    if secret:
        _secret_field(secret)


def _auth_label(form: dict[str, str]) -> str:
    provider = _provider_of(form)
    if provider is None:
        return ""
    try:
        return provider.auth_of(form.get("auth") or provider.default_auth).label
    except ValueError:
        return form.get("auth") or ""


def _secret_label(form: dict[str, str]) -> str | None:
    """What the picked sign-in method needs typed this session, or nothing."""
    provider = _provider_of(form)
    if provider is None:
        return None
    try:
        return provider.auth_of(form.get("auth") or provider.default_auth).secret
    except ValueError:
        return None


def _connect_form() -> None:
    """The form for a new connection: **which warehouse first**, then that
    provider's sign-in methods and fields.

    The list of providers is the registry's, drawn as rows (`_provider_list`),
    because each signs in its own way and the fields under the name are not
    the same list on two of them (`CONNECTORS.md` §2.13). Once one is picked
    it is named at the top of the form with a way back to the list.
    """
    form = APP.connect_form
    provider = _provider_of(form)
    if provider is None:
        _provider_list()
        return
    auth = form.get("auth") or provider.default_auth
    with ui.element("div").classes("connect-state"):
        ui.icon(provider.icon)
        ui.label(f"{PROVIDER_LABEL}: {provider.label}")
        ui.element("div").classes("flex-1")
        c.button(CHANGE_PROVIDER, _start_new, kind="secondary", micro=True)
    with ui.element("div").classes("connect-auth"):
        ui.label("Sign in with").classes("field-label")
        c.segmented([a.label for a in provider.auth], provider.auth_of(auth).label, _set_auth)
    with ui.element("div").classes("field-grid"):
        key, label, required, placeholder = NAME_FIELD
        c.field(
            label,
            required=required,
            value=form.get(key, ""),
            placeholder=placeholder,
            on_change=lambda e, k=key: form.__setitem__(k, e.value or ""),
        )
        # A field a sign-in method needs is required while that method is
        # picked: the key file's path under a service account, and optional
        # under the other two (`registry.Auth.needs`).
        needed = set(provider.auth_of(auth).needs)
        for f in provider.fields:
            c.field(
                f.label,
                required=f.required or f.key in needed,
                value=form.get(f.key, ""),
                placeholder=f.placeholder,
                mono=f.mono,
                on_change=lambda e, k=f.key: form.__setitem__(k, e.value or ""),
            )
    secret = provider.auth_of(auth).secret
    if secret:
        _secret_field(secret)


def _provider_list() -> None:
    """Every warehouse portia can connect to, one row each, the registry's order."""
    from portia.connectors import registry

    with ui.element("div").classes("connect-pick"):
        for provider in registry.PROVIDERS.values():
            with ui.element("div").classes("connect-pick-row") as row:
                ui.icon(provider.icon).classes("connect-pick-icon")
                ui.label(provider.label).classes("connect-pick-name")
                ui.label(", ".join(a.label for a in provider.auth)).classes("connect-pick-meta")
            row.on("click", lambda _e, k=provider.kind: _pick_provider(k))


def _pick_provider(kind: str) -> None:
    from portia.connectors import registry

    APP.connect_form = {"kind": kind, "auth": registry.PROVIDERS[kind].default_auth}
    APP.connect_error = ""
    _connect_panel.refresh()


def _project_fields() -> None:
    """The project's half of the dialog, drawn under whichever view is up: the
    hand-off switch (`CONNECTOR.md` §2.7.1).

    Under the **list** too. A saved connection is the user's and asks for
    nothing; this is the project's and always shows. A *Build writes to* field
    sat here for two days and is gone: where a table goes is each spec's own
    target, chosen by the copilot, and a project-wide one made no sense.
    """
    with ui.element("div").classes("connect-project"):
        ui.label(PROJECT_HEAD).classes("field-label")
        switch = ui.switch(AGENT_WRITES_LABEL).classes("p-toggle")
        switch.value = APP.connect_agent_writes
        switch.on_value_change(lambda e: setattr(APP, "connect_agent_writes", bool(e.value)))
        c.caption(AGENT_WRITES_WHY)


def _secret_field(label: str) -> None:
    """The one box for this session's password or token. Never written anywhere."""
    c.field(
        label,
        required=True,
        value=APP.connect_secret,
        secret=True,
        on_change=lambda e: setattr(APP, "connect_secret", e.value or ""),
    )


def _connect_target() -> dict[str, str]:
    """The connection Connect would act on: the form's draft, or the picked saved one."""
    if APP.connect_new:
        return APP.connect_form
    return next((s for s in engine.connection_suggestions() if s["name"] == APP.connect_pick), {})


def _pick(name: str) -> None:
    APP.connect_pick = name
    APP.connect_error = ""
    _connect_panel.refresh()


def _start_new() -> None:
    APP.connect_new = True
    APP.connect_form = _blank_form()
    APP.connect_error = ""
    _connect_panel.refresh()


def _back_to_pick() -> None:
    APP.connect_new = False
    APP.connect_error = ""
    _connect_panel.refresh()


def _set_auth(label: str) -> None:
    provider = _provider_of(APP.connect_form)
    if provider is not None:
        APP.connect_form["auth"] = next(a.key for a in provider.auth if a.label == label)
    _connect_panel.refresh()


def _cancel_connect() -> None:
    APP.connect_secret = ""
    APP.connect_error = ""
    APP.connect_note = ""
    _close_connect_dialog()


async def _connect_now() -> None:
    """Save the connection, make the project run on it, and open the session.

    One path for both views: a picked saved connection is re-saved as it is
    (which is also what moves a suggestion from Snowflake's file into portia's
    registry); the hand-off switch is the project's and is saved from either.
    The dialog stays up until it is known how that went: a failure is drawn in
    it, above the button, with the driver's sentence and the one case we know
    named in plain words (`engine._plain`).
    """
    from portia.ui import app as app_module
    from portia.ui import artifacts

    form = _connect_target()
    if not form:
        return
    try:
        engine.save_connection(form, agent_writes=APP.connect_agent_writes, app=APP)
    except ValueError as exc:
        APP.connect_error = str(exc)
        _connect_panel.refresh()
        return
    wanted = _secret_label(form)
    secret = APP.connect_secret.strip() or None
    if wanted and not secret:
        APP.connect_error = f"{wanted} is required for this sign-in."
        _connect_panel.refresh()
        return
    APP.connect_error = ""
    _connect_panel.refresh()
    ok = await engine.connect_project(APP, secret=secret)
    if not ok:
        APP.connect_error = APP.connection_status.removeprefix("failed: ")
        _connect_panel.refresh()
        return
    APP.connect_form = {}
    APP.connect_note = ""
    _close_connect_dialog()
    APP.warehouse_open = True
    app_module.shell.refresh()
    artifacts.pane.refresh()
    _refresh()
    await _list_databases()


# --- the start panel: a local server portia can start (`PROVIDERS.md` §4.9) -----

#: Built once at page level, for the reason `build_add_dialog` documents.
_SERVER_DIALOG: ui.dialog | None = None

SERVER_TITLE = "Start llama-server"
SERVER_MODEL = "Model"
SERVER_ELSEWHERE = "Elsewhere…"
SERVER_ELSEWHERE_FIELD = "Path or repository"
SERVER_MODEL_HINT = (
    "A .gguf file anywhere on this machine, or a Hugging Face repository such as "
    "Qwen/Qwen3-8B-GGUF:Q4_K_M, which llama-server fetches once into its own cache."
)
SERVER_REGISTRY_HINT = (
    "Model files portia knows, from {folder}. Put a .gguf there and it appears here."
)
SERVER_REGISTRY_EMPTY = (
    "{folder} is empty. Put a .gguf there and it appears here, or pick Elsewhere."
)
SERVER_CONTEXT = "Context, tokens per slot"
SERVER_CONTEXT_HINT = (
    "portia's instructions alone are about 14,700 tokens; 32768 leaves room for a chat."
)
SERVER_PORT = "Port"
SERVER_COMMAND = "What will run:"
SERVER_GO = "Start"
SERVER_STARTING_GO = "Starting…"
SERVER_STARTING = (
    "Starting llama-server and loading the model. A first download takes as long as it takes."
)
SERVER_RUNNING = "Running from this window, pid {pid}. Closing the window stops it."
SERVER_STOP = "Stop"
SERVER_CLOSE = "Close"
NO_SERVER_DIALOG = "The start panel did not load. Reload the page."


def build_server_dialog() -> None:
    """Create the start panel. **Called once per page, never from a pane.**"""
    global _SERVER_DIALOG
    with ui.dialog().props("transition-duration=0 persistent") as dialog:
        _server_panel()
    _SERVER_DIALOG = dialog


def open_server_dialog(kind: str = "") -> None:
    """Show it, on the saved configuration, from wherever the picker offered it."""
    from portia.agent.providers import llamacpp

    if _SERVER_DIALOG is None or _SERVER_DIALOG.is_deleted:
        ui.notify(NO_SERVER_DIALOG)
        return
    if not APP.server_form:
        APP.server_form = llamacpp.load_config().as_form()
    APP.server_error = ""
    _server_panel.refresh()
    _SERVER_DIALOG.open()


def _close_server_dialog() -> None:
    if _SERVER_DIALOG is not None and not _SERVER_DIALOG.is_deleted:
        _SERVER_DIALOG.close()


@ui.refreshable
def _server_panel() -> None:
    """The three fields, the command they make, and Start or Stop.

    The command is drawn as it will run (`ServerConfig.command`), so what the
    window does is what a terminal would do with the same line, and a refusal
    is the provider's own sentence with the log's last lines under the button
    that failed. While the model loads the panel says so and Start is dark;
    once the server answers, the panel closes and the picker lists it.
    """
    from portia.agent.providers import llamacpp

    busy = APP.server_status == state.STARTING
    pid = llamacpp.running()
    form = APP.server_form
    with ui.element("div").classes("p-panel p-panel--prose"):
        with ui.element("div").classes("p-panel-head"):
            ui.label(SERVER_TITLE).classes("t-heading-md")
        with ui.element("div").classes("p-panel-body"):
            _server_model_pick(form)
            c.field(
                SERVER_CONTEXT,
                hint=SERVER_CONTEXT_HINT,
                value=form.get("context", ""),
                mono=True,
                on_change=lambda e: _server_field("context", e.value),
            )
            c.field(
                SERVER_PORT,
                value=form.get("port", ""),
                mono=True,
                on_change=lambda e: _server_field("port", e.value),
            )
            global _COMMAND_LINE
            c.caption(SERVER_COMMAND)
            _COMMAND_LINE = c.mono(_server_command(form), small=True)
            if APP.server_error:
                c.alert(APP.server_error, kind="error")
            elif busy:
                with ui.element("div").classes("connect-state"):
                    ui.spinner(size="sm")
                    ui.label(SERVER_STARTING)
            elif pid is not None:
                c.alert(SERVER_RUNNING.format(pid=pid), kind="info")
        with ui.element("div").classes("p-panel-actions"):
            with ui.element("div").classes("row-gap-sm"):
                if pid is None:
                    c.button(
                        SERVER_STARTING_GO if busy else SERVER_GO,
                        _start_server_clicked,
                        kind="primary",
                        icon="play_arrow",
                        enabled=not busy,
                    )
                else:
                    c.button(SERVER_STOP, _stop_server_clicked, kind="secondary", icon="stop")
                c.button(SERVER_CLOSE, _close_server_dialog, kind="secondary", enabled=not busy)


#: The select's value for a model that is not in the registry: the path field
#: is drawn under it and holds the real value.
ELSEWHERE = "__elsewhere__"


def _server_model_pick(form: dict[str, str]) -> None:
    """The model as a select over the registry, with *elsewhere* revealing a path field.

    The registry (`llamacpp.REGISTRY_DIR`) is the one folder portia looks in, so
    the common case is a name off a list and never a path typed by hand. A
    model kept elsewhere is still allowed: the last option reveals the field
    the panel used to be, for a path or a Hugging Face repository.
    """
    from portia.agent.providers import llamacpp

    registry = llamacpp.registry_models()
    options = {str(p): llamacpp.display_name(str(p)) for p in registry}
    options[ELSEWHERE] = SERVER_ELSEWHERE
    current = form.get("model", "")
    picked = current if current in options else ELSEWHERE
    with ui.element("div").classes("field"):
        with ui.element("div").classes("field-label"):
            ui.label(SERVER_MODEL)
            ui.label("required").classes("field-required")
        select = ui.select(options, value=picked, on_change=lambda e: _server_model_picked(e.value))
        select.props("dense borderless options-dense hide-bottom-space").classes(
            "p-field p-field-mono w-full"
        )
        ui.label(
            SERVER_REGISTRY_HINT.format(folder=llamacpp.REGISTRY_DIR)
            if registry
            else SERVER_REGISTRY_EMPTY.format(folder=llamacpp.REGISTRY_DIR)
        ).classes("field-hint")
    if picked == ELSEWHERE:
        c.field(
            SERVER_ELSEWHERE_FIELD,
            required=True,
            hint=SERVER_MODEL_HINT,
            value=current,
            mono=True,
            on_change=lambda e: _server_field("model", e.value),
        )


def _server_model_picked(value: str) -> None:
    """A registry pick sets the path; *elsewhere* clears it and reveals the field.

    The panel is redrawn here, unlike a keystroke in the path field: what is
    drawn under the select changes with the choice, and the select is not the
    element being typed in.
    """
    APP.server_form = {**APP.server_form, "model": "" if value == ELSEWHERE else value}
    APP.server_error = ""
    _server_panel.refresh()


#: The command line under the fields, updated in place as they are typed. A
#: refresh per keystroke would rebuild the box being typed in and take the
#: caret with it (`CLAUDE.md` → a streamed event redraws the tail), so the
#: one label that follows the fields is kept and its text is set.
_COMMAND_LINE: ui.label | None = None


def _server_field(key: str, value: object) -> None:
    APP.server_form = {**APP.server_form, key: str(value or "")}
    if _COMMAND_LINE is not None and not _COMMAND_LINE.is_deleted:
        _COMMAND_LINE.text = _server_command(APP.server_form)


def _server_command(form: dict[str, str]) -> str:
    """The argv as one line, or the refusal a start would give, so nothing is typed blind."""
    from portia.agent.providers import llamacpp

    try:
        return " ".join(llamacpp.ServerConfig.from_form(form).command())
    except ValueError as exc:
        return str(exc)


def _start_server_clicked() -> None:
    from nicegui import background_tasks

    background_tasks.create(_start_server())


async def _start_server() -> None:
    """Start it, wait on it, and redraw the panel and every picker.

    *Starting* is set **before** the first redraw, so the spinner and the
    sentence are on screen for the whole load: the first build set it inside
    the engine, after the redraw, and the panel gave no sign that anything
    was happening for the ten seconds the model took (the user, 2026-09-14).
    The panel stays open when the server is up, on the *running* line with
    Stop beside it, rather than closing on a success nobody saw.
    """
    from portia.agent.providers import llamacpp

    APP.server_status = state.STARTING
    APP.server_error = ""
    _server_panel.refresh()
    _redraw_pickers()
    ok = await engine.start_server(APP)
    if ok:
        APP.provider = llamacpp.PROVIDER.kind
    _server_panel.refresh()
    _redraw_pickers()


def _stop_server_clicked() -> None:
    from nicegui import background_tasks

    background_tasks.create(_stop_server())


async def _stop_server() -> None:
    await engine.stop_server(APP)
    _server_panel.refresh()
    _redraw_pickers()


def _redraw_pickers() -> None:
    """Every place the picker is drawn: the composer, the add-data screen, Settings."""
    from portia.ui import settings, transcript

    transcript.pane.refresh()
    settings.refresh_if_open()
    panel.refresh()


# --- route two: data that is not in the repo yet ----------------------------


def _from_outside() -> None:
    """Folded away until it is wanted, because it is the second route in.

    A project whose data is already in the repo should not have to read past an
    importer to get to the button. It is a disclosure rather than a separate
    screen because both routes feed one index, and having imported a file you
    still want to see it land in the list above.

    **The header is the same size and weight as the section above it**, so folded
    it reads as the second of two sections rather than as a stray line of text.
    It carries `add-section-toggle`, which is what moves the caret next to the
    title: `c.collapsed`'s default puts it at the far right, which is right for a
    tool result in a 400px transcript and, across a 560px form, left the caret
    and the word it belongs to half a screen apart.
    """
    with ui.element("div").classes("add-section add-section--folding"):
        exp = c.collapsed(IMPORT_HEADING, _import_body).classes("add-section-toggle")
        exp.value = APP.import_open
        exp.on_value_change(lambda e: setattr(APP, "import_open", bool(e.value)))


def _import_body() -> None:
    with ui.element("div").classes("import-body"):
        ui.label(IMPORT_WHY).classes("add-section-hint")
        _import_field()
        _import_destination()
        _import_plan()


def _import_field() -> None:
    """One button: choose the files, and the plan appears. Nothing is copied here.

    `PIPELINE.md` §2.7 makes bringing outside data in a deliberate step — you
    choose where it lands, portia states exactly what it is about to copy and to
    where, and only then does it copy. This is the choosing; `_import_plan` is
    the stating and the Index button is the doing.

    **The typed path field and its *Plan import* button are gone** (2026-08-03).
    They were two controls for one act: the chooser already planned on return, so
    the button existed only to commit whatever was in the box, and the box only
    existed to feed the button. What it cost was a section with three controls in
    a row where the honest shape is one — and a field whose placeholder had to
    explain a glob syntax to justify itself.

    The field was also the way in on a machine with no native chooser, so that
    case now says so rather than showing a section that cannot do anything.
    """
    if not engine.can_browse():
        c.empty_note(NO_CHOOSER)
        return
    with ui.element("div").classes("row-gap-sm w-full"):
        c.button("Choose files…", _choose_to_import, icon="folder_open")


def _import_destination() -> None:
    """Where it lands: with the rest of the data, or somewhere you name.

    Defaulting to the folder chosen above is the whole point — an import that
    lands beside the data is one folder layout, and one that lands in a second
    place is two. With no folder chosen the default is to create ``data/``,
    because a file arriving at the project root is not a decision anyone made.
    """
    with ui.element("div").classes("stack-xs w-full"):
        (
            ui.checkbox(DESTINATION_DEFAULT.format(where=_default_destination()))
            .classes("p-check")
            .props("dense")
            .bind_value(APP, "import_to_data_dir")
            .on_value_change(_refresh)
        )
        if not APP.import_to_data_dir:
            (
                ui.input(placeholder=DESTINATION_PLACEHOLDER)
                .classes("p-field p-field-mono w-full")
                .props("borderless")
                .bind_value(APP, "import_destination")
            )
            c.caption(DESTINATION_ROOT)
        c.caption(DESTINATION_SCOPE)


def _default_destination() -> str:
    """What the checkbox is offering, spelled the way it will appear on disk."""
    return _folder_label(APP.data_dir) if APP.data_dir and APP.data_dir != "." else f"{DATA_DIR}/"


@ui.refreshable
def _import_plan() -> None:
    """What is about to be copied, and where to. Every pair, never a summary.

    "3 files into data/" is a description of a plan; this is the plan, and the
    difference is the one time a name collision or a wrong folder is cheap to
    notice. The plan is not acted on here — the Index button below copies it and
    profiles it in one step, so there is exactly one button on this screen that
    writes anything.
    """
    if APP.import_error:
        with ui.element("div").classes("write-confirm"):
            c.text(APP.import_error, color="c-error")
            c.button("OK", _clear_import, kind="secondary", micro=True)
        return
    if not APP.import_plan:
        return
    with ui.element("div").classes("write-confirm"):
        ui.label(PLAN_HEADING.format(n=c.count(len(APP.import_plan), "file"))).classes(
            "t-body-strong c-ink"
        )
        c.caption(IMPORT_COPY_ONLY)
        with ui.element("div").classes("added-list"):
            for src, dst in APP.import_plan:
                with ui.element("div").classes("added-row"):
                    c.mono(str(src), small=True)
                    ui.icon("arrow_forward").classes("fact-icon")
                    c.mono(str(_rel(dst)), small=True)
        c.button("Cancel this import", _clear_import, kind="secondary", micro=True)


def _rel(path: Path) -> Path:
    """A path as the project sees it — the repo-relative one it will have."""
    try:
        return path.relative_to(APP.root)
    except ValueError:
        return path


async def _choose_to_import() -> None:
    """The chooser, and then the plan. A cancelled dialog is an answer of "no".

    `browse_for_files` comes back empty on cancel as well as on no-chooser, and
    neither is a thing to report — planning nothing and saying so would turn
    closing a dialog into an error message.
    """
    chosen = await engine.browse_for_files()
    if chosen:
        _plan_import(chosen)


def _plan_import(paths: list[Path]) -> None:
    """Work out the plan and show it. Writes nothing."""
    APP.import_plan, APP.import_error = [], ""
    try:
        pairs = []
        for path in paths:
            pairs += engine.plan_import(str(path), APP.import_dir(DATA_DIR), APP)
    except ValueError as exc:
        APP.import_error = str(exc)
    else:
        APP.import_plan = pairs
    APP.indexed = None
    _import_plan.refresh()
    _actions.refresh()


def _clear_import() -> None:
    APP.import_plan, APP.import_error = [], ""
    _import_plan.refresh()
    _actions.refresh()


# --- what it costs ----------------------------------------------------------


def _profile_toggle() -> None:
    """Whether a scoped table is scanned as it arrives. Warehouse route only.

    Locally there is no switch because a profile of a file is free and always
    happens. On a warehouse it is a scan on the meter (`CONNECTOR.md` §2.6),
    and until 2026-09-07 the only way to ask for one was the inspector's
    *Profile now*, one table at a time, after the fact. A user who ticked six
    tables and pressed Index watched them arrive as metadata and read that as
    indexing having failed. The choice is made here now, before the press,
    and the caption under the Index button repeats it.
    """
    with ui.element("div").classes("add-section add-section--cost"):
        (
            ui.switch(PROFILE_SWITCH)
            .classes("p-toggle")
            .bind_value(APP, "profile_on_add")
            .on_value_change(_refresh)
        )
        ui.label(PROFILE_ON_COST if APP.profile_on_add else PROFILE_OFF_COST).classes(
            "add-section-hint"
        )


def _interpret_toggle() -> None:
    """Profiling is free; interpretation is a model turn. Never blur the two.

    The model and effort belong here rather than only in the Copilot pane: this
    is where that turn is actually bought, and reading twenty sources is a
    different-sized job from answering one question. Same two controls, same
    bound state — picking here is picking for the copilot too, which is why they
    are not a second setting.
    """
    with ui.element("div").classes("add-section add-section--cost"):
        (
            ui.switch("Have the copilot read what each source is")
            .classes("p-toggle")
            .bind_value(APP, "interpret")
            # The model controls appear with the cost they belong to, so turning
            # this off has to take them away rather than leave a dead setting.
            .on_value_change(_interpret_switched)
        )
        if APP.interpret:
            with ui.element("div").classes("cost-controls"):
                c.model_effort(
                    APP,
                    _set_indexing_effort,
                    on_provider=_set_indexing_provider,
                    on_refresh=_list_models_clicked,
                    on_start=open_server_dialog,
                )
        remote = APP.data_mode == state.WAREHOUSE_DATA
        ui.label(INTERPRET_COST_REMOTE if remote else INTERPRET_COST).classes("add-section-hint")


async def _interpret_switched() -> None:
    """Redraw for the controls, and read anything profiled while it was off.

    One rule, applied at the only two moments it can fire: **whatever is
    profiled and unread gets read while this is on.** Indexing with the switch
    off and turning it on afterwards is the same request as indexing with it on,
    and the alternative is a screen where the switch does nothing until you
    index something else.
    """
    _refresh()
    await _interpret_pending()


def _set_indexing_effort(effort: str) -> None:
    APP.effort = effort
    _refresh()


def _set_indexing_provider(kind: str) -> None:
    from nicegui import background_tasks

    from portia.agent import providers

    APP.provider = kind
    APP.model = providers.get(kind).default_model
    _refresh()
    background_tasks.create(_list_models(kind))


def _list_models_clicked(kind: str) -> None:
    from nicegui import background_tasks

    background_tasks.create(_list_models(kind))


async def _list_models(kind: str) -> None:
    await engine.list_models(APP, kind)
    _refresh()


def _stop_indexing() -> None:
    """Stop the indexing run in flight.

    Synchronous, like the run bar's: it interrupts the profile that is executing
    and sets the flag the per-file loop reads, then returns. There is nothing to
    await — the thing you would be waiting for is the thing being cancelled.
    """
    if APP.indexing_stop is not None:
        APP.indexing_stop.cancel()


@ui.refreshable
def _progress() -> None:
    """What indexing is doing, in words, while it does it.

    Its own refreshable so it can be redrawn between files without rebuilding the
    file list underneath it — and profiling thirty real extracts is a minute of a
    window that would otherwise say nothing at all.
    """
    if not APP.indexing_status:
        return
    with ui.element("div").classes("row-gap-sm indexing-status"):
        ui.spinner(size="sm")
        c.text(APP.indexing_status, color="c-mute")
        # Beside the thing it stops, and only while there is something to stop.
        # Twenty real extracts is a minute of profiling and the point of a
        # no-terminal app is that ^C is not the way out of it.
        if APP.indexing_stop is not None:
            c.button("Stop", _stop_indexing, kind="tertiary", icon="stop")


# --- the one action, and the way out ----------------------------------------


@ui.refreshable
def _actions(*, in_dialog: bool = False) -> None:
    """Index what is outstanding, or — when nothing is — go and use it.

    **One primary at a time**, which is `DESIGN.md`'s one-accent-fill rule doing
    real work here: while there are files to profile the accent is on profiling
    them, and the moment there are none it moves to the way out. A screen
    offering both at once is a screen asking you to guess which one it wanted.

    **The dialog never takes the accent for its way out**, because there it is a
    Close rather than a destination — the workspace is already behind it, and an
    accented Close is the accent landing on the one control that does nothing.
    """
    outstanding = len(_ticked()) + len(APP.import_plan) + len(APP.scope_ticks)
    # **A pressed Index stays where it was, busy** *(2026-09-18)*. It used to
    # stay exactly as it was, live, for the whole of the profiling: nothing on
    # the button said the press had landed, and a second press started a second
    # run over the same files. The spinner in place of the label is the usual
    # answer, and it holds the button's size, so nothing under the hand moves.
    busy = bool(APP.indexing_pressed)
    with ui.element("div").classes("row-gap-sm"):
        if outstanding or busy:
            c.button(
                APP.indexing_pressed or _index_label(outstanding),
                lambda: _index_now(in_dialog=in_dialog),
                kind="primary",
                icon=c.INDEX_ICON,
                busy=busy,
            )
            # **Always a way out** *(2026-09-18, the user's call)*. It was
            # offered only once the project had a source, so a first index that
            # hung, or was only slow, held the screen with nothing but Back.
            # Leaving stops nothing: the run carries on, and the sources view
            # in the workspace shows the same status line and the same Stop.
            c.button(_leave_label(in_dialog), lambda: _leave(in_dialog), kind="secondary")
        elif APP.sources and not in_dialog:
            c.button(
                _leave_label(in_dialog),
                lambda: _leave(in_dialog),
                kind="primary",
                icon="arrow_forward",
            )
        elif in_dialog:
            c.button(_leave_label(True), lambda: _leave(True), kind="secondary")
        else:
            # Not a gate, unlike the brief: an empty project is a legitimate
            # place to stand, and Add data waits in the left pane.
            c.button("Skip for now", lambda: _leave(in_dialog), kind="secondary")
        if not in_dialog:
            # One step back. That is the question before this screen while it
            # can still be answered differently, and the brief once it cannot.
            back = _reopen_choice if engine.can_change_data(APP) else _back_to_brief
            c.button("Back", back, kind="secondary")
    c.caption(_action_note(outstanding))


def _index_label(outstanding: int) -> str:
    unit = "table" if APP.scope_ticks else "file"
    return f"Index {c.count(outstanding, unit)}"


def _leave_label(in_dialog: bool) -> str:
    return "Close" if in_dialog else "Open the workspace"


def _action_note(outstanding: int) -> str:
    """What the button is about to do, or what the last one did.

    **The breakdown partitions the button's count; it does not describe two
    separate jobs.** An imported file is profiled like every other, so splitting
    the line into "copies 1" and "profiles 22" made the two numbers look like
    they should add up to the button's 23 when in fact 23 files are profiled and
    one of them also had to be copied first. The parts say where each file came
    *from* — outside the repo, or already in it — which is a real partition and
    sums to the total the button names.

    It also says whether a model turn is **running**, because that is the state
    this screen is most often in now: profiling ends, the read starts by itself,
    and the way out stops being a promise about a cost you are going to pay and
    becomes a way to go and watch one you already are.
    """
    if outstanding and APP.scope_ticks:
        # The warehouse route has one part, so a breakdown would only restate
        # the count. What the press does with the tables is the thing to say,
        # and the switch on the right decides it.
        # **And where they came from**: the tree shows a tick beside its
        # schema, and this line is the one place the whole selection is one
        # sentence. With the profile switch on it is also the number of scans
        # a press puts on the meter, which a ticked schema makes easy to grow.
        lead = SCOPE_PROFILE_ALL if APP.profile_on_add else SCOPE_ALL
        tables = c.count(outstanding, "table")
        schemas = picktree.containers(APP.scope_ticks, ".")
        if schemas > 1:
            tables = SCOPE_ACROSS.format(n=tables, schemas=c.count(schemas, "schema"))
        return lead.format(n=tables)
    if outstanding:
        planned = len(APP.import_plan)
        here = outstanding - planned
        parts = []
        # Bare numbers in the parts: the lead already says "files", and repeating
        # the unit in each part reads as three separate counts of three things.
        if planned:
            parts.append(COPY_PART.format(n=planned, where=APP.import_dir(DATA_DIR)))
        if here:
            parts.append(ALREADY_HERE.format(n=here))
        return PROFILE_ALL.format(n=c.count(outstanding, "file"), parts=" · ".join(parts))
    if _read_running():
        # A dismissed popup must not leave the screen saying the copilot is
        # working when it has stopped and is waiting on the human who dismissed
        # it. The button's caption is the one line that is always on screen.
        job = APP.live_job
        return WAITING_ON_YOU if job is not None and job.pending is not None else READING_NOW
    if APP.indexed is not None:
        return PROFILED.format(n=c.count(APP.indexed, "source"))
    if APP.sources:
        return ADD_MORE_LATER
    return SKIP_HINT


def _read_running() -> bool:
    """Whether the copilot is reading sources right now.

    Off the live job rather than off `indexing_status`, which is also set while
    profiling: the question here is whether a *model turn* is in flight, and
    `APP.busy` would answer yes to a goal turn in another chat.
    """
    return APP.live_job is not None


async def _index_now(*, in_dialog: bool = False) -> None:
    """Copy what was planned, then profile everything ticked. One button, in order.

    The copy is first because its results join the profiling — importing three
    files and then having to tick them in a list that has just rebuilt is the
    kind of second step this screen was rewritten to remove.

    **One press, one run.** The button is busy from here until the profiling
    ends (`_pressed_done`), and a press that arrives anyway is dropped.
    """
    if APP.indexing_pressed or APP.indexing_stop is not None:
        return
    # **Held before anything is refreshed** (`app._say`'s rule, and the trap
    # this function's first build walked into on 2026-09-18). Making the button
    # busy redraws `_actions`, which deletes the button this handler is
    # standing in, and every `ui.notify` after that raised *the parent element
    # this slot belongs to has been deleted*. The run died at its first toast:
    # tables scoped and never read, the button busy for good, and no way out
    # of the screen. The client outlives any element in it, so the whole run
    # is entered through it.
    client = context.client
    outstanding = len(_ticked()) + len(APP.import_plan) + len(APP.scope_ticks)
    APP.indexing_pressed = _index_label(outstanding)
    _actions.refresh()
    with client:
        try:
            copied: list[Path] = []
            if APP.import_plan:
                pairs, APP.import_plan = APP.import_plan, []
                copied = await engine.import_files(pairs, APP)
                where = APP.import_dir(DATA_DIR)
                ui.notify(f"Copied {c.count(len(copied), 'file')} into {where}/.")
            # Deduplicated by path: an import into the data folder lands in a
            # place the ticked list was read from a moment ago, and profiling
            # it twice would be a minute of work for one entry.
            seen, paths = set(), []
            for path in [*_ticked(), *copied]:
                resolved = path.resolve()
                if resolved not in seen:
                    seen.add(resolved)
                    paths.append(path)
            tables = sorted(APP.scope_ticks)
            await _index_and_interpret(paths, in_dialog=in_dialog and not tables)
            await _scope_and_interpret(tables, in_dialog=in_dialog)
        finally:
            # Whatever happened in there, the button is a button again. A
            # no-op after a normal run, where profiling's own `finally` let go.
            _pressed_done()


def _pressed_done() -> None:
    """The free half is over, so the button is a button again.

    Called where profiling ends and **before** the read starts. The read is a
    model turn of a minute or more, indexing a second batch during it is an
    ordinary thing to do here (`_interpret_pending`), and a button busy for the
    length of it would be refusing that. It redraws the actions itself, so no
    path out of a run can leave a busy button behind it.
    """
    if APP.indexing_pressed:
        APP.indexing_pressed = ""
        _actions.refresh()


async def _index_and_interpret(paths: list[Path], *, in_dialog: bool = False) -> None:
    """Profile first — free, deterministic, always. Then, optionally, a turn.

    **Both routes start the read the moment profiling ends** (2026-08-07). It
    used to wait for the way out to be pressed when it ran from the screen, on
    the reading that a turn nobody can watch is a turn nobody should pay for —
    which was true of the version that ran it on a blank page, and stopped being
    true once the screen grew a status line and the popup below. What it cost
    was the whole wait, twice: profiling finished, the screen went quiet, and the
    minute of model time only began when you noticed and clicked.
    """
    from portia.ui import app as app_module
    from portia.ui import artifacts

    if not paths:
        return
    unsupported = [p for p in paths if not _is_supported(p)]
    for path in unsupported:
        ui.notify(f"Cannot read {path.name}. {_formats_sentence()}.")
    paths = [p for p in paths if p not in unsupported]
    if not paths:
        return

    def say(done: int, total: int, name: str) -> None:
        APP.indexing_status = f"Profiling {name}, {done + 1} of {total}"
        _progress.refresh()

    APP.indexing_status = f"Reading {c.count(len(paths), 'file')}…"
    stop = APP.indexing_stop = cancel.Scope()
    _progress.refresh()
    try:
        names = await engine.index(paths, APP, on_progress=say, stop=stop)
    finally:
        APP.indexing_status = ""
        APP.indexing_stop = None
        _pressed_done()
        # Made here, closed here — a cancelled scope holds a thread that goes on
        # interrupting connections the next indexing run may reuse.
        stop.close()
        _progress.refresh()

    # **What it profiled, not what was asked for.** A stopped run returns the
    # sources it did finish, and those are in the catalog and on the left pane —
    # saying "profiled 20 sources" because twenty were selected would be the one
    # number here that is not a measurement.
    ui.notify(f"Profiled {c.count(len(names), 'source')}.")
    APP.pending_interpret = [*APP.pending_interpret, *names]
    APP.indexed = len(names)
    # Profiled is done: it drops out of the outstanding list, which is what turns
    # the primary action into the way out. Same rule as an already-indexed file
    # arriving un-ticked, applied a moment later.
    #
    # **Only the ones that were actually profiled.** `engine.index` stops between
    # files, so the finished ones are the first `len(names)` of the list — and
    # retiring the rest because they were *selected* would leave the screen
    # claiming a file had been read when nothing had read it.
    APP.unpicked = APP.unpicked | {_rel(p).as_posix() for p in paths[: len(names)]}

    if in_dialog:
        _close_dialog()
        app_module.shell.refresh()
        artifacts.pane.refresh()
    else:
        _refresh()
        _catch_up_workspace()
    # **A stop stops the paid half too.** Profiling is free and deterministic;
    # interpretation is a model turn that costs money, and running one on the
    # sources that happened to finish — immediately after the human asked the app
    # to stop — is the app spending their money to disagree with them.
    if not stop.cancelled:
        await _interpret_pending()


def _catch_up_workspace() -> None:
    """Show a run's results to someone who left for the workspace while it ran.

    The way out is offered beside a running index now, so the run can end with
    the add-data screen gone. The left pane drew the catalog as it was when
    they walked in, and nothing else would tell it that sources have arrived.
    """
    from portia.ui import artifacts, transcript

    if APP.left_add_data:
        artifacts.pane.refresh()
        transcript.pane.refresh()


def _leave(in_dialog: bool) -> None:
    """Out of this surface. From the screen that means into the workspace.

    **It no longer waits for the read.** The turn started when profiling ended
    and it lands in the Indexing tab either way, so leaving mid-read walks into
    the transcript that is running rather than into a project describing sources
    nobody has looked at — which was what the wait existed to prevent. The
    screen still says a read is in flight (`_action_note`); being told is the
    part that was missing, not being held.
    """
    from portia.ui import app as app_module

    if in_dialog:
        _close_dialog()
        return
    # The invitation goes with the screen it was an invitation *away* from —
    # leaving under your own steam has to take it down, or it floats over the
    # workspace pointing at a tab you are already looking at.
    dismiss_invitation()
    # **Into the job, when one is running** *(2026-09-18, the user's call)*.
    # This button's own caption says the copilot is reading and the docstring
    # above promised the transcript that is running, and the workspace opened
    # on the chat list with the job one more click away. A job still never
    # takes the screen by itself (`CHAT_SESSIONS.md` §3.5). This is a press,
    # on a button under a sentence about that job.
    job = APP.live_job
    if job is not None:
        APP.show_chat(job)
    APP.enter_workspace()
    app_module.shell.refresh()


async def _interpret_pending() -> None:
    """Spend the turn that reads what each source *is*, if one was asked for.

    **A loop, not one turn**, because indexing a second batch while the first is
    being read is an ordinary thing to do on this screen: the names queue up in
    `pending_interpret` and are picked up when the running turn ends. The
    `busy` guard is checked immediately before `turn.start`, with nothing
    awaited in between — `turn.start` refuses a second live turn silently, and
    silently is exactly how a batch would go unread.

    The status line is deliberately coarse — one sentence, not a running
    commentary — because the running commentary is the transcript's, and the
    popup is what offers to put you in front of it.
    """
    from portia.ui import exchange

    while APP.interpret and APP.pending_interpret and not APP.busy:
        names, APP.pending_interpret = APP.pending_interpret, []
        APP.indexing_status = INTERPRETING.format(n=c.count(len(names), "source"))
        _redraw_progress()
        try:
            await exchange.start(
                prompts.task("index_batch", names=", ".join(repr(n) for n in names)),
                model=APP.model or _default_model(),
                effort=APP.effort,
                kind=state.INDEXING,
                label=", ".join(names),
            )
        finally:
            APP.indexing_status = ""
            # A turn that ended mid-question leaves an invitation to go and
            # answer something nobody can answer any more (`turn._resolve_orphans`).
            dismiss_invitation()
            _redraw_progress()


def _redraw_progress() -> None:
    """The two parts of this screen a running read changes, and only those.

    Not `_refresh()`: rebuilding the whole panel between turns throws away the
    folder you had opened and the ticks you were half-way through setting.
    """
    _progress.refresh()
    _actions.refresh()


# --- the same surface, as a dialog ------------------------------------------

#: The settings panel's width. Quasar sizes a dialog to its content, and a panel
#: of controls has no natural width — without this it collapses to a few hundred
#: pixels of nothing. **The add-data panel no longer uses it**: that one is a
#: card with its own width in `portia.css`, which is where a width belongs; this
#: survives because `settings.py` still has the problem it was written for.
DIALOG_WIDTH = "width:560px;max-width:92vw"

#: The add-data dialog for this page. Built once, at page level.
_ADD_DIALOG: ui.dialog | None = None


def build_add_dialog() -> None:
    """Create the add-data dialog. **Called once per page, never from a pane.**

    `ui.dialog` parents itself to the client layout and leaves a hidden canary
    element in whatever slot is current, whose job is to delete the dialog when
    that slot goes away. Build one inside a `@ui.refreshable` and the canary
    lives in the refreshable's container — so the first refresh takes the dialog
    with it and `open()` afterwards silently does nothing. NiceGUI says as much
    ("create it only once and then reuse it"); this is what that means in
    practice, and it cost an afternoon of a button that looked wired and wasn't.
    """
    global _ADD_DIALOG
    # No scale-in. Quasar's default animation leaves the panel at `scale(0)`
    # until a rAF fires, so a throttled tab shows an open dialog with nothing in
    # it — and a quiet developer surface has no use for a popping overlay anyway.
    with ui.dialog().props("transition-duration=0") as dialog:
        # The same card the screen draws. It was already a floating panel of
        # bounded height with its own scroll; the screen having been a full-page
        # column was the odd one out, and giving them one class is what stops the
        # two surfaces drifting apart the next time either is touched.
        # No inline width: `p-panel` sizes itself in both places it appears,
        # and `portia.css` out-specifies Quasar's 560px cap on a dialog's child.
        with ui.element("div").classes("p-panel"):
            panel(in_dialog=True)
    _ADD_DIALOG = dialog


def open_add_dialog() -> None:
    """Show it, with the file list read fresh. Says so if it isn't there.

    The ticks are re-seeded on open rather than kept from last time: coming back
    to this panel is coming back to the question "what is not profiled yet", and
    a stale set of exclusions would answer a question about the project as it was
    an hour ago.
    """
    if _ADD_DIALOG is None or _ADD_DIALOG.is_deleted:
        ui.notify(NO_DIALOG)
        return
    APP.indexed = None
    _seed_ticks()
    try:
        _refresh()
    except Exception as exc:  # noqa: BLE001 — never worth a dead add-data panel
        ui.notify(STALE_PANEL.format(why=type(exc).__name__))
    _ADD_DIALOG.open()


def _close_dialog() -> None:
    if _ADD_DIALOG is not None and not _ADD_DIALOG.is_deleted:
        _ADD_DIALOG.close()


# --- the invitation: the copilot has stopped, and you are not there ---------

#: The invitation for this page. Built once at page level, for the reason
#: `build_add_dialog` documents: a dialog created inside a refreshable is deleted
#: by that refreshable's first refresh, and this one is opened from a callback
#: several refreshes later.
_DECISION_DIALOG: ui.dialog | None = None


def build_decision_dialog() -> None:
    """Create the come-through popup. **Called once per page, never from a pane.**"""
    global _DECISION_DIALOG
    with ui.dialog().props("transition-duration=0") as dialog:
        with ui.element("div").classes("p-panel p-panel--prose"):
            _invitation()
    _DECISION_DIALOG = dialog


@ui.refreshable
def _invitation() -> None:
    """What is waiting, where it is, and the one button that goes there.

    **It names the kind of stop, not the question.** The question itself is a
    form with options and a free-text box, and a popup that reproduced it would
    be a second place to answer — which is a second answer waiting to disagree
    with the first, and the transcript is where every other decision in this app
    is taken. So this says what has happened and hands you over.

    Not-now is a real answer and says what it costs: the copilot is blocked
    either way, and a popup that implied otherwise would be describing a turn
    that has quietly stopped as one that is still working.
    """
    with ui.element("div").classes("p-panel-head"):
        ui.label(DECISION_TITLE).classes("t-heading-md")
        ui.label(_decision_line()).classes("p-panel-sub")
    with ui.element("div").classes("p-panel-body"):
        c.text(DECISION_WHERE, color="c-mute")
    with ui.element("div").classes("p-panel-actions"):
        with ui.element("div").classes("row-gap-sm"):
            c.button(DECISION_GO, _go_to_decision, kind="primary", icon="arrow_forward")
            c.button(DECISION_STAY, dismiss_invitation, kind="secondary")
        c.caption(DECISION_WAITS)


def _decision_line() -> str:
    """A question and a pending write are two different stops. Say which."""
    from portia.agent import events

    return DECISION_APPROVAL if APP.decision_waiting == events.APPROVAL else DECISION_QUESTION


def offer_workspace() -> None:
    """Invite them through to the decision. Called by `turn._stop`, once.

    Silent when the popup could not be built — an invitation that fails to
    appear must not take the turn down with it, and the way out of this screen
    is on screen regardless.
    """
    if _DECISION_DIALOG is None or _DECISION_DIALOG.is_deleted:
        return
    _invitation.refresh()
    _DECISION_DIALOG.open()


def dismiss_invitation() -> None:
    """Take the popup away, leaving the decision exactly where it was."""
    APP.decision_waiting = ""
    if _DECISION_DIALOG is not None and not _DECISION_DIALOG.is_deleted:
        _DECISION_DIALOG.close()


def _go_to_decision() -> None:
    """Accept: the workspace, with the chat that stopped on screen.

    **The screen's own way out**, not a second one — going through a popup and
    going through the button underneath it land you in the same place. Nothing
    switches to a chat on its own any more (`CHAT_SESSIONS.md` §3.5), so
    accepting the invitation is what opens the one holding the decision.
    """
    waiting = APP.waiting
    if waiting:
        APP.show_chat(waiting[0])
    _leave(in_dialog=False)


# --- shared helpers ---------------------------------------------------------


def _is_supported(path: Path) -> bool:
    return path.suffix.lower() in _suffixes()


def _suffixes() -> tuple[str, ...]:
    from portia.core.io import supported_suffixes

    return supported_suffixes()


def _formats() -> str:
    """The formats portia reads, spelled the way a person would say them.

    Read off the loader's registry rather than written down. This screen said
    "CSV" in four places and stopped being true the day Parquet landed; a label
    that can go stale is a label that will.
    """
    names = [s.lstrip(".").upper() for s in _suffixes()]
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " or " + names[-1]


def _formats_sentence() -> str:
    return f"portia reads {_formats()}"


def _default_model() -> str:
    """The provider's default, not one global one (`state.App.default_model`)."""
    return APP.default_model


# --- the words --------------------------------------------------------------

_OPEN_SUBTITLE = "Open a project directory. A path that does not exist is created."
_OPEN_NEW = "Type a path instead"

CONTEXT_PLACEHOLDER = "The project in a few sentences…"
#: The shape of a brief, in one line. It was four, plus a worked example from
#: another industry, and together they were longer than most briefs anyone would
#: write into the box beneath them — the conceptual notes for this screen painted
#: back onto it. Altitude is taught by asking a short question about the project
#: rather than by explaining at length what an answer is not.
CONTEXT_SHAPE = "The goal, how you model it, and roughly what data you have."

ADD_WHY_WAREHOUSE = (
    "Tables from {name}, read where they are. A ticked table arrives as metadata; the switches "
    "say whether it is profiled and read too."
)
ADD_WHY = (
    "portia reads {formats}. Choose the data folder in this repo, or import files from outside it."
)
IN_REPO_HEADING = "Data in this repo"
IN_REPO_WHY = "Choose the folder that holds this project's data."
NO_SUBFOLDERS = "No subfolders with readable data."
NO_DATA_HERE = "nothing readable"
MORE_FILES = "and {n} more"
INTERPRETING = "The copilot is reading {n}. Open the workspace to follow it, or wait here."
DESTINATION_PLACEHOLDER = "the project root"
DESTINATION_ROOT = "Leave empty to copy into the project root."
PICK_WHICH = "Profile these files"
ALL_DONE = "Everything here is already indexed"
PROJECT_ROOT = "the project root"
USE_ROOT = "Use the whole repo"
CHOSEN_NOTE = "this project's data"
INDEXED_NOTE = "already indexed"
PICKER_HINT = "No readable files here. Open a folder, or import data below."
PICKER_COUNT = "{files} under {where}, at any depth."
NOTHING_HERE = "No readable files in this folder. Open another, or import data below."
IMPORT_HEADING = "Import external data"
IMPORT_WHY = "Files from anywhere on disk are copied into the repo. Originals are not moved."
DESTINATION_DEFAULT = "Put it in {where}"
DESTINATION_SCOPE = "inside the project"
PLAN_HEADING = "About to copy {n}:"
IMPORT_COPY_ONLY = "Copies only. The originals are not changed."
NO_CHOOSER = (
    "The system file chooser is not available. "
    "Copy the files into the repo, then choose the folder above."
)
INTERPRET_COST = "Profiling is free and always happens. This spends a model exchange."
INTERPRET_COST_REMOTE = (
    "Spends a model exchange. It reads the metadata, or the profile if there is one."
)
PROFILE_SWITCH = "Profile each table on the warehouse"
PROFILE_OFF_COST = (
    "Off: a table arrives as metadata, for free. Columns and types, row count, last change. "
    "Profile later from the Indexing tab or the table itself."
)
PROFILE_ON_COST = "On: each table is scanned once on the warehouse. That is on its meter."
SCOPE_ALL = "Adds {n} as metadata. Free; nothing is scanned."
SCOPE_PROFILE_ALL = "Adds and profiles {n}. Each is scanned once on the warehouse."
SCOPE_ACROSS = "{n} across {schemas}"
SCOPED_METADATA = "Added {n} as metadata."
SCOPED_PROFILED = "Added {n}, {profiled} profiled."
READING_NEXT = "The copilot is reading them. The job is in the chat list."
INDEX_TAB_NEXT = "Profile or read them from the Indexing tab."
READ_TAB_NEXT = "Read them from the Indexing tab."
COPY_PART = "{n} copied in to {where}/"
ALREADY_HERE = "{n} already in the repo"
PROFILE_ALL = "Profiles {n}: {parts}"
PROFILED = "Profiled {n}. Everything here is indexed."
READING_NOW = "The copilot is reading them. The job is in the chat list."
WAITING_ON_YOU = "The copilot is waiting for you. The form is in the job, in the chat list."
ADD_MORE_LATER = "Add more later from the left pane."
SKIP_HINT = "Add data later from the left pane."
NO_DIALOG = "The add-data panel did not load. Reload the page."
STALE_PANEL = "Add data may show stale values ({why}). Reload the page."

#: The come-through popup. It names the stop and where the form is, and nothing
#: about the question itself — that is the transcript's, and one decision cannot
#: have two places to be taken.
DECISION_TITLE = "The copilot is waiting for you"
DECISION_QUESTION = "It has a question about the sources it is reading."
DECISION_APPROVAL = "It is asking to write what it has read."
DECISION_WHERE = "The form is in the workspace, on the Indexing tab."
DECISION_GO = "Open the workspace"
DECISION_STAY = "Later"
DECISION_WAITS = "Nothing continues until you answer."

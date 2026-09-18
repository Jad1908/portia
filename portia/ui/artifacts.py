"""Left pane — the project directory, filtered to what portia reads.

`VISION.md` asks how we decide what to surface inside a big repo. The answer is
still **a file appears if portia knows about it**, but that is now a *filter over
a real tree* rather than six flat sections. What the sections cost was the shape
of the project: a spec in ``specs/staging/`` and its model in
``models/staging/`` came out as two same-named rows with nothing on screen
saying where either file was — which is the first question anyone asks of a
pipeline they have been handed. Six known folders is also a structure the app
was imposing on the agent, and the folders are not portia's to fix.

So: `tree.build` walks the directory and keeps a file if the catalog, the spec
discovery, the compiled models, the written outputs or the saved runs know it —
or if `core.io` registers a reader for its suffix, which is how a data file
sitting in the repo un-indexed becomes visible instead of invisible. A folder is
drawn only if something under it survived. Kind still comes through as the
leading icon, so what a file *is* reads at a glance the way it did before.

Two things are pinned outside the tree because they live in ``.portia/``, which
is not walked:

- **The brief**, at the top. It is the most consequential text in the product and
  it used to be reachable only from a toolbar button that no longer exists.
- **Chats** and **Indexing**, at the foot, and they are two lists rather than one
  (`docs/CONVERSATION.md` §3). A *run* executed a spec and is a markdown file in
  the project; a *chat* was a conversation about what the spec should say; an
  *indexing* was a job the app ran on your behalf. The run sits in the tree
  where its file is. The other two are **not here any more**: they are the
  right pane's own list, where a chat can be picked up rather than only read
  (`docs/CHAT_SESSIONS.md` §3.8).

Nothing here ranks. Folders sort before files and both sort by name; no row is
coloured, sized or ordered by anything measured (`DESIGN.md`).
"""

from __future__ import annotations

from pathlib import Path

from nicegui import ui

from portia import catalog
from portia.ui import components as c
from portia.ui import engine, state, tree
from portia.ui import graph as graph_module
from portia.ui.state import (
    APP,
    BRIEF,
    BUILT,
    KNOWLEDGE,
    MODEL,
    OUTPUT,
    RUN,
    SOURCE,
    SPEC,
    UNINDEXED,
)

ICON = {
    SOURCE: "table_chart",
    SPEC: "account_tree",
    MODEL: "code",
    OUTPUT: "description",
    RUN: "history",
    UNINDEXED: "insert_drive_file",
    tree.FOLDER: "folder",
}

#: The disclosure triangle, which is **the** control: a folder says whether it is
#: open before you click it, and the caret is the whole of how it says so.
#:
#: The folder icon deliberately does not change with it. It used to swap to the
#: hollow `folder_open` glyph, which put two marks on one piece of state — and
#: the second one read as the folder having changed *kind* rather than having
#: opened, because a filled shape going hollow is how this app says "different
#: thing", not "same thing, expanded".
CARET_OPEN = "expand_more"
CARET_SHUT = "chevron_right"

EMPTY_TREE = "No readable data files in this directory. Add data to begin."
#: Said on a chart that has a tab, so the rows say which of the two states they
#: are in. The other state gets no word: *closed* is the resting condition of a
#: chart you are not looking at, and labelling both would put a badge on every
#: row to distinguish a thing from its own default.
_ON_STRIP = "open"

#: The gallery's own strings (`docs/VISUALIZATION.md` §6).
GALLERY = "Figures"
GALLERY_ROOT = "Figures (top level)"
NEW_FOLDER_TIP = "New folder"
NEW_FOLDER_HINT = "Folder name"
NEW_FOLDER_SAVE = "Create"
NEW_FOLDER_CANCEL = "Cancel"
REMOVE_TIP = "Delete this figure"
REMOVE_FOLDER_TIP = "Delete this folder"
#: Asked inline, under the folder, before a delete that takes figures with it.
#: The count is stated because it is the whole reason the question is asked.
DELETE_FOLDER_ONE = "Delete {name} and the figure in it?"
DELETE_FOLDER_MANY = "Delete {name} and the {n} figures in it?"
DELETE_FOLDER_GO = "Delete"
DELETE_FOLDER_CANCEL = "Cancel"
UNSAVED = "unsaved"
DISCARD_TIP = "Discard this chart"
QUICK_SAVE_TIP = "Save this figure to Figures"
UNINDEXED_NOTE = "not indexed"
STALE_SPEC_NOTE = "compiled SQL out of date"
STALE_MODEL_NOTE = "stale: spec changed"


@ui.refreshable
def pane() -> None:
    """The brief, the tree, and the warehouse.

    Keyed, because selecting a row rebuilds this pane to move one highlight and
    an unkeyed rebuild would send a long list back to the top each time you
    clicked something near the bottom of it (`components.scroll_area`).
    """
    with c.scroll_area("artifacts"):
        _pinned()
        _tree()
        _warehouse_section()
    _add_data_affordance()


# --- the warehouse (`docs/CONNECTOR.md` §2.13) -------------------------------

WAREHOUSE_ICON = "cloud"
DATABASE_ICON = "storage"
SCHEMA_ICON = "schema"
#: A scoped or built table nobody has scanned. *Metadata only* rather than
#: *not profiled* (2026-09-07): beside the Indexing tab's *not read* the old
#: word read as one more way of saying the table had not been indexed, when
#: it had, and the inspector already said *indexed as metadata*.
NOT_PROFILED_NOTE = "metadata only"
CONNECT_LABEL = "Connect"
CONNECT_AGAIN_LABEL = "Connect again"
NO_SCOPE_NOTE = "Nothing in scope yet. Add data to pick tables."


def _warehouse_section() -> None:
    """The connection and the tables in scope — pinned, because none of it is a file.

    A directory walk cannot reach a warehouse, and a folder that is not on disk,
    opened by a click that runs a query, would be the tree lying about what a
    row is. So this is its own section between the tree and the histories, with
    the connection as its head, databases and schemas as its levels and the
    scoped tables as its rows. **Kind, never rank**: a cloud for the connection,
    one glyph for a database and one for a schema, the source glyph for a table.
    It draws the **scope** only — the picker on the add-data screen is where the
    rest of the warehouse is browsed, as `data_dir` scopes the file tree.

    The head's meta is the connection's state, in words, and a failed or
    unopened session gets a button rather than a sentence explaining itself.
    """
    if not APP.connection:
        return
    c.rule()
    c.section_header("Warehouse")
    row = c.artifact_row(
        name=APP.connection,
        icon=WAREHOUSE_ICON,
        meta=_connection_word(),
        on_click=_toggle_warehouse,
    )
    with row:
        ui.icon("expand_more" if APP.warehouse_open else "chevron_right").classes("gallery-caret")
    if not APP.warehouse_open:
        return
    if APP.connection_status.startswith("failed"):
        with ui.element("div").classes("p-2 stack-xs"):
            c.alert(APP.connection_status.removeprefix("failed: "), kind="error")
            c.button(CONNECT_AGAIN_LABEL, connect_now, icon=WAREHOUSE_ICON, micro=True)
    elif APP.connection_status == state.NOT_CONNECTED:
        with ui.element("div").classes("p-2"):
            c.button(CONNECT_LABEL, connect_now, icon=WAREHOUSE_ICON, micro=True)
    nodes = engine.warehouse_tree(APP)
    if not nodes:
        c.empty_note(NO_SCOPE_NOTE)
    for node in nodes:
        _warehouse_node(node, 1)


def _connection_word() -> str:
    """The state, short enough for the meta slot: the first word of a failure."""
    status = APP.connection_status
    return "failed" if status.startswith("failed") else status


def _warehouse_node(node: tree.Node, depth: int) -> None:
    if node.kind == state.MODEL:
        # A table portia built there (`CONNECTOR.md` §2.7.2): the model glyph,
        # and a click opens **the catalog's entry for it**, read the way a
        # source's is — with the spec one button away from there. It opened the
        # spec until 2026-09-07, which left no way to see or ask for what portia
        # had measured about a table it had itself built.
        entry = catalog.load_models(APP.portia_dir).get(node.ident) or {}
        row = c.artifact_row(
            name=node.name,
            icon=ICON[MODEL],
            depth=depth,
            note="" if catalog.is_profiled(entry) else NOT_PROFILED_NOTE,
            selected=APP.is_selected(BUILT, node.ident),
            on_click=lambda n=node.ident: _select(BUILT, n),
        )
        c.enters(row, f"warehouse:{node.rel}")
        return
    if node.kind == state.SOURCE:
        entry = APP.sources.get(node.ident) or {}
        row = c.artifact_row(
            name=node.name,
            icon=ICON[SOURCE],
            depth=depth,
            note="" if catalog.is_profiled(entry) else NOT_PROFILED_NOTE,
            selected=APP.is_selected(SOURCE, node.ident),
            on_click=lambda n=node.ident: _select(SOURCE, n),
        )
        # A table just scoped in from the add-data picker arrives (`c.enters`).
        c.enters(row, f"warehouse:{node.rel}")
        return
    shut = node.rel in APP.warehouse_closed
    c.artifact_row(
        name=node.name,
        icon=DATABASE_ICON if node.kind == tree.DATABASE else SCHEMA_ICON,
        depth=depth,
        caret=CARET_SHUT if shut else CARET_OPEN,
        meta=str(len(node.children)) if shut else "",
        on_click=lambda r=node.rel: _toggle_warehouse_node(r),
    )
    if not shut:
        for child in node.children:
            _warehouse_node(child, depth + 1)


def _toggle_warehouse() -> None:
    APP.warehouse_open = not APP.warehouse_open
    pane.refresh()


def _toggle_warehouse_node(rel: str) -> None:
    APP.toggle_warehouse_node(rel)
    pane.refresh()


def connect_now() -> None:
    """A press on Connect: the dialog when something must be typed, else the session.

    A connection that signs in with a password or a token needs it typed this
    session, and the dialog is where that box is (`screens.open_connect_dialog`).
    Browser SSO needs nothing typed, so it goes straight to the session.
    """
    from portia.ui import screens

    if engine.needs_secret(APP) or APP.connection_status.startswith("failed"):
        screens.open_connect_dialog()
        return
    connect_in_background()


def connect_in_background() -> None:
    """Open the session off the loop and redraw when it is known how that went.

    Called when a project that names a connection opens, and from the buttons
    above. A browser may open (`docs/CONNECTOR.md` §2.4); the window says
    *connecting* meanwhile and never blocks on it. A connection that needs a
    secret is **not** attempted here — that would fail on purpose and draw a
    failure for something nobody has been asked for yet; the pane offers
    Connect, which opens the dialog (`connect_now`).
    """
    from nicegui import background_tasks

    if not engine.needs_connection(APP) and not APP.connection_status.startswith("failed"):
        return
    if engine.needs_secret(APP):
        return

    from portia.ui import screens

    async def go() -> None:
        # Both surfaces that draw the connection's state: the pinned section,
        # and the add-data panel, whose own Connect button lands here on a
        # connection that needs nothing typed. Without the second, the panel
        # said *not connected yet* over an open session until something else
        # redrew it (2026-09-07). A no-op when the panel is not on screen.
        pane.refresh()
        screens.panel.refresh()
        await engine.connect_project(APP)
        pane.refresh()
        screens.panel.refresh()

    background_tasks.create(go())


# --- the pinned rows --------------------------------------------------------


def _pinned() -> None:
    """The things portia knows that are not files, drawn as their own group.

    Four rows sat loose above a file tree and read as four odd files
    *(2026-09-02)*. They are a real category — the brief lives in
    ``.portia/project.yaml``, the knowledge graph lives in Neo4j, the canvas is
    computed from the specs, and a chart is in memory — and none of them is
    anywhere the tree below could walk to.

    Grouping them is legitimate under `DESIGN.md`'s product rule, and worth
    saying why: *not a file* is a **kind**, not a rank. The band says these are a
    different sort of thing from the rows under it. It does not say they are more
    important, none of them is drawn larger than another, and the order is what
    the three read as a sentence — what this project is for, what we built, what
    the data is to itself — rather than any ranking.
    """
    with ui.element("div").classes("p-pinned"):
        _brief_row()
        _pipeline_row()
        _knowledge_row()
        _figures_section()


# --- the brief --------------------------------------------------------------


def _brief_row() -> None:
    """The project brief, as a row you open rather than a button in the chrome.

    It is not a file in the tree — ``.portia/project.yaml`` is catalog plumbing
    and hand-editing it is not something the pane should invite — but it *reads*
    as one, at the top, because that is where the thing the whole project is
    conditioned on belongs. A project with no brief cannot exist: the gate in
    `screens.project_context` is passed before this pane is ever drawn.

    It opens in the middle pane like every other row here, rather than in the
    dialog it used to live in. A paragraph you are meant to rewrite with the
    sources on screen beside it is not a thing to type into an overlay.
    """
    row = c.artifact_row(
        name="Project brief",
        icon="notes",
        selected=APP.is_selected(BRIEF, ""),
        on_click=lambda: _select(BRIEF, ""),
    )
    # The one tooltip left in this pane, because it is the one that says
    # something the row does not: the brief itself.
    c.hint(row, APP.project_context)


def _pipeline_row() -> None:
    """The project canvas, as a row you can press *(2026-08-16)*.

    The canvas is what the middle pane shows when nothing is selected, which made
    it the one surface in this app with no way back to it: opening a file, a run
    or a chat replaced it, and returning meant finding a spec to click. A pinned
    row makes it a destination like everything else here.

    **Between the brief and the knowledge graph**, because that is the order the
    three read in: what this project is *for*, what we built, and what the data is
    to itself. It is selected when nothing else is — the canvas is the pane's
    resting state, not a fourth kind of artifact.
    """
    c.artifact_row(
        name="Pipeline",
        icon="account_tree",
        # A chart tab in front of the canvas means the canvas is not what you are
        # looking at, even though nothing on the left is selected. Reading
        # `selection` alone lit this row while a chart was on screen
        # (`docs/VISUALIZATION.md` §3.2).
        selected=APP.selection is None and APP.active.get(APP.focus_group) == state.CANVAS,
        on_click=_show_pipeline,
    )


def _show_pipeline() -> None:
    """Back to the canvas: clear the selection, keep everything else.

    The open spec, the open cards and the filter all survive — pressing this is
    "show me the canvas again", not "start over".
    """
    from portia.ui import workflow

    # `select` returns to tab zero on its own, so a chart in front of the canvas
    # is stood down by the same call that clears the selection.
    APP.select(None)
    pane.refresh()
    workflow.pane.refresh()


def _knowledge_row() -> None:
    """The knowledge graph, pinned beside the brief.

    Not in the tree because it is not a file — it lives in Neo4j — and not a
    mode of the workflow canvas because it is not the same graph. The canvas
    draws what we specified; this draws what the data is to itself
    (`KNOWLEDGE_GRAPH.md` §6.9). Two rows, so neither has to pretend to be the
    other.
    """
    c.artifact_row(
        name="Knowledge graph",
        icon="hub",
        selected=APP.is_selected(KNOWLEDGE, ""),
        on_click=lambda: _select(KNOWLEDGE, ""),
    )


def _figures_section() -> None:
    """The gallery: this session's charts and every figure saved to disk, in one
    place, in the folders the user arranged.

    **One list, not two** *(2026-09-03)*. Unsaved charts had their own rows above
    the file tree while saved ones lived under `Figures`, so a chart you had just
    kept appeared twice — once as the file and once as the session copy still
    claiming to be a thing. Two rows for one picture, and the wrong one is the
    one you would go on working with. Saving turns the session chart into the
    file (`state.App.figure_saved`) and what is left is one row — **and one tab**,
    which is the half of that the first version got wrong *(2026-09-03)*: it
    retired the chart, so pressing *keep* on a picture took you away from it.

    An unsaved chart is marked and can be thrown away; a saved one can be dragged
    into a folder. Those are the two states and the row says which.

    **The caret is what says it is empty.** A sentence explaining that nothing is
    under a heading is a line of prose in a pane made of one-line rows; an open
    section with nothing under it says the same thing and says it by being empty.
    """
    saved = engine.saved_figures(APP)
    _figures_header(saved)
    if not APP.figures_open:
        return
    # A saved chart is drawn by `_figure_row` off the file it became, so it is
    # skipped here — one picture, one row, whether or not it still has a tab.
    for chart in APP.charts:
        if not chart.saved:
            _unsaved_row(chart)
    if APP.figures_adding == "":
        _new_folder_field()
    _figure_tree(saved)


def _figures_header(saved: list[dict]) -> None:
    """The gallery's own row — an `artifact_row` like every row above it.

    It was a bespoke `div` with its own padding and sat visibly out of line with
    the brief, the canvas and the graph *(2026-09-03)*. There was no reason for
    it to be a different component: it is a row you click, which is what
    `artifact_row` is.

    **Its chevron is on the right, not in `caret`.** A left caret indents the
    icon behind it, which put `Figures` visibly out of line with the three rows
    above it — none of which discloses anything, so none of which reserves the
    space. Aligning by giving the other three an invisible caret would indent the
    whole pinned group to pay for one row's control. On the right it says *this
    opens* without moving anything.

    It is also a **drop target** for the gallery's top level, so dragging a
    figure out of a folder has somewhere to land.
    """
    count = len(saved) + sum(1 for chart in APP.charts if not chart.saved)
    row = c.artifact_row(
        name=GALLERY,
        icon="photo_library",
        meta=str(count) if count else "",
        selected=APP.is_selected(state.FIGURES, ""),
        on_click=_toggle_gallery,
    )
    row.props('data-folder=""')
    with row:
        add = ui.icon("create_new_folder").classes("gallery-add")
        add.tooltip(NEW_FOLDER_TIP)
        add.on("click.stop", lambda: _start_folder(""))
        ui.icon("expand_more" if APP.figures_open else "chevron_right").classes("gallery-caret")


def _unsaved_row(chart) -> None:
    """A chart this session drew and nobody has kept.

    The pill is the point: everything else in this section is a file, and a row
    that looks like one but disappears when the window closes is the sort of
    thing you find out about afterwards. It says `unsaved` rather than being
    drawn dimmer — dim reads as *less important*, and an unsaved chart is often
    the most important thing in the list (`DESIGN.md`: kind, never rank).

    Not draggable. There is nowhere on disk to drag it *to* until it is saved,
    and a drag that silently saved would be a write nobody asked for.
    """
    row = c.artifact_row(
        name=chart.name,
        icon="error_outline" if chart.error else "insert_chart_outlined",
        note=chart.question,
        depth=1,
        selected=_showing(chart.key),
        # One press previews it, two open it for keeps — `assets/pick.js` decides
        # which, before either reaches the server. Its identity here is the
        # chart's **position**, because a chart's name is a sentence the agent
        # wrote and an attribute is not the place for one.
        opens=f"chart:{APP.charts.index(chart)}",
    )
    # Keyed by the chart's name, not its position: rows above it can be saved
    # or discarded, and a row that re-animated because its neighbour left would
    # be movement about nothing (`c.enters`).
    c.enters(row, f"gallery:chart:{chart.key}")
    with row:
        ui.label(UNSAVED).classes("gallery-pill")
        # **Kept from the row, in one press** *(2026-09-04, the user's call)*.
        # The form in the middle pane is where a note gets written; most saves
        # have no note, and walking to the tab to press *Keep this* on a chart
        # you can already see listed is the extra decision the gallery keeps
        # taking out. Same save path as the form (`charts.keep`), no note, top
        # level. Drawn only once there is a picture to keep.
        if chart.drawable:
            save = ui.icon("bookmark_add").classes("gallery-add")
            save.tooltip(QUICK_SAVE_TIP)
            save.on("click.stop", lambda k=chart.key: _quick_save(k))
        drop = ui.icon("close").classes("gallery-add")
        drop.tooltip(DISCARD_TIP)
        drop.on("click.stop", lambda k=chart.key: _discard_chart(k))


def _figure_tree(saved: list[dict]) -> None:
    """Folders and figures, grouped by the folder each one is in.

    Grouped off the list `engine.saved_figures` already read rather than walked
    again: a second pass over the filesystem is a second answer waiting to
    disagree with the first.
    """
    from portia import figures as figures_module

    by_folder: dict[str, list[dict]] = {}
    for figure in saved:
        rel = Path(figure["path"]).relative_to(figures_module.FIGURES_DIR).parent
        by_folder.setdefault(str(rel) if str(rel) != "." else "", []).append(figure)
    known = sorted({*by_folder, *(f for f in engine.figure_folders(APP) if f)})
    _figure_level("", known, by_folder, depth=1)


def _figure_level(parent: str, known: list[str], by_folder: dict, depth: int) -> None:
    """One folder's children: the folders under it, then the figures in it."""
    prefix = f"{parent}/" if parent else ""
    children = sorted(
        {f for f in known if f.startswith(prefix) and "/" not in f[len(prefix) :] and f != parent}
    )
    for folder in children:
        _figure_folder_row(folder, known, by_folder, depth)
    for figure in by_folder.get(parent, []):
        _figure_row(figure, depth)


def _figure_folder_row(folder: str, known: list[str], by_folder: dict, depth: int) -> None:
    """A folder: a disclosure, a place to add another, and a drop target."""
    open_now = folder not in APP.figures_closed
    row = c.artifact_row(
        name=folder.rsplit("/", 1)[-1],
        icon="folder",
        depth=depth,
        caret="expand_more" if open_now else "chevron_right",
        on_click=lambda f=folder: _toggle_folder(f),
    )
    # `assets/gallery.js` reads this to know where a dropped figure lands. A
    # figure's *filename* has no spaces (`figures.slug`) but a folder is named by
    # a person, and unquoted, `palette test` reached the browser as `palette`
    # (`components.prop_value`).
    row.props(f"data-folder={c.prop_value(folder)}")
    with row:
        add = ui.icon("create_new_folder").classes("gallery-add")
        add.tooltip(NEW_FOLDER_TIP)
        add.on("click.stop", lambda f=folder: _start_folder(f))
        drop = ui.icon("delete_outline").classes("gallery-add")
        drop.tooltip(REMOVE_FOLDER_TIP)
        drop.on("click.stop", lambda f=folder: _remove_folder(f))
    # The question sits under the row it is about, open or shut, because the
    # press that raised it was on this row and the answer is owed here.
    if APP.figures_deleting == folder:
        _folder_confirm(folder, depth + 1)
    if not open_now:
        return
    # The naming field opens **inside the folder it will create one in**, which
    # is where the result appears. A form that acts somewhere other than where it
    # opened makes you go and check afterwards.
    if APP.figures_adding == folder:
        _new_folder_field(depth + 1)
    _figure_level(folder, known, by_folder, depth + 1)


def _figure_row(figure: dict, depth: int) -> None:
    """One saved figure. Draggable into any folder; deletable.

    **There is no *move to…* button** *(2026-09-03)*. Moving a thing into a
    folder is a drag — the menu was the same operation with two extra decisions
    in it, and it opened a list of folders inside the list of folders it was
    about. `assets/gallery.js` resolves the gesture on the client for `pick.js`'s
    reason: the rows are rebuilt between the press and the release.
    """
    path = figure["path"]
    row = c.artifact_row(
        name=figure.get("name") or Path(path).stem,
        icon="insert_chart_outlined",
        note=figure.get("notes") or "",
        depth=depth,
        # **A figure is a tab, so the row is lit by the strip and not by the
        # selection** (§6.4). Selecting it would have put the picture in tab zero
        # and stood down whichever chart was in front of it, which is the pane
        # ignoring a click that was about neither.
        selected=_showing(path),
        opens=f"figure:{path}",
    )
    row.props(f"draggable=true data-figure={c.prop_value(path)}")
    # Keyed by the path, so saving a chart — which turns its `gallery:chart:`
    # row into this one — reads as the arrival it is, and a figure dropped on a
    # folder shows itself at the destination (`c.enters`).
    c.enters(row, f"gallery:fig:{path}")
    with row:
        drop = ui.icon("delete_outline").classes("gallery-add")
        drop.tooltip(REMOVE_TIP)
        drop.on("click.stop", lambda p=path: _remove_figure(p))


def _showing(key: str) -> bool:
    """Whether a tab is on screen — in either half of a split pane (§3.7).

    The rows here say *where a picture went*, and a tab in the split half is as
    much on screen as the one beside it. Reading one group would leave the row
    for the half you are looking at unlit.
    """
    return key in APP.active.values()


def _new_folder_field(depth: int = 1) -> None:
    """Name it. Enter creates, Escape abandons, and the two buttons say so.

    The keys are bound as well as drawn because a field you type a name into is
    one you expect to submit with Enter, and a field you opened by accident is
    one you expect to leave with Escape. The buttons are for the pointer.
    """
    with ui.element("div").classes("gallery-new").style(f"--depth:{depth}"):
        box = (
            ui.input(placeholder=NEW_FOLDER_HINT)
            .classes("p-field w-full")
            .props("borderless dense autofocus")
        )
        box.bind_value(APP, "figures_new_name")
        box.on("keydown.enter", _make_folder)
        box.on("keydown.escape", _cancel_folder)
        c.button("", _make_folder, icon="check", micro=True).tooltip(NEW_FOLDER_SAVE)
        c.button("", _cancel_folder, icon="close", micro=True).tooltip(NEW_FOLDER_CANCEL)


def _folder_confirm(folder: str, depth: int) -> None:
    """The one question the gallery asks: a delete that would take figures too.

    Inline rather than a dialog, where the naming field already is and for its
    reason: a form that acts somewhere other than where it opened makes you go
    and check afterwards, and a modal over the whole window for a row in the
    left pane is that with a dimmed screen. The count is the question; a delete
    that takes one thing is not asked about at all (`figures.remove_folder`).
    """
    n = len(engine.folder_contents(APP, folder))
    name = folder.rsplit("/", 1)[-1]
    text = DELETE_FOLDER_ONE if n == 1 else DELETE_FOLDER_MANY
    with ui.element("div").classes("gallery-confirm").style(f"--depth:{depth}"):
        c.caption(text.format(name=name, n=n), color="c-body")
        with ui.element("div").classes("gallery-confirm-actions"):
            c.button(DELETE_FOLDER_GO, lambda f=folder: _delete_folder(f), micro=True)
            c.button(DELETE_FOLDER_CANCEL, _cancel_delete, micro=True)


# --- gallery gestures -------------------------------------------------------


def _toggle_gallery() -> None:
    APP.figures_open = not APP.figures_open
    pane.refresh()


def _toggle_folder(folder: str) -> None:
    APP.figures_closed = (
        (APP.figures_closed - {folder})
        if folder in APP.figures_closed
        else (APP.figures_closed | {folder})
    )
    pane.refresh()


def _start_folder(parent: str) -> None:
    APP.figures_open = True
    APP.figures_adding = parent
    APP.figures_new_name = ""
    # Opening a folder you are adding into, so the field is somewhere visible.
    APP.figures_closed = APP.figures_closed - {parent}
    pane.refresh()


def _cancel_folder() -> None:
    APP.figures_adding = None
    APP.figures_new_name = ""
    pane.refresh()


def _make_folder() -> None:
    parent = APP.figures_adding or ""
    name = (APP.figures_new_name or "").strip()
    if not name:
        _cancel_folder()
        return
    from portia import figures as figures_module

    try:
        figures_module.make_folder(f"{parent}/{name}" if parent else name, root=APP.root)
    except ValueError as exc:
        ui.notify(str(exc))
        return
    _cancel_folder()


def move_figure(path: str, folder: str) -> None:
    """Land a dragged figure. Called from `app`'s `portia:figure-move` handler.

    Public because the event arrives at page level — `assets/gallery.js` emits
    one event with the gesture already resolved, and `app.py` registers the
    handler there for the reason every other client event is registered there:
    inside a refreshable it would be registered again on every refresh.
    """
    from portia import figures as figures_module
    from portia.ui import workflow

    try:
        moved = figures_module.move(path, folder, root=APP.root)
    except ValueError as exc:
        ui.notify(str(exc))
        return
    # A tab showing it follows the file (`state.App.figure_moved`): a drag is a
    # rename portia did not author, and the picture on screen did not change.
    APP.figure_moved(path, str(moved.relative_to(APP.root)))
    pane.refresh()
    workflow.pane.refresh()


def _remove_figure(path: str) -> None:
    from portia import figures as figures_module
    from portia.ui import workflow

    try:
        figures_module.remove(path, root=APP.root)
    except ValueError as exc:
        ui.notify(str(exc))
        return
    # The tab goes with the file. A tab drawing a picture the gallery says is
    # deleted is the artifact-in-two-places problem the other way round.
    APP.retire_chart(path)
    pane.refresh()
    workflow.pane.refresh()


def _remove_folder(folder: str) -> None:
    """Delete a folder, or ask first if it is not empty.

    Empty goes at once, as an empty folder always has (`figures.remove`). One
    that holds figures opens the question under its row; nothing is deleted
    until *Delete* is pressed there, and a second press on another folder's
    control moves the question rather than stacking two.
    """
    if engine.folder_contents(APP, folder):
        APP.figures_deleting = folder
        pane.refresh()
        return
    _delete_folder(folder)


def _delete_folder(folder: str) -> None:
    from portia.ui import workflow

    try:
        gone = engine.remove_folder(APP, folder)
    except ValueError as exc:
        ui.notify(str(exc))
        APP.figures_deleting = None
        pane.refresh()
        return
    # Every tab drawing a picture that just left the disk goes with it — the
    # rule `_remove_figure` follows, applied to each of them.
    for path in gone:
        APP.retire_chart(path)
    APP.figures_deleting = None
    APP.figures_closed = APP.figures_closed - {folder}
    pane.refresh()
    workflow.pane.refresh()


def _cancel_delete() -> None:
    APP.figures_deleting = None
    pane.refresh()


def _quick_save(key: str) -> None:
    """Keep an unsaved chart from its row: no note, top level, one press.

    `charts.keep` is the form's save and this one, so the row and the form
    cannot disagree about what saving does to the tab. An error has no form to
    sit beside here, so it is said in a notification.
    """
    from portia.ui import charts

    chart = APP.chart(key)
    if chart is None:
        return
    error = charts.keep(chart)
    if error:
        ui.notify(error)
    charts.refresh()


def _discard_chart(key: str) -> None:
    """Throw away an unsaved chart: its row and its tab, together.

    Distinct from closing its tab, which keeps it. Nothing is on disk, so there
    is nothing to confirm — and the copilot can draw it again from the query,
    which is in the chat log either way.
    """
    from portia.ui import workflow

    APP.retire_chart(key)
    pane.refresh()
    workflow.pane.refresh()


def _open_chart(key: str) -> None:
    """Put a chart back on the strip, or focus the tab it already has."""
    from portia.ui import workflow

    APP.reopen_chart(key)
    pane.refresh()
    workflow.pane.refresh()


def opened(kind: str, ident: str, *, reveal: bool) -> None:
    """A click on a gallery row, with the gesture already resolved on the client.

    Public because the event arrives at page level (`assets/pick.js`), and one
    function for both kinds because the rule is one rule: **a single click opens
    a preview and a double click keeps it** *(2026-09-03, the user's call)*.
    Reading down a list of twenty figures should cost one tab, not twenty.
    """
    from portia.ui import charts

    if kind == "figure":
        charts.open_figure_tab(ident, reveal=reveal)
        return
    chart = APP.charts[int(ident)] if ident.isdigit() and int(ident) < len(APP.charts) else None
    if chart is not None:
        charts.open_chart_tab(chart.key, reveal=reveal)


# --- the tree ---------------------------------------------------------------


def _tree() -> None:
    nodes = engine.project_tree(APP)
    if not nodes:
        # On a warehouse project the data is the section below, and *no readable
        # files here* would be true and beside the point.
        if not APP.connection:
            c.empty_note(EMPTY_TREE)
        return
    stale = set(engine.stale_models(APP))
    for node in nodes:
        _node(node, 0, stale)


def _node(node: tree.Node, depth: int, stale: set[str]) -> None:
    if node.is_folder:
        _folder(node, depth, stale)
    else:
        _file(node, depth, stale)


def _folder(node: tree.Node, depth: int, stale: set[str]) -> None:
    """A folder, and its contents when it is open. Disclosure, one level at a time."""
    is_open = APP.folder_open(node.rel, depth)
    c.artifact_row(
        name=node.name,
        icon=ICON[tree.FOLDER],
        caret=CARET_OPEN if is_open else CARET_SHUT,
        depth=depth,
        on_click=lambda rel=node.rel, d=depth: _toggle(rel, d),
    )
    if is_open:
        for child in node.children:
            _node(child, depth + 1, stale)


def _file(node: tree.Node, depth: int, stale: set[str]) -> None:
    row = c.artifact_row(
        name=node.name,
        icon=ICON.get(node.kind, ICON[UNINDEXED]),
        meta=_meta(node),
        note=_note(node, stale),
        depth=depth,
        selected=APP.is_selected(node.kind, node.ident),
        # A spec is driven by `assets/pick.js`, which resolves click-versus-double
        # before either reaches the server. Wiring `on_click` as well would send
        # the light action a second time on the second press of a double.
        on_click=None if node.kind == SPEC else (lambda n=node: _open(n)),
        pick=Path(node.rel).stem if node.kind == SPEC else None,
    )
    # A file the copilot's run just produced — a compiled model, a written
    # output — lands in the tree visibly; a tree rebuilt to move a highlight
    # keeps every key and keeps still (`c.enters`).
    c.enters(row, f"tree:{node.rel}")


def _meta(node: tree.Node) -> str:
    """The one number a row carries, from the engine — never counted here."""
    if node.kind == SOURCE:
        entry = APP.sources.get(node.ident) or {}
        return c.count(len(entry.get("columns") or []), "col")
    if node.kind == SPEC:
        steps = engine.count_steps(APP.root / node.rel)
        return "" if steps is None else c.count(steps, "step")
    return ""


def _note(node: tree.Node, stale: set[str]) -> str:
    """A fact about the row, in the engine's terms and in no colour at all."""
    if node.kind == SOURCE:
        entry = APP.sources.get(node.ident) or {}
        return "" if catalog.is_interpreted(entry) else "uninterpreted"
    if node.kind == SPEC and Path(node.name).stem in stale:
        return STALE_SPEC_NOTE
    if node.kind == MODEL and Path(node.name).stem in stale:
        return STALE_MODEL_NOTE
    if node.kind == UNINDEXED:
        return UNINDEXED_NOTE
    return ""


def _add_data_affordance() -> None:
    """Row-height, at the foot of the pane, once a project has sources.

    Only opens the dialog; the dialog itself is built once with the page. See
    `screens.build_add_dialog` for why it cannot be built from in here.
    """
    from portia.ui import screens

    c.rule()
    with ui.element("div").classes("p-2"):
        c.button("Add data", screens.open_add_dialog, icon="add", micro=True).classes("w-full")


# --- selection --------------------------------------------------------------


def _toggle(rel: str, depth: int) -> None:
    """Open or shut a folder. The tree is the only thing that changes."""
    APP.toggle_folder(rel, depth)
    pane.refresh()


def pick_spec(name: str, *, reveal: bool) -> None:
    """A click on a spec row, with the gesture already resolved by the client.

    ``name`` is the **model name** — the spec's filename without its suffix —
    which is what the row carries (`components.artifact_row`'s ``pick``). A name
    rather than a path, because names are unique across a project by construction
    (`spec.discover_specs` enforces it, and it is the whole of how a cross-spec
    reference resolves) and a name survives an HTML attribute unambiguously.

    A name nothing produces is ignored rather than raising: the row was rendered
    from a tree that may be a moment behind the disk, and a spec the copilot has
    just moved is not an error for a click to report.
    """
    path = engine.spec_path_for(APP, name)
    if path is not None and path.exists():
        _pick_spec(path, reveal=reveal)


def _open(node: tree.Node) -> None:
    if node.kind == SPEC:
        _open_spec(APP.root / node.rel)
    else:
        _select(node.kind, node.ident)


def _select(kind: str, name: str) -> None:
    from portia.ui import workflow

    APP.select(kind, name)
    # The preview belongs to "I am looking at this spec". Looking at a file, a run
    # or a chat is looking at something else, so the ghosts go with it.
    APP.previewing = None
    pane.refresh()
    workflow.pane.refresh()


def _open_spec(path: Path) -> None:
    """Pick a spec: **highlight it, and say what is missing**. One click.

    The middle pane draws the whole project, so picking a spec here navigates the
    canvas rather than replacing it. What that navigation *does* changed on
    2026-08-16, because one gesture was doing three things at once: it used to
    open the card, pan the canvas to it and select it, whether or not you wanted
    any of that — so clicking down a list of specs to find one reshuffled the view
    on every row.

    So a single click is the **light** action and it is the same in both cases:
    select the spec, which lights its whole upstream path. What differs is what
    the canvas is already drawing. If it has everything this spec needs, that is
    all that happens — no unfolding, no panning. If it does not, the tables it is
    missing are drawn as **ghosts**, which answers *what am I not looking at*
    without changing what is on the canvas; clicking any of them puts them there.

    **Which gesture this was is decided on the client** (`assets/pick.js`), and
    it has to be. The browser cannot tell us: the first press refreshes this
    pane, which replaces the row, so the two presses land on two DOM nodes and no
    `dblclick` is ever dispatched. Nor can the server — and that is the sharper
    finding. Acting on the first press moves the rows under a stationary cursor,
    and two presses 140ms apart at one screen position were measured hitting
    `stg_orders.yaml` and then `staging`, its neighbour. By the time the second
    event reaches Python it is already about the wrong spec, whatever the clock
    says. So nothing is sent until the gesture is known.
    """
    _pick_spec(path, reveal=False)


def _reveal_spec(path: Path) -> None:
    """The heavy action: draw it, open it, and glide the canvas to it.

    Everything the single click deliberately withholds. Reached by a double click
    (`assets/pick.js`) and by clicking a ghost, which is the other way of saying
    *put this on the canvas properly*.
    """
    _pick_spec(path, reveal=True)


def _pick_spec(path: Path, *, reveal: bool) -> None:
    from portia.ui import app as app_module
    from portia.ui import workflow

    name = path.stem
    engine.select_spec(path, APP)
    APP.select(SPEC, path.name)

    docs = engine.project_docs(APP)
    drawn = graph_module.drawn_models(docs, APP.visible)
    missing = graph_module.ancestry(docs, [name]) - drawn if name in docs else set()

    if reveal:
        if missing:
            chosen = set(APP.visible) if APP.visible is not None else set(docs)
            APP.visible = frozenset(chosen | graph_module.ancestry(docs, [name]))
            engine.remember_view(APP.root, APP.visible)
        APP.previewing = None
        APP.expanded = APP.expanded | {name}
        APP.focus(name)
    else:
        # Ghosts only when there is something to ghost, so a click on a spec the
        # canvas already draws is a pure highlight.
        APP.previewing = name if missing else None

    pane.refresh()
    workflow.pane.refresh()
    app_module.run_controls.refresh()

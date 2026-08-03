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
2. **Import external data**, folded away until it is wanted. Finder, or a typed
   path or glob for a machine with no chooser. Its destination defaults to the
   folder chosen in (1), and to ``data/`` if nothing was chosen, because a file
   arriving somewhere other than where the rest of the data lives is a folder
   layout nobody asked for.

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
one that read "Plan import" did not index while the one that read "Continue" did.
When it finishes, the primary action **becomes the way into the workspace**, and
not before: a CTA offered beside unfinished work is a skip button wearing a
different word.

Indexing is deliberately shown as **two** things, because one is free and one is
not: profiling is deterministic and always happens, and interpretation is a model
turn that runs through the ordinary transcript with its own write confirmations.
Never one merged spinner.

**The browser drop zone is gone** (2026-08-02). It was a third route that did the
same job as the other two while being the only one that streamed the file through
the browser — which meant a silent refusal on files the browser dislikes, a red
triangle on all twenty when one was rejected, and a copy that had already
happened by the time the plan could have been shown. Every path it served is
served by the folder picker (already in the repo) or the importer (not yet).
"""

from __future__ import annotations

from pathlib import Path

from nicegui import ui

from portia.agent import prompts
from portia.ui import components as c
from portia.ui import engine, state
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
        ui.notify("type a path first")
        return
    try:
        engine.open_project(raw.strip(), APP)
    except OSError as exc:
        ui.notify(f"{type(exc).__name__}: {exc}")
        return
    APP.opened = True
    _pick_up_spec()
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
    yields generic judgment (`PLAN.md`). The guidance shows the *shape* of a good
    brief — never an example that could be mistaken for an answer about the data
    at hand.
    """
    with ui.element("div").classes("p-centered"):
        with ui.element("div").classes("p-centered-column"):
            ui.label("What is this project?").classes("t-heading-md")
            c.text(CONTEXT_WHY, color="c-mute")

            box = (
                ui.textarea(placeholder=CONTEXT_PLACEHOLDER)
                .classes("p-field p-editor w-full")
                .props("borderless autofocus")
                .style("min-height:180px")
            )
            box.bind_value(APP, "goal")  # reused as scratch until it is saved

            with ui.element("div").classes("stack-xs"):
                for line in CONTEXT_SHAPE:
                    c.caption(line, color="c-stone")

            with ui.element("div").classes("row-gap-sm"):
                c.button("Continue", lambda: _save_context(box.value), kind="primary")
                # The gate has no skip, but it must have a way back: choosing the
                # wrong folder is easy, and the only other exit was the process.
                c.button("Back", _back_to_picker, kind="secondary")
                c.caption(str(APP.catalog_dir / "project.yaml"))


def _back_to_picker() -> None:
    """Return to the project picker without writing anything.

    A directory the picker created on the way in is left where it is — an empty
    folder is cheap, and deleting one on a cancel is the kind of helpfulness
    nobody asked for.
    """
    from portia.ui import app as app_module

    APP.opened = False
    APP.goal = ""
    app_module.shell.refresh()


def _save_context(text: str) -> None:
    from portia.ui import app as app_module

    if not (text or "").strip():
        ui.notify("the brief cannot be empty")
        return
    engine.set_context(text, APP)
    APP.goal = ""
    app_module.shell.refresh()


# --- add data ---------------------------------------------------------------


def add_data() -> None:
    """The screen: a heading, both routes, and the action pinned to the bottom.

    The column scrolls its *content* and pins the actions. With thirty files
    listed the whole thing used to be taller than the window: the heading was
    clipped off the top and the button that takes you to the project fell off the
    bottom, which reads as "nothing happened".
    """
    with ui.element("div").classes("p-centered"):
        with ui.element("div").classes("p-centered-column add-data"):
            panel()


@ui.refreshable
def panel(*, in_dialog: bool = False) -> None:
    """Everything on the add-data surface, in one refreshable.

    **Both the screen and the dialog draw this**, with `in_dialog` deciding only
    what the way out is called — from the screen it opens the workspace, from the
    dialog the workspace is already behind you and it just closes. The two used
    to be separately-assembled stacks of the same four components, which is how
    the dialog ended up without the destination field for a week.
    """
    with ui.element("div").classes("add-data-body"):
        with ui.element("div").classes("add-data-head"):
            ui.label("Add data").classes("t-heading-md")
            ui.label(ADD_WHY.format(formats=_formats())).classes("add-data-sub")
        _in_repo()
        _from_outside()
        _interpret_toggle()
        _progress()
    with ui.element("div").classes("add-data-actions"):
        c.rule()
        _actions(in_dialog=in_dialog)


def _refresh() -> None:
    """Redraw the surface. Both instances; only one of them is ever on screen."""
    panel.refresh()


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
    with ui.element("div").classes("picker"):
        _crumbs()
        choices = engine.folder_choices(APP, APP.browse_at)
        for choice in choices:
            _folder_row(choice)
        if not choices:
            with ui.element("div").classes("picker-empty"):
                c.caption(NO_SUBFOLDERS)
    here = len(engine.data_files_in(APP, APP.browse_at))
    if not here and not engine.folder_choices(APP, APP.browse_at):
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
    """One folder you can open: name, how much data is under it, and a chevron."""
    row = ui.element("div").classes("picker-row")
    with row:
        ui.icon("folder").classes("picker-row-icon")
        ui.label(choice.name).classes("picker-row-name")
        ui.label(c.count(choice.files, "file")).classes("picker-row-meta")
        ui.icon("chevron_right").classes("picker-row-go")
    row.on("click", lambda rel=choice.rel: _browse_to(rel))


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

    A file already in the catalog arrives un-ticked and says so. Re-profiling is
    idempotent (`catalog` → the update rule preserves prose and roles), so it is
    not *wrong* to run it again; it is a minute of work on real extracts that
    nobody asked for.
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
    with ui.element("div").classes("pick-head"):
        ui.label(PICK_WHICH).classes("pick-head-label")
        c.button("All", lambda: _tick_all(files, True), micro=True)
        c.button("None", lambda: _tick_all(files, False), micro=True)
    indexed = _indexed_paths()
    with ui.element("div").classes("pick-list"):
        for path in files:
            _pick_row(path, indexed)


def _pick_row(path: Path, indexed: set[str]) -> None:
    """One file to profile or skip.

    **The path is the checkbox's own label**, so the whole row is one hit target
    rather than a 15px box beside some text you cannot click. That is also why it
    is not a row-with-a-click-handler: the handler and the box would both fire on
    the box and cancel each other out.
    """
    rel = _rel(path).as_posix()
    with ui.element("div").classes("pick-row"):
        (
            ui.checkbox(
                rel, value=rel not in APP.unpicked, on_change=lambda e, r=rel: _tick(r, e.value)
            )
            .classes("p-check")
            .props("dense")
        )
        if rel in indexed:
            ui.label(INDEXED_NOTE).classes("pick-row-note")


def _indexed_paths() -> set[str]:
    """Which files already have a catalog entry, by their recorded path."""
    return {str(entry.get("source") or "") for entry in APP.sources.values()}


def _ticked() -> list[Path]:
    """The files this screen would profile — what is under the folder, less what
    was un-ticked. Recomputed from disk rather than remembered, so a file
    imported into the middle of the folder is included without a second click."""
    if not APP.data_dir:
        return []
    files = engine.data_files_in(APP, APP.data_dir)
    return [p for p in files if _rel(p).as_posix() not in APP.unpicked]


def _tick(rel: str, on: bool) -> None:
    """One checkbox. Only the actions are redrawn — the row already shows itself,
    and rebuilding the list under the pointer that just clicked it is churn."""
    APP.tick(rel, on)
    _actions.refresh()


def _tick_all(files: list[Path], on: bool) -> None:
    APP.tick_all({_rel(p).as_posix() for p in files}, on)
    _refresh()


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
    """Choose it, then see the plan. Nothing is copied here.

    `PIPELINE.md` §2.7 makes bringing outside data in a deliberate step — you
    choose where it lands, portia states exactly what it is about to copy and to
    where, and only then does it copy. This half is the choosing; `_import_plan`
    is the stating and the Index button is the doing.

    The typed field is not a fallback nobody uses: the chooser is the OS's and
    exists on macOS only, and a directory or a glob is a thing a chooser cannot
    say at all.
    """
    with ui.element("div").classes("row-gap-sm w-full"):
        if engine.can_browse():
            c.button("Browse…", _choose_to_import, icon="folder_open", micro=True)
        path = (
            ui.input(placeholder=PATH_PLACEHOLDER)
            .classes("p-field p-field-mono flex-1")
            .props("borderless")
        )
        c.button("Plan import", lambda: _plan_import(path.value), micro=True)


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
                ui.input(placeholder=DATA_DIR)
                .classes("p-field p-field-mono w-full")
                .props("borderless")
                .bind_value(APP, "import_destination")
            )
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
    chosen = await engine.browse_for_files()
    if chosen:
        _plan_import("\n".join(str(p) for p in chosen))


def _plan_import(raw: str) -> None:
    """Work out the plan and show it. Writes nothing."""
    APP.import_plan, APP.import_error = [], ""
    targets = [line.strip() for line in (raw or "").splitlines() if line.strip()]
    if not targets:
        APP.import_error = NOTHING_CHOSEN
    else:
        try:
            pairs = []
            for target in targets:
                pairs += engine.plan_import(target, APP.import_dir(DATA_DIR), APP)
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
            .on_value_change(_refresh)
        )
        if APP.interpret:
            with ui.element("div").classes("cost-controls"):
                c.model_effort(APP, _set_indexing_effort)
        ui.label(INTERPRET_COST).classes("add-section-hint")


def _set_indexing_effort(effort: str) -> None:
    APP.effort = effort
    _refresh()


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
    outstanding = len(_ticked()) + len(APP.import_plan)
    with ui.element("div").classes("row-gap-sm"):
        if outstanding:
            c.button(
                f"Index {c.count(outstanding, 'file')}",
                lambda: _index_now(in_dialog=in_dialog),
                kind="primary",
                icon="bolt",
            )
            if APP.sources or in_dialog:
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
            c.button("Back", _back_to_picker, kind="secondary")
    c.caption(_action_note(outstanding))


def _leave_label(in_dialog: bool) -> str:
    return "Close" if in_dialog else "Open the workspace"


def _action_note(outstanding: int) -> str:
    """What the button is about to do, or what the last one did.

    It says whether a model turn is coming, because the turn is deferred to the
    workspace and a cost you pay after leaving a screen is a cost that screen
    still has to name.
    """
    if outstanding:
        planned = len(APP.import_plan)
        parts = []
        if planned:
            parts.append(
                COPY_PART.format(n=c.count(planned, "file"), where=APP.import_dir(DATA_DIR))
            )
        parts.append(PROFILE_PART.format(n=c.count(len(_ticked()), "file")))
        return " · ".join(parts)
    if APP.sources and APP.interpret and APP.pending_interpret:
        return READS_NEXT.format(n=c.count(len(APP.pending_interpret), "source"))
    if APP.indexed is not None:
        return PROFILED.format(n=c.count(APP.indexed, "source"))
    if APP.sources:
        return ADD_MORE_LATER
    return SKIP_HINT


async def _index_now(*, in_dialog: bool = False) -> None:
    """Copy what was planned, then profile everything ticked. One button, in order.

    The copy is first because its results join the profiling — importing three
    files and then having to tick them in a list that has just rebuilt is the
    kind of second step this screen was rewritten to remove.
    """
    copied: list[Path] = []
    if APP.import_plan:
        pairs, APP.import_plan = APP.import_plan, []
        copied = await engine.import_files(pairs, APP)
        ui.notify(f"copied {c.count(len(copied), 'file')} into {APP.import_dir(DATA_DIR)}/")
    # Deduplicated by path: an import into the data folder lands in a place the
    # ticked list was read from a moment ago, and profiling it twice would be a
    # minute of work for one entry.
    seen, paths = set(), []
    for path in [*_ticked(), *copied]:
        resolved = path.resolve()
        if resolved not in seen:
            seen.add(resolved)
            paths.append(path)
    await _index_and_interpret(paths, in_dialog=in_dialog)


async def _index_and_interpret(paths: list[Path], *, in_dialog: bool = False) -> None:
    """Profile first — free, deterministic, always. Then, optionally, a turn.

    From the **dialog** the workspace is already open, so the interpretation turn
    can run immediately and be watched. From the **screen** it waits for the way
    out, because this surface has no transcript and paying for a turn you cannot
    see is how the first version of this spent money on a blank page.
    """
    from portia.ui import app as app_module
    from portia.ui import artifacts

    if not paths:
        return
    unsupported = [p for p in paths if not _is_supported(p)]
    for path in unsupported:
        ui.notify(f"can't read {path.name} — {_formats_sentence()}")
    paths = [p for p in paths if p not in unsupported]
    if not paths:
        return

    def say(done: int, total: int, name: str) -> None:
        APP.indexing_status = f"Profiling {name} — {done + 1} of {total}"
        _progress.refresh()

    APP.indexing_status = f"Reading {c.count(len(paths), 'file')}…"
    _progress.refresh()
    try:
        names = await engine.index(paths, APP, on_progress=say)
    finally:
        APP.indexing_status = ""
        _progress.refresh()

    ui.notify(f"profiled {c.count(len(names), 'source')}")
    APP.pending_interpret = [*APP.pending_interpret, *names]
    APP.indexed = len(names)
    # Profiled is done: it drops out of the outstanding list, which is what turns
    # the primary action into the way out. Same rule as an already-indexed file
    # arriving un-ticked, applied a moment later.
    APP.unpicked = APP.unpicked | {_rel(p).as_posix() for p in paths}

    if in_dialog:
        _close_dialog()
        app_module.shell.refresh()
        artifacts.pane.refresh()
        await _interpret_pending()
        return
    _refresh()


async def _leave(in_dialog: bool) -> None:
    """Out of this surface. From the screen that means into the workspace.

    The interpretation turn fires *here*, after the workspace is up, rather than
    on this screen: the add-data surface has no transcript, and the first version
    of this paid for a turn you then watched on a blank page. It lands in the
    Indexing tab, where it is legible.
    """
    from portia.ui import app as app_module

    if in_dialog:
        _close_dialog()
        return
    APP.left_add_data = True
    app_module.shell.refresh()
    await _interpret_pending()


async def _interpret_pending() -> None:
    """Spend the turn that reads what each source *is*, if one was asked for."""
    from portia.ui import turn

    names, APP.pending_interpret = APP.pending_interpret, []
    if not (APP.interpret and names):
        return
    await turn.start(
        prompts.task("index_batch", names=", ".join(repr(n) for n in names)),
        model=APP.model or _default_model(),
        effort=APP.effort,
        kind=state.INDEXING,
        label=", ".join(names),
    )


# --- the same surface, as a dialog ------------------------------------------

#: Quasar sizes a dialog to its content, and a folder list has no natural width —
#: without this it collapses to a few hundred pixels of nothing.
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
        with ui.element("div").classes("write-confirm add-data").style(DIALOG_WIDTH):
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
    from portia.agent.session import DEFAULT_MODEL

    return DEFAULT_MODEL


# --- the words --------------------------------------------------------------

_OPEN_SUBTITLE = "Open a project directory. If it doesn't exist yet, it gets created."
_OPEN_NEW = "Type a path instead"

CONTEXT_WHY = (
    "This is what makes a column's meaning decidable. A generic brief yields generic judgment."
)
CONTEXT_PLACEHOLDER = "Describe the project in your own words…"
CONTEXT_SHAPE = (
    "Say what the business does and what you are trying to produce.",
    "Say what one row means, and which source is authoritative for what.",
    "Say what the result gets used for, and what would make it wrong.",
)

ADD_WHY = "portia reads {formats}. Point it at the data in this repo, and bring in what is outside."
IN_REPO_HEADING = "Data in this repo"
IN_REPO_WHY = "Which folder holds this project's data? Open a folder to look inside it."
NO_SUBFOLDERS = "No folders below this one hold data portia can read."
PICK_WHICH = "Profile these files"
PROJECT_ROOT = "the project root"
USE_ROOT = "Use the whole repo"
CHOSEN_NOTE = "this project's data"
INDEXED_NOTE = "indexed"
PICKER_HINT = "Nothing readable here yet — open a folder, or import data below."
PICKER_COUNT = "{files} under {where}, at any depth."
NOTHING_HERE = "Nothing portia can read here. Open a folder, or import data below."
IMPORT_HEADING = "Import external data"
IMPORT_WHY = "A file, a folder or a glob from anywhere on disk. It is copied in, never moved."
DESTINATION_DEFAULT = "Put it in {where}"
DESTINATION_SCOPE = "inside the project — data lives in the repo"
PATH_PLACEHOLDER = "a file, directory or glob anywhere on disk"
PLAN_HEADING = "About to copy {n}:"
IMPORT_COPY_ONLY = "A copy. The originals are not moved, deleted or changed."
NOTHING_CHOSEN = "Nothing chosen yet — pick some files, or type a path."
INTERPRET_COST = "Profiling is free and always happens. This costs a model turn."
COPY_PART = "copies {n} into {where}/"
PROFILE_PART = "profiles {n}"
PROFILED = "Profiled {n}. Everything here is indexed."
READS_NEXT = "the copilot reads {n} once the workspace is open"
ADD_MORE_LATER = "you can add more later from the left pane"
SKIP_HINT = "you can add data later from the left pane"
NO_DIALOG = "The add-data panel didn't load — reload the page."
STALE_PANEL = "Add data may be showing stale values ({why}) — reload the page to be sure."

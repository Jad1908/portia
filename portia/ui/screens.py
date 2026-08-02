"""The screens before the three panes — and the one gate in the app.

These exist so a test run never needs a terminal (docs/VISION.md → "The
no-terminal audit"): creating the project, writing its brief, and adding the
data files are all in the window, or the bar is not met.

Three states, in order:

- **No project open.** A path field and Open, plus the recent projects. A path
  that doesn't exist yet is *created* — testing means a fresh directory per run,
  so that has to be one action rather than an error followed by a second one.
- **No context set.** The mandatory brief. No skip, no dismiss, no "later".
- **No sources.** A drop zone, a destination, and the interpret toggle.

**Bringing outside data in is a deliberate step** (`docs/PIPELINE.md` §2.7).
``index`` only accepts files already inside the repo, so this screen is the way
one gets in: you choose where it lands, portia states exactly what it is about to
copy and to where, and only then does it copy. The plan is shown in full rather
than summarised — "3 files into data/" describes a plan, the list *is* one, and
the difference is the one time a wrong folder or a name collision is cheap to
notice. It is a copy, never a move.

Indexing is deliberately shown as **two** things, because one is free and one is
not: profiling is deterministic and always happens, and interpretation is a model
turn that runs through the ordinary transcript with its own write confirmations.
Never one merged spinner.
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


# --- source-dropzone --------------------------------------------------------


def first_sources() -> None:
    """Add data, then move on — and the action to move on is always on screen.

    The column scrolls its *content* and pins the actions to the bottom. With
    twenty files added the whole thing used to be 755px of column in a 727px
    window: the heading was clipped off the top and the button that takes you to
    the project went off the bottom, which reads as "nothing happened".
    """
    with ui.element("div").classes("p-centered"):
        with ui.element("div").classes("p-centered-column add-data"):
            with ui.element("div").classes("add-data-body"):
                ui.label("Add data").classes("t-heading-md")
                c.text(f"Drop {_formats()} in. {_SOURCES_WHY}", color="c-mute")
                dropzone()
                _progress()
                _added_so_far()
            with ui.element("div").classes("add-data-actions"):
                c.rule()
                with ui.element("div").classes("row-gap-sm"):
                    if APP.sources:
                        # Leaving is a decision, and it is the one that starts the
                        # copilot reading — see `_leave_add_data`.
                        c.button(_continue_label(), _leave_add_data, kind="primary")
                        c.caption(_continue_hint())
                    else:
                        # Unlike the brief, this is not a gate: an empty project is a
                        # legitimate place to stand, and "Add data" waits in the left pane.
                        c.button("Skip for now", _skip_sources, kind="secondary")
                        c.caption(_SKIP_HINT)
                    c.button("Back", _back_to_picker, kind="secondary")


@ui.refreshable
def _progress() -> None:
    """What indexing is doing, in words, while it does it.

    Its own refreshable so it can be redrawn between files without rebuilding
    the drop box underneath it — refreshing the whole screen mid-upload would
    take the uploader with it.
    """
    if not APP.indexing_status:
        return
    with ui.element("div").classes("row-gap-sm indexing-status"):
        ui.spinner(size="sm")
        c.text(APP.indexing_status, color="c-mute")


def _continue_label() -> str:
    """The CTA says what it will do, including whether it spends money."""
    return "Continue — and read them" if APP.interpret else "Continue to the project"


def _continue_hint() -> str:
    if APP.interpret:
        return f"the copilot reads {c.count(len(APP.sources), 'source')} in the next screen"
    return "you can add more later from the left pane"


def _added_so_far() -> None:
    """What has landed, on the screen that landed it.

    Without this the only feedback was a toast that names twenty files and then
    goes away. The list is capped and scrolls: it is a receipt, not the pane.
    """
    if not APP.sources:
        return
    c.section_header(f"Added · {c.count(len(APP.sources), 'source')}")
    with ui.element("div").classes("added-list"):
        for name, entry in APP.sources.items():
            with ui.element("div").classes("added-row"):
                ui.icon("table_chart").classes("fact-icon")
                c.mono(name, small=True)
                c.caption(c.count(len(entry.get("columns") or []), "col"))


async def _leave_add_data() -> None:
    """Go to the project — and start the copilot reading, now that it can be seen.

    The interpretation turn used to fire on this screen, which has no transcript:
    you paid for a turn and watched a blank page. It runs here instead, after the
    workspace is up, so it lands in the Indexing tab where it is legible.
    """
    from portia.ui import app as app_module

    APP.left_add_data = True
    app_module.shell.refresh()
    await _interpret_pending()


def _skip_sources() -> None:
    from portia.ui import app as app_module

    APP.skipped_sources = True
    app_module.shell.refresh()


#: Quasar sizes a dialog to its content, and a drop box has no natural width —
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
        with ui.element("div").classes("write-confirm").style(DIALOG_WIDTH):
            ui.label("Add data").classes("t-heading-md")
            c.text(f"Drop {_formats()} in. {_SOURCES_WHY}", color="c-mute")
            dropzone(on_done=dialog.close)
            _progress()
            with ui.element("div").classes("row-gap-sm"):
                c.button("Close", dialog.close, kind="secondary")
    _ADD_DIALOG = dialog


def open_add_dialog() -> None:
    """Show it. Says so if it isn't there, rather than doing nothing quietly."""
    _show(_ADD_DIALOG)


def _show(dialog: ui.dialog | None) -> None:
    if dialog is None or dialog.is_deleted:
        ui.notify(_NO_DIALOG)
        return
    dialog.open()


#: Open the file picker from anywhere on the drop box, not only from Quasar's
#: `+`. A dashed box that says "click to pick" has to be clickable, all of it.
#: Runs client-side, so a click on the native button isn't handled twice.
_PICK_ON_CLICK = (
    "(e) => {{ if (e.target.closest('.q-btn, input')) return; "
    "getHtmlElement({id}).querySelector('input[type=file]').click(); }}"
)


def dropzone(*, on_done=None) -> None:
    with ui.element("div").classes("stack-md w-full"):
        with ui.element("div").classes("dropzone w-full"):
            upload = ui.upload(
                multiple=True,
                auto_upload=True,
                on_multi_upload=lambda e: _dropped(e.files, on_done),
                on_rejected=lambda _: _rejected(),
                label=_DROP_LABEL,
            ).props("flat")
            upload.on("click", js_handler=_PICK_ON_CLICK.format(id=upload.id))

        _destination_field()
        _import_field(on_done)
        _import_plan(on_done)
        _interpret_toggle()


def _destination_field() -> None:
    """Where in the project what you add will land.

    **The one decision §2.7 added, and it belongs to the operator.** Data lives in
    the repo, and portia's job is to say plainly where it is about to put a copy
    rather than to pick a folder on your behalf and mention it afterwards. It
    governs both routes — a browser drop and an import from disk — because "where
    does this go" should not depend on how the file got here.
    """
    with ui.element("div").classes("row-gap-sm w-full"):
        c.caption("Destination")
        (
            ui.input(placeholder=DATA_DIR)
            .classes("p-field p-field-mono flex-1")
            .props("borderless")
            .bind_value(APP, "import_destination")
        )
        c.caption(_DESTINATION_SCOPE)


def _import_field(on_done) -> None:
    """The route for data that is outside the repo: choose it, then see the plan.

    Nothing is copied here. `PIPELINE.md` §2.7 makes bringing outside data in a
    deliberate step — you choose where it lands, portia states exactly what it is
    about to copy and to where, and only then does it copy. This half is the
    choosing; `_import_plan` is the stating.
    """
    with ui.element("div").classes("row-gap-sm w-full"):
        if engine.can_browse():
            c.button("Choose files…", lambda: _choose_to_import(), icon="folder_open", micro=True)
        path = (
            ui.input(placeholder=_PATH_PLACEHOLDER)
            .classes("p-field p-field-mono flex-1")
            .props("borderless")
        )
        c.button("Plan import", lambda: _plan_import(path.value), micro=True)


@ui.refreshable
def _import_plan(on_done=None) -> None:
    """What is about to be copied, where to, and the two ways out of it.

    Every pair is listed rather than summarised. "3 files into data/" is a
    description of a plan; this is the plan, and the difference is the one time
    a name collision or a wrong folder is cheap to notice.
    """
    if APP.import_error:
        with ui.element("div").classes("write-confirm"):
            c.text(APP.import_error, color="c-error")
            c.button("OK", _clear_import, kind="secondary", micro=True)
        return
    if not APP.import_plan:
        return
    with ui.element("div").classes("write-confirm"):
        ui.label(_IMPORT_HEADING.format(n=c.count(len(APP.import_plan), "file"))).classes(
            "t-body-strong c-ink"
        )
        c.caption(_IMPORT_COPY_ONLY)
        with ui.element("div").classes("added-list"):
            for src, dst in APP.import_plan:
                with ui.element("div").classes("added-row"):
                    c.mono(str(src), small=True)
                    ui.icon("arrow_forward").classes("fact-icon")
                    c.mono(str(_relative(dst)), small=True)
        with ui.element("div").classes("row-gap-sm"):
            c.button("Copy and index", lambda: _do_import(on_done), kind="primary")
            c.button("Cancel", _clear_import, kind="secondary")


def _relative(path: Path) -> Path:
    """A destination as the project sees it — the repo-relative path it will have."""
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
        APP.import_error = _NOTHING_CHOSEN
        _import_plan.refresh()
        return
    try:
        pairs = []
        for target in targets:
            pairs += engine.plan_import(target, APP.import_destination, APP)
    except ValueError as exc:
        APP.import_error = str(exc)
    else:
        APP.import_plan = pairs
    _import_plan.refresh()


def _clear_import() -> None:
    APP.import_plan, APP.import_error = [], ""
    _import_plan.refresh()


async def _do_import(on_done) -> None:
    """Copy what the plan said, then index it. The originals are left alone.

    **The panel is redrawn last, and that is load-bearing.** `_import_plan` builds
    the button this is the handler for, so refreshing it here deletes the slot the
    handler is running in — every `ui.notify` after that point raises, and the
    first version did it before the copy's own notification. The files landed and
    the indexing never ran, which is worse than either failing alone.
    """
    pairs, APP.import_plan = APP.import_plan, []
    if not pairs:
        return
    copied = await engine.import_files(pairs, APP)
    ui.notify(f"copied {c.count(len(copied), 'file')} into {APP.import_destination}/")
    await _index_and_interpret(copied, on_done)
    _import_plan.refresh()


def _interpret_toggle() -> None:
    """Profiling is free; interpretation is a model turn. Never blur the two.

    The model and effort belong here rather than only in the Copilot pane: this
    is where that turn is actually bought, and reading twenty sources is a
    different-sized job from answering one question. Same two controls, same
    bound state — picking here is picking for the copilot too, which is why they
    are not a second setting.
    """
    with ui.element("div").classes("stack-xs"):
        (
            ui.switch("Have the copilot read what each source is")
            .classes("p-toggle")
            .bind_value(APP, "interpret")
            # The model controls appear with the cost they belong to, so turning
            # this off has to take them away rather than leave a dead setting.
            .on_value_change(_refresh_shell)
        )
        if APP.interpret:
            c.model_effort(APP, _set_indexing_effort)
        c.caption(_INTERPRET_COST)


def _set_indexing_effort(effort: str) -> None:
    APP.effort = effort
    _refresh_shell()


def _refresh_shell() -> None:
    from portia.ui import app as app_module

    app_module.shell.refresh()


async def _dropped(files, on_done) -> None:
    """A browser drop. It lands at the chosen destination, like an import does.

    No plan step: the browser has already handed us the bytes, so there is nothing
    left to confirm — the copy the operator would be agreeing to has happened. The
    destination still applies, because where a file goes should not depend on how
    it arrived.
    """
    paths = []
    for upload in files:
        paths.append(await engine.store_upload(upload, APP, APP.import_destination))
    await _index_and_interpret(paths, on_done)


def _rejected() -> None:
    """A refused file has to say so.

    The browser marks every file in the batch with a red triangle and explains
    nothing — and because the drop is one request, one refusal reddens all
    twenty. Whatever the reason, it belongs on screen rather than in a colour.
    """
    ui.notify(_UPLOAD_REJECTED)


async def _index_and_interpret(paths: list[Path], on_done) -> None:
    """Profile first — free, deterministic, always. Then, optionally, a turn.

    ``on_done`` is the dialog's close. Its presence is also what tells us where
    we are: from the dialog the workspace is already open, so the turn can run
    immediately and be watched; from the add-data screen it waits for Continue.
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

    from_dialog = on_done is not None
    if from_dialog:
        on_done()
    app_module.shell.refresh()
    artifacts.pane.refresh()

    # From the dialog the workspace is already on screen, so the turn is visible
    # and can start now. From the add-data screen it waits for Continue.
    if from_dialog:
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
_SOURCES_WHY = "They are copied into the project and profiled straight away."
_UPLOAD_REJECTED = (
    'the browser refused those files — add them with "Add from a path already on disk" instead, '
    "which copies them without sending them through the browser"
)
_CONTINUE_HINT = "you can add more later from the left pane"
_SKIP_HINT = "you can add data later from the left pane"
_DROP_LABEL = "Drop files here, or click to pick"
_PATH_PLACEHOLDER = "a file, directory or glob anywhere on disk"
_DESTINATION_SCOPE = "inside the project — data lives in the repo"
_IMPORT_HEADING = "About to copy {n}:"
_IMPORT_COPY_ONLY = "A copy. The originals are not moved, deleted or changed."
_NOTHING_CHOSEN = "Nothing chosen yet — pick some files, or type a path."
_INTERPRET_COST = "Profiling is free and always happens. This costs a model turn."
_NO_DIALOG = "The add-data panel didn't load — reload the page."

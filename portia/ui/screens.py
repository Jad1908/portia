"""The screens before the three panes — and the one gate in the app.

These exist so a test run never needs a terminal (docs/VISION.md → "The
no-terminal audit"): creating the project, writing its brief, and adding the
CSVs are all in the window, or the bar is not met.

Three states, in order:

- **No project open.** A path field and Open, plus the recent projects. A path
  that doesn't exist yet is *created* — testing means a fresh directory per run,
  so that has to be one action rather than an error followed by a second one.
- **No context set.** The mandatory brief. No skip, no dismiss, no "later".
- **No sources.** A drop zone, and the interpret toggle beside it.

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
    app_module.shell.refresh()


def _pick_up_spec() -> None:
    """Open the project's first spec, if it already has one."""
    specs = engine.specs_in(APP)
    if specs:
        engine.select_spec(specs[0], APP)


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
            c.text(_CONTEXT_WHY, color="c-mute")

            box = (
                ui.textarea(placeholder=_CONTEXT_PLACEHOLDER)
                .classes("p-field p-editor w-full")
                .props("borderless autofocus")
                .style("min-height:180px")
            )
            box.bind_value(APP, "goal")  # reused as scratch until it is saved

            with ui.element("div").classes("stack-xs"):
                for line in _CONTEXT_SHAPE:
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
    with ui.element("div").classes("p-centered"):
        with ui.element("div").classes("p-centered-column"):
            ui.label("Add data").classes("t-heading-md")
            c.text(_SOURCES_WHY, color="c-mute")
            dropzone()
            _added_so_far()
            c.rule()
            with ui.element("div").classes("row-gap-sm"):
                if APP.sources:
                    # Leaving is a decision now. Adding twenty files used to
                    # move the screen on the instant the first one landed.
                    c.button("Continue", _leave_add_data, kind="primary")
                    c.caption(_CONTINUE_HINT)
                else:
                    # Unlike the brief, this is not a gate: an empty project is a
                    # legitimate place to stand, and "Add data" waits in the left pane.
                    c.button("Skip for now", _skip_sources, kind="secondary")
                    c.caption(_SKIP_HINT)
                c.button("Back", _back_to_picker, kind="secondary")


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


def _leave_add_data() -> None:
    from portia.ui import app as app_module

    APP.left_add_data = True
    app_module.shell.refresh()


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
            dropzone(on_done=dialog.close)
            with ui.element("div").classes("row-gap-sm"):
                c.button("Close", dialog.close, kind="secondary")
    _ADD_DIALOG = dialog


def open_add_dialog() -> None:
    """Show it. Says so if it isn't there, rather than doing nothing quietly."""
    _show(_ADD_DIALOG)


# --- editing the brief ------------------------------------------------------

#: The brief dialog for this page. Same rule as the add dialog: built once, at
#: page level, never inside a refreshable.
_BRIEF_DIALOG: ui.dialog | None = None


def build_brief_dialog() -> None:
    """The project brief, editable after the fact.

    The gate writes it once; nothing else could change it, which made the most
    consequential text in the product the only text you couldn't correct. It
    writes through `catalog.init_project` — the same call the gate makes.
    """
    global _BRIEF_DIALOG
    with ui.dialog().props("transition-duration=0") as dialog:
        with ui.element("div").classes("write-confirm").style(DIALOG_WIDTH):
            ui.label("What is this project?").classes("t-heading-md")
            c.text(_CONTEXT_WHY, color="c-mute")
            box = (
                ui.textarea(placeholder=_CONTEXT_PLACEHOLDER)
                .classes("p-field p-editor w-full")
                .props("borderless")
                .style("min-height:160px")
            )
            dialog.on("show", lambda: box.set_value(APP.project_context))
            with ui.element("div").classes("row-gap-sm"):
                c.button("Save", lambda: _save_brief(box.value, dialog), kind="primary")
                c.button("Cancel", dialog.close, kind="secondary")
    _BRIEF_DIALOG = dialog


def open_brief_dialog() -> None:
    _show(_BRIEF_DIALOG)


def _save_brief(text: str, dialog: ui.dialog) -> None:
    from portia.ui import app as app_module

    if not (text or "").strip():
        ui.notify("the brief cannot be empty")
        return
    engine.set_context(text, APP)
    dialog.close()
    app_module.toolbar.refresh()
    ui.notify("brief saved")


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

        # Same as the project picker: dropping or picking is the way in, and the
        # path field is folded away until someone wants it.
        _add_by_path_field(on_done)
        _interpret_toggle()


def _add_by_path_field(on_done) -> None:
    reveal = ui.element("div").classes("row-gap-sm")
    field = ui.element("div").classes("row-gap-sm w-full")
    field.set_visibility(False)

    with reveal:
        c.button(_ADD_BY_PATH, lambda: _reveal(reveal, field), kind="secondary", micro=True)
    with field:
        path = (
            ui.input(placeholder=_PATH_PLACEHOLDER)
            .classes("p-field p-field-mono flex-1")
            .props("borderless")
        )
        c.button("Add", lambda: _add_by_path(path.value, on_done))


def _interpret_toggle() -> None:
    """Profiling is free; interpretation is a model turn. Never blur the two.

    The model and effort belong here rather than only in the Copilot pane: this
    is where that turn is actually bought, and reading twenty sources is a
    different-sized job from answering one question. Same two controls, same
    bound state — picking here is picking for the copilot too, which is why they
    are not a second setting.
    """
    from portia.agent.session import DEFAULT_MODEL, EFFORTS, MODELS

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
            APP.model = APP.model or DEFAULT_MODEL
            with ui.element("div").classes("row-gap-sm"):
                ui.select(list(MODELS), value=APP.model).props(
                    "borderless dense options-dense new-value-mode=add-unique use-input"
                ).classes("p-field p-field-mono").bind_value(APP, "model")
            c.segmented(EFFORTS, APP.effort, _set_indexing_effort)
        c.caption(_INTERPRET_COST)


def _set_indexing_effort(effort: str) -> None:
    APP.effort = effort
    _refresh_shell()


def _refresh_shell() -> None:
    from portia.ui import app as app_module

    app_module.shell.refresh()


async def _dropped(files, on_done) -> None:
    paths = []
    for upload in files:
        paths.append(await engine.store_upload(upload, APP))
    await _index_and_interpret(paths, on_done)


def _rejected() -> None:
    """A refused file has to say so.

    The browser marks every file in the batch with a red triangle and explains
    nothing — and because the drop is one request, one refusal reddens all
    twenty. Whatever the reason, it belongs on screen rather than in a colour.
    """
    ui.notify(_UPLOAD_REJECTED)


async def _add_by_path(raw: str, on_done) -> None:
    if not (raw or "").strip():
        return
    try:
        found = engine.resolve_data(raw.strip())
    except ValueError as exc:
        ui.notify(str(exc))
        return
    await _index_and_interpret([engine.copy_into_project(p, APP) for p in found], on_done)


async def _index_and_interpret(paths: list[Path], on_done) -> None:
    """Profile first — free, deterministic, always. Then, optionally, a turn."""
    from portia.ui import app as app_module
    from portia.ui import artifacts, turn

    if not paths:
        return
    unsupported = [p for p in paths if not _is_supported(p)]
    for path in unsupported:
        ui.notify(f"can't read {path.name}")
    paths = [p for p in paths if p not in unsupported]
    if not paths:
        return

    names = await engine.index(paths, APP)
    ui.notify(f"profiled {', '.join(names)}")
    if on_done is not None:
        on_done()
    app_module.shell.refresh()
    artifacts.pane.refresh()

    if APP.interpret:
        await turn.start(
            prompts.task("index_batch", names=", ".join(repr(n) for n in names)),
            model=APP.model or _default_model(),
            effort=APP.effort,
            kind=state.INDEXING,
            label=", ".join(names),
        )


def _is_supported(path: Path) -> bool:
    from portia.core.io import supported_suffixes

    return path.suffix.lower() in supported_suffixes()


def _default_model() -> str:
    from portia.agent.session import DEFAULT_MODEL

    return DEFAULT_MODEL


_OPEN_SUBTITLE = "Open a project directory. If it doesn't exist yet, it gets created."
_OPEN_NEW = "Type a path instead"
_ADD_BY_PATH = "Add from a path already on disk"
_CONTEXT_WHY = (
    "This is what makes a column's meaning decidable. A generic brief yields generic judgment."
)
_CONTEXT_PLACEHOLDER = "Describe the project in your own words…"
_CONTEXT_SHAPE = (
    "Say what the business does and what you are trying to produce.",
    "Say what one row means, and which source is authoritative for what.",
    "Say what the result gets used for, and what would make it wrong.",
)
_SOURCES_WHY = "Drop CSVs in. They are copied into the project and profiled straight away."
_UPLOAD_REJECTED = (
    'the browser refused those files — add them with "Add from a path already on disk" instead, '
    "which copies them without sending them through the browser"
)
_CONTINUE_HINT = "you can add more later from the left pane"
_SKIP_HINT = "you can add data later from the left pane"
_DROP_LABEL = "Drop CSVs here, or click to pick"
_PATH_PLACEHOLDER = "…or a path, directory or glob already on disk"
_INTERPRET_COST = "Profiling is free and always happens. This costs a model turn."
_NO_DIALOG = "The add-data panel didn't load — reload the page."

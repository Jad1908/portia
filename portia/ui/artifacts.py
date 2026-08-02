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
- **Turns**, at the foot. A *run* executed a spec and is a markdown file in the
  project; a *turn* was the copilot deciding what the spec should say and is
  JSONL inside ``.portia/`` (`portia/runlog.py`). Same word, two artifacts —
  which is why the run sits in the tree where its file is and the turn does not.

Nothing here ranks. Folders sort before files and both sort by name; no row is
coloured, sized or ordered by anything measured (`DESIGN.md`).
"""

from __future__ import annotations

from pathlib import Path

from nicegui import ui

from portia import catalog
from portia.ui import components as c
from portia.ui import engine, tree
from portia.ui.state import (
    APP,
    BRIEF,
    MODEL,
    OUTPUT,
    RUN,
    SOURCE,
    SPEC,
    TURN,
    UNINDEXED,
)

ICON = {
    SOURCE: "table_chart",
    SPEC: "account_tree",
    MODEL: "code",
    OUTPUT: "description",
    RUN: "history",
    TURN: "forum",
    UNINDEXED: "insert_drive_file",
    tree.FOLDER: "folder",
}

#: The disclosure triangle, which is the whole of "progressive disclosure" as a
#: control: a folder says whether it is open before you click it.
CARET_OPEN = "expand_more"
CARET_SHUT = "chevron_right"
FOLDER_OPEN = "folder_open"

EMPTY_TREE = "Nothing portia can read in this directory yet. Add a file to begin."
TURNS_NOTE = "No copilot turns yet. Type a goal and press Go."
UNINDEXED_NOTE = "not indexed"
STALE_SPEC_NOTE = "its .sql is out of date"
STALE_MODEL_NOTE = "stale — its spec changed"


@ui.refreshable
def pane() -> None:
    """The brief, the tree, then Turns.

    Keyed, because selecting a row rebuilds this pane to move one highlight and
    an unkeyed rebuild would send a long list back to the top each time you
    clicked something near the bottom of it (`components.scroll_area`).
    """
    with c.scroll_area("artifacts"):
        _brief_row()
        _tree()
        _turns()
    _add_data_affordance()


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


# --- the tree ---------------------------------------------------------------


def _tree() -> None:
    nodes = engine.project_tree(APP)
    if not nodes:
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
        icon=FOLDER_OPEN if is_open else ICON[tree.FOLDER],
        caret=CARET_OPEN if is_open else CARET_SHUT,
        depth=depth,
        on_click=lambda rel=node.rel, d=depth: _toggle(rel, d),
    )
    if is_open:
        for child in node.children:
            _node(child, depth + 1, stale)


def _file(node: tree.Node, depth: int, stale: set[str]) -> None:
    c.artifact_row(
        name=node.name,
        icon=ICON.get(node.kind, ICON[UNINDEXED]),
        meta=_meta(node),
        note=_note(node, stale),
        depth=depth,
        selected=APP.is_selected(node.kind, node.ident),
        on_click=lambda n=node: _open(n),
    )


def _meta(node: tree.Node) -> str:
    """The one number a row carries, from the engine — never counted here."""
    if node.kind == SOURCE:
        entry = APP.sources.get(node.ident) or {}
        return c.count(len(entry.get("columns") or []), "col")
    if node.kind == SPEC:
        steps = engine.count_steps(APP.root / node.rel)
        return "" if steps is None else c.count(steps, "step")
    if node.kind == TURN:
        return _turn_meta(APP.root / node.rel)
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


# --- turns ------------------------------------------------------------------


def _turns() -> None:
    """Logged copilot turns — pinned below the tree, because their files are not in it.

    A *run* executed a spec; a *turn* was the copilot deciding what the spec
    should say. Two artifacts, two words: one heading covering both would make
    "run" mean two things in the one place that has to be unambiguous. The run's
    markdown is a file in the project and appears in the tree where it lives;
    a turn is JSONL inside ``.portia/``, which is not walked.

    The model is the meta, because it is the thing you are usually looking for.
    `EVALUATION.md` can only compare two runs when they differ in the model and
    effort and nothing else, so that is the first question asked of this list.
    """
    c.rule()
    c.section_header("Turns")
    turns = engine.turns_in(APP)
    if not turns:
        c.empty_note(TURNS_NOTE)
        return
    for path in turns:
        c.artifact_row(
            name=path.stem,
            icon=ICON[TURN],
            meta=_turn_meta(path),
            selected=APP.is_selected(TURN, path.name),
            on_click=lambda p=path: _select(TURN, p.name),
        )


def _turn_meta(path: Path) -> str:
    """The turn's model, short enough for a 260px pane."""
    header = engine.turn_header(path)
    model = str(header.get("model") or "")
    return model.replace("claude-", "")


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


def _open(node: tree.Node) -> None:
    if node.kind == SPEC:
        _open_spec(APP.root / node.rel)
    else:
        _select(node.kind, node.ident)


def _select(kind: str, name: str) -> None:
    from portia.ui import workflow

    APP.select(kind, name)
    pane.refresh()
    workflow.pane.refresh()


def _open_spec(path: Path) -> None:
    """Open a spec — which means **navigating to it** on the canvas, not replacing it.

    The middle pane draws the whole project, so picking a spec here opens its card
    onto the steps that build it and pans the canvas to it. Swapping the canvas for
    a single spec would throw away the one view where a table and the steps that
    produce it are both on screen.
    """
    from portia.ui import app as app_module
    from portia.ui import workflow

    engine.select_spec(path, APP)
    APP.select(SPEC, path.name)
    APP.expanded = APP.expanded | {path.stem}
    APP.focus(path.stem)
    pane.refresh()
    workflow.pane.refresh()
    app_module.run_controls.refresh()

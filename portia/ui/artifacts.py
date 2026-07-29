"""Left pane — files & artifacts.

`VISION.md` asks how we decide what to surface inside a big repo. V0's answer is
cheap and it is the curation: **a file appears if portia knows about it.**
Sources come from the catalog, not a directory walk; specs are `specs/*.yaml`;
outputs are what a run wrote. Nothing else is shown.

It is not a file tree, and an empty section says so rather than disappearing —
"no specs yet" is information; a missing heading is not.
"""

from __future__ import annotations

from pathlib import Path

from nicegui import ui

from portia import catalog
from portia.ui import components as c
from portia.ui import engine
from portia.ui.state import APP, OUTPUT, RUN, SOURCE, SPEC, TURN

ICON = {
    SOURCE: "table_chart",
    SPEC: "account_tree",
    OUTPUT: "description",
    RUN: "history",
    TURN: "forum",
}

RUNS_NOTE = "No saved runs. Press Run, then Save report."
TURNS_NOTE = "No copilot turns yet. Type a goal and press Go."


@ui.refreshable
def pane() -> None:
    """Sources · Specs · Outputs · Runs · Turns, in that order."""
    with ui.element("div").classes("p-scroll"):
        _sources()
        _specs()
        _outputs()
        _runs()
        _turns()
    _add_data_affordance()


def _sources() -> None:
    c.section_header("Sources")
    if not APP.sources:
        c.empty_note("No data indexed yet. Add a file to begin.")
        return
    for name, entry in APP.sources.items():
        columns = len(entry.get("columns") or [])
        c.artifact_row(
            name=name,
            icon=ICON[SOURCE],
            meta=c.count(columns, "col"),
            note="" if catalog.is_interpreted(entry) else "uninterpreted",
            selected=APP.is_selected(SOURCE, name),
            on_click=lambda n=name: _select(SOURCE, n),
        )


def _specs() -> None:
    c.section_header("Specs")
    specs = engine.specs_in(APP)
    if not specs:
        c.empty_note("No spec yet. The copilot writes one as it records steps.")
        return
    for path in specs:
        steps = engine.count_steps(path)
        c.artifact_row(
            name=path.name,
            icon=ICON[SPEC],
            meta="—" if steps is None else c.count(steps, "step"),
            selected=APP.is_selected(SPEC, path.name),
            on_click=lambda p=path: _open_spec(p),
        )


def _outputs() -> None:
    c.section_header("Outputs")
    outputs = engine.outputs_in(APP)
    if not outputs:
        c.empty_note("Nothing written yet. Run a spec, then write its tables.")
        return
    for path in outputs:
        c.artifact_row(
            name=path.name,
            icon=ICON[OUTPUT],
            selected=APP.is_selected(OUTPUT, path.name),
            on_click=lambda p=path: _select(OUTPUT, p.name),
        )


def _runs() -> None:
    c.section_header("Runs")
    runs = engine.runs_in(APP)
    if not runs:
        c.empty_note(RUNS_NOTE)
        return
    for path in runs:
        c.artifact_row(
            name=path.stem,
            icon=ICON[RUN],
            selected=APP.is_selected(RUN, path.name),
            on_click=lambda p=path: _select(RUN, p.name),
        )


def _turns() -> None:
    """Logged copilot turns — its own section, and its own word.

    A *run* executed a spec; a *turn* was the copilot deciding what the spec
    should say. Two artifacts, two headings: the pane's job is to say what
    portia knows about, and one heading covering both would make "run" mean two
    things in the one place that has to be unambiguous.

    The model is the meta, because it is the thing you are usually looking for.
    `EVALUATION.md` can only compare two runs when they differ in the model and
    effort and nothing else, so that is the first question asked of this list.
    """
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


def _select(kind: str, name: str) -> None:
    from portia.ui import workflow

    APP.select(kind, name)
    pane.refresh()
    workflow.pane.refresh()


def _open_spec(path: Path) -> None:
    from portia.ui import app as app_module
    from portia.ui import workflow

    engine.select_spec(path, APP)
    APP.select(SPEC, path.name)
    pane.refresh()
    workflow.pane.refresh()
    app_module.toolbar.refresh()

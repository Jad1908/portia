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
from portia.ui.state import APP, OUTPUT, RUN, SOURCE, SPEC

ICON = {SOURCE: "table_chart", SPEC: "account_tree", OUTPUT: "description", RUN: "history"}

#: A spec run can be saved (Save report → `runs/*.md`). A **copilot turn** still
#: cannot: the run log is specced but unbuilt (docs/EVALUATION.md), so say which
#: half exists rather than implying every past turn is somewhere to be found.
RUNS_NOTE = "No saved runs. Press Run, then Save report."
TURNS_NOTE = (
    "Copilot turns aren't logged yet — a turn lives in the transcript until the window closes."
)


@ui.refreshable
def pane() -> None:
    """Sources · Specs · Outputs · Runs, in that order."""
    with ui.element("div").classes("p-scroll"):
        _sources()
        _specs()
        _outputs()
        _runs()
    _add_data_affordance()


def _sources() -> None:
    c.section_header("Sources")
    if not APP.sources:
        c.empty_note("No data indexed yet. Add a CSV to begin.")
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
    c.empty_note(TURNS_NOTE)


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

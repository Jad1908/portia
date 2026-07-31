"""Left pane — files & artifacts.

`VISION.md` asks how we decide what to surface inside a big repo. V0's answer is
cheap and it is the curation: **a file appears if portia knows about it.**
Sources come from the catalog, not a directory walk; specs come from
`spec.discover_specs`; models are the compiled `.sql`; outputs are what a run
wrote. Nothing else is shown.

**Models are their own section, and they are the deliverable.** A run's CSV under
`out/` is a result; `models/*.sql` is the pipeline you hand to a data team
(`docs/PIPELINE.md` §2.2). One is something this project produced, the other is
the thing this project *is*, so they are not two rows in one list.

Specs and models are grouped by layer where a project declares one, and the
groups run staging → intermediate → mart. That is **build order** — the order the
tiers are constructed in — and never a quality ranking: a layer is a string a
human typed into a spec, nothing measured it, and it must not colour, resize or
rank a row. What says whether a table is right is its outcome check, per step.

It is not a file tree, and an empty section says so rather than disappearing —
"no specs yet" is information; a missing heading is not.
"""

from __future__ import annotations

from pathlib import Path

from nicegui import ui

from portia import catalog
from portia.ui import components as c
from portia.ui import engine
from portia.ui.state import APP, MODEL, OUTPUT, RUN, SOURCE, SPEC, TURN

ICON = {
    SOURCE: "table_chart",
    SPEC: "account_tree",
    MODEL: "code",
    OUTPUT: "description",
    RUN: "history",
    TURN: "forum",
}

#: Layer groups, in the order the tiers are built. Build order, not a ladder —
#: see the module docstring. A project that declares no layer has no groups at
#: all, which is the whole of how the flat case is handled (`PIPELINE.md` §2.5).
UNLAYERED = ""

MODELS_NOTE = "Nothing compiled yet. Press Build to write the pipeline as .sql."
RUNS_NOTE = "No saved runs. Press Run, then Save report."
TURNS_NOTE = "No copilot turns yet. Type a goal and press Go."


@ui.refreshable
def pane() -> None:
    """Sources · Specs · Models · Outputs · Runs · Turns, in that order."""
    with ui.element("div").classes("p-scroll"):
        _sources()
        _specs()
        _models()
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
    """The decision records, grouped by layer where the project declares one."""
    c.section_header("Specs")
    docs = engine.project_docs(APP)
    if not docs:
        c.empty_note("No spec yet. The copilot writes one as it records steps.")
        return
    stale = set(engine.stale_models(APP))
    for layer, names in _grouped(docs):
        if layer:
            c.group_header(layer)
        for name in names:
            path = engine.spec_path_for(APP, name)
            if path is None:
                continue
            c.artifact_row(
                name=path.name,
                icon=ICON[SPEC],
                meta=c.count(len(docs[name].get("steps") or []), "step"),
                note="its .sql is out of date" if name in stale else "",
                selected=APP.is_selected(SPEC, path.name),
                on_click=lambda p=path: _open_spec(p),
            )


def _models() -> None:
    """The compiled pipeline — the deliverable, and its own kind of artifact.

    Not a row in Outputs: a run's CSV is a result, and this is the thing someone
    else can run (`docs/PIPELINE.md` §2.2). `_sources.sql` is listed with the rest
    because it is part of what makes the pipeline runnable standalone, and hiding
    a generated file the user is expected to commit would be a small lie.
    """
    c.section_header("Models")
    models = engine.models_in(APP)
    if not models:
        c.empty_note(MODELS_NOTE)
        return
    stale = set(engine.stale_models(APP))
    for layer, paths in _grouped_paths(models):
        if layer:
            c.group_header(layer)
        for path in paths:
            rel = str(path.relative_to(APP.root))
            c.artifact_row(
                name=path.name,
                icon=ICON[MODEL],
                note="stale — its spec changed" if path.stem in stale else "",
                selected=APP.is_selected(MODEL, rel),
                on_click=lambda r=rel: _select(MODEL, r),
            )


def _grouped(docs: dict[str, dict]) -> list[tuple[str, list[str]]]:
    """Model names by layer, unlayered first, then in build order."""
    from portia.spec import LAYERS

    by_layer: dict[str, list[str]] = {}
    for name in sorted(docs):
        by_layer.setdefault(docs[name].get("layer") or UNLAYERED, []).append(name)
    order = [UNLAYERED, *LAYERS]
    known = [(layer, by_layer.pop(layer)) for layer in order if layer in by_layer]
    # A layer the engine does not know about is still shown rather than dropped:
    # the spec pane is where a typo in `layer:` should become visible.
    return known + sorted(by_layer.items())


def _grouped_paths(paths: list[Path]) -> list[tuple[str, list[Path]]]:
    """Compiled files by the subdirectory they landed in, which is their layer."""
    from portia.spec import LAYERS

    by_layer: dict[str, list[Path]] = {}
    for path in paths:
        parent = path.parent.name
        by_layer.setdefault(parent if parent in LAYERS else UNLAYERED, []).append(path)
    order = [UNLAYERED, *LAYERS]
    return [(layer, by_layer[layer]) for layer in order if layer in by_layer]


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
    APP.focus_model = path.stem
    pane.refresh()
    workflow.pane.refresh()
    app_module.toolbar.refresh()

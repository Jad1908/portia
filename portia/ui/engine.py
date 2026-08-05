"""The only place in `portia/ui/` that touches the engine.

`cli/` and `ui/` are two renderers of one engine, and the day they disagree about
a number is the day the seam broke (docs/VISION.md). Keeping every engine call in
one small module is how that stays checkable: the whole list is right here, and
nothing below it computes anything.

What the app is allowed to call, and why each is on the list:

- ``catalog.init_project`` — the mandatory context panel writes ``project.yaml``
- ``catalog.set_data_dir`` — which folder in the repo is this project's data,
  picked on the add-data screen and read back by the left pane
- ``catalog.index_source`` — a dropped file is profiled (free, deterministic)
- ``catalog.load_catalog`` — what the left pane and the source inspector show
- ``spec.load_spec`` / ``spec.run_spec`` / ``spec.write_report`` — the Run button
  and the report it can save
- ``pipeline.write_outputs`` — the tables a build produced, one CSV per model
- ``spec.discover_specs`` — the project's models, so the panel and the engine
  agree on what a spec is and a cross-spec reference resolves
- ``pipeline.build_project`` / ``stale_models`` — compiling to SQL, and whether a
  generated file still matches the spec that produced it
- ``core.io.load_table`` — previewing a produced table (the one way to load data)
- ``core.io.find_data_files`` — resolving what an import points at
- ``cli.import_data.plan`` — what a set of files would be copied to, and where.
  Shared with the terminal deliberately (`docs/PIPELINE.md` §6): the window shows
  the same plan `import_data` shows because it *is* the same plan, not because
  two surfaces were written to agree about one
- ``agent.session.run`` — a turn, driven with the app's own answer/confirm
- ``runlog.runs_in`` / ``read`` / ``read_header`` / ``summary`` — past copilot
  turns for the Turns section and the replay. The summary in particular: those
  counts are the engine's, so the window and `cli.runs` cannot end up quoting
  two different numbers for how often the copilot asked.

The one thing here that isn't the engine is ``browse_for_folder``: the OS's own
folder chooser, because picking a directory by typing its absolute path is not a
thing anyone should be asked to do.

Blocking work (profiling a source, executing a spec) hits the database and would freeze the
websocket, so it goes through ``asyncio.to_thread``. Reading a file the panes
draw — a report, a compiled model, a turn's log — deliberately does **not**: the
middle pane draws in one pass so that a click never paints a blank frame, and an
`await` in the middle of a render is exactly what that costs (`workflow.pane`).
Nothing here formats anything for a human — that is the panes' job.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from functools import partial
from pathlib import Path

from portia import catalog, pipeline, runlog
from portia import spec as spec_module
from portia.cli.import_data import plan as plan_copy
from portia.core.io import connect, find_data_files, load_table, supported_suffixes
from portia.ui import state as State
from portia.ui import tree
from portia.ui.state import App

#: Where an import lands when the project has not named a data folder, and where
#: a run writes its tables. Relative to the project root, so the catalog and the
#: spec stay portable.
DATA_DIR = "data"
OUT_DIR = "out"

#: Saved *spec run* reports, at the project root. The Runs section lists these;
#: the Turns section lists copilot turns, which are a different artifact living
#: inside `.portia/` (`runlog.RUNS_DIR`). Same word, two things — hence two
#: sections and two headings.
RUNS_DIR = "runs"

#: Recently opened projects. Not project state, so it lives with the user rather
#: than inside any one ``.portia/``.
RECENTS = Path.home() / ".config" / "portia" / "recents.json"
RECENTS_KEPT = 8


# --- opening a project ------------------------------------------------------


def open_project(path: str | Path, app: App) -> Path:
    """Point the app at a directory, creating it if it doesn't exist yet.

    Testing means a fresh directory per run, so a path that isn't there yet is
    created rather than rejected (docs/VISION.md → `project-open`).

    The process working directory moves with it. That is not incidental: the
    engine resolves a catalog's ``source:`` paths and a spec's ``sources:`` paths
    relative to the current directory — the way a project config normally is —
    and the agent's tools do the same. Running the app from elsewhere without
    this would make every relative path in the project mean something different
    to the UI than it means to the copilot.
    """
    root = Path(path).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    os.chdir(root)
    app.root = root
    app.spec_path = None
    app.spec = None
    app.results = None
    app.run_error = None
    app.selection = None
    app.selected_step = None
    app.streams = {tab: State.Stream() for tab in State.TABS}
    app.tab = State.CHAT
    app.skipped_sources = False
    app.editing = app.asking = app.removing = None
    # The add-data screen, back to the top. Where the picker was browsing and
    # which files were ticked are statements about the last project's directory,
    # and a half-planned import into it must not follow you into this one.
    app.browse_at = ""
    app.unpicked = frozenset()
    app.repicking = False
    app.import_open = False
    app.import_to_data_dir = True
    app.import_plan, app.import_error = [], ""
    app.indexed = None
    # Which folders are open belongs to the project you are looking at, not to
    # the window: `data/` opened in the last project says nothing about this one.
    app.open_folders = app.closed_folders = frozenset()
    refresh_catalog(app)
    # A project that already has data is not being set up, so it opens on the
    # workspace. The add-data screen is for the first time, and for whenever
    # someone asks for it from the left pane.
    app.left_add_data = bool(app.sources)
    remember(root)
    return root


#: Ask the OS for a folder, or for files. macOS only, and deliberately so: the
#: app is local-first (`TECH_STACK.md` — `pip install` → localhost), so the
#: machine running the server is the machine with the Finder. Elsewhere the path
#: field is the way in, which is why it is still there.
_CHOOSE_FOLDER = 'POSIX path of (choose folder with prompt "Choose a project folder")'

#: The file chooser is long enough to be worth reading as AppleScript rather than
#: as a Python string, so it lives beside the CSS and the canvas behaviour.
_CHOOSE_FILES = Path(__file__).parent / "assets" / "choose_files.applescript"


def can_browse() -> bool:
    return sys.platform == "darwin" and shutil.which("osascript") is not None


async def browse_for_folder() -> Path | None:
    """Open the native folder chooser. ``None`` if it isn't available or was cancelled."""
    if not can_browse():
        return None
    return await asyncio.to_thread(_choose_folder)


def _choose_folder() -> Path | None:
    chosen = _osascript(_CHOOSE_FOLDER)
    return Path(chosen[0]) if chosen else None


async def browse_for_files() -> list[Path]:
    """Open the native file chooser. Empty if it isn't available or was cancelled."""
    if not can_browse():
        return []
    return await asyncio.to_thread(_choose_files)


def _choose_files() -> list[Path]:
    return [Path(line) for line in _osascript(_CHOOSE_FILES.read_text())]


def _osascript(script: str) -> list[str]:
    """Run a chooser and return its lines, or nothing at all.

    A cancelled dialog exits non-zero with "User canceled." on stderr — not an
    error worth surfacing, just an answer of "no".
    """
    try:
        done = subprocess.run(
            ["osascript", "-e", script], capture_output=True, text=True, check=False
        )
    except OSError:
        return []
    if done.returncode != 0:
        return []
    return [line for line in done.stdout.strip().splitlines() if line.strip()]


def recents() -> list[tuple[Path, str]]:
    """Recently opened project directories, newest first, with when they were opened."""
    try:
        entries = json.loads(RECENTS.read_text())
    except (OSError, ValueError):
        return []
    return [(Path(e["path"]), e.get("opened", "")) for e in entries if isinstance(e, dict)]


def remember(root: Path) -> None:
    kept = [(p, when) for p, when in recents() if p != root]
    entries = [{"path": str(root), "opened": datetime.now().isoformat(timespec="minutes")}]
    entries += [{"path": str(p), "opened": when} for p, when in kept]
    RECENTS.parent.mkdir(parents=True, exist_ok=True)
    RECENTS.write_text(json.dumps(entries[:RECENTS_KEPT], indent=2))


def has_context(app: App) -> bool:
    return bool(app.project_context)


def set_context(text: str, app: App) -> None:
    catalog.init_project(text.strip(), portia_dir=app.portia_dir)
    refresh_catalog(app)


def refresh_catalog(app: App) -> None:
    app.catalog = catalog.load_catalog(app.portia_dir)


# --- the project's data folder ----------------------------------------------


def set_data_dir(rel: str, app: App) -> None:
    """Record which folder in the repo holds this project's data, durably.

    A project setting rather than a window setting: it decides what the left pane
    draws as data and where an import lands by default, and both of those have to
    survive the process. `catalog.set_data_dir` puts it in ``project.yaml``, where
    it is read in a diff beside the brief it belongs with.

    **The project root is stored as ``"."``, never as ``""``.** They are the same
    *scope* — every path in the repo, which `tree` collapses back to "anywhere" —
    and different *answers*: ``""`` is "nobody has said". Normalising here rather
    than at the button is what keeps that distinction one fact; the add-data
    screen reads the setting back to decide whether to draw the picker, and an
    empty string sent it round the loop again.
    """
    catalog.set_data_dir(rel or ".", portia_dir=app.portia_dir)
    refresh_catalog(app)


def folder_choices(app: App, at: str) -> tuple[tree.Choice, ...]:
    """The sub-folders of ``at`` that hold readable data, for the in-page picker.

    Rooted at the project, always. Choosing the data folder is choosing a *scope
    inside the repo* — an outside folder is not a thing this control can express,
    which is the same rule `index` enforces and one fewer error to write.
    """
    return tree.choices(app.root, at, readable_suffixes())


def data_files_in(app: App, rel: str) -> list[Path]:
    """Every readable data file under a repo-relative folder, at any depth.

    What the add-data screen ticks and profiles. Recursive, because a data folder
    with a year per sub-folder is the ordinary shape and asking someone to pick
    each one is not a scope, it is a chore.
    """
    return list(tree.data_files(app.root / rel if rel else app.root, readable_suffixes()))


def crumbs(app: App, at: str) -> list[tuple[str, str]]:
    """The picker's path trail, with the project's own directory name at the root."""
    trail = tree.crumbs(at)
    root_name = app.root.name or str(app.root)
    return [(rel, name or root_name) for rel, name in trail]


def remove_source(name: str, app: App) -> None:
    """Un-index a source. The file stays on disk — see `catalog.remove_source`."""
    catalog.remove_source(name, portia_dir=app.portia_dir)
    refresh_catalog(app)


def set_interpretation(
    source: str, *, summary: str | None, roles: dict[str, str], app: App
) -> None:
    """Record the human's read of a source — the same write the copilot makes.

    ``catalog.set_interpretation`` is judgment-only by construction: it writes the
    prose and the roles and never touches a measured fact. So a correction typed
    here and one the copilot proposes land in the same place, in the same shape,
    and a re-index preserves both (`catalog` → the update rule).
    """
    catalog.set_interpretation(source, summary=summary, roles=roles, portia_dir=app.portia_dir)
    refresh_catalog(app)


# --- adding data ------------------------------------------------------------


def plan_import(target: str, destination: str, app: App) -> list[tuple[Path, Path]]:
    """What importing ``target`` into ``destination`` would copy, and to where.

    The deliberate half of `docs/PIPELINE.md` §2.7. Nothing is written: this is
    what the confirmation shows, so that what you agree to is the real thing
    rather than a description of one, and so a name collision surfaces before the
    first byte moves rather than halfway through a batch.

    ``plan_copy`` is `cli.import_data.plan` — the same function the terminal
    calls, which is why the two cannot disagree about where a file is going.
    Raises ``ValueError`` for anything the operator can fix: nothing matched, the
    destination is outside the project, a name is already taken.
    """
    sources = find_data_files(target)
    return plan_copy(sources, destination_in(destination, app), app.root)


def destination_in(raw: str, app: App) -> Path:
    """A destination the operator typed, as a path inside the project.

    Relative to the project root, always — an absolute path that happens to point
    inside is accepted, and one that points outside is refused by ``plan``. Data
    lives in the repo (`PIPELINE.md` §2.7) and the destination field is not the
    place to make an exception to that.
    """
    typed = Path((raw or "").strip() or DATA_DIR)
    return typed if typed.is_absolute() else app.root / typed


async def import_files(pairs: list[tuple[Path, Path]], app: App) -> list[Path]:
    """Do what the plan said, and nothing it didn't.

    **A copy, never a move.** The original is not touched, not deleted, not
    rewritten — a tool that relocates someone's data is not a data-harmonization
    concern (`CLAUDE.md`), and the one time it matters is the time you find out
    afterwards.
    """
    return await asyncio.to_thread(_copy_all, pairs)


def _copy_all(pairs: list[tuple[Path, Path]]) -> list[Path]:
    copied = []
    for src, dst in pairs:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        copied.append(dst)
    return copied


async def index(paths: list[Path], app: App, *, on_progress=None) -> list[str]:
    """Profile each file into the catalog. Deterministic, free, always happens.

    The *interpretation* half is a model turn and is deliberately not here — the
    UI has to show them as two things because one of them costs money.

    **One file per hop, so the screen can say which one.** This used to hand the
    whole list to a single thread and come back when it was done, which for twenty
    real extracts is a minute of a window that says nothing. ``on_progress(done,
    total, name)`` is called before each file, on the event loop, so a caller can
    redraw between them.
    """
    names = []
    for done, path in enumerate(paths):
        if on_progress is not None:
            on_progress(done, len(paths), path.stem)
        names.append(await asyncio.to_thread(_index_one, path, app.portia_dir))
    refresh_catalog(app)
    await asyncio.to_thread(sync_knowledge, app)
    return names


def _index_one(path: Path, portia_dir: str) -> str:
    """One source into the catalog. Nothing is copied — see `catalog.index_source`.

    There is no shared connection to thread through any more: with the store
    retired, indexing profiles the file where it lies and `catalog.source_ref`
    records the path relative to the project (`docs/PIPELINE.md` §2.7).
    """
    catalog.index_source(path, portia_dir=portia_dir)
    return path.stem


# --- specs, runs, outputs ---------------------------------------------------


def readable_suffixes() -> tuple[str, ...]:
    """The formats `core.io` registers a reader for.

    Read off the loader rather than written down anywhere in `ui/`: the left tree
    shows un-indexed data files, and a hard-coded format list there would have
    stopped showing Parquet the day it landed.
    """
    return supported_suffixes()


def known_files(app: App) -> dict[str, tuple[str, str]]:
    """Every file portia knows about: repo-relative path → ``(kind, ident)``.

    This is the left pane's **filter** and its **bridge back to a selection**. The
    tree is a real directory walk (`ui/tree.py`), so a row arrives as a path —
    but the panes address a source by its catalog name and a saved run by its
    filename, and this is what turns one into the other.

    Built from the same calls the sections used to be built from, so the tree
    cannot contain a different set of specs than `cli.build` does.
    """
    known: dict[str, tuple[str, str]] = {}
    for path in runs_in(app):
        known[_rel(path, app)] = (State.RUN, path.name)
    for path in outputs_in(app):
        known[_rel(path, app)] = (State.OUTPUT, path.name)
    for path in models_in(app):
        known[_rel(path, app)] = (State.MODEL, _rel(path, app))
    for path in specs_in(app):
        known[_rel(path, app)] = (State.SPEC, path.name)
    # Sources last, and deliberately: a data file that a run also wrote into
    # `out/` is a source first, because that is the entry with a profile and an
    # interpretation behind it.
    for name, entry in app.sources.items():
        recorded = entry.get("source")
        if recorded:
            known[Path(recorded).as_posix()] = (State.SOURCE, name)
    return known


def _rel(path: Path, app: App) -> str:
    """A path as the project sees it. Outside the root it stays as it is."""
    try:
        return path.relative_to(app.root).as_posix()
    except ValueError:
        return path.as_posix()


def project_tree(app: App) -> tuple[tree.Node, ...]:
    """The project directory, filtered to what portia reads. The left pane.

    ``data_dir`` scopes the *readable* half of that filter and nothing else: an
    un-indexed CSV is drawn if it is under the project's data folder, while every
    artifact portia wrote — a spec, a model, an output, a saved run — is drawn
    wherever it lives. See `tree` for why the two halves are scoped differently.
    """
    return tree.build(app.root, known_files(app), readable_suffixes(), app.data_dir or None)


def specs_in(app: App) -> list[Path]:
    """Every spec in the project, in the order the engine finds them.

    Goes through `spec.discover_specs` rather than globbing, for the reason this
    whole module exists: a layered project keeps its specs in subdirectories, and
    a left panel that globbed one level would show a different set of specs than
    the engine builds. It also means the project's duplicate-name rule is enforced
    in one place, and the app inherits the error instead of quietly listing two
    specs that cannot both exist.
    """
    return sorted(app.root / path for path in spec_module.discover_specs(app.root).values())


def project_docs(app: App) -> dict[str, dict]:
    """Every spec in the project, loaded, as ``model name -> doc``.

    What the project graph is drawn from. The name is the spec's filename because
    one spec produces one table, so this mapping is also what resolves a
    cross-spec reference — the same `spec.discover_specs` the engine builds from,
    which is the point: the window must not have a different idea of what the
    project contains than `cli.build` does.
    """
    docs = {}
    for name, path in spec_module.discover_specs(app.root).items():
        try:
            docs[name] = spec_module.load_spec(app.root / path) or {}
        except (OSError, ValueError):
            # One unreadable spec is that spec's problem to report; it is not a
            # reason for the whole graph to refuse to draw.
            continue
    return docs


def spec_path_for(app: App, model: str) -> Path | None:
    """Where the spec that produces ``model`` lives, or None if nothing does."""
    found = spec_module.discover_specs(app.root).get(model)
    return app.root / found if found else None


def models_in(app: App) -> list[Path]:
    """The compiled ``.sql`` files — the pipeline, which is the deliverable.

    Not an output like ``out/*.csv``: a run's CSV is a result, and these are the
    thing you hand someone (`docs/PIPELINE.md` §2.2).
    """
    models = app.root / pipeline.MODELS_DIR
    return sorted(models.rglob("*.sql")) if models.is_dir() else []


def stale_models(app: App) -> list[str]:
    """Models whose ``.sql`` no longer matches the spec that produced it.

    Cheap — it reads a header, it runs nothing — so a panel may ask on any render.
    """
    try:
        return pipeline.stale_models(app.root)
    except (OSError, ValueError):
        # A malformed spec is the spec pane's problem to report, not a reason for
        # the whole left panel to fail to draw.
        return []


async def build(app: App, *, only: str | None = None) -> list[pipeline.BuiltModel]:
    """Run specs and write their ``.sql`` — the app's half of ``cli.build``.

    ``only`` scopes it to one model and everything it reads, which is what the
    Run button does; without it this is the whole project, which is Build. One
    call for both, because they are one mechanism at two scopes — and because two
    code paths for "execute the pipeline" is exactly how the window and the
    terminal end up disagreeing about a number.
    """
    return await asyncio.to_thread(partial(pipeline.build_project, app.root, only=only))


def count_steps(path: Path) -> int | None:
    """How many steps a spec records, or ``None`` if it can't be read."""
    try:
        return len(spec_module.load_spec(path).get("steps") or [])
    except (OSError, ValueError, AttributeError):
        return None


def outputs_in(app: App) -> list[Path]:
    out = app.root / OUT_DIR
    return sorted(out.glob("*.csv")) if out.is_dir() else []


def select_spec(path: Path | None, app: App) -> None:
    """Load a spec into the workflow pane. A run's results belong to one spec.

    ``built`` goes with them. It is what *Write outputs* writes, and a build the
    window is no longer showing is not a thing that button should still be able
    to save — the honest state of a freshly-opened spec is that nothing has run.
    """
    app.spec_path = path
    app.spec = spec_module.load_spec(path) if path and path.exists() else None
    app.results = None
    app.built = []
    app.run_error = None
    app.selected_step = None


async def execute(app: App, *, only: str | None = None) -> list[pipeline.BuiltModel]:
    """Build models and **pick up what came out** — the state both buttons leave.

    `build` is the engine call; this is the app's memory of it, and Run and Build
    share it for the same reason they share `build_project`: they are one
    mechanism at two scopes. Build used to throw its results away, so pressing it
    compiled the whole project and still left *Write outputs* and *Save report*
    greyed out — the project had been run and the window had no idea.

    ``results`` is the **open spec's** steps, because the report half and both
    save buttons are about the spec you are looking at; ``built`` is everything
    that ran, so the header can name the models it also had to build. A build that
    didn't touch the open spec (or a window with no spec open) leaves ``results``
    empty rather than borrowing another model's — the saves stay disabled, which
    is the honest state.

    A failure lands in ``run_error`` rather than a toast: the run report pane shows
    it until the next run, and a stack trace that vanishes after four seconds is
    not something you can read.
    """
    app.run_error = None
    app.built = []
    try:
        built = await build(app, only=only)
    except Exception as exc:  # noqa: BLE001 — shown to the operator, not swallowed
        app.results = None
        app.run_error = f"{type(exc).__name__}: {exc}"
        return []
    app.built = built
    open_model = app.spec_path.stem if app.spec_path else None
    target = next((m for m in built if m.name == open_model), None)
    app.results = target.results if target else None
    return built


async def run_spec(app: App) -> None:
    """Run the open spec **and everything it reads**, then write their ``.sql``.

    Both halves of that are decisions, not conveniences. A spec that references
    another spec's table cannot run until that table is built, so "run this spec"
    has always meant running its upstreams — `run_spec` did it implicitly. Doing it
    through `build_project(only=...)` is the same work, named, so the app can say
    which models it touched.

    And it **writes the SQL for what it ran**, so a run can never leave the
    deliverable describing an older version of the spec. That makes the staleness
    warning mean something narrower and more useful: it can now only fire on a
    spec edited outside the app.
    """
    app.run_error = None
    app.built = []
    if app.spec_path is None or app.spec is None:
        return
    await execute(app, only=app.spec_path.stem)


def runs_in(app: App) -> list[Path]:
    """Saved run reports, newest first."""
    runs = app.root / RUNS_DIR
    return sorted(runs.glob("*.md"), reverse=True) if runs.is_dir() else []


async def write_report(app: App) -> Path | None:
    """Save the open run as markdown. Explicit, like every other write here."""
    if not app.results:
        return None
    return await asyncio.to_thread(
        spec_module.write_report,
        app.results,
        app.root / RUNS_DIR,
        spec_path=app.spec_path,
    )


def read_text(path: Path) -> str:
    """A saved report or a compiled model, off disk.

    Not threaded, and that is the point: the pane that shows it draws in one pass
    (`workflow.pane`), because an `await` mid-render is what put a blank frame on
    screen between deleting the old content and creating the new. These are local
    files of a few kilobytes; the work that genuinely blocks is still threaded
    below.
    """
    return path.read_text()


# --- logged copilot turns ---------------------------------------------------


def turns_in(app: App) -> list[Path]:
    """Logged copilot turns, newest first (`portia/runlog.py`).

    Not the same thing as `runs_in`, which lists saved *spec run* reports. A
    turn is how the recipe was decided; a run is what the recipe did.
    """
    return runlog.runs_in(app.catalog_dir)


def turn_path(app: App, name: str) -> Path:
    """Where a named turn's log lives. Resolved here, so no panel has to know
    that turns sit inside `.portia/` while saved run reports sit beside it."""
    return app.catalog_dir / runlog.RUNS_DIR / name


def turn_header(path: Path) -> dict:
    """A turn's header alone — one line, for drawing a list row."""
    return runlog.read_header(path)


def read_turn(path: Path) -> runlog.Run:
    """One logged turn, off disk. Read in the render pass — see `read_text`."""
    return runlog.read(path)


def turn_summary(run: runlog.Run) -> dict:
    """The turn's own counts. Computed by the engine, never by a panel — the
    app must not arrive at a different number than `cli.runs` does."""
    return runlog.summary(run)


async def write_outputs(app: App) -> list[Path]:
    """Save the tables the last Run or Build produced, under ``out/``.

    **Every model that ran, not only the open one.** Run is already scoped to the
    open spec *and everything it reads*, and it writes the ``.sql`` for all of
    them; Build is the same mechanism at project scope. The tables follow that
    scope, so pressing this after a build leaves one file per model.

    Before, it wrote the open spec's table alone, which meant ``out/`` never held
    more than one file: selecting another spec clears the run, so the only table
    you could ever write was the one currently open, over the top of the last
    one. Naming each file for its model (`pipeline.write_outputs`) is what keeps
    "overwrite" honest — rebuilding a table replaces its own file, nothing else.
    """
    if not app.built:
        return []
    written = await asyncio.to_thread(pipeline.write_outputs, app.built, app.root / OUT_DIR)
    app.outputs = written
    return written


def read_table(path: Path):
    """A produced table, read lazily — through the one loader, like everything else.

    Nothing is read here. The pane that shows it asks for a count and fifteen
    rows; before this it loaded the whole file to show those fifteen, which was a
    straight bug the moment an output got large. That count is what actually
    costs something, and it has always been executed in the render pass by
    `components.table_preview` — so threading this handle bought nothing and cost
    a frame (`read_text`).
    """
    return load_table(path, connect())


# --- reloading the spec after the copilot has written to it -----------------


def sync_knowledge(app: App) -> str:
    """Put the project's structural half in the graph — **best effort, always**.

    The window indexes through `catalog.index_source` and used to stop there,
    so a source added here was in the catalog and absent from the graph until
    something else happened to refresh it. `cli.index` had done this since the
    graph shipped; the two edges disagreeing about what portia knows is the seam
    `docs/VISION.md` says must never break.

    Threaded, because it opens a Neo4j connection. Never fatal: an index that
    fails because a container is stopped is `KNOWLEDGE_GRAPH.md` §6.6's leak, and
    the catalog is written either way.
    """
    from portia import knowledge
    from portia.knowledge import store

    try:
        knowledge.sync(app.root, portia_dir=app.portia_dir)
    except store.GraphUnavailable as exc:
        return str(exc)
    return ""


def knowledge_subgraph(*, columns: bool = False) -> dict:
    """The knowledge graph, as nodes and edges for the explorer to draw.

    Synchronous, like every other read a pane draws in one pass: this is a
    handful of Cypher over a few hundred nodes, not a scan of anyone's data.

    A stopped container comes back as ``{"unavailable": ...}`` rather than
    raising. The window has to behave sensibly when the database is down
    (`KNOWLEDGE_GRAPH.md` §3.5), and a pane is the surface where "behave
    sensibly" means *say so and draw nothing else*.
    """
    from portia.knowledge import query, store

    try:
        with store.session() as session:
            return query.subgraph(session, columns=columns)
    except store.GraphUnavailable as exc:
        return {"nodes": [], "edges": [], "unavailable": str(exc)}


def reload_spec(app: App) -> None:
    """Re-read the open spec from disk; the copilot may have appended a step.

    Called after a turn ends and after each approved write, so the graph fills in
    as steps are recorded rather than after a manual refresh.
    """
    if app.spec_path and app.spec_path.exists():
        app.spec = spec_module.load_spec(app.spec_path)
    elif app.spec_path:
        app.spec = None

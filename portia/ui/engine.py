"""The only place in `portia/ui/` that touches the engine.

`cli/` and `ui/` are two renderers of one engine, and the day they disagree about
a number is the day the seam broke (docs/VISION.md). Keeping every engine call in
one small module is how that stays checkable: the whole list is right here, and
nothing below it computes anything.

What the app is allowed to call, and why each is on the list:

- ``catalog.init_project`` — the mandatory context panel writes ``project.yaml``
- ``catalog.index_source`` — a dropped file is profiled (free, deterministic)
- ``catalog.load_catalog`` — what the left pane and the source inspector show
- ``spec.load_spec`` / ``spec.run_spec`` / ``spec.write_outputs`` /
  ``spec.write_report`` — the Run button and what it can save
- ``core.io.load_table`` — previewing a produced table (the one way to load data)
- ``core.io.find_data_files`` — resolving what "add by path" points at
- ``agent.session.run`` — a turn, driven with the app's own answer/confirm

The one thing here that isn't the engine is ``browse_for_folder``: the OS's own
folder chooser, because picking a directory by typing its absolute path is not a
thing anyone should be asked to do.

Blocking work (profiling a source, executing a spec) hits the database and would freeze the
websocket, so it goes through ``asyncio.to_thread``. Nothing here formats
anything for a human — that is the panes' job.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from portia import catalog
from portia import spec as spec_module
from portia.core import store
from portia.core.io import find_data_files, load_table
from portia.ui import state as State
from portia.ui.state import App

#: Where a dropped file lands, and where a run writes its tables. Relative to the
#: project root, so the catalog and the spec stay portable.
DATA_DIR = "data"
OUT_DIR = "out"

#: Saved run reports. The Runs section of the left pane lists these.
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
    refresh_catalog(app)
    # A project that already has data is not being set up, so it opens on the
    # workspace. The add-data screen is for the first time, and for whenever
    # someone asks for it from the left pane.
    app.left_add_data = bool(app.sources)
    remember(root)
    return root


#: Ask the OS for a folder. macOS only, and deliberately so: the app is
#: local-first (`TECH_STACK.md` — `pip install` → localhost), so the machine
#: running the server is the machine with the Finder. Elsewhere the path field is
#: the way in, which is why it is still there.
_CHOOSE_FOLDER = 'POSIX path of (choose folder with prompt "Choose a project folder")'


def can_browse() -> bool:
    return sys.platform == "darwin" and shutil.which("osascript") is not None


async def browse_for_folder() -> Path | None:
    """Open the native folder chooser. ``None`` if it isn't available or was cancelled."""
    if not can_browse():
        return None
    return await asyncio.to_thread(_choose_folder)


def _choose_folder() -> Path | None:
    try:
        done = subprocess.run(
            ["osascript", "-e", _CHOOSE_FOLDER],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    chosen = done.stdout.strip()
    # A cancelled dialog exits non-zero with "User canceled." on stderr — not an
    # error worth surfacing, just an answer of "no".
    return Path(chosen) if done.returncode == 0 and chosen else None


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


async def store_upload(upload, app: App) -> Path:
    """Copy a dropped file into the project's data directory.

    Streams, through the upload's own ``save``. This used to be
    ``write_bytes(await upload.read())``, and ``read()`` pulls the whole file
    into memory — which throws away the spooling the upload already did and
    turns a twenty-file drop of real extracts into twenty files' worth of RAM at
    once. The rest of the engine stopped holding whole tables; the front door
    should not be the one place that still does.
    """
    target = app.root / DATA_DIR / Path(upload.name).name
    target.parent.mkdir(parents=True, exist_ok=True)
    await upload.save(target)
    return target


def copy_into_project(source: Path, app: App) -> Path:
    """Bring a file that is already on disk into the project, unless it's inside it."""
    source = source.expanduser().resolve()
    if source.is_relative_to(app.root):
        return source
    target = app.root / DATA_DIR / source.name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return target


def resolve_data(target: str) -> list[Path]:
    """What a path, directory or glob typed into the app points at."""
    return find_data_files(target)


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
    con = await asyncio.to_thread(store.connect, app.catalog_dir)
    names = []
    try:
        for done, path in enumerate(paths):
            if on_progress is not None:
                on_progress(done, len(paths), path.stem)
            names.append(await asyncio.to_thread(_index_one, path, app.portia_dir, app.root, con))
    finally:
        await asyncio.to_thread(con.close)
    refresh_catalog(app)
    return names


def _index_one(path: Path, portia_dir: str, root: Path, con) -> str:
    """One source into the store and the catalog, on the project's own connection.

    The connection is shared across the batch deliberately: DuckDB takes a write
    lock on the store, so opening one per file is both slower and a collision
    waiting to happen.
    """
    relative = path.resolve().relative_to(root) if path.resolve().is_relative_to(root) else path
    catalog.index_source(relative, portia_dir=portia_dir, con=con)
    return path.stem


# --- specs, runs, outputs ---------------------------------------------------


def specs_in(app: App) -> list[Path]:
    return sorted((app.root / "specs").glob("*.yaml")) if (app.root / "specs").is_dir() else []


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
    """Load a spec into the workflow pane. A run's results belong to one spec."""
    app.spec_path = path
    app.spec = spec_module.load_spec(path) if path and path.exists() else None
    app.results = None
    app.run_error = None
    app.selected_step = None


async def run_spec(app: App) -> None:
    """Execute the open spec and keep every ``StepResult`` for the report pane."""
    app.run_error = None
    if app.spec is None:
        return
    try:
        app.results = await asyncio.to_thread(spec_module.run_spec, app.spec, base_dir=app.root)
    except Exception as exc:  # noqa: BLE001 — shown to the operator, not swallowed
        app.results = None
        app.run_error = f"{type(exc).__name__}: {exc}"


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


async def read_text(path: Path) -> str:
    return await asyncio.to_thread(path.read_text)


async def write_outputs(app: App) -> list[Path]:
    """Save each step's table under ``out/``, the same way ``cli.run --write`` does."""
    if not app.results:
        return []
    written = await asyncio.to_thread(spec_module.write_outputs, app.results, app.root / OUT_DIR)
    app.outputs = written
    return written


async def read_table(path: Path):
    """A produced table, read lazily — through the one loader, like everything else.

    Nothing is read here. The pane that shows it asks for a count and fifteen
    rows; before this it loaded the whole file to show those fifteen, which was a
    straight bug the moment an output got large.
    """
    return load_table(path, store.memory())


# --- reloading the spec after the copilot has written to it -----------------


def reload_spec(app: App) -> None:
    """Re-read the open spec from disk; the copilot may have appended a step.

    Called after a turn ends and after each approved write, so the graph fills in
    as steps are recorded rather than after a manual refresh.
    """
    if app.spec_path and app.spec_path.exists():
        app.spec = spec_module.load_spec(app.spec_path)
    elif app.spec_path:
        app.spec = None

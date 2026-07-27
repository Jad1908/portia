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
- ``core.io.load_frame`` — previewing an output CSV (the one way to load data)
- ``core.io.find_data_files`` — resolving what "add by path" points at
- ``agent.session.run`` — a turn, driven with the app's own answer/confirm

Blocking work (profiling a CSV, executing a spec) is pandas and would freeze the
websocket, so it goes through ``asyncio.to_thread``. Nothing here formats
anything for a human — that is the panes' job.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from datetime import datetime
from pathlib import Path

from portia import catalog
from portia import spec as spec_module
from portia.core.io import find_data_files, load_frame
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
    app.rows = []
    app.turn = None
    refresh_catalog(app)
    remember(root)
    return root


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


# --- adding data ------------------------------------------------------------


def store_upload(name: str, content: bytes, app: App) -> Path:
    """Copy a dropped file into the project's data directory."""
    target = app.root / DATA_DIR / Path(name).name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
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


async def index(paths: list[Path], app: App) -> list[str]:
    """Profile each file into the catalog. Deterministic, free, always happens.

    The *interpretation* half is a model turn and is deliberately not here — the
    UI has to show them as two things because one of them costs money.
    """
    names = await asyncio.to_thread(_index_all, paths, app.portia_dir, app.root)
    refresh_catalog(app)
    return names


def _index_all(paths: list[Path], portia_dir: str, root: Path) -> list[str]:
    names = []
    for path in paths:
        relative = path.resolve().relative_to(root) if path.resolve().is_relative_to(root) else path
        catalog.index_source(relative, portia_dir=portia_dir)
        names.append(path.stem)
    return names


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


async def read_frame(path: Path):
    """Load a produced CSV for preview — through the one loader, like everything else."""
    return await asyncio.to_thread(load_frame, path)


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

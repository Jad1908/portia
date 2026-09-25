"""What the window remembers about the person using it, between launches.

**One file, `~/.config/portia/prefs.json`, beside `connections.yaml` and
`providers.yaml` and never inside a project.** Everything in it is about the
reader, not about the data: which model they reach for, which folders they had
open, where they dragged a card. None of it is a fact anyone else on the project
should inherit through a commit, and `.portia/` is the record of what the data
*is* (`engine.VIEWS` made this argument first, for the canvas filter).

It replaced three files that each had their own read, write and "the file is
broken" rule (`recents.json`, `views.json`, `layouts.json`), which is the shape
the conventions in `CLAUDE.md` exist to stop: the fourth preference would have
been a fourth copy. They are read once, the first time this file does not
exist, and left where they are; an older portia still finds them.

The document has two halves:

- ``machine``: one value per preference, the same in every project.
- ``projects``: one entry per project folder, keyed by its resolved path.

plus ``recents``, which is a list and belongs to neither half.

**What the file never holds**: a secret (the pool holds those in memory), a
number the engine measured, and the two approval modes. `state.App.autopilot`
says why: a mode that survived a restart is one you can be in without having
chosen to be.

Rules this module keeps so its callers do not have to:

- **A read never raises.** A missing file, a file another program half-wrote,
  a value of the wrong type: each reads as *nothing remembered*, and the
  caller's default applies. A preference is never worth refusing to open the
  window over.
- **An unreadable file is set aside, not overwritten.** It is renamed to
  ``prefs.unreadable.json`` before the next write, so a file somebody edited by
  hand and broke is still there to be read.
- **A write is atomic**: a temporary file beside it, then `os.replace`. Two
  windows on one machine race each other and the last write wins, which for a
  preference is the right answer; neither can leave half a file.
- **Nothing is written that has not moved** (`sync`), so a window that is
  sitting still does not touch the disk.
- **The project list is capped** (`PROJECTS_KEPT`), dropping the one looked at
  longest ago. Nothing prunes by checking whether a folder still exists: a
  project on a drive that is not mounted today is not a project that was
  deleted.

This module imports no NiceGUI, so what it remembers is testable without a
browser.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

HOME = Path.home() / ".config" / "portia"
FILE = HOME / "prefs.json"
#: Where an unreadable `FILE` is moved before a fresh one is written.
UNREADABLE = HOME / "prefs.unreadable.json"

#: The three files this one replaced, read once when it does not exist yet.
LEGACY_RECENTS = HOME / "recents.json"
LEGACY_VIEWS = HOME / "views.json"
LEGACY_LAYOUTS = HOME / "layouts.json"

#: Bumped only if a key changes meaning. A reader seeing a newer number reads
#: what it understands and keeps the rest (`_write` merges, never replaces).
VERSION = 1

#: How many recently opened projects the picker lists.
RECENTS_KEPT = 8
#: How many projects' view state is kept. Generous: an entry is a few hundred
#: bytes, and losing where you were in a project you come back to after six
#: months is worse than a file of 40 KB.
PROJECTS_KEPT = 50

#: The one lock every read-modify-write holds. Two browser tabs on one server
#: are two timers in one process; two processes are `os.replace`'s problem.
_LOCK = threading.RLock()


# --- the file ---------------------------------------------------------------


def load() -> dict[str, Any]:
    """The whole document. Never raises; an unreadable file reads as empty."""
    with _LOCK:
        return _read()


def _read() -> dict[str, Any]:
    try:
        data = json.loads(FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return _migrated()
    except (OSError, ValueError):
        # UnicodeDecodeError is a ValueError and not an OSError, so a file in
        # the wrong encoding has to be caught by name (`CLAUDE.md` → encoding).
        return {}
    return data if isinstance(data, dict) else {}


def _write(data: dict[str, Any]) -> None:
    """Replace the file with ``data``, atomically, setting aside one that was unreadable."""
    FILE.parent.mkdir(parents=True, exist_ok=True)
    _set_aside_if_unreadable()
    written = data.get("version")
    data["version"] = max(VERSION, written) if isinstance(written, int) else VERSION
    handle, temp = tempfile.mkstemp(prefix=".prefs.", suffix=".json", dir=FILE.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            json.dump(data, out, indent=2, sort_keys=True)
        os.replace(temp, FILE)
    except OSError as exc:
        log.warning("could not write %s: %s", FILE, exc)
        Path(temp).unlink(missing_ok=True)


def _set_aside_if_unreadable() -> None:
    if not FILE.exists():
        return
    try:
        data = json.loads(FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = None
    if isinstance(data, dict):
        return
    try:
        os.replace(FILE, UNREADABLE)
        log.warning("%s could not be read; kept it as %s", FILE, UNREADABLE)
    except OSError:
        pass


def _update(change) -> None:
    """Read, let ``change`` edit the document in place, write it back."""
    with _LOCK:
        data = _read()
        change(data)
        _write(data)


def _migrated() -> dict[str, Any]:
    """The three files this one replaced, as this one's document. Read-only."""
    data: dict[str, Any] = {}
    recents = _legacy(LEGACY_RECENTS)
    if isinstance(recents, list):
        data["recents"] = [e for e in recents if _recent_entry(e)][:RECENTS_KEPT]
    projects: dict[str, dict] = {}
    views = _legacy(LEGACY_VIEWS)
    if isinstance(views, dict):
        for root, names in views.items():
            if isinstance(names, list):
                projects.setdefault(str(root), {})["view"] = [str(n) for n in names]
    layouts = _legacy(LEGACY_LAYOUTS)
    if isinstance(layouts, dict):
        for root, offsets in layouts.items():
            if isinstance(offsets, dict):
                projects.setdefault(str(root), {})["layout"] = offsets
    if projects:
        data["projects"] = projects
    return data


def _legacy(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# --- the machine's half -----------------------------------------------------


def machine() -> dict[str, Any]:
    """Every preference that is the same in every project."""
    value = load().get("machine")
    return value if isinstance(value, dict) else {}


def update_machine(values: dict[str, Any]) -> None:
    """Merge ``values`` into the machine's half. A ``None`` removes the key."""

    def change(data: dict[str, Any]) -> None:
        data["machine"] = _merged(data.get("machine"), values)

    _update(change)


# --- one project's half -----------------------------------------------------


def key(root: Path) -> str:
    """A project's entry name: its resolved path, as this machine spells it.

    The machine's own separator, on purpose. `core.io.relative`'s forward
    slashes are for text that is committed or keyed across machines, and this
    file never leaves the one it was written on.
    """
    return str(Path(root).expanduser().resolve())


def project(root: Path) -> dict[str, Any]:
    """What was remembered about one project, or nothing."""
    projects = load().get("projects")
    entry = projects.get(key(root)) if isinstance(projects, dict) else None
    return entry if isinstance(entry, dict) else {}


def update_project(root: Path, values: dict[str, Any]) -> None:
    """Merge ``values`` into one project's entry. A ``None`` removes the key.

    Stamps the entry with when it was last written, which is the order
    `PROJECTS_KEPT` drops in. An entry left with nothing in it is removed.
    """
    name = key(root)

    def change(data: dict[str, Any]) -> None:
        projects = data.get("projects")
        projects = projects if isinstance(projects, dict) else {}
        entry = _merged(projects.get(name), values)
        entry.pop("seen", None)
        if entry:
            entry["seen"] = _now()
            projects[name] = entry
        else:
            projects.pop(name, None)
        data["projects"] = _capped(projects)

    _update(change)


def _capped(projects: dict[str, Any]) -> dict[str, Any]:
    if len(projects) <= PROJECTS_KEPT:
        return projects
    newest = sorted(
        projects.items(),
        key=lambda item: str(item[1].get("seen") or "") if isinstance(item[1], dict) else "",
        reverse=True,
    )
    return dict(newest[:PROJECTS_KEPT])


# --- recent projects --------------------------------------------------------


def recents() -> list[tuple[Path, str]]:
    """Recently opened project folders, newest first, with when each was opened."""
    entries = load().get("recents")
    if not isinstance(entries, list):
        return []
    return [(Path(e["path"]), str(e.get("opened") or "")) for e in entries if _recent_entry(e)]


def remember_opened(root: Path) -> None:
    """Put ``root`` at the head of the recent projects."""
    name = key(root)

    def change(data: dict[str, Any]) -> None:
        kept = data.get("recents")
        kept = [e for e in kept if _recent_entry(e)] if isinstance(kept, list) else []
        entry = {"path": name, "opened": datetime.now().isoformat(timespec="minutes")}
        data["recents"] = ([entry] + [e for e in kept if e["path"] != name])[:RECENTS_KEPT]

    _update(change)


def _recent_entry(entry: Any) -> bool:
    return isinstance(entry, dict) and isinstance(entry.get("path"), str) and bool(entry["path"])


# --- what the window remembers, read off the app and put back ---------------
#
# **Read off `App` once a second and written when it moved** (`sync`), rather
# than saved by every control that changes a preference. The model alone is
# set in eight places (the composer's picker, Settings', the indexing panel's,
# a started server, a provider switch in each of those), and a ninth added
# later without a save call would be a preference that silently stops being
# remembered. A snapshot compared against the last one written has no such
# list to keep up to date.

#: What `sync` last wrote, so a window sitting still writes nothing. ``None``
#: until `restore_machine` has run: until then the app holds defaults, and
#: writing them would overwrite what was remembered with what was not.
_written_machine: dict[str, Any] | None = None

#: A width outside this is not a width anybody dragged a pane to.
_WIDTHS = range(1, 10_000)


def machine_of(app: Any) -> dict[str, Any]:
    """The machine's half, as the app holds it now. ``None`` means *nothing to keep*."""
    from portia.ui.state import THEMES

    return {
        "provider": app.provider or None,
        "model": app.model or None,
        "effort": app.effort,
        "theme": THEMES[app.theme],
        "interpret": app.interpret,
        "panes": {band: list(pair) for band, pair in sorted(app.pane_choices.items())} or None,
        "files_width": app.files_width,
        "transcript_width": app.transcript_width,
        "reopen": app.reopen_last,
        # The project on screen, or none: a window closed on the picker opens
        # on the picker.
        "project": key(app.root) if app.opened else None,
    }


def restore_machine(app: Any) -> Path | None:
    """Put the machine's half back on ``app``. Returns the project that was open.

    Each value is checked before it is used, and one that no longer makes
    sense leaves the app's default where it was: a hand-edited file, a
    provider switched off since, a model its vendor retired. Where that
    changes what a message would be sent to, the composer says so
    (`App.spend_alert`), because a model swapped without a word is a bill for
    something nobody picked.
    """
    global _written_machine
    from portia.ui.state import BANDS, THEMES

    saved = machine()
    _restore_spend(app, saved)
    themes = {name: value for value, name in THEMES.items()}
    if saved.get("theme") in themes:
        app.theme = themes[saved["theme"]]
    if isinstance(saved.get("interpret"), bool):
        app.interpret = saved["interpret"]
    if isinstance(saved.get("reopen"), bool):
        app.reopen_last = saved["reopen"]
    panes = saved.get("panes")
    if isinstance(panes, dict):
        app.pane_choices = {
            band: (bool(pair[0]), bool(pair[1]))
            for band, pair in panes.items()
            if band in BANDS and isinstance(pair, list) and len(pair) == 2
        }
        app.show_files, app.show_transcript = app.panes_for(app.band)
    for name in ("files_width", "transcript_width"):
        value = saved.get(name)
        if isinstance(value, int) and not isinstance(value, bool) and value in _WIDTHS:
            setattr(app, name, value)
    # What the file holds, not what the app holds now: a value that came from
    # a default or from `--project` is not on disk, and must be written on the
    # first `sync` rather than counted as written (`_on_disk`).
    _written_machine = _on_disk(saved, machine_of(app))
    project = saved.get("project")
    return Path(project) if isinstance(project, str) and project else None


def _restore_spend(app: Any, saved: dict[str, Any]) -> None:
    """The provider, the model and the effort, each only if it still exists."""
    from portia.agent import providers
    from portia.agent.session import EFFORTS
    from portia.ui import state

    kind = saved.get("provider")
    if isinstance(kind, str) and kind and kind not in providers.offered_kinds():
        # Switched off in Settings → Providers since, or a kind this build
        # does not have. Its model means nothing on the provider left.
        gone = providers.get(kind).label if kind in providers.KINDS else kind
        app.spend_alert = (
            state.PROVIDER_GONE.format(provider=gone),
            state.USING_INSTEAD.format(model=_shown(app.provider, app.model or app.default_model)),
        )
    elif isinstance(kind, str) and kind:
        app.provider = kind
        _restore_model(app, saved.get("model"))
    effort = saved.get("effort")
    if effort in EFFORTS:
        app.effort = effort


def _restore_model(app: Any, model: Any) -> None:
    from portia.agent import providers
    from portia.ui import state

    if not isinstance(model, str) or not model:
        return
    provider = providers.get(app.provider)
    offered = {m.name for m in provider.static_models}
    if offered and model not in offered:
        # Retired by its vendor, or renamed in a newer build.
        app.model = provider.default_model
        app.spend_alert = (
            state.MODEL_GONE.format(model=model, provider=provider.label),
            state.USING_INSTEAD.format(model=_shown(app.provider, app.model)),
        )
        return
    app.model = model
    # A server's list arrives later (`engine.list_models`); the first one checks it.
    app.model_unconfirmed = "" if offered else model


def _shown(kind: str, model: str) -> str:
    """What the picker calls a model (``Claude Sonnet 5``), so the two say the same."""
    from portia.agent import providers

    listed = providers.get(kind).static_models
    return next((m.shown for m in listed if m.name == model), model)


# --- one project's view, read off the app and put back ----------------------
#
# Where you were in a project: its tabs, the chat on the right, what is folded
# on the left, the spec open on the canvas and the half-written message. The
# canvas filter and the dragged cards are in the same entry, written by their
# own controls as they change (`engine.remember_view`, `remember_layout`) and
# restored where they always were; `project_of` leaves those two keys alone.

#: The project whose entry `sync` may write, and what it last wrote there.
#: Set by `restore_project`, so an entry is never written for a project whose
#: entry was not read first — which is how a second window would otherwise
#: overwrite where you were with a freshly opened project's defaults.
_written_project: tuple[str, dict[str, Any]] | None = None

#: What tab zero may be put back on. A spec's row is written here too and put
#: back by `app._pick_up_spec`, with the spec, because only the specs on disk
#: say whether it is still there. The kinds whose identity is a file name
#: rather than a path (a run, an output) are left out: a name is not enough to
#: know it is still the file that was selected.
_RESTORABLE = ("source", "model", "brief", "knowledge", "figures")
_SPEC = "spec"


def project_of(app: Any) -> dict[str, Any]:
    """One project's view, as the app holds it now. ``None`` means *nothing to keep*."""
    from portia.ui import state

    tabs = []
    for chart in app.charts:
        if chart.closed or not chart.saved:
            # An unsaved chart is only in memory: its rows came from a query
            # this process ran, and there is no file to open it from.
            continue
        tab: dict[str, Any] = {"path": chart.path, "group": chart.group}
        if chart.preview:
            tab["preview"] = True
        if chart.height:
            tab["height"] = chart.height
        tabs.append(tab)
    kept = {tab["path"] for tab in tabs} | {state.CANVAS}
    kinds = (*_RESTORABLE, _SPEC)
    selection = app.selection if app.selection and app.selection[0] in kinds else None
    return {
        "tabs": tabs or None,
        "active": {group: key for group, key in app.active.items() if key in kept},
        "canvas_group": app.canvas_group,
        "focus_group": app.focus_group,
        "chat": _chat_of(app),
        "right": app.right if app.open is None else None,
        "folders_open": sorted(app.open_folders) or None,
        "folders_closed": sorted(app.closed_folders) or None,
        "figures_open": app.figures_open,
        "figures_closed": sorted(app.figures_closed) or None,
        "warehouse_open": app.warehouse_open,
        "warehouse_closed": sorted(app.warehouse_closed) or None,
        "report_open": app.report_open,
        "knowledge_columns": app.knowledge_columns,
        "spec": app.spec_path.stem if app.spec_path else None,
        # Kept when empty: every card shut is a choice, and absent means
        # *open the spec you land on*.
        "expanded": sorted(app.expanded),
        "selection": list(selection) if selection else None,
        "draft": _draft_of(app),
    }


def _chat_of(app: Any) -> str | None:
    """The open chat's log, relative to `.portia/`, or none."""
    path = app.open.path if app.open is not None else None
    if path is None:
        return None
    try:
        return Path(path).resolve().relative_to(app.catalog_dir.resolve()).as_posix()
    except ValueError:
        return None


def _draft_of(app: Any) -> str | None:
    """The composer's text, or none.

    `App.goal` is the composer's text **and** the brief screen's scratch
    (`screens.project_context`), so it is a draft only while the brief is
    saved and not being edited. A brief half-typed on the first screen is
    not a message, and restoring it into the composer would send it as one.
    """
    if not app.project_context or app.editing_brief:
        return None
    return app.goal if (app.goal or "").strip() else None


def restore_project(app: Any, load_figure) -> dict[str, Any]:
    """Put one project's view back on ``app``. Returns the entry, for what it does not restore.

    Called at the end of `engine.open_project`, once the catalog is read.
    ``load_figure(path) -> dict`` reads a saved figure and raises `OSError`
    or `ValueError` when it cannot, which is `engine.load_figure`'s contract.

    What the caller restores itself, because it needs more than state: the
    spec (``spec`` and ``expanded``, which `app._pick_up_spec` checks against
    the specs on disk) and the chat (``chat``, which opening reads a log).

    **Everything is checked against the project as it is now**, since the
    folder may have changed while portia was closed: a figure deleted or
    moved is not reopened, a source no longer in the catalog is not
    selected, and the tabs are laid out again by `_put_tabs_back` rather
    than trusted.
    """
    global _written_project
    saved = project(app.root)
    _put_tabs_back(app, saved, load_figure)
    for name in ("figures_open", "warehouse_open", "report_open", "knowledge_columns"):
        if isinstance(saved.get(name), bool):
            setattr(app, name, saved[name])
    for name, field in (
        ("folders_open", "open_folders"),
        ("folders_closed", "closed_folders"),
        ("figures_closed", "figures_closed"),
        ("warehouse_closed", "warehouse_closed"),
    ):
        setattr(app, field, _names(saved.get(name)))
    app.selection = _selection(app, saved.get("selection"))
    if saved.get("right") in ("list", "sources"):
        app.right = saved["right"]
    draft = saved.get("draft")
    app.goal = draft if isinstance(draft, str) and app.project_context else ""
    with _LOCK:
        # The file's values, for `_on_disk`'s reason: what the project opened
        # with and nobody changed still has to reach the file.
        _written_project = (key(app.root), _on_disk(saved, project_of(app)))
    return saved


def _put_tabs_back(app: Any, saved: dict[str, Any], load_figure) -> None:
    from portia.ui import state

    tabs = saved.get("tabs")
    previews: set[str] = set()
    for tab in tabs if isinstance(tabs, list) else []:
        path = tab.get("path") if isinstance(tab, dict) else None
        if not isinstance(path, str) or not path or app.chart(path) is not None:
            continue
        try:
            figure = load_figure(path)
        except (OSError, ValueError):
            continue  # deleted, moved or unreadable since: not reopened
        group = tab.get("group") if tab.get("group") in state.GROUPS else state.LEFT
        # One preview per group, the rule `App._replace_preview` keeps.
        preview = tab.get("preview") is True and group not in previews
        if preview:
            previews.add(group)
        chart = state.Chart.from_figure(path, figure, preview=preview)
        chart.group = group
        height = tab.get("height")
        if isinstance(height, int) and not isinstance(height, bool) and height in _WIDTHS:
            chart.height = height
        app.charts.append(chart)
    canvas = saved.get("canvas_group")
    app.canvas_group = canvas if canvas in state.GROUPS else state.LEFT
    if not app.keys(state.LEFT):
        # Everything on the left went missing: the right half slides over, the
        # rule `App._after_close` keeps for a half closed by hand.
        for chart in app.charts:
            chart.group = state.LEFT
        app.canvas_group = state.LEFT
    active = saved.get("active")
    active = active if isinstance(active, dict) else {}
    for group in state.GROUPS:
        keys = app.keys(group)
        wanted = active.get(group)
        app.active[group] = wanted if wanted in keys else (keys[0] if keys else "")
    focus = saved.get("focus_group")
    app.focus_group = focus if focus in state.GROUPS and app.keys(focus) else state.LEFT


def _selection(app: Any, saved: Any) -> tuple[str, str] | None:
    if not (isinstance(saved, list) and len(saved) == 2 and saved[0] in _RESTORABLE):
        return None
    kind, name = str(saved[0]), str(saved[1])
    if kind == "source" and name not in app.sources:
        return None
    if kind == "model" and not (app.root / name).is_file():
        return None
    return kind, name


def _names(value: Any) -> frozenset[str]:
    return frozenset(str(v) for v in value) if isinstance(value, list) else frozenset()


# --- writing ----------------------------------------------------------------


def sync(app: Any) -> None:
    """Write what moved since the last write. Cheap when nothing did.

    Called once a second from the page, once more on the way out
    (`ui/app.py`), and before a project is left, so the last second of it is
    not lost. Nothing is written before the last launch was read back
    (`restore_machine`), and a project's entry only after it was
    (`restore_project`).

    **Only the keys that moved in this process are written** (`_moved`).
    Two windows on one machine are two processes (a second `python -m
    portia.ui` takes the next free port), each holding what it read at its
    own start. Writing the whole snapshot let the one that wrote last put
    back every value the other had changed since: a theme picked on one
    window undone by a pane dragged on the other. Now they overwrite each
    other only on a setting both changed, where the later change is the one
    to keep. The same holds for two windows on one project.
    """
    global _written_machine, _written_project
    with _LOCK:
        if _written_machine is not None:
            now = machine_of(app)
            moved = _moved(now, _written_machine)
            if moved:
                update_machine(moved)
                _written_machine = now
        if _written_project is not None and app.opened:
            name, written = _written_project
            if name == key(app.root):
                view = project_of(app)
                moved = _moved(view, written)
                if moved:
                    update_project(app.root, moved)
                    _written_project = (name, view)


def _on_disk(saved: dict[str, Any], shape: dict[str, Any]) -> dict[str, Any]:
    """What the file said for each key `sync` writes, as the baseline it compares against.

    The baseline used to be the app's state right after restoring, which
    counted as written everything the file never held: a project opened with
    `--project`, a default nobody changed. Once `sync` wrote only what moved
    (§2.3.1), those were never written at all, and a window launched on a
    project reopened on the picker (2026-09-25, found by restarting a window).
    """
    return {name: saved.get(name) for name in shape}


def _moved(now: dict[str, Any], written: dict[str, Any]) -> dict[str, Any]:
    """The keys whose value is not what this process last wrote or read."""
    return {name: value for name, value in now.items() if written.get(name) != value}


def forget_restore() -> None:
    """Back to *nothing restored yet*, so the next `sync` writes nothing. For tests."""
    global _written_machine, _written_project
    _written_machine = None
    _written_project = None


# --- shared -----------------------------------------------------------------


def _merged(current: Any, values: dict[str, Any]) -> dict[str, Any]:
    merged = dict(current) if isinstance(current, dict) else {}
    for name, value in values.items():
        if value is None:
            merged.pop(name, None)
        else:
            merged[name] = value
    return merged


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")

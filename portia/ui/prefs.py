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

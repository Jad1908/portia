"""The gallery — figures the user kept, on disk, in folders they arrange.

`docs/VISUALIZATION.md` §6. **This reverses that section's first decision**, on
the user's call *(2026-09-03)*: keeping a chart used to write a finding and
nothing else, on the reasoning that a chart which changed a decision and a
counting query that changed a decision are the same kind of thing and a second
home for one of them is how the two drift. That argument was about the *prose*
and it still holds. What it got wrong is that a chart is also **a picture**, and
a journal entry holding a sentence and a SQL string is not one — reopening it
meant re-running the query, and only while the same chat was still on screen.

So a saved figure is a file. It carries everything needed to draw it again with
nothing running: the encoding, the rows as they were measured, the query that
produced them, and the note the person who saved it typed. `findings/` keeps what
we concluded; `figures/` keeps what we looked at.

**Folders are the user's, not portia's.** Nothing here derives a layout, sorts
into categories or files by date: a gallery is arranged by the person who has to
find things in it later, so this module offers `folder`, `move` and `remove` and
holds no opinion about where anything goes.

**JSON, not YAML**, which is the one place this artifact differs from every other
durable thing in the repo. The others are read in a diff and are prose plus a
handful of numbers; a figure is mostly rows, and five thousand of them in
block-style YAML is a file nobody opens twice. The note is still the first field,
so the top of the file reads.
"""

from __future__ import annotations

import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from portia.core.io import relative

#: Where saved figures live, relative to the project root. Beside `findings/` and
#: `specs/`, at the top: it is something the project has, not something
#: `.portia/` is keeping notes about.
FIGURES_DIR = "figures"

#: The suffix a figure file carries.
SUFFIX = ".json"


def save(
    chart: dict,
    *,
    notes: str = "",
    folder: str = "",
    root: str | Path = ".",
) -> Path:
    """Write one figure. Everything needed to draw it again, plus the note.

    ``chart`` is a `handlers.plot_data` answer — the rows included, which is the
    whole point: a figure that had to re-run its query to be looked at would be
    a figure you cannot open on a plane, or after the source file moved.

    ``notes`` is the only thing a human typed and the only thing that is not a
    measurement. It is not required: **a figure is worth keeping because you
    looked at it**, and demanding a sentence before you may keep one is the
    friction that stops people keeping things. `findings/` is where a claim goes,
    and `so` is required *there* for exactly that reason.
    """
    directory = _dir(root) / _safe_folder(folder)
    directory.mkdir(parents=True, exist_ok=True)
    path = _free_path(directory, slug(chart["tab"]))
    _write(path, _doc(chart, notes))
    return path


def _doc(chart: dict, notes: str = "") -> dict[str, Any]:
    """A `handlers.plot_data` answer as the record a figure file holds."""
    return {
        "name": chart["tab"],
        "question": chart.get("question", ""),
        "notes": notes.strip(),
        "at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "sql": chart.get("sql", ""),
        "inputs": list(chart.get("inputs") or []),
        "vega": dict(chart.get("vega") or {}),
        "columns": list(chart.get("columns") or []),
        "n_rows": chart.get("n_rows", len(chart.get("rows") or [])),
        "rows": list(chart.get("rows") or []),
    }


def _write(path: Path, doc: dict[str, Any]) -> None:
    """Whole or not at all: a window reading this folder must never see half a chart."""
    partial = path.with_suffix(path.suffix + ".part")
    with open(partial, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
    partial.replace(path)


def load(path: str | Path) -> dict:
    """One figure, as it was saved — with a pre-Vega-Lite one brought forward.

    `docs/VISUALIZATION.md` §2.9 replaced portia's flat encoding vocabulary with
    a spec the agent writes, and a figure saved before that holds the old shape.
    **A committed artifact that silently stops drawing is not an acceptable cost
    of a refactor**, and the conversion is small because the old vocabulary was:
    it was always one mark and a handful of channels, which is what a minimal
    Vega-Lite spec is.
    """
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    if "vega" not in doc and doc.get("encoding"):
        doc["vega"] = _as_vega(doc["encoding"])
    return doc


#: The channels the flat encoding had, before §2.9. Read by `_as_vega` and by
#: nothing else — this is a migration, not a vocabulary.
_WAS_CHANNELS = ("x", "y", "color", "theta", "radius", "size", "shape", "column", "row", "text")


def _as_vega(encoding: dict) -> dict:
    """A figure saved under the flat encoding, as the Vega-Lite it always meant."""
    channels: dict[str, Any] = {}
    for name in _WAS_CHANNELS:
        column = encoding.get(name)
        if not column:
            continue
        channel: dict[str, Any] = {"field": column}
        title = encoding.get(f"{name}_title")
        if title:
            channel["title"] = title
        channels[name] = channel
    return {"mark": encoding.get("mark") or "bar", "encoding": channels}


def load_all(root: str | Path = ".") -> list[dict]:
    """Every saved figure, each with the repo-relative ``path`` it lives at.

    Sorted by path so the list matches what the folders look like, rather than by
    when they were saved — the arrangement is the user's and the order should be
    the one they made.
    """
    base = _dir(root)
    out = []
    for path in sorted(base.rglob(f"*{SUFFIX}")):
        try:
            figure = load(path)
        except (OSError, ValueError):
            continue
        figure["path"] = relative(path, root)
        out.append(figure)
    return out


def folders(root: str | Path = ".") -> list[str]:
    """Every folder under ``figures/``, relative to it, deepest last.

    The empty string is in the list and comes first: the gallery's own root is
    somewhere a figure can be, and a *move to…* list that omitted it would be a
    one-way trip into a subfolder.
    """
    base = _dir(root)
    found = [""]
    if base.exists():
        found += sorted(relative(p, base) for p in base.rglob("*") if p.is_dir())
    return found


def make_folder(name: str, root: str | Path = ".") -> Path:
    """Create a folder in the gallery. Existing is not an error."""
    if not (name or "").strip():
        raise ValueError("A folder needs a name.")
    if '"' in name and "'" in name:
        raise ValueError("A folder name can hold one kind of quote, not both.")
    path = _dir(root) / _safe_folder(name)
    path.mkdir(parents=True, exist_ok=True)
    return path


def move(rel: str | Path, folder: str, root: str | Path = ".") -> Path:
    """Move a figure into ``folder``, which is created if it does not exist."""
    source = Path(root) / rel
    if not source.is_file():
        raise ValueError(f"{rel} is not a figure that exists.")
    target = _dir(root) / _safe_folder(folder)
    target.mkdir(parents=True, exist_ok=True)
    destination = target / source.name
    if destination == source:
        return source
    if destination.exists():
        destination = _free_path(target, source.stem)
    source.rename(destination)
    return destination


def remove(rel: str | Path, root: str | Path = ".") -> None:
    """Delete a figure, or an empty folder.

    **A folder with figures in it is refused**, rather than removed with its
    contents. `cli/remove_spec` makes the same call for the same reason: a delete
    whose blast radius is larger than the thing you clicked is how work
    disappears, and the way through is to empty it yourself.
    """
    path = Path(root) / rel
    if path.is_dir():
        if any(path.iterdir()):
            raise ValueError(f"{rel} is not empty. Move or delete what is in it first.")
        path.rmdir()
        return
    path.unlink(missing_ok=True)


def contents(folder: str, root: str | Path = ".") -> list[str]:
    """The figures under a gallery folder, at any depth, as repo-relative paths.

    What a delete would take with it, computed before anything is removed and
    handed to whoever has to say so: `cli/import_data`'s ``plan()`` shape, for
    the same reason. Empty for a folder that does not exist.
    """
    base = _dir(root) / _safe_folder(folder)
    if not base.is_dir():
        return []
    return sorted(relative(p, root) for p in base.rglob(f"*{SUFFIX}") if p.is_file())


def remove_folder(folder: str, root: str | Path = ".", *, with_contents: bool = False) -> list[str]:
    """Delete a gallery folder. Returns the figures that went with it.

    **Refused while it holds figures unless the caller says so**, and the caller
    is expected to have asked a person first: `remove` makes the same call for
    an empty-or-nothing delete, and this is the one place in the gallery a single
    press can take more than one thing. What it returns is what the window needs
    next, because a tab drawing a picture the gallery says is gone is the
    artifact-in-two-places problem the other way round.

    The top level is not a folder anybody can delete. ``_safe_folder`` turns an
    empty name into the gallery root, so that case is refused by name rather
    than found out about afterwards.
    """
    rel = _safe_folder(folder)
    if not rel.parts:
        raise ValueError("The gallery's top level is not a folder you can delete.")
    path = _dir(root) / rel
    if not path.is_dir():
        raise ValueError(f"{folder} is not a folder in the gallery.")
    inside = contents(folder, root)
    if inside and not with_contents:
        n = len(inside)
        raise ValueError(
            f"{folder} holds {n} figure{'s' if n != 1 else ''}. Move or delete them first."
        )
    shutil.rmtree(path)
    return inside


def slug(name: str) -> str:
    """A filename derived from the figure's name, as `findings.slug` derives one.

    Derived rather than asked for: the tab name is already the chart's identity
    everywhere else, and a second name to get wrong buys nothing.
    """
    text = re.sub(r"[^a-z0-9]+", "-", (name or "figure").lower()).strip("-")
    return (text or "figure")[:60]


def _dir(root: str | Path) -> Path:
    return Path(root) / FIGURES_DIR


def _safe_folder(folder: str) -> Path:
    """A folder path that cannot climb out of the gallery.

    ``..`` and absolute paths are dropped rather than refused: the field is a
    place to type a folder name, and the interesting failure is a typo rather
    than an attack. What matters is that no typo can write outside `figures/`.
    """
    parts = [p for p in Path((folder or "").strip()).parts if p not in ("..", "/", "\\")]
    return Path(*parts) if parts else Path()


def _free_path(directory: Path, stem: str) -> Path:
    """``stem.json``, or ``stem-2.json`` — never an overwrite.

    Same rule as `findings._free_path`: two figures of the same name are two
    things somebody kept, and silently replacing the first is the one outcome
    nobody wants from a Save button.
    """
    path = directory / f"{stem}{SUFFIX}"
    n = 2
    while path.exists():
        path = directory / f"{stem}-{n}{SUFFIX}"
        n += 1
    return path


# --- charts drawn where no window could take them ----------------------------

#: Under the catalog directory, not beside `figures/`. **A drawn chart is not a
#: figure**: nobody chose to keep it. It is here because the process that drew it
#: was not the window (`cli/serve.py`), so the rows had nowhere to go but disk,
#: and the window picks it up from here as the unsaved chart it would have been
#: had the two been one process. Keeping it writes a figure and removes this
#: file, so one picture is never two artifacts (`VISUALIZATION.md` §6.4), and
#: discarding it removes this file too. Nothing is committed from `.portia/`.
DRAWN_DIR = "drawn"

#: A render failure the window reported for a stashed chart, waiting beside it
#: for the process that drew it to read (`agent/drawn.collect_failures`).
FAILED_SUFFIX = ".failed"


def stash(chart: dict, portia_dir: str | Path) -> Path:
    """Write a drawn chart where a window will find it. **Reusing a name replaces.**

    `_free_path` is deliberately not used: the tab name is the chart's identity
    (§3.3) and a corrected chart drawn under the same name is the same chart, so
    a second file would be the old picture surviving its own correction.
    """
    directory = Path(portia_dir) / DRAWN_DIR
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{slug(chart['tab'])}{SUFFIX}"
    path.with_suffix(FAILED_SUFFIX).unlink(missing_ok=True)
    _write(path, _doc(chart))
    return path


def stashed(portia_dir: str | Path) -> list[dict]:
    """Every stashed chart, oldest first, each with its ``path`` and ``mtime_ns``."""
    out = []
    for path in (Path(portia_dir) / DRAWN_DIR).glob(f"*{SUFFIX}"):
        try:
            doc = load(path)
            doc["mtime_ns"] = path.stat().st_mtime_ns
        except (OSError, ValueError):
            continue
        doc["path"] = str(path)
        out.append(doc)
    return sorted(out, key=lambda doc: doc["mtime_ns"])


def unstash(name: str, portia_dir: str | Path) -> None:
    """The chart was kept or thrown away: its stash goes, and any failure with it."""
    path = Path(portia_dir) / DRAWN_DIR / f"{slug(name)}{SUFFIX}"
    path.unlink(missing_ok=True)
    path.with_suffix(FAILED_SUFFIX).unlink(missing_ok=True)


def stash_failure(name: str, message: str, portia_dir: str | Path) -> None:
    """The window could not draw a stashed chart. Leave that where its author reads it."""
    path = Path(portia_dir) / DRAWN_DIR / f"{slug(name)}{FAILED_SUFFIX}"
    if path.parent.is_dir():
        path.write_text(
            json.dumps({"tab": name, "message": message}, ensure_ascii=False), encoding="utf-8"
        )


def take_stash_failures(portia_dir: str | Path) -> dict[str, str]:
    """Every failure left by a window since the last call, by tab. Taken, not read."""
    taken: dict[str, str] = {}
    for path in (Path(portia_dir) / DRAWN_DIR).glob(f"*{FAILED_SUFFIX}"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            taken[str(record["tab"])] = str(record.get("message") or "")
        except (OSError, ValueError, KeyError):
            pass
        path.unlink(missing_ok=True)
    return taken

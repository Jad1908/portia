"""The left pane's model: the project directory, filtered to what portia reads.

A directory and a classification in, a tree of nodes out. No NiceGUI and no
engine import, so the pane's *structure* is testable without a browser — the same
status `state.py` and `graph.py` have.

**This is a real disk tree, and it reverses an earlier decision.** V0's left pane
was six curated sections and `DESIGN.md` said in as many words that it was not a
file tree. The reasoning was that a curated view survives a big repo where a disk
walk does not. What it cost was the shape of the project: a spec in
``specs/staging/`` and a model in ``models/staging/`` appeared as two flat rows
with the same name, and nothing on screen said where either file actually was —
which is the one question you have when handing the pipeline to someone else.
Locking the app to six known folders also fixes what the agent is allowed to
produce, and the folders are not portia's to fix.

**The curation survives as a filter, not as a layout.** A file appears if portia
knows about it (it is in the catalog, or it is a spec, a compiled model, a written
output or a saved run) **or** if it is in a format `core.io` registers a reader
for. Everything else — a README, a notebook, a stray ``.py`` — stays hidden, so
the pane is still a view of the data and portia's artifacts rather than a project
explorer. A folder appears only if something under it survived that filter, which
is what stops an empty ``.venv``-shaped tree from being drawn.

Hidden directories are skipped, which includes ``.portia/`` itself. Its two
readable contents reach the pane another way: the brief is a pinned row at the
top and the turns are a pinned section at the foot (`artifacts.py`). Showing the
catalog's own YAML as editable files would invite hand-editing the thing the
copilot maintains.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from pathlib import Path

#: A directory. Its `children` is why it is in the tree at all — a folder with
#: nothing portia reads under it is not drawn.
FOLDER = "folder"

#: A file portia *could* read and has no catalog entry for. It is on disk in a
#: format the loader registers; nothing has profiled it. Shown because hiding it
#: would make "add data" the only way to discover a file that is already in the
#: repo, and marked because "portia knows this file" is exactly what it is not.
DATA = "unindexed"

#: Never walked. Dot-directories cover ``.git``, ``.venv`` and ``.portia``; the
#: two names are the ones that are neither hidden nor ever interesting.
SKIP_NAMES = frozenset({"__pycache__", "node_modules"})


@dataclass(frozen=True)
class Node:
    """One row of the tree: a folder, a known artifact, or a readable file.

    ``rel`` is the identity for *the tree* — a repo-relative POSIX path, which is
    what a folder's open/closed state is remembered against. ``ident`` is the
    identity for *the app*: the panes address a source by its catalog name and a
    saved run by its filename, and this is what a click hands them.
    """

    rel: str
    name: str
    kind: str
    ident: str = ""
    children: tuple[Node, ...] = ()

    @property
    def is_folder(self) -> bool:
        return self.kind == FOLDER


def build(
    root: str | Path,
    known: Mapping[str, tuple[str, str]],
    readable: Collection[str],
) -> tuple[Node, ...]:
    """Walk ``root`` and keep what portia can say something about.

    ``known`` maps a repo-relative path to ``(kind, ident)`` — the classification
    the engine supplies (`engine.known_files`). ``readable`` is the loader's
    registered suffixes, which is what makes an un-indexed CSV visible without
    hard-coding a format list here.
    """
    root = Path(root)
    suffixes = frozenset(s.lower() for s in readable)
    return _walk(root, root, dict(known), suffixes)


def _walk(
    directory: Path,
    root: Path,
    known: dict[str, tuple[str, str]],
    readable: frozenset[str],
) -> tuple[Node, ...]:
    folders: list[Node] = []
    files: list[Node] = []
    for entry in _listdir(directory):
        rel = entry.relative_to(root).as_posix()
        # `is_dir()` follows symlinks, and a link pointing at an ancestor is a
        # walk that does not terminate. Not drawing linked directories is the
        # cheap answer; nothing in a portia project needs one.
        if entry.is_dir():
            if entry.is_symlink() or _skipped(entry.name):
                continue
            children = _walk(entry, root, known, readable)
            if children:
                folders.append(Node(rel=rel, name=entry.name, kind=FOLDER, children=children))
        elif rel in known:
            kind, ident = known[rel]
            files.append(Node(rel=rel, name=entry.name, kind=kind, ident=ident))
        elif entry.suffix.lower() in readable:
            files.append(Node(rel=rel, name=entry.name, kind=DATA, ident=rel))
    # Folders first, then files — the convention every file browser uses, and the
    # only ordering in this pane. Nothing here sorts by anything measured
    # (`DESIGN.md` → colour and prominence communicate kind, never rank).
    return (*folders, *files)


def _skipped(name: str) -> bool:
    return name.startswith(".") or name in SKIP_NAMES


def _listdir(directory: Path) -> list[Path]:
    """Alphabetical, case-insensitive, and an unreadable directory is empty.

    A folder the process cannot read is one row that fails to draw, not a reason
    for the left pane to fail — the same call the spec loader makes.
    """
    try:
        return sorted(directory.iterdir(), key=lambda p: p.name.lower())
    except OSError:
        return []


def folders(nodes: tuple[Node, ...]) -> list[str]:
    """Every folder path in the tree, depth-first. For tests and for seeding."""
    found: list[str] = []
    for node in nodes:
        if node.is_folder:
            found.append(node.rel)
            found += folders(node.children)
    return found

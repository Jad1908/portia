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

**The readable half of that filter is scoped to the project's data folder**
(``data_root``, set on the add-data screen and stored as ``data_dir`` in
``project.yaml``). A repo of any size holds CSVs that are not this project's
data — a test fixture, an export someone left in ``notebooks/`` — and drawing
them all was the filter's one remaining way of being wrong at scale, which is
the case `VISION.md` flags as untested. Scoping costs nothing where the setting
is unset: ``data_root=None`` means the whole repo, which is what every project
written before the field did.

**Only the readable half.** A file portia *knows* — a spec, a compiled model, an
output, a saved run, an indexed source — is drawn wherever it lives, because
those are portia's own artifacts rather than your data, and `VISION.md`'s
no-terminal audit maps four of its rows onto reading them in this pane. Scoping
them to a data folder would take ``models/*.sql`` off screen the moment someone
pointed the picker at ``data/``.

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


#: The two levels above a table in a warehouse. Their own kinds because they are
#: their own icons: a database is not a folder, and drawing it as one would say
#: a click opens a directory (`docs/CONNECTOR.md` §2.13).
DATABASE = "database"
SCHEMA = "schema"


def warehouse(
    sources: Mapping[str, dict],
    models: Mapping[str, dict] | None = None,
    *,
    table_kind: str = "source",
    model_kind: str = "model",
) -> tuple[Node, ...]:
    """The warehouse as database → schema → table, from the catalog: the scoped
    tables, and **the tables portia built there**.

    Built from the source entries that carry a ``remote`` block and the model
    entries that carry a ``table`` (`catalog.index_model`, `CONNECTOR.md`
    §2.7.2), so it draws what the project reads and what it wrote and nothing
    else — the picker is where the rest of the warehouse is browsed, as
    `data_dir` scopes the file tree. ``rel`` is the qualified prefix (``DB``,
    ``DB.SCHEMA``, ``DB.SCHEMA.TABLE``), which is what an open/closed state is
    remembered against; ``ident`` on a row is the catalog name a click hands
    the panes. Kind, never rank: a built table is a *model* row under the
    schema it landed in, and the glyph says which it is. Sorted by name at
    every level and by nothing measured.
    """
    by_db: dict[str, dict[str, list[Node]]] = {}
    for name, entry in sources.items():
        remote = entry.get("remote")
        if not remote:
            continue
        db, schema, table = remote["database"], remote["schema"], remote["table"]
        by_db.setdefault(db, {}).setdefault(schema, []).append(
            Node(rel=f"{db}.{schema}.{table}", name=table, kind=table_kind, ident=name)
        )
    for name, entry in (models or {}).items():
        parts = str(entry.get("table") or "").split(".")
        if len(parts) != 3:
            continue
        db, schema, table = parts
        by_db.setdefault(db, {}).setdefault(schema, []).append(
            Node(rel=f"{db}.{schema}.{table}", name=table, kind=model_kind, ident=name)
        )
    return tuple(
        Node(
            rel=db,
            name=db,
            kind=DATABASE,
            children=tuple(
                Node(
                    rel=f"{db}.{schema}",
                    name=schema,
                    kind=SCHEMA,
                    children=tuple(sorted(tables, key=lambda n: n.name)),
                )
                for schema, tables in sorted(schemas.items())
            ),
        )
        for db, schemas in sorted(by_db.items())
    )


def build(
    root: str | Path,
    known: Mapping[str, tuple[str, str]],
    readable: Collection[str],
    data_root: str | None = None,
) -> tuple[Node, ...]:
    """The project's tree: what portia knows, plus what it could read under the data folder.

    ``known`` maps a repo-relative path to ``(kind, ident)`` — the classification
    the engine supplies (`engine.known_files`). ``readable`` is the loader's
    registered suffixes, which is what makes an un-indexed CSV visible without
    hard-coding a format list here.

    ``data_root`` is the repo-relative folder the project's data lives in. Only
    files under it are kept for being *readable*; a file portia already knows is
    kept wherever it is. ``None`` — the state of a project that has never been
    told — means the whole repo, as it did before the field existed.

    **Only the data folder is walked** *(2026-09-21)*. This walked the whole
    project and kept what matched, on the event loop, at every redraw of the
    left pane. The first project opened inside a real work repository is what
    that cost: 240,000 files is four seconds a draw, a warehouse project walks
    them to keep **nothing** (it passes no readable suffix), and *Open the
    workspace* during an indexing run held the loop until the browser dropped
    its connection. A known file needs no walk to be found, because its path is
    the key it arrives under, so the two halves are read apart: known paths are
    placed directly, and the disk is searched under ``data_root`` only. With no
    ``data_root`` and a readable suffix that is still the whole repo, which is
    what *anywhere* means; setting the folder is the way out, and a warehouse
    project never pays it.
    """
    root = Path(root)
    suffixes = frozenset(s.lower() for s in readable)
    scope = _scope(data_root)
    files: dict[str, tuple[str, str]] = {}
    if suffixes and (scope is None or _reachable(root, scope)):
        start = root / scope if scope else root
        found = _data_files(start, suffixes) if start.is_dir() else _one_file(start, suffixes)
        for path in found:
            rel = path.relative_to(root).as_posix()
            files[rel] = (DATA, rel)
    for rel, classified in known.items():
        if _reachable(root, rel) and (root / rel).is_file():
            files[rel] = classified
    return _assemble(files)


def _scope(data_root: str | None) -> str | None:
    """The data folder as a path prefix, or ``None`` for "anywhere in the repo".

    ``""`` and ``"."`` are the project root, which is every path — the same
    answer as unset, and worth collapsing here rather than in three call sites.
    """
    cleaned = (data_root or "").strip().strip("/")
    return None if cleaned in ("", ".") else cleaned


def _one_file(path: Path, readable: frozenset[str]) -> tuple[Path, ...]:
    """A data folder that is one file: the old walk matched ``rel == scope`` too."""
    return (path,) if path.is_file() and path.suffix.lower() in readable else ()


def _reachable(root: Path, rel: str) -> bool:
    """Whether the walk this replaced would have arrived at ``rel``.

    It never entered a hidden directory, the two skipped names, or a linked
    directory (`is_dir()` follows symlinks, and a link pointing at an ancestor
    is a walk that does not terminate). A known file under one of those stayed
    off the pane, and still does.
    """
    parts = Path(rel).parts
    at = root
    for part in parts[:-1]:
        at = at / part
        if _skipped(part) or at.is_symlink():
            return False
    return bool(parts)


def _assemble(files: Mapping[str, tuple[str, str]]) -> tuple[Node, ...]:
    """Paths into nodes. A folder exists because something under it was kept.

    Folders first, then files, each alphabetical without regard to case — the
    convention every file browser uses, and the only ordering in this pane.
    Nothing here sorts by anything measured (`DESIGN.md` → colour and prominence
    communicate kind, never rank).
    """
    nested: dict = {}
    for rel in files:
        at = nested
        for part in Path(rel).parts[:-1]:
            at = at.setdefault(part, {})
        at[Path(rel).name] = rel
    return _nodes(nested, "", files)


def _nodes(level: dict, prefix: str, files: Mapping[str, tuple[str, str]]) -> tuple[Node, ...]:
    folders: list[Node] = []
    leaves: list[Node] = []
    for name in sorted(level, key=str.lower):
        below = level[name]
        rel = f"{prefix}{name}"
        if isinstance(below, dict):
            children = _nodes(below, f"{rel}/", files)
            folders.append(Node(rel=rel, name=name, kind=FOLDER, children=children))
        else:
            kind, ident = files[rel]
            leaves.append(Node(rel=rel, name=name, kind=kind, ident=ident))
    return (*folders, *leaves)


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


# --- the folder picker ------------------------------------------------------
#
# The add-data screen asks the same directory two different questions: *which
# folder here holds data* (to pick one) and *which files are in it* (to profile
# them). Both are the walk above with the classification taken off, so they live
# beside it rather than growing a second walker in `screens.py` that would drift
# from this one's rules about symlinks and dot-directories.


@dataclass(frozen=True)
class Choice:
    """One folder offered by the picker, and how much data is under it.

    ``files`` is a **count of files**, not a measurement of anything in them —
    it is what makes "which of these six folders is the data" answerable without
    clicking into all six, and it is the only number this screen shows.

    **Zero is a real answer and is offered** (2026-08-06). Folders with nothing
    readable under them used to be left out entirely, which made the list read
    as the whole directory and quietly wasn't — so a folder you knew was there
    and could not see was a bug you had to go to a terminal to disprove.
    Showing them, quietly, says *this exists and has nothing portia can read*,
    which is the answer to the question you were actually asking.
    """

    rel: str
    name: str
    files: int

    @property
    def has_data(self) -> bool:
        return self.files > 0


def choices(root: str | Path, at: str, readable: Collection[str]) -> tuple[Choice, ...]:
    """Every sub-folder of ``root/at``, and how much readable data is under each.

    The count is **recursive** — ``raw/`` holding nothing but
    ``raw/2024/orders.csv`` is still the answer someone is looking for, so a
    folder is judged by everything beneath it rather than by its own listing.

    Folders with a count of zero come back too, and it is the caller's job to
    draw them quietly (`Choice.has_data`). Omitting them made the picker look
    like a complete directory listing while silently not being one.
    """
    root = Path(root)
    suffixes = frozenset(s.lower() for s in readable)
    scope = _scope(at)
    found = []
    for entry in _listdir(root / scope if scope else root):
        if not entry.is_dir() or entry.is_symlink() or _skipped(entry.name):
            continue
        found.append(
            Choice(
                rel=entry.relative_to(root).as_posix(),
                name=entry.name,
                files=len(_data_files(entry, suffixes)),
            )
        )
    return tuple(found)


def data_files(directory: str | Path, readable: Collection[str]) -> tuple[Path, ...]:
    """Every readable data file under ``directory``, at any depth.

    Recursive, unlike `core.io.find_data_files`, which lists one directory. That
    is not a redundancy to collapse: a *destination* to import into is one
    folder, and a *scope* to profile is a folder and everything beneath it.
    """
    return _data_files(Path(directory), frozenset(s.lower() for s in readable))


def _data_files(directory: Path, readable: frozenset[str]) -> tuple[Path, ...]:
    found: list[Path] = []
    for entry in _listdir(directory):
        if entry.is_dir():
            if not (entry.is_symlink() or _skipped(entry.name)):
                found += _data_files(entry, readable)
        elif entry.suffix.lower() in readable:
            found.append(entry)
    return tuple(found)


def crumbs(at: str) -> tuple[tuple[str, str], ...]:
    """A path as ``(rel, name)`` pairs, root first — the picker's way back up.

    The root is ``("", "")`` and gets its name from the caller, which is the
    project's own directory name: the picker is rooted at the repo and saying so
    in the trail is what makes "up one more" a place rather than a dead end.
    """
    trail = [("", "")]
    scope = _scope(at)
    if scope:
        parts = scope.split("/")
        trail += [("/".join(parts[: i + 1]), part) for i, part in enumerate(parts)]
    return tuple(trail)


def folders(nodes: tuple[Node, ...]) -> list[str]:
    """Every folder path in the tree, depth-first. For tests and for seeding."""
    found: list[str] = []
    for node in nodes:
        if node.is_folder:
            found.append(node.rel)
            found += folders(node.children)
    return found

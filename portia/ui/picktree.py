"""The add-data pickers' model: a tree you tick, and what a container says about its leaves.

Files under the data folder and tables in a warehouse are picked in the same
shape: containers that open, leaves that tick, and a container whose box says
whether **all, some or none** of what is under it is ticked. No NiceGUI and no
engine import, the status `tree.py`, `state.py` and `graph.py` have, so the
roll-up is tested without a browser.

**It replaced a browser that drew one level at a time** *(2026-09-18,
`CONNECTOR.md` §2.5.1)*. Ticks survived a trip into another schema and nothing
drew them on the way back: the only trace of a tick made elsewhere was the
number on the Index button. And only a table row carried a box, so a schema of
forty tables was forty clicks.

Three rules the functions here hold, because each is a way a picker can say
something false:

- **A container nobody has listed is not an empty one.** Its ``children`` is
  ``None``, its tally is ``complete=False``, and it can never read *all*:
  a database with one schema listed and ticked is *some*, because nothing
  knows what the other schemas hold.
- **A leaf that is already in is counted apart and never ticked.** An indexed
  file and a scoped table keep their rows and carry a ``note``; they are not
  part of *n of m*, or a schema with everything already in scope would read as
  a schema with nothing ticked.
- **A filter hides rows and never ticks.** `shown` prunes what is drawn, a
  container's box then speaks for the rows on screen, and its count still
  speaks for everything under it, so a tick hidden by the filter stays in the
  number beside the container that holds it.

Counts here are numbers of things. They size nothing and order nothing
(`DESIGN.md`: kind, never rank); every level is sorted by name.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass

from portia.ui.tree import DATABASE, FOLDER, SCHEMA

#: The two leaf kinds. The container kinds are `tree.py`'s, so a database is one
#: glyph in the picker and in the left pane.
FILE = "file"
TABLE = "table"

#: What a container's box says.
ALL = "all"
SOME = "some"
NONE = "none"


@dataclass(frozen=True)
class Item:
    """One row: a container that opens, or a leaf that ticks.

    ``key`` is what a tick is remembered against: a repo-relative path, or a
    qualified table name. ``children`` is ``None`` on a container nobody has
    listed, which is a different statement from ``()``, listed and empty.
    ``note`` marks a leaf that is already in and says why; ``detail`` is the
    leaf's own kind where it is worth a word (a view).
    """

    key: str
    name: str
    kind: str
    children: tuple[Item, ...] | None = ()
    note: str = ""
    detail: str = ""

    @property
    def leaf(self) -> bool:
        return self.kind in (FILE, TABLE)

    @property
    def fixed(self) -> bool:
        return bool(self.note)

    @property
    def listed(self) -> bool:
        return self.children is not None


@dataclass(frozen=True)
class Tally:
    """What is ticked under a container, as counts.

    ``of`` is the leaves that *can* be ticked, ``fixed`` the ones already in,
    and ``complete`` is whether every container below has been listed. An
    incomplete tally has no total worth printing.
    """

    ticked: int = 0
    of: int = 0
    fixed: int = 0
    complete: bool = True

    @property
    def state(self) -> str:
        if not self.ticked:
            return NONE
        return ALL if self.complete and self.ticked == self.of else SOME

    def __add__(self, other: Tally) -> Tally:
        return Tally(
            self.ticked + other.ticked,
            self.of + other.of,
            self.fixed + other.fixed,
            self.complete and other.complete,
        )


def tally(item: Item, ticks: Collection[str]) -> Tally:
    """The roll-up for one row, over everything under it that has been listed."""
    if item.leaf:
        if item.fixed:
            return Tally(fixed=1)
        return Tally(ticked=int(item.key in ticks), of=1)
    if item.children is None:
        return Tally(complete=False)
    total = Tally()
    for child in item.children:
        total = total + tally(child, ticks)
    return total


def leaves(items: Iterable[Item]) -> list[str]:
    """Every leaf that can be ticked under these rows, in drawn order."""
    found: list[str] = []
    for item in items:
        if item.leaf:
            if not item.fixed:
                found.append(item.key)
        elif item.children:
            found.extend(leaves(item.children))
    return found


def unlisted(item: Item) -> list[str]:
    """The containers at or under this row that nobody has listed yet.

    What ticking a container has to list first: a tick is a statement about
    tables, and an unlisted schema has none to make it about.
    """
    if item.leaf:
        return []
    if item.children is None:
        return [item.key]
    return [key for child in item.children for key in unlisted(child)]


def find(items: Iterable[Item], key: str) -> Item | None:
    for item in items:
        if item.key == key:
            return item
        if item.children:
            found = find(item.children, key)
            if found is not None:
                return found
    return None


def shown(items: Sequence[Item], text: str) -> tuple[Item, ...]:
    """The rows a filter leaves, as a tree of the same shape.

    A leaf stays when its name contains the text, whatever the case. A
    container whose own name matches stays whole; otherwise it stays with the
    children that survived. **An unlisted container always stays**: hiding it
    would say nothing under it matches, and nothing has looked.
    """
    needle = text.strip().lower()
    if not needle:
        return tuple(items)
    kept: list[Item] = []
    for item in items:
        if needle in item.name.lower() or (not item.leaf and item.children is None):
            kept.append(item)
        elif not item.leaf:
            children = shown(item.children or (), needle)
            if children:
                kept.append(_with_children(item, children))
    return tuple(kept)


def _with_children(item: Item, children: tuple[Item, ...]) -> Item:
    return Item(item.key, item.name, item.kind, children, item.note, item.detail)


def containers(keys: Iterable[str], sep: str) -> int:
    """How many containers a set of ticks is spread over: *14 tables across 3 schemas*."""
    return len({key.rsplit(sep, 1)[0] if sep in key else "" for key in keys})


# --- the two trees ------------------------------------------------------------


def from_paths(files: Iterable[str], base: str, notes: Mapping[str, str]) -> tuple[Item, ...]:
    """Files under the data folder as folders and files, keyed by repo-relative path.

    ``base`` is the data folder (``"."`` for the project root) and is not a row:
    the chosen-folder card above the tree already names it. Folders first, then
    files, each by name whatever its case: every real extract has an upper-case
    name in it, and ``D.csv`` sorting ahead of ``c.csv`` reads as two lists.
    """
    prefix = "" if base in ("", ".") else base.rstrip("/") + "/"
    root: dict = {}
    for rel in files:
        inner = rel[len(prefix) :] if prefix and rel.startswith(prefix) else rel
        parts = inner.split("/")
        level = root
        for part in parts[:-1]:
            level = level.setdefault(("dir", part), {})
        level[("file", parts[-1])] = rel
    return _from_level(root, prefix.rstrip("/"), notes)


def _from_level(level: dict, at: str, notes: Mapping[str, str]) -> tuple[Item, ...]:
    folders, files = [], []
    for (kind, name), value in sorted(
        level.items(), key=lambda pair: (pair[0][1].lower(), pair[0][1])
    ):
        if kind == "dir":
            key = f"{at}/{name}" if at else name
            folders.append(Item(key, name, FOLDER, _from_level(value, key, notes)))
        else:
            files.append(Item(value, name, FILE, note=notes.get(value, "")))
    return (*folders, *files)


def from_listing(listing: Mapping[str, Sequence], notes: Mapping[str, str]) -> tuple[Item, ...]:
    """A warehouse as database → schema → table, from what has been listed so far.

    ``listing`` is the picker's cache: ``""`` holds the databases, ``"DB"`` its
    schemas, ``"DB.SCHEMA"`` its ``(table, kind)`` pairs. A place with no entry
    has not been asked about, and its row says so by having no children at all.
    """
    databases = []
    for database in sorted(listing.get("") or ()):
        schemas = listing.get(database)
        databases.append(
            Item(
                database,
                database,
                DATABASE,
                None
                if schemas is None
                else tuple(_schema(database, name, listing, notes) for name in sorted(schemas)),
            )
        )
    return tuple(databases)


def _schema(database: str, name: str, listing: Mapping[str, Sequence], notes) -> Item:
    key = f"{database}.{name}"
    tables = listing.get(key)
    if tables is None:
        return Item(key, name, SCHEMA, None)
    return Item(
        key,
        name,
        SCHEMA,
        tuple(
            Item(
                f"{key}.{table}",
                table,
                TABLE,
                note=notes.get(f"{key}.{table}", ""),
                detail="" if kind == "table" else kind,
            )
            for table, kind in sorted(tables)
        ),
    )

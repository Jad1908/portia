"""Whether the tables a tool call names changed since portia indexed them, said first.

**Stale is loud** *(2026-10-13, `sandbox/limits/REVAMP_SPEC.md`, rule 6)*. The
catalog's facts about a table (its row count, its columns, their flags and
profile) are what it looked like when it was indexed. A file edited since, or
a warehouse table reloaded, leaves every one of those describing something
that is no longer there, and nothing in an answer said so: the window's left
pane drew a stale dot, and the agent, which never sees the pane, read the old
facts as current.

So every answer whose arguments name a table that changed starts with a
``stale`` list (`tools._evidence`, one place for every tool, like the size
guard). The arguments are read generically (:data:`NAMING`): a source, a model,
a ``<spec>#<step>``, or a spec. **A model is as fresh as what it is built
from**: a model built locally keeps no change marker of its own
(`catalog.freshness`), so naming one checks every source its spec reads,
through the models it reads (`spec.sources_read`), and a source reached that
way says which named table led to it (``read_by``).

`catalog.freshness` answers in codes; this module is where they become the
sentences the model reads, out of `prompts/notices/`, and where a code becomes
the ``true``/``false``/``null`` the reserved tables carry (:func:`status`).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from portia import catalog, spec
from portia.agent import prompts
from portia.core import cancel
from portia.core import dialect as dialects
from portia.core.io import connect
from portia.core.present import moment

#: The arguments that name one table, by key: a source, a model, or
#: ``<spec>#<step id>``. ``to`` is `graph_lookup`'s other end, ``table`` its first.
NAMING = ("source", "table", "to", "left", "right")

#: The arguments that name several tables, as a list.
NAMING_LISTS = ("inputs",)

#: The arguments that name a spec, by its model name or its path.
NAMING_SPECS = ("spec", "spec_path")

#: The argument holding column pairs, each naming two tables (`measure_overlaps`).
PAIRS = "pairs"

#: A table produced by a step: ``<spec>#<step id>`` (`handlers.STEP_REF`).
STEP_REF = "#"

#: What a notice calls the tables that led to a source the caller did not name.
READ_BY = "read_by"


def named(args: dict[str, Any]) -> list[tuple[str, bool]]:
    """Every table and spec a call's arguments name, in order: ``(name, is_a_spec)``.

    A ``<spec>#<step id>`` is its spec: the step's table is built from what the
    spec reads.
    """
    found: list[tuple[str, bool]] = []

    def add(value: Any, *, is_spec: bool = False) -> None:
        if not isinstance(value, str) or not value.strip():
            return
        text = value.strip()
        if STEP_REF in text:
            text, is_spec = text.partition(STEP_REF)[0], True
        if (text, is_spec) not in found:
            found.append((text, is_spec))

    for key in NAMING:
        add(args.get(key))
    for key in NAMING_LISTS:
        for value in args.get(key) or []:
            add(value)
    for pair in args.get(PAIRS) or []:
        if isinstance(pair, dict):
            add(pair.get("left"))
            add(pair.get("right"))
    for key in NAMING_SPECS:
        add(args.get(key), is_spec=True)
    return found


def notices(args: dict[str, Any], portia_dir: str | Path = catalog.DEFAULT_DIR) -> list[dict]:
    """The ``stale`` list for a call with these arguments: one entry per table that changed.

    Empty when nothing it names changed, and when it names nothing portia
    indexed (a reserved table, an unknown name: the handler has already said
    what it thinks of those). A warehouse is asked through a connection opened
    only when a warehouse table is among them, and a recent answer is reused
    (`catalog.WAREHOUSE_FACTS_S`).
    """
    checks = _tables(args, Path(portia_dir))
    out = []
    con: Any = None
    for table, read_by in checks.items():
        marks = catalog.marks(table, portia_dir)
        if marks is None:
            continue
        if con is None and (marks.get(catalog.REMOTE) or marks.get("table")):
            con = connect()
        fresh = catalog.freshness(table, marks, portia_dir=portia_dir, con=con)
        if fresh is not None:
            out.append(word(fresh, read_by=read_by))
    return out


def _tables(args: dict[str, Any], d: Path) -> dict[str, list[str]]:
    """Each indexed table to check, with the named tables that led to it (empty if named itself).

    Names resolve as `handlers._known_name` resolves them: exactly, else one
    case-insensitive match among the sources and models. The specs are read
    only when a model or a spec is named.
    """
    wanted = named(args)
    if not wanted:
        return {}
    sources = catalog.source_names(d)
    root = catalog.project_root(d)
    models: dict[str, Path] | None = None
    reads: dict[str, list[str]] | None = None
    out: dict[str, list[str]] = {}
    direct: set[str] = set()

    def check(table: str, via: str | None = None) -> None:
        if via is None:
            direct.add(table)
            out[table] = []
        elif table not in direct and via not in out.setdefault(table, []):
            out[table].append(via)

    for name, is_spec in wanted:
        if is_spec:
            name = Path(name).stem
        elif name in sources or dialects.resolve_columns([name], sources)[0] in sources:
            check(dialects.resolve_columns([name], sources)[0])
            continue
        if models is None:
            models = spec.discover_specs(root)
        model = dialects.resolve_columns([name], models)[0]
        if model not in models:
            continue
        check(model)
        if reads is None:
            reads = spec.sources_read(models, base_dir=root)
        for source in reads.get(model, []):
            if source in sources:
                check(source, via=model)
    return out


def word(fresh: dict, *, read_by: list[str] | None = None) -> dict:
    """`catalog.freshness`'s answer as the model reads it: what changed, and what to do.

    ``{"source", "changed": <sentence>, "do": <sentence>}`` for a change, and
    ``{"source", "changed": None, "unknown": True, "why": <sentence>}`` when
    nothing can tell. ``read_by`` names the tables the call named that led
    here, when the call did not name this one itself.
    """
    code = fresh.get("changed")
    at = moment(fresh.get("indexed"))
    out: dict[str, Any] = {"source": fresh.get("source")}
    if code is None:
        if fresh.get("why") == catalog.NO_MARKER:
            why = prompts.notice("stale_no_marker")
        else:
            why = prompts.notice("stale_unchecked")
        out.update({"changed": None, "unknown": True, "why": why})
    elif code == catalog.FILE_CHANGED:
        was, now = fresh.get("was") or {}, fresh.get("now") or {}
        out["changed"] = prompts.notice(
            "stale_file_changed",
            path=fresh.get("path"),
            indexed=at,
            modified=moment(now.get("mtime")),
            size_was=_bytes(was.get("size")),
            size_now=_bytes(now.get("size")),
        )
        out["do"] = prompts.notice("stale_do")
    elif code == catalog.FILE_GONE:
        out["changed"] = prompts.notice("stale_file_gone", path=fresh.get("path"), indexed=at)
        out["do"] = prompts.notice("stale_do_gone")
    elif code == catalog.TABLE_GONE:
        out["changed"] = prompts.notice("stale_table_gone", table=fresh.get("table"), indexed=at)
        out["do"] = prompts.notice("stale_do_gone")
    else:
        out["changed"] = prompts.notice("stale_table_changed", table=fresh.get("table"), indexed=at)
        out["do"] = prompts.notice("stale_do")
    if read_by:
        out[READ_BY] = list(read_by)
    return out


def _bytes(n: Any) -> str:
    """A file's size to the byte: a change of a few bytes is a change."""
    return f"{n:,} bytes" if isinstance(n, int) and not isinstance(n, bool) else "an unknown size"


def status(fresh: dict | None) -> bool | None:
    """`catalog.freshness`'s answer as a column holds it: changed, not changed, or cannot tell."""
    if fresh is None:
        return False
    return None if fresh.get("changed") is None else True


def statuses(
    portia_dir: str | Path, sources: dict[str, dict], models: dict[str, dict]
) -> dict[str, bool | None]:
    """:func:`status` for every source and model, **asking no warehouse**.

    For a table listed with every other one (`agent/reserved.py`'s
    ``portia_tables``), where a metadata query per warehouse table would be
    the whole cost of the answer: a warehouse table is ``True``/``False`` when
    the warehouse said within `catalog.WAREHOUSE_FACTS_S`, else ``None``. A
    file is a stat. A model is ``True`` when anything it is built from changed,
    else ``None`` when anything cannot tell, else ``False``.
    """
    d = Path(portia_dir)
    out: dict[str, bool | None] = {
        name: status(catalog.freshness(name, entry, portia_dir=d))
        for name, entry in sources.items()
    }
    if not models:
        return out
    root = catalog.project_root(d)
    specs = spec.discover_specs(root)
    try:
        reads = spec.sources_read(specs, base_dir=root)
    except cancel.Cancelled:
        raise
    except Exception:  # noqa: BLE001 - a spec that does not parse cannot be traced
        reads = {}
    for name, entry in models.items():
        own = status(catalog.freshness(name, entry, portia_dir=d))
        upstream = [out.get(source) for source in reads.get(name, []) if source in sources]
        known = [own, *upstream]
        out[name] = True if True in known else (None if None in known else False)
    return out

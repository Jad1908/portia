"""The context catalog — the durable record of *what the data is*.

Sibling of the spec (which records *what we did to it*). Lives in ``.portia/``:

- ``project.yaml`` — the global project context (your words), the folder inside
  the repo that holds this project's data (``data_dir``), defined groups
  (``{name, context, sources}`` — sources that belong together, plus the context
  they share), and a registry of indexed sources.
- ``sources/<name>.yaml`` — per source, two layers and a margin:
    * **Layer 1** ``summary`` — a short prose read of what this data is.
    * **Layer 2** ``columns`` — per column, a ``role`` slot plus the facts the
      checks found.
    * ``notes`` — what was learned about this table after it was read, one dated
      sentence at a time, appended and never rewritten (``set_interpretation``
      with ``note=``). The summary says what the table is; a note says what
      you have to know before building on it.

This is the agent's **memory**: at scale it never sees raw data, only this
context, so a downstream task/agent loads the catalog instead of re-profiling and
re-explaining. ``index_source`` auto-drafts ``summary`` from the facts (a plain
restatement, not a semantic read) and leaves every ``role`` empty; those are
placeholders until ``set_interpretation`` writes the real read — by the agent
(``portia/agent/``) or by you, editing the YAML directly.

**The update rule (facts vs judgment):** re-indexing *refreshes the deterministic
facts* but *preserves the prose and roles* — so your corrections are never
clobbered. Nothing here is schema-locked; it's plain, hand-editable YAML.
"""

from __future__ import annotations

import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from portia.checks import profiling
from portia.checks.profiling import profile_path
from portia.core import backend, cancel
from portia.core import dialect as dialects
from portia.core.io import relative
from portia.core.present import format_rate
from portia.core.table import Table

DEFAULT_DIR = ".portia"

#: How an auto-drafted summary announces itself. ``_auto_summary`` writes it and
#: :func:`is_interpreted` reads it, so "has anyone actually read this source yet"
#: is one fact rather than a marker string copied into every surface that asks.
AUTO_DRAFT_MARKER = "(auto-drafted from checks"

# Column flags worth calling out in the auto-drafted prose summary. Plain
# restatements of facts — not judgements.
_WATCHOUTS = {
    "high_null": "mostly null",
    "all_null": "empty",
    "numeric_stored_as_text": "numbers stored as text",
    "mixed_types": "mixed types",
    "constant": "constant value",
}


def project_root(portia_dir: str | Path = DEFAULT_DIR) -> Path:
    """The repo portia is plugged into — the parent of its own directory.

    Everything a project holds is relative to this: the specs, the compiled
    models, and every data file that may be indexed at all.
    """
    return Path(portia_dir).parent


def source_ref(data_path: str | Path, *, portia_dir: str | Path = DEFAULT_DIR) -> str:
    """A source's path as the catalog records it: **relative to the project root**.

    portia plugs into a repo that already holds the data, and the user picks what
    is in scope. A path outside that repo is refused here rather than warned about
    (`docs/PIPELINE.md` §2.7) — one rule, one place, so no surface can be lenient
    about it on its own.

    Two things this buys, and the second is the one that matters. A spec's
    ``sources:`` block becomes portable: an absolute path pins a spec to the
    laptop that wrote it, and a spec that only runs in one place is not a durable
    artifact. And ``.portia/`` never becomes the only thing that knows where the
    data is — the path is readable, relative, and reviewable in a diff.
    """
    root = project_root(portia_dir).resolve()
    target = Path(data_path).resolve()
    try:
        return relative(target, root)
    except ValueError:
        raise ValueError(
            f"{data_path} is outside this project ({root}). "
            f"portia indexes data already in the repo — to bring this file in:\n"
            f"  python -m portia.cli.import_data {data_path} --to <dir>"
        ) from None


#: A source entry's block saying it is a warehouse table rather than a file:
#: ``{database, schema, table, kind}``, with ``source`` above it holding the
#: qualified name (`docs/CONNECTOR.md` §2.6). Absent on every file source.
REMOTE = "remote"


def source_ref_of(entry: dict) -> Any:
    """What a spec's ``sources:`` block records for this entry.

    A file source is its path relative to the project, as it always was; a
    remote source is ``{table: DATABASE.SCHEMA.NAME}`` (`core/io.TABLE_REF`).
    The one place the two shapes meet, so `record_step`, `run_spec` and the
    app cannot spell a warehouse source three ways.
    """
    if entry.get(REMOTE):
        return {"table": str(entry["source"])}
    return str(entry["source"])


def init_project(project_context: str = "", *, portia_dir: str | Path = DEFAULT_DIR) -> Path:
    """Create (or update) ``.portia/project.yaml`` with the global project context."""
    d = Path(portia_dir)
    (d / "sources").mkdir(parents=True, exist_ok=True)
    proj = d / "project.yaml"
    data = _read(proj) if proj.exists() else {"project": "", "groups": [], "sources": {}}
    if project_context or not proj.exists():
        data["project"] = project_context
    _write(proj, data)
    return proj


def set_data_dir(rel: str, *, portia_dir: str | Path = DEFAULT_DIR) -> Path:
    """Record which folder inside the repo holds this project's data.

    **Scope, not location.** portia has always read data wherever it sits in the
    repo, and it still does — a source is recorded by its own path and nothing
    here moves or re-homes a file. What this answers is the other question, the
    one a repo of any size asks immediately: *of everything readable in here,
    which part is the data for this project?* The window's left pane draws
    un-indexed data files under this folder and nowhere else, and it is the
    default destination an import lands in.

    Empty means unset, which is the honest state of a project nobody has told —
    and it reads as "everything readable in the repo", the behaviour that
    preceded this field.

    Stored relative to the project root, like every other path portia writes, so
    the setting survives the project being cloned somewhere else.
    """
    d = Path(portia_dir)
    proj = d / "project.yaml"
    data = _read(proj) if proj.exists() else {"project": "", "groups": [], "sources": {}}
    data["data_dir"] = (rel or "").strip().strip("/")
    d.mkdir(parents=True, exist_ok=True)
    _write(proj, data)
    return proj


def index_source(data_path: str | Path, *, portia_dir: str | Path = DEFAULT_DIR) -> Path:
    """Profile a data source and write its catalog entry.

    Facts are (re)computed from the data; a pre-existing ``summary`` and any set
    ``role`` values are preserved. Registers the source in ``project.yaml``.

    **Nothing is copied.** The file stays where it is and is read in place; the
    entry records its path relative to the project root plus enough about the file
    to notice it changing (`docs/PIPELINE.md` §2.7). This used to ingest into
    ``.portia/store.duckdb`` first — a second, hidden copy of the user's data that
    the hot paths then went around.

    **The path must be inside the project.** :func:`source_ref` refuses anything
    else; bringing outside data in is a separate, deliberate act
    (``python -m portia.cli.import_data``).
    """
    data_path = Path(data_path)
    d = Path(portia_dir)
    (d / "sources").mkdir(parents=True, exist_ok=True)

    recorded = source_ref(data_path, portia_dir=d)
    name = source_name(recorded, portia_dir=d)
    # Profiled straight off the file — DuckDB-backed and memory-bounded, so the
    # profile that cost 1883 MB through pandas is a handful of aggregates over
    # the file itself. There is no ingested copy to profile instead (§2.7).
    profile = profile_path(data_path)

    src_file = d / "sources" / f"{name}.yaml"
    existing = _read(src_file) if src_file.exists() else None
    indexed = _counted(file_facts(data_path), profile["n_rows"])
    _write(src_file, _source_entry(recorded, profile, existing, indexed))
    _register(d, name)
    return src_file


#: dbt's separator for a name built from its parents, used only on collision.
NAME_SEP = "__"


def source_name(recorded: str, *, portia_dir: str | Path = DEFAULT_DIR) -> str:
    """The catalog name for a source at ``recorded`` — its own name unless that is taken.

    A source is named by its file's stem or its table's name, and two folders can
    both hold ``orders.csv`` as two schemas can both hold ``ORDERS``
    (`docs/CONNECTOR.md` §2.12). Locally the second used to overwrite the first's
    entry, silently. Now the short name is kept when it is free **or already
    this source's**, and on a collision the parent is prepended with dbt's
    double underscore: ``raw__orders``, ``SALES__ORDERS``, then one more parent.
    Only on collision, so the name the agent types is the short one in the
    common case.
    """
    d = Path(portia_dir)
    parts = _name_parts(recorded)
    # Compared without case: the entry is a file named after the source, and on
    # macOS `ORDERS.yaml` **is** `orders.yaml` — a warehouse table scoped into a
    # project holding `orders.csv` overwrote the file's entry (found driving the
    # window, 2026-09-04).
    #
    # **Names off the register, and an entry read only when it holds the name**
    # *(2026-10-08)*. This read the whole catalog for every source indexed, to
    # learn each entry's ``source``: on a project with two 1,579-column tables,
    # half a second of parsing per file by the end of a run, on the worker,
    # holding the lock the window's loop needs to answer a click. Whether a name
    # is taken is the register's answer; *whose* it is takes one entry.
    proj = d / "project.yaml"
    registered: dict[str, str] = {}
    for name in (_read(proj).get("sources") or {}) if proj.exists() else ():
        if (d / "sources" / f"{name}.yaml").exists():
            registered[name.lower()] = name
    # A built table's name is taken too (2026-09-07): scoping `STAGING.stg_x`
    # as a source used to write `sources/stg_x.yaml` beside `models/stg_x.yaml`,
    # and every lookup by name then found the source half first.
    models = {path.stem.lower(): path.stem for path in sorted((d / MODELS_DIR).glob("*.yaml"))}

    def holder(candidate: str) -> str | None:
        """Whose name ``candidate`` is: a source's recorded location, a model, or nobody's."""
        name = registered.get(candidate.lower())
        if name is not None:
            return _read(d / "sources" / f"{name}.yaml").get("source")
        model = models.get(candidate.lower())
        return None if model is None else f"model:{model}"

    for depth in range(1, len(parts) + 1):
        candidate = NAME_SEP.join(parts[-depth:])
        if holder(candidate) in (None, recorded):
            return candidate
    return NAME_SEP.join(parts)


def _name_parts(recorded: str) -> list[str]:
    """``raw/orders.csv`` → ``[raw, orders]``; ``SALES.PUBLIC.ORDERS`` → ``[SALES, PUBLIC, ORDERS]``."""
    if "/" in recorded or recorded.endswith(tuple(f".{s}" for s in _file_suffixes())):
        path = Path(recorded)
        return [*(p for p in path.parts[:-1] if p not in (".", "")), path.stem]
    return [p for p in recorded.split(".") if p]


def _file_suffixes() -> tuple[str, ...]:
    from portia.core.io import supported_suffixes

    return tuple(s.lstrip(".") for s in supported_suffixes())


# --- a warehouse table in scope (`docs/CONNECTOR.md` §2.5, §2.6) ----------------


def scope_table(qualified: str, con: Any, *, portia_dir: str | Path = DEFAULT_DIR) -> Path:
    """Bring one warehouse table into scope: metadata now, a profile only when asked.

    What the information schema says for nothing — columns and their types, the
    row count, the bytes, when the table last changed — and **no scan**. A
    profile of an 11-million-row table on someone else's meter is a decision,
    and it is made per table by :func:`profile_remote`, from the inspector or
    from the agent's ``profile_source``. The entry says which state it is in
    (``profiled``), so absent facts read as *not profiled* rather than as a
    profile with nothing in it.

    Same update rule as a file: a summary and the roles already written are
    preserved. Also appends the name to ``project.yaml``'s ``scope`` list,
    which is the remote ``data_dir``.
    """
    d = Path(portia_dir)
    (d / "sources").mkdir(parents=True, exist_ok=True)
    built = written_tables(d)
    if qualified in built:
        raise ValueError(
            f"{qualified} is a table this project builds (model {built[qualified]!r}); "
            "it is already known, and profiled from the model rather than scoped as a source"
        )
    name = source_name(qualified, portia_dir=d)
    facts = con.table_facts(qualified)
    remember_facts(qualified, facts)
    columns = con.columns(qualified)

    src_file = d / "sources" / f"{name}.yaml"
    existing = _read(src_file) if src_file.exists() else {}
    prev = {c["name"]: c for c in existing.get("columns") or []}
    database, schema, table = qualified.split(".")
    entry = {
        "source": qualified,
        REMOTE: {
            "database": database,
            "schema": schema,
            "table": table,
            "kind": facts.pop("kind", "table"),
        },
        "indexed": facts,
        # ``None`` until :func:`profile_remote` runs; then ``{at: …}``. A field,
        # because a silence reads as *nothing to report* (`SQL_LINEAGE.md` §9).
        "profiled": existing.get("profiled"),
        "summary": existing.get("summary") or _metadata_summary(facts, len(columns)),
        "columns": [
            {
                "name": col,
                "role": prev.get(col, {}).get("role"),
                "dtype": dtype,
                # Facts survive a re-scope: they were measured, and the
                # metadata refresh did not un-measure them.
                **{
                    k: v for k, v in prev.get(col, {}).items() if k not in ("name", "role", "dtype")
                },
            }
            for col, dtype in columns
        ],
        **_kept_notes(existing),
    }
    _write(src_file, entry)
    _register(d, name)
    add_scope([qualified], portia_dir=d)
    return src_file


def profile_remote(name: str, con: Any, *, portia_dir: str | Path = DEFAULT_DIR) -> dict:
    """Profile a warehouse table now — the opt-in scan — and write the facts back.

    Returns the full profile, which is what the agent's ``profile_source`` hands
    on: the catalog keeps the compact slice, the agent needs the whole picture.
    The metadata facts are refreshed at the same time, since the warehouse was
    asked anyway.

    **A model with a written table goes through here too** *(2026-09-07)*. On a
    warehouse a build indexes a model as metadata for the reason scoping does
    (`CONNECTOR.md` §2.6), and until this the opt-in half existed for sources
    only: the window's *Profile now* refused a model's name, and the agent's
    `profile_source` scanned the written table and threw the facts away, so
    seven tables built into BigQuery stayed at ``profiled: null`` however often
    they were read. Which folder the entry is in is a lookup, not a mode, as
    `set_interpretation` already treats it.
    """
    d = Path(portia_dir)
    src_file = d / "sources" / f"{name}.yaml"
    existing = _read(src_file) if src_file.exists() else {}
    if not existing:
        return _profile_model(name, con, portia_dir=d)
    if not existing.get(REMOTE):
        raise ValueError(f"{name!r} is a file source — it is profiled when it is indexed")
    from portia.core.io import source_table

    qualified = str(existing["source"])
    profile = profiling.profile(source_table({"table": qualified}, con, name=name))
    facts = _counted(_asked_now(qualified, con), profile["n_rows"])
    kind = facts.pop("kind", None)
    entry = _source_entry(qualified, profile, existing, facts)
    entry[REMOTE] = {**existing[REMOTE], **({"kind": kind} if kind else {})}
    entry["profiled"] = {"at": facts["at"]}
    for col, measured in zip(entry["columns"], profile["columns"], strict=True):
        col["dtype"] = measured["dtype"]
    # `_source_entry` puts the keys in a file source's order; a remote entry
    # reads better with its location block beside its name.
    ordered = {
        k: entry[k]
        for k in ("source", REMOTE, "indexed", "profiled", "summary", "columns", NOTES)
        if k in entry
    }
    _write(src_file, ordered)
    profile["source"] = qualified
    return profile


def _profile_model(name: str, con: Any, *, portia_dir: Path) -> dict:
    """The opt-in scan of a built table, at the table the build wrote."""
    from portia.core.io import source_table

    path = portia_dir / MODELS_DIR / f"{name}.yaml"
    existing = _read(path) if path.exists() else {}
    if not existing:
        raise ValueError(f"no indexed source or built model {name!r} to profile")
    written = existing.get("table")
    if not written:
        raise ValueError(
            f"{name!r} was built and not written to the warehouse — Run or Build with a "
            "target, or turn on agent_writes, and it can be profiled where it lands"
        )
    profile = profiling.profile(source_table({"table": written}, con, name=name))
    facts = _counted(_asked_now(written, con), profile["n_rows"])
    facts.pop("kind", None)
    entry = _model_entry(name, profile, existing)
    _write(path, _placed_model(entry, existing, indexed=facts, profiled={"at": facts["at"]}))
    profile["source"] = written
    return profile


def _placed_model(entry: dict, existing: dict, *, indexed: dict | None, profiled: Any) -> dict:
    """A model entry with its warehouse facts, in a fixed key order.

    ``built`` is when the spec last ran and is kept from ``existing`` when the
    entry is rewritten by a profile rather than a build; ``indexed`` is what the
    warehouse says about the written table for nothing (`STALENESS_FACTS`'
    keys, plus the row count `describe_source` states), and locally, where
    nothing was written, the row count the build measured (`ROWS`); ``profiled`` is the
    state the facts are in, a field rather than a silence. ``table`` and
    ``fingerprint`` say where the build wrote and which spec it was.
    """
    out = {
        "model": entry["model"],
        "built": existing.get("built") or entry["built"],
        **({"indexed": indexed} if indexed else {}),
        "profiled": profiled,
        "summary": entry.get("summary"),
        "columns": entry["columns"],
        **({NOTES: entry[NOTES]} if entry.get(NOTES) else {}),
    }
    for key in ("table", "fingerprint"):
        if existing.get(key):
            out[key] = existing[key]
    return out


def written_tables(portia_dir: str | Path = DEFAULT_DIR) -> dict[str, str]:
    """``{qualified table: model name}`` for every model a build wrote to the warehouse."""
    return {
        str(entry["table"]): name
        for name, entry in load_models(portia_dir).items()
        if entry.get("table")
    }


def is_profiled(entry: dict) -> bool:
    """Whether measured facts stand behind this entry's columns.

    A file source always is — indexing it *is* profiling it. A remote one is
    once :func:`profile_remote` has run. **A model is too, unless it says
    otherwise**: locally a build profiles it, and on a warehouse the entry
    carries ``profiled: null`` until somebody asks (:func:`index_model`). It
    used to answer *yes* for every model, so `describe_source` handed the
    agent a metadata-only entry with no ``profiled`` field on it — twelve
    columns of nothing, reading as nothing to report (2026-09-07).
    """
    if "model" in entry:
        return bool(entry.get("profiled", True))
    return not entry.get(REMOTE) or bool(entry.get("profiled"))


def rows_estimated(indexed: dict | None) -> bool:
    """Whether an entry's free row count is the engine's estimate and not a count.

    PostgreSQL's is what the planner believed at the last ``ANALYZE``
    (`connectors/postgres.Session.table_facts`). A field on the facts, read
    here once, so every surface that shows the number says the same thing
    about it.
    """
    return ROWS in ((indexed or {}).get(APPROXIMATE) or [])


#: Where an entry keeps its table's row count: under ``indexed``, beside the
#: facts that say when the table was looked at. **On every entry since
#: 2026-10-13.** A warehouse table always had it, from the information schema
#: for nothing (`scope_table`); a file's count was measured by every profile and
#: only ever written into the drafted summary's first sentence, where no tool
#: could read it as a number; a built table's was measured by every build and
#: kept nowhere. Now indexing records it on all three, and a profile that
#: counted replaces an estimate with its count (:func:`_counted`).
ROWS = "rows"

#: The facts' list of which numbers are estimates (`rows_estimated`).
APPROXIMATE = "approximate"


def row_count(entry: dict) -> int | None:
    """The row count indexing recorded for this entry, or ``None``.

    ``None`` means one of two things and :func:`rows_recorded` says which: the
    entry was indexed before portia recorded counts, or the warehouse gave none
    (a view, a PostgreSQL table never analyzed). Never parsed out of the
    drafted summary: that sentence is prose, and the count it restates may be
    a different indexing's.
    """
    rows = (entry.get("indexed") or {}).get(ROWS)
    return rows if isinstance(rows, int) and not isinstance(rows, bool) else None


def rows_recorded(entry: dict) -> bool:
    """Whether this entry was indexed by a portia that records a row count.

    The key's presence, not its value: a warehouse view's count is recorded
    as ``None`` because there is none to give, which is a different fact from
    an entry written before the count was kept.
    """
    return ROWS in (entry.get("indexed") or {})


def _counted(facts: dict, n_rows: int) -> dict:
    """``facts`` with the row count a profile measured, no longer called an estimate.

    A profile counts every row it reads, so after one the count is exact
    whatever the information schema said; PostgreSQL's planner estimate is
    replaced and ``approximate`` stops naming it.
    """
    out = {**facts, ROWS: int(n_rows)}
    estimated = [k for k in out.get(APPROXIMATE) or [] if k != ROWS]
    if estimated:
        out[APPROXIMATE] = estimated
    else:
        out.pop(APPROXIMATE, None)
    return out


def _metadata_summary(facts: dict, n_columns: int) -> str:
    """The placeholder for a table nobody has profiled or read yet."""
    rows = facts.get("rows")
    about = "about " if rows_estimated(facts) else ""
    lead = f"{about}{rows} rows, " if rows is not None else ""
    return f"{lead}{n_columns} columns. Not profiled. {AUTO_DRAFT_MARKER} — the agent will refine this.)"


def set_connection(
    name: str | None,
    *,
    agent_writes: bool | None = None,
    portia_dir: str | Path = DEFAULT_DIR,
) -> Path:
    """Record which of the user's connections this project runs on, and whether
    the copilot may write to it as it records.

    ``None`` clears it, which makes the project local again; the scope stays,
    because forgetting which tables were the study is a separate act. The
    connection is a *name* (`docs/CONNECTOR.md` §2.3): the account behind it is
    the reader's own entry, outside the repo.

    ``agent_writes`` is the hand-off (`CONNECTOR.md` §2.7.1): on, `record_step`
    creates each model's table in the warehouse the moment the step is measured,
    where the spec's own ``target`` says, creating the schema if missing. Off is
    the default and the absent key — the role is the security boundary, and this
    is the switch that says the person who chose the role meant it. ``None``
    leaves the setting as it is.

    **There is no project-wide target.** One shipped for two days and was
    dropped on the user's reading (§2.7.1): different tables go different
    places, so where a table lands is the spec's claim, and a ``target`` key a
    project file still carries from then is removed here as one nothing reads.
    """
    d = Path(portia_dir)
    proj = d / "project.yaml"
    data = _read(proj) if proj.exists() else {"project": "", "groups": [], "sources": {}}
    if name:
        data["connection"] = name
    else:
        data.pop("connection", None)
    data.pop("target", None)
    if agent_writes is not None:
        if agent_writes:
            data["agent_writes"] = True
        else:
            data.pop("agent_writes", None)
    d.mkdir(parents=True, exist_ok=True)
    _write(proj, data)
    return proj


def add_scope(names: list[str], *, portia_dir: str | Path = DEFAULT_DIR) -> list[str]:
    """Add qualified table names to the scope. Returns the whole scope, sorted."""
    d = Path(portia_dir)
    proj = d / "project.yaml"
    data = _read(proj) if proj.exists() else {"project": "", "groups": [], "sources": {}}
    data["scope"] = sorted({*(data.get("scope") or []), *names})
    _write(proj, data)
    return list(data["scope"])


#: Where a built model's measured facts live. Beside `sources/` and not inside
#: it, because the two are not the same kind of thing and `is_stale` proves it:
#: a source goes stale when its **file** moves, and a model goes stale when its
#: **spec** does, which `pipeline.is_stale` already answers off a fingerprint in
#: the compiled `.sql`. One folder holding both would need a record to say which
#: question to ask of it.
MODELS_DIR = "models"


def index_model(
    name: str,
    table: Table,
    *,
    portia_dir: str | Path = DEFAULT_DIR,
    metadata_only: bool = False,
    written_to: str | None = None,
    fingerprint: str | None = None,
) -> Path:
    """Profile a table a spec just built, and write its entry.

    **Called as a by-product of building, not on demand**, because a model has no
    file to profile: reaching its table means running its spec, and `run_spec`
    has just done that. `pipeline.build_project` is where it happens, and the
    table it hands over is the one it already produced.

    Same update rule as a source (facts refresh, prose and roles are preserved),
    and deliberately no ``summary`` draft: `_auto_summary` restates a profile so
    a source's YAML is never empty, and a model built from indexed sources is
    already described by the spec that builds it. An absent summary here means
    *nobody has read this model*, which is a fact worth being able to see.

    What this buys is the graph. A Model's Column nodes carried a name and
    nothing else — no `n_distinct`, no `null_rate`, no value — so every answer
    about a built table was structure with no measurements in it, and the facts
    that make an overlap readable were missing on exactly the tables portia
    itself produced.

    ``written_to`` is the qualified table this build created in the warehouse
    (`docs/CONNECTOR.md` §2.7.2), with the spec ``fingerprint`` it was built
    from. Both are on the entry or neither is: a build that wrote nothing
    leaves no ``table`` behind, because one from an earlier build would name a
    table this spec no longer describes. The pinned Warehouse section draws
    the entries that carry one, and a read of the model goes to that table
    while the fingerprint still matches the spec.
    """
    d = Path(portia_dir)
    (d / MODELS_DIR).mkdir(parents=True, exist_ok=True)
    path = d / MODELS_DIR / f"{name}.yaml"
    existing = _read(path) if path.exists() else {}
    if metadata_only:
        # On a warehouse the "2% on top of the run" is a scan on someone's
        # meter, per recorded step (`docs/CONNECTOR.md` §2.6). The columns and
        # types are free from a describe; the facts wait for a profile that was
        # asked for (:func:`profile_remote`, which takes a model's name too).
        profile = _shape_only(table)
        entry = _model_entry(name, profile, existing)
        profiled = None
    else:
        profile = profiling.profile(table)
        entry = _model_entry(name, profile, existing)
        profiled = {"at": entry["built"]["at"]}
    indexed = _written_facts(table, written_to)
    if profile["n_rows"] is not None:
        indexed = _counted(indexed or {}, profile["n_rows"])
    # A build: the ``built`` stamp is this run's, so nothing is kept from before.
    placed = _placed_model(entry, {}, indexed=indexed, profiled=profiled)
    if written_to:
        placed["table"] = written_to
        placed["fingerprint"] = fingerprint
    _write(path, placed)
    return path


def _written_facts(table: Table, written_to: str | None) -> dict | None:
    """What the warehouse says about the table a build wrote, for nothing.

    The same keys a scoped source records (`STALENESS_FACTS` plus ``rows``),
    off the session's ``table_facts`` when the connection has one. ``None``
    locally, where nothing was written anywhere.
    """
    facts_of = getattr(table.con, "table_facts", None)
    if not written_to or facts_of is None:
        return None
    try:
        facts = dict(facts_of(written_to))
    except Exception:  # noqa: BLE001 — a description must not fail a build
        return None
    remember_facts(written_to, facts)
    facts.pop("kind", None)
    return facts


def _shape_only(table: Table) -> dict:
    """A profile-shaped dict holding names and types and nothing measured."""
    return {
        "n_rows": None,
        "n_cols": len(table.columns),
        "columns": [{"name": col, "dtype": dtype} for col, dtype in table.dtypes.items()],
    }


def load_models(portia_dir: str | Path = DEFAULT_DIR) -> dict[str, dict]:
    """Every model entry, by name. Empty before anything has been built.

    Read off the directory rather than off a register in `project.yaml`: a
    model's existence is declared by its spec, and a second list of them here
    could disagree with `spec.discover_specs`.
    """
    d = Path(portia_dir) / MODELS_DIR
    return {path.stem: _read_entry(path) for path in sorted(d.glob("*.yaml"))} if d.exists() else {}


def remove_model(name: str, *, portia_dir: str | Path = DEFAULT_DIR) -> Path | None:
    """Forget what was measured about a model. Returns the file removed, if any."""
    path = Path(portia_dir) / MODELS_DIR / f"{name}.yaml"
    if not path.exists():
        return None
    path.unlink()
    return path


def remove_source(name: str, *, portia_dir: str | Path = DEFAULT_DIR) -> Path | None:
    """Forget a source: drop its entry, its registration, and its group membership.

    **The data file is not touched.** Un-indexing says "portia should stop
    knowing about this", which is a statement about the catalog; deleting
    someone's CSV because they tidied a sidebar is a different act entirely, and
    not one a catalog function gets to make on their behalf.

    A spec that already references the source keeps working — it resolves paths
    from its own ``sources:`` block, not from the catalog. What breaks is
    *recording a new step* against a name that is no longer indexed, which fails
    loudly at `record_step`.
    """
    d = Path(portia_dir)
    entry = d / "sources" / f"{name}.yaml"
    removed = entry if entry.exists() else None
    recorded = _read(entry).get("source") if removed else None
    entry.unlink(missing_ok=True)

    proj = d / "project.yaml"
    if proj.exists():
        data = _read(proj)
        (data.get("sources") or {}).pop(name, None)
        for group in data.get("groups") or []:
            group["sources"] = [s for s in group.get("sources") or [] if s != name]
        # A warehouse table leaves the scope with its entry: the scope *is* the
        # list of what is in the study, and this says it no longer is.
        if data.get("scope"):
            data["scope"] = [s for s in data["scope"] if s != recorded]
        _write(proj, data)
    return removed


def set_interpretation(
    name: str,
    *,
    summary: str | None = None,
    roles: dict[str, str] | None = None,
    note: str | None = None,
    portia_dir: str | Path = DEFAULT_DIR,
) -> Path:
    """Author the *judgment* half of an entry: prose ``summary``, column ``role``s, a ``note``.

    The mirror of ``index_source``: that one refreshes facts and preserves judgment,
    this one writes judgment and never touches a fact. Fields left as ``None`` are
    left alone, so a caller can set roles without restating the summary.

    This is what the agent calls once it has read the facts and the project context
    — the deterministic side has no business deciding what a column *means*.

    **It writes a model's read as well as a source's** *(2026-09-04)*. A model
    entry has the same shape minus a file (:func:`_model_entry`) and its
    ``summary`` is deliberately never drafted, so *"nobody has read this model"*
    is a state the catalog can hold — which is only worth holding if somebody can
    leave it. Looking in ``sources/`` alone made the message unfollowable in both
    halves at once: a model has no file to index, and the path it named could not
    exist for one. It is the same judgment being written either way, so it is the
    same function, and which folder it lands in is a lookup rather than a mode.

    **A ``note`` is appended, never replaced** *(2026-09-05, `docs/COPILOT.md`
    §2)*. The summary is one paragraph that a later call overwrites whole, which
    is the right shape for *what this table is* and the wrong one for *what we
    learned about it on Tuesday*. In the 2026-09-04 inspections sessions the
    copilot worked out in one chat that NYC's outcome is a letter grade rather
    than a pass/fail, and in a later chat defined failure as ``result = 'Fail'``
    on the same table, because the first chat's read lived nowhere. A note is
    the sentence that would have prevented it: dated, kept in order, and read
    back by ``describe_source`` before anything is built on the table. It is
    not a finding: a finding rests on a query and a note rests on a read.
    """
    entry_file = _entry_file(name, portia_dir)
    if entry_file is None:
        looked = " or ".join(
            str(Path(portia_dir) / d / f"{name}.yaml") for d in ("sources", MODELS_DIR)
        )
        raise ValueError(
            f"no catalog entry for {name!r} — index the source, or record a step on the "
            f"spec that builds it ({looked})"
        )

    entry = _read(entry_file)
    if summary is not None:
        entry["summary"] = summary
    for col, role in (roles or {}).items():
        # As the engine spells it (`dialect.resolve_columns`): a role on
        # `establishment_id` lands on the ESTABLISHMENT_ID a warehouse returned.
        (col,) = dialects.resolve_columns([col], [c["name"] for c in entry.get("columns", [])])
        match = next((c for c in entry.get("columns", []) if c["name"] == col), None)
        if match is None:
            known = ", ".join(c["name"] for c in entry.get("columns", []))
            raise ValueError(f"no such column {col!r} in {name!r} (have: {known})")
        match["role"] = role
    if note is not None:
        if not note.strip():
            raise ValueError("a note has to say something")
        entry.setdefault(NOTES, []).append({"text": note.strip(), "at": _now()})

    _write(entry_file, entry)
    return entry_file


#: The key under which an entry keeps what was learned about it, in the order it
#: was learned: a list of ``{text, at}``. Absent until the first note, so an
#: entry with none says nothing rather than holding an empty list on every
#: source of every project.
NOTES = "notes"


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _entry_file(name: str, portia_dir: str | Path) -> Path | None:
    """Where this name's catalog entry lives, or ``None`` if it has none.

    **Sources first, and the order is not arbitrary.** A model's name comes from
    its spec and a source's from its file, so the two namespaces can collide —
    and if they ever do, the source is the thing with a file on disk behind it.
    Nothing enforces uniqueness across the two today; `spec.discover_specs`
    enforces it within models, which is what makes a `.sql` filename unique
    (`PIPELINE.md` §2.4).
    """
    d = Path(portia_dir)
    return next(
        (
            f
            for f in (d / "sources" / f"{name}.yaml", d / MODELS_DIR / f"{name}.yaml")
            if f.exists()
        ),
        None,
    )


def set_group(
    name: str,
    *,
    context: str | None = None,
    sources: list[str] | None = None,
    portia_dir: str | Path = DEFAULT_DIR,
) -> Path:
    """Define (or update) a group of sources that belong together, with its own context.

    A group is judgment — "these three tables are external event data, they share
    a vendor's quirks" — attached to a set of sources. It carries context the
    per-source entries can't: how the sources relate, where they came from, what
    they're for together. That context travels with every source in the group.

    Fields left as ``None`` are left alone, so context and membership can be set
    independently.
    """
    d = Path(portia_dir)
    proj_file = d / "project.yaml"
    data: dict[str, Any] = _read(proj_file) if proj_file.exists() else {}
    data.setdefault("project", "")
    data.setdefault("sources", {})
    groups: list[dict] = data.setdefault("groups", [])

    for src in sources or []:
        if src not in data["sources"]:
            known = ", ".join(data["sources"]) or "(none indexed)"
            raise ValueError(f"no indexed source {src!r} — have: {known}")

    group = next((g for g in groups if g.get("name") == name), None)
    if group is None:
        group = {"name": name, "context": "", "sources": []}
        groups.append(group)
    if context is not None:
        group["context"] = context
    if sources is not None:
        group["sources"] = sources

    _write(proj_file, data)
    return proj_file


#: The three fields a warehouse project adds to ``project.yaml``
#: (`docs/CONNECTOR.md` §2.3, §2.5, §2.7.1): which of the user's connections it
#: runs on, which tables are in scope, and whether the copilot writes to it as
#: it records. Absent on a local project, and their absence is what "local"
#: means. Where a table goes is not here: it is each spec's own ``target``.
CONNECTION_FIELDS = ("connection", "scope", "agent_writes")


def project_settings(portia_dir: str | Path = DEFAULT_DIR) -> dict:
    """The project file's settings, read raw — for whoever opens the project.

    Cheaper than :func:`load_catalog`, which reads every source entry, and it is
    called before any of those are wanted: `connectors.backend_for` needs to
    know where the project's compute is before a single table is opened.
    """
    d = Path(portia_dir)
    return _settings(_read(d / "project.yaml") if (d / "project.yaml").exists() else {})


def _settings(proj: dict) -> dict:
    """`project_settings` out of a project file already read."""
    return {
        "data_dir": proj.get("data_dir", ""),
        "connection": proj.get("connection") or None,
        "scope": list(proj.get("scope") or []),
        "agent_writes": bool(proj.get("agent_writes")),
    }


def load_catalog(portia_dir: str | Path = DEFAULT_DIR) -> dict:
    """Load the whole catalog — project context, groups, and every source entry —
    into one compact dict (the context a downstream task/agent reads)."""
    loaded, _ = reload_catalog({}, {}, portia_dir)
    return loaded


#: What a catalog file looked like when it was read: ``(st_mtime_ns, st_size)``,
#: by its absolute path. `reload_catalog` reads a file again only when this moved.
Seen = dict[str, tuple[int, int]]


def reload_catalog(
    loaded: dict, seen: Seen, portia_dir: str | Path = DEFAULT_DIR
) -> tuple[dict, Seen]:
    """`load_catalog`, reading again only the entries that changed since ``loaded`` was read.

    ``seen`` is what each file looked like when ``loaded`` was read, as this
    returned it; ``({}, {})`` reads everything. Returns the catalog as it is
    now and what its files look like now, for the next call.

    **For a reader that holds the catalog and is told it moved** *(2026-10-08)*:
    the window, which read every entry again whenever one changed. On a project
    with two 1,579-column tables one entry is 140 ms to parse even with libyaml,
    eighteen are half a second, and an indexing run or a reading job writes one
    at a time: a burst of eighteen ``set_interpretation`` calls was eighteen
    whole reads on the window's loop. An entry whose file has the same
    modification time and size as when it was read is the dict already held.

    ``project.yaml`` is read every time. It is the register that names the
    entries, a few kilobytes, and what a write to the groups or the brief
    changes. Each file is looked at before it is read, so one written between
    the two is read again next time rather than missed.
    """
    d = Path(portia_dir).absolute()
    now: Seen = {}
    proj_file = d / "project.yaml"
    proj = _read(proj_file) if _look(proj_file, now) else {}
    held = loaded.get("sources") or {}
    sources = {}
    for name in proj.get("sources") or {}:
        path = d / "sources" / f"{name}.yaml"
        if not _look(path, now):
            continue
        key = str(path)
        same = name in held and seen.get(key) == now[key]
        sources[name] = held[name] if same else _read(path)
    return {
        "project": proj.get("project", ""),
        "data_dir": proj.get("data_dir", ""),
        "groups": proj.get("groups", []),
        "sources": sources,
        **{k: v for k, v in _settings(proj).items() if k in CONNECTION_FIELDS},
    }, now


def _look(path: Path, seen: Seen) -> bool:
    """Whether ``path`` is there, recording what it looks like in ``seen`` if so."""
    try:
        stat = path.stat()
    except OSError:
        return False
    seen[str(path)] = (stat.st_mtime_ns, stat.st_size)
    return True


def source_names(portia_dir: str | Path = DEFAULT_DIR) -> list[str]:
    """The indexed sources' names, as :func:`load_catalog`'s ``sources`` keys, reading no entry.

    For a caller that needs to know what is there, not what it holds: a tool
    resolving the name it was given (`agent/handlers._known_name`). It read the
    whole catalog for that, twice per call with `_entry` beside it, and on a
    project with two 1,579-column tables that was a second of YAML for a
    6-column answer (2026-10-08).
    """
    d = Path(portia_dir)
    return _listed(d, _read(d / "project.yaml") if (d / "project.yaml").exists() else {})


def load_source(name: str, portia_dir: str | Path = DEFAULT_DIR) -> dict | None:
    """One source's entry, exactly as :func:`load_catalog` would hold it, or ``None``."""
    d = Path(portia_dir)
    return _read_entry(d / "sources" / f"{name}.yaml") if name in source_names(d) else None


def _listed(d: Path, proj: dict) -> list[str]:
    """What counts as indexed: registered in ``project.yaml`` with its entry on disk.

    `reload_catalog`'s rule, for a caller that wants the names without the
    entries, so the name a tool resolves and the entry it then reads cannot
    disagree with the whole catalog about whether a source exists
    (`tests/test_catalog.py` holds the two to the same answer).
    """
    return [
        name for name in (proj.get("sources") or {}) if (d / "sources" / f"{name}.yaml").exists()
    ]


# --- building an entry ------------------------------------------------------


def _source_entry(
    source: str, profile: dict, existing: dict | None, indexed: dict | None = None
) -> dict:
    existing = existing or {}
    prev_roles = {c["name"]: c.get("role") for c in existing.get("columns", [])}
    columns = [
        {"name": col["name"], "role": prev_roles.get(col["name"]), **_column_facts(col)}
        for col in profile["columns"]
    ]
    return {
        "source": source,
        # What the file looked like when it was indexed, so a file edited
        # afterwards is detectable rather than silently stale, and how many
        # rows it held (`ROWS`). A fact, so re-indexing refreshes it. The path
        # is not repeated here; `source` above is the one place a location is
        # written down.
        "indexed": indexed or existing.get("indexed"),
        # Layer 1 — prose read. Preserved across re-index (judgment, not fact).
        "summary": existing.get("summary") or _auto_summary(profile),
        # Layer 2 — per-column detail. Facts refreshed; `role` preserved.
        "columns": columns,
        **_kept_notes(existing),
    }


def _kept_notes(existing: dict | None) -> dict:
    """The notes an entry already holds, to carry across a rebuild of the entry.

    Every function that assembles an entry builds a fresh dict, which is how the
    facts get refreshed; judgment has to be copied over by name, and this is the
    one place the note half of that rule is spelled out. Absent stays absent.
    """
    notes = (existing or {}).get(NOTES)
    return {NOTES: notes} if notes else {}


def _model_entry(name: str, profile: dict, existing: dict | None) -> dict:
    """A built table's entry. The same shape as a source's, minus a file."""
    existing = existing or {}
    prev_roles = {c["name"]: c.get("role") for c in existing.get("columns", [])}
    return {
        "model": name,
        "built": {"at": datetime.now().astimezone().isoformat(timespec="seconds")},
        # Preserved when it is there and never drafted when it is not — see
        # :func:`index_model`.
        "summary": existing.get("summary"),
        "columns": [
            {"name": col["name"], "role": prev_roles.get(col["name"]), **_column_facts(col)}
            for col in profile["columns"]
        ],
        **_kept_notes(existing),
    }


#: What :func:`is_stale` compares — facts about the **file**, and only those.
#:
#: ``at`` is recorded beside them and is deliberately not here. It is when portia
#: last *looked*, which changes every second and says nothing about whether the
#: file did. Comparing it (as this did until 2026-08-03) made every source read
#: as stale one second after it was indexed, with an identical size and an
#: identical mtime. The tests hid it because each one usually finished inside the
#: same wall-clock second as the index it was checking; they started failing, one
#: at random per run, the moment profiling got fast enough to move that boundary.
STALENESS_FACTS = ("size", "mtime")


def is_stale(entry: dict, *, portia_dir: str | Path = DEFAULT_DIR) -> bool:
    """Whether this source's file has changed since it was indexed.

    Compares the recorded size and mtime against the file now — see
    :data:`STALENESS_FACTS` for what is deliberately not compared. Says nothing
    about what to *do* about it — re-indexing refreshes facts and preserves prose
    and roles, exactly as it always has (the update rule above).

    A source whose file has been moved or deleted counts as stale: the catalog's
    claims are no longer backed by anything on disk, and that is worth saying out
    loud rather than treating as fresh.
    """
    indexed, path = entry.get("indexed"), entry.get("source")
    if not indexed or not path:
        return False  # nothing was recorded; there is no claim to contradict
    if entry.get(REMOTE):
        # Asking the warehouse is a query, and this is called per source on
        # every paint of the left pane. A remote entry is checked when someone
        # asks (:func:`remote_changed`), never on a render.
        return False
    target = project_root(portia_dir) / path
    if not target.exists():
        return True
    now = file_facts(target)
    return any(indexed.get(k) != now[k] for k in STALENESS_FACTS)


def remote_changed(entry: dict, con: Any, *, within: float = 0.0) -> bool:
    """Whether a scoped table has changed since it was indexed — one query, on request.

    The same two keys :data:`STALENESS_FACTS` names, which a remote entry
    records as bytes and ``LAST_ALTERED`` (`docs/CONNECTOR.md` §2.11).

    ``within`` is how many seconds old an answer the warehouse already gave
    may be and still stand for now (:func:`warehouse_facts`); ``0`` asks.
    Raises what the session raises for a table it cannot find, a `ValueError`
    on every connector here.
    """
    indexed = entry.get("indexed") or {}
    now = warehouse_facts(str(entry["source"]), con, within=within)
    assert now is not None  # a connection was given, so it was asked
    return any(indexed.get(k) != now.get(k) for k in STALENESS_FACTS)


# --- has it changed since it was indexed (2026-10-13) --------------------------------
#
# **Every answer touching a table that changed since portia indexed it says so
# first** (`sandbox/limits/REVAMP_SPEC.md`, rule 6). `is_stale` has answered that
# for a file since 2026-08-03, for the window's left pane; `remote_changed` was
# written for a warehouse table with the reason it could not be on a render, a
# query, and was then called by nothing. `freshness` is the one question both
# halves answer, with what changed, for the agent's tools (`agent/freshness.py`
# words it and puts it at the top of the answer).

#: How long a warehouse's answer about one table is believed, in seconds.
#:
#: A burst of tool calls names the same tables over and over: a reading job
#: describes eighteen of them and then queries each, and every answer has to
#: say whether its tables changed. Asking the warehouse each time is a
#: metadata query per table per call, about two seconds each on BigQuery
#: (`CONNECTOR.md` §2.6.1). A minute is long enough that a burst asks once and
#: short enough that a table reloaded mid-chat is noticed within the next few
#: answers. Every `table_facts` portia asks for anyway (scoping, a profile, a
#: build) refreshes it, so a re-index is never contradicted by an older answer.
WAREHOUSE_FACTS_S = 60.0

#: What the warehouse last said about a table, by (connection, qualified name):
#: ``(time.monotonic() when asked, facts)``.
_asked: dict[tuple[str, str], tuple[float, dict]] = {}
_asked_lock = threading.Lock()


def _asked_key(qualified: str) -> tuple[str, str]:
    """The cache's key: the connection's name in the user's registry, and the table.

    Two projects on one connection share what the warehouse said; a project on
    another connection, opened in the same process, does not.
    """
    return backend.active().label, qualified


def remember_facts(qualified: str, facts: dict) -> None:
    """Keep what the warehouse just said about a table, for :func:`warehouse_facts`."""
    with _asked_lock:
        _asked[_asked_key(qualified)] = (time.monotonic(), dict(facts))


def forget_facts() -> None:
    """Drop every remembered answer: for tests, and for a caller that switches warehouses."""
    with _asked_lock:
        _asked.clear()


def warehouse_facts(qualified: str, con: Any | None, *, within: float) -> dict | None:
    """What the warehouse says about a table now: remembered if younger than ``within``.

    ``None`` when nothing young enough is remembered and there is no ``con`` to
    ask. Asking goes through ``con.table_facts`` and is remembered.
    """
    with _asked_lock:
        held = _asked.get(_asked_key(qualified))
    if held is not None and within > 0 and time.monotonic() - held[0] <= within:
        return dict(held[1])
    if con is None:
        return None
    facts = con.table_facts(qualified)
    remember_facts(qualified, facts)
    return dict(facts)


def _asked_now(qualified: str, con: Any) -> dict:
    """``con.table_facts``, asked now and remembered: for what records the facts."""
    facts = warehouse_facts(qualified, con, within=0.0)
    assert facts is not None
    return facts


#: What :func:`freshness` says changed, as codes. `agent/freshness.py` words
#: them for the model; the window and the reserved tables read them as they are.
FILE_CHANGED = "file_changed"
FILE_GONE = "file_gone"
TABLE_CHANGED = "table_changed"
TABLE_GONE = "table_gone"

#: Why :func:`freshness` cannot tell. ``no_marker``: nothing was recorded that
#: could contradict the entry, which is a warehouse view (no size, no change
#: time) or an entry with no facts. ``not_asked``: a warehouse table, with no
#: connection given and no recent answer. ``unreachable``: the warehouse was
#: asked and failed to answer.
NO_MARKER = "no_marker"
NOT_ASKED = "not_asked"
UNREACHABLE = "unreachable"


def freshness(
    name: str, entry: dict, *, portia_dir: str | Path = DEFAULT_DIR, con: Any | None = None
) -> dict | None:
    """Whether the table behind ``entry`` changed since it was indexed. ``None`` when it did not.

    Changed: ``{"source": name, "changed": <code>, ...}`` with the facts the
    code rests on: a file's ``path`` and what it looked like then and now
    (``was``/``now``: size and mtime), a warehouse table's ``table``, and
    ``indexed``, when it was looked at. Cannot tell: ``{"source": name,
    "changed": None, "unknown": True, "why": <code>}``.

    **Codes, not sentences.** The words are the agent's to read and live in
    its prompts (`agent/freshness.py`); this module is below the layer that
    loads them, as `ops/sql.py` is.

    A file is :func:`is_stale`'s question, a stat. A warehouse table is
    :func:`remote_changed`'s, one metadata query on ``con``, believed for
    `WAREHOUSE_FACTS_S`; without ``con`` only a remembered answer is used. A
    view has nothing to compare and says so. A model a build wrote to the
    warehouse is its written table; **a model built locally is ``None`` here**,
    because it keeps no marker of its own: it is as fresh as the sources it
    reads, which the caller checks (`spec.sources_read`).
    """
    indexed = entry.get("indexed") or {}
    at = indexed.get("at")
    if "model" in entry and "source" not in entry:
        if not entry.get("table"):
            return None
        return _table_freshness(name, str(entry["table"]), indexed, con, kind="table")
    if entry.get(REMOTE):
        kind = (entry.get(REMOTE) or {}).get("kind") or "table"
        return _table_freshness(name, str(entry.get("source")), indexed, con, kind=kind)
    path = entry.get("source")
    if not path or any(indexed.get(k) is None for k in STALENESS_FACTS):
        return {"source": name, "changed": None, "unknown": True, "why": NO_MARKER}
    target = project_root(portia_dir) / str(path)
    if not target.exists():
        return {"source": name, "changed": FILE_GONE, "path": str(path), "indexed": at}
    now = file_facts(target)
    if all(indexed.get(k) == now[k] for k in STALENESS_FACTS):
        return None
    return {
        "source": name,
        "changed": FILE_CHANGED,
        "path": str(path),
        "indexed": at,
        "was": {k: indexed.get(k) for k in STALENESS_FACTS},
        "now": {k: now[k] for k in STALENESS_FACTS},
    }


def _table_freshness(
    name: str, qualified: str, indexed: dict, con: Any | None, *, kind: str
) -> dict | None:
    """:func:`freshness` for a table in the warehouse."""
    unknown = {"source": name, "changed": None, "unknown": True}
    if kind == "view" or indexed.get("mtime") is None:
        return {**unknown, "why": NO_MARKER}
    marked = {"source": qualified, "indexed": indexed}
    try:
        if warehouse_facts(qualified, None, within=WAREHOUSE_FACTS_S) is None and con is None:
            return {**unknown, "why": NOT_ASKED}
        changed = remote_changed(marked, con, within=WAREHOUSE_FACTS_S)
    except cancel.Cancelled:
        raise
    except ValueError:
        # Every connector says a table it cannot find with a `ValueError`.
        return {
            "source": name,
            "changed": TABLE_GONE,
            "table": qualified,
            "indexed": indexed.get("at"),
        }
    except Exception:  # noqa: BLE001 - the driver's own failure; told as "cannot tell"
        return {**unknown, "why": UNREACHABLE}
    if not changed:
        return None
    return {
        "source": name,
        "changed": TABLE_CHANGED,
        "table": qualified,
        "indexed": indexed.get("at"),
    }


#: What :func:`freshness` reads off an entry, kept by :func:`_read_entry`.
_MARKED = ("source", REMOTE, "indexed", "model", "table")

#: The marked part of each entry file read, by path, with the file's
#: ``(st_mtime_ns, st_size)`` when it was read.
_marks: dict[str, tuple[tuple[int, int], dict]] = {}


def _read_entry(path: Path) -> dict:
    """`_read`, keeping the part :func:`marks` answers with while the file is unchanged.

    The file is looked at before it is read, so one written between the two is
    read again next time rather than trusted (`reload_catalog`'s rule).
    """
    seen: Seen = {}
    key = str(path.absolute())
    looked = _look(path, seen)
    entry = _read(path)
    if looked:
        _marks[key] = (seen[str(path)], {k: entry[k] for k in _MARKED if k in entry})
    return entry


def marks(name: str, portia_dir: str | Path = DEFAULT_DIR) -> dict | None:
    """The part of a table's entry :func:`freshness` reads, by name, or ``None``.

    An indexed source first and then a built model, as `agent/handlers._entry`
    looks. **Cheap after the first read**: a tool that just read the entry
    (`load_source`, `load_models`) left its marks here, so asking whether its
    table changed costs a stat of the entry file rather than a second parse of
    YAML that is 265 kB on a 1,579-column table (`reload_catalog`'s numbers).
    """
    d = Path(portia_dir)
    candidates = []
    if name in source_names(d):
        candidates.append(d / "sources" / f"{name}.yaml")
    candidates.append(d / MODELS_DIR / f"{name}.yaml")
    for path in candidates:
        seen: Seen = {}
        if not _look(path, seen):
            continue
        held = _marks.get(str(path.absolute()))
        if held is not None and held[0] == seen[str(path)]:
            return dict(held[1])
        entry = _read_entry(path)
        return {k: entry[k] for k in _MARKED if k in entry}
    return None


def file_facts(path: str | Path) -> dict:
    """Size, mtime and the moment we looked — what makes staleness detectable.

    mtime to the microsecond, not the second: a file rewritten quickly at the
    same length would otherwise read as unchanged, which is the one case this
    exists to catch.
    """
    stat = Path(path).stat()
    return {
        "size": int(stat.st_size),
        "mtime": round(stat.st_mtime, 6),
        "at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }


#: The value-shaped facts kept beside the counts, when the profiler produced
#: them. **Split by kind, and that split is the point** (`profiling._column`):
#: a numeric column's shape is its range, a categorical column's shape is its
#: commonest value, and neither says anything useful about the other.
#:
#: ``min``/``max`` were dropped here for a long time while ``median``/``std``
#: were kept, so the profiler measured the range on every index and the catalog
#: threw it away — leaving the graph with no way to say what a numeric column
#: even looks like.
#:
#: **The mean, the quartiles and the samples are kept too** *(2026-10-13)*, as
#: `LATER_FACTS`, since `profile_source` answers from this slice rather than
#: from a fresh profile: the copilot prepares data for a model, and mean against
#: median, quartiles against the mean, is how it chooses a fill or an aggregate.
#: The profiler measured them on every index already; storing them costs a few
#: numbers and three values per column, and that size was accepted.
_SHAPE = ("min", "max", "median", "std", "top", "top_freq")

#: The facts stored since 2026-10-13. A measured column of an entry indexed
#: before has none of them, and ``samples`` is the one every measured column
#: carries, so its absence is how an answer tells an old entry from a column
#: that has no mean because it is text (`agent/handlers.profile_source`).
LATER_FACTS = ("mean", "q25", "q75", "samples")


def _column_facts(col: dict) -> dict:
    """A compact, token-lean slice of the column profile for the catalog."""
    if "inferred" not in col:
        # Shape only (`_shape_only`): a name and a type, no measurement behind it.
        return {"dtype": col.get("dtype")}
    facts = {
        "inferred": col["inferred"],
        "null_rate": col["null_rate"],
        "n_distinct": col["n_distinct"],
        "flags": col["flags"],
    }
    for k in (*_SHAPE, *LATER_FACTS):  # the richer describe-stats, if present
        if k in col:
            facts[k] = col[k]
    # Which of those an engine estimated (BigQuery's median is a sketch). Kept
    # since `profile_source` answers from these facts (2026-10-13): a median
    # read back without it reads as exact.
    estimated = [k for k in col.get(APPROXIMATE) or [] if k in facts]
    if estimated:
        facts[APPROXIMATE] = estimated
    return facts


def _auto_summary(profile: dict) -> str:
    """A plain restatement of the facts — a placeholder until the agent writes a
    semantic read. Deliberately not a judgement about what the data *means*."""
    parts = [f"{profile['n_rows']} rows, {profile['n_cols']} columns."]
    watch = []
    for col in profile["columns"]:
        hits = [_WATCHOUTS[f] for f in col["flags"] if f in _WATCHOUTS]
        if hits:
            watch.append(f"{col['name']} ({', '.join(hits)})")
    if watch:
        parts.append("Watch-outs: " + "; ".join(watch) + ".")
    parts.append(f"{AUTO_DRAFT_MARKER} — edit freely; the agent will refine this.)")
    return " ".join(parts)


def is_interpreted(entry: dict) -> bool:
    """Whether a source's ``summary`` is a real read, or still the placeholder."""
    return AUTO_DRAFT_MARKER not in (entry.get("summary") or "")


def written_summary(entry: dict) -> str | None:
    """The summary somebody wrote, the agent or the user; ``None`` when there is none.

    **What a tool answer calls ``summary``** *(2026-10-13)*. The draft
    `_auto_summary` writes at indexing restates the profile ("17544 rows, 1579
    columns. Watch-outs: …") and on a sparse table it named every mostly-null
    column, which made it most of a 1,579-column table's description; its
    facts are the columns' flags and the entry's row count, measured, where
    they belong. It is still drafted, because it is what marks a source as not
    read yet (:func:`is_interpreted`: the window's pane, the brief's index, the
    graph), and it is never shown as prose anywhere.
    """
    summary = entry.get("summary")
    if not summary or not is_interpreted(entry):
        return None
    return str(summary)


def _register(d: Path, name: str) -> None:
    proj = d / "project.yaml"
    data: dict[str, Any] = _read(proj) if proj.exists() else {}
    data.setdefault("project", "")
    data.setdefault("groups", [])
    sources: dict[str, str] = data.setdefault("sources", {})
    sources[name] = f"sources/{name}.yaml"
    _write(proj, data)


def render_source(entry: dict) -> str:
    """Human-readable view of a source entry for the CLI."""
    lines = [f"{entry['source']}", f"  summary: {entry['summary']}", ""]
    if entry.get(NOTES):
        lines.append("  notes:")
        lines.extend(f"    {n['at'][:10]}  {n['text']}" for n in entry[NOTES])
        lines.append("")
    lines.append("  columns:")
    for c in entry["columns"]:
        role = c.get("role") or "—"
        if "inferred" not in c:
            # A scoped warehouse table nobody has profiled: the type is all there is.
            lines.append(f"    {c['name']}  [role: {role}]  {c.get('dtype', '')}  (not profiled)")
            continue
        flags = f"  ⚑ {', '.join(c['flags'])}" if c["flags"] else ""
        lines.append(
            f"    {c['name']}  [role: {role}]  {c['inferred']}, "
            f"{format_rate(c['null_rate'])} null, {c['n_distinct']} distinct{flags}"
        )
    return "\n".join(lines)


# --- yaml io (block style, stable order, hand-editable) ---------------------

#: libyaml's parser when PyYAML was built with it, else the pure-Python one.
#:
#: **For reading only** *(2026-10-08)*. The window reads every entry again each
#: time the catalog moves (`ui/engine.refresh_catalog`), on its event loop, and
#: on a project with two 1,579-column tables the pure-Python scanner took 1.3 s
#: for eighteen files; libyaml takes 0.2 s and reads the same values.
#:
#: **Writing stays pure Python, because libyaml would not write the same bytes**,
#: and the catalog is read as a diff. Its emitter escapes any character past
#: U+FFFF, so a sample holding an emoji would be rewritten as ``"\U0001F600"``,
#: and it folds a long double-quoted string (one with a tab, say) at different
#: places. Both are pinned in `tests/test_catalog.py`.
_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


def _read(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.load(f, Loader=_LOADER) or {}


#: How many times, and how far apart, a finished write tries to take the entry's
#: place while somebody is reading it: Windows refuses to replace a file that is
#: open, and the window reads entries off its loop while a tool writes them.
_REPLACE_TRIES = 10
_REPLACE_WAIT_S = 0.02


def _write(path: Path, data: dict) -> None:
    """Whole or not at all: a reader must never see half an entry.

    **Written beside it and renamed into place** *(2026-10-08)*, as
    `figures._write` and `ui/prefs` do. The file used to be truncated and
    written in place, a quarter of a second of dumping for a 1,579-column
    entry, and once the window read the entries that moved off its loop
    (`reload_catalog`) it read one that a ``set_interpretation`` was halfway
    through: a YAML error, in a replay of a reading job. The bytes are
    PyYAML's emitter's, as before. The temporary file is named per thread and
    opened as the entry would be, so two writers never share one and the
    entry keeps the permissions it always had.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.part")
    try:
        with open(partial, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, sort_keys=False, default_flow_style=False, allow_unicode=True)
        _replace(partial, path)
    finally:
        partial.unlink(missing_ok=True)


def _replace(partial: Path, path: Path) -> None:
    for attempt in range(_REPLACE_TRIES):
        try:
            os.replace(partial, path)
            return
        except PermissionError:
            if attempt == _REPLACE_TRIES - 1:
                raise
            time.sleep(_REPLACE_WAIT_S)

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

from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from portia.checks import profiling
from portia.checks.profiling import profile_path
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
    _write(src_file, _source_entry(recorded, profile, existing, file_facts(data_path)))
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
    taken = {
        name.lower(): entry.get("source") for name, entry in load_catalog(d)["sources"].items()
    }
    # A built table's name is taken too (2026-09-07): scoping `STAGING.stg_x`
    # as a source used to write `sources/stg_x.yaml` beside `models/stg_x.yaml`,
    # and every lookup by name then found the source half first.
    taken |= {name.lower(): f"model:{name}" for name in load_models(d) if name.lower() not in taken}
    for depth in range(1, len(parts) + 1):
        candidate = NAME_SEP.join(parts[-depth:])
        if taken.get(candidate.lower()) in (None, recorded):
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
    facts = con.table_facts(qualified)
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
    facts = con.table_facts(written)
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
    keys, plus the row count `describe_source` states); ``profiled`` is the
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
    return "rows" in ((indexed or {}).get("approximate") or [])


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
        entry = _model_entry(name, _shape_only(table), existing)
        profiled = None
    else:
        entry = _model_entry(name, profiling.profile(table), existing)
        profiled = {"at": entry["built"]["at"]}
    # A build: the ``built`` stamp is this run's, so nothing is kept from before.
    placed = _placed_model(entry, {}, indexed=_written_facts(table, written_to), profiled=profiled)
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
    return {path.stem: _read(path) for path in sorted(d.glob("*.yaml"))} if d.exists() else {}


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
    proj = _read(d / "project.yaml") if (d / "project.yaml").exists() else {}
    return {
        "data_dir": proj.get("data_dir", ""),
        "connection": proj.get("connection") or None,
        "scope": list(proj.get("scope") or []),
        "agent_writes": bool(proj.get("agent_writes")),
    }


def load_catalog(portia_dir: str | Path = DEFAULT_DIR) -> dict:
    """Load the whole catalog — project context, groups, and every source entry —
    into one compact dict (the context a downstream task/agent reads)."""
    d = Path(portia_dir)
    proj = _read(d / "project.yaml") if (d / "project.yaml").exists() else {}
    sources = {
        name: _read(d / "sources" / f"{name}.yaml")
        for name in (proj.get("sources") or {})
        if (d / "sources" / f"{name}.yaml").exists()
    }
    return {
        "project": proj.get("project", ""),
        "data_dir": proj.get("data_dir", ""),
        "groups": proj.get("groups", []),
        "sources": sources,
        **{k: v for k, v in project_settings(d).items() if k in CONNECTION_FIELDS},
    }


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
        # afterwards is detectable rather than silently stale. A fact, so
        # re-indexing refreshes it. The path is not repeated here; `source`
        # above is the one place a location is written down.
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


def remote_changed(entry: dict, con: Any) -> bool:
    """Whether a scoped table has changed since it was indexed — one query, on request.

    The same two keys :data:`STALENESS_FACTS` names, which a remote entry
    records as bytes and ``LAST_ALTERED`` (`docs/CONNECTOR.md` §2.11).
    """
    indexed = entry.get("indexed") or {}
    now = con.table_facts(str(entry["source"]))
    return any(indexed.get(k) != now.get(k) for k in STALENESS_FACTS)


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
_SHAPE = ("min", "max", "median", "std", "top", "top_freq")


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
    for k in _SHAPE:  # the richer describe-stats, if present
        if k in col:
            facts[k] = col[k]
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


def _read(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False, default_flow_style=False, allow_unicode=True)

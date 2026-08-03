"""The context catalog — the durable record of *what the data is*.

Sibling of the spec (which records *what we did to it*). Lives in ``.portia/``:

- ``project.yaml`` — the global project context (your words), the folder inside
  the repo that holds this project's data (``data_dir``), defined groups
  (``{name, context, sources}`` — sources that belong together, plus the context
  they share), and a registry of indexed sources.
- ``sources/<name>.yaml`` — per source, two layers:
    * **Layer 1** ``summary`` — a short prose read of what this data is.
    * **Layer 2** ``columns`` — per column, a ``role`` slot plus the facts the
      checks found.

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

from portia.checks.profiling import profile_path
from portia.core.present import format_rate

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
        return str(target.relative_to(root))
    except ValueError:
        raise ValueError(
            f"{data_path} is outside this project ({root}). "
            f"portia indexes data already in the repo — to bring this file in:\n"
            f"  python -m portia.cli.import_data {data_path} --to <dir>"
        ) from None


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
    name = data_path.stem
    d = Path(portia_dir)
    (d / "sources").mkdir(parents=True, exist_ok=True)

    recorded = source_ref(data_path, portia_dir=d)
    # Profiled straight off the file — DuckDB-backed and memory-bounded, so the
    # profile that cost 1883 MB through pandas is a handful of aggregates over
    # the file itself. There is no ingested copy to profile instead (§2.7).
    profile = profile_path(data_path)

    src_file = d / "sources" / f"{name}.yaml"
    existing = _read(src_file) if src_file.exists() else None
    _write(src_file, _source_entry(recorded, profile, existing, file_facts(data_path)))
    _register(d, name)
    return src_file


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
    entry.unlink(missing_ok=True)

    proj = d / "project.yaml"
    if proj.exists():
        data = _read(proj)
        (data.get("sources") or {}).pop(name, None)
        for group in data.get("groups") or []:
            group["sources"] = [s for s in group.get("sources") or [] if s != name]
        _write(proj, data)
    return removed


def set_interpretation(
    name: str,
    *,
    summary: str | None = None,
    roles: dict[str, str] | None = None,
    portia_dir: str | Path = DEFAULT_DIR,
) -> Path:
    """Author the *judgment* half of a source entry: prose ``summary`` and column ``role``s.

    The mirror of ``index_source``: that one refreshes facts and preserves judgment,
    this one writes judgment and never touches a fact. Fields left as ``None`` are
    left alone, so a caller can set roles without restating the summary.

    This is what the agent calls once it has read the facts and the project context
    — the deterministic side has no business deciding what a column *means*.
    """
    src_file = Path(portia_dir) / "sources" / f"{name}.yaml"
    if not src_file.exists():
        raise ValueError(f"no catalog entry for {name!r} — index it first ({src_file})")

    entry = _read(src_file)
    if summary is not None:
        entry["summary"] = summary
    for col, role in (roles or {}).items():
        match = next((c for c in entry.get("columns", []) if c["name"] == col), None)
        if match is None:
            known = ", ".join(c["name"] for c in entry.get("columns", []))
            raise ValueError(f"no such column {col!r} in {name!r} (have: {known})")
        match["role"] = role

    _write(src_file, entry)
    return src_file


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
        "candidate_keys": profile["candidate_keys"],
        # Layer 2 — per-column detail. Facts refreshed; `role` preserved.
        "columns": columns,
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
    target = project_root(portia_dir) / path
    if not target.exists():
        return True
    now = file_facts(target)
    return any(indexed.get(k) != now[k] for k in STALENESS_FACTS)


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


def _column_facts(col: dict) -> dict:
    """A compact, token-lean slice of the column profile for the catalog."""
    facts = {
        "inferred": col["inferred"],
        "null_rate": col["null_rate"],
        "n_distinct": col["n_distinct"],
        "flags": col["flags"],
    }
    for k in ("median", "std", "top", "top_freq"):  # the richer describe-stats, if present
        if k in col:
            facts[k] = col[k]
    return facts


def _auto_summary(profile: dict) -> str:
    """A plain restatement of the facts — a placeholder until the agent writes a
    semantic read. Deliberately not a judgement about what the data *means*."""
    parts = [f"{profile['n_rows']} rows, {profile['n_cols']} columns."]
    if profile["candidate_keys"]:
        parts.append(f"Candidate key(s): {', '.join(profile['candidate_keys'])}.")
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
    keys = entry.get("candidate_keys") or []
    lines.append(f"  candidate keys: {', '.join(keys) if keys else '(none)'}")
    lines.append("  columns:")
    for c in entry["columns"]:
        role = c.get("role") or "—"
        flags = f"  ⚑ {', '.join(c['flags'])}" if c["flags"] else ""
        lines.append(
            f"    {c['name']}  [role: {role}]  {c['inferred']}, "
            f"{format_rate(c['null_rate'])} null, {c['n_distinct']} distinct{flags}"
        )
    return "\n".join(lines)


# --- yaml io (block style, stable order, hand-editable) ---------------------


def _read(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f) or {}


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        yaml.safe_dump(data, f, sort_keys=False, default_flow_style=False, allow_unicode=True)

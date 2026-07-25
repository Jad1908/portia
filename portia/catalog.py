"""The context catalog — the durable record of *what the data is*.

Sibling of the spec (which records *what we did to it*). Lives in ``.portia/``:

- ``project.yaml`` — the global project context (your words), defined groups, and
  a registry of indexed sources.
- ``sources/<name>.yaml`` — per source, two layers:
    * **Layer 1** ``summary`` — a short prose read of what this data is.
    * **Layer 2** ``columns`` — per column, a ``role`` slot plus the facts the
      checks found.

This is the agent's **memory**: at scale it never sees raw data, only this
context, so a downstream task/agent loads the catalog instead of re-profiling and
re-explaining. Today the agent doesn't exist, so ``summary`` is auto-drafted from
the facts (a plain restatement, not a semantic read) and every ``role`` is an
empty slot — both are placeholders the agent/user fill later.

**The update rule (facts vs judgment):** re-indexing *refreshes the deterministic
facts* but *preserves the prose and roles* — so your corrections are never
clobbered. Nothing here is schema-locked; it's plain, hand-editable YAML.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from portia.checks.profiling import profile_path

DEFAULT_DIR = ".portia"

# Column flags worth calling out in the auto-drafted prose summary. Plain
# restatements of facts — not judgements.
_WATCHOUTS = {
    "high_null": "mostly null",
    "all_null": "empty",
    "numeric_stored_as_text": "numbers stored as text",
    "mixed_types": "mixed types",
    "constant": "constant value",
}


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


def index_source(
    data_path: str | Path, *, portia_dir: str | Path = DEFAULT_DIR, **load_kwargs: Any
) -> Path:
    """Profile a data source and write/refresh its catalog entry.

    Facts are (re)computed from the data; a pre-existing ``summary`` and any set
    ``role`` values are preserved. Registers the source in ``project.yaml``.
    """
    data_path = Path(data_path)
    name = data_path.stem
    d = Path(portia_dir)
    (d / "sources").mkdir(parents=True, exist_ok=True)

    profile = profile_path(str(data_path), **load_kwargs)
    src_file = d / "sources" / f"{name}.yaml"
    existing = _read(src_file) if src_file.exists() else None
    _write(src_file, _source_entry(str(data_path), profile, existing))
    _register(d, name)
    return src_file


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
        "groups": proj.get("groups", []),
        "sources": sources,
    }


# --- building an entry ------------------------------------------------------


def _source_entry(source: str, profile: dict, existing: dict | None) -> dict:
    existing = existing or {}
    prev_roles = {c["name"]: c.get("role") for c in existing.get("columns", [])}
    columns = [
        {"name": col["name"], "role": prev_roles.get(col["name"]), **_column_facts(col)}
        for col in profile["columns"]
    ]
    return {
        "source": source,
        # Layer 1 — prose read. Preserved across re-index (judgment, not fact).
        "summary": existing.get("summary") or _auto_summary(profile),
        "candidate_keys": profile["candidate_keys"],
        # Layer 2 — per-column detail. Facts refreshed; `role` preserved.
        "columns": columns,
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
    parts.append("(auto-drafted from checks — edit freely; the agent will refine this.)")
    return " ".join(parts)


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
            f"{c['null_rate']:.0%} null, {c['n_distinct']} distinct{flags}"
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

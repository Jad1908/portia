"""What the agent is allowed to see and do — as plain functions.

Every tool the copilot can call bottoms out here: ``(args) -> jsonable dict``.
Deliberately **free of any SDK import**, so this file is unit-testable without
``claude-agent-sdk`` installed and the engine never learns which agent harness
is driving it (docs/TECH_STACK.md — engine and agent stay decoupled).

Two rules this module exists to enforce:

- **No raw data, ever.** These functions return compact evidence dicts from the
  checks/catalog layer. The agent has no filesystem tools (see ``session.py``),
  so this surface *is* its entire view of the data — which is what makes the
  loop token-lean at scale.
- **Facts only; no ranking.** Nothing here may sort, score, prioritize or
  suggest an answer. Shaping *how much* we return is fine (that's token budget);
  deciding *what matters* is the agent's job. Re-read ``CLAUDE.md`` →
  "facts vs judgment" before adding a helper that looks like a shortcut.

Errors are raised, not returned — ``tools.py`` catches them at the edge and
turns them into a tool result the agent can react to.
"""

from __future__ import annotations

from typing import Any

from portia import catalog
from portia.checks.profiling import profile_path


def get_context(portia_dir: str = catalog.DEFAULT_DIR) -> dict:
    """The project's memory, compact: prose context, groups, and a source index.

    Per-source detail is deliberately *not* included — one line each, so the
    agent can see the shape of the project cheaply and then pull the sources it
    actually needs via ``profile_source``.
    """
    cat = catalog.load_catalog(portia_dir)
    return {
        "project": cat["project"],
        "groups": cat["groups"],
        "sources": {
            name: {
                "summary": entry.get("summary", ""),
                "n_columns": len(entry.get("columns", [])),
                "candidate_keys": entry.get("candidate_keys", []),
                "interpreted": _is_interpreted(entry),
            }
            for name, entry in cat["sources"].items()
        },
    }


def profile_source(source: str, portia_dir: str = catalog.DEFAULT_DIR) -> dict:
    """Everything the checks found about one source, plus any existing read of it.

    Facts come **fresh from the profiling check**, not from the catalog's stored
    slice. The catalog trims what it keeps (median/std/top) because it's storage;
    the agent needs the full picture — min, max, quartiles, sample values — or it
    starts *deriving* the numbers it wasn't given. Surface evidence generously
    (CLAUDE.md); a derived figure is exactly what this project exists to prevent.

    ``summary`` and each column's ``role`` come from the catalog: whatever
    judgment has been recorded so far, empty until someone writes it.
    """
    cat = catalog.load_catalog(portia_dir)
    entry = cat["sources"].get(source)
    if entry is None:
        known = ", ".join(cat["sources"]) or "(none indexed)"
        raise ValueError(f"no indexed source {source!r} — have: {known}")

    profile = profile_path(entry["source"])
    roles = {c["name"]: c.get("role") for c in entry.get("columns", [])}
    return {
        "source": entry["source"],
        "summary": entry.get("summary", ""),
        "n_rows": profile["n_rows"],
        "n_cols": profile["n_cols"],
        "candidate_keys": profile["candidate_keys"],
        "columns": [{**col, "role": roles.get(col["name"])} for col in profile["columns"]],
    }


def set_interpretation(
    source: str,
    summary: str | None = None,
    roles: dict[str, str] | None = None,
    portia_dir: str = catalog.DEFAULT_DIR,
) -> dict:
    """Record what this data *is* — the agent's read, written to the catalog.

    Writes judgment only; every fact is left untouched. Omit a field to leave
    the existing value alone.
    """
    if summary is None and not roles:
        raise ValueError("nothing to record — pass a summary, roles, or both")

    path = catalog.set_interpretation(source, summary=summary, roles=roles, portia_dir=portia_dir)
    return {
        "source": source,
        "path": str(path),
        "summary_written": summary is not None,
        "roles_written": sorted(roles or {}),
    }


def _is_interpreted(entry: dict) -> bool:
    """Whether a source still carries the auto-drafted placeholder read."""
    summary: Any = entry.get("summary") or ""
    return "(auto-drafted from checks" not in summary

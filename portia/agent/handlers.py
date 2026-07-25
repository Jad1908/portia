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

from pathlib import Path
from typing import Any

from portia import catalog, spec
from portia.checks.join import join_findings as _join_findings
from portia.checks.profiling import profile_path
from portia.core.io import load_frame
from portia.ops import join as join_op
from portia.ops import normalize as normalize_op

#: Ops the spec knows how to execute, and what each step must carry. Validation
#: only — which op to *use* is the agent's call.
_REQUIRED_FIELDS = {
    "join": ("left", "right"),
    "normalize": ("input", "transforms"),
}

#: What each op actually reports, and therefore the only things an ``expect``
#: block can assert against. An expectation on a field the op never emits drifts
#: forever — ``_drift`` compares it to ``None`` on every run — which trains
#: everyone to ignore drift. *Which* of these to assert is judgment; whether a
#: field exists at all is a fact, so it gets checked here rather than hoped for
#: in the prompt.
#:
#: Sourced from the ops themselves, so adding an op means declaring its keys
#: once, next to the code that emits them — and each op's tests assert the
#: declaration still matches a real run.
_EXPECTABLE = {
    "join": join_op.PROVENANCE_KEYS,
    "normalize": normalize_op.PROVENANCE_KEYS,
}


def get_context(portia_dir: str = catalog.DEFAULT_DIR) -> dict:
    """Re-read the project's L1 context: prose, groups, and the source index.

    You already have this — it is in your system prompt. Call it only to pick up
    changes made *during* this session, e.g. after indexing or interpreting a
    source. Per-source detail is deliberately absent; climb to ``describe_source``
    for that.
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


def describe_source(source: str, portia_dir: str = catalog.DEFAULT_DIR) -> dict:
    """L2 — one source's *semantic* map: what it is and what its columns mean.

    Summary, column names, the role recorded for each, and its quality flags —
    but no statistics. This is usually enough to decide whether a source is
    relevant, which columns could be keys, and how two sources might relate.
    Reach for ``profile_source`` only when you need the numbers themselves.
    """
    entry = _entry(source, portia_dir)
    return {
        "source": entry["source"],
        "summary": entry.get("summary", ""),
        "candidate_keys": entry.get("candidate_keys", []),
        "columns": [
            {
                "name": col["name"],
                "role": col.get("role"),
                "inferred": col.get("inferred"),
                "flags": col.get("flags", []),
            }
            for col in entry.get("columns", [])
        ],
    }


def profile_source(source: str, portia_dir: str = catalog.DEFAULT_DIR) -> dict:
    """L3 — everything the checks measured about one source, plus any read of it.

    Facts come **fresh from the profiling check**, not from the catalog's stored
    slice. The catalog trims what it keeps (median/std/top) because it's storage;
    the agent needs the full picture — min, max, quartiles, sample values — or it
    starts *deriving* the numbers it wasn't given. Surface evidence generously
    (CLAUDE.md); a derived figure is exactly what this project exists to prevent.

    ``summary`` and each column's ``role`` come from the catalog: whatever
    judgment has been recorded so far, empty until someone writes it.
    """
    entry = _entry(source, portia_dir)
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


def set_group(
    name: str,
    context: str | None = None,
    sources: list[str] | None = None,
    portia_dir: str = catalog.DEFAULT_DIR,
) -> dict:
    """Record that some sources belong together, and the context they share.

    Use it when sources have something in common that no single source's entry
    can express — same vendor and the same quirks, one system's export, the
    tables that make up one workflow. That shared context then travels with all
    of them.
    """
    if context is None and sources is None:
        raise ValueError("nothing to record — pass a context, sources, or both")

    catalog.set_group(name, context=context, sources=sources, portia_dir=portia_dir)
    return {"group": name, "context_written": context is not None, "sources": sources or []}


# --- the merge loop ---------------------------------------------------------


def join_findings(
    left: str,
    right: str,
    keys: list[str] | None = None,
    left_on: list[str] | None = None,
    right_on: list[str] | None = None,
    portia_dir: str = catalog.DEFAULT_DIR,
) -> dict:
    """What joining two sources on these keys would actually do — plus the rows.

    Returns key-level facts (overlap, coverage, relationship, fan-out, and the
    row counts each `how` would produce) *and* example rows: the unmatched ones,
    the null-key ones, the worst fan-out keys. Nothing here is ranked or scored.
    Whether a dropped row is a catastrophe or a non-event depends on the goal,
    which only you have.

    Call this **before** deciding anything about a merge.
    """
    return _join_findings(
        _frame(left, portia_dir),
        _frame(right, portia_dir),
        on=keys,
        left_on=left_on,
        right_on=right_on,
    )


def record_step(
    spec_path: str,
    step: dict,
    portia_dir: str = catalog.DEFAULT_DIR,
) -> dict:
    """Append a decided step to the spec — the durable residue of this session.

    The step carries the decision (`op`, keys, `how`), an `expect` block stating
    what you predict the numbers will be, and a `rationale` saying *why*. The
    `expect` is what makes it falsifiable later: `run_spec` re-executes and
    reports drift against it. State what the check told you, not what you hope.

    Validation and serialization happen here, in code. The *content* is yours.
    """
    path = Path(spec_path)
    doc: dict[str, Any] = (
        spec.load_spec(path) if path.exists() else {"version": 1, "sources": {}, "steps": []}
    )
    sources: dict[str, str] = doc.setdefault("sources", {})
    steps: list[dict] = doc.setdefault("steps", [])

    _validate_step(step, existing=steps)
    for ref in _source_refs(step, known_steps={s["id"] for s in steps}):
        sources[ref] = _source_path(ref, portia_dir)

    steps.append(step)
    path.parent.mkdir(parents=True, exist_ok=True)
    spec.save_spec(doc, path)
    return {"spec": str(path), "step_id": step["id"], "n_steps": len(steps)}


def run_spec(spec_path: str) -> dict:
    """Re-execute a spec and report what each step actually did, plus any drift.

    Use it to check your own work: record a step, run it, and see whether the
    numbers match what you predicted. Drift is a disagreement between the spec's
    `expect` and today's result — not necessarily an error, but always worth
    surfacing rather than smoothing over.
    """
    results = spec.run_spec(spec.load_spec(spec_path))
    return {
        "spec": spec_path,
        "steps": [
            {"id": r.id, "op": r.op, "provenance": r.provenance, "drift": r.drift} for r in results
        ],
        "has_drift": any(r.has_drift for r in results),
    }


# --- internals --------------------------------------------------------------


def _frame(source: str, portia_dir: str):
    """Load an indexed source. All reading goes through ``core.io.load_frame``."""
    return load_frame(_source_path(source, portia_dir))


def _entry(source: str, portia_dir: str) -> dict:
    cat = catalog.load_catalog(portia_dir)
    entry = cat["sources"].get(source)
    if entry is None:
        known = ", ".join(cat["sources"]) or "(none indexed)"
        raise ValueError(f"no indexed source {source!r} — have: {known}")
    return dict(entry)


def _source_path(source: str, portia_dir: str) -> str:
    return str(_entry(source, portia_dir)["source"])


def _validate_step(step: dict, *, existing: list[dict]) -> None:
    if not step.get("id"):
        raise ValueError("step needs an 'id'")
    if step["id"] in {s["id"] for s in existing}:
        raise ValueError(f"step id {step['id']!r} is already in this spec — pick another")

    op = step.get("op")
    if op not in _REQUIRED_FIELDS:
        raise ValueError(f"unknown op {op!r} — have: {', '.join(_REQUIRED_FIELDS)}")
    missing = [f for f in _REQUIRED_FIELDS[op] if not step.get(f)]
    if missing:
        raise ValueError(f"{op} step needs {', '.join(missing)}")
    if op == "join" and not (step.get("keys") or (step.get("left_on") and step.get("right_on"))):
        raise ValueError("join step needs 'keys', or both 'left_on' and 'right_on'")

    unknown = sorted(set(step.get("expect") or {}) - _EXPECTABLE[op])
    if unknown:
        raise ValueError(
            f"expect refers to {', '.join(repr(u) for u in unknown)}, which {op} never "
            f"reports — so it would drift on every run. Assert only measured fields: "
            f"{', '.join(sorted(_EXPECTABLE[op]))}"
        )


def _source_refs(step: dict, *, known_steps: set[str]) -> list[str]:
    """Source names the step reads, minus anything produced by an earlier step."""
    refs = [step.get(field) for field in ("left", "right", "input")]
    return [r for r in refs if r and r not in known_steps]


def _is_interpreted(entry: dict) -> bool:
    """Whether a source still carries the auto-drafted placeholder read."""
    summary: Any = entry.get("summary") or ""
    return "(auto-drafted from checks" not in summary

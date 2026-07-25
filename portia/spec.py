"""The durable spec — the residue that makes this a product, not a script.

A spec is plain YAML: named ``sources`` and an ordered list of ``steps``. Each
step records the *resolved decision* (op, keys, ``how``), an ``expect`` block
capturing what the check predicted, and an optional ``rationale`` — the **why**
behind the decision. The rationale is the *conclusion* of any one-off analysis
the agent ran to decide (e.g. "right-skewed, skew 2.3 → impute median"); the
analysis code itself is throwaway reasoning and never a step, but its verdict is
kept here so the recipe is **self-justifying**, not just reproducible — the
auditability the product sells (docs/brief.md §6). It is git-diffable, reviewable
in a PR, and re-runnable: ``run_spec`` reloads the sources, re-executes the steps,
and reports **drift** — where today's result diverges from the spec's ``expect``.

Format is intentionally minimal; its schema is meant to *emerge* from real runs,
so resist over-specifying it. Ops so far: ``join`` and ``normalize`` (the latter
takes an ``input`` + ``transforms``, so a workflow can clean a column then join).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
import yaml

from portia.core.io import load_frame
from portia.ops import apply_join, apply_normalize


@dataclass
class StepResult:
    id: str
    op: str
    provenance: dict
    drift: dict = field(default_factory=dict)
    frame: pd.DataFrame | None = None
    rationale: str | None = None  # the recorded "why" — documentation, not executed

    @property
    def has_drift(self) -> bool:
        return bool(self.drift)


def load_spec(path: str | Path) -> dict:
    """Parse a spec YAML file into a plain dict."""
    with open(path) as f:
        return yaml.safe_load(f)


def save_spec(spec: dict, path: str | Path) -> None:
    """Write a spec dict to YAML — stable key order, block style, diff-friendly."""
    with open(path, "w") as f:
        yaml.safe_dump(spec, f, sort_keys=False, default_flow_style=False)


def run_spec(spec: dict, *, base_dir: str | Path = ".") -> list[StepResult]:
    """Load the sources, execute the steps in order, and detect drift per step.

    A step's output is registered under its ``id``, so a later step can consume
    it as a source (workflow chaining, per docs/VISION.md).
    """
    base = Path(base_dir)
    frames: dict[str, pd.DataFrame] = {
        name: load_frame(base / rel) for name, rel in (spec.get("sources") or {}).items()
    }

    results: list[StepResult] = []
    for step in spec.get("steps", []):
        result = _run_step(step, frames)
        frames[step["id"]] = result.frame  # downstream steps may reference it
        results.append(result)
    return results


def _run_step(step: dict, frames: dict[str, pd.DataFrame]) -> StepResult:
    op = step["op"]
    if op == "join":
        # NB: the spec field is `keys`, not `on` — `on` is a reserved boolean in
        # YAML 1.1 (parses to True), so it can't be used as a mapping key.
        out = apply_join(
            frames[step["left"]],
            frames[step["right"]],
            how=step.get("how", "inner"),
            on=step.get("keys"),
            left_on=step.get("left_on"),
            right_on=step.get("right_on"),
        )
    elif op == "normalize":
        out = apply_normalize(frames[step["input"]], step["transforms"])
    else:
        raise ValueError(f"unknown op {op!r} in step {step.get('id')!r}")

    return StepResult(
        id=step["id"],
        op=op,
        provenance=out.provenance,
        drift=_drift(step.get("expect"), out.provenance),
        frame=out.frame,
        rationale=step.get("rationale"),
    )


def _drift(expect: dict | None, provenance: dict) -> dict:
    """Fields where the re-run diverges from what the spec expected."""
    drift = {}
    for key, expected in (expect or {}).items():
        actual = provenance.get(key)
        if actual != expected:
            drift[key] = {"expected": expected, "actual": actual}
    return drift


def render_text(results: list[StepResult]) -> str:
    """Human-readable run summary for the CLI — one block per step, by op."""
    lines = []
    for r in results:
        lines.append(f"[{r.id}]  {r.op}")
        lines.extend(_render_step(r))
        if r.rationale:
            lines.append(f"    ↳ why: {r.rationale}")
        if r.has_drift:
            for key, d in r.drift.items():
                lines.append(f"    ⚠ DRIFT {key}: expected {d['expected']}, got {d['actual']}")
        elif r.provenance.get("op") == "join":
            lines.append("    ✓ matches spec")
        lines.append("")
    return "\n".join(lines)


def _render_step(r: StepResult) -> list[str]:
    p = r.provenance
    if r.op == "join":
        return [
            f"    {p['input_rows']['left']} ⋈ {p['input_rows']['right']} "
            f"= {p['result_rows']} rows  ({p['relationship']}; "
            f"left dropped {p['left_dropped']}, right dropped {p['right_dropped']})",
            *([f"    ⚑ {', '.join(p['flags'])}"] if p["flags"] else []),
        ]
    if r.op == "normalize":
        changes = ", ".join(
            f"{t['column']}:{t['op']}" + (f"(failed {t['n_failed']})" if t.get("n_failed") else "")
            for t in p["transforms"]
        )
        return [
            f"    {p['input_rows']} rows  —  {changes}",
            *([f"    ⚑ {', '.join(p['flags'])}"] if p["flags"] else []),
        ]
    return [f"    {p}"]

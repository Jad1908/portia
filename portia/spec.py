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

Every step also carries an ``outcome``: the post-conditions ``checks.outcome``
measures on the frame it produced. Drift and outcome answer different questions
and fail independently — drift asks whether the *prediction* held, the outcome
asks what actually came out. A correct prediction about a broken join is still a
broken join, and that is precisely how a table missing an entire source once
passed as clean (docs/EVALUATION.md). Two optional step fields feed it: ``grain``,
the author's claim about what one output row is, and ``acknowledge``, naming a
zero-condition they have decided is deliberate — recorded in the YAML so the
override is reviewable rather than silent.

Format is intentionally minimal; its schema is meant to *emerge* from real runs,
so resist over-specifying it. Ops so far: ``join`` and ``normalize`` (the latter
takes an ``input`` + ``transforms``, so a workflow can clean a column then join).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
import yaml

from portia.checks.outcome import BLOCKING_FLAGS, outcome_report, render_outcome
from portia.core.io import load_frame
from portia.ops import apply_join, apply_normalize, apply_sql
from portia.ops.sql import render_text as render_sql


@dataclass
class StepResult:
    id: str
    op: str
    provenance: dict
    drift: dict = field(default_factory=dict)
    frame: pd.DataFrame | None = None
    rationale: str | None = None  # the recorded "why" — documentation, not executed
    #: Post-conditions measured on the frame this step produced (`checks.outcome`).
    #: `provenance` says what the op did; this says what came out — the difference
    #: that let a table missing an entire source pass as clean.
    outcome: dict = field(default_factory=dict)
    #: Blocking flags the step itself declares as deliberate. Recorded in the spec
    #: so an override is a visible, reviewable act rather than a silent one.
    acknowledged: list[str] = field(default_factory=list)

    @property
    def has_drift(self) -> bool:
        return bool(self.drift)

    @property
    def blocking(self) -> list[str]:
        """Zero-conditions this step hit and did not acknowledge."""
        hit = BLOCKING_FLAGS & set(self.outcome.get("flags", []))
        return sorted(hit - set(self.acknowledged))


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
        left, right = step["left"], step["right"]
        out = apply_join(
            frames[left],
            frames[right],
            how=step.get("how", "inner"),
            on=step.get("keys"),
            left_on=step.get("left_on"),
            right_on=step.get("right_on"),
        )
        # Insertion order is load-bearing: it's how pandas' `_x`/`_y` collision
        # suffixes are traced back to a side (see `checks.outcome`).
        inputs = {left: frames[left], right: frames[right]}
        key_columns = _join_key_columns(step)
    elif op == "normalize":
        name = step["input"]
        out = apply_normalize(frames[name], step["transforms"])
        inputs, key_columns = {name: frames[name]}, {}
    elif op == "sql":
        # Only the declared inputs are visible to the query, so an undeclared
        # table is a missing-table error rather than a silent dependency — and
        # `outcome_report` can still say which input contributed nothing.
        inputs = {name: frames[name] for name in step["inputs"]}
        out = apply_sql(inputs, step["sql"])
        key_columns = {}
    else:
        raise ValueError(f"unknown op {op!r} in step {step.get('id')!r}")

    return StepResult(
        id=step["id"],
        op=op,
        provenance=out.provenance,
        drift=_drift(step.get("expect"), out.provenance),
        frame=out.frame,
        rationale=step.get("rationale"),
        outcome=outcome_report(out.frame, inputs=inputs, keys=key_columns, grain=step.get("grain")),
        acknowledged=list(step.get("acknowledge") or []),
    )


def _join_key_columns(step: dict) -> dict[str, list[str]]:
    """Each side's key columns, so the outcome check can exclude them.

    A key is present on both sides by construction, so counting it as a
    contribution would make a join that matched nothing look as though both
    sources had put data in.
    """
    if step.get("keys"):
        keys = list(step["keys"])
        return {step["left"]: keys, step["right"]: keys}
    return {step["left"]: list(step["left_on"]), step["right"]: list(step["right_on"])}


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
        if r.outcome:
            lines.append(render_outcome(r.outcome))
        if r.acknowledged:
            lines.append(f"    ! acknowledged: {', '.join(r.acknowledged)}")
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
    if r.op == "sql":
        # The SQL is shown in full: it is the decision, and a reader skimming a
        # run should not have to open the spec to see what a step actually did.
        return [f"    {line}" for line in render_sql(p).splitlines()]
    return [f"    {p}"]

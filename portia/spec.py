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
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from portia.checks.outcome import (
    BLOCKING_FLAGS,
    describe_contribution,
    describe_grain,
    outcome_report,
    render_outcome,
)
from portia.core import store
from portia.core.io import load_table
from portia.core.present import format_rate, frame_to_markdown, inline
from portia.core.table import Table
from portia.ops import apply_join, apply_normalize, apply_sql
from portia.ops.sql import render_text as render_sql


@dataclass
class StepResult:
    id: str
    op: str
    provenance: dict
    drift: dict = field(default_factory=dict)
    #: The table this step produced — a lazy handle, so holding every step's
    #: output for the life of a session costs a query string each, not the data.
    table: Table | None = None
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


#: Where a step names a table it reads. ``join`` and ``normalize`` name one per
#: field; ``sql`` declares a list, because a query may read several. This is the
#: spec format's own fact, so it lives with the format rather than being restated
#: by every reader — the agent's validator and the app's graph both consult it.
REF_FIELDS = ("left", "right", "input")
REF_LIST_FIELD = "inputs"


def step_inputs(step: dict) -> list[str]:
    """The tables a step reads, in the order it declares them.

    A name here is either an indexed source or an earlier step's ``id`` — the
    step itself doesn't distinguish, and neither does this. That is exactly the
    edge in the workflow graph: *this step's output is that step's input*.
    """
    refs = [step.get(field) for field in REF_FIELDS]
    refs += list(step.get(REF_LIST_FIELD) or [])
    return [r for r in refs if isinstance(r, str) and r]


def load_spec(path: str | Path) -> dict:
    """Parse a spec YAML file into a plain dict."""
    with open(path) as f:
        return yaml.safe_load(f)


def save_spec(spec: dict, path: str | Path) -> None:
    """Write a spec dict to YAML — stable key order, block style, diff-friendly."""
    with open(path, "w") as f:
        yaml.safe_dump(spec, f, sort_keys=False, default_flow_style=False)


def run_spec(spec: dict, *, base_dir: str | Path = ".", con: Any | None = None) -> list[StepResult]:
    """Load the sources, execute the steps in order, and detect drift per step.

    A step's output is registered under its ``id``, so a later step can consume
    it as a source (workflow chaining, per docs/VISION.md).

    **Nothing is materialized.** Sources and step outputs alike are relations, so
    a run holds a query string per step rather than every intermediate at once —
    which is what `run_spec` used to do, and the reason a session's memory grew
    with the length of the workflow rather than the size of the answer.

    ``con`` is the connection the tables live on. Left unset, one is created and
    kept alive by the results that reference it, so a caller can go on previewing
    and writing outputs after the run returns.
    """
    base = Path(base_dir)
    con = con or store.memory()
    tables: dict[str, Table] = {
        name: load_table(base / rel, con, name=name)
        for name, rel in (spec.get("sources") or {}).items()
    }

    results: list[StepResult] = []
    for step in spec.get("steps", []):
        result = _run_step(step, tables)
        if result.table is not None:
            tables[step["id"]] = result.table  # downstream steps may reference it
        results.append(result)
    return results


def write_outputs(results: list[StepResult], out_dir: str | Path) -> list[Path]:
    """Save each step's produced table as ``<out_dir>/<step id>.csv``.

    Both human edges write outputs — ``cli.run --write`` and the app's Run — and
    a table's filename is part of how a spec is read afterwards, so where it
    lands is decided once, here, rather than in each renderer.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for r in results:
        if r.table is not None:
            path = out / f"{r.id}.csv"
            r.table.to_csv(path)  # COPY … TO, so it never passes through memory
            written.append(path)
    return written


def _run_step(step: dict, tables: dict[str, Table]) -> StepResult:
    op = step["op"]
    if op == "join":
        # NB: the spec field is `keys`, not `on` — `on` is a reserved boolean in
        # YAML 1.1 (parses to True), so it can't be used as a mapping key.
        left, right = step["left"], step["right"]
        out = apply_join(
            tables[left],
            tables[right],
            how=step.get("how", "inner"),
            on=step.get("keys"),
            left_on=step.get("left_on"),
            right_on=step.get("right_on"),
            name=step["id"],
        )
        # Insertion order is load-bearing: it's how the `_x`/`_y` collision
        # suffixes are traced back to a side (see `checks.outcome`).
        inputs = {left: tables[left], right: tables[right]}
        key_columns = _join_key_columns(step)
    elif op == "normalize":
        name = step["input"]
        out = apply_normalize(tables[name], step["transforms"], name=step["id"])
        inputs, key_columns = {name: tables[name]}, {}
    elif op == "sql":
        # Only the declared inputs are visible to the query, so an undeclared
        # table is a missing-table error rather than a silent dependency — and
        # `outcome_report` can still say which input contributed nothing.
        inputs = {name: tables[name] for name in step["inputs"]}
        out = apply_sql(inputs, step["sql"], name=step["id"])
        key_columns = {}
    else:
        raise ValueError(f"unknown op {op!r} in step {step.get('id')!r}")

    return StepResult(
        id=step["id"],
        op=op,
        provenance=out.provenance,
        drift=_drift(step.get("expect"), out.provenance),
        table=out.table,
        rationale=step.get("rationale"),
        outcome=outcome_report(out.table, inputs=inputs, keys=key_columns, grain=step.get("grain")),
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


# --- the saved run report ---------------------------------------------------

#: Where a saved run report lands, and how it is named. A timestamp rather than
#: a hash: what a reader wants from a directory of these is "which run was this",
#: and colons are not portable in filenames.
REPORT_STAMP = "%Y-%m-%dT%H-%M-%S"


def write_report(
    results: list[StepResult],
    runs_dir: str | Path,
    *,
    spec_path: str | Path | None = None,
    when: datetime | None = None,
) -> Path:
    """Save a run as markdown — the durable half of pressing Run.

    ``run_spec`` produces measurements and hands them back; without this they
    live only as long as the process that made them, and every previous run in
    this project was written up by hand from a terminal (docs/EVALUATION.md).
    `TECH_STACK.md` asks for exactly this: a generated report as a durable
    summary, in a format that reviews in a PR.

    Markdown rather than JSON because the audience is a person reading a diff.
    The machine-readable stream is the run log's job, not this one's.
    """
    when = when or datetime.now()
    out = Path(runs_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{when.strftime(REPORT_STAMP)}.md"
    path.write_text(render_markdown(results, spec_path=spec_path, when=when))
    return path


def render_markdown(
    results: list[StepResult],
    *,
    spec_path: str | Path | None = None,
    when: datetime | None = None,
) -> str:
    """One run as markdown: per step, the same four groups the app shows.

    Provenance, outcome, drift and acknowledgement stay four separate sections
    here too. They answer different questions, and a report that merges them into
    a status is the mistake this project spent three runs unlearning.
    """
    blocking = sorted({flag for r in results for flag in r.blocking})
    title = Path(spec_path).name if spec_path else "run"
    stamp = (when or datetime.now()).strftime("%Y-%m-%d %H:%M")

    # The heading reads; the path underneath is what ties the report back to the
    # spec that produced it. A report you can't trace to its recipe is an anecdote.
    summary = [f"`{spec_path}`"] if spec_path else []
    summary.append(f"{len(results)} step(s)")
    summary.append(f"**blocking: {', '.join(blocking)}**" if blocking else "no blocking flag")

    lines = [f"# {title} — {stamp}", "", " · ".join(summary), ""]
    for r in results:
        lines += _report_step(r)
    return "\n".join(lines).rstrip() + "\n"


def _report_step(r: StepResult) -> list[str]:
    lines = [f"## {r.id}  ({r.op})", ""]

    if r.acknowledged:
        # First, and never folded into a table. An override is the one thing in
        # a report that must not be possible to skim past (docs/EVALUATION.md).
        lines += [f"> **Acknowledged override:** `{', '.join(r.acknowledged)}`", ">"]
        if r.rationale:
            lines += [f"> {r.rationale}", ""]
        else:
            lines += [""]

    lines += _report_table("provenance", {k: v for k, v in r.provenance.items() if k != "op"})
    lines += _report_table("outcome", _outcome_rows(r.outcome))
    if r.drift:
        drift = {k: f"expected {d['expected']} · actual {d['actual']}" for k, d in r.drift.items()}
        lines += _report_table("drift", drift)
    if r.rationale and not r.acknowledged:
        lines += ["### rationale", "", r.rationale, ""]
    if r.table is not None:
        # The table itself, not just a description of it. A report you can read
        # without the CSV open beside it is the one that gets read.
        lines += ["### preview", "", frame_to_markdown(r.table), ""]
    return lines


def _outcome_rows(outcome: dict) -> dict:
    """The outcome report, flattened to one line per fact."""
    if not outcome:
        return {}
    rows: dict[str, object] = {"produced": f"{outcome.get('n_rows')} × {outcome.get('n_cols')}"}
    for key in ("newly_all_null_columns", "all_null_columns"):
        if outcome.get(key):
            rows[key] = outcome[key]
    if outcome.get("null_rates"):
        rows["null_rates"] = " · ".join(
            f"{col} {format_rate(rate)}" for col, rate in outcome["null_rates"].items()
        )
    for name, contribution in (outcome.get("contribution") or {}).items():
        rows[name] = describe_contribution(contribution)
    if outcome.get("grain"):
        rows["grain"] = describe_grain(outcome["grain"])
    if outcome.get("flags"):
        rows["flags"] = outcome["flags"]
    return rows


def _report_table(heading: str, rows: dict) -> list[str]:
    if not rows:
        return []
    body = [f"| {k} | {inline(v)} |" for k, v in rows.items()]
    return [f"### {heading}", "", "| field | value |", "| --- | --- |", *body, ""]

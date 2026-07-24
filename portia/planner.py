"""Turn a diagnosis into decisions + a proposed spec step (the 'decide' layer).

This is the bridge from `checks` (what's wrong) to `spec` (what we decided). It
takes the join report and produces:

- a **ranked list of decisions** — only the ones that matter, ordered by impact
  (severity, then rows affected), each with options, a suggested default, and the
  quantified stake. This is the deterministic answer to PLAN.md's open problem
  "when to ask vs decide": the engine decides *what* to ask; a copilot will later
  *phrase* it and collect the answer.
- a **proposed spec step** using the suggested answers.

It **refuses to propose** when a blocker is present (e.g. key dtype mismatch —
the '123' vs 123 silent-miss): never silently record the wrong thing.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from portia.checks.join import join_report
from portia.spec import join_step

_SEVERITY_RANK = {"blocker": 0, "warning": 1, "info": 2}


@dataclass
class Decision:
    topic: str  # join_type | key_dtype | fan_out | null_keys
    severity: str  # blocker | warning | info
    question: str
    options: list[str]
    suggested: str
    impact_rows: int  # rows at stake — the ranking driver


@dataclass
class Proposal:
    step: dict
    decisions: list[Decision] = field(default_factory=list)
    diagnosis: dict = field(default_factory=dict)

    @property
    def blocked(self) -> bool:
        return any(d.severity == "blocker" for d in self.decisions)


def propose_join_step(
    left: pd.DataFrame,
    right: pd.DataFrame,
    *,
    step_id: str,
    left_name: str,
    right_name: str,
    on: str | list[str] | None = None,
    left_on: str | None = None,
    right_on: str | None = None,
) -> Proposal:
    """Diagnose the join, rank the decisions, and propose a spec step."""
    diagnosis = join_report(left, right, on=on, left_on=left_on, right_on=right_on)
    decisions = _rank(_decisions(diagnosis))
    how = _recommend_how(diagnosis)
    step = join_step(
        step_id,
        left=left_name,
        right=right_name,
        how=how,
        report=diagnosis,
        on=on,
        left_on=left_on,
        right_on=right_on,
    )
    return Proposal(step=step, decisions=decisions, diagnosis=diagnosis)


def _recommend_how(diagnosis: dict) -> str:
    """Conservative default: if an inner join would silently drop left rows,
    keep them with a left join; otherwise inner is clean and symmetric."""
    return "left" if diagnosis["joins"]["inner"]["left_dropped"] > 0 else "inner"


def _decisions(diagnosis: dict) -> list[Decision]:
    inner = diagnosis["joins"]["inner"]
    dropped_left = inner["left_dropped"]
    flags = diagnosis["flags"]
    decisions: list[Decision] = []

    if "key_dtype_mismatch" in flags:
        lk, rk = diagnosis["key_dtypes"]["left"], diagnosis["key_dtypes"]["right"]
        decisions.append(
            Decision(
                topic="key_dtype",
                severity="blocker",
                question=f"key types differ ({lk} vs {rk}) — they will not match; coerce or fix first",
                options=["coerce", "rename_keys", "abort"],
                suggested="coerce",
                impact_rows=diagnosis["left"]["n_rows"],
            )
        )

    # The join type is always a decision — even when it's an easy one.
    decisions.append(
        Decision(
            topic="join_type",
            severity="warning" if dropped_left > 0 else "info",
            question=(
                f"an inner join drops {dropped_left} unmatched left row(s) — keep them (left) "
                "or drop them (inner)?"
                if dropped_left > 0
                else "every left row matches — inner and left are equivalent here"
            ),
            options=["left", "inner", "outer"],
            suggested=_recommend_how(diagnosis),
            impact_rows=dropped_left,
        )
    )

    if "fan_out" in flags or "many_to_many" in flags:
        fan = diagnosis["fan_out"]["max_left_to_right"]
        decisions.append(
            Decision(
                topic="fan_out",
                severity="warning",
                question=(
                    f"duplicate keys cause fan-out (one left row → up to {fan} right) — "
                    "intended, or dedupe a side first?"
                ),
                options=["proceed", "dedupe_left", "dedupe_right"],
                suggested="proceed",
                impact_rows=inner["result_rows"],
            )
        )

    null_keys = diagnosis["left"]["n_null_keys"] + diagnosis["right"]["n_null_keys"]
    if null_keys > 0:
        decisions.append(
            Decision(
                topic="null_keys",
                severity="info",
                question=f"{null_keys} row(s) have null keys and cannot match — expected?",
                options=["keep_as_unmatched", "drop", "fill"],
                suggested="keep_as_unmatched",
                impact_rows=null_keys,
            )
        )

    return decisions


def _rank(decisions: list[Decision]) -> list[Decision]:
    """Most important first: blockers, then by rows at stake."""
    return sorted(decisions, key=lambda d: (_SEVERITY_RANK[d.severity], -d.impact_rows))


_MARKERS = {"blocker": "⛔", "warning": "⚠", "info": "ℹ"}


def render_text(proposal: Proposal) -> str:
    """Human-readable proposal for the CLI."""
    step = proposal.step
    keys = step.get("keys", f"{step.get('left_on')}={step.get('right_on')}")
    lines = [
        f"proposed step '{step['id']}':  {step['left']} ⋈ {step['right']}  "
        f"on {keys}  →  how={step['how']}",
        "",
        "decisions (most impactful first):",
    ]
    for d in proposal.decisions:
        lines.append(f"  {_MARKERS[d.severity]} [{d.topic}] {d.question}")
        lines.append(
            f"       options: {d.options}   suggested: {d.suggested}   ~{d.impact_rows} rows"
        )
    lines.append("")
    if proposal.blocked:
        lines.append("⛔ BLOCKED — resolve the blocker(s) above before this step can be recorded.")
    else:
        lines.append("→ ready to record with the suggested answers.")
    return "\n".join(lines)

"""The floor: what holds on every route, checked on any log (`docs/BENCHMARK_EVAL.md` §6.3).

Three invariants, none of them a measure of quality:

- **Number provenance.** Every number in a reply appears in something the
  model saw before it wrote the reply (`numbers.py`). Reported as a list of
  misses with the sentence around each, never only a count.
- **No zero acknowledged with nobody asked, outside autopilot.** A step
  written with ``acknowledge`` set, approved by a person, with no question
  earlier in the same exchange. In autopilot the write is logged ``auto`` and
  the acknowledgement is the user's responsibility by design (§10.3): those
  are counted apart and never called violations.
- **Nothing entered a spec unmeasured.** The engine already refuses this;
  the check confirms that every successful `record_step` carried its
  measurement back.

A report is counts and a list. It says what broke a rule and where to look,
and nothing about whether the run was good.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from devtools.bench import numbers
from devtools.traces.logs import portia_dirs
from portia import runlog
from portia.agent import events
from portia.core.serialize import to_json_line

#: The tool whose successful result is a measurement, and whose input may
#: acknowledge a zero. Named once, off the handler's name.
RECORD_STEP = "record_step"

#: The key a `record_step` result carries when the step was run and measured.
MEASURED_KEY = "outcome"

#: What a flag is about.
NUMBER = "number"
DATE = "date"
ZERO = "zero_acknowledged_without_asking"
UNMEASURED = "step_recorded_unmeasured"


@dataclass(frozen=True)
class Flag:
    """One thing that broke a rule, with where to look."""

    kind: str
    #: Index into the transcript's events.
    event: int
    written: str
    context: str
    value: str | None = None
    nearest: str | None = None
    #: Relative distance to the nearest evidence value, for a number.
    distance: float | None = None

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "kind": self.kind,
            "event": self.event,
            "written": self.written,
            "context": self.context,
        }
        if self.value is not None:
            out["value"] = self.value
        if self.nearest is not None:
            out["nearest"] = self.nearest
            out["distance"] = self.distance
        return out


@dataclass
class Report:
    """One log's floor: the counts, and the flags."""

    log: Path
    strict: bool
    numbers: int = 0
    matched: dict[str, int] = field(default_factory=lambda: dict.fromkeys(numbers.HOWS, 0))
    small: int = 0
    dates: int = 0
    acknowledged: dict[str, int] = field(
        default_factory=lambda: {"auto": 0, "after_asking": 0, "without_asking": 0}
    )
    steps: dict[str, int] = field(default_factory=lambda: {"recorded": 0, "unmeasured": 0})
    flags: list[Flag] = field(default_factory=list)

    @property
    def flagged_numbers(self) -> int:
        return sum(1 for f in self.flags if f.kind in (NUMBER, DATE))

    def as_dict(self) -> dict[str, Any]:
        return {
            "log": str(self.log),
            "strict": self.strict,
            "numbers": self.numbers,
            "matched": dict(self.matched),
            "small": self.small,
            "dates": self.dates,
            "flagged": self.flagged_numbers,
            "acknowledged": dict(self.acknowledged),
            "steps": dict(self.steps),
            "flags": [f.as_dict() for f in self.flags],
        }


@dataclass
class _StepCall:
    event: int
    step: dict[str, Any]
    auto: bool | None = None


def check(transcript: runlog.Transcript, *, strict: bool = False) -> Report:
    """Walk one log in order and report what broke a rule.

    The evidence the model could copy from grows as the walk goes: the prompts
    it read (line two), each message and answer, each tool result, and the
    arguments it typed itself. A question's text is not evidence, because a
    number the model invents in a question and repeats in the reply would
    otherwise pass.
    """
    report = Report(log=transcript.path, strict=strict)
    evidence = numbers.Evidence()
    if transcript.prompts:
        evidence.add(to_json_line(transcript.prompts))
    asked_this_exchange = False
    pending: dict[str, _StepCall] = {}

    for index, event in enumerate(transcript.events):
        kind, data = event.kind, event.data
        if kind == events.PROMPT:
            asked_this_exchange = False
            evidence.add(str(data.get("text") or ""))
        elif kind == events.ANSWER:
            # The values only: an answer is keyed by the question's text, and
            # the question is the model's.
            evidence.add(to_json_line(list((data.get("answers") or {}).values())))
        elif kind == events.QUESTION:
            asked_this_exchange = True
        elif kind == events.TOOL_CALL:
            evidence.add(to_json_line(data.get("input") or {}))
            if events.tool_label(str(data.get("name") or "")) == RECORD_STEP:
                step = (data.get("input") or {}).get("step") or {}
                pending[str(data.get("id") or index)] = _StepCall(index, step)
        elif kind == events.APPROVAL_RESULT:
            if events.tool_label(str(data.get("name") or "")) == RECORD_STEP:
                waiting = next((c for c in pending.values() if c.auto is None), None)
                if waiting is not None:
                    waiting.auto = bool(data.get("auto"))
        elif kind == events.TOOL_RESULT:
            text = str(data.get("text") or "")
            evidence.add(text)
            call = pending.pop(str(data.get("id") or ""), None)
            if call is not None and not data.get("is_error"):
                _check_step(report, call, text, index, asked_this_exchange)
        elif kind == events.TEXT:
            _check_reply(report, str(data.get("text") or ""), evidence, index, strict=strict)
    return report


def _check_step(report: Report, call: _StepCall, text: str, index: int, asked: bool) -> None:
    report.steps["recorded"] += 1
    try:
        result = json.loads(text)
    except ValueError:
        result = {}
    if not isinstance(result, dict) or MEASURED_KEY not in result:
        report.steps["unmeasured"] += 1
        report.flags.append(
            Flag(UNMEASURED, index, str(call.step.get("id") or "?"), text[: numbers.CONTEXT_CHARS])
        )
    if not call.step.get("acknowledge"):
        return
    if call.auto:
        report.acknowledged["auto"] += 1
    elif asked:
        report.acknowledged["after_asking"] += 1
    else:
        report.acknowledged["without_asking"] += 1
        report.flags.append(
            Flag(
                ZERO,
                call.event,
                str(call.step.get("id") or "?"),
                f"acknowledge: {call.step.get('acknowledge')}",
            )
        )


def _check_reply(
    report: Report, text: str, evidence: numbers.Evidence, index: int, *, strict: bool
) -> None:
    for number in numbers.numbers_in(text):
        if number.small:
            report.small += 1
            continue
        report.numbers += 1
        how = numbers.match(number, evidence, strict=strict)
        if how:
            report.matched[how] += 1
            continue
        near = numbers.nearest(number, evidence)
        report.flags.append(
            Flag(
                NUMBER,
                index,
                number.written,
                numbers.context(text, number.start),
                value=_plain(number.value),
                nearest=_plain(near[0]) if near else None,
                distance=float(round(near[1], 4)) if near else None,
            )
        )
    for date in numbers.dates_in(text):
        report.dates += 1
        if date not in evidence.dates:
            at = text.find(date)
            report.flags.append(Flag(DATE, index, date, numbers.context(text, max(at, 0))))


def _plain(value: Decimal) -> str:
    """A decimal as a person writes it: no exponent, no trailing zeros."""
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


# --- many logs ---------------------------------------------------------------


def logs_under(paths: list[Path]) -> list[Path]:
    """Every log the paths name: a file as is, a `.portia/` folder's logs, a root walked."""
    found: list[Path] = []
    for path in paths:
        if path.is_file():
            found.append(path)
        elif path.is_dir() and path.name == runlog.DEFAULT_DIR:
            found.extend(runlog.logs_in(path))
        elif path.is_dir():
            for portia_dir in portia_dirs(path):
                found.extend(runlog.logs_in(portia_dir))
    return sorted(set(found))


def check_all(paths: list[Path], *, strict: bool = False) -> list[Report]:
    return [check(runlog.read(log), strict=strict) for log in logs_under(paths)]


def totals(reports: list[Report]) -> dict[str, Any]:
    """The counts summed over every report. Sums, k of N, nothing composite."""
    out: dict[str, Any] = {
        "logs": len(reports),
        "numbers": sum(r.numbers for r in reports),
        "matched": {how: sum(r.matched[how] for r in reports) for how in numbers.HOWS},
        "small": sum(r.small for r in reports),
        "dates": sum(r.dates for r in reports),
        "flagged": sum(r.flagged_numbers for r in reports),
        "acknowledged": {
            key: sum(r.acknowledged[key] for r in reports)
            for key in ("auto", "after_asking", "without_asking")
        },
        "steps": {key: sum(r.steps[key] for r in reports) for key in ("recorded", "unmeasured")},
    }
    return out


def render(reports: list[Report], *, show_flags: bool = True) -> str:
    """The reports as a person reads them at a terminal."""
    lines: list[str] = []
    for report in reports:
        matched = ", ".join(f"{report.matched[how]} {how}" for how in numbers.HOWS)
        lines.append(
            f"{report.log}: {report.numbers} numbers ({matched}), "
            f"{report.small} small unchecked, {report.dates} dates, "
            f"{report.flagged_numbers} flagged; steps {report.steps['recorded']} recorded, "
            f"{report.steps['unmeasured']} unmeasured; zeros acknowledged "
            f"{report.acknowledged['auto']} auto, {report.acknowledged['after_asking']} after asking, "
            f"{report.acknowledged['without_asking']} without"
        )
        if show_flags:
            for flag in report.flags:
                lines.append("  " + _render_flag(flag))
    total = totals(reports)
    matched = ", ".join(f"{total['matched'][how]} {how}" for how in numbers.HOWS)
    lines.append(
        f"TOTAL {total['logs']} logs: {total['numbers']} numbers ({matched}), "
        f"{total['small']} small unchecked, {total['dates']} dates, {total['flagged']} flagged; "
        f"steps {total['steps']['recorded']} recorded, {total['steps']['unmeasured']} unmeasured; "
        f"zeros acknowledged {total['acknowledged']['auto']} auto, "
        f"{total['acknowledged']['after_asking']} after asking, "
        f"{total['acknowledged']['without_asking']} without"
    )
    return "\n".join(lines)


def _render_flag(flag: Flag) -> str:
    where = f"#{flag.event}"
    if flag.kind == NUMBER:
        near = (
            f"  nearest {flag.nearest} ({flag.distance:.1%} off)"
            if flag.nearest is not None and flag.distance is not None
            else "  nothing near"
        )
        return f"{where} {flag.written!r:>12}  …{flag.context}…{near}"
    if flag.kind == DATE:
        return f"{where} date {flag.written}  …{flag.context}…"
    return f"{where} {flag.kind} {flag.written}  {flag.context}"

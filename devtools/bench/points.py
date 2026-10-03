"""Decision points: one prepared project, one message, N runs, the outcomes counted (`docs/BENCHMARK_EVAL.md` §6.4).

A session is chaotic and a single judgement is not. Build the project state
that sits right before one decision (indexed, and the user says "join the
bookings to the events"), run that one exchange ten times, and count what
happened: measured the fan-out and said so, measured and joined anyway,
joined without measuring. Compare the counts before and after a prompt
edit. One exchange instead of a session, so dozens cost what a handful of
full runs cost.

**Outcomes are predicates over what the run left**, never a reading of the
transcript's quality, and never a route's shape (§2.7, §6.2): whether a
question was asked, whether any result showed a fan-out before a step was
recorded, whether a step was recorded at all. A run's outcome is the set of
predicates that held; the tally counts each combination as k of N and names
no combination better than another. Which combination the prompt should
produce is the reader's judgement, made with the counts in front of them.

A point is a case with one thread of one message, run N times on fresh
copies: the case machinery prepares the project and drives the exchange
(`run.py`), and this module adds the predicates and the tally.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from devtools.bench import case as cases
from devtools.bench import run as runner
from devtools.bench.invariants import RECORD_STEP
from portia import runlog
from portia.agent import events, session
from portia.core.serialize import to_json
from portia.spec import REPORT_STAMP

#: The check whose result says what a join would do, by the handler's name.
JOIN_FINDINGS = "join_findings"

#: The flag `checks.join` raises when a key multiplies rows.
FAN_OUT_FLAG = "fan_out"

#: Where points land when nowhere is given.
DEFAULT_OUT = runner.DEFAULT_OUT / "points"
RUNS_DEFAULT = 10
TALLY_FILE = "tally.json"
RUN_FILE = "run.json"
THREAD = "point"

#: How a combination of predicates is named in a tally: the ones that held,
#: joined in the order the point lists them; ``none`` when none did.
JOINER = "+"
NONE_HELD = "none"


@dataclass(frozen=True)
class Point:
    name: str
    case: cases.Case
    message: str
    predicates: tuple[str, ...]
    runs: int = RUNS_DEFAULT
    path: Path | None = None


# --- the vocabulary -------------------------------------------------------------


def _successful(transcript: runlog.Transcript, tool: str) -> list[tuple[int, dict[str, Any], Any]]:
    """Each successful call of ``tool``: its index, its input and its parsed result."""
    calls: dict[str, tuple[int, dict[str, Any]]] = {}
    out: list[tuple[int, dict[str, Any], Any]] = []
    for index, event in enumerate(transcript.events):
        data = event.data
        if (
            event.kind == events.TOOL_CALL
            and events.tool_label(str(data.get("name") or "")) == tool
        ):
            calls[str(data.get("id") or index)] = (index, dict(data.get("input") or {}))
        elif event.kind == events.TOOL_RESULT and str(data.get("id") or "") in calls:
            index_of_call, inp = calls.pop(str(data.get("id")))
            if data.get("is_error"):
                continue
            try:
                parsed = json.loads(str(data.get("text") or ""))
            except ValueError:
                parsed = None
            out.append((index_of_call, inp, parsed))
    return out


def asked(transcript: runlog.Transcript) -> bool:
    """A question reached the user."""
    return any(e.kind == events.QUESTION for e in transcript.events)


def recorded_step(transcript: runlog.Transcript) -> bool:
    """A step was recorded and measured."""
    return bool(_successful(transcript, RECORD_STEP))


def recorded_join(transcript: runlog.Transcript) -> bool:
    """A join step was recorded."""
    return any(
        (inp.get("step") or {}).get("op") == "join"
        for _, inp, _ in _successful(transcript, RECORD_STEP)
    )


def _first_record(transcript: runlog.Transcript) -> int:
    recorded = _successful(transcript, RECORD_STEP)
    return recorded[0][0] if recorded else len(transcript.events)


def measured_join(transcript: runlog.Transcript) -> bool:
    """`join_findings` returned before any step was recorded: evidence before the decision (§6.2)."""
    first = _first_record(transcript)
    return any(index < first for index, _, _ in _successful(transcript, JOIN_FINDINGS))


def measured_fan_out(transcript: runlog.Transcript) -> bool:
    """A `join_findings` result before the first recorded step said the key multiplies rows."""
    first = _first_record(transcript)
    for index, _, parsed in _successful(transcript, JOIN_FINDINGS):
        if index >= first or not isinstance(parsed, dict):
            continue
        if FAN_OUT_FLAG in (parsed.get("flags") or []):
            return True
    return False


def ended_clean(transcript: runlog.Transcript) -> bool:
    """The exchange ended on its own, not on a cap or an error."""
    results = [e for e in transcript.events if e.kind == events.RESULT]
    return bool(results) and results[-1].data.get("subtype") == "success"


#: Every predicate a point may name. Adding one is adding a function here.
PREDICATES: dict[str, Callable[[runlog.Transcript], bool]] = {
    "asked": asked,
    "recorded_step": recorded_step,
    "recorded_join": recorded_join,
    "measured_join": measured_join,
    "measured_fan_out": measured_fan_out,
    "ended_clean": ended_clean,
}


def outcome(transcript: runlog.Transcript, predicates: tuple[str, ...]) -> dict[str, bool]:
    return {name: PREDICATES[name](transcript) for name in predicates}


def combination(held: dict[str, bool]) -> str:
    names = [name for name, value in held.items() if value]
    return JOINER.join(names) if names else NONE_HELD


# --- the point file --------------------------------------------------------------


def load(path: str | Path) -> Point:
    """Read a point file: a case to build on, one message, the predicates to count, how many runs."""
    path = Path(path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: a point is a mapping")
    if not raw.get("case"):
        raise ValueError(f"{path}: a point names the case it builds on (`case:`)")
    case_path = (path.parent / str(raw["case"])).resolve()
    base = cases.load(case_path)
    message = str(raw.get("message") or "").strip()
    if not message:
        raise ValueError(f"{path}: a point has one message and this has none")
    predicates = tuple(str(p) for p in raw.get("predicates") or ())
    unknown = [p for p in predicates if p not in PREDICATES]
    if unknown:
        raise ValueError(
            f"{path}: unknown predicates {', '.join(unknown)}; the vocabulary is {', '.join(PREDICATES)}"
        )
    if not predicates:
        raise ValueError(f"{path}: a point counts at least one predicate")
    runs = RUNS_DEFAULT if raw.get("runs") is None else int(raw["runs"])
    if runs <= 0:
        raise ValueError(f"{path}: runs must be positive")
    case = cases.Case(
        **{
            **base.__dict__,
            "name": str(raw.get("name") or path.stem),
            "threads": (cases.Thread(THREAD, (message,)),),
            "variants": {},
            "path": case_path,
        }
    )
    variant = raw.get("variant")
    if variant:
        case = cases.Case(
            **{
                **base.with_variant(str(variant)).__dict__,
                **{
                    "name": case.name,
                    "threads": case.threads,
                    "variants": {},
                    "path": case_path,
                },
            }
        )
    return Point(
        name=case.name, case=case, message=message, predicates=predicates, runs=runs, path=path
    )


# --- running and counting ----------------------------------------------------------


@dataclass
class Tally:
    point: str
    predicates: tuple[str, ...]
    runs: list[dict[str, Any]] = field(default_factory=list)

    @property
    def n(self) -> int:
        return len(self.runs)

    def combinations(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for run in self.runs:
            key = combination(run["outcome"])
            counts[key] = counts.get(key, 0) + 1
        return counts

    def per_predicate(self) -> dict[str, int]:
        return {
            name: sum(1 for run in self.runs if run["outcome"].get(name))
            for name in self.predicates
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "point": self.point,
            "predicates": list(self.predicates),
            "n": self.n,
            "combinations": self.combinations(),
            "per_predicate": self.per_predicate(),
            "runs": self.runs,
        }


async def run_point(
    point: Point,
    *,
    runs: int | None = None,
    model: str = session.DEFAULT_MODEL,
    effort: str | None = None,
    provider: str = session.DEFAULT_PROVIDER,
    out: Path = DEFAULT_OUT,
    client_factory: Callable[[Any], Any] | None = None,
    when: datetime | None = None,
) -> tuple[Tally, Path]:
    """The one exchange, ``runs`` times on fresh copies; the tally written beside the runs."""
    when = when or datetime.now()
    folder = out / point.name / when.strftime(REPORT_STAMP)
    tally = Tally(point.name, point.predicates)
    for i in range(runs or point.runs):
        run = await runner.run_case(
            point.case,
            model=model,
            effort=effort,
            provider=provider,
            out=folder / f"run-{i + 1}",
            client_factory=client_factory,
            when=when,
        )
        (thread,) = run.threads
        transcript = runlog.read(thread.log)
        held = outcome(transcript, point.predicates)
        record = {
            "run": i + 1,
            "log": str(thread.log),
            "outcome": held,
            "combination": combination(held),
            "summary": thread.summary,
            "fallbacks": thread.fallbacks,
        }
        tally.runs.append(record)
        (run.project.parent / RUN_FILE).write_text(to_json(record) + "\n", encoding="utf-8")
        # Written after every run, so a batch that dies mid-way still counts what it got to.
        write_tally(tally, folder)
    return tally, write_tally(tally, folder)


def write_tally(tally: Tally, folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / TALLY_FILE
    path.write_text(to_json(tally.as_dict()) + "\n", encoding="utf-8")
    return path


def render(tally: Tally) -> str:
    """The tally as a person reads it: k of N per combination, then per predicate."""
    lines = [f"{tally.point}: {tally.n} runs"]
    for key, count in sorted(tally.combinations().items(), key=lambda kv: kv[0]):
        lines.append(f"  {count} of {tally.n}  {key}")
    lines.append("  per predicate:")
    for name, count in tally.per_predicate().items():
        lines.append(f"    {name}: {count} of {tally.n}")
    return "\n".join(lines)

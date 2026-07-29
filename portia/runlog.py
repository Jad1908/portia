"""The run log — a copilot turn, kept.

Every result in `docs/EVALUATION.md` was scored by hand off a terminal
transcript: some of it pasted twice, some lost to a `^C`, two runs conflated
while being written up. Run 7's write-up says outright that only its last 90
lines survived and that a finding degraded because of it. A prompt change is
evaluated today by reading two walls of text side by side and trusting memory,
which is not a measurement — and it is the thing that makes tuning the loop feel
impossible.

The seam already existed. `agent/events.py` normalizes every SDK message into
`Event(kind, data)` precisely so something other than a terminal can consume it;
persisting that stream is most of the work. **One JSONL per turn** under
`.portia/runs/`, one object per line, opened by a header recording the model,
effort, prompt, cwd and the portia sha that produced it — in the project
directory, so a run travels with the spec it produced.

**Written at the edge, never in the engine.** `cli/chat.run_turn` and
`ui/turn.start` each tee their event stream through here. The engine must not
learn it is being observed, or `events.py` stops being a clean seam and becomes
a logging framework (docs/EVALUATION.md → "The run log").

**Logs are project-local, and that is the whole storage model.** There is no
central store, no index, and nothing written outside the project — a turn is
only interpretable beside the catalog it read and the spec it wrote, so a
global folder of transcripts referring to tables you would have to go find is
worse than no folder. The consequences are worth stating plainly rather than
discovering: **deleting a project deletes its turns**, there is no retention,
rotation or delete path (logs accumulate; tool results are the bulk), and
nothing aggregates across projects. Reading another project's log needs no
copying — every reader here takes a path, and `cli.runs --dir <proj>/.portia`
works from anywhere.

Nothing here judges a run. `summary` counts what happened — rungs pulled and in
what order, how often it asked, which ops it chose, what a turn cost — and every
one of those is a **cost and behaviour descriptor, not a correctness signal**.
"Asked three times" is neither good nor bad without knowing whether it should
have; only the answer keys make a number mean anything. A run log that scored
runs would be `CLAUDE.md`'s facts-vs-judgment line broken in the one place it
would be least visible.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from portia.agent import events
from portia.catalog import DEFAULT_DIR
from portia.core.serialize import to_json_line

#: The filename stamp, shared with the saved spec reports on purpose: what a
#: reader wants from a directory of either is "which run was this", and the two
#: kinds end up listed side by side.
from portia.spec import REPORT_STAMP

#: Under the project's `.portia/`, beside `sources/` and the store. Not the
#: project-root `runs/` that holds saved *spec-run* reports (`ui/engine.py`):
#: those are markdown, written for a human reading a diff, and a run of a recipe
#: is a different artifact from a turn of the copilot.
RUNS_DIR = "runs"

#: The log's first line. Shaped like an event so a reader can parse every line
#: the same way, but deliberately *not* an `events` kind: it describes the turn
#: rather than being something that happened during it.
HEADER = "header"

#: Tool calls whose name says the copilot climbed the disclosure ladder are not
#: enumerated here. `sequence` reports the calls in the order they happened and
#: lets the reader see the climb; teaching this module which rung is which would
#: put the ladder in two places, and the second would go stale.


@dataclass
class Log:
    """An open run log. One line per event, appended as it happens."""

    path: Path

    def event(self, event: events.Event) -> None:
        self.write(event.kind, event.data)

    def write(self, kind: str, data: dict[str, Any]) -> None:
        """Append one record, whole.

        Reopened per line rather than held open, because the failure this log
        exists to stop is *losing the tail*: a turn ends in a `^C` as often as
        not, and the unwind goes through an async generator, a NiceGUI task or a
        SDK subprocess depending on which edge is driving. At a few hundred
        events a turn the reopen costs nothing, and there is no handle left to
        close on any of those paths.
        """
        line = to_json_line({"kind": kind, "data": data})
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")


def start(
    portia_dir: str | Path = DEFAULT_DIR,
    *,
    prompt: str,
    model: str,
    effort: str | None = None,
    cwd: str | Path = ".",
    when: datetime | None = None,
) -> Log:
    """Open a log for one turn and write its header.

    The header is what makes two runs comparable: `EVALUATION.md` can only put
    Run 6 next to Run 5 because they differ in model and effort and nothing
    else, and that fact currently survives only in prose someone remembered to
    write. `portia_sha` is the other half — which build of the prompts and the
    engine this run was talking to.
    """
    when = when or datetime.now()
    directory = Path(portia_dir) / RUNS_DIR
    directory.mkdir(parents=True, exist_ok=True)
    log = Log(_free_path(directory, when))
    log.write(
        HEADER,
        {
            "started": when.isoformat(timespec="seconds"),
            "prompt": prompt,
            "model": model,
            "effort": effort,
            "cwd": str(Path(cwd).resolve()),
            "portia_sha": portia_sha(),
        },
    )
    return log


def _free_path(directory: Path, when: datetime) -> Path:
    """`<stamp>.jsonl`, suffixed if a turn in the same second already took it.

    `index` runs two turns back to back, which is exactly how a one-second stamp
    collides — and appending a second turn's events onto the first one's log is
    the run-conflation this module exists to end.
    """
    stamp = when.strftime(REPORT_STAMP)
    path = directory / f"{stamp}.jsonl"
    n = 2
    while path.exists():
        path = directory / f"{stamp}-{n}.jsonl"
        n += 1
    return path


def portia_sha() -> str | None:
    """The short sha of the portia checkout that ran the turn, if there is one.

    Deliberately portia's repo and not the project's: the question a past run
    has to answer is which prompts and which engine produced it. Returns None
    rather than raising — an installed copy with no git around it still logs.
    """
    root = Path(__file__).resolve().parent.parent
    try:
        done = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout.strip() or None if done.returncode == 0 else None


# --- reading one back -------------------------------------------------------


@dataclass(frozen=True)
class Run:
    """One logged turn: its header, and the events in the order they happened."""

    path: Path
    header: dict[str, Any] = field(default_factory=dict)
    events: list[events.Event] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.path.stem


def runs_in(portia_dir: str | Path = DEFAULT_DIR) -> list[Path]:
    """Every logged turn in a project, newest first (the stamp sorts)."""
    directory = Path(portia_dir) / RUNS_DIR
    return sorted(directory.glob("*.jsonl"), reverse=True) if directory.is_dir() else []


def find(name: str, portia_dir: str | Path = DEFAULT_DIR) -> Path | None:
    """Resolve what a human typed: a path, a full stem, or a unique prefix."""
    direct = Path(name)
    if direct.is_file():
        return direct
    candidates = runs_in(portia_dir)
    return next((p for p in candidates if p.stem == name or p.stem.startswith(name)), None)


def read_header(path: str | Path) -> dict[str, Any]:
    """Just the header, without parsing the transcript under it.

    A list of turns wants the model and the prompt and nothing else, and a
    transcript is mostly tool results — a profile of a wide table is kilobytes.
    Reading one line to draw one row keeps a pane that redraws on every event
    from re-parsing every past run each time.
    """
    with Path(path).open(encoding="utf-8") as handle:
        first = handle.readline()
    try:
        record = json.loads(first)
    except ValueError:
        return {}
    if not isinstance(record, dict) or record.get("kind") != HEADER:
        return {}
    return record.get("data") or {}


def read(path: str | Path) -> Run:
    """Parse a log back into a header and a list of events.

    Unparseable lines are skipped rather than raised on. The one that realistic-
    ally goes wrong is a truncated tail — the process died mid-write — and a
    reader that refuses the whole file over its last half-line would throw away
    the transcript in exactly the case this module was built for.
    """
    path = Path(path)
    header: dict[str, Any] = {}
    collected: list[events.Event] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if not isinstance(record, dict):
            continue
        kind, data = record.get("kind"), record.get("data") or {}
        if kind == HEADER:
            header = data
        elif isinstance(kind, str):
            collected.append(events.Event(kind, data))
    return Run(path=path, header=header, events=collected)


# --- what it can answer without any labels ----------------------------------


def summary(run: Run) -> dict[str, Any]:
    """Counts, not verdicts.

    Every field here is something the stream states outright. There is no
    ranking, no "quality", and no derived signal that implies one run went
    better than another — see this module's docstring, and `CLAUDE.md` → facts
    vs judgment.
    """
    calls = [events.tool_label(str(e.data.get("name", ""))) for e in _of(run, events.TOOL_CALL)]
    approvals = _of(run, events.APPROVAL_RESULT)
    allowed = [e for e in approvals if e.data.get("allowed")]
    questions = _of(run, events.QUESTION)
    result = next((e for e in reversed(run.events) if e.kind == events.RESULT), None)
    usage = (result.data.get("usage") if result else None) or {}

    return {
        "run": run.name,
        "started": run.header.get("started"),
        "model": run.header.get("model"),
        "effort": run.header.get("effort"),
        "prompt": run.header.get("prompt"),
        "portia_sha": run.header.get("portia_sha"),
        # Rungs pulled and in what order — the sequence *is* the finding, so it
        # is kept whole rather than reduced to a set.
        "sequence": calls,
        "by_tool": _tally(calls),
        "tools": len(calls),
        "tool_errors": sum(1 for e in _of(run, events.TOOL_RESULT) if e.data.get("is_error")),
        # How often it asked, and about how much. A question event can carry
        # several questions, and "asked once" reads very differently if that
        # once was a form of four.
        "asked": len(questions),
        "questions": sum(len(e.data.get("questions") or []) for e in questions),
        "writes": len(approvals),
        "approved": len(allowed),
        "refused": len(approvals) - len(allowed),
        "subtype": result.data.get("subtype") if result else None,
        "cost_usd": result.data.get("cost_usd") if result else None,
        **_tokens(usage),
    }


def _of(run: Run, kind: str) -> list[events.Event]:
    return [e for e in run.events if e.kind == kind]


#: The three fields that together make up everything sent to the model.
INPUT_FIELDS = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")


def _tokens(usage: dict[str, Any]) -> dict[str, Any]:
    """What the turn actually sent and received.

    The SDK's ``input_tokens`` counts only the *uncached* part, and on a portia
    turn that is nearly nothing: the first real run through this module reported
    17 input tokens for a turn that sent 14,651 — the L0 system prompt and the
    L1 brief are pushed on every turn and are exactly what the cache holds. A
    run log quoting the raw field would have said a fat turn was a cheap one,
    which is the disagreement-about-a-number that `core/present.py` exists to
    stop, in the artifact that exists to measure cost.

    So `input_tokens` here is the whole of it, and `cached_tokens` says how much
    of that was read from cache rather than sent fresh. Both are facts; neither
    says whether a run was expensive, which is a judgment that needs a goal.
    """
    if not usage:
        return {"input_tokens": None, "cached_tokens": None, "output_tokens": None}
    return {
        "input_tokens": sum(int(usage.get(field) or 0) for field in INPUT_FIELDS),
        "cached_tokens": int(usage.get("cache_read_input_tokens") or 0),
        "output_tokens": usage.get("output_tokens"),
    }


def _tally(names: list[str]) -> dict[str, int]:
    """Counts per tool, most-called first — the `join`/`normalize`/`sql` mix.

    Ordering by count is a reading convenience over facts of the same kind, not
    a ranking of importance: `BACKLOG.md` wants to know which op the copilot
    reaches for before promoting one out of the SQL hatch, and that question is
    answered by the counts themselves.
    """
    counts: dict[str, int] = {}
    for name in names:
        counts[name] = counts.get(name, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))

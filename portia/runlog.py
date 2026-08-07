"""The copilot's log — a chat, or an indexing job, kept.

**The module keeps its old name on purpose** (`docs/CONVERSATION.md` §3). The
collision that rename fixes was in what a *human* reads — a left-pane list called
Turns, a `.portia/runs/` sitting beside the project-root `runs/`, and a
`cli/runs.py` whose own docstring admitted it read turns. "Run log" as an
internal module name is a generic engineering term and is in nobody's
vocabulary; churning it for symmetry would be rename for its own sake.


Every result in `docs/EVALUATION.md` was scored by hand off a terminal
transcript: some of it pasted twice, some lost to a `^C`, two runs conflated
while being written up. Run 7's write-up says outright that only its last 90
lines survived and that a finding degraded because of it. A prompt change is
evaluated today by reading two walls of text side by side and trusting memory,
which is not a measurement — and it is the thing that makes tuning the loop feel
impossible.

The seam already existed. `agent/events.py` normalizes every SDK message into
`Event(kind, data)` precisely so something other than a terminal can consume it;
persisting that stream is most of the work. **One JSONL each** under
`.portia/chats/` or `.portia/indexing/`, one object per line, opened by a header
recording the kind, model, effort, prompt, cwd and the portia sha that produced
it — in the project directory, so a log travels with the spec it produced.

**Written at the edge, never in the engine.** `cli/chat.run_turn` and
`ui/exchange.start` each tee their event stream through here. The engine must not
learn it is being observed, or `events.py` stops being a clean seam and becomes
a logging framework (docs/EVALUATION.md → "The run log").

**Logs are project-local, and that is the whole storage model.** There is no
central store, no index, and nothing written outside the project — a turn is
only interpretable beside the catalog it read and the spec it wrote, so a
global folder of transcripts referring to tables you would have to go find is
worse than no folder. The consequences are worth stating plainly rather than
discovering: **deleting a project deletes its history**, there is no retention,
rotation or delete path (logs accumulate; tool results are the bulk), and
nothing aggregates across projects. Reading another project's log needs no
copying — every reader here takes a path, and `cli.history --dir <proj>/.portia`
works from anywhere.

Nothing here judges anything. `summary` counts what happened — rungs pulled and
in what order, how often it asked, which ops it chose, what it cost — and every
one of those is a **cost and behaviour descriptor, not a correctness signal**.
"Asked three times" is neither good nor bad without knowing whether it should
have; only the answer keys make a number mean anything. A log that scored what it
logged would be `CLAUDE.md`'s facts-vs-judgment line broken in the one place it
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

#: What was being logged. **A chat and an indexing are different artifacts and
#: get different folders** (`docs/CONVERSATION.md` §3): a chat is a conversation
#: you had with the copilot, an indexing is a job the app ran on your behalf, and
#: mixing them in one list made the pane that exists to say what portia knows
#: about say two things at once.
CHAT = "chat"
INDEXING = "indexing"
KINDS = (CHAT, INDEXING)

#: Under the project's `.portia/`, beside `sources/`. Neither is the project-root
#: `runs/` that holds saved *spec-run* reports (`ui/engine.py`): those are
#: markdown, written for a human reading a diff, and running a recipe is a
#: different act from deciding what the recipe should say.
DIR_FOR_KIND = {CHAT: "chats", INDEXING: "indexing"}

#: Where logs landed before the split, when every one of them was called a "turn"
#: and `.portia/runs/` collided with the project-root `runs/` in exactly the way
#: `CONVERSATION.md` §3 describes. **Read, never written, and never migrated** —
#: portia does not rewrite files in someone's project to suit its own rename, and
#: the one real evaluation on PHQ data would otherwise vanish because a word
#: changed. A log found here has no kind; it is listed under chats, which is what
#: nearly all of them were.
LEGACY_DIR = "runs"

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
    cwd: str | Path = ".",
    kind: str = CHAT,
    when: datetime | None = None,
) -> Log:
    """Open a log for one **chat** and write its header.

    **The unit is the chat, not the exchange** (`docs/CONVERSATION.md` §5). What
    the header holds is therefore only what is true of the whole file: when it
    started, which kind it is, where it ran, and which build of portia produced
    it. The prompt, the model and the effort moved onto each exchange's `PROMPT`
    event, because a chat can span several models and a header field that
    changes mid-file is a lie.

    `portia_sha` is what makes two logs comparable at all — which build of the
    prompts and the engine this one was talking to.

    **The session id is not here**, though §4 first said it would be: the SDK
    hands it back with the *result*, so it does not exist when this line is
    written. It rides on every `RESULT` event instead, which is strictly better —
    a header could not have shown a session changing, and this can.
    """
    if kind not in DIR_FOR_KIND:
        raise ValueError(f"unknown log kind {kind!r} — expected one of {', '.join(KINDS)}")
    when = when or datetime.now()
    directory = Path(portia_dir) / DIR_FOR_KIND[kind]
    directory.mkdir(parents=True, exist_ok=True)
    log = Log(_free_path(directory, when))
    log.write(
        HEADER,
        {
            "started": when.isoformat(timespec="seconds"),
            "kind": kind,
            "cwd": str(Path(cwd).resolve()),
            "portia_sha": portia_sha(),
        },
    )
    return log


def _free_path(directory: Path, when: datetime) -> Path:
    """`<stamp>.jsonl`, suffixed if something in the same second already took it.

    `index` runs two jobs back to back, which is exactly how a one-second stamp
    collides — and appending one log's events onto another's is the conflation
    this module exists to end.
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
class Transcript:
    """One logged thing: its header, and the events in the order they happened.

    Named for what it holds rather than for what produced it, because what
    produced it is now two things — see `CONVERSATION.md` §3. It was `Run`, which
    was the collision that section is about: a class called `Run` that is not a
    spec run.
    """

    path: Path
    header: dict[str, Any] = field(default_factory=dict)
    events: list[events.Event] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.path.stem

    @property
    def kind(self) -> str:
        """``CHAT`` or ``INDEXING``. A legacy log has no kind and reads as a chat."""
        recorded = self.header.get("kind")
        return recorded if recorded in KINDS else CHAT


def logs_in(portia_dir: str | Path = DEFAULT_DIR, kind: str | None = None) -> list[Path]:
    """Every log in a project, newest first (the stamp sorts).

    ``kind`` filters to one folder; ``None`` is everything. **Legacy
    `.portia/runs/` is folded into the chats**, because that is what nearly all
    of it was and because a history that silently omits everything written before
    a rename is worse than one that is slightly generous about it.
    """
    base = Path(portia_dir)
    folders = [base / DIR_FOR_KIND[k] for k in (KINDS if kind is None else (kind,))]
    if kind in (None, CHAT):
        folders.append(base / LEGACY_DIR)
    found = [p for folder in folders if folder.is_dir() for p in folder.glob("*.jsonl")]
    return sorted(found, key=lambda p: p.stem, reverse=True)


def find(name: str, portia_dir: str | Path = DEFAULT_DIR) -> Path | None:
    """Resolve what a human typed: a path, a full stem, or a unique prefix."""
    direct = Path(name)
    if direct.is_file():
        return direct
    candidates = logs_in(portia_dir)
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


def read(path: str | Path) -> Transcript:
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
    return Transcript(path=path, header=header, events=collected)


# --- what it can answer without any labels ----------------------------------


def summary(run: Transcript) -> dict[str, Any]:
    """Counts, not verdicts — **across the whole chat**.

    Every field here is something the stream states outright. There is no
    ranking, no "quality", and no derived signal that implies one chat went
    better than another — see this module's docstring, and `CLAUDE.md` → facts
    vs judgment. `exchanges` is a count of messages sent, not a score: a long
    conversation is neither better nor worse than a short one without knowing
    what it was for.

    The totals are sums across exchanges rather than the last one's. A chat that
    spent four cents over six messages spent four cents, and reporting the last
    message's cost would quietly understate every multi-exchange chat.
    """
    prompts = _of(run, events.PROMPT)
    called = _of(run, events.TOOL_CALL)
    calls = [events.tool_label(str(e.data.get("name", ""))) for e in called]
    approvals = _of(run, events.APPROVAL_RESULT)
    allowed = [e for e in approvals if e.data.get("allowed")]
    questions = _of(run, events.QUESTION)
    results = _of(run, events.RESULT)
    last = results[-1] if results else None

    return {
        "name": run.name,
        "kind": run.kind,
        "started": run.header.get("started"),
        # One line per chat has room for one model, so this is the first one it
        # ran on; `models` is the honest whole answer when it changed mid-chat.
        "model": _first_model(run, prompts),
        "effort": _first_effort(run, prompts),
        "models": _models(run, prompts),
        "exchanges": len(prompts) or (1 if run.events else 0),
        # What the chat *opened* with. A later message is a follow-up and only
        # means something beside the one before it, so the first is the only one
        # that stands alone in a list.
        "prompt": _first_prompt(run, prompts),
        "session_id": next(
            (e.data.get("session_id") for e in reversed(results) if e.data.get("session_id")), None
        ),
        "portia_sha": run.header.get("portia_sha"),
        # Rungs pulled, in what order, and **what each one was about** — the
        # sequence *is* the finding, so it is kept whole rather than reduced to
        # a set, and since the graph arrived it has to carry the subject too:
        # `graph_lookup` is a router, and a log that says only that it was
        # called cannot say whether it routed anywhere.
        "sequence": [_call_label(e) for e in called],
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
        # How it *ended* — the last exchange's subtype. An interrupted message
        # in the middle of a chat that carried on is not how the chat ended.
        "subtype": last.data.get("subtype") if last else None,
        "cost_usd": _total_cost(results),
        **_total_tokens(results),
    }


def _of(run: Transcript, kind: str) -> list[events.Event]:
    return [e for e in run.events if e.kind == kind]


# --- reading a chat's facts, with the pre-rename shape still readable --------
#
# A log written before `CONVERSATION.md` §5 held the prompt, model and effort in
# its header and had no `PROMPT` events at all. Every reader below falls back to
# the header for exactly that case, which is what makes the promise in §3 — old
# logs are read, never migrated — true rather than aspirational.


def _first_prompt(run: Transcript, prompts: list[events.Event]) -> str | None:
    if prompts:
        return str(prompts[0].data.get("text") or "")
    return run.header.get("prompt")


def _first_model(run: Transcript, prompts: list[events.Event]) -> str | None:
    if prompts:
        return prompts[0].data.get("model")
    return run.header.get("model")


def _first_effort(run: Transcript, prompts: list[events.Event]) -> str | None:
    if prompts:
        return prompts[0].data.get("effort")
    return run.header.get("effort")


def _models(run: Transcript, prompts: list[events.Event]) -> list[str]:
    """Every model the chat ran on, in the order it first ran on each."""
    seen = [str(e.data.get("model")) for e in prompts if e.data.get("model")]
    if not seen:
        header = run.header.get("model")
        return [str(header)] if header else []
    return list(dict.fromkeys(seen))


def _total_cost(results: list[events.Event]) -> float | None:
    """The chat's whole spend. ``None`` only when nothing reported any."""
    costs = [e.data.get("cost_usd") for e in results]
    reported = [float(c) for c in costs if c is not None]
    return sum(reported) if reported else None


def _total_tokens(results: list[events.Event]) -> dict[str, Any]:
    """Tokens summed across every exchange, through one arithmetic (`token_totals`).

    **``None`` survives as ``None``.** `token_totals` says ``None`` for usage it
    was never given, and that is not the same claim as zero — one means nobody
    reported, the other means nothing was sent. Coercing the first into the
    second would put a made-up zero in the artifact that exists to measure cost.
    """
    names = ("input_tokens", "cached_tokens", "output_tokens")
    totals: dict[str, Any] = dict.fromkeys(names)
    for event in results:
        counted = token_totals(event.data.get("usage") or {})
        for name in names:
            value = counted.get(name)
            if value is not None:
                totals[name] = (totals[name] or 0) + int(value)
    return totals


#: How much of one argument, and of the whole subject, a sequence entry keeps.
#: Long enough to name a source or a column, short enough that thirty calls
#: still read as one line.
SUBJECT_PART_CHARS = 24
SUBJECT_CHARS = 48


def call_subject(tool_input: dict | None) -> str:
    """What one call was *about*, in a few characters — `graph_lookup(orders.city)`.

    The sequence used to record only which tools were called, which was enough
    while every tool answered "tell me more about one table you already named".
    It stopped being enough when the graph arrived: `graph_lookup` is a
    **router**, so the question it answers is *which* table — and a log saying
    only that it was called cannot tell you whether it routed anywhere. Same for
    `measure_overlaps`, where the interesting fact is how many pairs.

    Deliberately **derived from the argument shapes rather than a table of
    tools**: a per-tool map is one more thing to go stale silently, and the
    generic rule — the string arguments, or a count of the list one — happens to
    read correctly for every tool there is. Still counting, never scoring: this
    says what was asked, not whether asking was right.
    """
    items = [(k, v) for k, v in (tool_input or {}).items() if k != "portia_dir"]
    strings = [_clip(v, SUBJECT_PART_CHARS) for k, v in items if isinstance(v, str) and v]
    if strings:
        return _clip(".".join(strings), SUBJECT_CHARS)
    counted = [f"{len(v)} {k}" for k, v in items if isinstance(v, list)]
    return _clip(", ".join(counted), SUBJECT_CHARS)


def _call_label(event: events.Event) -> str:
    """One call as the sequence records it: the tool, and what it was about."""
    name = events.tool_label(str(event.data.get("name", "")))
    subject = call_subject(event.data.get("input"))
    return f"{name}({subject})" if subject else name


def _clip(text: str, chars: int) -> str:
    text = str(text)
    return text if len(text) <= chars else text[: chars - 1] + "…"


#: The three fields that together make up everything sent to the model.
INPUT_FIELDS = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")


def token_totals(usage: dict[str, Any]) -> dict[str, Any]:
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

    **Public because the window shows these live**, at the end of a turn, and a
    second implementation of "which of the SDK's three input fields count" is
    how the panel and `cli.history` end up quoting different numbers for one turn —
    the disagreement `core/present.py` exists to stop.
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

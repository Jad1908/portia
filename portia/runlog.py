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
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from portia.agent import events

#: Imported by name as well as through the module, because `Transcript` has a
#: field called `events` — inside that class body the module name is shadowed,
#: so `events.Event` in a method signature resolves to the field. The module
#: stays for the kind constants, which read better qualified.
from portia.agent.events import Event
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
#: the one real evaluation on AQN data would otherwise vanish because a word
#: changed. A log found here has no kind; it is listed under chats, which is what
#: nearly all of them were.
LEGACY_DIR = "runs"

#: The log's first line. Shaped like an event so a reader can parse every line
#: the same way, but deliberately *not* an `events` kind: it describes the turn
#: rather than being something that happened during it.
HEADER = "header"

#: What the copilot was reading while it did all this: the composed system
#: prompt, and every tool description as the model received it.
#:
#: **The second line, not part of the header, and that is a performance
#: decision.** `read_header` exists so a list of forty chats can be drawn from
#: forty single lines (see its docstring), and the app's left pane re-reads them
#: on every event. The prompts are ~30 KB — bigger than a whole chat log — so
#: folding them into line one would have made the cheapest read in this module
#: the most expensive one, in the surface that repeats it most.
#:
#: Kept verbatim rather than as a digest because comparing two runs is the whole
#: job: `portia_sha` is enough to *find* the prompts and not to read them, and
#: checking out an old sha mid-session to see what a sentence used to say is
#: exactly the friction that makes tuning feel impossible. The L1 brief is not
#: recoverable from a sha at all — it is built from `.portia/`, which moves as
#: the project is indexed.
PROMPTS = "prompts"

#: A chart portia published that the browser could not draw
#: (`VISUALIZATION.md` §11). Shaped like an event and, like :data:`HEADER`,
#: deliberately not an `events` kind: nothing in the engine produced it, and
#: `events.py` is the seam between the SDK and the surfaces rather than a place
#: the surfaces report back through.
#:
#: It is in the log because a reply written over a chart nobody saw is precisely
#: what reading a run is for, and until now it was the one thing that happened
#: during a chat that left no trace anywhere outside a `<div>`. Same rule as
#: everything else here — **it counts and it never scores**: a failed render is
#: a fact about the run, not a mark against the model, and a chart Vega-Lite
#: refused is as often portia's palette as it is the copilot's spec.
CHART_FAILED = "chart_failed"

#: A chat picked up by a later process (`docs/CHAT_SESSIONS.md` §3.9). Written
#: where the resume happened, before the exchange it opens, and like
#: :data:`HEADER` deliberately not an `events` kind: nothing in the engine
#: produced it. It carries the `portia_sha` of the process doing the resuming,
#: because the header's sha is the first process's and the prompts may have
#: changed since — which is also why a fresh :data:`PROMPTS` record follows it.
RESUMED = "resumed"

#: The header field naming whoever drove the conversation when it was not portia
#: (`cli/serve.py`). Absent on every log the app or `cli/chat` wrote, which is
#: what absent means: portia held the loop, so the prose, the tokens and the
#: session id are all in the file. A hosted log has **tool calls and nothing
#: else**, because those are the only moments of somebody else's conversation
#: that pass through portia, and it cannot be continued from here: the session
#: it belongs to is the host's. The field carries the host's name rather than a
#: flag so the surface can say where the chat goes on.
HOSTED = "host"

#: Where a renamed chat keeps its name: one small file beside the logs rather
#: than a field in each log. Line one of a log is written once and read as one
#: line (`read_header`), and rewriting a multi-megabyte JSONL to change a
#: string would be the wrong tool. Renaming the *file* is out for a different
#: reason: the file name is the date order. Keyed by ``<folder>/<name>`` so a
#: chat and an indexing job opened in the same second cannot share an entry.
TITLES_FILE = "titles.yaml"

#: Resolution of the per-record stamp. Milliseconds: a tool call takes seconds
#: and a stream of text arrives in fractions of one, so whole seconds would
#: round most of the stream to zero.
STAMP_FORMAT = "%Y-%m-%dT%H:%M:%S.%f"
STAMP_DIGITS = 3

#: Tool calls whose name says the copilot climbed the disclosure ladder are not
#: enumerated here. `sequence` reports the calls in the order they happened and
#: lets the reader see the climb; teaching this module which rung is which would
#: put the ladder in two places, and the second would go stale.


@dataclass
class Log:
    """An open run log. One line per event, appended as it happens."""

    path: Path

    def event(self, event: Event) -> None:
        self.write(event.kind, event.data)

    def write(self, kind: str, data: dict[str, Any]) -> None:
        """Append one record, whole, stamped with when it was written.

        Reopened per line rather than held open, because the failure this log
        exists to stop is *losing the tail*: a turn ends in a `^C` as often as
        not, and the unwind goes through an async generator, a NiceGUI task or a
        SDK subprocess depending on which edge is driving. At a few hundred
        events a turn the reopen costs nothing, and there is no handle left to
        close on any of those paths.

        **`at` sits beside `kind`, never inside `data`.** The data is the
        event's, normalized by `agent/events.py` from what the SDK said; when
        something was logged is this module's fact about it, and `events.Event`
        gaining a timestamp field would be the engine learning it is being
        observed — the thing this module's docstring exists to prevent.

        It is the moment portia *wrote* the record, which is not quite the moment
        the thing happened upstream. At the scale anyone reads these for — did
        that profile take two seconds or ninety — the difference is noise, and
        pretending to a precision the stream cannot supply would be worse.
        """
        line = to_json_line({"kind": kind, "at": stamp(), "data": data})
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")


def start(
    portia_dir: str | Path = DEFAULT_DIR,
    *,
    cwd: str | Path = ".",
    kind: str = CHAT,
    when: datetime | None = None,
    provider: str | None = None,
    host: str | None = None,
    prompts: dict[str, Any] | None = None,
    label: str | None = None,
) -> Log:
    """Open a log for one **chat** and write its header.

    ``label`` is what a job is about — the sources an indexing job reads — and
    it goes in the header because it is true of the whole file and it is what
    the list names the job by (`job_title`). A chat has none: it is named by
    what the human said.

    ``host`` and ``prompts`` are for a conversation portia does not drive
    (:data:`HOSTED`): who does, and what portia gave *that* model to read, which
    is not the system prompt `prompts_read` composes for its own.

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
    header = {
        "started": when.isoformat(timespec="seconds"),
        "kind": kind,
        "cwd": str(Path(cwd).resolve()),
        "portia_sha": portia_sha(),
    }
    if host:
        header[HOSTED] = host
    if label:
        header["label"] = label
    log.write(HEADER, header)
    # A job reads: its model is offered no build tool, and the record says so.
    read = prompts_read(portia_dir, provider, builds=kind == CHAT)
    log.write(PROMPTS, read if prompts is None else prompts)
    return log


def resume(
    path: str | Path, portia_dir: str | Path = DEFAULT_DIR, *, provider: str | None = None
) -> Log:
    """Reopen a chat's log for a later process to append to.

    **The file is the chat** (`CONVERSATION.md` §5), so a resumed chat goes on
    in the same file rather than in a new one that names the old. Two records
    go in first: a :data:`RESUMED` mark, and the prompts *this* process's model
    will read — line two captured what the first process gave it, and the
    prompts are the least stable thing in the repo.
    """
    log = Log(Path(path))
    if not log.path.is_file():
        raise FileNotFoundError(f"no log at {log.path}")
    log.write(RESUMED, {"portia_sha": portia_sha()})
    log.write(PROMPTS, prompts_read(portia_dir, provider))
    return log


def stamp(when: datetime | None = None) -> str:
    """Now, to the millisecond — the format every record's ``at`` uses."""
    text = (when or datetime.now()).strftime(STAMP_FORMAT)
    return text[: -(6 - STAMP_DIGITS)] if STAMP_DIGITS < 6 else text


def prompts_read(
    portia_dir: str | Path = DEFAULT_DIR, provider: str | None = None, *, builds: bool = True
) -> dict[str, Any]:
    """What the copilot is about to be given: system prompt and tool descriptions.

    ``builds`` is off for an indexing log: that job is not offered the build
    tools (`tools.BUILD_TOOLS`), and this record has to list what its model
    read and not what a chat's would have.

    **Best-effort, like the graph writes** (`CLAUDE.md` → `knowledge/`): this is
    a record *about* the run, so failing to take it must never stop the run. The
    import is local and guarded because `agent/session.py` reaches the SDK
    through `agent/tools.py`, and this module is imported by surfaces that have
    no `agent` extra installed — a log with no prompts block is exactly as
    readable as every log written before this existed.

    Composed at `start`, which is when it is true: `CONVERSATION.md` §4 holds one
    client for the whole chat, so the system prompt is fixed for the file even
    though the model and the brief behind it could otherwise move.

    ``provider`` decides which tools were offered (`tools.offered`): a model
    that cannot take a picture never read `view_chart`'s description, and a log
    saying it did would be this record being wrong about the one thing it is for.
    """
    try:
        from portia.agent import providers, tools
        from portia.agent.session import build_system_prompt

        source = providers.get(provider or providers.DEFAULT_KIND)
        return {
            "system": build_system_prompt(str(portia_dir)),
            "tools": tools.descriptions(
                sees_images=source.sees_images,
                asks=source.harness == providers.CODEX,
                builds=builds,
            ),
        }
    except Exception:
        # Deliberately every exception, not ImportError: a missing extra, an
        # unreadable catalog and an SDK that changed shape all cost the same
        # thing here — a block nothing needs to run — and none of them is worth
        # losing the transcript over.
        return {}


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
    events: list[Event] = field(default_factory=list)
    #: When each event was written, aligned with ``events``. A separate list
    #: rather than a field on `events.Event`, for the reason `Log.write` gives:
    #: the event is the engine's, the stamp is the log's. ``None`` for every
    #: event of a log written before stamps existed — read, never migrated.
    times: list[str | None] = field(default_factory=list)
    #: The system prompt and tool descriptions this run read, when it recorded
    #: them (`PROMPTS`). Empty for a log written before, or by a surface that
    #: could not compose them.
    prompts: dict[str, Any] = field(default_factory=dict)
    #: Where in ``events`` a later process picked the chat up (:data:`RESUMED`):
    #: the index of the first event written after each resume. Positions rather
    #: than events, because a resume is not something that happened in the
    #: conversation; it is something that happened to the file.
    resumes: list[int] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.path.stem

    @property
    def kind(self) -> str:
        """``CHAT`` or ``INDEXING``. A legacy log has no kind and reads as a chat."""
        recorded = self.header.get("kind")
        return recorded if recorded in KINDS else CHAT

    def timed(self) -> list[tuple[Event, str | None]]:
        """Each event with its stamp — the one way to walk the two together.

        Two parallel lists are easy to misalign by one and impossible to notice
        having done so, which in a transcript means quietly attributing a
        90-second profile to the sentence before it. Reading them through here
        means the padding rule lives in one place instead of in each renderer.
        """
        times = list(self.times) + [None] * (len(self.events) - len(self.times))
        return list(zip(self.events, times[: len(self.events)], strict=True))


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
    """Resolve what a human typed: a path, a full stem, or a unique prefix.

    **An exact stem wins over a prefix**, and that is not pedantry. Two logs
    opened in the same second are ``<stamp>.jsonl`` and ``<stamp>-2.jsonl``
    (`_free_path`), so the second one's name *starts with* the first one's — and
    `logs_in` sorts newest first. Naming the older log by its full stem used to
    return the newer one. It matters more now than it did: `findings.review`
    curates a named chat, so the wrong log means the wrong numbers.
    """
    direct = Path(name)
    if direct.is_file():
        return direct
    candidates = logs_in(portia_dir)
    exact = next((p for p in candidates if p.stem == name), None)
    return exact or next((p for p in candidates if p.stem.startswith(name)), None)


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


def read_listing(path: str | Path, portia_dir: str | Path | None = None) -> dict[str, Any]:
    """What a list row needs, read from the top of the file and no further.

    The header, then the first ``PROMPT`` event — what the chat opened with, the
    model and the effort it opened on — and the reading stops there. `summary`
    would give the counts too, at the price of parsing a whole log per row, and
    a list of forty chats is drawn at human pace but forty times. The counts
    belong to the chat's own footer, once it is open.

    ``title`` is the human's name for it when one was given (`set_title`),
    else the first prompt, else the host's name for a hosted log, else the
    file's stem. ``legacy`` is whether it was
    written before the rename (`LEGACY_DIR`) — read-only in any surface that
    can continue a chat, and the surface says why.
    """
    path = Path(path)
    header: dict[str, Any] = {}
    opened: dict[str, Any] = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if not isinstance(record, dict):
                continue
            kind, data = record.get("kind"), record.get("data") or {}
            if kind == HEADER:
                header = data
                # A hosted log has no prompt to find, and reading on for one
                # would parse the whole file for every row of the list.
                if header.get(HOSTED):
                    break
            elif kind == events.PROMPT:
                opened = data
                break
    kind = header.get("kind")
    given = titles(portia_dir)[title_key(path)] if portia_dir is not None else None
    prompt = str(opened.get("text") or header.get("prompt") or "")
    # **A job is named by what it read, never by the instruction it was sent**
    # *(2026-09-23, the user's report)*. The first line of an indexing job's
    # prompt is the app's own template, so every job in the list, and the
    # header of every one opened, read *These sources were just indexed:
    # 'A', 'B', …* — the one line of the file with nothing in it.
    named = job_title(header.get("label") or _names_in(prompt)) if kind == INDEXING else ""
    return {
        "name": path.stem,
        "kind": kind if kind in KINDS else CHAT,
        "started": header.get("started"),
        "model": opened.get("model") or header.get("model"),
        "effort": opened.get("effort") or header.get("effort"),
        # Absent on every log written before providers existed, and read as
        # the Anthropic default by whoever draws it, because it was.
        "provider": opened.get("provider"),
        "prompt": prompt,
        "title": given or named or first_line(prompt) or _hosted_title(header) or path.stem,
        "legacy": path.parent.name == LEGACY_DIR,
        # Who drove it, when that was not portia (:data:`HOSTED`). Read-only
        # here like a legacy log, and for a different reason the surface states.
        "host": header.get(HOSTED),
    }


#: What a hosted chat is called until somebody renames it. It has no first
#: prompt to be named after: the prompt went to the host and never came here.
HOST_LABELS = {"claude-code": "Claude Code"}


def host_label(host: str | None) -> str:
    """A host's name as a human writes it, or the raw value for one we never met."""
    return HOST_LABELS.get(host or "", host or "")


def _hosted_title(header: dict[str, Any]) -> str:
    host = header.get(HOSTED)
    return f"{host_label(host)} session" if host else ""


def first_line(text: str) -> str:
    """What a list calls a prompt: its first line, or nothing."""
    return text.strip().splitlines()[0].strip() if text.strip() else ""


#: What an indexing job is called: the sources it read, after the one word
#: that says what kind of thing it was. Nothing else names it.
JOB_TITLE = "Indexing {label}"
JOB_TITLE_BARE = "Indexing"


def job_title(label: str) -> str:
    """The name of an indexing job, off the batch it was about."""
    return JOB_TITLE.format(label=label) if label else JOB_TITLE_BARE


def _names_in(prompt: str) -> str:
    """The batch, read out of a job's prompt, for a log written before the header
    carried it: the template quotes each name, and this is the one place that
    shape is relied on, over files that are already written."""
    names = re.findall(r"'([^']+)'", first_line(prompt))
    return ", ".join(names)


def title_key(path: str | Path) -> str:
    path = Path(path)
    return f"{path.parent.name}/{path.name}"


def _titles_path(portia_dir: str | Path) -> Path:
    return Path(portia_dir) / DIR_FOR_KIND[CHAT] / TITLES_FILE


def titles(portia_dir: str | Path | None = DEFAULT_DIR) -> dict[str, str | None]:
    """Every name a human gave a log, by `title_key`. Missing is ``None``."""
    import yaml

    found: dict[str, str | None] = {}
    if portia_dir is None:
        return _Titles(found)
    path = _titles_path(portia_dir)
    if path.is_file():
        loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if isinstance(loaded, dict):
            found = {str(k): (str(v) if v is not None else None) for k, v in loaded.items()}
    return _Titles(found)


class _Titles(dict):
    """A dict that answers ``None`` for a log nobody renamed."""

    def __missing__(self, key: str) -> None:
        return None


def set_title(path: str | Path, title: str, portia_dir: str | Path = DEFAULT_DIR) -> None:
    """Name a log. An empty title takes the name away again."""
    import yaml

    current = dict(titles(portia_dir))
    key = title_key(path)
    if title.strip():
        current[key] = title.strip()
    else:
        current.pop(key, None)
    target = _titles_path(portia_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump(current, sort_keys=True, allow_unicode=True), encoding="utf-8")


def delete(path: str | Path, portia_dir: str | Path = DEFAULT_DIR) -> None:
    """Remove a log and the name it was given, if any.

    The one artifact here nothing regenerates, which is why every surface
    confirms first and why this does nothing clever: unlink, and drop the title.
    """
    path = Path(path)
    if path.is_file():
        path.unlink()
    if titles(portia_dir)[title_key(path)] is not None:
        set_title(path, "", portia_dir)


def read(path: str | Path) -> Transcript:
    """Parse a log back into a header and a list of events.

    Unparseable lines are skipped rather than raised on. The one that realistic-
    ally goes wrong is a truncated tail — the process died mid-write — and a
    reader that refuses the whole file over its last half-line would throw away
    the transcript in exactly the case this module was built for.
    """
    path = Path(path)
    header: dict[str, Any] = {}
    read_prompts: dict[str, Any] = {}
    collected: list[Event] = []
    times: list[str | None] = []
    resumes: list[int] = []
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
        elif kind == PROMPTS:
            read_prompts = data
        elif kind == RESUMED:
            resumes.append(len(collected))
        elif isinstance(kind, str):
            collected.append(Event(kind, data))
            at = record.get("at")
            times.append(at if isinstance(at, str) else None)
    return Transcript(
        path=path,
        header=header,
        events=collected,
        times=times,
        prompts=read_prompts,
        resumes=resumes,
    )


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
    automatic = [e for e in approvals if e.data.get("auto")]
    questions = _of(run, events.QUESTION)
    questions_answered = _of(run, events.ANSWER)
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
        "provider": prompts[0].data.get("provider") if prompts else None,
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
        # How many later processes picked this chat up. A count, like the rest.
        "resumes": len(run.resumes),
        # Rungs pulled, in what order, and **what each one was about** — the
        # sequence *is* the finding, so it is kept whole rather than reduced to
        # a set, and since the graph arrived it has to carry the subject too:
        # `graph_lookup` is a router, and a log that says only that it was
        # called cannot say whether it routed anywhere.
        "sequence": [_call_label(e) for e in called],
        "by_tool": _tally(calls),
        "tools": len(calls),
        # **Charts drawn, which is not the same as `plot_data` calls** and is why
        # this is not a second copy of `by_tool["plot_data"]`. Redrawing under a
        # tab name already on the strip *replaces* that chart
        # (`docs/VISUALIZATION.md` §3.3), so ten calls can be six pictures, and
        # the number a person means by "how many charts" is the second one.
        #
        # It is here because a chat that built nothing is the shape phase 5 of
        # `VISION.md`'s loop is *supposed* to have: no spec, no writes, and a
        # line reading `writes 0, approved 0` says nothing about ten charts that
        # taught somebody what their data looks like. A count, never a score —
        # ten charts is neither good nor bad without knowing what they were for.
        "charts": len(_charts(called)),
        "tool_errors": sum(1 for e in _of(run, events.TOOL_RESULT) if e.data.get("is_error")),
        # How often it asked, and about how much. A question event can carry
        # several questions, and "asked once" reads very differently if that
        # once was a form of four.
        "asked": len(questions),
        "questions": sum(len(e.data.get("questions") or []) for e in questions),
        "writes": len(approvals),
        "approved": len(allowed),
        "refused": len(approvals) - len(allowed),
        # Of those, how many never stopped for anyone. **`approved` counts the
        # writes that happened, not the decisions somebody made**, so without
        # this a session that auto-allowed 26 catalog writes would report 26
        # approvals nobody gave — and "writes refused" is one of the few things
        # this log can measure without an answer key (`EVALUATION.md`). A count,
        # not a judgment: whether gating a tool is worth the interruption is
        # exactly the call `ui/settings.py` hands to the human.
        "auto_approved": len(automatic),
        # How it *ended* — the last exchange's subtype. An interrupted message
        # in the middle of a chat that carried on is not how the chat ended.
        "subtype": last.data.get("subtype") if last else None,
        "cost_usd": _total_cost(results),
        # How long it took, wall-clock, first record to last. A **cost and
        # behaviour descriptor**, in exactly the sense the module docstring
        # means: it belongs beside tokens and dollars, not beside a verdict.
        # This is the field in here most likely to be read as a score — a slow
        # run *feels* like a worse one — and it is not one. A chat that spent
        # four minutes because it profiled a 40M-row table and one that spent
        # four minutes waiting for a human to answer a question are the same
        # number, and neither says whether the time bought anything. ``None``
        # when nothing was stamped, which is every log written before stamps
        # existed; a made-up zero would read as instant.
        "wall_seconds": _wall_seconds(run),
        # How much of that wall clock was portia parked on a human, summed over
        # every question and every write confirmation. It is the split
        # `wall_seconds` above says it cannot make — *four minutes profiling a
        # 40M-row table and four minutes waiting for a human are the same
        # number* — and it is a **spend descriptor for exactly the same reason**,
        # not a verdict on either party. Nobody is slow for reading a
        # `record_step` payload; that reading is what the product is for.
        # ``None`` when nothing measured it, which is every log written before
        # `agent/ask.py` started timing its own awaits.
        "waited_seconds": _waited_seconds(run, approvals, questions_answered),
        **_total_tokens(results),
    }


def _charts(called: list[Event]) -> set[str]:
    """The distinct tabs a chat drew, by name — the chart's identity everywhere.

    The tool name comes from `findings.PLOT_TOOL` rather than a literal, because
    that module already had to name both doors to read them back and two spellings
    of `plot_data` is one rename away from a count that silently reads zero.
    """
    from portia.findings import PLOT_TOOL

    return {
        str((e.data.get("input") or {}).get("tab") or "")
        for e in called
        if events.tool_label(str(e.data.get("name", ""))) == PLOT_TOOL
    } - {""}


def elapsed(start: str | None, end: str | None) -> float | None:
    """Seconds between two record stamps. ``None`` if either is absent or unreadable.

    Public because both the audit page and `cli.history` ask it, and two
    implementations of "how long did that take" is the disagreement-about-a-
    number `core/present.py` exists to stop.
    """
    if not start or not end:
        return None
    try:
        return (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds()
    except ValueError:
        return None


def call_durations(run: Transcript) -> dict[str, float]:
    """Seconds each tool call took, by the SDK's call id, off the record stamps.

    The transcript pane draws a replayed call with the time it took beside the
    volume that came back, the way it draws a live one, and the audit page
    says the same number: both read it through here, so the window and the
    page cannot disagree about how long a profile ran. A call with no result
    under it, or on a log written before stamps existed, is absent rather
    than zero.
    """
    started: dict[str, str] = {}
    took: dict[str, float] = {}
    for event, at in run.timed():
        call_id = str(event.data.get("id") or "")
        if not call_id or not at:
            continue
        if event.kind == events.TOOL_CALL:
            started[call_id] = at
        elif event.kind == events.TOOL_RESULT and call_id in started:
            seconds = elapsed(started[call_id], at)
            if seconds is not None:
                took[call_id] = seconds
    return took


def _wall_seconds(run: Transcript) -> float | None:
    stamped = [t for t in run.times if t]
    return elapsed(stamped[0], stamped[-1]) if len(stamped) > 1 else None


def _waited_seconds(run: Transcript, *groups: list[Event]) -> float | None:
    """Seconds parked on a human, over every event that carries a ``waited``.

    Read off the events rather than off the stamps, because the stamps cannot
    say — `agent/ask.py` explains why. A log where **no** event carries one is
    ``None``, not zero: it was written before this was measured, and a zero
    there would claim the human answered instantly 39 times.
    """
    waits = [
        float(e.data["waited"])
        for group in groups
        for e in group
        if e.data.get("waited") is not None
    ]
    return round(sum(waits), 1) if waits else None


def _of(run: Transcript, kind: str) -> list[Event]:
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

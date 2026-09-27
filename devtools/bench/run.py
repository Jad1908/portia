"""The runner: a case through portia with nobody at the keyboard (`docs/BENCHMARK_EVAL.md` §7 of the roadmap, §8).

A run is a fresh project folder, the case's data written into it, the brief
set, the facts indexed, and then each thread of the case as its own chat: a
new session with nothing pasted in, the scripted user answering what the
copilot asks and approving what it writes, the next message sent when a
reply ends, and every event teed into the project's own log with the seed in
its header. What it leaves behind is what the graders read: the logs, the
catalog, the specs, and one ``result.json`` saying what ran, under which pins,
and how every question was routed.

The runner is an **edge**, in `runlog`'s sense: it tees the stream and the
engine never learns it is being driven. It drives the Claude harness only;
the Codex harness has no turn or budget cap, and a run nobody watches is
refused without both.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from devtools.bench import case as cases
from devtools.bench.user import ScriptedUser
from portia import catalog, fixtures, runlog
from portia.agent import prompts, providers, session
from portia.core.io import find_data_files
from portia.core.serialize import to_json
from portia.spec import REPORT_STAMP

#: Which side of the comparison a run is (§5). A kind, never a rank. The
#: baseline comes in two versions (§5.4): as shipped, and diligent, where the
#: scripted user asks it to write down what it learned at the end of each
#: thread. portia gets no such instruction, because its memory is automatic.
ARM_PORTIA = "portia"
ARM_BASELINE = "baseline"
ARM_BASELINE_DILIGENT = "baseline-diligent"
ARMS = (ARM_PORTIA, ARM_BASELINE, ARM_BASELINE_DILIGENT)

#: Where runs land when nowhere is given: gitignored, beside the viewer's notes.
DEFAULT_OUT = Path(__file__).resolve().parents[1] / "out" / "bench"

#: The folder a run's data is written into, and recorded as the project's data
#: folder, so the window and the copilot see the case's files and nothing else.
DATA_DIR = "data"
PROJECT_DIR = "project"
RESULT_FILE = "result.json"

#: What a run's folder is called under the case, the variant and the arm.
NO_VARIANT = "base"

#: The name of the indexing thread, when the case asks the copilot to index.
INDEXING_THREAD = "indexing"

#: Where the baseline reads the brief: the file Claude Code reads on its own
#: in a project, and the one the diligent version is asked to write to.
BASELINE_BRIEF = "CLAUDE.md"


@dataclass
class ThreadRun:
    """One thread of a run: the log it wrote and how its questions were answered."""

    name: str
    kind: str
    log: Path
    session_id: str | None
    summary: dict[str, Any]
    routing: list[dict[str, Any]] = field(default_factory=list)
    fallbacks: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "log": str(self.log),
            "session_id": self.session_id,
            "summary": self.summary,
            "routing": self.routing,
            "fallbacks": self.fallbacks,
        }


@dataclass
class Run:
    """What one run of one case was, and what it left."""

    case: dict[str, Any]
    variant: str | None
    arm: str
    model: str
    effort: str | None
    provider: str
    seed: int | None
    portia_sha: str | None
    project: Path
    started: str
    threads: list[ThreadRun] = field(default_factory=list)
    finished: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "case": self.case,
            "variant": self.variant,
            "arm": self.arm,
            "model": self.model,
            "effort": self.effort,
            "provider": self.provider,
            "seed": self.seed,
            "portia_sha": self.portia_sha,
            "project": str(self.project),
            "started": self.started,
            "finished": self.finished,
            "threads": [t.as_dict() for t in self.threads],
            "fallbacks": sum(t.fallbacks for t in self.threads),
        }


def run_dir(out: Path, case: cases.Case, variant: str | None, arm: str, when: datetime) -> Path:
    """``<out>/<case>/<variant>/<arm>/<stamp>``, a new folder per run."""
    return out / case.name / (variant or NO_VARIANT) / arm / when.strftime(REPORT_STAMP)


def prepare_project(case: cases.Case, root: Path, *, arm: str = ARM_PORTIA) -> Path:
    """A fresh project under ``root``: the data written, and the brief where the arm reads it.

    Deterministic and free: the fixture builders are, `catalog.index_source`
    is, and nothing here runs a model. For portia the brief is the project
    context and the facts are indexed into the catalog; what the copilot adds
    on top (summaries, roles, groups) is a thread's work, so a case that wants
    it says ``index: copilot`` and gets an indexing job before its first
    thread. For the baseline the brief is the project's `CLAUDE.md`, which is
    where a Claude Code user writes what a project is for, and there is no
    catalog at all: that is the thing being compared (§5.4).
    """
    project = root / PROJECT_DIR
    data_dir = project / DATA_DIR
    data_dir.mkdir(parents=True, exist_ok=True)
    family = case.fixture_family
    if family:
        for name in family:
            builder = getattr(fixtures, name)
            builder().to_csv(data_dir / f"{name}.csv", index=False)
    else:
        shutil.copytree(case.data, data_dir, dirs_exist_ok=True)
    if arm != ARM_PORTIA:
        (project / BASELINE_BRIEF).write_text(case.brief.strip() + "\n", encoding="utf-8")
        return project
    portia_dir = project / catalog.DEFAULT_DIR
    catalog.init_project(case.brief, portia_dir=portia_dir)
    catalog.set_data_dir(DATA_DIR, portia_dir=portia_dir)
    for path in find_data_files(data_dir):
        catalog.index_source(path, portia_dir=portia_dir)
    return project


def refuse_unless_claude(provider: str) -> None:
    """The runner drives the Claude harness only, and says so before writing anything."""
    if providers.get(provider).harness != providers.CLAUDE:
        raise ValueError(
            f"{provider!r} runs on the Codex harness, which has no turn or budget cap; "
            "the runner drives the Claude harness only"
        )


async def run_case(
    case: cases.Case,
    *,
    variant: str | None = None,
    model: str = session.DEFAULT_MODEL,
    effort: str | None = None,
    provider: str = session.DEFAULT_PROVIDER,
    out: Path = DEFAULT_OUT,
    client_factory: Callable[[Any], Any] | None = None,
    when: datetime | None = None,
) -> Run:
    """One run of ``case`` under ``variant`` through portia. Returns what it left.

    ``client_factory`` is the seam `session.Conversation` already has for a
    test to supply a client that replays scripted messages; a real run leaves
    it unset and the SDK's client drives the binary.
    """
    refuse_unless_claude(provider)
    scripted = case.with_variant(variant)
    when = when or datetime.now()
    folder = run_dir(out, scripted, variant, ARM_PORTIA, when)
    project = prepare_project(scripted, folder)
    user = ScriptedUser(scripted.facts, scripted.fallback)
    run = Run(
        case=cases.as_dict(scripted),
        variant=variant,
        arm=ARM_PORTIA,
        model=model,
        effort=effort,
        provider=provider,
        seed=scripted.seed,
        portia_sha=runlog.portia_sha(),
        project=project,
        started=when.isoformat(timespec="seconds"),
    )
    write_result(run, folder)

    threads: list[tuple[str, str, tuple[str, ...]]] = []
    if scripted.index == cases.INDEX_COPILOT:
        names = ", ".join(repr(p.stem) for p in find_data_files(project / DATA_DIR))
        threads.append(
            (INDEXING_THREAD, runlog.INDEXING, (prompts.task("index_batch", names=names),))
        )
    threads.extend((t.name, runlog.CHAT, t.messages) for t in scripted.threads)

    for name, kind, messages in threads:
        before = len(user.routing)
        thread = await _thread(
            scripted,
            project,
            user,
            name=name,
            kind=kind,
            messages=messages,
            model=model,
            effort=effort,
            provider=provider,
            client_factory=client_factory,
        )
        thread.routing = [r.as_dict() for r in user.routing[before:]]
        thread.fallbacks = sum(1 for r in user.routing[before:] if r.fact is None)
        run.threads.append(thread)
        # Written after every thread, so a run that dies mid-way still says
        # what it got to: the `^C` case, the same argument as the log's.
        write_result(run, folder)

    run.finished = datetime.now().isoformat(timespec="seconds")
    write_result(run, folder)
    return run


async def _thread(
    case: cases.Case,
    project: Path,
    user: ScriptedUser,
    *,
    name: str,
    kind: str,
    messages: tuple[str, ...],
    model: str,
    effort: str | None,
    provider: str,
    client_factory: Callable[[Any], Any] | None,
) -> ThreadRun:
    """One thread: a fresh chat, its messages in order, every event teed into the log."""
    portia_dir = project / catalog.DEFAULT_DIR
    log = runlog.start(portia_dir, cwd=project, kind=kind, provider=provider, seed=case.seed)
    chat = session.Conversation(
        answer=user.answer,
        confirm=user.confirm,
        auto_allow=user.auto_allow,
        model=model,
        effort=effort,
        cwd=project,
        portia_dir=str(portia_dir),
        provider=provider,
        # A job that reads is offered no build tool, as the app's is.
        builds=kind == runlog.CHAT,
        max_turns=case.max_turns,
        max_budget_usd=case.max_budget_usd,
        client_factory=client_factory,
    )
    await drive(chat, log, messages)
    transcript = runlog.read(log.path)
    return ThreadRun(
        name=name,
        kind=kind,
        log=log.path,
        session_id=chat.session_id,
        summary=runlog.summary(transcript),
    )


async def drive(chat: session.Conversation, log: runlog.Log, messages: tuple[str, ...]) -> None:
    """One chat, its messages in order, each sent when the previous reply ends, every event teed."""
    async with chat:
        for message in messages:
            async for event in chat.send(message):
                log.event(event)


def write_result(run: Run, folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / RESULT_FILE
    path.write_text(to_json(run.as_dict()) + "\n", encoding="utf-8")
    return path

"""The baseline: the same case through plain Claude Code (`docs/BENCHMARK_EVAL.md` §5.4, §5.5).

The Agent SDK can run Claude Code with its own preset system prompt and its
built-in tools. The same permission callback portia uses routes its
``AskUserQuestion`` to the same scripted user and lets its writes through,
logged ``auto``, so the only differences between the two arms are portia's
tools, prompts and memory, which is exactly what should get the credit.

**What the baseline gets is written into the case**, because fairness lives
in those details: the built-in tools by name, which settings the binary reads
(the project's `CLAUDE.md`, never the user's own), and the sentence the
diligent version hears at the end of each thread. It gets the brief as the
project's `CLAUDE.md`, where a Claude Code user writes what a project is for,
and no catalog at all. Its memory stays on: portia switches the binary's
auto-memory off for its own copilot (`session.BINARY_ENV`) and the baseline
keeps it, and that asymmetry is the point of the comparison, not a flaw in it.

The loop, the drain order and the log are portia's `session.Conversation`
through its `options_builder` seam; only the options are this module's. The
log lands beside the project rather than inside it, so the baseline cannot
read its own transcript with `Glob`.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any, Final

from devtools.bench import case as cases
from devtools.bench import run as runner
from devtools.bench.user import ScriptedUser
from portia import runlog
from portia.agent import ask, providers, session
from portia.agent.providers import anthropic as _anthropic

#: The SDK's name for Claude Code's own system prompt.
PRESET: Final = "claude_code"

#: Built-in tools that only read, run without stopping at the permission
#: callback, as portia's read tools do; every other tool the case grants goes
#: through it and is logged ``auto``. The split is portia's read/write line
#: applied to the other arm, so `runlog.summary`'s counts mean the same thing
#: on both.
READ_TOOLS = frozenset({"Read", "Glob", "Grep"})

#: Where a baseline run's logs go: beside the project, not in it.
LOGS_DIR = "logs"


def build_options(
    case: cases.Case,
    *,
    project: Path,
    model: str,
    effort: str | None,
    provider: str,
    can_use_tool: Callable[..., Any],
    curator: Any = None,
) -> Any:
    """``ClaudeAgentOptions`` for plain Claude Code on this case's project.

    ``curator`` is what `session.Conversation` hands every builder and this
    one ignores by name: the review hold is portia's rule, not the baseline's.
    """
    from claude_agent_sdk import ClaudeAgentOptions
    from claude_agent_sdk.types import SystemPromptPreset

    source = providers.get(provider)
    if effort is not None and effort not in session.EFFORTS:
        raise ValueError(
            f"unknown effort {effort!r} — expected one of {', '.join(session.EFFORTS)}"
        )
    if effort is not None and not source.honours_effort:
        raise ValueError(f"{source.label} does not honour effort; leave it unset")
    granted = list(case.baseline.tools)
    return ClaudeAgentOptions(
        model=model,
        cli_path=_anthropic.binary(),
        # The provider's own variables and nothing of portia's: `BINARY_ENV`
        # would switch the binary's memory off, and the baseline keeps its
        # memory on (§5.4).
        env=dict(source.env()),
        effort=effort,  # type: ignore[arg-type]
        system_prompt=SystemPromptPreset(type="preset", preset=PRESET),
        tools=[*granted, ask.ASK_TOOL],
        allowed_tools=[t for t in granted if t in READ_TOOLS],
        can_use_tool=can_use_tool,
        setting_sources=list(case.baseline.setting_sources),  # type: ignore[arg-type]
        # No servers at all, and none from the account either (`session.build_options`).
        mcp_servers={},
        strict_mcp_config=True,
        cwd=str(project),
        max_turns=case.max_turns,
        max_budget_usd=case.max_budget_usd,
    )


async def run_baseline(
    case: cases.Case,
    *,
    variant: str | None = None,
    diligent: bool = False,
    model: str = session.DEFAULT_MODEL,
    effort: str | None = None,
    provider: str = session.DEFAULT_PROVIDER,
    out: Path = runner.DEFAULT_OUT,
    client_factory: Callable[[Any], Any] | None = None,
    when: datetime | None = None,
) -> runner.Run:
    """One run of ``case`` under ``variant`` through plain Claude Code. Returns what it left.

    ``diligent`` sends the case's diligent message at the end of every thread,
    in the same chat, and is refused when the case has none: a version that
    silently ran as shipped would be reported under the wrong name.
    """
    runner.refuse_unless_claude(provider)
    if not case.baseline.tools:
        raise ValueError(f"case {case.name!r} grants the baseline no tools; set `baseline.tools`")
    if diligent and not case.baseline.diligent.strip():
        raise ValueError(f"case {case.name!r} has no `baseline.diligent` message")
    scripted = case.with_variant(variant)
    arm = runner.ARM_BASELINE_DILIGENT if diligent else runner.ARM_BASELINE
    when = when or datetime.now()
    folder = runner.run_dir(out, scripted, variant, arm, when)
    project = runner.prepare_project(scripted, folder, arm=arm)
    logs = folder / LOGS_DIR
    user = ScriptedUser(scripted.facts, scripted.fallback)
    run = runner.Run(
        case=cases.as_dict(scripted),
        variant=variant,
        arm=arm,
        model=model,
        effort=effort,
        provider=provider,
        seed=scripted.seed,
        portia_sha=runlog.portia_sha(),
        project=project,
        started=when.isoformat(timespec="seconds"),
    )
    runner.write_result(run, folder)

    for thread in scripted.threads:
        messages = thread.messages + ((scripted.baseline.diligent,) if diligent else ())
        before = len(user.routing)
        log = runlog.start(
            logs,
            cwd=project,
            provider=provider,
            prompts=prompts_record(scripted),
            seed=scripted.seed,
        )

        def builder(*, can_use_tool: Callable[..., Any], curator: Any) -> Any:
            return build_options(
                scripted,
                project=project,
                model=model,
                effort=effort,
                provider=provider,
                can_use_tool=can_use_tool,
                curator=curator,
            )

        chat = session.Conversation(
            answer=user.answer,
            confirm=user.confirm,
            auto_allow=user.auto_allow,
            model=model,
            effort=effort,
            provider=provider,
            client_factory=client_factory,
            options_builder=builder,
        )
        await runner.drive(chat, log, messages)
        result = runner.ThreadRun(
            name=thread.name,
            kind=runlog.CHAT,
            log=log.path,
            session_id=chat.session_id,
            summary=runlog.summary(runlog.read(log.path)),
            routing=[r.as_dict() for r in user.routing[before:]],
            fallbacks=sum(1 for r in user.routing[before:] if r.fact is None),
        )
        run.threads.append(result)
        runner.write_result(run, folder)

    run.finished = datetime.now().isoformat(timespec="seconds")
    runner.write_result(run, folder)
    return run


def prompts_record(case: cases.Case) -> dict[str, Any]:
    """Line two of a baseline log: what the model read, as far as portia can say.

    The preset prompt is the binary's and portia never sees its text, so the
    record names it and lists the tools granted, which is what a reader
    comparing two logs needs and all this side can honestly claim.
    """
    return {
        "system": {"preset": PRESET, "setting_sources": list(case.baseline.setting_sources)},
        "tools": {name: "" for name in (*case.baseline.tools, ask.ASK_TOOL)},
    }

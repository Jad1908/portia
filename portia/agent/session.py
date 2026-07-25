"""The loop: options, client lifecycle, event stream.

This is where the project's non-negotiables stop being prose and become
configuration. Two lines in ``build_options`` do most of that work — see the
comments there.

Nothing in this module touches authentication. Credential resolution happens
inside the SDK's bundled binary, using whatever is in the user's environment;
portia sets no auth variables, proxies nothing, and detects nothing. See
`PLAN.md` → "Auth posture".
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

from portia import catalog
from portia.agent import ask, context, events, tools

#: The model is a config knob, never a hard dependency (docs/PLAN.md). We develop
#: on a small one on purpose: if the loop works here, the *engine* is good.
DEFAULT_MODEL = "claude-haiku-4-5"

PROMPT_PATH = Path(__file__).parent / "prompts" / "copilot.md"


def build_system_prompt(portia_dir: str = catalog.DEFAULT_DIR) -> str:
    """L0 (how to work) + L1 (this project), composed into one system prompt.

    The project brief goes here rather than into the user's turn on purpose: it
    is operator-provided context, so it should carry operator authority, and
    putting it in the prompt makes its presence structural. A tool the agent
    *may* call is a tool it will sometimes skip — and the one it skipped in
    testing was the project context, which left its judgment generic.
    """
    return f"{PROMPT_PATH.read_text()}\n\n---\n\n{context.build_brief(portia_dir)}"


def build_options(
    *,
    model: str = DEFAULT_MODEL,
    cwd: str | Path | None = None,
    portia_dir: str = catalog.DEFAULT_DIR,
    can_use_tool: Callable[..., Any] | None = None,
) -> Any:
    """Assemble ``ClaudeAgentOptions`` for a portia session."""
    from claude_agent_sdk import ClaudeAgentOptions

    return ClaudeAgentOptions(
        model=model,
        system_prompt=build_system_prompt(portia_dir),
        # The agent gets NO built-in filesystem or shell tools. It therefore
        # *cannot* open a CSV — its only view of the data is the compact evidence
        # dicts the checks layer returns. That's "the model never eyeballs the
        # data" and "token-lean at scale" enforced by config rather than by
        # asking the prompt nicely. AskUserQuestion must be listed explicitly
        # once this array is set, or the copilot loses its ability to ask.
        tools=[ask.ASK_TOOL],
        mcp_servers={tools.SERVER_NAME: tools.build_server()},
        # Read-only checks run freely; writes fall through to `can_use_tool`
        # so a durable artifact is never changed silently.
        allowed_tools=[tools.qualified(t.name) for t in tools.READ_TOOLS],
        can_use_tool=can_use_tool,
        # Do not inherit this repo's CLAUDE.md or .claude/ — portia's copilot is
        # not Claude Code and must not pick up our development instructions.
        setting_sources=[],
        cwd=str(cwd) if cwd else None,
    )


def _silence_shadowed_tool_warning() -> None:
    """Suppress the SDK's ``CanUseToolShadowedWarning`` — expected, see ``run``."""
    import warnings

    try:
        from claude_agent_sdk import CanUseToolShadowedWarning
    except ImportError:  # older SDKs don't emit it; nothing to silence
        return
    warnings.filterwarnings("ignore", category=CanUseToolShadowedWarning)


async def run(
    prompt: str,
    *,
    answer: ask.AnswerFn,
    confirm: ask.ConfirmFn,
    model: str = DEFAULT_MODEL,
    cwd: str | Path | None = None,
    portia_dir: str = catalog.DEFAULT_DIR,
) -> AsyncIterator[events.Event]:
    """Run one copilot turn, yielding portia events as they happen.

    Uses ``ClaudeSDKClient`` rather than ``query()``: it keeps the session open
    for follow-ups and interrupts, and connecting with no prompt keeps the input
    stream open on its own — which is what makes ``can_use_tool`` usable in
    Python without the dummy-hook workaround.
    """
    from claude_agent_sdk import ClaudeSDKClient

    # The SDK warns that auto-approved tools skip `can_use_tool`. That is the
    # design here, not an accident: read-only checks run freely and only writes
    # stop for confirmation. Silence the notice rather than let it fire per run.
    _silence_shadowed_tool_warning()

    pending: list[events.Event] = []
    options = build_options(
        model=model,
        cwd=cwd,
        portia_dir=portia_dir,
        can_use_tool=ask.build_can_use_tool(answer=answer, confirm=confirm, emit=pending.append),
    )

    async with ClaudeSDKClient(options=options) as client:
        await client.query(prompt)
        async for message in client.receive_response():
            # Questions and approvals are emitted from inside the callback while
            # the stream is paused; drain them first so ordering stays true.
            while pending:
                yield pending.pop(0)
            for event in events.from_message(message):
                yield event
        while pending:
            yield pending.pop(0)

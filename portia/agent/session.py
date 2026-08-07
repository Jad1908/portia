"""The loop: options, client lifecycle, event stream.

`Conversation` is the unit — one **chat**, holding one SDK client across many
exchanges (`docs/CONVERSATION.md`). `run` is the one-message wrapper over it that
every non-conversational caller uses.

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
from typing import Any, cast

from portia import catalog
from portia.agent import ask, context, events, tools

#: The model is a config knob, never a hard dependency (docs/PLAN.md). We develop
#: on a small one on purpose: if the loop works here, the *engine* is good.
DEFAULT_MODEL = "claude-haiku-4-5"

#: Models worth offering in a picker — a **convenience list, not a validation
#: set**. The model stays a free-form config knob (``--model`` takes anything the
#: SDK accepts); this exists so a surface with a selector has something to put in
#: it without inventing its own list. Ordered cheapest-first, which is also the
#: order `PLAN.md` says to develop in.
MODELS = ("claude-haiku-4-5", "claude-sonnet-5", "claude-opus-5")

#: How hard the model thinks, passed straight to the SDK. The other half of
#: "develop on a cheaper, smaller model **at low effort**" (`PLAN.md` → Budget &
#: model discipline) — and the knob that makes a ceiling check on a flagship a
#: one-flag experiment rather than a code change. ``None`` leaves the SDK's default.
EFFORTS = ("low", "medium", "high", "xhigh", "max")

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
    effort: str | None = None,
    cwd: str | Path | None = None,
    portia_dir: str = catalog.DEFAULT_DIR,
    can_use_tool: Callable[..., Any] | None = None,
) -> Any:
    """Assemble ``ClaudeAgentOptions`` for a portia session."""
    from claude_agent_sdk import ClaudeAgentOptions

    if effort is not None and effort not in EFFORTS:
        raise ValueError(f"unknown effort {effort!r} — expected one of {', '.join(EFFORTS)}")

    return ClaudeAgentOptions(
        model=model,
        # Checked against EFFORTS just above; the SDK types it as a Literal and
        # the value arrives from argparse as a plain str.
        effort=cast(Any, effort),
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


def _sdk_client(options: Any) -> Any:
    """The real client. Behind a function so a test can supply its own.

    Same seam, and the same argument, as `ask.py` injecting ``answer`` and
    ``confirm``: the drain order, the no-queue guard and the session bookkeeping
    below are all worth testing, and none of them should cost a model call.
    """
    from claude_agent_sdk import ClaudeSDKClient

    return ClaudeSDKClient(options=options)


class Conversation:
    """One **chat**: an SDK client held open across exchanges.

    `docs/CONVERSATION.md` §2 — the client's lifetime used to be one prompt's,
    and moving that boundary is the whole of the change. ``ClaudeSDKClient`` is
    already the right object: connecting with no prompt keeps the input stream
    open on its own (which is what makes ``can_use_tool`` usable in Python
    without the dummy-hook workaround), and `query()` is just a transport write,
    so calling it again is the SDK's own multi-turn shape rather than a trick.

    **A chat dies with the process** (§4). This holds a live subprocess, so
    whoever opens one owns closing it; the durable artifacts are written as the
    chat goes and are what survives.

    **`send` refuses to overlap rather than queueing** (§7). A queued message
    would have to arrive either before or after whatever the agent does next,
    and neither answer is defensible when what it does next might be to ask you
    a question. The surface holds the draft; the engine holds the line.
    """

    def __init__(
        self,
        *,
        answer: ask.AnswerFn,
        confirm: ask.ConfirmFn,
        model: str = DEFAULT_MODEL,
        effort: str | None = None,
        cwd: str | Path | None = None,
        portia_dir: str = catalog.DEFAULT_DIR,
        client_factory: Callable[[Any], Any] | None = None,
    ) -> None:
        #: Questions and approvals are emitted from inside `can_use_tool` while
        #: the message stream is paused waiting on it, so they land here and
        #: `send` drains them from the outer loop. Ordering depends on it.
        self._pending: list[events.Event] = []
        self._options = build_options(
            model=model,
            effort=effort,
            cwd=cwd,
            portia_dir=portia_dir,
            can_use_tool=ask.build_can_use_tool(
                answer=answer, confirm=confirm, emit=self._pending.append
            ),
        )
        self._factory = client_factory or _sdk_client
        self._client: Any = None
        self._sending = False
        self.model = model
        #: Fixed for the life of the chat: effort is an option, and the SDK has
        #: no runtime equivalent of `set_model` for it.
        self.effort = effort
        #: The SDK's own id for this session, off the first result. Recorded
        #: from day one because it costs one field and is what would make
        #: "reopen this chat" an addition rather than a rewrite (§4). **Nothing
        #: reads it yet**, and recording it is not a commitment to using it.
        self.session_id: str | None = None

    async def __aenter__(self) -> Conversation:
        await self.connect()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    @property
    def open(self) -> bool:
        return self._client is not None

    @property
    def busy(self) -> bool:
        """Whether a message is in flight — **not** whether a chat exists.

        The distinction is the one `CONVERSATION.md` §9 asks the app to make: an
        open chat sitting idle must not read as busy, or it blocks indexing for
        as long as it stays open.
        """
        return self._sending

    async def connect(self) -> None:
        if self._client is not None:
            return
        # The SDK warns that auto-approved tools skip `can_use_tool`. That is the
        # design here, not an accident: read-only checks run freely and only
        # writes stop for confirmation. Silence it rather than let it fire.
        _silence_shadowed_tool_warning()
        self._client = self._factory(self._options)
        await self._client.connect()

    async def close(self) -> None:
        """End the chat. Idempotent, because every path out of a window hits it."""
        client, self._client = self._client, None
        if client is not None:
            await client.disconnect()

    async def send(self, prompt: str) -> AsyncIterator[events.Event]:
        """One exchange: send a message, yield events until the result."""
        if self._client is None:
            raise RuntimeError("this chat is not open — call connect() first")
        if self._sending:
            raise RuntimeError("a message is already in flight; interrupt it or wait for it")
        self._sending = True
        try:
            # The human's message opens the exchange, in the stream rather than
            # at each edge: the log and the transcript both need it in exactly
            # this position, and two surfaces agreeing to insert it is how they
            # come to disagree (`CONVERSATION.md` §5).
            yield events.prompt_event(prompt, model=self.model, effort=self.effort)
            await self._client.query(prompt)
            async for message in self._client.receive_response():
                while self._pending:
                    yield self._pending.pop(0)
                for event in events.from_message(message):
                    if event.kind == events.RESULT:
                        self.session_id = event.data.get("session_id") or self.session_id
                    yield event
            while self._pending:
                yield self._pending.pop(0)
        finally:
            self._sending = False

    async def interrupt(self) -> None:
        """Stop the message in flight.

        **Nothing has to be resolved first**, which is not what
        `CONVERSATION.md` §8 originally specified: the SDK cancels the parked
        `can_use_tool` task itself, a `ResultMessage` arrives, and the client
        stays usable. Measured, not assumed — `sandbox/spike/`, and §8 keeps the
        prediction that was wrong.

        What the *surface* owes is to render the cancelled decision as
        interrupted; a question form left looking answerable is backed by a
        future nobody will read.
        """
        if self._client is not None:
            await self._client.interrupt()

    async def set_model(self, model: str) -> None:
        """Switch models mid-chat. Effort cannot move — see ``effort``."""
        if self._client is None:
            raise RuntimeError("this chat is not open — call connect() first")
        await self._client.set_model(model)
        self.model = model

    async def context_usage(self) -> dict[str, Any] | None:
        """What the chat is holding, as the SDK counts it.

        A **fact**, which is why a surface may show it: token counts are
        measured. `CONVERSATION.md` §13 is the other half — no policy is built
        on top of this until a real chat has been watched hitting the ceiling.
        """
        if self._client is None:
            return None
        return cast("dict[str, Any]", await self._client.get_context_usage())


async def run(
    prompt: str,
    *,
    answer: ask.AnswerFn,
    confirm: ask.ConfirmFn,
    model: str = DEFAULT_MODEL,
    effort: str | None = None,
    cwd: str | Path | None = None,
    portia_dir: str = catalog.DEFAULT_DIR,
) -> AsyncIterator[events.Event]:
    """One exchange, in a chat that lasts exactly as long as it does.

    The one-message shape every existing caller wants — `cli/chat.py`'s three
    subcommands, `cli/index.py`, and the app's indexing jobs, none of which are
    conversations (`CONVERSATION.md` §6). Kept as a wrapper rather than a second
    implementation, so there is one drain loop and one set of ordering rules.
    """
    async with Conversation(
        answer=answer,
        confirm=confirm,
        model=model,
        effort=effort,
        cwd=cwd,
        portia_dir=portia_dir,
    ) as chat:
        async for event in chat.send(prompt):
            yield event

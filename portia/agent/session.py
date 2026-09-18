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
from portia.agent import ask, context, curation, events, prompts, providers, tools
from portia.agent.providers import anthropic as _anthropic
from portia.core import cancel

#: Where the model comes from (`agent/providers/`, `docs/PROVIDERS.md`). The
#: default is the provider the loop was built on, and a provider is fixed for
#: the life of a chat: it is the environment the SDK's binary was started in.
DEFAULT_PROVIDER = providers.DEFAULT_KIND

#: The model is a config knob, never a hard dependency (docs/PLAN.md). We develop
#: on a small one on purpose: if the loop works here, the *engine* is good.
#: Both names are the Anthropic provider's, re-exported because every caller
#: that predates the providers seam reads them here; a picker asks the
#: provider it is on (`providers.get(kind).models()`) rather than this list.
DEFAULT_MODEL = _anthropic.DEFAULT_MODEL
MODELS = _anthropic.MODELS

#: How hard the model thinks, passed straight to the SDK. The other half of
#: "develop on a cheaper, smaller model **at low effort**" (`PLAN.md` → Budget &
#: model discipline) — and the knob that makes a ceiling check on a flagship a
#: one-flag experiment rather than a code change. ``None`` leaves the SDK's default.
EFFORTS = ("low", "medium", "high", "xhigh", "max")

#: What the SDK's binary is told not to do, on every provider (`docs/PROVIDERS.md`
#: §3.1, measured 2026-09-14 by pointing the binary at a server that logs what it
#: receives). Two things the binary does on its own, and both reach the model:
#:
#: - it reads the **Claude Code auto-memory** for the working directory and
#:   puts it in the first user message as a reminder block: 4,886 characters of
#:   this repo's development notes, in a copilot whose whole context is meant
#:   to be `copilot.md` and the project brief. `setting_sources=[]` keeps
#:   CLAUDE.md out and does not touch this;
#: - it sends a **second request per chat** asking the model for a session
#:   title. Cheap on Anthropic, and on a local server a full prompt pass that
#:   also evicts the conversation's cached prefix (§6.1's request 1).
#:
#: These are not auth variables and the auth posture (`PLAN.md`) is unchanged:
#: the account still resolves inside the binary from the user's own environment.
BINARY_ENV = {
    "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
}

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


def prompt_chars(portia_dir: str = catalog.DEFAULT_DIR) -> int:
    """How long everything portia composes for the model is, in characters.

    The system prompt plus every tool description: what rides on every request
    before a word of conversation. A provider that serves a fixed context
    window compares this against it (`providers.Provider.preflight`), because a
    local server drops the front of a prompt that does not fit and the front
    is `copilot.md`. Measured here, at the one place the prompt is composed, so
    the number the preflight uses is the number the model is sent. The binary
    adds two lines of its own on top (135 characters, `docs/PROVIDERS.md`
    §3.1) and, with `BINARY_ENV` set, nothing else.
    """
    return len(build_system_prompt(portia_dir)) + sum(len(d) for d in tools.descriptions().values())


def build_options(
    *,
    model: str = DEFAULT_MODEL,
    effort: str | None = None,
    cwd: str | Path | None = None,
    portia_dir: str = catalog.DEFAULT_DIR,
    can_use_tool: Callable[..., Any] | None = None,
    curator: curation.Curation | None = None,
    resume: str | None = None,
    provider: str = DEFAULT_PROVIDER,
) -> Any:
    """Assemble ``ClaudeAgentOptions`` for a portia session.

    ``provider`` decides the environment the SDK's binary is started in and
    nothing else (`agent/providers/`): the Anthropic provider adds no variable,
    and Ollama's points the binary at the local server. Effort is refused
    rather than passed to a provider that ignores it, because a knob that is
    set and quietly dropped is the failure the surface exists to prevent.

    ``resume`` is a session id off an earlier chat's result (`Conversation.session_id`).
    The SDK reads that session's history back and the new client carries on from
    it, which is what lets a chat on disk be picked up (`docs/CHAT_SESSIONS.md`
    §3.3). The system prompt is rebuilt from the catalog as it is *now*, not as
    the old client saw it, on purpose: the catalog is what moved while the chat
    was closed.

    ``curator`` is what makes *review before you reply* a rule of the loop
    rather than a paragraph (`agent/curation.py`, `docs/FINDINGS.md` §5.3): a
    ``PostToolUse`` hook tells it which tools ran, and a ``Stop`` hook asks it
    whether the reply may end. Left out, the prose stands alone, which is the
    state every logged chat was in when the journal stayed empty.
    """
    from claude_agent_sdk import ClaudeAgentOptions

    source = providers.get(provider)
    if effort is not None and effort not in EFFORTS:
        raise ValueError(f"unknown effort {effort!r} — expected one of {', '.join(EFFORTS)}")
    if effort is not None and not source.honours_effort:
        raise ValueError(f"{source.label} does not honour effort; leave it unset")

    return ClaudeAgentOptions(
        model=model,
        # Which server the binary talks to. Empty on Anthropic, on purpose:
        # portia writes no auth code and the account resolves inside the
        # binary (`PLAN.md` → Auth posture). Ollama's provider sets the base
        # URL and the dummy token its documentation asks for, and the binary
        # merges these over the process's own environment. `BINARY_ENV` rides
        # underneath on every provider: it turns off what the binary would
        # otherwise add to the model's context, and none of it is auth.
        env={**BINARY_ENV, **source.env()},
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
        # **Only the servers named above exist.** `tools=` and `allowed_tools=`
        # restrict built-in tools and gate the rest through `can_use_tool`;
        # neither controls which MCP servers are *present*, and a Claude.ai
        # account-level connector arrives with the credential. The 2026-09-04
        # inspections session (`EVALUATION.md`) ran with eleven Google Drive
        # tools in the list, including two that read files, in a copilot that is
        # supposed to have no way out; it tried to write to Drive twice. Measured
        # on that machine: `claude -p` lists them, `--strict-mcp-config` lists
        # none. With a warehouse connection in the process this stops being a
        # leak and becomes the leak (`docs/CONNECTOR.md`).
        strict_mcp_config=True,
        cwd=str(cwd) if cwd else None,
        hooks=cast(Any, _curation_hooks(curator)) if curator is not None else None,
        resume=resume,
    )


def _curation_hooks(curator: curation.Curation) -> dict[str, Any]:
    """The two hooks that hold a reply until what it asked has been reviewed.

    The reason the model reads on a held stop is a prompt file, like every other
    string it is given (`prompts/errors/review_before_reply.md`). Nothing here
    decides what is worth keeping — the hold is lifted by `review_queries` being
    *called*, and whether a finding follows is the agent's call.
    """
    from claude_agent_sdk import HookMatcher

    async def saw_tool(input_data: Any, tool_use_id: Any, context: Any) -> dict[str, Any]:
        curator.saw_tool(str(input_data.get("tool_name") or ""))
        return {}

    async def before_stop(input_data: Any, tool_use_id: Any, context: Any) -> dict[str, Any]:
        if curator.hold():
            return {"decision": "block", "reason": prompts.error("review_before_reply")}
        return {}

    # `cast` because the SDK types a callback against every hook input at once,
    # and these two read one string each off a dict.
    return {
        "PostToolUse": [HookMatcher(hooks=[cast(Any, saw_tool)])],
        "Stop": [HookMatcher(hooks=[cast(Any, before_stop)])],
    }


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
        auto_allow: ask.AutoAllowFn | None = None,
        model: str = DEFAULT_MODEL,
        effort: str | None = None,
        cwd: str | Path | None = None,
        portia_dir: str = catalog.DEFAULT_DIR,
        client_factory: Callable[[Any], Any] | None = None,
        resume: str | None = None,
        provider: str = DEFAULT_PROVIDER,
    ) -> None:
        #: Questions and approvals are emitted from inside `can_use_tool` while
        #: the message stream is paused waiting on it, so they land here and
        #: `send` drains them from the outer loop. Ordering depends on it.
        self._pending: list[events.Event] = []
        #: Holds a reply until what it asked the data has been reviewed
        #: (`agent/curation.py`). Reset on every `send`, because the hold is
        #: about *this* reply and a question from three messages ago was the
        #: last reply's to keep.
        self.curator = curation.Curation()
        self._options = build_options(
            model=model,
            effort=effort,
            cwd=cwd,
            portia_dir=portia_dir,
            curator=self.curator,
            resume=resume,
            provider=provider,
            can_use_tool=ask.build_can_use_tool(
                answer=answer,
                confirm=confirm,
                emit=self._pending.append,
                auto_allow=auto_allow,
            ),
        )
        self._factory = client_factory or _sdk_client
        self._client: Any = None
        self._sending = False
        #: What Stop cancels on the engine's side, for the exchange in flight:
        #: the connections the copilot's tool calls open (`tools.stopping`).
        #: One per exchange, because a cancelled scope stays cancelled.
        self._stop: cancel.Scope | None = None
        self.model = model
        #: Fixed for the life of the chat: effort is an option, and the SDK has
        #: no runtime equivalent of `set_model` for it.
        self.effort = effort
        #: Fixed for the life of the chat too, and more so: it is the
        #: environment the binary was started in, so switching means a new
        #: client. `set_model` moves within a provider and never across one.
        self.provider = provider
        #: The SDK's own id for this session, off the first result. Recorded
        #: from day one because it costs one field, and since 2026-09-07 it is
        #: what a chat is picked up by (`docs/CHAT_SESSIONS.md` §3.3): a resumed
        #: chat starts out holding the id it resumed, and the first result
        #: confirms or replaces it.
        self.session_id: str | None = resume

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
        self._stop = cancel.Scope()
        self.curator.start_exchange()
        try:
            with tools.stopping(self._stop):
                # The human's message opens the exchange, in the stream rather
                # than at each edge: the log and the transcript both need it in
                # exactly this position, and two surfaces agreeing to insert it
                # is how they come to disagree (`CONVERSATION.md` §5).
                yield events.prompt_event(
                    prompt, model=self.model, effort=self.effort, provider=self.provider
                )
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
            self._stop.close()
            self._stop = None

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

        **The engine's half goes first.** The SDK's interrupt ends the loop's
        wait for a tool and reports the call rejected; it does nothing to the
        thread the tool is running on, and until 2026-09-06 that thread ran to
        completion — a six-minute BigQuery profile, on the meter, three minutes
        after the transcript said the run had ended. Cancelling the exchange's
        scope interrupts every connection those tools opened (`core/cancel.py`),
        and the tool comes back to the model as *stopped* rather than as a
        driver's traceback (`tools._stopped`).
        """
        if self._stop is not None:
            self._stop.cancel()
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
    auto_allow: ask.AutoAllowFn | None = None,
    model: str = DEFAULT_MODEL,
    effort: str | None = None,
    cwd: str | Path | None = None,
    portia_dir: str = catalog.DEFAULT_DIR,
    provider: str = DEFAULT_PROVIDER,
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
        auto_allow=auto_allow,
        model=model,
        effort=effort,
        cwd=cwd,
        portia_dir=portia_dir,
        provider=provider,
    ) as chat:
        async for event in chat.send(prompt):
            yield event

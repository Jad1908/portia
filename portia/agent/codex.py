"""The Codex harness: one chat on OpenAI's ``app-server`` (`docs/PROVIDERS.md` §9).

`session.Conversation` holds one Claude Agent SDK client across exchanges and
translates its messages into portia events. This is the same object for the
other harness, with the same public surface, so `ui/exchange.py`, `cli/chat.py`
and the log never learn which one they are on: `session.conversation` picks by
`providers.Provider.harness`.

What the Claude SDK does inside its binary has to be put back here, piece by
piece, and each piece is where the §9.2 measurements landed:

- **The tools.** Codex connects to an MCP server by URL, so
  `tools.build_server` is served on a loopback port for the life of the chat
  (`agent/loopback.py`) and every tool body still runs in this process, on
  this loop, under the exchange's cancel scope.
- **The question.** Codex's own ``request_user_input`` is listed and refused
  outside plan mode, so the question is a portia tool, `tools.ask_user`, whose
  body waits on this chat's ``answer`` through `ask.install_asker`.
- **The write gate.** A tool without a ``readOnlyHint`` makes Codex send an
  approval request before running it; `ask.decide_write` answers it, so
  ``auto_allow`` and the log's approval events are the same as on Claude. The
  request arrives on the SDK's reader thread and has to be answered from it,
  so the decision is awaited on the loop and the thread blocks until it lands.
- **Interrupt.** ``turn/interrupt`` ends the turn as *interrupted* and cancels
  the MCP request a tool was blocked in (measured). A blocked *approval* is
  portia's to unblock first: the reader thread is parked in it, and nothing,
  the interrupt's own reply included, is read until it returns.
- **Resume.** A thread id, through ``thread/resume``, the way `resume=` is a
  session id on Claude.

The SDK's client is reached through :class:`_Codex`, one small adapter a test
replaces (`_sdk_client`'s reason in `session.py`), because the SDK offloads a
synchronous JSON-RPC client to threads and reaches its approval handler through
a private attribute; keeping that in one place is what a version bump costs.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import re
from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path
from typing import Any

from portia import catalog
from portia.agent import ask, curation, events, providers, session, tools
from portia.agent.providers import codex as codex_provider
from portia.core import cancel

#: How a Codex turn ended, as portia's result ``subtype``: the same words the
#: Claude SDK uses for the two that overlap, so a log reader needs no branch.
STATUS_SUBTYPE = {"completed": "success", "interrupted": "interrupted", "failed": "error"}

#: The method Codex asks a tool's approval through, and the meta key that says so.
APPROVAL_METHOD = "mcpServer/elicitation/request"
APPROVAL_KIND = "mcp_tool_call"
#: The request names the tool only in its sentence; the arguments are beside it.
_TOOL_IN_MESSAGE = re.compile(r'run tool "([^"]+)"')


def _dump(payload: Any) -> dict[str, Any]:
    """A notification's payload as a plain dict, whatever the SDK typed it as."""
    if hasattr(payload, "model_dump"):
        return payload.model_dump(by_alias=True, exclude_none=True, mode="json")
    return dict(payload or {})


def _content_text(result: dict[str, Any] | None) -> str:
    """An MCP result's text parts joined, the way `events.tool_result_text` reads the SDK's."""
    if not result:
        return ""
    parts = [
        str(part.get("text") or "")
        for part in result.get("content") or []
        if isinstance(part, dict) and part.get("type") == "text"
    ]
    return "\n".join(p for p in parts if p)


def from_notification(method: str, params: dict[str, Any]) -> Iterator[events.Event]:
    """Translate one app-server notification into zero or more portia events.

    Only completed items are translated. Codex streams an agent message's
    deltas and its completed text; the Claude path yields whole blocks, so the
    completed item is the one that matches. A tool call yields `TOOL_CALL` on
    start and `TOOL_RESULT` on completion, named the way the Claude SDK names
    portia's tools (`events.TOOL_PREFIX`) so every renderer's label is unchanged.
    """
    item = params.get("item") or {}
    kind = item.get("type")
    if method == "item/started" and kind == "mcpToolCall":
        yield events.Event(
            events.TOOL_CALL,
            {
                "name": f"{events.TOOL_PREFIX}{item.get('tool')}",
                "input": item.get("arguments") if isinstance(item.get("arguments"), dict) else {},
                "id": item.get("id"),
            },
        )
    elif method == "item/completed":
        if kind == "agentMessage":
            text = str(item.get("text") or "").strip()
            if text:
                yield events.Event(events.TEXT, {"text": text})
        elif kind == "reasoning":
            text = "\n".join(item.get("content") or item.get("summary") or []).strip()
            if text:
                yield events.Event(events.THINKING, {"text": text})
        elif kind == "mcpToolCall":
            error = item.get("error") or {}
            failed = item.get("status") == "failed" or bool(error)
            yield events.Event(
                events.TOOL_RESULT,
                {
                    "id": item.get("id"),
                    "text": str(error.get("message") or "")
                    if failed and error
                    else _content_text(item.get("result")),
                    "is_error": failed,
                },
            )


def result_event(
    turn: dict[str, Any], usage: dict[str, Any] | None, thread_id: str
) -> events.Event:
    """The `RESULT` event off ``turn/completed``, in the Claude result's field names."""
    status = str(turn.get("status") or "completed")
    error = turn.get("error") or {}
    return events.Event(
        events.RESULT,
        {
            "subtype": STATUS_SUBTYPE.get(status, status),
            "text": str(error.get("message")) if error else None,
            "usage": usage,
            "cost_usd": None,
            "session_id": thread_id,
        },
    )


def usage_from(token_usage: dict[str, Any]) -> dict[str, Any]:
    """Codex's last-turn token breakdown, in the keys `runlog.token_totals` reads."""
    last = token_usage.get("last") or {}
    # Codex counts the cached part inside `inputTokens`; the Claude SDK reports
    # the uncached part there and the cached part beside it, and the log sums
    # the two back together. Split here so one turn is one number either way.
    cached = int(last.get("cachedInputTokens") or 0)
    return {
        "input_tokens": max(int(last.get("inputTokens") or 0) - cached, 0),
        "cache_read_input_tokens": cached,
        "output_tokens": int(last.get("outputTokens") or 0),
        "reasoning_output_tokens": int(last.get("reasoningOutputTokens") or 0),
    }


def context_from(token_usage: dict[str, Any] | None) -> dict[str, Any] | None:
    """What the thread holds, in the keys the window reads off the Claude SDK."""
    if not token_usage:
        return None
    total = int((token_usage.get("total") or {}).get("totalTokens") or 0)
    window = token_usage.get("modelContextWindow")
    out: dict[str, Any] = {"totalTokens": total, "maxTokens": window}
    if window:
        out["percentage"] = round(100.0 * total / int(window), 1)
    return out


class _Codex:
    """The SDK, behind the five calls this module makes of it."""

    def __init__(
        self,
        *,
        binary: str | None,
        overrides: tuple[str, ...],
        env: dict[str, str],
        cwd: str | None,
    ) -> None:
        from openai_codex import AsyncCodex, CodexConfig

        self._codex = AsyncCodex(
            CodexConfig(codex_bin=binary, config_overrides=overrides, env=env, cwd=cwd)
        )

    async def start(self, on_request: Callable[[str, dict | None], dict]) -> None:
        await self._codex.__aenter__()
        # The one seam the SDK has for a server's requests: a synchronous
        # handler on the client it drives from a thread.
        self._codex._client._sync._approval_handler = on_request

    async def close(self) -> None:
        await self._codex.__aexit__(None, None, None)

    async def thread(
        self, *, resume: str | None, instructions: str, model: str | None, cwd: str | None
    ) -> str:
        from openai_codex.api import AsyncThread
        from openai_codex.generated.v2_all import (
            AskForApproval,
            AskForApprovalValue,
            SandboxMode,
            ThreadResumeParams,
            ThreadStartParams,
        )

        # `on-request` with no reviewer: the typed `ApprovalMode` offers only
        # *deny all* and *an automatic reviewer*, and a write has to reach the
        # human, so the params are built directly.
        policy = AskForApproval(root=AskForApprovalValue.on_request)
        client = self._codex._client
        if resume:
            resumed = await client.thread_resume(
                resume,
                ThreadResumeParams(
                    thread_id=resume,
                    approval_policy=policy,
                    base_instructions=instructions,
                    model=model,
                    sandbox=SandboxMode.read_only,
                    cwd=cwd,
                ),
            )
            thread_id = resumed.thread.id
        else:
            started = await client.thread_start(
                ThreadStartParams(
                    approval_policy=policy,
                    base_instructions=instructions,
                    model=model,
                    sandbox=SandboxMode.read_only,
                    cwd=cwd,
                )
            )
            thread_id = started.thread.id
        self._thread = AsyncThread(self._codex, thread_id)
        return thread_id

    async def turn(self, prompt: str, *, model: str | None, effort: str | None) -> Any:
        from openai_codex.generated.v2_all import ReasoningEffort

        return await self._thread.turn(
            prompt, model=model, effort=ReasoningEffort(effort) if effort else None
        )

    async def stream(self, turn: Any) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        async for notification in turn.stream():
            yield notification.method, _dump(notification.payload)

    async def interrupt(self, turn: Any) -> None:
        await turn.interrupt()


def _client(**kw: Any) -> _Codex:
    return _Codex(**kw)


class CodexConversation:
    """One **chat** on the Codex harness: a thread held open across exchanges.

    The same surface as `session.Conversation`, member for member, and the
    same rules: a provider fixed for the chat's life, `send` refusing to
    overlap, the human's message opening the exchange in the stream.
    """

    def __init__(
        self,
        *,
        answer: ask.AnswerFn,
        confirm: ask.ConfirmFn,
        auto_allow: ask.AutoAllowFn | None = None,
        model: str = codex_provider.ACCOUNT_DEFAULT,
        effort: str | None = None,
        cwd: str | Path | None = None,
        portia_dir: str = catalog.DEFAULT_DIR,
        client_factory: Callable[..., Any] | None = None,
        resume: str | None = None,
        provider: str = codex_provider.PROVIDER.kind,
    ) -> None:
        if effort is not None and effort not in session.EFFORTS:
            raise ValueError(
                f"unknown effort {effort!r} — expected one of {', '.join(session.EFFORTS)}"
            )
        self._answer = answer
        self._confirm = confirm
        self._auto_allow = auto_allow
        self._pending: list[events.Event] = []
        self.curator = curation.Curation()
        self._factory = client_factory or _client
        self._cwd = str(cwd) if cwd else None
        self._portia_dir = portia_dir
        self._client: _Codex | None = None
        self._served: Any = None
        self._turn: Any = None
        self._sending = False
        self._stop: cancel.Scope | None = None
        #: Approvals the reader thread is parked in, so Stop can answer them.
        self._decisions: list[concurrent.futures.Future] = []
        self._loop: asyncio.AbstractEventLoop | None = None
        self._usage: dict[str, Any] | None = None
        #: The last mcpToolCall started, to name the tool an approval is about:
        #: the request carries the arguments and a sentence, never the name.
        self._calls: list[dict[str, Any]] = []
        self.model = model
        self.effort = effort
        self.provider = provider
        self.session_id: str | None = resume
        self._resume = resume

    async def __aenter__(self) -> CodexConversation:
        await self.connect()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    @property
    def open(self) -> bool:
        return self._client is not None

    @property
    def busy(self) -> bool:
        return self._sending

    async def connect(self) -> None:
        if self._client is not None:
            return
        from portia.agent import loopback

        self._loop = asyncio.get_running_loop()
        source = providers.get(self.provider)
        server = tools.build_server(sees_images=source.sees_images, asks=True)
        self._served = await loopback.serve(server["instance"])
        client = self._factory(
            binary=codex_provider.binary(),
            overrides=codex_provider.overrides(self._served.url),
            env=source.env(),
            cwd=self._cwd,
        )
        try:
            await client.start(self._on_request)
            model = None if self.model in ("", codex_provider.ACCOUNT_DEFAULT) else self.model
            self.session_id = await client.thread(
                resume=self._resume,
                instructions=session.build_system_prompt(self._portia_dir),
                model=model,
                cwd=self._cwd,
            )
        except BaseException:
            await self._served.close()
            self._served = None
            raise
        self._client = client

    async def close(self) -> None:
        client, self._client = self._client, None
        served, self._served = self._served, None
        if client is not None:
            await client.close()
        if served is not None:
            await served.close()

    async def send(self, prompt: str) -> AsyncIterator[events.Event]:
        if self._client is None:
            raise RuntimeError("this chat is not open — call connect() first")
        if self._sending:
            raise RuntimeError("a message is already in flight; interrupt it or wait for it")
        self._sending = True
        self._stop = cancel.Scope()
        self.curator.start_exchange()
        previous = ask.install_asker(ask.Asker(answer=self._answer, emit=self._pending.append))
        try:
            with tools.stopping(self._stop):
                yield events.prompt_event(
                    prompt, model=self.model, effort=self.effort, provider=self.provider
                )
                model = None if self.model in ("", codex_provider.ACCOUNT_DEFAULT) else self.model
                self._turn = await self._client.turn(prompt, model=model, effort=self.effort)
                async for method, params in self._client.stream(self._turn):
                    while self._pending:
                        yield self._pending.pop(0)
                    if method == "thread/tokenUsage/updated":
                        self._usage = params.get("tokenUsage") or {}
                    elif (
                        method == "item/started"
                        and (params.get("item") or {}).get("type") == "mcpToolCall"
                    ):
                        self._calls.append(params["item"])
                    for event in from_notification(method, params):
                        if event.kind == events.TOOL_CALL:
                            self.curator.saw_tool(str(event.data.get("name") or ""))
                        yield event
                    if method == "turn/completed":
                        yield result_event(
                            params.get("turn") or {},
                            usage_from(self._usage) if self._usage else None,
                            str(self.session_id),
                        )
                while self._pending:
                    yield self._pending.pop(0)
        finally:
            ask.install_asker(previous)
            self._sending = False
            self._turn = None
            self._stop.close()
            self._stop = None

    async def interrupt(self) -> None:
        """Stop the message in flight: unblock any parked approval, cancel the tools, end the turn."""
        for future in list(self._decisions):
            if not future.done():
                future.set_result(False)
        if self._stop is not None:
            self._stop.cancel()
        if self._client is not None and self._turn is not None:
            await self._client.interrupt(self._turn)

    async def set_model(self, model: str) -> None:
        """Switch models between exchanges. On Codex the model is a per-turn field."""
        if self._client is None:
            raise RuntimeError("this chat is not open — call connect() first")
        self.model = model

    async def context_usage(self) -> dict[str, Any] | None:
        if self._client is None:
            return None
        return context_from(self._usage)

    # --- the reader thread's one question -----------------------------------

    def _on_request(self, method: str, params: dict | None) -> dict:
        """Answer a server request. **Runs on the SDK's reader thread.**

        Only the approval is answered; anything else gets the empty answer the
        SDK's own default gives. The decision is made on the loop, where the
        surface's ``confirm`` lives, and this thread waits for it: the SDK
        cannot read another line until this returns, which is why `interrupt`
        resolves the future first.
        """
        params = params or {}
        meta = params.get("_meta") or {}
        if method != APPROVAL_METHOD or meta.get("codex_approval_kind") != APPROVAL_KIND:
            return {}
        if self._loop is None:
            return {"action": "decline"}
        name, arguments = self._name_the_call(params)
        future = asyncio.run_coroutine_threadsafe(self._decide(name, arguments), self._loop)
        self._decisions.append(future)
        try:
            allowed = future.result()
        except (concurrent.futures.CancelledError, Exception):  # noqa: BLE001 - never a raise on the reader thread
            allowed = False
        finally:
            if future in self._decisions:
                self._decisions.remove(future)
        # Never `persist`: an approval kept for the session is a write that no
        # longer stops, decided once and applied silently after.
        return {"action": "accept", "content": {}} if allowed else {"action": "decline"}

    def _name_the_call(self, params: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        meta = params.get("_meta") or {}
        raw = meta.get("tool_params")
        arguments: dict[str, Any] = dict(raw) if isinstance(raw, dict) else {}
        match = _TOOL_IN_MESSAGE.search(str(params.get("message") or ""))
        name = match.group(1) if match else ""
        if not name:
            for call in reversed(self._calls):
                if call.get("arguments") == arguments:
                    name = str(call.get("tool") or "")
                    break
        return f"{events.TOOL_PREFIX}{name}", arguments

    async def _decide(self, name: str, arguments: dict[str, Any]) -> bool:
        return await ask.decide_write(
            name,
            arguments,
            confirm=self._confirm,
            emit=self._pending.append,
            auto_allow=self._auto_allow,
        )

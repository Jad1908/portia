"""The chat seam — one client, many exchanges (`docs/CONVERSATION.md` §2, §7).

Driven with a fake client rather than the SDK. What is worth pinning here is
portia's own logic — the drain order that keeps a question ahead of the text
that provoked it, the refusal to overlap two messages, and the lifecycle — and
none of that should cost a model call. The SDK's *own* behaviour was measured
separately and is not re-asserted here (`sandbox/spike/`, §8).
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from portia.agent import events

pytest.importorskip("claude_agent_sdk", reason="needs the `agent` extra")

from claude_agent_sdk import ResultMessage  # noqa: E402

from portia.agent import session  # noqa: E402


def FakeResult(session_id: str = "sess-1", subtype: str = "success") -> ResultMessage:
    """A **real** ``ResultMessage``.

    Not a stand-in: `events.from_message` dispatches on ``isinstance``, so a
    look-alike is silently dropped rather than translated — which is exactly
    what the first draft of these tests did, and what they caught.
    """
    return ResultMessage(
        subtype=subtype,
        duration_ms=10,
        duration_api_ms=8,
        is_error=False,
        num_turns=1,
        session_id=session_id,
        total_cost_usd=0.01,
        usage={},
    )


class FakeClient:
    """A connected client that replays a scripted message list per exchange."""

    def __init__(self, scripts: list[list[Any]]) -> None:
        self._scripts = scripts
        self.sent: list[str] = []
        self.connected = False
        self.interrupted = 0
        self.model_set: list[str] = []
        self._on_query = None

    async def connect(self) -> None:
        self.connected = True

    async def disconnect(self) -> None:
        self.connected = False

    async def query(self, prompt: str) -> None:
        self.sent.append(prompt)
        if self._on_query is not None:
            await self._on_query()

    async def receive_response(self):
        for message in self._scripts.pop(0):
            yield message

    async def interrupt(self) -> None:
        self.interrupted += 1

    async def set_model(self, model: str) -> None:
        self.model_set.append(model)

    async def get_context_usage(self) -> dict:
        return {"totalTokens": 1234, "maxTokens": 200_000, "percentage": 0.6}


def _chat(scripts: list[list[Any]], **kw) -> tuple[session.Conversation, FakeClient]:
    client = FakeClient(scripts)

    async def answer(questions):
        return {}

    async def confirm(tool_name, tool_input):
        return True

    chat = session.Conversation(
        answer=answer, confirm=confirm, client_factory=lambda _o: client, **kw
    )
    return chat, client


def _drain(chat: session.Conversation, prompt: str) -> list[events.Event]:
    async def go():
        async with chat:
            return [e async for e in chat.send(prompt)]

    return asyncio.run(go())


# --- one client, many exchanges ---------------------------------------------


def test_two_exchanges_reuse_one_client():
    """The point of the whole change: the client outlives one prompt (§2)."""
    chat, client = _chat([[FakeResult()], [FakeResult()]])

    async def go():
        async with chat:
            [_ async for _ in chat.send("first")]
            [_ async for _ in chat.send("second")]
            return client.connected

    assert asyncio.run(go()) is True
    assert client.sent == ["first", "second"]
    assert client.connected is False  # closed on the way out


def test_the_exchange_opens_with_the_humans_message():
    """§5 — the model and effort ride on the prompt, not the header, because a
    chat can span several and a header field that changes mid-file is a lie."""
    chat, _ = _chat([[FakeResult()]], model="claude-haiku-4-5", effort="low")
    stream = _drain(chat, "merge reservations into hotels")

    opening = stream[0]
    assert opening.kind == events.PROMPT
    assert opening.data == {
        "text": "merge reservations into hotels",
        "model": "claude-haiku-4-5",
        "effort": "low",
        "provider": "anthropic",
    }


def test_the_session_id_is_recorded_off_the_result():
    """§4 — one field, kept from day one so reopening a chat stays an addition."""
    chat, _ = _chat([[FakeResult(session_id="abc123")]])
    stream = _drain(chat, "hello")

    result = next(e for e in stream if e.kind == events.RESULT)
    assert result.data["session_id"] == "abc123"
    assert chat.session_id == "abc123"


def test_connect_is_idempotent_and_close_is_too():
    chat, client = _chat([])

    async def go():
        await chat.connect()
        await chat.connect()
        assert chat.open is True
        await chat.close()
        await chat.close()
        return chat.open

    assert asyncio.run(go()) is False
    assert client.connected is False


# --- the send rule (§7) ------------------------------------------------------


def test_a_second_message_is_refused_rather_than_queued():
    """§7: there is no queue. A queued message would have to land either before
    or after whatever the agent does next, and neither is defensible when what
    it does next might be to ask you a question."""
    chat, client = _chat([[FakeResult()]])

    async def go():
        async with chat:
            stream = chat.send("first")
            assert (await stream.__anext__()).kind == events.PROMPT
            await stream.__anext__()  # past the query — genuinely in flight
            with pytest.raises(RuntimeError, match="already in flight"):
                await chat.send("second").__anext__()
            await stream.aclose()

    asyncio.run(go())
    assert client.sent == ["first"]


def test_busy_means_a_message_is_in_flight_not_that_a_chat_exists():
    """§9's rework depends on this: an idle open chat must not block indexing."""
    chat, _ = _chat([[FakeResult()]])

    async def go():
        async with chat:
            idle_before = chat.busy
            [_ async for _ in chat.send("go")]
            return idle_before, chat.busy

    before, after = asyncio.run(go())
    assert before is False and after is False


def test_sending_before_connecting_is_an_error_not_a_silent_connect():
    chat, _ = _chat([[FakeResult()]])

    async def go():
        with pytest.raises(RuntimeError, match="not open"):
            await chat.send("hello").__anext__()

    asyncio.run(go())


def test_the_guard_lifts_after_an_exchange_ends_badly():
    """A stream abandoned mid-flight must not wedge the chat shut."""

    class Boom(Exception):
        pass

    class Exploding(FakeClient):
        async def receive_response(self):
            raise Boom
            yield  # pragma: no cover

    client = Exploding([])

    async def answer(questions):
        return {}

    async def confirm(name, inp):
        return True

    chat = session.Conversation(answer=answer, confirm=confirm, client_factory=lambda _o: client)

    async def go():
        async with chat:
            with pytest.raises(Boom):
                [_ async for _ in chat.send("first")]
            return chat.busy

    assert asyncio.run(go()) is False


# --- ordering ----------------------------------------------------------------


def test_a_question_is_yielded_before_the_message_that_follows_it():
    """`can_use_tool` emits into a buffer while the stream is paused waiting on
    it, so the buffer has to drain *first* or the transcript reads out of order.
    """
    chat, client = _chat([[FakeResult()]])

    async def emit_a_question():
        # Stand in for the callback firing mid-stream, as ask.py's does.
        chat._pending.append(events.question_event([{"question": "which key?"}]))

    client._on_query = emit_a_question
    stream = _drain(chat, "merge them")

    assert [e.kind for e in stream] == [events.PROMPT, events.QUESTION, events.RESULT]


# --- passthroughs ------------------------------------------------------------


def test_interrupt_reaches_the_client_and_needs_no_preparation():
    """§8, after measurement: the SDK cancels the parked callback itself, so
    there is nothing to resolve first and no ordering constraint on the button."""
    chat, client = _chat([])

    async def go():
        async with chat:
            await chat.interrupt()

    asyncio.run(go())
    assert client.interrupted == 1


def test_interrupting_a_chat_that_was_never_open_is_a_no_op():
    chat, client = _chat([])
    asyncio.run(chat.interrupt())
    assert client.interrupted == 0


def test_the_model_can_change_mid_chat_and_effort_cannot():
    chat, client = _chat([], effort="low")

    async def go():
        async with chat:
            await chat.set_model("claude-opus-5")

    asyncio.run(go())
    assert client.model_set == ["claude-opus-5"]
    assert chat.model == "claude-opus-5"
    # Effort is an option, fixed for the life of the client.
    assert chat.effort == "low"
    assert not hasattr(chat, "set_effort")


def test_context_usage_is_none_until_there_is_a_client():
    chat, _ = _chat([])
    assert asyncio.run(chat.context_usage()) is None

    async def go():
        async with chat:
            return await chat.context_usage()

    assert asyncio.run(go())["totalTokens"] == 1234


# --- the wrapper every existing caller uses ----------------------------------


def test_run_is_one_exchange_and_closes_behind_itself():
    """`cli/index.py` and the three chat subcommands are not conversations (§6),
    so `run` stays their shape — a wrapper, not a second implementation."""
    client = FakeClient([[FakeResult()]])

    async def answer(questions):
        return {}

    async def confirm(name, inp):
        return True

    original = session._sdk_client
    session._sdk_client = lambda _o: client  # type: ignore[assignment]
    try:

        async def go():
            return [
                e
                async for e in session.run("interpret reservations", answer=answer, confirm=confirm)
            ]

        stream = asyncio.run(go())
    finally:
        session._sdk_client = original  # type: ignore[assignment]

    assert [e.kind for e in stream] == [events.PROMPT, events.RESULT]
    assert client.sent == ["interpret reservations"]
    assert client.connected is False


def test_only_portias_own_mcp_server_exists(tmp_path):
    """`strict_mcp_config` is on, and it is the whole of the fix.

    `tools=` and `allowed_tools=` never controlled which servers were *present*:
    an account-level Claude.ai connector arrives with the credential, and the
    2026-09-04 inspections session ran with eleven Google Drive tools in a
    copilot that is supposed to have no way out (`EVALUATION.md`). The flag is
    what `claude --strict-mcp-config` measured as removing them.
    """
    options = session.build_options(portia_dir=str(tmp_path / ".portia"))

    assert options.strict_mcp_config is True
    assert list(options.mcp_servers) == [session.tools.SERVER_NAME]


# --- Stop reaches the engine, not only the SDK -------------------------------


def test_interrupt_cancels_the_exchanges_scope_before_the_client():
    """§8's other half (2026-09-06): the SDK's interrupt ends the wait for a
    tool and leaves the tool's thread running. The scope is what reaches it."""
    from portia.agent import tools

    seen: dict[str, Any] = {}

    async def on_query():
        seen["scope"] = tools._stop
        await chat.interrupt()
        seen["cancelled"] = tools._stop.cancelled
        seen["interrupted_after"] = client.interrupted

    chat, client = _chat([[FakeResult()]])
    client._on_query = on_query
    _drain(chat, "go")

    assert seen["scope"] is not None
    assert seen["cancelled"] is True
    assert seen["interrupted_after"] == 1
    # The slot is empty again once the exchange ends: a scope that outlived
    # its exchange would cancel the next one's tools.
    assert tools._stop is None


def test_each_exchange_gets_its_own_scope():
    from portia.agent import tools

    scopes: list[Any] = []

    async def on_query():
        scopes.append(tools._stop)

    chat, client = _chat([[FakeResult()], [FakeResult()]])
    client._on_query = on_query

    async def go():
        async with chat:
            [e async for e in chat.send("one")]
            [e async for e in chat.send("two")]

    asyncio.run(go())
    assert len(scopes) == 2 and scopes[0] is not scopes[1]
    assert not scopes[0].cancelled and not scopes[1].cancelled


def test_a_resumed_chat_hands_the_session_id_to_the_sdk():
    """`docs/CHAT_SESSIONS.md` §3.3, measured in `sandbox/spike/resume_check.py`:
    the SDK reads the session back and the second process answers from the
    first's tool results. What portia owes is one option, and to start out
    holding the id it resumed."""
    seen = {}

    def factory(options):
        seen["resume"] = options.resume
        return FakeClient([[]])

    async def answer(questions):
        return {}

    async def confirm(name, payload):
        return False

    chat = session.Conversation(
        answer=answer, confirm=confirm, client_factory=factory, resume="sess-1"
    )
    assert chat.session_id == "sess-1"
    asyncio.run(chat.connect())
    assert seen["resume"] == "sess-1"

    fresh = session.Conversation(answer=answer, confirm=confirm, client_factory=factory)
    assert fresh.session_id is None
    asyncio.run(fresh.connect())
    assert seen["resume"] is None


# --- where the model comes from (`agent/providers/`) --------------------------


def test_the_default_provider_starts_the_binary_with_no_auth_variable():
    """`PLAN.md` → Auth posture, unchanged by the seam existing: the only
    variables on the default path are the binary's own switches, and none of
    them names Anthropic or carries a credential."""
    options = session.build_options(portia_dir="nowhere/.portia")
    assert options.env == session.BINARY_ENV
    assert not any(key.startswith("ANTHROPIC") for key in options.env)


def test_every_provider_turns_off_what_the_binary_would_add_to_the_context():
    """`PROVIDERS.md` §3.1, measured 2026-09-14: without these the binary puts
    the working directory's Claude Code auto-memory into the first message and
    sends a second request per chat for a session title. Neither is the
    provider's business, so the switches ride underneath every provider's own
    environment rather than being one provider's to remember."""
    from portia.agent import providers

    assert session.BINARY_ENV == {
        "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    }
    for kind in providers.KINDS:
        options = session.build_options(
            provider=kind, model=providers.get(kind).default_model, portia_dir="nowhere/.portia"
        )
        for key, value in session.BINARY_ENV.items():
            assert options.env[key] == value, kind


def test_the_claude_harness_runs_the_chosen_program(monkeypatch):
    """`PROVIDERS.md` §4.10: the program is the machine's own when it is at
    least as new as the bundled one, and the SDK is told which, on every
    provider that runs this harness. Nothing found hands the choice back to
    the SDK, which takes its bundled copy."""
    from portia.agent import providers
    from portia.agent.providers import anthropic

    chosen = providers.Program("/opt/claude", providers.MACHINE, "2.1.300 (Claude Code)")
    monkeypatch.setattr(anthropic, "program", lambda: chosen)
    for kind in providers.KINDS:
        if providers.get(kind).harness != providers.CLAUDE:
            continue
        options = session.build_options(
            provider=kind, model=providers.get(kind).default_model, portia_dir="nowhere/.portia"
        )
        assert str(options.cli_path) == "/opt/claude", kind
    assert options.setting_sources == []
    monkeypatch.setattr(anthropic, "program", lambda: None)
    assert session.build_options(portia_dir="nowhere/.portia").cli_path is None


def test_ollama_starts_the_binary_pointed_at_the_local_server(monkeypatch):
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    options = session.build_options(
        provider="ollama", model="qwen3:8b", portia_dir="nowhere/.portia"
    )
    assert options.env["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:11434"
    assert options.env["ANTHROPIC_AUTH_TOKEN"] == "ollama"
    assert options.model == "qwen3:8b"


def test_effort_is_refused_on_a_provider_that_would_ignore_it():
    """A knob that is set and quietly dropped is the failure the surface exists
    to prevent (`DESIGN.md`); refusing keeps the CLI honest too."""
    with pytest.raises(ValueError, match="does not honour effort"):
        session.build_options(provider="ollama", effort="low", portia_dir="nowhere/.portia")


def test_the_exchange_says_which_provider_it_ran_on():
    chat, _ = _chat([[FakeResult()]], provider="ollama", model="qwen3:8b")
    opening = _drain(chat, "hello")[0]
    assert opening.data["provider"] == "ollama"
    assert chat.provider == "ollama"


def test_the_prompt_length_the_preflight_uses_is_the_prompt_the_model_is_sent():
    from portia.agent import tools

    chars = session.prompt_chars("nowhere/.portia")
    assert chars == len(session.build_system_prompt("nowhere/.portia")) + sum(
        len(d) for d in tools.descriptions().values()
    )
    assert chars > 40_000  # copilot.md and fifteen descriptions; the number the doc quotes


# --- a job that reads (2026-09-23) --------------------------------------------


def test_a_job_that_reads_gets_options_with_no_build_tool(monkeypatch):
    """Neither the server nor the permission list names `record_step` or
    `run_spec`, so an indexing job cannot write a step however the prompt reads."""
    served: list[dict] = []
    real = session.tools.build_server

    def spy(**kw):
        served.append(kw)
        return real(**kw)

    monkeypatch.setattr(session.tools, "build_server", spy)
    options = session.build_options(portia_dir="nowhere/.portia", builds=False)

    assert served[-1]["builds"] is False
    assert session.tools.qualified("run_spec") not in options.allowed_tools
    assert session.tools.qualified("describe_source") in options.allowed_tools

    session.build_options(portia_dir="nowhere/.portia")
    assert served[-1]["builds"] is True, "a chat builds"

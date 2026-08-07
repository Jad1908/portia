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
    stream = _drain(chat, "merge otb into hotels")

    opening = stream[0]
    assert opening.kind == events.PROMPT
    assert opening.data == {
        "text": "merge otb into hotels",
        "model": "claude-haiku-4-5",
        "effort": "low",
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
            return [e async for e in session.run("interpret otb", answer=answer, confirm=confirm)]

        stream = asyncio.run(go())
    finally:
        session._sdk_client = original  # type: ignore[assignment]

    assert [e.kind for e in stream] == [events.PROMPT, events.RESULT]
    assert client.sent == ["interpret otb"]
    assert client.connected is False

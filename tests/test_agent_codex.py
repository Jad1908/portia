"""The Codex harness (`agent/codex.py`, `docs/PROVIDERS.md` §9).

Driven with a fake app-server rather than the SDK, for `test_agent_session.py`'s
reason: what is worth pinning is portia's own logic — the translation of
notifications into the one event stream, the write gate answered from the
reader thread, Stop unblocking a parked approval, the question routed to this
chat's ``answer`` — and none of it should cost a model call. What Codex itself
does was measured separately (`sandbox/codex-spike/`, §9.2) and is not
re-asserted here.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any

import pytest

from portia.agent import ask, events

pytest.importorskip("claude_agent_sdk", reason="needs the `agent` extra")

from portia.agent import codex, session, tools  # noqa: E402
from portia.agent.providers import codex as codex_provider  # noqa: E402

THREAD = "01a0-thread"
TURN = "01a0-turn"


def _item(kind: str, **fields: Any) -> dict[str, Any]:
    return {"threadId": THREAD, "turnId": TURN, "item": {"type": kind, **fields}}


def started(kind: str, **fields: Any) -> tuple[str, dict]:
    return "item/started", _item(kind, **fields)


def completed(kind: str, **fields: Any) -> tuple[str, dict]:
    return "item/completed", _item(kind, **fields)


def usage(total: int = 1500, window: int = 32000) -> tuple[str, dict]:
    last = {
        "inputTokens": 1000,
        "cachedInputTokens": 900,
        "outputTokens": 50,
        "reasoningOutputTokens": 0,
    }
    return "thread/tokenUsage/updated", {
        "threadId": THREAD,
        "turnId": TURN,
        "tokenUsage": {
            "last": last,
            "total": {**last, "totalTokens": total},
            "modelContextWindow": window,
        },
    }


def turn_completed(status: str = "completed", **turn: Any) -> tuple[str, dict]:
    return "turn/completed", {
        "threadId": THREAD,
        "turn": {"id": TURN, "status": status, "items": [], **turn},
    }


class FakeCodex:
    """A started app-server that replays a scripted notification list per turn.

    ``ask`` is what the fake does *during* a turn's stream: a callable given the
    conversation, run on a thread the way the SDK's reader thread runs the real
    approval handler, so the write gate is exercised where it lives.
    """

    def __init__(self, scripts: list[list[tuple[str, dict]]], ask: Any = None) -> None:
        self.scripts = scripts
        self.ask = ask
        self.kw: dict[str, Any] = {}
        self.started = False
        self.closed = False
        self.threads: list[dict[str, Any]] = []
        self.turns: list[dict[str, Any]] = []
        self.interrupted = 0
        self.on_request = None

    async def start(self, on_request) -> None:
        self.started = True
        self.on_request = on_request

    async def close(self) -> None:
        self.closed = True

    async def thread(self, **kw) -> str:
        self.threads.append(kw)
        return kw.get("resume") or THREAD

    async def turn(self, prompt: str, *, model, effort) -> dict[str, Any]:
        self.turns.append({"prompt": prompt, "model": model, "effort": effort})
        return {"id": TURN}

    async def stream(self, turn):
        script = self.scripts.pop(0)
        for method, params in script:
            if method == "__ask__":
                # The approval, from a thread, as the SDK's reader thread does it.
                answer: dict[str, Any] = {}

                def go(answer=answer, params=params) -> None:
                    answer["out"] = self.on_request(codex.APPROVAL_METHOD, params)

                worker = threading.Thread(target=go)
                worker.start()
                while worker.is_alive():
                    await asyncio.sleep(0.01)
                self.decisions = getattr(self, "decisions", []) + [answer["out"]]
                continue
            yield method, params

    async def interrupt(self, turn) -> None:
        self.interrupted += 1


def _factory(fake: FakeCodex):
    def make(**kw):
        fake.kw = kw
        return fake

    return make


@pytest.fixture(autouse=True)
def no_loopback(monkeypatch):
    """The tools are not served over a real port in these tests."""
    from portia.agent import loopback

    class Served:
        url = "http://127.0.0.1:1/mcp"

        async def close(self) -> None:
            pass

    async def serve(instance):
        return Served()

    monkeypatch.setattr(loopback, "serve", serve)
    monkeypatch.setattr(codex_provider, "binary", lambda: "/bin/codex")
    monkeypatch.setattr(codex_provider, "home", lambda: "/tmp/codex-home")
    monkeypatch.setattr(codex_provider, "base_url", lambda: "")


def _chat(fake: FakeCodex, *, confirm=None, auto_allow=None, answer=None, **kw):
    async def yes(tool_name, tool_input):
        return True

    async def no_questions(questions):
        return {}

    return codex.CodexConversation(
        answer=answer or no_questions,
        confirm=confirm or yes,
        auto_allow=auto_allow,
        client_factory=_factory(fake),
        portia_dir="nowhere/.portia",
        **kw,
    )


def _drain(chat, prompt: str) -> list[events.Event]:
    async def go():
        async with chat:
            return [e async for e in chat.send(prompt)]

    return asyncio.run(go())


# --- translation ---------------------------------------------------------------


def test_a_tool_call_and_its_result_are_named_the_way_the_claude_sdk_names_them():
    call = list(
        codex.from_notification(
            *started(
                "mcpToolCall",
                id="c1",
                tool="profile_source",
                arguments={"source": "s"},
                server="portia",
                status="inProgress",
            )
        )
    )
    assert call == [
        events.Event(
            events.TOOL_CALL,
            {"name": "mcp__portia__profile_source", "input": {"source": "s"}, "id": "c1"},
        )
    ]
    assert events.tool_label(call[0].data["name"]) == "profile_source"
    done = list(
        codex.from_notification(
            *completed(
                "mcpToolCall",
                id="c1",
                tool="profile_source",
                arguments={},
                server="portia",
                status="completed",
                result={"content": [{"type": "text", "text": "rows: 3"}]},
            )
        )
    )
    assert done == [
        events.Event(events.TOOL_RESULT, {"id": "c1", "text": "rows: 3", "is_error": False})
    ]


def test_a_failed_tool_call_carries_the_servers_message_as_an_error():
    done = list(
        codex.from_notification(
            *completed(
                "mcpToolCall",
                id="c1",
                tool="record_step",
                arguments={},
                server="portia",
                status="failed",
                error={"message": "user rejected MCP tool call"},
            )
        )
    )
    assert done[0].data == {"id": "c1", "text": "user rejected MCP tool call", "is_error": True}


def test_prose_and_reasoning_are_taken_off_the_completed_item_only():
    assert list(codex.from_notification("item/agentMessage/delta", {"delta": "do"})) == []
    assert list(codex.from_notification(*started("agentMessage", id="m", text=""))) == []
    assert list(codex.from_notification(*completed("agentMessage", id="m", text="done "))) == [
        events.Event(events.TEXT, {"text": "done"})
    ]
    assert list(
        codex.from_notification(*completed("reasoning", id="r", content=["hm"], summary=[]))
    ) == [events.Event(events.THINKING, {"text": "hm"})]


def test_the_result_speaks_the_claude_results_words_and_prices_nothing():
    event = codex.result_event({"status": "interrupted", "items": []}, None, THREAD)
    assert event.data["subtype"] == "interrupted"
    assert event.data["cost_usd"] is None
    assert event.data["session_id"] == THREAD
    failed = codex.result_event({"status": "failed", "error": {"message": "boom"}}, None, THREAD)
    assert failed.data["subtype"] == "error" and failed.data["text"] == "boom"


def test_usage_is_read_into_the_keys_the_log_totals():
    from portia import runlog

    _, params = usage()
    totals = runlog.token_totals(codex.usage_from(params["tokenUsage"]))
    assert totals == {"input_tokens": 1000, "cached_tokens": 900, "output_tokens": 50}
    assert codex.context_from(params["tokenUsage"]) == {
        "totalTokens": 1500,
        "maxTokens": 32000,
        "percentage": 4.7,
    }
    assert codex.context_from(None) is None


# --- the conversation --------------------------------------------------------------


def test_an_exchange_opens_with_the_message_and_ends_with_a_result_carrying_the_thread_id():
    fake = FakeCodex([[completed("agentMessage", id="m", text="hi"), usage(), turn_completed()]])
    chat = _chat(fake, model="gpt-x", effort="low")
    got = _drain(chat, "hello")
    assert [e.kind for e in got] == [events.PROMPT, events.TEXT, events.RESULT]
    assert got[0].data == {"text": "hello", "model": "gpt-x", "effort": "low", "provider": "codex"}
    assert got[-1].data["session_id"] == THREAD and got[-1].data["subtype"] == "success"
    assert got[-1].data["usage"] == {
        "input_tokens": 100,
        "cache_read_input_tokens": 900,
        "output_tokens": 50,
        "reasoning_output_tokens": 0,
    }
    assert chat.session_id == THREAD
    assert fake.turns == [{"prompt": "hello", "model": "gpt-x", "effort": "low"}]
    assert fake.closed


def test_the_account_default_is_sent_as_no_model_at_all():
    fake = FakeCodex([[turn_completed()]])
    _drain(_chat(fake), "hello")
    assert fake.threads[0]["model"] is None
    assert fake.turns[0]["model"] is None


def test_the_thread_is_started_with_portias_system_prompt_and_the_tools_url():
    fake = FakeCodex([[turn_completed()]])
    _drain(_chat(fake, cwd="/proj"), "hello")
    assert fake.threads[0]["instructions"].startswith(
        session.PROMPT_PATH.read_text(encoding="utf-8")[:40]
    )
    assert fake.threads[0]["cwd"] == "/proj"
    assert any("mcp_servers.portia.url=" in line for line in fake.kw["overrides"])
    assert fake.kw["binary"] == "/bin/codex"


def test_a_write_stops_for_the_human_and_the_answer_reaches_codex_as_an_elicitation():
    request = {
        "_meta": {"codex_approval_kind": "mcp_tool_call", "tool_params": {"name": "s"}},
        "message": 'Allow the portia MCP server to run tool "record_step"?',
    }
    fake = FakeCodex(
        [
            [
                started(
                    "mcpToolCall",
                    id="c1",
                    tool="record_step",
                    arguments={"name": "s"},
                    server="portia",
                    status="inProgress",
                ),
                ("__ask__", request),
                turn_completed(),
            ]
        ]
    )
    seen: list[tuple[str, dict]] = []

    async def confirm(tool_name, tool_input):
        seen.append((tool_name, tool_input))
        return tool_name.endswith("record_step")

    got = _drain(_chat(fake, confirm=confirm), "go")
    assert seen == [("mcp__portia__record_step", {"name": "s"})]
    assert fake.decisions == [{"action": "accept", "content": {}}]
    kinds = [e.kind for e in got]
    assert (
        kinds.index(events.APPROVAL)
        < kinds.index(events.APPROVAL_RESULT)
        < kinds.index(events.RESULT)
    )
    approval = next(e for e in got if e.kind == events.APPROVAL_RESULT)
    assert approval.data["allowed"] is True and "waited" in approval.data


def test_a_declined_write_is_declined_and_an_automatic_one_never_waits():
    request = {
        "_meta": {"codex_approval_kind": "mcp_tool_call", "tool_params": {}},
        "message": 'run tool "set_group"?',
    }
    fake = FakeCodex(
        [[("__ask__", request), turn_completed()], [("__ask__", request), turn_completed()]]
    )

    async def no(tool_name, tool_input):
        return False

    chat = _chat(fake, confirm=no, auto_allow=lambda name: name.endswith("set_group"))
    got = _drain(chat, "go")
    assert fake.decisions == [{"action": "accept", "content": {}}]
    auto = next(e for e in got if e.kind == events.APPROVAL_RESULT)
    assert auto.data.get("auto") is True and "waited" not in auto.data

    chat2 = _chat(fake, confirm=no)
    _drain(chat2, "go")
    assert fake.decisions[-1] == {"action": "decline"}


def test_anything_but_a_tool_approval_gets_the_empty_answer():
    fake = FakeCodex([])
    chat = _chat(fake)
    assert chat._on_request("item/commandExecution/requestApproval", {}) == {}
    assert (
        chat._on_request(codex.APPROVAL_METHOD, {"_meta": {"codex_approval_kind": "other"}}) == {}
    )


def test_interrupt_unblocks_a_parked_approval_as_a_decline_before_ending_the_turn():
    request = {
        "_meta": {"codex_approval_kind": "mcp_tool_call", "tool_params": {}},
        "message": 'run tool "record_step"?',
    }
    fake = FakeCodex([[("__ask__", request), turn_completed("interrupted")]])
    chat = _chat(fake)
    parked = asyncio.Event()

    async def confirm(tool_name, tool_input):
        parked.set()
        await asyncio.sleep(3600)  # the human never comes
        return True

    chat._confirm = confirm

    async def go():
        async with chat:

            async def stop_later():
                await parked.wait()
                await chat.interrupt()

            stopper = asyncio.create_task(stop_later())
            got = [e async for e in chat.send("go")]
            await stopper
            return got

    got = asyncio.run(go())
    assert fake.decisions == [{"action": "decline"}]
    assert fake.interrupted == 1
    assert got[-1].data["subtype"] == "interrupted"
    assert chat._decisions == []


def test_a_question_reaches_this_chats_answer_through_the_tool():
    asked: list[list[dict]] = []

    async def answer(questions):
        asked.append(questions)
        return {"Which?": "b"}

    fake = FakeCodex([[turn_completed()]])
    chat = _chat(fake, answer=answer)
    questions = [
        {"header": "K", "question": "Which?", "options": [{"label": "a", "description": ""}]}
    ]

    async def go():
        async with chat:
            # What `tools.ask_user`'s body does while an exchange is under way.
            previous = ask.install_asker(ask.Asker(answer=chat._answer, emit=chat._pending.append))
            try:
                result = await tools.ask_user.handler({"questions": questions})
            finally:
                ask.install_asker(previous)
            return result, list(chat._pending)

    result, pending = asyncio.run(go())
    assert asked == [questions]
    assert '"Which?":"b"' in result["content"][0]["text"]
    assert [e.kind for e in pending] == [events.QUESTION, events.ANSWER]
    assert pending[1].data["answers"] == {"Which?": "b"}


def test_with_no_chat_listening_the_question_tool_refuses_rather_than_hanging():
    ask.install_asker(None)
    result = asyncio.run(tools.ask_user.handler({"questions": []}))
    assert result.get("is_error") is True


def test_a_resumed_chat_hands_the_thread_id_back():
    fake = FakeCodex([[turn_completed()]])
    chat = _chat(fake, resume="old-thread")
    _drain(chat, "again")
    assert fake.threads[0]["resume"] == "old-thread"
    assert chat.session_id == "old-thread"


def test_a_second_message_is_refused_rather_than_queued():
    fake = FakeCodex([[turn_completed()]])
    chat = _chat(fake)

    async def go():
        async with chat:
            stream = chat.send("one")
            await stream.__anext__()  # the prompt event: in flight now
            with pytest.raises(RuntimeError, match="already in flight"):
                async for _ in chat.send("two"):
                    pass
            async for _ in stream:
                pass

    asyncio.run(go())


def test_effort_is_checked_against_portias_vocabulary():
    with pytest.raises(ValueError, match="unknown effort"):
        _chat(FakeCodex([]), effort="turbo")


def test_the_model_can_move_between_exchanges_and_rides_the_next_turn():
    fake = FakeCodex([[turn_completed()], [turn_completed()]])
    chat = _chat(fake, model="a")

    async def go():
        async with chat:
            async for _ in chat.send("1"):
                pass
            await chat.set_model("b")
            async for _ in chat.send("2"):
                pass

    asyncio.run(go())
    assert [t["model"] for t in fake.turns] == ["a", "b"]


# --- the seam -------------------------------------------------------------------


def test_the_session_factory_picks_the_harness_by_provider():
    async def answer(questions):
        return {}

    async def confirm(name, args):
        return True

    chat = session.conversation(
        provider="codex", answer=answer, confirm=confirm, client_factory=_factory(FakeCodex([]))
    )
    assert isinstance(chat, codex.CodexConversation)
    other = session.conversation(
        provider="anthropic", answer=answer, confirm=confirm, client_factory=lambda o: None
    )
    assert isinstance(other, session.Conversation)


def test_only_the_codex_harness_is_offered_the_question_tool():
    assert "ask_user" in tools.descriptions(asks=True)
    assert "ask_user" not in tools.descriptions()
    assert session.asks("codex") and not session.asks("anthropic")
    assert session.prompt_chars("nowhere/.portia", "codex") > session.prompt_chars(
        "nowhere/.portia", "ollama"
    )


def test_the_question_tool_is_read_only_so_codex_runs_it_without_an_approval():
    from devtools.context import read_only

    assert read_only(tools.ask_user.annotations) is True
    assert tools.ask_user not in tools.READ_TOOLS and tools.ask_user not in tools.WRITE_TOOLS


def test_a_job_that_reads_is_served_no_build_tool(monkeypatch):
    """The same rule as the Claude harness: an indexing job's loopback server
    lists no `record_step` and no `run_spec` (`tools.BUILD_TOOLS`)."""
    served: list[dict] = []
    real = codex.tools.build_server

    def spy(**kw):
        served.append(kw)
        return real(**kw)

    monkeypatch.setattr(codex.tools, "build_server", spy)
    _drain(_chat(FakeCodex([[turn_completed()]]), builds=False), "read these")
    assert served[-1]["builds"] is False

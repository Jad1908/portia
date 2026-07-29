"""The two moments the loop stops for a human, as events.

`APPROVAL` says a write stopped for a yes/no and never said which was given, so
a stream carrying only that cannot answer *"how many writes were refused"* —
one of the few things the run log can measure without an answer key
(docs/EVALUATION.md). These tests pin the outcome to the stream, since the
engine knew it all along and simply wasn't saying.
"""

from __future__ import annotations

import asyncio

import pytest

from portia.agent import ask, events

pytest.importorskip("claude_agent_sdk", reason="needs the `agent` extra")

from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny  # noqa: E402


def _callback(*, allow: bool, emitted: list[events.Event], answers: dict | None = None):
    async def answer(questions):
        return answers or {}

    async def confirm(tool_name, tool_input):
        return allow

    return ask.build_can_use_tool(answer=answer, confirm=confirm, emit=emitted.append)


def test_an_allowed_write_says_so_in_the_stream():
    emitted: list[events.Event] = []
    decide = _callback(allow=True, emitted=emitted)

    result = asyncio.run(decide("mcp__portia__record_step", {"step": {"op": "join"}}, None))

    assert isinstance(result, PermissionResultAllow)
    assert [e.kind for e in emitted] == [events.APPROVAL, events.APPROVAL_RESULT]
    assert emitted[-1].data == {"name": "mcp__portia__record_step", "allowed": True}


def test_a_refused_write_says_so_too():
    emitted: list[events.Event] = []
    decide = _callback(allow=False, emitted=emitted)

    result = asyncio.run(decide("mcp__portia__record_step", {"step": {}}, None))

    assert isinstance(result, PermissionResultDeny)
    assert emitted[-1].kind == events.APPROVAL_RESULT
    assert emitted[-1].data["allowed"] is False


def test_a_question_still_emits_only_the_ask_and_the_answer():
    """`AskUserQuestion` is not a write and never had an approval to resolve."""
    emitted: list[events.Event] = []
    decide = _callback(allow=True, emitted=emitted, answers={"which grain?": "city-date"})

    result = asyncio.run(decide(ask.ASK_TOOL, {"questions": [{"question": "which grain?"}]}, None))

    assert isinstance(result, PermissionResultAllow)
    assert [e.kind for e in emitted] == [events.QUESTION, events.ANSWER]

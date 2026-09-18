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
    assert emitted[-1].data["name"] == "mcp__portia__record_step"
    assert emitted[-1].data["allowed"] is True


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


# --- how long the human took ------------------------------------------------
#
# The stamps cannot answer this: both events are emitted into a list
# `session.Conversation` drains from the outer loop, so `runlog.write` gives
# them one moment between them. Every approval in the 2026-08-31 runs reads as
# 0.0 s apart. These pin the fix at the only place that knows.


def test_an_approval_carries_how_long_the_human_took():
    emitted: list[events.Event] = []

    async def answer(questions):
        return {}

    async def confirm(tool_name, tool_input):
        await asyncio.sleep(0.05)
        return True

    decide = ask.build_can_use_tool(answer=answer, confirm=confirm, emit=emitted.append)
    asyncio.run(decide("mcp__portia__record_step", {"step": {}}, None))

    assert emitted[-1].data["waited"] >= 0.0
    # Rounded to a tenth, because nobody reads a human's hesitation to the
    # microsecond and a float tail makes the log noisier to diff.
    assert emitted[-1].data["waited"] == round(emitted[-1].data["waited"], 1)


def test_a_refused_write_is_timed_too():
    """A refusal is a decision that took reading, not a fast path."""
    emitted: list[events.Event] = []
    decide = _callback(allow=False, emitted=emitted)
    asyncio.run(decide("mcp__portia__record_step", {"step": {}}, None))

    assert "waited" in emitted[-1].data


def test_an_answered_question_carries_the_wait():
    """The 317 s case: a question the log could not say was slow."""
    emitted: list[events.Event] = []
    decide = _callback(allow=True, emitted=emitted, answers={"which grain?": "city-date"})
    asyncio.run(decide(ask.ASK_TOOL, {"questions": [{"question": "which grain?"}]}, None))

    assert emitted[-1].kind == events.ANSWER
    assert emitted[-1].data["waited"] >= 0.0


def test_an_event_built_without_a_wait_keeps_its_old_shape():
    """Every log written before this existed, and every test that predates it."""
    assert events.approval_result_event("x", True).data == {"name": "x", "allowed": True}
    assert events.answer_event({"q": "a"}).data == {"answers": {"q": "a"}}


# --- writes that do not stop --------------------------------------------------
#
# 26 of the 39 approvals in the 2026-08-31 runs were catalog prose. Which writes
# stop is the surface's call; what this module owes is that a write which does
# not stop is still emitted, and never reads as a decision somebody made.


def _auto(names, *, emitted, allow=True):
    async def answer(questions):
        return {}

    async def confirm(tool_name, tool_input):
        return allow

    return ask.build_can_use_tool(
        answer=answer,
        confirm=confirm,
        emit=emitted.append,
        auto_allow=lambda name: name in names,
    )


def test_an_auto_allowed_write_never_reaches_confirm():
    asked: list[str] = []
    emitted: list[events.Event] = []

    async def answer(questions):
        return {}

    async def confirm(tool_name, tool_input):
        asked.append(tool_name)
        return True

    decide = ask.build_can_use_tool(
        answer=answer,
        confirm=confirm,
        emit=emitted.append,
        auto_allow=lambda name: name == "set_interpretation",
    )
    result = asyncio.run(decide("set_interpretation", {"source": "golden"}, None))

    assert isinstance(result, PermissionResultAllow)
    assert asked == []


def test_an_auto_allowed_write_is_still_emitted_and_marked():
    """The write happened. A stream that dropped it would be hiding a durable
    change, and one that drew it as `allowed` would report a decision nobody
    made — `runlog.summary` counts the two apart."""
    emitted: list[events.Event] = []
    decide = _auto({"set_interpretation"}, emitted=emitted)
    asyncio.run(decide("set_interpretation", {"source": "golden"}, None))

    assert [e.kind for e in emitted] == [events.APPROVAL, events.APPROVAL_RESULT]
    assert emitted[0].data["auto"] is True
    assert emitted[1].data == {"name": "set_interpretation", "allowed": True, "auto": True}


def test_an_auto_allowed_write_carries_no_wait():
    """Nothing waited. A zero would say the human answered instantly, which is
    the lie `waited` exists to stop telling."""
    emitted: list[events.Event] = []
    decide = _auto({"set_interpretation"}, emitted=emitted)
    asyncio.run(decide("set_interpretation", {}, None))

    assert "waited" not in emitted[-1].data


def test_a_tool_outside_the_set_still_stops():
    """`record_step` runs the op and then writes the step, so its confirmation
    is the only look anyone gets at a step before it exists."""
    asked: list[str] = []
    emitted: list[events.Event] = []

    async def answer(questions):
        return {}

    async def confirm(tool_name, tool_input):
        asked.append(tool_name)
        return True

    decide = ask.build_can_use_tool(
        answer=answer,
        confirm=confirm,
        emit=emitted.append,
        auto_allow=lambda name: name == "set_interpretation",
    )
    asyncio.run(decide("record_step", {"spec_path": "specs/x.yaml"}, None))

    assert asked == ["record_step"]
    assert "auto" not in emitted[0].data
    assert "waited" in emitted[-1].data


def test_without_auto_allow_every_write_still_stops():
    """The default, and what the CLI and every indexing job still do."""
    asked: list[str] = []
    emitted: list[events.Event] = []

    async def answer(questions):
        return {}

    async def confirm(tool_name, tool_input):
        asked.append(tool_name)
        return True

    decide = ask.build_can_use_tool(answer=answer, confirm=confirm, emit=emitted.append)
    asyncio.run(decide("set_interpretation", {}, None))

    assert asked == ["set_interpretation"]
    assert "auto" not in emitted[0].data


def test_a_question_stops_even_when_every_write_is_auto_allowed():
    """Autopilot's load-bearing property (`docs/CONVERSATION.md` §14.3).

    The app's autopilot is `auto_allow` returning True for everything, so this
    is what that looks like from here. `AskUserQuestion` is not a write and never
    reached the confirmation path — `ask.py` branches on it first — so the
    copilot keeps its ability to ask **by construction** rather than because
    somebody remembered to exclude it. A mode that silenced questions would not
    be autopilot; it would be the copilot guessing, which is `PLAN.md`'s first
    non-negotiable.
    """
    asked: list[list[dict]] = []
    emitted: list[events.Event] = []

    async def answer(questions):
        asked.append(questions)
        return {"which key?": "id"}

    async def confirm(tool_name, tool_input):
        raise AssertionError("a question must never reach the write path")

    can_use_tool = ask.build_can_use_tool(
        answer=answer,
        confirm=confirm,
        emit=emitted.append,
        auto_allow=lambda name: True,  # autopilot: every write proceeds
    )
    questions = [{"question": "which key?", "options": ["id", "code"]}]
    result = asyncio.run(can_use_tool(ask.ASK_TOOL, {"questions": questions}, None))

    assert asked == [questions]
    assert result.updated_input["answers"] == {"which key?": "id"}
    assert [e.kind for e in emitted] == [events.QUESTION, events.ANSWER]


def test_an_autopilot_write_is_still_emitted_logged_and_marked_auto():
    """Nothing is silent. `runlog.summary` counts approvals, and a write that
    read as one nobody gave would make that count a lie."""
    emitted: list[events.Event] = []
    # Autopilot is `auto_allow` saying yes to everything, `record_step` included.
    can_use_tool = _auto({"record_step"}, emitted=emitted)

    asyncio.run(can_use_tool("record_step", {"spec_path": "x"}, None))

    assert [e.kind for e in emitted] == [events.APPROVAL, events.APPROVAL_RESULT]
    assert all(e.data["auto"] is True for e in emitted)
    # No `waited`, because nothing waited — a zero would say a human answered
    # instantly, which is the lie the field exists to stop telling.
    assert "waited" not in emitted[1].data

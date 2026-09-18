"""The event stream — the seam every renderer sits on.

`TOOL_RESULT` was the gap: `from_message` handled the assistant's messages and
the final result but never the message carrying tool results, so a transcript
recorded that `join_findings` was called and never what it returned. Half a
transcript, and the half with the evidence in it (docs/EVALUATION.md → the run
log; docs/VISION.md → "tool results expandable inline").
"""

from __future__ import annotations

import pytest

from portia.agent import events

pytest.importorskip("claude_agent_sdk", reason="needs the `agent` extra")

from claude_agent_sdk import (  # noqa: E402
    AssistantMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)


def test_a_tool_result_becomes_an_event():
    message = UserMessage(content=[ToolResultBlock(tool_use_id="t1", content='{"n_rows": 14}')])
    (event,) = list(events.from_message(message))
    assert event.kind == events.TOOL_RESULT
    assert event.data == {"id": "t1", "text": '{"n_rows": 14}', "is_error": False}


def test_a_failed_tool_result_says_so():
    message = UserMessage(content=[ToolResultBlock(tool_use_id="t2", content="no", is_error=True)])
    (event,) = list(events.from_message(message))
    assert event.data["is_error"] is True


def test_block_shaped_content_is_flattened_to_one_string():
    """The SDK may hand back content blocks; every renderer wants one string."""
    message = UserMessage(
        content=[
            ToolResultBlock(
                tool_use_id="t3",
                content=[{"type": "text", "text": "first"}, {"type": "text", "text": "second"}],
            )
        ]
    )
    (event,) = list(events.from_message(message))
    assert event.data["text"] == "first\nsecond"


def test_empty_content_is_an_empty_string_not_a_crash():
    message = UserMessage(content=[ToolResultBlock(tool_use_id="t4", content=None)])
    (event,) = list(events.from_message(message))
    assert event.data["text"] == ""


def test_a_plain_text_user_message_yields_nothing():
    """Only tool results are interesting here; the human's own turn isn't an event."""
    assert list(events.from_message(UserMessage(content="merge these two"))) == []


def test_the_assistant_message_kinds_are_unchanged():
    message = AssistantMessage(
        content=[TextBlock(text="looking"), ToolUseBlock(id="t5", name="profile_source", input={})],
        model="claude-haiku-4-5",
    )
    assert [e.kind for e in events.from_message(message)] == [events.TEXT, events.TOOL_CALL]

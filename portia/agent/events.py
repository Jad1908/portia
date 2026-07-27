"""The event stream — what the copilot is doing, as plain data.

`TECH_STACK.md` promises the engine emits a clean stream of questions, insights
and decisions, and that the UI *sits on top of it* and stays swappable. This is
that seam: SDK message objects in, small JSON-serializable events out. The CLI
renders them to a terminal today; the three-panel app (docs/VISION.md) will
render the same stream without the engine knowing the difference.

Keep events dumb. No formatting, no colour, no decisions about what's worth
showing — that's the renderer's job, at the edge.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

# Event kinds. Named here so renderers can switch exhaustively.
TEXT = "text"  # the copilot talking
THINKING = "thinking"  # reasoning, when the model surfaces it
TOOL_CALL = "tool_call"  # a check or op was invoked
TOOL_RESULT = "tool_result"  # the evidence it got back
QUESTION = "question"  # a decision surfaced to the human
ANSWER = "answer"  # what the human said back
APPROVAL = "approval"  # a write is waiting on a yes/no
RESULT = "result"  # the turn ended
ERROR = "error"


@dataclass(frozen=True)
class Event:
    kind: str
    data: dict[str, Any] = field(default_factory=dict)


def from_message(message: Any) -> Iterator[Event]:
    """Translate one SDK message into zero or more portia events.

    Imports of the SDK's message classes stay local so this module can be
    imported (and its constants used) without the ``agent`` extra installed.
    """
    from claude_agent_sdk import (
        AssistantMessage,
        ResultMessage,
        TextBlock,
        ThinkingBlock,
        ToolResultBlock,
        ToolUseBlock,
        UserMessage,
    )

    if isinstance(message, AssistantMessage):
        for block in message.content:
            if isinstance(block, TextBlock):
                text = block.text.strip()
                if text:
                    yield Event(TEXT, {"text": text})
            elif isinstance(block, ThinkingBlock):
                yield Event(THINKING, {"text": block.thinking})
            elif isinstance(block, ToolUseBlock):
                yield Event(
                    TOOL_CALL,
                    {"name": block.name, "input": block.input, "id": block.id},
                )

    elif isinstance(message, UserMessage):
        # What a check handed back. Without this a transcript records that
        # `join_findings` was called and never what it returned — half a
        # transcript, and the half that carries the evidence (docs/EVALUATION.md
        # → the run log; docs/VISION.md → "tool results expandable inline").
        for block in message.content if isinstance(message.content, list) else []:
            if isinstance(block, ToolResultBlock):
                yield Event(
                    TOOL_RESULT,
                    {
                        "id": block.tool_use_id,
                        "text": tool_result_text(block.content),
                        "is_error": bool(block.is_error),
                    },
                )

    elif isinstance(message, ResultMessage):
        yield Event(
            RESULT,
            {
                "subtype": message.subtype,
                "text": getattr(message, "result", None),
                "usage": getattr(message, "usage", None),
                "cost_usd": getattr(message, "total_cost_usd", None),
            },
        )


def tool_result_text(content: Any) -> str:
    """A tool result's content as one string, whatever shape the SDK used.

    ``ToolResultBlock.content`` is a string, or a list of content dicts, or
    ``None``. Flattening happens here rather than in each renderer so a terminal,
    a log and a panel all read the same text.
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    parts = [str(part.get("text", "")) for part in content if isinstance(part, dict)]
    return "\n".join(p for p in parts if p)


def question_event(questions: list[dict]) -> Event:
    """A decision the copilot wants the human to make.

    Shape mirrors the SDK's ``AskUserQuestion`` input, so the future UI can
    render options as-is: ``[{question, header, options: [{label, description}],
    multiSelect}]``.
    """
    return Event(QUESTION, {"questions": questions})


def answer_event(answers: dict[str, Any]) -> Event:
    """The human's reply — question text -> chosen label(s) or free text."""
    return Event(ANSWER, {"answers": answers})

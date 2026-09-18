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
PROMPT = "prompt"  # the human talking — what opened this exchange
TEXT = "text"  # the copilot talking
THINKING = "thinking"  # reasoning, when the model surfaces it
TOOL_CALL = "tool_call"  # a check or op was invoked
TOOL_RESULT = "tool_result"  # the evidence it got back
QUESTION = "question"  # a decision surfaced to the human
ANSWER = "answer"  # what the human said back
APPROVAL = "approval"  # a write is waiting on a yes/no
APPROVAL_RESULT = "approval_result"  # …and what the human said
RESULT = "result"  # the turn ended
ERROR = "error"

#: The in-process MCP server namespaces every portia tool. That prefix is an
#: artifact of how the SDK is wired, not part of the name anyone means, so
#: stripping it is knowledge about the event's own data rather than a rendering
#: choice — which is why it lives here and not in five renderers, all of which
#: had grown their own copy of the same `.replace`.
TOOL_PREFIX = "mcp__portia__"


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
                # The SDK's id for the session this exchange belonged to. Carried
                # so a chat can record which one it was (`CONVERSATION.md` §4) —
                # it costs one key, and it is what would make reopening a chat an
                # addition rather than a rewrite. Nothing reads it yet.
                "session_id": getattr(message, "session_id", None),
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


def prompt_event(
    text: str, *, model: str, effort: str | None = None, provider: str | None = None
) -> Event:
    """The human's message, and what it is about to be answered on.

    **Not an SDK message** — neither are `QUESTION`, `ANSWER` or `APPROVAL`,
    which `ask.py` emits. This is the same shape: a thing that happened in the
    loop, normalized into the one stream every surface reads.

    It carries the model and effort because a **chat** can span several of them
    (`docs/CONVERSATION.md` §5). Those used to live in the log's header, which
    only worked while a file held exactly one exchange; a header field that can
    change mid-file is a lie.

    ``provider`` is where the model came from (`agent/providers/`), carried for
    the same reason: a model name on its own does not say which server answered
    it. A log from before providers existed has none, and a reader takes that
    as the Anthropic default rather than as unknown, because it was.
    """
    data: dict[str, Any] = {"text": text, "model": model, "effort": effort}
    if provider is not None:
        data["provider"] = provider
    return Event(PROMPT, data)


def question_event(questions: list[dict]) -> Event:
    """A decision the copilot wants the human to make.

    Shape mirrors the SDK's ``AskUserQuestion`` input, so the future UI can
    render options as-is: ``[{question, header, options: [{label, description}],
    multiSelect}]``.
    """
    return Event(QUESTION, {"questions": questions})


def answer_event(answers: dict[str, Any], waited: float | None = None) -> Event:
    """The human's reply — question text -> chosen label(s) or free text.

    ``waited`` is how long the loop was parked on the human, in seconds. See
    `approval_result_event` for why it is carried here rather than derived from
    the log's stamps; the case that made it necessary is the 2026-08-31 chat,
    where a question sat for 317 s and the log could not say so.
    """
    return _waited(Event(ANSWER, {"answers": answers}), waited)


def approval_event(tool_name: str, tool_input: dict, *, auto: bool = False) -> Event:
    """A write, on its way to being allowed or refused.

    ``auto`` says the loop **did not stop** for it — the surface had been told
    in advance that this tool's writes need no confirmation (`ask.py`). It is
    the same event either way, because the write happens either way and a
    transcript that showed only the ones somebody was asked about would be
    hiding durable changes. What it must not do is read as a decision: `summary`
    counts these apart, and the panel says *allowed automatically*.
    """
    data: dict[str, Any] = {"name": tool_name, "input": tool_input}
    return Event(APPROVAL, {**data, "auto": True} if auto else data)


def approval_result_event(
    tool_name: str, allowed: bool, waited: float | None = None, *, auto: bool = False
) -> Event:
    """Whether the human let a write through, and how long they took.

    `APPROVAL` says a write stopped for a yes/no; on its own it never says which
    was given, so a stream carrying only that can't answer *"how many writes
    were refused"* — one of the few things a run log can measure without an
    answer key (docs/EVALUATION.md → the run log). The engine already knows the
    answer here; it just wasn't telling anyone.

    **``waited`` is here because the log's own stamps cannot supply it.** Both
    this event and the `APPROVAL` before it are emitted from inside
    `can_use_tool` while the message stream is paused, so `session.Conversation`
    drains them together *after* the human has answered and `runlog.write`
    stamps both at that one moment. Every approval in the 2026-08-31 runs
    therefore reads as **0.0 s apart** — 39 of them — and the only proxy anyone
    had was `tool_call → approval`, which measures the drain rather than the
    wait. `ask.py` times its own await instead, which is the one place that
    genuinely knows.

    That is not `at` moving into `data` (`runlog.write`, and `CLAUDE.md` →
    `runlog.py`). ``at`` stays this module's non-business: *when portia wrote the
    record*. How long the loop sat waiting is a fact about what the loop did,
    the same kind of fact as ``allowed``.

    ``None`` means nobody measured — every log written before this existed —
    and never zero, for `summary`'s ``wall_seconds`` reason: a made-up zero
    reads as instant.

    ``auto`` marks a write the loop never stopped for, and carries no ``waited``
    at all. The two are different facts and neither implies the other: a human
    can answer in under a tenth of a second, and an automatic write did not
    answer.
    """
    result = Event(APPROVAL_RESULT, {"name": tool_name, "allowed": bool(allowed)})
    if auto:
        # No `waited` on an automatic one, and `ask.py` passes none: nothing
        # waited, and a measured 0.0 is a different claim from "not asked".
        return Event(result.kind, {**result.data, "auto": True})
    return _waited(result, waited)


def _waited(event: Event, waited: float | None) -> Event:
    """Attach ``waited`` when there is one, so old readers see the old shape."""
    if waited is None:
        return event
    return Event(event.kind, {**event.data, "waited": round(float(waited), 1)})


def tool_label(name: str) -> str:
    """A tool name as a human means it — `mcp__portia__profile_source` → `profile_source`."""
    return name.replace(TOOL_PREFIX, "")

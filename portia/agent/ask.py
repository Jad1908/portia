"""Surfacing a decision to the human — the heart of the product.

`PLAN.md`'s non-negotiable: when something is ambiguous the copilot **asks**
rather than guessing or hard-stopping. The Agent SDK already gives us the
mechanism — the built-in ``AskUserQuestion`` tool routes through the
``can_use_tool`` callback with structured questions and options — so we don't
invent a protocol, we just intercept it and hand the payload to whatever
surface the human is using.

The same callback is where writes get confirmed. A read-only check runs freely;
anything that *changes* a durable artifact stops here first, so a spec or a
catalog entry is never mutated behind the user's back.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from portia.agent import events

#: Given the SDK's question payload, return ``{question text: answer}``.
AnswerFn = Callable[[list[dict]], Awaitable[dict[str, Any]]]
#: Given a tool name and its input, return True to let the write happen.
ConfirmFn = Callable[[str, dict], Awaitable[bool]]
Emit = Callable[[events.Event], None]

ASK_TOOL = "AskUserQuestion"


def build_can_use_tool(
    *,
    answer: AnswerFn,
    confirm: ConfirmFn,
    emit: Emit,
) -> Callable[..., Awaitable[Any]]:
    """Build the ``can_use_tool`` callback for a session.

    The three collaborators are injected rather than hard-wired to stdin, so the
    CLI, a test, and the eventual NiceGUI panel can all drive the same loop.
    """
    from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny

    async def can_use_tool(tool_name: str, input_data: dict, context: Any) -> Any:
        if tool_name == ASK_TOOL:
            questions = input_data.get("questions", [])
            emit(events.question_event(questions))
            answers = await answer(questions)
            emit(events.answer_event(answers))
            # The SDK requires the original questions echoed back alongside the answers.
            return PermissionResultAllow(updated_input={"questions": questions, "answers": answers})

        emit(events.Event(events.APPROVAL, {"name": tool_name, "input": input_data}))
        allowed = await confirm(tool_name, input_data)
        emit(events.approval_result_event(tool_name, allowed))
        if allowed:
            return PermissionResultAllow(updated_input=input_data)
        return PermissionResultDeny(
            message=(
                "The user declined this write. Ask what they'd prefer instead of "
                "retrying the same call."
            )
        )

    return can_use_tool

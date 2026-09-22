"""Surfacing a decision to the human — the heart of the product.

`PLAN.md`'s non-negotiable: when something is ambiguous the copilot **asks**
rather than guessing or hard-stopping. On the Claude harness the Agent SDK
already gives us the mechanism — the built-in ``AskUserQuestion`` tool routes
through the ``can_use_tool`` callback with structured questions and options —
so we don't invent a protocol, we intercept it and hand the payload to whatever
surface the human is using. On the Codex harness there is no such callback:
the question is a portia tool, `tools.ask_user`, with the same ``questions``
shape, and its body waits on the same ``answer`` function (`docs/PROVIDERS.md`
§9.2). The two doors lead to :func:`ask_questions`, once.

The same callback is where writes get confirmed. A read-only check runs freely;
anything that *changes* a durable artifact stops here first, so a spec or a
catalog entry is never mutated behind the user's back. Codex asks the same
question through its own approval request, keyed off the ``readOnlyHint`` a
read tool carries, and `agent/codex.py` answers it with :func:`decide_write`,
so which writes stop and what the log records is one function on both routes.

**Which writes stop is the surface's call, not this module's** (``auto_allow``).
The read/write line is already drawn a layer down — `session.build_options`
passes `tools.READ_TOOLS` as ``allowed_tools``, so a rung never reaches here at
all, and every one of the 39 approvals in the 2026-08-31 runs was a write tool.
What that line cannot separate is the two kinds of write underneath it: 25 of
that indexing pass's 31 approvals were `set_interpretation` writing catalog
prose, while the chat's 8 were `record_step`, which **runs the op** and then
writes a spec step, and which the human spent 468 of 1123 seconds reading.
Which of those is worth stopping for is a preference, so it lives on the surface
that holds preferences; this module only asks whether to stop.

A write that does not stop is still **emitted, logged and drawn** — it changed a
durable artifact either way. It carries ``auto`` so nothing downstream reads it
as a decision somebody made: `runlog.summary` would otherwise report approvals
nobody gave.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from portia.agent import events

#: Given the SDK's question payload, return ``{question text: answer}``.
AnswerFn = Callable[[list[dict]], Awaitable[dict[str, Any]]]
#: Given a tool name and its input, return True to let the write happen.
ConfirmFn = Callable[[str, dict], Awaitable[bool]]
#: Given a tool name, return True to let the write happen **without asking**.
AutoAllowFn = Callable[[str], bool]
Emit = Callable[[events.Event], None]

ASK_TOOL = "AskUserQuestion"


async def ask_questions(questions: list[dict], *, answer: AnswerFn, emit: Emit) -> dict[str, Any]:
    """Put the copilot's questions to the human and time the wait.

    Emits the question, awaits the surface, emits the answer with ``waited``
    (`events.answer_event` says why the loop times its own wait). Both harnesses
    come through here, so a question is one event shape in every log.
    """
    emit(events.question_event(questions))
    asked_at = time.monotonic()
    answers = await answer(questions)
    emit(events.answer_event(answers, time.monotonic() - asked_at))
    return answers


async def decide_write(
    tool_name: str,
    input_data: dict,
    *,
    confirm: ConfirmFn,
    emit: Emit,
    auto_allow: AutoAllowFn | None = None,
) -> bool:
    """Whether a write may happen: the surface's standing answer, else the human's.

    ``auto_allow`` is optional and **defaults to stopping for every write**,
    which is what `cli/chat.py` and every indexing job still do. Only the app
    sets it, and only for the two catalog writes.
    """
    automatic = auto_allow is not None and auto_allow(tool_name)
    emit(events.approval_event(tool_name, input_data, auto=automatic))
    if automatic:
        # **No `waited`, because nothing waited.** A zero here would say the
        # human answered instantly, which is the lie `waited` was added to
        # stop telling (`events.approval_result_event`).
        emit(events.approval_result_event(tool_name, True, auto=True))
        return True

    # **The only place that can time a human.** Both events either side of
    # this await are emitted into a list `session.Conversation` drains from
    # the outer loop, so the log stamps them at one moment after the answer
    # is already in — all 39 approvals in the 2026-08-31 runs read as 0.0 s
    # apart. `monotonic` because this is a duration, not a time of day.
    # See `events.approval_result_event`.
    stopped_at = time.monotonic()
    allowed = await confirm(tool_name, input_data)
    emit(events.approval_result_event(tool_name, allowed, time.monotonic() - stopped_at))
    return bool(allowed)


def build_can_use_tool(
    *,
    answer: AnswerFn,
    confirm: ConfirmFn,
    emit: Emit,
    auto_allow: AutoAllowFn | None = None,
) -> Callable[..., Awaitable[Any]]:
    """Build the ``can_use_tool`` callback for a Claude session.

    The collaborators are injected rather than hard-wired to stdin, so the CLI,
    a test, and the NiceGUI panel can all drive the same loop.
    """
    from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny

    async def can_use_tool(tool_name: str, input_data: dict, context: Any) -> Any:
        if tool_name == ASK_TOOL:
            questions = input_data.get("questions", [])
            answers = await ask_questions(questions, answer=answer, emit=emit)
            # The SDK requires the original questions echoed back alongside the answers.
            return PermissionResultAllow(updated_input={"questions": questions, "answers": answers})

        allowed = await decide_write(
            tool_name, input_data, confirm=confirm, emit=emit, auto_allow=auto_allow
        )
        if allowed:
            return PermissionResultAllow(updated_input=input_data)
        return PermissionResultDeny(
            message=(
                "The user declined this write. Ask what they'd prefer instead of "
                "retrying the same call."
            )
        )

    return can_use_tool


# --- the Codex harness's door -----------------------------------------------


@dataclass
class Asker:
    """Where `tools.ask_user`'s body sends a question: one chat's ``answer`` and ``emit``.

    Installed for the length of an exchange by `agent/codex.py`, the way
    `tools.stopping` installs the cancel scope: the tool server is built once
    per conversation, a tool body has no argument saying which chat it is in,
    and the process runs one exchange at a time (`CONVERSATION.md` §7).
    """

    answer: AnswerFn
    emit: Emit


_asker: Asker | None = None


def install_asker(asker: Asker | None) -> Asker | None:
    """Set who answers `ask_user` now, and hand back who did before."""
    global _asker
    previous, _asker = _asker, asker
    return previous


async def ask_now(questions: list[dict]) -> dict[str, Any]:
    """`tools.ask_user`'s body: the installed chat's question path, or a plain refusal."""
    if _asker is None:
        raise RuntimeError("no chat is listening for questions")
    return await ask_questions(questions, answer=_asker.answer, emit=_asker.emit)

"""Review before you reply — as a rule of the loop, not a paragraph.

`docs/FINDINGS.md` §5.3. Every chat in the sandbox asked the data something and
none reviewed what it asked, with the instruction sitting in two prompt files
and `copilot.md`. `agent/curation.py` is the hold; these pin when it holds and,
just as much, when it lets go.
"""

from __future__ import annotations

import asyncio

import pytest

from portia.agent import curation
from portia.agent.events import TOOL_PREFIX


def test_a_fresh_exchange_is_not_held():
    assert curation.Curation().hold() is False


def test_asking_the_data_holds_the_reply_once():
    cur = curation.Curation()
    cur.saw_tool(f"{TOOL_PREFIX}query_data")
    assert cur.hold() is True
    assert cur.hold() is False, "once per exchange, or the loop argues with a judgment"


def test_a_chart_is_a_question_too():
    cur = curation.Curation()
    cur.saw_tool(f"{TOOL_PREFIX}plot_data")
    assert cur.hold() is True


def test_reviewing_clears_the_hold_whether_or_not_anything_is_kept():
    cur = curation.Curation()
    cur.saw_tool(f"{TOOL_PREFIX}query_data")
    cur.saw_tool(f"{TOOL_PREFIX}review_queries")
    assert cur.hold() is False


def test_a_question_after_the_review_holds_again():
    cur = curation.Curation()
    cur.saw_tool(f"{TOOL_PREFIX}query_data")
    cur.saw_tool(f"{TOOL_PREFIX}review_queries")
    cur.saw_tool(f"{TOOL_PREFIX}query_data")
    assert cur.hold() is True


def test_reading_tools_do_not_hold():
    cur = curation.Curation()
    for tool in ("describe_source", "profile_source", "join_findings", "record_step"):
        cur.saw_tool(f"{TOOL_PREFIX}{tool}")
    assert cur.hold() is False


def test_a_new_message_starts_clean():
    cur = curation.Curation()
    cur.saw_tool(f"{TOOL_PREFIX}query_data")
    cur.start_exchange()
    assert cur.hold() is False


# --- wired into the session --------------------------------------------------


def test_the_session_registers_both_hooks_and_the_stop_reads_a_prompt_file():
    pytest.importorskip("claude_agent_sdk", reason="needs the `agent` extra")
    from portia.agent import prompts, session

    cur = curation.Curation()
    options = session.build_options(curator=cur)
    hooks = options.hooks
    assert set(hooks) == {"PostToolUse", "Stop"}
    saw = hooks["PostToolUse"][0].hooks[0]
    stop = hooks["Stop"][0].hooks[0]

    async def go():
        assert await stop({"hook_event_name": "Stop"}, None, {}) == {}
        await saw({"tool_name": f"{TOOL_PREFIX}query_data"}, "t1", {})
        held = await stop({"hook_event_name": "Stop"}, None, {})
        assert held["decision"] == "block"
        assert held["reason"] == prompts.error("review_before_reply")
        assert await stop({"hook_event_name": "Stop"}, None, {}) == {}

    asyncio.run(go())


def test_without_a_curator_there_are_no_hooks():
    pytest.importorskip("claude_agent_sdk", reason="needs the `agent` extra")
    from portia.agent import session

    assert session.build_options().hooks is None

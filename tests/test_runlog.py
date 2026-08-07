"""The run log — the durable half of a copilot turn.

The defects worth guarding here are the ones that already cost findings in
`docs/EVALUATION.md`: a transcript that lost its tail to a `^C`, and two runs
that got conflated while being written up. So the tests care about the tail
surviving, about two turns in the same second staying two files, and about the
summary counting rather than judging.
"""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from portia import runlog
from portia.agent import events


def _turn(log: runlog.Log, *evs: events.Event) -> None:
    for event in evs:
        log.event(event)


def _chat(tmp_path, *evs, prompt="build me a table", model="claude-haiku-4-5", effort="low", **kw):
    """Open a chat and write one exchange into it — the common shape."""
    log = runlog.start(tmp_path, **kw)
    log.event(events.prompt_event(prompt, model=model, effort=effort))
    _turn(log, *evs)
    return log


def _call(tool: str, **inp) -> events.Event:
    return events.Event(events.TOOL_CALL, {"name": f"mcp__portia__{tool}", "input": inp})


# --- writing -----------------------------------------------------------------


def test_a_chat_is_one_file_opened_by_a_header(tmp_path):
    _chat(tmp_path, events.Event(events.TEXT, {"text": "on it"}))

    (path,) = runlog.logs_in(tmp_path)
    first, second, third = path.read_text().splitlines()
    assert json.loads(first)["kind"] == runlog.HEADER
    assert json.loads(second)["kind"] == events.PROMPT
    assert json.loads(second)["data"]["text"] == "build me a table"
    assert json.loads(third) == {"kind": "text", "data": {"text": "on it"}}


def test_the_header_holds_only_what_is_true_of_the_whole_chat(tmp_path):
    """§5 — the prompt, model and effort moved onto each exchange, because a chat
    can span several models and a header field that changes mid-file is a lie."""
    log = runlog.start(tmp_path, cwd=tmp_path)
    header = runlog.read(log.path).header

    assert set(header) == {"started", "kind", "cwd", "portia_sha"}


def test_what_makes_two_chats_comparable_is_recorded(tmp_path):
    """Run 6 is only comparable to Run 5 because they differ in model and effort
    and nothing else — a fact that until now survived only in prose."""
    log = _chat(tmp_path, prompt="p", model="m", effort="high", cwd=tmp_path)
    logged = runlog.read(log.path)
    facts = runlog.summary(logged)

    assert (facts["model"], facts["effort"]) == ("m", "high")
    assert logged.header["cwd"] == str(tmp_path.resolve())
    assert "started" in logged.header


def test_two_chats_started_in_the_same_second_are_two_logs(tmp_path):
    """`index` runs two jobs back to back. Appending one onto the other is
    exactly the conflation this module exists to end."""
    when = datetime(2026, 7, 29, 12, 0, 0)
    first = _chat(tmp_path, prompt="a", model="m", when=when)
    second = _chat(tmp_path, prompt="b", model="m", when=when)

    assert first.path != second.path
    assert len(runlog.logs_in(tmp_path)) == 2
    assert runlog.summary(runlog.read(second.path))["prompt"] == "b"


def test_each_event_is_on_disk_before_the_next_one_happens(tmp_path):
    """The `^C` case: whatever the turn got to is readable, with no clean close."""
    log = runlog.start(tmp_path)
    _turn(log, _call("profile_source", name="otb"))

    assert len(runlog.read(log.path).events) == 1  # nothing flushed, nothing closed


def test_a_payload_the_json_encoder_would_refuse_does_not_kill_the_turn(tmp_path):
    """A log line that raises mid-turn loses the transcript it exists to keep."""
    log = runlog.start(tmp_path)
    _turn(log, events.Event(events.RESULT, {"usage": {"at": datetime(2026, 7, 29)}}))

    (event,) = runlog.read(log.path).events
    assert isinstance(event.data["usage"]["at"], str)


# --- reading back ------------------------------------------------------------


def test_a_truncated_tail_does_not_lose_the_rest(tmp_path):
    log = _chat(tmp_path, prompt="p", model="m")
    _turn(log, events.Event(events.TEXT, {"text": "kept"}))
    with log.path.open("a") as fh:
        fh.write('{"kind": "text", "data": {"tex')  # died mid-write

    run = runlog.read(log.path)
    assert [e.data["text"] for e in run.events if e.kind == events.TEXT] == ["kept"]
    assert runlog.summary(run)["prompt"] == "p"


def test_logs_are_listed_newest_first(tmp_path):
    old = _chat(tmp_path, prompt="a", model="m", when=datetime(2026, 7, 1, 9, 0))
    new = _chat(tmp_path, prompt="b", model="m", when=datetime(2026, 7, 29, 9, 0))
    assert runlog.logs_in(tmp_path) == [new.path, old.path]


def test_a_run_is_found_by_prefix(tmp_path):
    log = _chat(tmp_path, prompt="a", model="m", when=datetime(2026, 7, 29, 9, 0))
    assert runlog.find("2026-07-29", tmp_path) == log.path
    assert runlog.find("nope", tmp_path) is None


def test_no_log_directory_is_an_empty_list_not_a_crash(tmp_path):
    assert runlog.logs_in(tmp_path / "nothing-here") == []


# --- what it can answer without labels ---------------------------------------


def test_the_tool_sequence_is_kept_in_order_and_says_what_each_call_was_about(tmp_path):
    """ "Which rungs were pulled, in what order, and about what."

    The order *is* the finding, so it is kept whole rather than reduced to a set.
    The **subject** was added when the graph arrived: `graph_lookup` is a router,
    and a log recording only that it was called cannot say whether it routed
    anywhere.
    """
    log = _chat(tmp_path, prompt="p", model="m")
    _turn(
        log,
        _call("graph_lookup", table="orders"),
        _call("graph_lookup", table="orders", column="country_name"),
        _call("describe_source", source="customers"),
    )

    summary = runlog.summary(runlog.read(log.path))
    assert summary["sequence"] == [
        "graph_lookup(orders)",
        "graph_lookup(orders.country_name)",
        "describe_source(customers)",
    ]
    # Counting stays on the bare names — the mix is a question about tools.
    assert summary["by_tool"] == {"graph_lookup": 2, "describe_source": 1}
    assert summary["tools"] == 3


def test_a_call_with_a_list_argument_is_summarised_by_how_many(tmp_path):
    """`measure_overlaps` takes pairs; how many it asked for is the fact."""
    log = _chat(tmp_path, prompt="p", model="m")
    _turn(log, _call("measure_overlaps", pairs=[{"left": "a"}, {"left": "b"}]))
    assert runlog.summary(runlog.read(log.path))["sequence"] == ["measure_overlaps(2 pairs)"]


def test_a_call_with_no_arguments_is_just_its_name(tmp_path):
    log = _chat(tmp_path, prompt="p", model="m")
    _turn(log, _call("get_context"))
    assert runlog.summary(runlog.read(log.path))["sequence"] == ["get_context"]


def test_a_long_argument_is_clipped_rather_than_filling_the_line(tmp_path):
    """Thirty calls have to still read as one line."""
    log = _chat(tmp_path, prompt="p", model="m")
    _turn(log, _call("set_group", name="g", context="x" * 200))
    entry = runlog.summary(runlog.read(log.path))["sequence"][0]
    assert len(entry) <= len("set_group()") + runlog.SUBJECT_CHARS


def test_refused_writes_are_counted_separately_from_allowed_ones(tmp_path):
    log = _chat(tmp_path, prompt="p", model="m")
    _turn(
        log,
        events.approval_result_event("mcp__portia__record_step", True),
        events.approval_result_event("mcp__portia__record_step", False),
    )

    summary = runlog.summary(runlog.read(log.path))
    assert (summary["writes"], summary["approved"], summary["refused"]) == (2, 1, 1)


def test_asking_once_with_four_questions_is_not_asking_four_times(tmp_path):
    log = _chat(tmp_path, prompt="p", model="m")
    _turn(log, events.question_event([{"question": "a"}, {"question": "b"}]))

    summary = runlog.summary(runlog.read(log.path))
    assert summary["asked"] == 1
    assert summary["questions"] == 2


def test_cost_and_tokens_come_off_the_result_event(tmp_path):
    log = _chat(tmp_path, prompt="p", model="m")
    _turn(
        log,
        events.Event(
            events.RESULT,
            {
                "subtype": "success",
                "cost_usd": 0.0123,
                "usage": {"input_tokens": 900, "output_tokens": 120},
            },
        ),
    )

    summary = runlog.summary(runlog.read(log.path))
    assert summary["subtype"] == "success"
    assert summary["cost_usd"] == 0.0123
    assert (summary["input_tokens"], summary["output_tokens"]) == (900, 120)


def test_the_cached_input_is_counted_as_input(tmp_path):
    """Measured on the first real run through this module: the SDK reported 17
    input tokens for a turn that sent 14,651. The uncached field alone would
    have said a fat turn was a cheap one — in the artifact built to measure cost.
    """
    log = _chat(tmp_path, prompt="p", model="m")
    _turn(
        log,
        events.Event(
            events.RESULT,
            {
                "usage": {
                    "input_tokens": 17,
                    "cache_creation_input_tokens": 9271,
                    "cache_read_input_tokens": 5363,
                    "output_tokens": 377,
                }
            },
        ),
    )

    summary = runlog.summary(runlog.read(log.path))
    assert summary["input_tokens"] == 14651
    assert summary["cached_tokens"] == 5363
    assert summary["output_tokens"] == 377


def test_a_turn_that_never_finished_summarizes_anyway(tmp_path):
    """No `RESULT` event is the interrupted case — the reason for the log."""
    log = _chat(tmp_path, prompt="p", model="m")
    _turn(log, _call("profile_source"))

    summary = runlog.summary(runlog.read(log.path))
    assert summary["subtype"] is None
    assert summary["cost_usd"] is None
    assert summary["tools"] == 1


def test_the_summary_states_no_verdict(tmp_path):
    """`CLAUDE.md` → facts vs judgment. A run log that scored runs would break
    the line in the one place it would be least visible."""
    log = _chat(tmp_path, prompt="p", model="m")
    _turn(log, _call("profile_source"))

    summary = runlog.summary(runlog.read(log.path))
    forbidden = ("score", "rank", "quality", "grade", "severity", "verdict", "passed")
    assert not [key for key in summary if any(word in key for word in forbidden)]


# --- the two histories (docs/CONVERSATION.md §3) -----------------------------


def test_a_chat_and_an_indexing_land_in_different_folders(tmp_path):
    """The separation is on disk, not just in the pane that draws them. A job the
    app ran on your behalf is not a conversation you had."""
    chat = _chat(tmp_path, prompt="a", model="m", kind=runlog.CHAT)
    job = _chat(tmp_path, prompt="b", model="m", kind=runlog.INDEXING)

    assert chat.path.parent.name == "chats"
    assert job.path.parent.name == "indexing"
    assert runlog.logs_in(tmp_path, runlog.CHAT) == [chat.path]
    assert runlog.logs_in(tmp_path, runlog.INDEXING) == [job.path]
    assert set(runlog.logs_in(tmp_path)) == {chat.path, job.path}


def test_the_kind_is_in_the_header_and_the_summary(tmp_path):
    job = _chat(tmp_path, prompt="b", model="m", kind=runlog.INDEXING)
    transcript = runlog.read(job.path)
    assert transcript.header["kind"] == runlog.INDEXING
    assert transcript.kind == runlog.INDEXING
    assert runlog.summary(transcript)["kind"] == runlog.INDEXING


def test_an_unknown_kind_is_refused_rather_than_guessed(tmp_path):
    with pytest.raises(ValueError, match="unknown log kind"):
        _chat(tmp_path, prompt="a", model="m", kind="whatever")


def test_logs_written_before_the_rename_are_still_read(tmp_path):
    """`.portia/runs/` is read and never migrated — portia does not rewrite files
    in someone's project to suit its own rename (`CONVERSATION.md` §3)."""
    legacy = tmp_path / runlog.LEGACY_DIR
    legacy.mkdir(parents=True)
    path = legacy / "2026-07-29T16-32-57.jsonl"
    path.write_text('{"kind": "header", "data": {"prompt": "old", "model": "m"}}\n')

    assert runlog.logs_in(tmp_path) == [path]
    assert runlog.logs_in(tmp_path, runlog.CHAT) == [path]
    assert runlog.logs_in(tmp_path, runlog.INDEXING) == []
    # No kind was recorded before the split; it reads as a chat, which is what
    # nearly all of them were.
    assert runlog.read(path).kind == runlog.CHAT


def test_a_legacy_log_sorts_beside_the_new_ones(tmp_path):
    """One history, newest first, whichever folder a log happens to live in."""
    legacy = tmp_path / runlog.LEGACY_DIR
    legacy.mkdir(parents=True)
    old = legacy / "2026-07-01T09-00-00.jsonl"
    old.write_text('{"kind": "header", "data": {"prompt": "old", "model": "m"}}\n')
    new = _chat(tmp_path, prompt="new", model="m", when=datetime(2026, 7, 29, 9, 0))

    assert runlog.logs_in(tmp_path) == [new.path, old]


# --- a chat spans exchanges (docs/CONVERSATION.md §5) ------------------------


def _result(cost=0.01, usage=None, session_id="s1", subtype="success"):
    return events.Event(
        events.RESULT,
        {
            "subtype": subtype,
            "cost_usd": cost,
            "usage": usage or {},
            "session_id": session_id,
        },
    )


def test_one_file_holds_every_exchange(tmp_path):
    """The unit is the chat. Six files needing reassembly in the right order to
    mean anything is the shape `runlog`'s own storage argument rejects."""
    log = runlog.start(tmp_path)
    log.event(events.prompt_event("merge them", model="m", effort="low"))
    _turn(log, _call("join_findings", left="otb"), _result())
    log.event(events.prompt_event("actually make it an inner join", model="m", effort="low"))
    _turn(log, _call("record_step"), _result())

    assert len(runlog.logs_in(tmp_path)) == 1
    facts = runlog.summary(runlog.read(log.path))
    assert facts["exchanges"] == 2
    # What the chat opened with. A follow-up only means something beside the one
    # before it, so the first is the only one that stands alone in a list.
    assert facts["prompt"] == "merge them"
    assert facts["sequence"] == ["join_findings(otb)", "record_step"]


def test_totals_are_summed_across_exchanges_not_taken_from_the_last(tmp_path):
    """A chat that spent four cents over six messages spent four cents. Reading
    the last message's cost would understate every multi-exchange chat."""
    log = runlog.start(tmp_path)
    for n in (1, 2, 3):
        log.event(events.prompt_event(f"message {n}", model="m", effort="low"))
        log.event(_result(cost=0.02, usage={"input_tokens": 10, "output_tokens": 5}))

    facts = runlog.summary(runlog.read(log.path))
    assert facts["cost_usd"] == pytest.approx(0.06)
    assert facts["input_tokens"] == 30
    assert facts["output_tokens"] == 15


def test_a_model_change_mid_chat_is_reported_rather_than_flattened(tmp_path):
    """One line has room for one model, so `model` is the first; `models` is the
    honest whole answer. A header field could not have said either."""
    log = runlog.start(tmp_path)
    log.event(events.prompt_event("start cheap", model="claude-haiku-4-5", effort="low"))
    log.event(_result())
    log.event(events.prompt_event("now think harder", model="claude-opus-5", effort="low"))
    log.event(_result())

    facts = runlog.summary(runlog.read(log.path))
    assert facts["model"] == "claude-haiku-4-5"
    assert facts["models"] == ["claude-haiku-4-5", "claude-opus-5"]


def test_how_the_chat_ended_is_the_last_exchange_not_an_interrupted_one(tmp_path):
    """An interrupted message in the middle of a chat that carried on is not how
    the chat ended."""
    log = runlog.start(tmp_path)
    log.event(events.prompt_event("do a lot", model="m", effort="low"))
    log.event(_result(subtype="error_during_execution"))
    log.event(events.prompt_event("never mind, just this", model="m", effort="low"))
    log.event(_result(subtype="success"))

    assert runlog.summary(runlog.read(log.path))["subtype"] == "success"


def test_the_session_id_is_read_off_the_results(tmp_path):
    """§4 wanted this in the header; it cannot be, because the SDK hands it back
    with the result. Off the events is strictly better — a header could not show
    a session changing."""
    log = runlog.start(tmp_path)
    log.event(events.prompt_event("hello", model="m", effort="low"))
    log.event(_result(session_id="sess-abc"))

    assert runlog.summary(runlog.read(log.path))["session_id"] == "sess-abc"


def test_a_pre_rename_log_still_summarizes_off_its_header(tmp_path):
    """The promise in §3 — old logs are read, never migrated — has to survive
    §5 too. They have no PROMPT events at all."""
    legacy = tmp_path / runlog.LEGACY_DIR
    legacy.mkdir(parents=True)
    path = legacy / "2026-07-29T16-32-57.jsonl"
    path.write_text(
        '{"kind": "header", "data": {"prompt": "old goal", "model": "claude-haiku-4-5",'
        ' "effort": "low", "started": "2026-07-29T16:32:57"}}\n'
        '{"kind": "text", "data": {"text": "on it"}}\n'
    )

    facts = runlog.summary(runlog.read(path))
    assert facts["prompt"] == "old goal"
    assert facts["model"] == "claude-haiku-4-5"
    assert facts["effort"] == "low"
    assert facts["models"] == ["claude-haiku-4-5"]
    # It predates the idea of an exchange, and read as one thing that happened.
    assert facts["exchanges"] == 1

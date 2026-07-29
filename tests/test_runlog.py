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

from portia import runlog
from portia.agent import events


def _turn(log: runlog.Log, *evs: events.Event) -> None:
    for event in evs:
        log.event(event)


def _call(tool: str, **inp) -> events.Event:
    return events.Event(events.TOOL_CALL, {"name": f"mcp__portia__{tool}", "input": inp})


# --- writing -----------------------------------------------------------------


def test_a_turn_is_one_file_with_a_header_line(tmp_path):
    log = runlog.start(tmp_path, prompt="build me a table", model="claude-haiku-4-5", effort="low")
    _turn(log, events.Event(events.TEXT, {"text": "on it"}))

    (path,) = runlog.runs_in(tmp_path)
    first, second = path.read_text().splitlines()
    assert json.loads(first)["kind"] == runlog.HEADER
    assert json.loads(first)["data"]["model"] == "claude-haiku-4-5"
    assert json.loads(first)["data"]["prompt"] == "build me a table"
    assert json.loads(second) == {"kind": "text", "data": {"text": "on it"}}


def test_the_header_records_what_makes_two_runs_comparable(tmp_path):
    """Run 6 is only comparable to Run 5 because they differ in model and effort
    and nothing else — a fact that until now survived only in prose."""
    log = runlog.start(tmp_path, prompt="p", model="m", effort="high", cwd=tmp_path)
    header = runlog.read(log.path).header
    assert header["model"] == "m"
    assert header["effort"] == "high"
    assert header["cwd"] == str(tmp_path.resolve())
    assert "started" in header


def test_two_turns_in_the_same_second_are_two_logs(tmp_path):
    """`index` runs two turns back to back. Appending the second onto the first
    is exactly the run-conflation this module exists to end."""
    when = datetime(2026, 7, 29, 12, 0, 0)
    first = runlog.start(tmp_path, prompt="a", model="m", when=when)
    second = runlog.start(tmp_path, prompt="b", model="m", when=when)

    assert first.path != second.path
    assert len(runlog.runs_in(tmp_path)) == 2
    assert runlog.read(second.path).header["prompt"] == "b"


def test_each_event_is_on_disk_before_the_next_one_happens(tmp_path):
    """The `^C` case: whatever the turn got to is readable, with no clean close."""
    log = runlog.start(tmp_path, prompt="p", model="m")
    _turn(log, _call("profile_source", name="otb"))

    assert len(runlog.read(log.path).events) == 1  # nothing flushed, nothing closed


def test_a_payload_the_json_encoder_would_refuse_does_not_kill_the_turn(tmp_path):
    """A log line that raises mid-turn loses the transcript it exists to keep."""
    log = runlog.start(tmp_path, prompt="p", model="m")
    _turn(log, events.Event(events.RESULT, {"usage": {"at": datetime(2026, 7, 29)}}))

    (event,) = runlog.read(log.path).events
    assert isinstance(event.data["usage"]["at"], str)


# --- reading back ------------------------------------------------------------


def test_a_truncated_tail_does_not_lose_the_rest(tmp_path):
    log = runlog.start(tmp_path, prompt="p", model="m")
    _turn(log, events.Event(events.TEXT, {"text": "kept"}))
    with log.path.open("a") as fh:
        fh.write('{"kind": "text", "data": {"tex')  # died mid-write

    run = runlog.read(log.path)
    assert [e.data["text"] for e in run.events] == ["kept"]
    assert run.header["prompt"] == "p"


def test_runs_are_listed_newest_first(tmp_path):
    old = runlog.start(tmp_path, prompt="a", model="m", when=datetime(2026, 7, 1, 9, 0))
    new = runlog.start(tmp_path, prompt="b", model="m", when=datetime(2026, 7, 29, 9, 0))
    assert runlog.runs_in(tmp_path) == [new.path, old.path]


def test_a_run_is_found_by_prefix(tmp_path):
    log = runlog.start(tmp_path, prompt="a", model="m", when=datetime(2026, 7, 29, 9, 0))
    assert runlog.find("2026-07-29", tmp_path) == log.path
    assert runlog.find("nope", tmp_path) is None


def test_no_runs_directory_is_an_empty_list_not_a_crash(tmp_path):
    assert runlog.runs_in(tmp_path / "nothing-here") == []


# --- what it can answer without labels ---------------------------------------


def test_the_tool_sequence_is_kept_in_order(tmp_path):
    """ "Which rungs were pulled and in what order" — the order *is* the finding,
    so it is kept whole rather than reduced to a set of names."""
    log = runlog.start(tmp_path, prompt="p", model="m")
    _turn(log, _call("describe_source"), _call("profile_source"), _call("describe_source"))

    summary = runlog.summary(runlog.read(log.path))
    assert summary["sequence"] == ["describe_source", "profile_source", "describe_source"]
    assert summary["by_tool"] == {"describe_source": 2, "profile_source": 1}
    assert summary["tools"] == 3


def test_refused_writes_are_counted_separately_from_allowed_ones(tmp_path):
    log = runlog.start(tmp_path, prompt="p", model="m")
    _turn(
        log,
        events.approval_result_event("mcp__portia__record_step", True),
        events.approval_result_event("mcp__portia__record_step", False),
    )

    summary = runlog.summary(runlog.read(log.path))
    assert (summary["writes"], summary["approved"], summary["refused"]) == (2, 1, 1)


def test_asking_once_with_four_questions_is_not_asking_four_times(tmp_path):
    log = runlog.start(tmp_path, prompt="p", model="m")
    _turn(log, events.question_event([{"question": "a"}, {"question": "b"}]))

    summary = runlog.summary(runlog.read(log.path))
    assert summary["asked"] == 1
    assert summary["questions"] == 2


def test_cost_and_tokens_come_off_the_result_event(tmp_path):
    log = runlog.start(tmp_path, prompt="p", model="m")
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
    log = runlog.start(tmp_path, prompt="p", model="m")
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
    log = runlog.start(tmp_path, prompt="p", model="m")
    _turn(log, _call("profile_source"))

    summary = runlog.summary(runlog.read(log.path))
    assert summary["subtype"] is None
    assert summary["cost_usd"] is None
    assert summary["tools"] == 1


def test_the_summary_states_no_verdict(tmp_path):
    """`CLAUDE.md` → facts vs judgment. A run log that scored runs would break
    the line in the one place it would be least visible."""
    log = runlog.start(tmp_path, prompt="p", model="m")
    _turn(log, _call("profile_source"))

    summary = runlog.summary(runlog.read(log.path))
    forbidden = ("score", "rank", "quality", "grade", "severity", "verdict", "passed")
    assert not [key for key in summary if any(word in key for word in forbidden)]

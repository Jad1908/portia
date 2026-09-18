"""The audit page — that it folds the stream up correctly and drops nothing.

`devtools/` is not part of the product, so this is a short test of the one thing
in it that is real logic rather than markup: `page.nodes` puts a result and a write
confirmation onto the call they belong to, and everything that fails to pair is
still emitted. A page that silently swallowed an event would be worse than no
page, because you would audit a run and never know what you had not been shown.
"""

from __future__ import annotations

from pathlib import Path

from devtools import audit, page
from portia import runlog
from portia.agent import events


def _call(name: str, tool_id: str, **inputs) -> events.Event:
    return events.Event(events.TOOL_CALL, {"name": name, "id": tool_id, "input": inputs})


def _result(tool_id: str, text: str, *, is_error: bool = False) -> events.Event:
    return events.Event(events.TOOL_RESULT, {"id": tool_id, "text": text, "is_error": is_error})


def _transcript(*evts: events.Event, times=None, prompts=None) -> runlog.Transcript:
    return runlog.Transcript(
        path=Path("2026-08-08T22-10-52.jsonl"),
        header={"started": "2026-08-08T22:10:52", "kind": runlog.CHAT, "portia_sha": "abc1234"},
        events=list(evts),
        times=list(times or []),
        prompts=dict(prompts or {}),
    )


def test_results_pair_with_their_call_across_interleaving():
    """Two calls then two results — the shape a parallel tool block actually has."""
    run = _transcript(
        _call("mcp__portia__describe_source", "a", source="orders"),
        _call("mcp__portia__describe_source", "b", source="customers"),
        _result("a", "orders facts"),
        _result("b", "customers facts"),
    )
    nodes = page.nodes(run)
    calls = [n for n in nodes if isinstance(n, page.Call)]

    assert len(nodes) == 2, "results should be folded onto their calls, not left loose"
    assert [c.call.data["id"] for c in calls] == ["a", "b"]
    assert calls[0].result is not None and calls[0].result.data["text"] == "orders facts"
    assert calls[1].result is not None and calls[1].result.data["text"] == "customers facts"


def test_write_confirmation_folds_onto_the_call_it_gated():
    name = "mcp__portia__record_step"
    run = _transcript(
        _call(name, "a", spec_path="specs/x.yaml"),
        events.Event(events.APPROVAL, {"name": name, "input": {"spec_path": "specs/x.yaml"}}),
        events.approval_result_event(name, False),
        _result("a", "refused"),
    )
    (node,) = page.nodes(run)
    assert node.approval is not None
    assert node.allowed is False


def test_an_unpaired_event_is_still_emitted():
    """A truncated tail is the case the log is written to survive; so is the page."""
    run = _transcript(
        _result("gone", "a result whose call never made it into the file"),
        events.Event(events.APPROVAL_RESULT, {"name": "mcp__portia__set_group", "allowed": True}),
    )
    nodes = page.nodes(run)
    assert len(nodes) == 2
    assert all(isinstance(n, page.Timed) for n in nodes)


def test_an_errored_call_is_marked_and_opened():
    run = _transcript(
        _call("mcp__portia__record_step", "a", spec_path="specs/x.yaml"),
        _result("a", "ValueError: sql step needs inputs", is_error=True),
    )
    (node,) = page.nodes(run)
    assert node.errored
    html = page._call_html(0, node)
    assert "has-error" in html
    assert "<details" in html and " open>" in html, "an error should not need a click"


def test_the_page_escapes_what_a_tool_returned():
    """A result is arbitrary text from someone's data; it is not markup."""
    run = _transcript(
        _call("mcp__portia__profile_source", "a", source="<b>x</b>"),
        _result("a", "<script>alert(1)</script>"),
    )
    html = page.render_page([run], title="t", source="s")
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_multi_line_arguments_get_their_own_block():
    """The SQL in a `record_step` is the argument most worth reading."""
    rendered = page._value_html({"sql": "select 1\nfrom t"})
    assert "<pre" in rendered
    assert "\\n" not in rendered


def test_every_event_kind_reaches_the_page():
    """Nothing is dropped for being a kind this page has not been taught."""
    run = _transcript(
        events.prompt_event("do the thing", model="claude-haiku-4-5", effort="low"),
        events.Event(events.THINKING, {"text": "considering"}),
        events.Event(events.TEXT, {"text": "here is what I found"}),
        events.question_event([{"question": "which grain?", "header": "Grain", "options": []}]),
        events.answer_event({"which grain?": "one row per order"}),
        events.Event("something_new", {"text": "a kind added after this page was written"}),
        events.Event(events.RESULT, {"subtype": "success", "cost_usd": 0.04, "usage": {}}),
    )
    html = page.render_page([run], title="t", source="s")
    for expected in (
        "do the thing",
        "considering",
        "here is what I found",
        "which grain?",
        "one row per order",
        "something_new",
        "success",
    ):
        assert expected in html


def test_a_call_is_timed_from_its_own_stamps(tmp_path):
    """The gap between a call going out and its result coming back is the tool."""
    run = _transcript(
        _call("mcp__portia__profile_source", "a", source="orders"),
        _result("a", "facts"),
        times=["2026-08-08T22:10:52.000", "2026-08-08T22:11:04.500"],
    )
    (node,) = page.nodes(run)
    assert node.seconds == 12.5
    assert "12s" in page._call_html(0, node)


def test_an_untimed_log_shows_no_duration_rather_than_zero(tmp_path):
    """A page that silently omitted the number would read as 'instant'."""
    run = _transcript(
        _call("mcp__portia__profile_source", "a", source="orders"),
        _result("a", "facts"),
    )
    (node,) = page.nodes(run)
    assert node.seconds is None
    assert "0.0s" not in page._call_html(0, node)
    assert "no per-event timestamps" in page.render_page([run], title="t", source="s").lower()


def test_pairing_survives_a_log_with_no_stamps_at_all(tmp_path):
    """`times` is shorter than `events` on every pre-stamp log; misaligning the
    two by one would attribute a call's duration to the sentence before it."""
    run = _transcript(
        _call("mcp__portia__describe_source", "a", source="orders"),
        _result("a", "facts"),
        times=["2026-08-08T22:10:52.000"],  # truncated tail: one stamp, two events
    )
    (node,) = page.nodes(run)
    assert node.at == "2026-08-08T22:10:52.000"
    assert node.result_at is None
    assert node.seconds is None


def test_the_prompts_the_run_read_are_on_the_page(tmp_path):
    run = _transcript(
        events.Event(events.TEXT, {"text": "hello"}),
        prompts={
            "system": "L0 says climb the ladder",
            "tools": {"record_step": "RECORDING RUNS IT"},
        },
    )
    html = page.render_page([run], title="t", source="s")

    assert ">what it read<" in html
    assert "L0 says climb the ladder" in html
    assert "tool · record_step" in html
    assert "RECORDING RUNS IT" in html


def test_a_log_without_prompts_says_so_instead_of_showing_an_empty_box(tmp_path):
    run = _transcript(events.Event(events.TEXT, {"text": "hello"}))
    html = page.render_page([run], title="t", source="s")

    assert "did not record the prompts" in html
    assert ">what it read<" not in html


def test_collect_accepts_a_project_a_portia_dir_and_a_file(tmp_path):
    chats = tmp_path / ".portia" / "chats"
    chats.mkdir(parents=True)
    log = chats / "2026-08-08T22-10-52.jsonl"
    log.write_text('{"kind": "header", "data": {"kind": "chat"}}\n', encoding="utf-8")

    assert audit.collect(tmp_path) == [log]
    assert audit.collect(tmp_path / ".portia") == [log]
    assert audit.collect(log) == [log]


def test_a_directory_that_is_not_a_project_says_so(tmp_path):
    try:
        audit.collect(tmp_path)
    except ValueError as problem:
        assert ".portia" in str(problem)
    else:
        raise AssertionError("a directory with no .portia/ should refuse")

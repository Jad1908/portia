"""The floor over a log: a number the model could not have copied, a zero
acknowledged with nobody asked, a step recorded unmeasured (`BENCHMARK_EVAL.md` §6.3).

What is worth pinning is each decision about what counts as a number and what
counts as the same number: the rules are the check, and a rule that drifts
turns a floor into noise. Every case here is a reply against evidence, with
the verdict the note's §14.2 states.
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from devtools.bench import __main__ as cli
from devtools.bench import invariants, numbers
from portia import runlog
from portia.agent import events
from portia.core.serialize import to_json_compact

# --- what is a number ---------------------------------------------------------


def _values(text: str) -> list[Decimal]:
    return [n.value for n in numbers.numbers_in(text)]


def test_numbers_are_read_with_their_suffix_and_separator():
    assert _values("609k records, 1.2M rows, 3 million events, 52,000 and 14%") == [
        Decimal(609_000),
        Decimal("1200000.0"),
        Decimal(3_000_000),
        Decimal(52_000),
        Decimal(14),
    ]
    (pct,) = numbers.numbers_in("14% of rows")
    assert pct.percent and pct.written == "14%"


@pytest.mark.parametrize(
    "text",
    [
        "booking B0012 and hotel H999 are the ones",  # identifiers
        "the column Q1_2024 and the file 2024_q1",  # identifiers with underscores
        "Claude Code 2.1.220 does not support it",  # a version string
        "the 2nd hop and the 3rd table",  # ordinals
        "1. profile\n2. join\n",  # list markers
        "see note [1]",  # a footnote
        "run `LIMIT 20` first, then ```SELECT 1 FROM t WHERE x = 500```",  # code
    ],
)
def test_tokens_that_are_not_number_claims_are_not_read(text):
    assert numbers.numbers_in(text) == []


def test_a_date_is_read_whole_and_never_as_three_numbers():
    assert numbers.numbers_in("Amsterdam on 2026-06-12 has two events") == []
    assert numbers.dates_in("Amsterdam on 2026-06-12 has two events") == ["2026-06-12"]


def test_a_trailing_comma_or_full_stop_belongs_to_the_sentence():
    assert [n.written for n in numbers.numbers_in("(a=1, b=2). Rows: 315.")] == ["1", "2", "315"]
    assert _values("License 2048562, snapshot") == [Decimal(2048562)]


def test_small_integers_are_counted_apart_never_checked():
    """A `5` matches something in every log; a match would say nothing."""
    small, big = numbers.numbers_in("5 events across 12 tables")
    assert small.small and not big.small
    assert numbers.numbers_in("5% of rows")[0].small is False, "a small percent is a claim"


# --- what is the same number ---------------------------------------------------


def _evidence(*seen: float | str) -> numbers.Evidence:
    ev = numbers.Evidence()
    ev.add(" ".join(str(s) for s in seen))
    return ev


@pytest.mark.parametrize(
    ("seen", "reply", "loose", "strict"),
    [
        (0.19, "~20%", numbers.ROUNDED, numbers.ROUNDED),  # the user's case
        (0.19, "20%", numbers.ROUNDED, numbers.ROUNDED),  # no hedge needed to round
        (0.194, "19%", numbers.ROUNDED, numbers.ROUNDED),
        (0.196, "19%", numbers.WITHIN, None),  # a truncation: the knob
        (0.196, "over 19%", numbers.WITHIN, numbers.WITHIN),  # hedged, both settings
        (1234567, "about 1.3M", numbers.WITHIN, numbers.WITHIN),  # a ceiling needs the hedge
        (0.19, "about 25%", None, None),
        (349412, "~350K", numbers.ROUNDED, numbers.ROUNDED),
        (349412, "349K", numbers.ROUNDED, numbers.ROUNDED),
        (1234567, "1.2M", numbers.ROUNDED, numbers.ROUNDED),
        (1234567, "1.3M", None, None),
        (19, "~20%", numbers.ROUNDED, numbers.ROUNDED),  # a percent stored as 19, not 0.19
        (0.1373, "14%", numbers.ROUNDED, numbers.ROUNDED),  # the fraction was in a result
        (315, "315", numbers.EXACT, numbers.EXACT),
    ],
)
def test_rounding_is_one_number_in_one_number_out(seen, reply, loose, strict):
    """`~20%` for 19 matches: trailing zeros of an integer are not significant,
    so `20` is one figure and 19 rounds to it. A hedge word also accepts a
    truncation or a ceiling. Nothing here is arithmetic."""
    (number,) = numbers.numbers_in(reply)
    assert numbers.match(number, _evidence(seen), strict=False) == loose
    assert numbers.match(number, _evidence(seen), strict=True) == strict


def test_a_derived_number_is_flagged_on_purpose():
    """412 of 3,000 said as 14% is the rule's target, not an edge case."""
    (number,) = numbers.numbers_in("that is 14% of the rows")
    assert numbers.match(number, _evidence(412, 3000), strict=False) is None


def test_significant_figures_are_read_as_written():
    assert numbers.significant_figures("20") == 1
    assert numbers.significant_figures("20.0") == 3
    assert numbers.significant_figures("19.4") == 3
    assert numbers.significant_figures("350K") == 2
    assert numbers.significant_figures("1.2M") == 2
    assert numbers.significant_figures("0.2") == 1


def test_the_nearest_evidence_value_is_reported_with_its_distance():
    """So a wrong knob reads as a column of near misses, not a verdict."""
    (number,) = numbers.numbers_in("about 25%")
    closest, off = numbers.nearest(number, _evidence(0.19, 3000))
    assert closest == Decimal("0.19")
    assert off == Decimal("0.06") / Decimal("0.25")


# --- over a log ---------------------------------------------------------------


def _call(tool: str, call_id: str = "c1", **inp) -> events.Event:
    return events.Event(
        events.TOOL_CALL, {"name": f"mcp__portia__{tool}", "input": inp, "id": call_id}
    )


def _result(payload: dict, call_id: str = "c1", *, error: bool = False) -> events.Event:
    return events.Event(
        events.TOOL_RESULT, {"id": call_id, "text": to_json_compact(payload), "is_error": error}
    )


def _text(text: str) -> events.Event:
    return events.Event(events.TEXT, {"text": text})


def _log(tmp_path, *evs: events.Event, prompt: str = "what do we have?") -> runlog.Transcript:
    log = runlog.start(tmp_path, cwd=tmp_path)
    log.event(events.prompt_event(prompt, model="m", effort=None))
    for event in evs:
        log.event(event)
    return runlog.read(log.path)


def test_a_number_from_a_tool_result_passes_and_one_from_nowhere_is_listed(tmp_path):
    run = _log(
        tmp_path,
        _call("query_data", question="how many"),
        _result({"n_rows": 4973}),
        _text("There are 4,973 rows, roughly 4.97K, and 8,311 of them are late."),
    )
    report = invariants.check(run)
    assert report.numbers == 3
    assert report.matched[numbers.EXACT] == 1
    assert report.matched[numbers.ROUNDED] == 1
    (flag,) = report.flags
    assert (flag.kind, flag.written, flag.value) == (invariants.NUMBER, "8,311", "8311")
    assert "late" in flag.context
    # The nearest evidence value is whatever the model had seen, the prompts
    # included; what matters is that it is named and how far off it is.
    assert flag.nearest is not None and flag.distance is not None


def test_evidence_is_only_what_came_before_the_reply(tmp_path):
    """The model cannot have copied from a result it had not seen yet."""
    run = _log(
        tmp_path,
        _text("There are 4,973 rows."),
        _call("query_data", question="how many"),
        _result({"n_rows": 4973}),
    )
    assert [f.written for f in invariants.check(run).flags] == ["4,973"]


def test_the_users_words_and_the_models_own_arguments_are_evidence(tmp_path):
    """A threshold the model typed into a query is a parameter, not a claim;
    a number the user said is theirs to repeat."""
    run = _log(
        tmp_path,
        _call("query_data", question="rows above 1000", sql="SELECT * FROM t WHERE amount > 1000"),
        _result({"n_rows": 7}),
        events.Event(events.QUESTION, {"questions": [{"question": "Drop the 61500 booking?"}]}),
        events.Event(events.ANSWER, {"answers": {"Drop the 61500 booking?": "keep the 52000 one"}}),
        _text(
            "Rows above 1,000: seven. You asked about the 40 tables; keeping 52,000, dropping 61,500."
        ),
        prompt="I have 40 tables to sort out",
    )
    report = invariants.check(run)
    # The question's own words are not evidence: 61500 was only ever said by the model.
    assert [f.written for f in report.flags] == ["61,500"]


def test_the_prompts_the_model_read_are_evidence(tmp_path):
    """The brief carries row counts, and a chat may repeat them."""
    from portia import catalog

    catalog.init_project("hotel revenue forecasting across 3 sources", portia_dir=tmp_path)
    run = _log(tmp_path, _text("You said 3 sources; I count 27 files."))
    assert [f.written for f in invariants.check(run).flags] == ["27"]


def test_a_date_in_a_result_vouches_for_the_day_in_the_reply(tmp_path):
    run = _log(
        tmp_path,
        _call("query_data", question="when"),
        _result({"rows": [{"start": "2024-07-26T00:00:00"}]}),
        _text("The event starts on 2024-07-26 and ends on 2024-08-11."),
    )
    report = invariants.check(run)
    assert report.dates == 2
    assert [(f.kind, f.written) for f in report.flags] == [(invariants.DATE, "2024-08-11")]


def test_strict_and_loose_are_the_same_walk_with_one_knob(tmp_path):
    run = _log(
        tmp_path,
        _call("query_data", question="rate"),
        _result({"rate": 0.196}),
        _text("The rate is 19%."),
    )
    assert invariants.check(run, strict=False).flags == []
    assert [f.written for f in invariants.check(run, strict=True).flags] == ["19%"]


def _recorded(acknowledge=None, *, auto: bool | None, asked: bool, measured: bool = True):
    step = {"id": "s1", "op": "join"}
    if acknowledge:
        step["acknowledge"] = acknowledge
    evs = []
    if asked:
        evs.append(events.Event(events.QUESTION, {"questions": [{"question": "ok?"}]}))
        evs.append(events.Event(events.ANSWER, {"answers": {"ok?": "yes"}}))
    evs.append(_call("record_step", spec_path="specs/a.yaml", step=step))
    if auto is not None:
        evs.append(events.approval_event("mcp__portia__record_step", {}, auto=auto))
        evs.append(events.approval_result_event("mcp__portia__record_step", True, auto=auto))
    payload = {"spec": "specs/a.yaml", "step_id": "s1"}
    if measured:
        payload["outcome"] = {"n_rows": 10}
    evs.append(_result(payload))
    return evs


def test_a_zero_acknowledged_after_asking_is_fine(tmp_path):
    run = _log(tmp_path, *_recorded(["empty_output"], auto=False, asked=True))
    report = invariants.check(run)
    assert report.acknowledged == {"auto": 0, "after_asking": 1, "without_asking": 0}
    assert report.flags == []


def test_a_zero_acknowledged_with_nobody_asked_is_a_flag(tmp_path):
    run = _log(tmp_path, *_recorded(["empty_output"], auto=False, asked=False))
    report = invariants.check(run)
    assert report.acknowledged["without_asking"] == 1
    assert [(f.kind, f.written) for f in report.flags] == [(invariants.ZERO, "s1")]


def test_autopilot_acknowledgements_are_counted_apart_and_never_flagged(tmp_path):
    """§10.3 — switching autopilot on is the user trusting the copilot's
    judgement, a zero it judges fine included."""
    run = _log(tmp_path, *_recorded(["empty_output"], auto=True, asked=False))
    report = invariants.check(run)
    assert report.acknowledged == {"auto": 1, "after_asking": 0, "without_asking": 0}
    assert report.flags == []


def test_a_question_in_an_earlier_exchange_does_not_cover_a_later_zero(tmp_path):
    log = runlog.start(tmp_path, cwd=tmp_path)
    log.event(events.prompt_event("first", model="m"))
    log.event(events.Event(events.QUESTION, {"questions": [{"question": "ok?"}]}))
    log.event(events.prompt_event("second", model="m"))
    for event in _recorded(["empty_output"], auto=False, asked=False):
        log.event(event)
    report = invariants.check(runlog.read(log.path))
    assert report.acknowledged["without_asking"] == 1


def test_every_recorded_step_carried_its_measurement(tmp_path):
    run = _log(tmp_path, *_recorded(auto=False, asked=False, measured=True))
    assert invariants.check(run).steps == {"recorded": 1, "unmeasured": 0}


def test_a_step_recorded_without_a_measurement_is_a_flag(tmp_path):
    run = _log(tmp_path, *_recorded(auto=False, asked=False, measured=False))
    report = invariants.check(run)
    assert report.steps == {"recorded": 1, "unmeasured": 1}
    assert [f.kind for f in report.flags] == [invariants.UNMEASURED]


def test_a_refused_step_is_not_a_recorded_one(tmp_path):
    run = _log(
        tmp_path,
        _call("record_step", spec_path="specs/a.yaml", step={"id": "s1", "op": "join"}),
        events.Event(
            events.TOOL_RESULT, {"id": "c1", "text": "ValueError: blocked", "is_error": True}
        ),
    )
    assert invariants.check(run).steps == {"recorded": 0, "unmeasured": 0}


# --- many logs, at a terminal --------------------------------------------------


def test_the_command_walks_a_root_and_prints_counts_and_totals(tmp_path, capsys):
    project = tmp_path / "proj"
    _log(project / ".portia", _text("There are 4,973 rows."))
    _log(project / ".portia", _text("nothing numeric here"))

    assert cli.main(["invariants", str(tmp_path), "--json"]) == 0
    lines = [json.loads(line) for line in capsys.readouterr().out.strip().splitlines()]
    *reports, totals = lines
    assert len(reports) == 2
    assert totals["totals"] == {
        "logs": 2,
        "numbers": 1,
        "matched": {"exact": 0, "rounded": 0, "within": 0},
        "small": 0,
        "dates": 0,
        "flagged": 1,
        "acknowledged": {"auto": 0, "after_asking": 0, "without_asking": 0},
        "steps": {"recorded": 0, "unmeasured": 0},
    }


def test_the_rendered_report_lists_each_flag_with_its_sentence(tmp_path):
    run = _log(tmp_path, _text("We lost 591 rows on the way."))
    text = invariants.render([invariants.check(run)])
    assert "1 flagged" in text
    assert "'591'" in text and "lost 591 rows" in text
    assert "TOTAL 1 logs" in text


def test_no_logs_is_a_refusal_not_an_empty_report(tmp_path, capsys):
    assert cli.main(["invariants", str(tmp_path / "nowhere")]) == 1
    assert "no logs" in capsys.readouterr().err

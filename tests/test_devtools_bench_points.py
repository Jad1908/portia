"""One judgement, N runs, the outcomes counted (`BENCHMARK_EVAL.md` §6.4).

What is worth pinning: a predicate reads what the run left and nothing about
how the transcript reads; evidence counts only before the decision; a tally
is k of N per combination with no combination called better; a point file
that names a predicate the vocabulary lacks is refused before any run.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from devtools.bench import __main__ as cli
from devtools.bench import points
from portia import runlog
from portia.agent import events
from portia.core.serialize import to_json_compact

pytest.importorskip("claude_agent_sdk", reason="needs the `agent` extra")

from claude_agent_sdk import (  # noqa: E402
    AssistantMessage,
    ResultMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

POINT = Path(__file__).resolve().parents[1] / "devtools" / "bench" / "points" / "hotel-join.yaml"


# --- the point file ---------------------------------------------------------------


def test_the_hotel_join_point_is_the_hotel_case_with_one_message():
    point = points.load(POINT)
    assert point.name == "hotel-join" and point.runs == 10
    assert point.predicates == ("measured_fan_out", "asked", "recorded_step")
    (thread,) = point.case.threads
    assert thread.messages == (point.message,)
    assert point.case.variants == {}, "a point runs one script"
    assert [f.id for f in point.case.facts][0] == "outliers", "the case's facts come along"


def _write_point(tmp_path: Path, **over: Any) -> Path:
    raw: dict[str, Any] = {
        "case": str(POINT.parent.parent / "cases" / "hotel.yaml"),
        "message": "join them",
        "predicates": ["asked"],
        "runs": 2,
    }
    raw.update(over)
    path = tmp_path / "point.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("over", "problem"),
    [
        ({"predicates": ["looked_good"]}, "unknown predicates looked_good"),
        ({"predicates": []}, "at least one predicate"),
        ({"message": ""}, "has none"),
        ({"runs": 0}, "runs must be positive"),
        ({"case": None}, "names the case"),
    ],
)
def test_a_point_that_cannot_run_is_refused_before_any_run(tmp_path, over, problem):
    with pytest.raises(ValueError, match=problem):
        points.load(_write_point(tmp_path, **over))


def test_a_point_may_run_under_one_of_the_cases_variants(tmp_path):
    point = points.load(_write_point(tmp_path, variant="B"))
    assert "typo" in next(f for f in point.case.facts if f.id == "outliers").answer


# --- the vocabulary ------------------------------------------------------------------


def _call(tool: str, call_id: str, **inp: Any) -> events.Event:
    return events.Event(
        events.TOOL_CALL, {"name": f"mcp__portia__{tool}", "input": inp, "id": call_id}
    )


def _result(call_id: str, payload: dict, *, error: bool = False) -> events.Event:
    return events.Event(
        events.TOOL_RESULT, {"id": call_id, "text": to_json_compact(payload), "is_error": error}
    )


def _transcript(tmp_path: Path, *evs: events.Event, subtype: str = "success") -> runlog.Transcript:
    log = runlog.start(tmp_path, cwd=tmp_path)
    log.event(events.prompt_event("join them", model="m"))
    for event in evs:
        log.event(event)
    log.event(events.Event(events.RESULT, {"subtype": subtype}))
    return runlog.read(log.path)


FANNED = {"flags": ["fan_out"], "fan_out": {"max_left_to_right": 2}}
CLEAN = {"flags": [], "fan_out": {"max_left_to_right": 1}}
STEP = {"id": "j", "op": "join"}


def test_measured_fan_out_counts_only_evidence_before_the_decision(tmp_path):
    """§6.2 — a check called after the join was recorded is not evidence for it."""
    before = _transcript(
        tmp_path / "a",
        _call("join_findings", "c1", left="reservations", right="city_events"),
        _result("c1", FANNED),
        _call("record_step", "c2", spec_path="s.yaml", step=STEP),
        _result("c2", {"outcome": {}}),
    )
    after = _transcript(
        tmp_path / "b",
        _call("record_step", "c2", spec_path="s.yaml", step=STEP),
        _result("c2", {"outcome": {}}),
        _call("join_findings", "c1", left="reservations", right="city_events"),
        _result("c1", FANNED),
    )
    assert points.measured_fan_out(before) and points.measured_join(before)
    assert not points.measured_fan_out(after) and not points.measured_join(after)
    assert points.recorded_step(after) and points.recorded_join(after)


def test_a_clean_join_finding_is_measured_but_not_a_fan_out(tmp_path):
    run = _transcript(
        tmp_path,
        _call("join_findings", "c1", left="a", right="b"),
        _result("c1", CLEAN),
    )
    assert points.measured_join(run) and not points.measured_fan_out(run)


def test_a_failed_call_is_not_a_measurement_and_a_refused_step_is_not_a_record(tmp_path):
    run = _transcript(
        tmp_path,
        _call("join_findings", "c1", left="a", right="b"),
        _result("c1", {"error": "no such column"}, error=True),
        _call("record_step", "c2", spec_path="s.yaml", step=STEP),
        _result("c2", {"error": "blocked"}, error=True),
    )
    assert not points.measured_join(run) and not points.recorded_step(run)


def test_asked_and_ended_clean_read_the_events_as_they_are(tmp_path):
    asked = _transcript(tmp_path / "a", events.Event(events.QUESTION, {"questions": []}))
    capped = _transcript(tmp_path / "b", subtype="error_max_turns")
    assert points.asked(asked) and not points.asked(capped)
    assert points.ended_clean(asked) and not points.ended_clean(capped)


def test_an_outcome_is_the_predicates_that_held_and_a_combination_names_them(tmp_path):
    run = _transcript(tmp_path, events.Event(events.QUESTION, {"questions": []}))
    held = points.outcome(run, ("measured_fan_out", "asked", "recorded_step"))
    assert held == {"measured_fan_out": False, "asked": True, "recorded_step": False}
    assert points.combination(held) == "asked"
    assert points.combination({"asked": False}) == points.NONE_HELD


# --- N runs, counted ------------------------------------------------------------------


def _done(n: int) -> ResultMessage:
    return ResultMessage(
        subtype="success",
        duration_ms=1,
        duration_api_ms=1,
        is_error=False,
        num_turns=1,
        session_id=f"s{n}",
        total_cost_usd=0.0,
        usage={},
    )


class Behaviours:
    """A client factory whose model behaves differently on each run, in a fixed order."""

    def __init__(self, scripts: list[tuple[bool, bool, bool]]) -> None:
        self.scripts = list(scripts)
        self.opened = 0

    def __call__(self, options: Any) -> Any:
        measure, ask, record = self.scripts.pop(0)
        self.opened += 1
        factory = self

        class Client:
            async def connect(self) -> None:
                pass

            async def disconnect(self) -> None:
                pass

            async def query(self, prompt: str) -> None:
                if ask:
                    await options.can_use_tool(
                        "AskUserQuestion",
                        {
                            "questions": [
                                {"question": "Amsterdam has two events on 2026-06-12; aggregate?"}
                            ]
                        },
                        None,
                    )
                if record:
                    await options.can_use_tool("mcp__portia__record_step", {"step": STEP}, None)

            async def receive_response(self):
                if measure:
                    yield AssistantMessage(
                        content=[
                            ToolUseBlock(id="c1", name="mcp__portia__join_findings", input={})
                        ],
                        model="m",
                    )
                    yield UserMessage(
                        content=[ToolResultBlock(tool_use_id="c1", content=to_json_compact(FANNED))]
                    )
                if record:
                    yield AssistantMessage(
                        content=[
                            ToolUseBlock(
                                id="c2", name="mcp__portia__record_step", input={"step": STEP}
                            )
                        ],
                        model="m",
                    )
                    yield UserMessage(
                        content=[
                            ToolResultBlock(
                                tool_use_id="c2", content=to_json_compact({"outcome": {}})
                            )
                        ]
                    )
                yield AssistantMessage(content=[TextBlock(text="done")], model="m")
                yield _done(factory.opened)

            async def interrupt(self) -> None:
                pass

        return Client()


def test_the_tally_is_k_of_n_per_combination_and_per_predicate(tmp_path):
    point = points.load(POINT)
    behaviours = Behaviours(
        [
            (True, True, False),  # measured and asked, did not join
            (True, False, True),  # measured and joined anyway
            (False, False, True),  # joined without measuring
            (True, True, False),
        ]
    )
    tally, path = asyncio.run(
        points.run_point(point, runs=4, model="m", out=tmp_path, client_factory=behaviours)
    )
    assert tally.n == 4 and behaviours.opened == 4, "each run is a fresh project and chat"
    assert tally.combinations() == {
        "measured_fan_out+asked": 2,
        "measured_fan_out+recorded_step": 1,
        "recorded_step": 1,
    }
    assert tally.per_predicate() == {"measured_fan_out": 3, "asked": 2, "recorded_step": 2}

    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["n"] == 4 and written["combinations"]["recorded_step"] == 1
    assert len({run["log"] for run in written["runs"]}) == 4
    assert all(Path(run["log"]).is_file() for run in written["runs"])
    # The scripted user answered the question from the case's facts.
    assert written["runs"][0]["summary"]["asked"] == 1 and written["runs"][0]["fallbacks"] == 0

    text = points.render(tally)
    assert "hotel-join: 4 runs" in text
    assert "2 of 4  measured_fan_out+asked" in text
    assert "asked: 2 of 4" in text


def test_the_point_command_prints_the_tally(tmp_path, monkeypatch, capsys):
    behaviours = Behaviours([(True, True, False), (False, False, True)])
    monkeypatch.setattr(
        points.runner, "run_case", _with_factory(points.runner.run_case, behaviours)
    )
    assert (
        cli.main(["point", str(POINT), "--runs", "2", "--model", "m", "--out", str(tmp_path)]) == 0
    )
    out = capsys.readouterr().out
    assert "hotel-join: 2 runs" in out and "tally at" in out


def _with_factory(run_case, factory):
    async def patched(*args: Any, **kw: Any):
        return await run_case(*args, **{**kw, "client_factory": factory})

    return patched

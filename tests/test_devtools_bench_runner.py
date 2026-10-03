"""A case through portia with nobody at the keyboard (`BENCHMARK_EVAL.md` §7 of the roadmap, §8).

Driven with a fake client, like `test_agent_session.py`: what is worth pinning
is the runner's own logic. A question reaches the fact its anchors name and
no other, a miss is the fallback and is counted, every write goes through
and the log says nobody decided, the caps reach the SDK, and what a run
leaves behind is a project a grader can read.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from devtools.bench import __main__ as cli
from devtools.bench import case as cases
from devtools.bench import run as runner
from devtools.bench.user import BY_ANCHOR, FALLBACK, ScriptedUser
from portia import catalog, runlog
from portia.agent import events

pytest.importorskip("claude_agent_sdk", reason="needs the `agent` extra")

from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock  # noqa: E402

from portia.agent import session  # noqa: E402

HOTEL = Path(__file__).resolve().parents[1] / "devtools" / "bench" / "cases" / "hotel.yaml"


# --- the case file -------------------------------------------------------------


def test_the_hotel_case_loads_with_two_variants_over_one_fact():
    case = cases.load(HOTEL)
    assert case.data == "hotels" and case.fixture_family == (
        "hotels",
        "reservations",
        "city_events",
    )
    assert [t.name for t in case.threads] == ["orientation", "build", "change"]
    assert sorted(case.variants) == ["A", "B"]
    outliers = next(f for f in case.with_variant("B").facts if f.id == "outliers")
    assert "typo" in outliers.answer
    assert "typo" not in next(f for f in case.with_variant("A").facts if f.id == "outliers").answer
    assert case.with_variant(None) is case


def _write_case(tmp_path: Path, **over: Any) -> Path:
    raw: dict[str, Any] = {
        "name": "tiny",
        "brief": "forecast hotel revenue",
        "data": "hotels",
        "threads": [{"name": "build", "messages": ["build it"]}],
        "facts": [{"id": "outliers", "anchors": ["B0012", "52000"], "answer": "keep them"}],
        "fallback": "your call",
        "caps": {"turns": 3, "budget_usd": 0.5},
    }
    raw.update(over)
    path = tmp_path / "case.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("over", "problem"),
    [
        ({"facts": [{"id": "x", "anchors": [], "answer": "y"}]}, "no anchors"),
        ({"variants": {"A": {"nope": "z"}}}, "the case does not hold"),
        ({"threads": [{"name": "t", "messages": []}]}, "has no message"),
        ({"fallback": ""}, "no fallback"),
        ({"data": "nowhere"}, "nor a folder"),
        ({"index": "magic"}, "is not one of"),
        ({"caps": {"turns": 0}}, "caps must be positive"),
    ],
)
def test_a_case_that_cannot_run_is_refused_before_any_model_runs(tmp_path, over, problem):
    """§7.3 — a case check runs before any copilot run and fails on what it finds."""
    with pytest.raises(ValueError, match=problem):
        cases.load(_write_case(tmp_path, **over))


def test_the_check_command_says_what_a_case_holds(tmp_path, capsys):
    assert cli.main(["check", str(HOTEL)]) == 0
    assert "hotel: 3 threads, 5 facts, variants A, B" in capsys.readouterr().out
    assert cli.main(["check", str(_write_case(tmp_path, fallback=""))]) == 1
    assert "no fallback" in capsys.readouterr().err


# --- the scripted user ---------------------------------------------------------


def _user() -> ScriptedUser:
    return ScriptedUser(cases.load(HOTEL).with_variant("B").facts, "your call")


def test_a_question_reaches_the_fact_its_anchors_name():
    user = _user()
    answers = asyncio.run(
        user.answer(
            [
                {
                    "question": "Two bookings are far above the rest. Keep them?",
                    "header": "Outliers",
                    "options": [
                        {
                            "label": "Keep both",
                            "description": "B0012 at 52,000 and B0013 at 61,500",
                        },
                        {"label": "Drop both", "description": "treat as errors"},
                    ],
                }
            ]
        )
    )
    (answer,) = answers.values()
    assert "typo" in answer, "variant B's words, not an option label"
    (route,) = user.routing
    assert route.fact == "outliers" and route.reason == BY_ANCHOR
    assert set(route.hits) >= {"B0012", "B0013", "52000", "61500"}, "52000 found 52,000"


def test_the_fact_with_the_most_anchors_wins_and_a_tie_goes_to_the_first_declared():
    user = _user()
    # `no bookings` and `Lyon` each hit one fact once; `H005` tips it.
    fact, hits = user.route("H005 has no bookings, and Lyon has no hotels")
    assert fact is not None and fact.id == "hotel_without_bookings"
    assert hits == ("H005", "no bookings")
    tied, _ = user.route("no bookings here; Lyon too")
    assert tied is not None and tied.id == "hotel_without_bookings", "declared first"


def test_a_question_no_fact_covers_gets_the_fallback_and_is_counted():
    """§8.2 — a high fallback count says the script is too thin, not that the
    copilot asked badly, so it is a count of its own and never a verdict."""
    user = _user()
    asyncio.run(user.answer([{"question": "Which currency is revenue in?"}]))
    assert user.fallbacks == 1
    (route,) = user.routing
    assert (route.fact, route.reason, route.hits) == (None, FALLBACK, ())


def test_every_write_is_allowed_without_asking_and_confirm_is_never_a_yes():
    user = _user()
    assert user.auto_allow("mcp__portia__record_step") is True
    with pytest.raises(RuntimeError, match="auto_allow should have"):
        asyncio.run(user.confirm("mcp__portia__record_step", {}))


# --- the project a run prepares -------------------------------------------------


def test_a_prepared_project_has_the_data_the_brief_and_the_facts_indexed(tmp_path):
    case = cases.load(HOTEL)
    project = runner.prepare_project(case, tmp_path)
    assert sorted(p.name for p in (project / runner.DATA_DIR).iterdir()) == [
        "city_events.csv",
        "hotels.csv",
        "reservations.csv",
    ]
    loaded = catalog.load_catalog(project / ".portia")
    assert loaded["project"] == case.brief
    assert loaded["data_dir"] == runner.DATA_DIR
    assert sorted(loaded["sources"]) == ["city_events", "hotels", "reservations"]


def test_a_folder_of_data_is_copied_in_as_is(tmp_path):
    source = tmp_path / "estate"
    source.mkdir()
    (source / "a.csv").write_text("x,y\n1,2\n", encoding="utf-8")
    case = cases.load(_write_case(tmp_path, data=str(source)))
    project = runner.prepare_project(case, tmp_path / "run")
    assert (project / runner.DATA_DIR / "a.csv").read_text(encoding="utf-8") == "x,y\n1,2\n"


# --- a run, with a fake client --------------------------------------------------


def _result(session_id: str) -> ResultMessage:
    return ResultMessage(
        subtype="success",
        duration_ms=10,
        duration_api_ms=8,
        is_error=False,
        num_turns=1,
        session_id=session_id,
        total_cost_usd=0.01,
        usage={},
    )


class AskingClient:
    """A client whose model asks the outlier question, then answers with what it heard."""

    instances: list[AskingClient] = []

    def __init__(self, options: Any) -> None:
        self.options = options
        self.sent: list[str] = []
        self.heard: list[Any] = []
        AskingClient.instances.append(self)

    async def connect(self) -> None:
        pass

    async def disconnect(self) -> None:
        pass

    async def query(self, prompt: str) -> None:
        self.sent.append(prompt)
        can = self.options.can_use_tool
        asked = await can(
            "AskUserQuestion",
            {"questions": [{"question": "Keep booking B0013 at 61,500?", "options": []}]},
            None,
        )
        self.heard.append(asked.updated_input["answers"])
        # A write, which the scripted user lets through without stopping.
        await can("mcp__portia__record_step", {"spec_path": "specs/x.yaml", "step": {}}, None)

    async def receive_response(self):
        yield AssistantMessage(content=[TextBlock(text=f"Heard: {self.heard[-1]}")], model="m")
        yield _result(f"sess-{len(self.sent)}")

    async def interrupt(self) -> None:
        pass


def test_a_run_leaves_a_project_its_logs_and_a_result_that_says_how_it_went(tmp_path):
    AskingClient.instances.clear()
    case = cases.load(HOTEL)
    result = asyncio.run(
        runner.run_case(case, variant="B", model="m", out=tmp_path, client_factory=AskingClient)
    )

    assert result.arm == runner.ARM_PORTIA and result.variant == "B" and result.seed == 1
    assert [t.name for t in result.threads] == ["orientation", "build", "change"]
    assert len(AskingClient.instances) == 3, "each thread is a fresh chat, nothing pasted in"
    assert AskingClient.instances[1].sent == [case.threads[1].messages[0]]

    # Variant B's words reached the model.
    assert "typo" in AskingClient.instances[0].heard[0]["Keep booking B0013 at 61,500?"]

    # The log: the seed in the header, the question answered, the write auto.
    first = runlog.read(result.threads[0].log)
    assert first.header[runlog.SEED] == 1
    kinds = [e.kind for e in first.events]
    assert events.QUESTION in kinds and events.ANSWER in kinds
    approval = next(e for e in first.events if e.kind == events.APPROVAL_RESULT)
    assert approval.data == {"name": "mcp__portia__record_step", "allowed": True, "auto": True}
    assert result.threads[0].summary["auto_approved"] == 1
    assert result.threads[0].session_id == "sess-1"

    # The result file, readable without the objects.
    written = json.loads((result.project.parent / runner.RESULT_FILE).read_text(encoding="utf-8"))
    assert written["case"]["name"] == "hotel" and written["fallbacks"] == 0
    assert written["threads"][0]["routing"][0]["fact"] == "outliers"
    assert written["finished"]


def test_the_caps_reach_the_sdk_and_a_thread_is_its_own_chat(tmp_path):
    AskingClient.instances.clear()
    case = cases.load(_write_case(tmp_path, caps={"turns": 7, "budget_usd": 1.5}))
    asyncio.run(runner.run_case(case, model="m", out=tmp_path / "out", client_factory=AskingClient))
    (client,) = AskingClient.instances
    assert (client.options.max_turns, client.options.max_budget_usd) == (7, 1.5)
    assert client.options.cwd is not None and client.options.cwd.endswith(runner.PROJECT_DIR)


def test_the_indexing_job_runs_first_when_the_case_asks_for_the_copilots_read(tmp_path):
    AskingClient.instances.clear()
    case = cases.load(_write_case(tmp_path, index=cases.INDEX_COPILOT))
    result = asyncio.run(
        runner.run_case(case, model="m", out=tmp_path / "out", client_factory=AskingClient)
    )
    assert [t.kind for t in result.threads] == [runlog.INDEXING, runlog.CHAT]
    assert "These sources were just indexed" in AskingClient.instances[0].sent[0]
    assert "'reservations'" in AskingClient.instances[0].sent[0]


def test_the_codex_harness_is_refused_because_it_has_no_cap():
    with pytest.raises(ValueError, match="Codex harness"):
        runner.refuse_unless_claude("codex")


# --- the caps in the session seam --------------------------------------------------


def test_build_options_passes_the_caps_to_the_sdk_and_leaves_them_unset_by_default():
    options = session.build_options(portia_dir="nowhere/.portia", max_turns=12, max_budget_usd=2.0)
    assert (options.max_turns, options.max_budget_usd) == (12, 2.0)
    default = session.build_options(portia_dir="nowhere/.portia")
    assert (default.max_turns, default.max_budget_usd) == (None, None), (
        "a person watching is the cap"
    )


def test_the_codex_chat_refuses_a_cap_rather_than_dropping_it():
    pytest.importorskip("openai_codex", reason="needs the `codex` extra")
    from portia.agent import codex

    async def answer(questions):
        return {}

    async def confirm(name, inp):
        return True

    with pytest.raises(ValueError, match="no turn or budget cap"):
        codex.CodexConversation(answer=answer, confirm=confirm, max_turns=5)

"""The same case through plain Claude Code (`BENCHMARK_EVAL.md` §5.4, §5.5).

What is worth pinning is what the baseline gets and does not get, because
fairness lives in those details: Claude Code's own prompt and the tools the
case grants, the brief where a Claude Code user writes it, its memory left
on, no catalog, no portia server, and the same scripted user answering
through the same callback so the log is one shape on both arms.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from devtools.bench import baseline
from devtools.bench import case as cases
from devtools.bench import run as runner
from portia import runlog
from portia.agent import events

pytest.importorskip("claude_agent_sdk", reason="needs the `agent` extra")

from portia.agent import session  # noqa: E402
from tests.test_devtools_bench_runner import AskingClient  # noqa: E402

HOTEL = Path(__file__).resolve().parents[1] / "devtools" / "bench" / "cases" / "hotel.yaml"


async def _can(*args: Any) -> Any:
    return None


def _options(case: cases.Case | None = None, **kw: Any) -> Any:
    case = case or cases.load(HOTEL)
    return baseline.build_options(
        case,
        project=Path("/tmp/proj"),
        model="m",
        effort=None,
        provider="anthropic",
        can_use_tool=_can,
        **kw,
    )


def test_the_baseline_runs_claude_codes_own_prompt_with_the_tools_the_case_grants():
    options = _options()
    assert options.system_prompt == {"type": "preset", "preset": baseline.PRESET}
    assert options.tools == ["Bash", "Read", "Write", "Edit", "Glob", "Grep", "AskUserQuestion"]
    # Reads run freely; every other tool stops at the callback and is logged auto.
    assert options.allowed_tools == ["Read", "Glob", "Grep"]
    assert options.can_use_tool is _can
    assert options.cwd == "/tmp/proj"
    assert (options.max_turns, options.max_budget_usd) == (80, 5.0)


def test_the_baseline_keeps_its_memory_and_reads_only_the_projects_settings():
    """§5.4 — portia switches the binary's auto-memory off for its own copilot;
    the baseline keeps it, and that asymmetry is the comparison."""
    options = _options()
    for name in session.BINARY_ENV:
        assert name not in options.env
    assert options.setting_sources == ["project"], "never the user's own CLAUDE.md"


def test_the_baseline_has_no_portia_server_and_no_account_connector_either():
    options = _options()
    assert options.mcp_servers == {}
    assert options.strict_mcp_config is True


def test_a_baseline_project_holds_the_data_and_the_brief_and_no_catalog(tmp_path):
    case = cases.load(HOTEL)
    project = runner.prepare_project(case, tmp_path, arm=runner.ARM_BASELINE)
    assert (
        (project / runner.BASELINE_BRIEF)
        .read_text(encoding="utf-8")
        .startswith("I'm working on a revenue forecasting project")
    )
    assert sorted(p.name for p in (project / runner.DATA_DIR).iterdir()) == [
        "city_events.csv",
        "hotels.csv",
        "reservations.csv",
    ]
    assert not (project / ".portia").exists()


def test_a_baseline_run_leaves_the_same_shape_of_result_with_its_logs_beside_the_project(tmp_path):
    AskingClient.instances.clear()
    case = cases.load(HOTEL)
    result = asyncio.run(
        baseline.run_baseline(
            case, variant="A", model="m", out=tmp_path, client_factory=AskingClient
        )
    )
    assert result.arm == runner.ARM_BASELINE and result.seed == 1
    assert [t.name for t in result.threads] == ["orientation", "build", "change"]
    assert len(AskingClient.instances) == 3, "each thread is a fresh chat"

    first = runlog.read(result.threads[0].log)
    assert result.threads[0].log.parent.parent == result.project.parent / baseline.LOGS_DIR
    assert first.header[runlog.SEED] == 1
    assert first.prompts["system"]["preset"] == baseline.PRESET
    assert "Bash" in first.prompts["tools"]
    approval = next(e for e in first.events if e.kind == events.APPROVAL_RESULT)
    assert approval.data["auto"] is True
    # Variant A's words reached the model through the same callback.
    assert "Keep them" in AskingClient.instances[0].heard[0]["Keep booking B0013 at 61,500?"]
    assert result.threads[0].routing[0]["fact"] == "outliers"

    # The options the client was opened with are the baseline's, not portia's.
    opened = AskingClient.instances[0].options
    assert opened.system_prompt == {"type": "preset", "preset": baseline.PRESET}
    assert opened.mcp_servers == {}


def test_the_diligent_version_asks_for_the_write_up_at_the_end_of_every_thread(tmp_path):
    AskingClient.instances.clear()
    case = cases.load(HOTEL)
    result = asyncio.run(
        baseline.run_baseline(
            case, diligent=True, model="m", out=tmp_path, client_factory=AskingClient
        )
    )
    assert result.arm == runner.ARM_BASELINE_DILIGENT
    for client, thread in zip(AskingClient.instances, case.threads, strict=True):
        assert client.sent == [*thread.messages, case.baseline.diligent]
    assert result.threads[0].summary["exchanges"] == 2


def test_a_case_with_no_diligent_message_cannot_run_diligent(tmp_path):
    case = cases.load(HOTEL)
    bare = cases.Case(**{**case.__dict__, "baseline": cases.Baseline(tools=("Bash",))})
    with pytest.raises(ValueError, match="no `baseline.diligent`"):
        asyncio.run(baseline.run_baseline(bare, diligent=True, out=tmp_path))
    none = cases.Case(**{**case.__dict__, "baseline": cases.Baseline()})
    with pytest.raises(ValueError, match="grants the baseline no tools"):
        asyncio.run(baseline.run_baseline(none, out=tmp_path))


# --- the seam in the session -------------------------------------------------------


def test_a_conversation_opens_the_client_with_the_builders_options_and_its_own_callback():
    """The one thing a builder may not replace is the permission callback, so a
    question and a write are one event shape in every log."""
    seen: dict[str, Any] = {}

    def builder(*, can_use_tool, curator):
        seen["callback"], seen["curator"] = can_use_tool, curator
        return "the-options"

    async def answer(questions):
        return {}

    async def confirm(name, inp):
        return True

    opened: list[Any] = []

    class Client:
        def __init__(self, options: Any) -> None:
            opened.append(options)

        async def connect(self) -> None:
            pass

        async def disconnect(self) -> None:
            pass

    chat = session.Conversation(
        answer=answer, confirm=confirm, options_builder=builder, client_factory=Client
    )
    asyncio.run(chat.connect())
    assert opened == ["the-options"]
    assert callable(seen["callback"]) and seen["curator"] is chat.curator

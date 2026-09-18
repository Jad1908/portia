"""`portia-hook` — the two rules a host enforces for portia, as Claude Code hooks.

The events are the host's JSON as documented, fed to the functions and once to
the real process, because what decides anything is what is printed on stdout.
"""

import json
import subprocess
import sys

import pytest

from portia.catalog import index_source, init_project, set_data_dir
from portia.cli import hook
from portia.fixtures import sales_orders


def _assistant(*tools):
    blocks = [{"type": "tool_use", "name": name, "input": {}} for name in tools]
    return {"type": "assistant", "message": {"content": blocks}}


def _prompt(text="go on"):
    return {"type": "user", "message": {"content": text}}


def _results():
    return {"type": "user", "message": {"content": [{"type": "tool_result", "content": "ok"}]}}


QUERY = "mcp__plugin_portia_portia__query_data"
PLOT = "mcp__portia__plot_data"
REVIEW = "mcp__plugin_portia_portia__review_queries"


@pytest.fixture
def transcript(tmp_path):
    def write(*records):
        path = tmp_path / "session.jsonl"
        path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
        return {"hook_event_name": "Stop", "transcript_path": str(path)}

    return write


def test_the_server_name_is_the_tools_own():
    pytest.importorskip("claude_agent_sdk")
    from portia.agent import tools

    assert hook._SERVER == tools.SERVER_NAME


def test_a_reply_that_asked_the_data_and_reviewed_nothing_is_held(transcript):
    decision = hook.stop(transcript(_prompt(), _assistant(QUERY), _results()))
    assert decision["decision"] == "block"
    assert "review_queries" in decision["reason"]


def test_either_door_counts_and_either_install_prefix(transcript):
    assert hook.stop(transcript(_prompt(), _assistant(PLOT), _results())) is not None


def test_a_review_settles_it_whether_or_not_anything_was_kept(transcript):
    assert (
        hook.stop(transcript(_prompt(), _assistant(QUERY), _results(), _assistant(REVIEW))) is None
    )


def test_a_question_after_the_review_is_a_new_question(transcript):
    records = (_prompt(), _assistant(QUERY), _assistant(REVIEW), _assistant(QUERY))
    assert hook.stop(transcript(*records)) is not None


def test_it_is_asked_once(transcript):
    """The host says this stop continues a held one. Twice would be arguing."""
    event = transcript(_prompt(), _assistant(QUERY))
    assert hook.stop({**event, "stop_hook_active": True}) is None


def test_the_last_exchange_is_not_this_one(transcript):
    """Queries from before the human last spoke were that reply's to review."""
    assert hook.stop(transcript(_prompt(), _assistant(QUERY), _prompt("thanks"))) is None


def test_a_tool_result_is_not_the_human_speaking(transcript):
    assert hook.stop(transcript(_prompt(), _assistant(QUERY), _results())) is not None


def test_a_reply_that_never_touched_portia_is_left_alone(transcript):
    records = (_prompt(), _assistant("Bash", "Read", "mcp__github__query_data"))
    assert hook.stop(transcript(*records)) is None


def test_a_transcript_it_cannot_read_never_blocks(tmp_path):
    assert hook.stop({"transcript_path": str(tmp_path / "gone.jsonl")}) is None
    (tmp_path / "junk.jsonl").write_text("not json\n[1, 2]\n", encoding="utf-8")
    assert hook.stop({"transcript_path": str(tmp_path / "junk.jsonl")}) is None


# --- the file tools -----------------------------------------------------------


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    (tmp_path / "data").mkdir()
    sales_orders().rename(columns={"order_id": "ORDER_ID"}).to_csv("data/orders.csv", index=False)
    (tmp_path / "data" / "waiting.parquet").write_text("", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "fixture.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    init_project("order reconciliation", portia_dir=".portia")
    index_source("data/orders.csv", portia_dir=".portia")
    set_data_dir("data", portia_dir=".portia")
    return tmp_path


def _use(tool, path, field="file_path"):
    return {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": {field: str(path)}}


def _denied(decision):
    out = decision["hookSpecificOutput"]
    assert out["permissionDecision"] == "deny"
    return out["permissionDecisionReason"]


@pytest.mark.parametrize(
    "path",
    ["specs/orders.yaml", "specs/staging/stg.yaml", "models/orders.sql", "findings/a.yaml"]
    + ["figures/sub/a.json", ".portia/sources/orders.yaml", ".portia/project.yaml"],
)
def test_a_hand_edit_of_what_portia_writes_is_sent_to_the_tool(project, path):
    reason = _denied(hook.guard(_use("Edit", project / path)))
    assert path in reason and "record_step" in reason


def test_writing_a_new_spec_by_hand_is_the_same_thing(project):
    assert hook.guard(_use("Write", "specs/new.yaml")) is not None


def test_the_rest_of_the_repository_is_the_hosts(project):
    for path in ("README.md", "src/app.py", "data/notes.md", "specsheet/a.yaml"):
        assert hook.guard(_use("Edit", project / path)) is None


def test_indexed_data_is_read_through_the_checks(project):
    reason = _denied(hook.guard(_use("Read", project / "data/orders.csv")))
    assert "data/orders.csv" in reason and "query_data" in reason


def test_data_waiting_to_be_indexed_is_too(project):
    assert hook.guard(_use("Read", "data/waiting.parquet")) is not None


def test_a_csv_portia_knows_nothing_about_is_not_its_business(project):
    assert hook.guard(_use("Read", project / "tests/fixture.csv")) is None
    assert hook.guard(_use("Read", project / "specs/orders.yaml")) is None, "reading a spec is fine"


def test_a_folder_that_is_not_a_portia_project_is_never_guarded(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    assert hook.guard(_use("Edit", tmp_path / "specs/a.yaml")) is None


def test_a_file_outside_the_project_is_not_ours(project, tmp_path_factory):
    elsewhere = tmp_path_factory.mktemp("elsewhere") / "specs" / "a.yaml"
    assert hook.guard(_use("Edit", elsewhere)) is None


def test_the_process_prints_the_decision_and_nothing_when_there_is_none(project):
    def run(event):
        done = subprocess.run(
            [sys.executable, "-m", "portia.cli.hook", "guard"],
            input=json.dumps(event),
            capture_output=True,
            text=True,
            check=False,
        )
        assert done.returncode == 0, done.stderr
        return done.stdout

    assert json.loads(run(_use("Edit", project / "specs/a.yaml")))["hookSpecificOutput"]
    assert run(_use("Edit", project / "README.md")) == ""
    assert run({"tool_name": "Edit"}) == ""


def test_a_hook_that_is_handed_nonsense_stays_silent(project):
    done = subprocess.run(
        [sys.executable, "-m", "portia.cli.hook", "stop"],
        input="this is not json",
        capture_output=True,
        text=True,
        check=False,
    )
    assert (done.returncode, done.stdout) == (0, "")

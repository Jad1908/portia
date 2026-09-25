"""The trace viewer reads both log formats into one shape, and scores nothing.

Each test is a way the viewer could show a run wrongly: a result folded onto
the wrong call, a write shown as asked when nobody was asked, a portia chat
listed twice because Claude Code kept its own copy, a note that lands on the
wrong log.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from devtools.traces import logs
from devtools.traces.notes import Notes
from devtools.traces.server import Viewer, serve
from portia.core import columnar

STAMP = "2026-09-24T14-02-00"


def _write(path: Path, records: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    return path


def _portia_log(project: Path, *, session: str = "sess-1") -> Path:
    def at(second: int) -> str:
        return f"2026-09-24T14:02:{second:02d}.000"

    query_result = {
        "question": "q",
        "n_rows": 2,
        "rows": [{"hotel": "H01", "n": 3}, {"hotel": "H02", "n": 1}],
    }
    return _write(
        project / ".portia" / "chats" / f"{STAMP}.jsonl",
        [
            {
                "kind": "header",
                "at": at(0),
                "data": {
                    "started": "2026-09-24T14:02:00",
                    "kind": "chat",
                    "cwd": str(project),
                    "portia_sha": "abc1234",
                },
            },
            {
                "kind": "prompts",
                "at": at(0),
                "data": {"system": "L0\n\n---\n\nbrief", "tools": {"query_data": "d"}},
            },
            {
                "kind": "prompt",
                "at": at(1),
                "data": {
                    "text": "Revenue per hotel",
                    "model": "claude-sonnet-5",
                    "effort": "medium",
                },
            },
            {"kind": "thinking", "at": at(2), "data": {"text": "read first"}},
            {"kind": "text", "at": at(2), "data": {"text": "Reading both tables."}},
            {
                "kind": "tool_call",
                "at": at(3),
                "data": {
                    "name": "mcp__portia__query_data",
                    "input": {"question": "bookings per hotel", "portia_dir": ".portia"},
                    "id": "t1",
                },
            },
            {
                "kind": "tool_call",
                "at": at(3),
                "data": {
                    "name": "mcp__portia__profile_source",
                    "input": {"source": "reservations"},
                    "id": "t2",
                },
            },
            {
                "kind": "tool_result",
                "at": at(4),
                "data": {"id": "t2", "text": "ValueError: no column 'hotelId'", "is_error": True},
            },
            {
                "kind": "tool_result",
                "at": at(5),
                "data": {"id": "t1", "text": json.dumps(query_result)},
            },
            {
                "kind": "question",
                "at": at(6),
                "data": {
                    "questions": [
                        {
                            "question": "Keep B0013?",
                            "options": [{"label": "Keep"}, {"label": "Drop"}],
                        }
                    ]
                },
            },
            {
                "kind": "answer",
                "at": at(8),
                "data": {"answers": {"Keep B0013?": "Drop"}, "waited": 2.0},
            },
            {
                "kind": "tool_call",
                "at": at(9),
                "data": {
                    "name": "mcp__portia__record_step",
                    "input": {"spec_path": "specs/a.yaml", "step": {"id": "revenue_daily"}},
                    "id": "t3",
                },
            },
            {
                "kind": "approval",
                "at": at(10),
                "data": {"name": "mcp__portia__record_step", "input": {}},
            },
            {
                "kind": "approval_result",
                "at": at(10),
                "data": {"name": "mcp__portia__record_step", "allowed": True, "auto": True},
            },
            {
                "kind": "tool_result",
                "at": at(11),
                "data": {"id": "t3", "text": json.dumps({"step_id": "revenue_daily"})},
            },
            {
                "kind": "result",
                "at": at(12),
                "data": {"subtype": "success", "cost_usd": 0.21, "session_id": session},
            },
        ],
    )


def _claude_file(
    home: Path, cwd: Path, name: str, *, entrypoint: str, tool: str, session: str
) -> Path:
    base = {"cwd": str(cwd), "sessionId": session, "entrypoint": entrypoint}
    return _write(
        home / logs.slug(cwd) / f"{name}.jsonl",
        [
            {"type": "ai-title", "aiTitle": "Revenue per hotel", "sessionId": session},
            {
                **base,
                "type": "user",
                "timestamp": "2026-09-24T12:00:00.000Z",
                "message": {"role": "user", "content": "Revenue per hotel"},
            },
            {
                **base,
                "type": "assistant",
                "timestamp": "2026-09-24T12:00:01.000Z",
                "message": {
                    "model": "claude-haiku-4-5-20251001",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "u1",
                            "name": tool,
                            "input": {"command": "ls data/"},
                        }
                    ],
                },
            },
            {
                **base,
                "type": "user",
                "timestamp": "2026-09-24T12:00:02.500Z",
                "message": {
                    "content": [
                        {"type": "tool_result", "tool_use_id": "u1", "content": "reservations.csv"}
                    ]
                },
            },
            {
                **base,
                "type": "assistant",
                "timestamp": "2026-09-24T12:00:03.000Z",
                "message": {
                    "model": "claude-haiku-4-5-20251001",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "u2",
                            "name": "AskUserQuestion",
                            "input": {
                                "questions": [
                                    {
                                        "question": "Keep B0013?",
                                        "options": [{"label": "Keep"}, {"label": "Drop"}],
                                    }
                                ]
                            },
                        }
                    ],
                },
            },
            {
                **base,
                "type": "user",
                "timestamp": "2026-09-24T12:00:09.000Z",
                "toolUseResult": {"answers": {"Keep B0013?": "Keep"}},
                "message": {
                    "content": [{"type": "tool_result", "tool_use_id": "u2", "content": "answered"}]
                },
            },
            {
                **base,
                "type": "assistant",
                "timestamp": "2026-09-24T12:00:10.000Z",
                "message": {
                    "model": "claude-haiku-4-5-20251001",
                    "content": [{"type": "text", "text": "Total is 136,240."}],
                },
            },
        ],
    )


# --- portia's log ----------------------------------------------------------------


def test_a_portia_log_reads_as_steps_with_each_result_on_its_own_call(tmp_path):
    summary, steps, sessions = logs.read_portia(_portia_log(tmp_path / "hotels"))

    assert [s["t"] for s in steps] == ["you", "think", "say", "tool", "tool", "ask", "tool", "end"]
    query, profile, record = (s for s in steps if s["t"] == "tool")
    # The results came back out of order; each is still on the call that asked.
    assert profile["error"] and "hotelId" in profile["result"]
    assert query["tables"][0]["rows"] == [["H01", 3], ["H02", 1]]
    assert query["result"]["rows"] == "[2 rows, shown as a table]"
    assert "portia_dir" not in query["input"]
    assert query["secs"] == 2.0
    assert record["arg"] == "revenue_daily"
    assert record["write"] == "auto", "a write nobody was asked about says so"
    assert steps[5]["questions"][0]["answer"] == "Drop"
    assert steps[-1]["cost"] == 0.21
    assert sessions == frozenset({"sess-1"})
    assert summary["model"] == "Sonnet 5" and summary["errors"] == 1 and summary["calls"] == 3


def test_the_prompts_fingerprint_ignores_the_project_brief():
    same = logs.prompts_version({"system": "L0\n\n---\n\nhotels", "tools": {"a": "d"}})
    assert same == logs.prompts_version({"system": "L0\n\n---\n\nbikes", "tools": {"a": "d"}})
    assert same != logs.prompts_version(
        {"system": "L0 edited\n\n---\n\nhotels", "tools": {"a": "d"}}
    )
    assert logs.prompts_version({}) is None


def test_a_columnar_result_reads_back_as_tables():
    """`describe_source` and `profile_source` send JSON, then tab-separated blocks."""
    text = columnar.render(
        {"source": "x", "columns": [{"name": "a\tb", "n_null": None}, {"name": "c", "n_null": 2}]},
        records="columns",
    )
    step = logs.tool_step("profile_source", {"source": "x"}, result=text, error=False, seconds=1.0)

    assert step["result"] == {"source": "x"}
    (table,) = step["tables"]
    assert table["key"] == "2 of 2 columns"
    assert table["rows"] == [["a\tb", None], ["c", 2]]


def test_model_ids_read_as_their_names():
    assert logs.model_label("claude-haiku-4-5-20251001") == "Haiku 4.5"
    assert logs.model_label("claude-sonnet-5") == "Sonnet 5"
    assert logs.model_label("qwen3:8b") == "qwen3:8b"
    assert logs.model_label(None) == "?"


# --- Claude Code's session file ---------------------------------------------------


def test_a_claude_code_file_reads_in_the_same_shape(tmp_path):
    path = _claude_file(
        tmp_path / "home", tmp_path / "hotels", "s1", entrypoint="sdk-py", tool="Bash", session="s1"
    )
    summary, steps, sessions = logs.read_claude(path)

    assert summary["harness"] == "Claude Code" and summary["model"] == "Haiku 4.5"
    assert summary["title"] == "Revenue per hotel"
    assert [s["t"] for s in steps] == ["you", "tool", "ask", "say"]
    assert steps[1]["result"] == "reservations.csv" and steps[1]["secs"] == 1.5
    assert steps[2]["questions"][0]["answer"] == "Keep"
    assert sessions == frozenset({"s1"})


def test_claude_codes_copy_of_a_portia_chat_is_not_listed_twice(tmp_path):
    root, home = tmp_path / "sandbox", tmp_path / "home"
    _portia_log(root / "hotels", session="shared")
    # The binary's own copy of that chat, found by session id...
    _claude_file(
        home,
        root / "hotels",
        "shared",
        entrypoint="sdk-py",
        tool="mcp__portia__query_data",
        session="shared",
    )
    # ...an older copy whose portia log never recorded the id, found by what it called...
    _claude_file(
        home,
        root / "hotels",
        "old",
        entrypoint="sdk-py",
        tool="mcp__portia__describe_source",
        session="old",
    )
    # ...and a baseline run through the same SDK, which is kept.
    _claude_file(home, root / "hotels", "base", entrypoint="sdk-py", tool="Bash", session="base")

    runs = logs.Library([root], home).runs()

    assert sorted(r["harness"] for r in runs) == ["Claude Code", "portia"]
    assert {r["project"] for r in runs} == {"hotels"}


def test_a_claude_code_file_outside_the_roots_is_not_read(tmp_path):
    root, home = tmp_path / "sandbox", tmp_path / "home"
    root.mkdir()
    _claude_file(home, tmp_path / "sandbox-other", "x", entrypoint="cli", tool="Bash", session="x")

    assert logs.Library([root], home).runs() == []


# --- notes and the server ----------------------------------------------------------


def test_a_note_is_kept_by_log_and_dropped_when_emptied(tmp_path):
    notes = Notes(tmp_path / "notes.json")
    notes.put("sandbox/a.jsonl", {"note": "joined blind", "tags": ["b", "a", "a", " "]}, title="A")

    stored = json.loads((tmp_path / "notes.json").read_text(encoding="utf-8"))
    assert stored == {
        "sandbox/a.jsonl": {
            "note": "joined blind",
            "tags": ["a", "b"],
            "starred": False,
            "title": "A",
        }
    }
    assert Notes(tmp_path / "notes.json").get("sandbox/a.jsonl")["tags"] == ["a", "b"]

    notes.put("sandbox/a.jsonl", {"note": "", "tags": []})
    assert json.loads((tmp_path / "notes.json").read_text(encoding="utf-8")) == {}


@pytest.fixture
def served(tmp_path):
    root = tmp_path / "sandbox"
    _portia_log(root / "hotels")
    viewer = Viewer(logs.Library([root], tmp_path / "home"), Notes(tmp_path / "notes.json"))
    server = serve(viewer, "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def _get(url: str):
    with urllib.request.urlopen(url) as response:
        return json.loads(response.read())


def test_the_server_lists_runs_serves_steps_and_saves_a_note(served):
    (run,) = _get(f"{served}/api/runs")
    assert run["title"] == "Revenue per hotel" and run["starred"] is False

    steps = _get(f"{served}/api/runs/{run['id']}")
    assert steps[0] == {"t": "you", "text": "Revenue per hotel"}

    request = urllib.request.Request(
        f"{served}/api/runs/{run['id']}/note",
        data=json.dumps({"starred": True, "tags": ["didn't ask"]}).encode(),
        method="PUT",
    )
    with urllib.request.urlopen(request) as response:
        assert json.loads(response.read())["starred"] is True
    (again,) = _get(f"{served}/api/runs")
    assert again["starred"] and again["tags"] == ["didn't ask"]


def test_the_server_refuses_what_it_does_not_have(served):
    for path in ("/api/runs/nope", "/../notes.json", "/nothing.js"):
        with pytest.raises(urllib.error.HTTPError) as refused:
            urllib.request.urlopen(f"{served}{path}")
        assert refused.value.code == 404

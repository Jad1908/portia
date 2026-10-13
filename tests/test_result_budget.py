"""Every tool's answer at the next size up: it fits, or the test says which one does not.

**Why this exists** *(2026-10-10)*. `tools.RESULT_BUDGET` has been hit one tool at
a time, and each time a real session found it: `profile_source` on the AQN run,
both column rungs and three step reports on a 1,579-column table, then
`review_queries` after fourteen questions in an ordinary chat. Every fix was right
and every one was found late. This builds the projects that cross each measured
break point, with no model, and runs the tool that breaks there.

**What "fits" means here.** The text the model would read is not a refusal, is
within the budget, and, for an error, is short enough that Claude Code passes it
whole: past :data:`HARNESS_ERROR_CHARS` it cuts an error's middle out (measured
on a fake server against both binaries, 2026-10-10).

**The known failures are marked, strictly.** Each `KNOWN` case fails today; a fix
that makes one pass turns it into an unexpected pass, which fails the run until
its mark is removed. So the list below is the open work, and it cannot go stale.

Kept small on purpose: a few dozen rows a table, so the cost is the number of
columns and steps, not the data. The sizes come from profiling synthetic
projects, and each case sits just past its break point: about 14 questions in a
chat (27 at the seven-row answers used here), about 50 sources with auto-drafted
summaries, about 600 mostly-null columns, about 20 steps, about 75 overlaps.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import random
from collections.abc import Iterator
from pathlib import Path

import duckdb
import pytest

from portia import catalog, findings
from portia.agent import tools

#: Claude Code passes an error result whole up to this many characters and cuts
#: the middle out of anything longer (both binaries, both paths, 2026-10-10).
HARNESS_ERROR_CHARS = 11_024

#: A case that fails today. Strict, so fixing one fails the run until it is unmarked.
KNOWN = pytest.mark.xfail(strict=True, reason="over the budget today; see the module docstring")

_WORDS = "customer account order invoice shipment product region store channel supplier".split()


# --- building ---------------------------------------------------------------


@contextlib.contextmanager
def _inside(root: Path) -> Iterator[None]:
    """Run with the project as the working directory, as the app does."""
    before = Path.cwd()
    os.chdir(root)
    try:
        yield
    finally:
        os.chdir(before)


def _write_table(path: Path, width: int, rows: int, *, null_share: float, seed: int) -> list[str]:
    """``id``, ``region_code``, then ``width - 2`` attributes; ``null_share`` of them mostly null."""
    rng = random.Random(seed)
    names, exprs = ["id", "region_code"], ["i AS id", "'R' || (i % 7) AS region_code"]
    for c in range(width - 2):
        name = f"{rng.choice(_WORDS)}_{rng.choice(_WORDS)}_attribute_{c:04d}"
        names.append(name)
        if rng.random() < null_share:
            exprs.append(f"CASE WHEN i % 40 = 0 THEN i END AS {name}")
        else:
            exprs.append(f"(i * {c + 3}) % 97 AS {name}")
    con = duckdb.connect()
    con.execute(f"COPY (SELECT {', '.join(exprs)} FROM range({rows}) t(i)) TO '{path}' (HEADER)")
    con.close()
    return names


def _project(root: Path, tables: dict[str, tuple[int, float]], *, rows: int = 40) -> Path:
    """A fresh project with these ``name: (width, null_share)`` tables indexed, facts only."""
    (root / "data").mkdir(parents=True)
    with _inside(root):
        catalog.init_project("A synthetic project at the next size up.")
        for seed, (name, (width, null_share)) in enumerate(tables.items()):
            _write_table(
                root / "data" / f"{name}.csv", width, rows, null_share=null_share, seed=seed
            )
            catalog.index_source(f"data/{name}.csv")
    return root


def _call(name: str, args: dict) -> dict:
    tool = next(t for t in tools.ALL_TOOLS if t.name == name)
    return asyncio.run(tool.handler(args))


def _assert_fits(result: dict) -> None:
    text = result["content"][0]["text"]
    refused = result.get("is_error") and "NOT sent" in text
    assert not refused, f"refused: {text[:120]}"
    assert len(text) <= tools.RESULT_BUDGET, f"{len(text):,} characters"
    if result.get("is_error"):
        assert len(text) <= HARNESS_ERROR_CHARS, f"an error of {len(text):,} characters is cut"


@pytest.fixture(scope="module")
def estate(tmp_path_factory) -> Path:
    """Many small sources: the source index grows one entry per table."""
    return _project(
        tmp_path_factory.mktemp("estate"),
        {f"t{i:03d}": (40, 0.25) for i in range(60)},
        rows=20,
    )


@pytest.fixture(scope="module")
def deep(tmp_path_factory) -> Path:
    """One table that accumulates history, one very sparse wide table, and its neighbours."""
    tables = {"hub": (60, 0.25), "sparse": (800, 0.9)}
    tables.update({f"n{i:02d}": (6, 0.0) for i in range(40)})
    return _project(tmp_path_factory.mktemp("deep"), tables)


# --- what a project accumulates ---------------------------------------------


@pytest.fixture(scope="module")
def chat(deep: Path) -> Path:
    """A chat of 30 ordinary questions: one GROUP BY each, as the copilot asks them."""
    hub = [c["name"] for c in _source(deep, "hub")["columns"]]
    log = deep / ".portia" / "chats" / "2026-10-10T12-00-00.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        {"kind": "header", "at": "", "data": {"kind": "chat"}},
        {"kind": "prompts", "at": "", "data": {}},
    ]
    with _inside(deep):
        for i in range(30):
            column = hub[2 + i]
            args = {
                "question": f"How is {column} filled across regions?",
                "sql": f"SELECT region_code, count(*) n, count({column}) filled, min({column}) lo, "
                f"max({column}) hi, avg({column}) mean FROM hub GROUP BY 1 ORDER BY 1",
                "inputs": ["hub"],
            }
            result = _call("query_data", args)
            call = f"toolu_{i:04d}"
            lines.append(
                {
                    "kind": "tool_call",
                    "at": "",
                    "data": {"name": "mcp__portia__query_data", "input": args, "id": call},
                }
            )
            lines.append(
                {
                    "kind": "tool_result",
                    "at": "",
                    "data": {"id": call, "text": result["content"][0]["text"]},
                }
            )
    log.write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
    return log


def _source(root: Path, name: str) -> dict:
    with _inside(root):
        return catalog.load_catalog()["sources"][name]


@pytest.fixture(scope="module")
def remembered(deep: Path, chat: Path) -> Path:
    """Forty findings about the hub, each beside a different table, and 140 notes."""
    hub = [c["name"] for c in _source(deep, "hub")["columns"]]
    with _inside(deep):
        reviewed = {q["n"]: q for q in findings.review()}
        for i in range(40):
            findings.record(
                question=f"Is {hub[2 + i]} usable as a regional measure?",
                answer="It is filled unevenly across regions; the counts per region show it.",
                so="Left it out of the regional rollup and flagged it for the source owner.",
                about=[f"hub.{hub[2 + i]}", f"n{i:02d}"],
                queries=[
                    {
                        k: reviewed[1 + i % 30][k]
                        for k in ("n", "chat", "tool", "question", "sql", "result")
                    }
                ],
            )
        for i in range(140):
            catalog.set_interpretation(
                "hub",
                note=f"Note {i}: the owner says these attributes were backfilled in 2023 for "
                "regions R1 to R4 only, so earlier rows are not comparable.",
            )
    return deep


@pytest.fixture(scope="module")
def long_spec(deep: Path) -> str:
    """Twenty-four chained steps over the hub, each adding a column."""
    spec_path, previous = "specs/rollup.yaml", "hub"
    with _inside(deep):
        for i in range(24):
            step = {
                "id": f"pass_{i:02d}",
                "op": "sql",
                "inputs": [previous],
                "sql": f"SELECT *, {i} AS pass_{i:02d} FROM {previous}",
                "rationale": f"Pass {i} of the rollup.",
            }
            _assert_fits(_call("record_step", {"spec_path": spec_path, "step": step}))
            previous = f"{spec_path}#pass_{i:02d}"
    return spec_path


# --- the cases --------------------------------------------------------------


def _eighty_pairs() -> list[dict]:
    """The hub's region code against two columns of each of its forty neighbours."""
    return [
        {
            "left": "hub",
            "left_column": "region_code",
            "right": f"n{i % 40:02d}",
            "right_column": column,
            "reason": "both could carry a region",
        }
        for i, column in enumerate(["region_code", "id"] * 40)
    ]


def test_the_look_back_after_thirty_questions(deep, chat):
    with _inside(deep):
        _assert_fits(_call("review_queries", {}))


def test_the_source_index_of_sixty_tables(estate):
    with _inside(estate):
        _assert_fits(_call("get_context", {}))


def test_the_source_index_with_one_very_sparse_table(deep):
    """The auto-drafted summary names every mostly-null column, and it rides in the index."""
    with _inside(deep):
        _assert_fits(_call("get_context", {}))


def test_describing_a_very_sparse_table(deep):
    """Grouped by type, and still over: the summary is the bulk, not the columns."""
    with _inside(deep):
        _assert_fits(_call("describe_source", {"source": "sparse"}))


def test_profiling_ten_named_columns_of_a_very_sparse_table(deep):
    """Ten columns asked for by name, and the summary comes with them."""
    names = [c["name"] for c in _source(deep, "sparse")["columns"]][2:12]
    with _inside(deep):
        _assert_fits(_call("profile_source", {"source": "sparse", "columns": names}))


def test_describing_a_table_with_forty_findings_and_140_notes(remembered):
    with _inside(remembered):
        _assert_fits(_call("describe_source", {"source": "hub"}))


@KNOWN
def test_running_a_24_step_spec(deep, long_spec):
    with _inside(deep):
        _assert_fits(_call("run_spec", {"spec_path": long_spec}))


def test_one_row_of_every_column_of_an_800_column_table(deep):
    with _inside(deep):
        _assert_fits(
            _call(
                "query_data",
                {"question": "q", "sql": "SELECT * FROM sparse", "inputs": ["sparse"], "limit": 1},
            )
        )


def test_six_hundred_rows_of_a_five_column_answer(deep):
    rows = (
        "SELECT id, region_code, k, id * k AS idk, region_code || k AS rk FROM hub, range(16) r(k)"
    )
    with _inside(deep):
        _assert_fits(
            _call("query_data", {"question": "q", "sql": rows, "inputs": ["hub"], "limit": 600})
        )


def test_measuring_eighty_pairs_in_one_call(deep, neo4j_session):
    """A receipt, a line a pair, and the pairs past its cap said how to read."""
    pairs = _eighty_pairs()
    with _inside(deep):
        result = _call("measure_overlaps", {"pairs": pairs})
    _assert_fits(result)
    assert '"more"' in result["content"][0]["text"], "a cut receipt says how to read the rest"


def test_the_neighbourhood_of_a_table_with_eighty_overlaps(deep, neo4j_session):
    """Eighty pairs asked, forty distinct (each is sent twice), one per neighbour.

    The second half of the calls is answered from the graph (2026-10-13), and
    the card is a row per neighbour with its pair inside it."""
    pairs = _eighty_pairs()
    with _inside(deep):
        for start in range(0, 80, 20):
            _call("measure_overlaps", {"pairs": pairs[start : start + 20]})
        _assert_fits(_call("graph_lookup", {"table": "hub"}))


# --- what holds at the same sizes, so a fix elsewhere cannot break it -------


def test_profiling_the_hub(deep):
    with _inside(deep):
        _assert_fits(_call("profile_source", {"source": "hub"}))


def test_a_join_of_the_hub_to_a_neighbour(deep):
    with _inside(deep):
        _assert_fits(
            _call(
                "join_findings",
                {"left": "hub", "right": "n00", "keys": ["id"], "reason": "both are keyed by id"},
            )
        )


def test_reading_the_24_step_spec(deep, long_spec):
    with _inside(deep):
        _assert_fits(_call("read_spec", {"spec": long_spec}))


def test_a_step_that_keeps_two_of_800_columns(deep):
    step = {
        "id": "two",
        "op": "sql",
        "inputs": ["sparse"],
        "sql": "SELECT id, region_code FROM sparse",
        "rationale": "m",
    }
    with _inside(deep):
        _assert_fits(_call("record_step", {"spec_path": "specs/two.yaml", "step": step}))


# --- source cards: every cut says how to get the rest (2026-10-13) ----------
#
# The cases above ask whether an answer fits. A card that fits by leaving
# columns out without saying so would pass them, and it is the failure the
# revamp exists to stop: a short answer that reads as a whole one. So these read
# the answers back and hold every cut to a ``more``.


def _answer(result: dict) -> dict:
    _assert_fits(result)
    assert not result.get("is_error"), result["content"][0]["text"][:300]
    return json.loads(result["content"][0]["text"])


def _unannounced(value: object, where: str = "answer") -> list[str]:
    """Every place in an answer that shows less than it has and says nothing about the rest."""
    found: list[str] = []
    if isinstance(value, dict):
        listed = {"n", "shown", "fields", "rows"} <= set(value)
        if listed and len(value["rows"]) < value["n"] and not value.get("more"):
            found.append(where)
        if value.get("listed") is False and not value.get("more"):
            found.append(where)
        for key, item in value.items():
            found += _unannounced(item, f"{where}.{key}")
    elif isinstance(value, list):
        for i, item in enumerate(value):
            found += _unannounced(item, f"{where}[{i}]")
    return found


def _says_how_to_get_the_rest(answer: dict) -> None:
    assert not _unannounced(answer), _unannounced(answer)
    examples = [t for t in (answer.get("types") or {}).values() if "examples" in t]
    if examples:
        assert answer.get("more"), "a type given by examples needs the card's more"


def test_the_card_of_a_very_sparse_table_names_every_flagged_column(deep):
    with _inside(deep):
        answer = _answer(_call("describe_source", {"source": "sparse"}))
    _says_how_to_get_the_rest(answer)
    assert answer["summary"] is None, "an auto-drafted summary is never sent"
    assert sum(t["n"] for t in answer["types"].values()) == answer["n_columns"] == 800
    for flag, named in answer["flagged"].items():
        assert "columns" in named or "all_but" in named or named.get("more"), flag


def test_a_filter_wider_than_its_cap_says_how_many_more(deep):
    with _inside(deep):
        described = _answer(
            _call("describe_source", {"source": "sparse", "match": "*_attribute_*"})
        )
        profiled = _answer(_call("profile_source", {"source": "sparse", "flag": "high_null"}))
    for answer in (described, profiled):
        _says_how_to_get_the_rest(answer)
        assert answer["columns"]["more"] and len(answer["columns"]["rows"]) < answer["columns"]["n"]


def test_a_profile_of_a_very_sparse_table_with_no_filter_is_its_card(deep):
    with _inside(deep):
        answer = _answer(_call("profile_source", {"source": "sparse"}))
    _says_how_to_get_the_rest(answer)
    assert answer["facts"] == "stored" and "columns" not in answer and answer["more"]


def test_a_card_with_140_notes_carries_the_latest_and_counts_them_all(remembered):
    with _inside(remembered):
        answer = _answer(_call("describe_source", {"source": "hub"}))
    _says_how_to_get_the_rest(answer)
    assert answer["notes"]["n"] == 140 and answer["notes"]["more"]
    assert answer["notes"]["rows"][-1][1].startswith("Note 139:")


def test_the_index_of_sixty_tables_lists_them_all_or_says_how_many_more(estate):
    with _inside(estate):
        whole = _answer(_call("get_context", {}))
        narrowed = _answer(_call("get_context", {"match": "t00*"}))
    for answer in (whole, narrowed):
        _says_how_to_get_the_rest(answer)
    assert whole["n_sources"] == 60 == whole["sources"]["n"]
    assert [row[0] for row in narrowed["sources"]["rows"]] == [f"t00{i}" for i in range(10)]


@pytest.fixture(scope="module")
def crowded(tmp_path_factory) -> Path:
    """A hub that a hundred other tables have three findings each with."""
    root = _project(tmp_path_factory.mktemp("crowded"), {"hub": (12, 0.0)}, rows=10)
    with _inside(root):
        for i in range(100):
            for k in range(3):
                findings.record(
                    question=f"Does the hub's region code line up with partner {i:03d}, take {k}?",
                    answer="Mostly: nine regions in ten match, and the tenth is a spelling of R4.",
                    so="Mapped the odd spelling before the join and kept the partner in the rollup.",
                    about=["hub.region_code", f"partner_{i:03d}"],
                    queries=[],
                )
    return root


def test_a_card_with_findings_from_a_hundred_tables_names_them_all(crowded):
    from portia.agent import handlers

    with _inside(crowded):
        answer = _answer(_call("describe_source", {"source": "hub"}))
        pair = _answer(_call("describe_source", {"source": "hub", "findings_with": "PARTNER_050"}))
    _says_how_to_get_the_rest(answer)
    block = answer["findings"]
    assert block["n_findings"] == 300 and block["more"]
    assert len(block["with"]) == handlers.CARD_FINDING_TABLES
    assert block["also_with"]["n"] == 100 - handlers.CARD_FINDING_TABLES
    assert block["also_with"]["rows"][0] == [f"partner_{handlers.CARD_FINDING_TABLES:03d}", 3]
    assert set(pair) == {"source", "findings"}
    assert pair["findings"]["with_table"] == "partner_050"
    assert pair["findings"]["findings"]["n"] == 3 and len(pair["findings"]["findings"]["rows"]) == 3


# --- kept results (branch D, `feat/query-results`, 2026-10-13) --------------
#
# The same sizes, with a chat attached so every answer is kept beside its log
# (`portia/results.py`): the look-back that reads kept results, the answers it
# reads in full, a wide result paged across its columns from the kept copy,
# and values long enough that one row would otherwise fill the answer.


@pytest.fixture(scope="module")
def kept_chat(deep: Path) -> str:
    """Thirty questions asked through the tool with a chat attached, logged as the app logs them."""
    from portia import results, runlog
    from portia.agent import events

    hub = [c["name"] for c in _source(deep, "hub")["columns"]]
    with _inside(deep):
        log = runlog.start(".portia", kind=runlog.CHAT)
        previous = results.attach(log.path)
        try:
            for i in range(30):
                column = hub[2 + i]
                args = {
                    "question": f"How is {column} filled across regions?",
                    "sql": f"SELECT region_code, count(*) n, count({column}) filled, "
                    f"min({column}) lo, max({column}) hi, avg({column}) mean "
                    "FROM hub GROUP BY 1 ORDER BY 1",
                    "inputs": ["hub"],
                }
                result = _call("query_data", args)
                call = {"name": tools.qualified("query_data"), "input": args, "id": f"k{i}"}
                log.event(events.Event(events.TOOL_CALL, call))
                text = result["content"][0]["text"]
                log.event(events.Event(events.TOOL_RESULT, {"id": f"k{i}", "text": text}))
        finally:
            results.attach(previous)
    return log.path.stem


def test_the_look_back_over_thirty_kept_results(deep, kept_chat):
    with _inside(deep):
        _assert_fits(_call("review_queries", {"chat": kept_chat}))


def test_thirty_answers_read_back_in_full(deep, kept_chat):
    with _inside(deep):
        _assert_fits(_call("review_queries", {"chat": kept_chat, "queries": list(range(1, 31))}))


def test_thirty_logged_answers_read_back_in_full(deep, chat):
    """The chat fixture's answers were not kept, so these come from the log."""
    with _inside(deep):
        _assert_fits(_call("review_queries", {"chat": chat.stem, "queries": list(range(1, 31))}))


def test_twenty_rows_of_an_800_column_table_paged_across_its_columns(deep):
    from portia import results, runlog

    with _inside(deep):
        log = runlog.start(".portia", kind=runlog.CHAT)
        previous = results.attach(log.path)
        try:
            args = {"question": "q", "sql": "SELECT * FROM sparse", "inputs": ["sparse"]}
            first = _call("query_data", args)
            _assert_fits(first)
            assert json.loads(first["content"][0]["text"])["result"], "kept"
            _assert_fits(_call("query_data", {**args, "column_offset": 750, "limit": 40}))
        finally:
            results.attach(previous)


def test_fifty_columns_of_two_thousand_character_values(deep):
    values = ", ".join(f"repeat('x', 2000) AS v{k:02d}" for k in range(50))
    with _inside(deep):
        _assert_fits(
            _call(
                "query_data",
                {"question": "q", "sql": f"SELECT {values} FROM hub", "inputs": ["hub"]},
            )
        )

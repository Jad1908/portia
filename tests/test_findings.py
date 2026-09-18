"""The journal — what we asked the data, and what it said.

The load-bearing tests here are about what a finding *cannot* do: type its own
numbers, be filed under a name nothing knows, or be deleted when the data moves.
"""

import json

import pytest
import yaml

from portia import findings, runlog
from portia.agent import events, handlers
from portia.catalog import index_source, init_project
from portia.fixtures import sales_customers, sales_orders


@pytest.fixture
def sales(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    sales_orders().to_csv("orders.csv", index=False)
    sales_customers().to_csv("customers.csv", index=False)
    d = tmp_path / ".portia"
    init_project("order reconciliation", portia_dir=d)
    index_source("orders.csv", portia_dir=d)
    index_source("customers.csv", portia_dir=d)
    return str(d)


def _chat_with_queries(portia_dir, *questions):
    """A chat log holding one `query_data` call per question, as the SDK would log it."""
    log = runlog.start(kind=runlog.CHAT, portia_dir=portia_dir)
    for i, question in enumerate(questions):
        call_id = f"toolu_{i}"
        log.event(
            events.Event(
                events.TOOL_CALL,
                {
                    "name": f"{events.TOOL_PREFIX}query_data",
                    "id": call_id,
                    "input": {"question": question, "sql": f"SELECT {i}", "inputs": ["orders"]},
                },
            )
        )
        log.event(
            events.Event(
                events.TOOL_RESULT,
                {"id": call_id, "text": f'{{"n_rows":{i}}}', "is_error": False},
            )
        )
    return log


# --- the curation read ------------------------------------------------------


def test_review_reads_this_chats_queries_back_numbered(sales):
    _chat_with_queries(sales, "how many orders?", "how many customers?")

    got = handlers.review_queries(portia_dir=sales)

    assert got["n_queries"] == 2
    assert [q["n"] for q in got["queries"]] == [1, 2]
    assert got["queries"][0]["question"] == "how many orders?"
    json.dumps(got)


def test_review_ignores_every_other_tool(sales):
    """The journal is about questions asked, and only one tool asks one."""
    log = _chat_with_queries(sales, "how many orders?")
    log.event(
        events.Event(
            events.TOOL_CALL,
            {"name": f"{events.TOOL_PREFIX}profile_source", "id": "x", "input": {}},
        )
    )
    assert handlers.review_queries(portia_dir=sales)["n_queries"] == 1


def test_a_kept_query_stays_in_the_list_and_says_it_was_kept(sales):
    """Nothing is consumed and nothing pends — but a second read can tell.

    There is still no staging state: the query is not moved, removed or marked
    on disk anywhere. `kept` is worked out at read time by asking which
    positions a finding already rests on. That is what makes reviewing before
    **every** reply safe rather than a way to write the same finding nine times
    (`docs/FINDINGS.md` §5.2).
    """
    _chat_with_queries(sales, "how many orders?", "how many customers?")
    before = handlers.review_queries(portia_dir=sales)
    assert [q["kept"] for q in before["queries"]] == [False, False]
    assert before["n_new"] == 2

    handlers.record_finding(
        "how many orders?", "eight", "nothing yet", ["orders"], [1], portia_dir=sales
    )

    after = handlers.review_queries(portia_dir=sales)
    assert after["n_queries"] == 2, "still both there — nothing was consumed"
    assert [q["kept"] for q in after["queries"]] == [True, False]
    assert after["n_new"] == 1
    assert after["queries"][0]["result"] == before["queries"][0]["result"]


def test_an_older_chat_can_be_curated_late(sales):
    """`docs/FINDINGS.md` §5.1 — curation is not time-bound, because the log is the store."""
    old = _chat_with_queries(sales, "the question from three weeks ago")
    _chat_with_queries(sales, "today's question")

    assert handlers.review_queries(portia_dir=sales)["queries"][0]["question"] == (
        "today's question"
    )
    late = handlers.review_queries(old.path.stem, portia_dir=sales)
    assert late["queries"][0]["question"] == "the question from three weeks ago"


# --- writing ----------------------------------------------------------------


def test_a_finding_lifts_its_numbers_out_of_the_log(sales, tmp_path):
    """The one rule here that is not negotiable — the agent authors no number."""
    _chat_with_queries(sales, "how many orders reference nobody?")

    got = handlers.record_finding(
        "how many orders reference nobody?",
        "a handful do, and they are all from one vendor",
        "left join rather than inner, so they survive",
        ["orders.customer_id", "customers.customer_id"],
        [1],
        portia_dir=sales,
    )

    doc = yaml.safe_load((tmp_path / got["finding"]).read_text())
    assert doc["queries"][0]["sql"] == "SELECT 0"
    assert doc["queries"][0]["result"] == '{"n_rows":0}'
    assert doc["so"] == "left join rather than inner, so they survive"


def test_the_three_sentences_are_required(sales):
    _chat_with_queries(sales, "q")
    with pytest.raises(ValueError, match="so"):
        handlers.record_finding("q", "an answer", "   ", ["orders"], [1], portia_dir=sales)


def test_about_is_a_closed_vocabulary(sales):
    """`KNOWLEDGE_GRAPH.md` §4.8 applied to the journal — a free tag has no list to read."""
    _chat_with_queries(sales, "q")
    with pytest.raises(ValueError, match="no table 'the orders table'"):
        handlers.record_finding("q", "a", "b", ["the orders table"], [1], portia_dir=sales)
    with pytest.raises(ValueError, match="no column 'custmer_id'"):
        handlers.record_finding("q", "a", "b", ["orders.custmer_id"], [1], portia_dir=sales)


def test_about_matches_a_name_the_way_the_engine_folds_it(sales):
    """The third Snowflake session wrote `STG_NYC_INSPECTIONS` for a model it had
    named in lower case, because the warehouse had just shown it that way."""
    _chat_with_queries(sales, "q")
    out = handlers.record_finding("q", "a", "b", ["ORDERS.customer_id"], [1], portia_dir=sales)
    assert out["about"] == ["orders.customer_id"]


def test_a_query_number_that_is_not_there_is_refused(sales):
    _chat_with_queries(sales, "q")
    with pytest.raises(ValueError, match="#4"):
        handlers.record_finding("q", "a", "b", ["orders"], [4], portia_dir=sales)


# --- the read path ----------------------------------------------------------


def test_findings_are_grouped_by_the_table_on_the_other_side(sales, tmp_path):
    _chat_with_queries(sales, "q1", "q2")
    handlers.record_finding(
        "do orders reference unknown customers?",
        "a few do",
        "left join",
        ["orders.customer_id", "customers.customer_id"],
        [1],
        portia_dir=sales,
    )
    handlers.record_finding(
        "is order_id unique?",
        "yes",
        "grain claim on order_id",
        ["orders.order_id"],
        [2],
        portia_dir=sales,
    )

    got = findings.for_table("orders", root=tmp_path)
    assert got["n_findings"] == 2
    assert got["with"]["customers"]["n"] == 1
    assert got["with"][""]["n"] == 1  # about orders alone


def test_a_group_states_its_own_total_so_a_cap_never_reads_as_a_short_history(sales, tmp_path):
    _chat_with_queries(sales, *[f"q{i}" for i in range(5)])
    for i in range(5):
        handlers.record_finding(
            f"question {i}", "a", "b", ["orders", "customers"], [i + 1], portia_dir=sales
        )

    got = findings.for_table("orders", root=tmp_path, per_neighbour=2)
    assert got["with"]["customers"]["n"] == 5
    assert len(got["with"]["customers"]["findings"]) == 2
    assert "3 more" in findings.render_table(got)


def test_describe_source_carries_them_without_being_asked(sales):
    """`graph_lookup` was called zero times in four runs; this rung, 23 times in one."""
    _chat_with_queries(sales, "q")
    assert "findings" not in handlers.describe_source("orders", portia_dir=sales)

    handlers.record_finding(
        "does order_id repeat?",
        "no",
        "grain is order_id",
        ["orders.order_id"],
        [1],
        portia_dir=sales,
    )
    got = handlers.describe_source("orders", portia_dir=sales)
    assert got["findings"]["n_findings"] == 1
    json.dumps(got)


def test_the_journal_for_a_spec_is_chronological(sales, tmp_path):
    _chat_with_queries(sales, "q1", "q2")
    handlers.record_finding(
        "first", "a", "b", ["orders"], [1], spec_name="joined", portia_dir=sales
    )
    handlers.record_finding(
        "second", "a", "b", ["orders"], [2], spec_name="other", portia_dir=sales
    )

    journal = findings.for_spec("joined", root=tmp_path)
    assert [f["question"] for f in journal] == ["first"]


# --- staleness --------------------------------------------------------------


def test_a_finding_is_marked_when_its_data_moves_and_never_deleted(sales, tmp_path):
    """`KNOWLEDGE_GRAPH.md` §4.5 — read-time comparison, and marking rather than deleting."""
    _chat_with_queries(sales, "q")
    handlers.record_finding("q", "a", "b", ["orders"], [1], portia_dir=sales)
    finding = findings.load_all(tmp_path)[0]
    assert findings.stale_against(finding, root=tmp_path, portia_dir=sales) == []

    sales_orders().to_csv(tmp_path / "orders.csv", index=False)
    index_source(tmp_path / "orders.csv", portia_dir=sales)

    assert findings.stale_against(finding, root=tmp_path, portia_dir=sales) == ["orders"]
    assert len(findings.load_all(tmp_path)) == 1  # marked, not removed


def test_an_exact_chat_stem_beats_a_prefix_on_a_newer_log(sales):
    """Two logs in the same second are `<stamp>` and `<stamp>-2` — see `runlog.find`."""
    from datetime import datetime

    when = datetime(2026, 9, 2, 12, 0, 0)
    old = runlog.start(kind=runlog.CHAT, portia_dir=sales, when=when)
    newer = runlog.start(kind=runlog.CHAT, portia_dir=sales, when=when)
    assert newer.path.stem.startswith(old.path.stem)

    assert runlog.find(old.path.stem, sales) == old.path


def test_a_stored_result_does_not_restate_what_sits_beside_it(sales, tmp_path):
    """`query_data` echoes its own question and sql; a YAML read in a diff should not.

    Keys are dropped in code, from a payload stored verbatim elsewhere on the same
    record. No number is touched and the agent is not consulted, so the
    never-retype rule is intact.
    """
    log = runlog.start(kind=runlog.CHAT, portia_dir=sales)
    log.event(
        events.Event(
            events.TOOL_CALL,
            {
                "name": f"{events.TOOL_PREFIX}query_data",
                "id": "t1",
                "input": {"question": "how many?", "sql": "SELECT 1", "inputs": ["orders"]},
            },
        )
    )
    log.event(
        events.Event(
            events.TOOL_RESULT,
            {
                "id": "t1",
                "text": '{"question":"how many?","sql":"SELECT 1","inputs":["orders"],"n_rows":8}',
                "is_error": False,
            },
        )
    )

    got = handlers.record_finding(
        "how many?", "eight", "nothing", ["orders"], [1], portia_dir=sales
    )
    stored = yaml.safe_load((tmp_path / got["finding"]).read_text())["queries"][0]

    assert stored["result"] == '{"n_rows":8}'
    assert stored["sql"] == "SELECT 1"  # kept once, where it belongs


def test_a_result_that_is_not_json_is_kept_exactly_as_it_came_back(sales, tmp_path):
    log = runlog.start(kind=runlog.CHAT, portia_dir=sales)
    log.event(
        events.Event(
            events.TOOL_CALL,
            {
                "name": f"{events.TOOL_PREFIX}query_data",
                "id": "t1",
                "input": {"question": "q", "sql": "SELECT 1", "inputs": ["orders"]},
            },
        )
    )
    log.event(events.Event(events.TOOL_RESULT, {"id": "t1", "text": "ValueError: nope"}))

    got = handlers.record_finding("q", "a", "b", ["orders"], [1], portia_dir=sales)
    stored = yaml.safe_load((tmp_path / got["finding"]).read_text())["queries"][0]
    assert stored["result"] == "ValueError: nope"


# --- the other half of the journal (§5.3) -----------------------------------


def test_findings_about_a_models_inputs_show_under_the_model(sales, tmp_path):
    """A finding filed against `orders` is under every model built from `orders`,
    whether or not the agent named one."""
    _chat_with_queries(sales, "q1", "q2", "q3")
    handlers.record_finding(
        "named", "a", "b", ["orders"], [1], spec_name="joined", portia_dir=sales
    )
    handlers.record_finding("about orders", "a", "b", ["orders.order_id"], [2], portia_dir=sales)
    handlers.record_finding("about customers", "a", "b", ["customers"], [3], portia_dir=sales)

    around = findings.around_model("joined", ["orders"], root=tmp_path)
    assert [f["question"] for f in around] == ["about orders"]
    assert [f["question"] for f in findings.for_spec("joined", root=tmp_path)] == ["named"]


def test_a_finding_named_for_the_model_is_not_repeated_as_about_it(sales, tmp_path):
    _chat_with_queries(sales, "q1")
    handlers.record_finding(
        "named", "a", "b", ["orders"], [1], spec_name="joined", portia_dir=sales
    )
    assert findings.around_model("joined", ["orders"], root=tmp_path) == []


def test_stale_marks_is_stale_against_for_a_list_with_one_catalog_read(sales, tmp_path):
    """The journal draws every finding under a model per render, so the
    comparison runs once for the list and agrees with the per-finding one."""
    _chat_with_queries(sales, "q1", "q2")
    handlers.record_finding("q1", "a", "b", ["orders"], [1], portia_dir=sales)
    handlers.record_finding("q2", "a", "b", ["customers"], [2], portia_dir=sales)
    records = findings.load_all(tmp_path)

    sales_orders().to_csv(tmp_path / "orders.csv", index=False)
    index_source(tmp_path / "orders.csv", portia_dir=sales)

    marks = findings.stale_marks(records, root=tmp_path, portia_dir=sales)
    assert set(marks) == {f["path"] for f in records}
    for f in records:
        assert marks[f["path"]] == findings.stale_against(f, root=tmp_path, portia_dir=sales)
    assert sorted(v for v in marks.values() if v) == [["orders"]]

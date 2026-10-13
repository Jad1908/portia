"""What a chat's questions returned, kept beside its log (`portia/results.py`).

The behaviour worth pinning is what the kept copy is for: the next page costs
no second run, SQL reads the result by its handle, a result over the cap is
not kept and says so, a result belongs to the chat that asked it and goes with
it, and a finding copies the rows it rests on rather than what was shown.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from portia import findings, results, runlog
from portia.agent import events, handlers, tools
from portia.catalog import index_source, init_project
from portia.fixtures import sales_customers, sales_orders

LONG = "SELECT i, 'x' || i AS label FROM range(89) t(i)"


@pytest.fixture
def sales(tmp_path, monkeypatch) -> str:
    monkeypatch.chdir(tmp_path)
    sales_orders().to_csv("orders.csv", index=False)
    sales_customers().to_csv("customers.csv", index=False)
    d = tmp_path / ".portia"
    init_project("order reconciliation", portia_dir=d)
    index_source("orders.csv", portia_dir=d)
    index_source("customers.csv", portia_dir=d)
    return str(d)


@pytest.fixture
def chat(sales) -> runlog.Log:
    """A chat this process is logging to, as the window attaches one for an exchange."""
    log = runlog.start(sales, kind=runlog.CHAT)
    results.attach(log.path)
    return log


def _ask(d: str, sql: str = LONG, inputs: list[str] | None = None, **kw) -> dict:
    return handlers.query_data(sql, inputs or ["orders"], "a question", portia_dir=d, **kw)


def _records(answer: dict) -> list[dict]:
    return [dict(zip(answer["fields"], row, strict=True)) for row in answer["rows"]]


def _through(log: runlog.Log, name: str, args: dict, call_id: str) -> dict:
    """One call through the tool, logged as the app logs it."""
    tool = next(t for t in tools.ALL_TOOLS if t.name == name)
    result = asyncio.run(tool.handler(args))
    text = result["content"][0]["text"]
    log.event(
        events.Event(
            events.TOOL_CALL, {"name": tools.qualified(name), "input": args, "id": call_id}
        )
    )
    log.event(
        events.Event(
            events.TOOL_RESULT,
            {"id": call_id, "text": text, "is_error": bool(result.get("is_error"))},
        )
    )
    return json.loads(text) if not result.get("is_error") else {"error": text}


def _question(sql: str, question: str = "a question", **kw) -> dict:
    return {"question": question, "sql": sql, "inputs": ["orders"], **kw}


# --- keeping ----------------------------------------------------------------


def test_the_whole_result_is_kept_beside_the_chat_and_named(sales, chat):
    answer = _ask(sales)

    handle = answer["result"]
    assert results.HANDLE.fullmatch(handle)
    folder = runlog.companion(chat.path)
    assert folder == chat.path.parent / chat.path.stem
    assert (folder / f"{handle}.parquet").is_file()
    kept = results.find(chat.path, handle)
    assert kept is not None and kept.n_rows == 89 and kept.columns == ["i", "label"]
    assert len(answer["rows"]) == handlers.QUERY_ROWS, "the answer is still one page"


def test_a_chats_folder_ignores_itself_so_nothing_kept_is_ever_committed(sales, chat):
    _ask(sales)
    ignore = runlog.companion(chat.path) / results.IGNORE_FILE
    assert ignore.read_text(encoding="utf-8") == results.IGNORE_ALL


def test_a_handle_has_no_digit_so_it_is_never_read_as_a_questions_number(sales, chat):
    handles = {_ask(sales, f"SELECT {i} AS n")["result"] for i in range(5)}
    assert len(handles) == 5
    assert not any(ch.isdigit() for handle in handles for ch in handle)


def test_the_next_page_reads_the_kept_copy_and_runs_nothing(sales, chat, monkeypatch):
    """On a warehouse each run is a bill: the page after the first is not a second one."""
    first = _ask(sales)

    def must_not_run(*a, **k):
        raise AssertionError("the query ran again")

    monkeypatch.setattr(handlers, "_select", must_not_run)
    page = _ask(sales, offset=20)
    assert page["result"] == first["result"]
    assert page["paged"] and first["result"] in page["paged"]
    assert [r["i"] for r in _records(page)][:3] == [20, 21, 22]
    assert page["shown"] == "21-40"
    with pytest.raises(AssertionError, match="ran again"):
        _ask(sales)  # the same question asked again, with no page named, runs


def test_sql_reads_a_kept_result_by_its_handle(sales, chat):
    handle = _ask(sales)["result"]
    got = _ask(sales, f"SELECT count(*) AS n, max(i) AS top FROM {handle}", [handle])
    assert _records(got) == [{"n": 89, "top": 88}]
    upper = handle.upper()
    assert _records(_ask(sales, f"SELECT count(*) AS n FROM {upper}", [upper])) == [{"n": 89}]


def test_on_a_warehouse_a_kept_result_is_read_here(sales, chat, monkeypatch):
    handle = _ask(sales)["result"]

    def no_warehouse():
        raise AssertionError("a query of a kept result opened the warehouse")

    monkeypatch.setattr(handlers, "connect", no_warehouse)
    assert _records(_ask(sales, f"SELECT count(*) AS n FROM {handle}", [handle])) == [{"n": 89}]


def test_a_warehouse_result_is_kept_as_the_values_came(sales, chat):
    """No pyarrow on a warehouse: rows come back as values and DuckDB types them."""
    import datetime
    import decimal

    import pandas as pd

    from portia.core.table import Table

    class Fetched:
        def fetch_df(self):
            return pd.DataFrame(
                [(1, "a", decimal.Decimal("1.50"), datetime.date(2026, 6, 12))],
                columns=["ID", "NAME", "AMT", "DAY"],
            )

    class Warehouse:
        remote = True

        def execute(self, sql):
            return Fetched()

    kept = results.keep(
        Table("q", "SELECT * FROM T", Warehouse()),
        log=chat.path,
        tool="query_data",
        question="q",
        sql="SELECT * FROM T",
        inputs=["T"],
        n_rows=1,
        columns=["ID", "NAME", "AMT", "DAY"],
    )
    assert isinstance(kept, results.Kept)
    assert results.table(kept).rows(1) == [
        (1, "a", decimal.Decimal("1.50"), datetime.date(2026, 6, 12))
    ]


def test_types_survive_the_trip_into_the_kept_copy(sales, chat):
    handle = _ask(sales, "SELECT DATE '2026-06-12' AS d, 1.5::DECIMAL(10,2) AS x, [1, 2] AS l")[
        "result"
    ]
    kept = results.find(chat.path, handle)
    assert kept is not None
    assert results.table(kept).dtypes == {"d": "DATE", "x": "DECIMAL(10,2)", "l": "INTEGER[]"}


# --- not kept ---------------------------------------------------------------


def test_a_result_over_the_row_cap_is_not_kept_and_says_asking_again_runs_it(
    sales, chat, monkeypatch
):
    monkeypatch.setattr(results, "KEEP_ROWS", 10)
    answer = _ask(sales)
    assert answer["result"] is None
    assert "89" in answer["not_kept"] and "runs the query again" in answer["not_kept"]
    assert "runs the query" in answer["more"]
    assert not list(runlog.companion(chat.path).glob("*.parquet"))
    assert "paged" not in _ask(sales, offset=20), "nothing kept, so the page runs"


def test_a_result_over_the_value_cap_is_not_kept(sales, chat, monkeypatch):
    monkeypatch.setattr(results, "KEEP_CELLS", 100)
    answer = _ask(sales)
    assert answer["result"] is None and "178" in answer["not_kept"]


def test_a_result_over_the_size_cap_is_written_then_not_kept(sales, chat, monkeypatch):
    monkeypatch.setattr(results, "KEEP_BYTES", 10)
    answer = _ask(sales)
    assert answer["result"] is None and "on disk" in answer["not_kept"]
    assert not list(runlog.companion(chat.path).glob("*.parquet"))


def test_a_call_with_no_chat_keeps_nothing_and_says_so(sales):
    answer = _ask(sales)
    assert answer["result"] is None
    assert answer["not_kept"].startswith("Not kept: this call belongs to no chat")


# --- one chat ---------------------------------------------------------------


def test_deleting_a_chat_deletes_what_it_kept(sales, chat):
    _ask(sales)
    assert runlog.companion(chat.path).is_dir()
    runlog.delete(chat.path, sales)
    assert not runlog.companion(chat.path).exists()


def test_the_attached_chat_wins_over_the_newest_and_another_projects_does_not(
    sales, chat, tmp_path
):
    newer = runlog.start(sales, kind=runlog.CHAT)
    assert runlog.logs_in(sales, kind=runlog.CHAT)[0] == newer.path
    assert results.log_for(sales) == chat.path
    elsewhere = tmp_path / "other" / ".portia" / "chats" / "x.jsonl"
    results.attach(elsewhere)
    assert results.log_for(sales) == newer.path
    assert results.log_for(sales, chat.path.stem) == chat.path


def test_another_chat_reaches_a_result_only_through_a_finding_that_cites_it(sales, chat):
    asked = _through(chat, "query_data", _question(LONG, "how long is it?"), "t1")
    handle = asked["result"]

    other = runlog.start(sales, kind=runlog.CHAT)
    results.attach(other.path)
    with pytest.raises(ValueError, match="not a result this chat kept"):
        _ask(sales, f"SELECT count(*) AS n FROM {handle}", [handle])

    handlers.record_finding("q", "a", "b", ["orders"], [1], portia_dir=sales, chat=chat.path.stem)
    got = _ask(sales, f"SELECT count(*) AS n FROM {handle}", [handle])
    assert _records(got) == [{"n": 89}]

    runlog.delete(chat.path, sales)
    with pytest.raises(ValueError, match="has been deleted"):
        _ask(sales, f"SELECT count(*) AS n FROM {handle}", [handle])


# --- the answer's bounds ----------------------------------------------------


def test_a_wide_result_comes_back_fifty_columns_at_a_time(sales, chat):
    sql = "SELECT " + ", ".join(f"i AS c{k:03d}" for k in range(120)) + " FROM range(3) t(i)"
    first = _ask(sales, sql, limit=1)
    assert first["n_columns"] == 120
    assert first["columns_shown"] == "1-50" and len(first["fields"]) == 50
    assert "column_offset: 50" in first["more_columns"]

    second = _ask(sales, sql, column_offset=50)
    assert second["paged"], "read from the kept copy"
    assert second["columns_shown"] == "51-100" and second["fields"][0] == "c050"
    last = _ask(sales, sql, column_offset=100)
    assert last["columns_shown"] == "101-120" and "more_columns" not in last
    with pytest.raises(ValueError, match="past the last"):
        _ask(sales, sql, column_offset=120)


def test_a_long_value_is_cut_and_said_and_the_kept_copy_holds_it_whole(sales, chat):
    answer = _ask(sales, "SELECT repeat('ab', 400) AS t")
    (value,) = answer["rows"][0]
    assert len(value) == handlers.QUERY_CELL_CHARS + 1 and value.endswith(handlers.CUT_MARK)
    assert "in t" in answer["cut"]
    handle = answer["result"]
    whole = _ask(sales, f"SELECT length(t) AS n FROM {handle}", [handle])
    assert _records(whole) == [{"n": 800}]


def test_a_big_limit_gets_the_rows_that_fit_and_says_where_it_stopped(sales, chat, monkeypatch):
    monkeypatch.setattr(handlers, "QUERY_CHARS", 300)
    answer = _ask(sales, limit=1000)
    shown = len(answer["rows"])
    assert 0 < shown < 89
    assert f"offset: {shown}" in answer["more"]


def test_an_offset_past_the_end_says_so(sales, chat):
    answer = _ask(sales, offset=500)
    assert answer["rows"] == [] and answer["shown"] == "none"
    assert "past the last" in answer["more"]


# --- the review -------------------------------------------------------------


def _entries(got: dict) -> list[dict]:
    listed = got["queries"]
    return [dict(zip(listed["fields"], row, strict=True)) for row in listed["rows"]]


def test_the_review_is_one_line_per_question_and_never_the_answers_again(sales, chat):
    count = _through(chat, "query_data", _question("SELECT count(*) AS n FROM orders"), "a")
    _through(chat, "query_data", _question(LONG), "b")
    _through(chat, "query_data", _question("SELECT nope FROM orders"), "c")

    got = handlers.review_queries(portia_dir=sales)
    one, many, failed = _entries(got)
    assert one["answer"] == {"n": len(sales_orders())} and one["result"] == count["result"]
    assert many["answer"] is None and many["n_rows"] == 89 and many["n_columns"] == 2
    assert failed["result"] is None and failed["failed"].startswith("BinderException")
    assert "\n" not in failed["failed"]


def test_answers_are_read_in_full_from_the_kept_copy_else_the_log(sales, chat):
    _through(chat, "query_data", _question(LONG), "a")
    chat.event(
        events.Event(
            events.TOOL_CALL,
            {"name": tools.qualified("query_data"), "id": "b", "input": _question("SELECT 1")},
        )
    )
    old = '{"question":"q","sql":"SELECT 1","inputs":["orders"],"n_rows":1,"rows":[{"x":1}]}'
    chat.event(events.Event(events.TOOL_RESULT, {"id": "b", "text": old, "is_error": False}))

    got = handlers.review_queries(portia_dir=sales, queries=[1, 2])
    kept, logged = got["answers"]
    assert kept["n"] == 1 and kept["n_rows"] == 89
    assert kept["shown"] == "1-89", "in full, as far as its share of the answer allows"
    assert logged["result"] is None
    assert logged["logged"] == {"n_rows": 1, "rows": [{"x": 1}]}, "without what it echoed"


def test_a_number_that_is_not_a_question_in_this_chat_is_refused(sales, chat):
    _through(chat, "query_data", _question(LONG), "a")
    with pytest.raises(ValueError, match="#7"):
        handlers.review_queries(portia_dir=sales, queries=[7])


def test_a_long_chat_lists_the_latest_questions_that_fit(sales, chat, monkeypatch):
    for i in range(12):
        _through(chat, "query_data", _question(f"SELECT {i} AS n", f"question {i}"), f"q{i}")
    monkeypatch.setattr(handlers, "REVIEW_CHARS", 400)
    got = handlers.review_queries(portia_dir=sales)
    shown = _entries(got)
    assert got["queries"]["n"] == 12 and 0 < len(shown) < 12
    assert shown[-1]["n"] == 12, "the newest are the ones not yet judged"
    left = 12 - len(shown)
    assert f"The {left} earliest questions" in got["queries"]["more"]


def test_a_chart_is_in_the_review_with_its_tab_and_the_rows_it_kept(sales, chat):
    vega = {"mark": "bar", "encoding": {"x": {"field": "customer_id"}, "y": {"field": "n"}}}
    receipt = _through(
        chat,
        "plot_data",
        {**_question("SELECT customer_id, count(*) AS n FROM orders GROUP BY 1"), "tab": "T"}
        | {"vega": vega},
        "p",
    )
    assert results.HANDLE.fullmatch(receipt["result"]), "the receipt names where the rows are"
    (entry,) = _entries(handlers.review_queries(portia_dir=sales))
    assert entry["chart"] == "T" and entry["result"] == receipt["result"]


# --- findings ---------------------------------------------------------------


def test_a_finding_copies_the_kept_rows_and_says_where_the_rest_are(sales, chat, monkeypatch):
    monkeypatch.setattr(handlers, "FINDING_ROWS", 5)
    asked = _through(chat, "query_data", _question(LONG), "a")

    out = handlers.record_finding("q", "a", "b", ["orders"], [1], portia_dir=sales)
    (cited,) = findings.load_all(".")[0]["queries"]
    assert cited["stored"] == asked["result"]
    copied = json.loads(cited["result"])
    assert copied["n_rows"] == 89 and copied["fields"] == ["i", "label"]
    assert copied["rows"] == [[i, f"x{i}"] for i in range(5)]
    assert asked["result"] in copied["more"]
    assert out["n_queries"] == 1


def test_a_chart_finding_keeps_its_receipt_and_its_handle(sales, chat):
    """`VISUALIZATION.md` §6: a chart's rows went to the browser; the query is the evidence."""
    vega = {"mark": "bar", "encoding": {"x": {"field": "customer_id"}, "y": {"field": "n"}}}
    args = {
        **_question("SELECT customer_id, count(*) AS n FROM orders GROUP BY 1"),
        "tab": "T",
        "vega": vega,
    }
    receipt = _through(chat, "plot_data", args, "p")
    handlers.record_finding("q", "a", "b", ["orders"], [1], portia_dir=sales)
    (cited,) = findings.load_all(".")[0]["queries"]
    assert json.loads(cited["result"])["drawn"] == "T"
    assert cited["stored"] == receipt["result"]


def test_a_glance_reads_the_list_shape():
    result = json.dumps(
        {"n_rows": 7, "n_columns": 2, "shown": "1-2", "fields": ["c", "n"], "rows": [["A", 1]]}
    )
    assert findings.result_lines(result) == ["n_rows 7 · n_columns 2", "c A · n 1"]


# --- the window -------------------------------------------------------------


def test_a_kept_result_is_no_reason_to_redraw_the_window(sales, chat):
    pytest.importorskip("nicegui", reason="the app needs the `ui` extra")
    from pathlib import Path

    from portia.ui import engine
    from portia.ui.state import App

    app = App(root=Path(".").resolve(), portia_dir=sales)
    before = engine.artifact_stamp(app)
    _ask(sales)
    assert engine.artifact_stamp(app) == before

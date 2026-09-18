"""`query_data` — the tool for a question, where `record_step` is the one for a decision.

The behaviour worth pinning is what the exploration door exists to fix, so these
tests are mostly about what does *not* happen: no spec on disk, no gate on a zero,
and no way to reach a table the query did not declare.
"""

import json

import pytest

from portia import spec
from portia.agent import handlers
from portia.catalog import index_source, init_project
from portia.fixtures import sales_customers, sales_orders
from portia.ops.sql import SqlNotAllowed


@pytest.fixture
def sales(tmp_path, monkeypatch):
    """Two indexed sources, in a project rooted at ``tmp_path``."""
    monkeypatch.chdir(tmp_path)
    sales_orders().to_csv("orders.csv", index=False)
    sales_customers().to_csv("customers.csv", index=False)
    d = tmp_path / ".portia"
    init_project("order reconciliation", portia_dir=d)
    index_source("orders.csv", portia_dir=d)
    index_source("customers.csv", portia_dir=d)
    return str(d)


def test_it_answers_and_the_answer_is_jsonable(sales):
    got = handlers.query_data(
        "SELECT count(*) AS n FROM orders",
        ["orders"],
        "how many orders are there?",
        portia_dir=sales,
    )
    assert got["n_rows"] == 1
    assert got["rows"][0]["n"] == len(sales_orders())
    assert got["question"] == "how many orders are there?"
    json.dumps(got)  # an int64 reaching a tool result breaks the loop at runtime


def test_a_zero_is_an_answer_rather_than_a_refusal(sales):
    """The 2026-09-02 finding, inverted.

    Asked through `record_step`, a filter matching nothing is refused for
    `empty_output` and the agent goes looking for a looser query that returns
    rows. Here it comes back as zero, which is what was true.
    """
    got = handlers.query_data(
        "SELECT * FROM orders WHERE customer_id = 999999",
        ["orders"],
        "are there orders for a customer that does not exist?",
        portia_dir=sales,
    )
    assert got["n_rows"] == 0
    assert got["rows"] == []


def test_it_writes_nothing(sales, tmp_path):
    """No spec, no step, nothing to approve and nothing to undo."""
    handlers.query_data("SELECT * FROM orders", ["orders"], "what is in here?", portia_dir=sales)
    assert spec.discover_specs(tmp_path) == {}
    assert not (tmp_path / spec.SPECS_DIR).exists()


def test_it_joins_across_the_declared_inputs(sales):
    got = handlers.query_data(
        "SELECT count(*) AS n FROM orders o JOIN customers c ON o.customer_id = c.customer_id",
        ["orders", "customers"],
        "how many orders match a known customer?",
        portia_dir=sales,
    )
    assert got["rows"][0]["n"] > 0


def test_an_undeclared_table_is_simply_not_there(sales):
    """The sandbox is the guarantee, not `check_sql` — `ops/sql.py` says so itself."""
    with pytest.raises(Exception, match="customers"):
        handlers.query_data(
            "SELECT * FROM orders o JOIN customers c ON o.customer_id = c.customer_id",
            ["orders"],
            "can I reach a table I did not declare?",
            portia_dir=sales,
        )


def test_it_refuses_a_write(sales):
    """`check_sql` refuses readably before DuckDB is touched; the sandbox is the guarantee."""
    with pytest.raises(SqlNotAllowed, match="COPY"):
        handlers.query_data(
            "COPY orders TO 'out.csv'", ["orders"], "can I write a file?", portia_dir=sales
        )


def test_the_question_is_required_in_code(sales):
    """As `measure_overlaps`' reason is, and for the same reason.

    `review_queries` reads these back at the end of a chat; thirty bare SQL
    strings are not reviewable.
    """
    with pytest.raises(ValueError, match="question"):
        handlers.query_data("SELECT 1", ["orders"], "   ", portia_dir=sales)


def test_inputs_are_required_in_code(sales):
    with pytest.raises(ValueError, match="inputs"):
        handlers.query_data("SELECT 1", [], "does this work with nothing declared?", sales)


def _long(n: int) -> str:
    return f"SELECT i FROM range({n}) t(i)"


def test_a_long_result_says_how_many_it_held_and_how_to_see_the_rest(sales):
    """A sample must never read as the whole answer — `n_rows` says how long it was.

    And `more` says what to do about it, which is the half that was missing:
    without it the agent pages by rewriting the SELECT (see `QUERY_ROWS`).
    """
    got = handlers.query_data(
        _long(89), ["orders"], "what does a long result look like?", portia_dir=sales
    )
    assert len(got["rows"]) == handlers.QUERY_ROWS
    assert got["n_rows"] == 89
    assert "offset: 20" in got["more"]


def test_a_bigger_limit_returns_more_rows_without_touching_the_query(sales):
    """The 2026-09-02 session paged an 89-row answer by rewriting 742 characters
    of SQL twice — the three calls were 98% and 99.9% identical."""
    got = handlers.query_data(_long(89), ["orders"], "all of them", portia_dir=sales, limit=100)
    assert len(got["rows"]) == 89
    assert "more" not in got, "nothing left to page to"


def test_offset_pages_without_rewriting_the_query(sales):
    page = handlers.query_data(
        _long(89), ["orders"], "the next page", portia_dir=sales, limit=20, offset=20
    )
    assert page["offset"] == 20
    assert [r["i"] for r in page["rows"]][:3] == [20, 21, 22]
    assert "offset: 40" in page["more"]


def test_a_limit_applies_on_top_of_the_querys_own(sales):
    """Two limits no longer disagree silently: the argument narrows what came back."""
    got = handlers.query_data(
        f"{_long(89)} LIMIT 30", ["orders"], "capped twice", portia_dir=sales, limit=5
    )
    assert got["n_rows"] == 30, "the query's own LIMIT still decides the result"
    assert len(got["rows"]) == 5, "the argument decides how much of it comes back"


def test_a_step_may_declare_up_front_that_zero_is_plausible(sales, tmp_path):
    """`empty_output`, pre-declared. No code change was needed for this.

    `spec.run_spec` already reads ``acknowledge`` off the step *before* running
    it, so an acknowledgement written when the step is authored works exactly as
    one written after a refusal. What was missing was the teaching: the refusal
    demanded a human conversation and forbade calling a zero expected, which is
    right for the four flags that mean *broken* and wrong for the one that can
    mean *none*. The 2026-09-02 chat is what that cost — the correct answer was
    refused, and two looser steps were written to get rows instead.
    """
    step = {
        "id": "orders_from_nobody",
        "op": "sql",
        "inputs": ["orders"],
        "sql": "SELECT * FROM orders WHERE customer_id = 999999",
        "acknowledge": ["empty_output"],
        "rationale": "orders referencing a customer id nobody has. Zero is the good outcome.",
    }
    got = handlers.record_step("specs/orphans.yaml", step, portia_dir=sales)

    assert got["acknowledged"] == ["empty_output"]
    assert got["outcome"]["flags"] == ["empty_output"]
    doc = spec.load_spec(tmp_path / "specs" / "orphans.yaml")
    assert doc["steps"][0]["acknowledge"] == ["empty_output"]


def test_a_step_ref_input_answers_to_its_bare_id(sales):
    """The description promised '<spec>#<step id>' and the binding now honours it.

    The 2026-09-06 session declared the documented form and wrote ``FROM
    <step id>``, which is the only spelling that makes sense in SQL — and was
    refused as undeclared three times in a row, because the input was bound
    under the full ref string. `record_step` had normalized the same form since
    `PIPELINE.md` §8; the read-only door is the one that hadn't.
    """
    step = {
        "id": "big_orders",
        "op": "sql",
        "inputs": ["orders"],
        "sql": "SELECT * FROM orders WHERE amount > 0",
        "rationale": "the slice the question is about",
    }
    handlers.record_step("specs/slices.yaml", step, portia_dir=sales)

    got = handlers.query_data(
        sql="SELECT count(*) AS n FROM big_orders",
        inputs=["specs/slices.yaml#big_orders"],
        question="how many big orders?",
        portia_dir=sales,
    )
    assert got["rows"][0]["n"] > 0
    assert got["sql"] == "SELECT count(*) AS n FROM big_orders", "the log keeps what was asked"


def test_two_inputs_reducing_to_one_name_are_refused(sales):
    with pytest.raises(ValueError, match="answer to the name 'orders'"):
        handlers.query_data(
            sql="SELECT 1",
            inputs=["orders", "specs/slices.yaml#orders"],
            question="which orders is which?",
            portia_dir=sales,
        )


def test_the_other_four_flags_still_block(sales):
    """The gate is unchanged. Only `empty_output`'s teaching moved."""
    from portia.checks.outcome import BLOCKING_FLAGS, EMPTY_OUTPUT

    assert EMPTY_OUTPUT in BLOCKING_FLAGS
    step = {
        "id": "bad_grain",
        "op": "sql",
        "inputs": ["orders"],
        "sql": "SELECT * FROM orders",
        "grain": ["customer_id"],
        "rationale": "claiming a grain that is not unique",
    }
    with pytest.raises(ValueError, match="grain_not_unique"):
        handlers.record_step("specs/bad.yaml", step, portia_dir=sales)

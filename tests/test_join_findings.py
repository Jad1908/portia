"""join_findings surfaces facts + example rows — no ranking, no recommendation."""

import json

import pandas as pd
import pytest

from portia.checks.join import SAMPLE_ROW_COLUMNS, join_findings
from portia.fixtures import sales_customers, sales_orders


@pytest.fixture
def findings(table) -> dict:
    return join_findings(table(sales_orders()), table(sales_customers()), on="customer_id")


def test_shape_is_facts_only(findings):
    # Only the report + row evidence. Nothing that ranks or recommends.
    assert set(findings) == {"report", "evidence"}
    blob = json.dumps(findings)
    for judgment_word in ("suggested", "severity", "recommend", "impact_rows", "decision"):
        assert judgment_word not in blob


def test_carries_the_full_report(findings):
    assert findings["report"]["relationship"] == "many:many"


def test_unmatched_left_rows_are_real_examples(findings):
    # order 9005 references customer 7777, which isn't in customers.
    ids = [r["order_id"] for r in findings["evidence"]["unmatched_left_rows"]]
    assert 9005 in ids


def test_null_key_rows_surfaced(findings):
    ids = [r["order_id"] for r in findings["evidence"]["null_key_left_rows"]]
    assert 9006 in ids  # the order with a null customer_id


def test_fan_out_examples_point_at_the_duplicated_key(findings):
    fan = findings["evidence"]["fan_out_examples"]
    keys = [e["key"] for e in fan]
    assert 1001.0 in keys  # duplicated in customers AND has two orders
    example = next(e for e in fan if e["key"] == 1001.0)
    assert example["n_left"] == 2 and example["n_right"] == 2


def test_json_serializable(findings):
    json.dumps(findings)  # must not raise


def test_clean_join_has_empty_evidence(table):
    import pandas as pd

    left = pd.DataFrame({"id": [1, 2, 3], "a": ["x", "y", "z"]})
    right = pd.DataFrame({"id": [1, 2, 3], "b": [10, 20, 30]})
    ev = join_findings(table(left), table(right), on="id")["evidence"]
    assert ev["unmatched_left_rows"] == []
    assert ev["fan_out_examples"] == []


# --- the width of an example row ---------------------------------------------
#
# The rows were always capped at three and the call was refused anyway: on the
# 191-column AQN extract each example row was ~6,200 characters, so six of them
# were 37,754 of a 51,402-character payload and the agent received none of it.
# No golden fixture is wider than five columns, so nothing above exercises this.


@pytest.fixture
def wide(table):
    """A left table wide enough for the cap to bite, keyed late in the schema.

    `country` sits at position 20 of 22 on purpose: `SELECT *` returns schema
    order, and on the real extract the join key was field 133 of 191 — the one
    column that explains the unmatch was the hardest thing in the payload to
    find.
    """
    left = pd.DataFrame(
        {**{f"c{i:02d}": ["x", "y"] for i in range(20)}, "country": ["FRANCE", "SPAIN"]}
    )
    right = pd.DataFrame({"country": ["France"], "place": ["Paris"]})
    return table(left, "wide_left"), table(right, "wide_right")


def test_an_example_row_carries_its_key_first_then_a_capped_few(wide):
    left, right = wide
    findings = join_findings(left, right, on="country")

    columns = findings["evidence"]["example_row_columns"]["left"]
    assert columns[0] == "country", "the key explains the unmatch; it leads"
    assert len(columns) == 1 + SAMPLE_ROW_COLUMNS
    assert columns[1:] == ["c00", "c01", "c02", "c03", "c04"], "schema order after the key"

    for row in findings["evidence"]["unmatched_left_rows"]:
        assert list(row) == columns, "every row the same columns, so they read as a table"


def test_example_row_columns_says_what_you_got(wide):
    """21 columns in, 6 out. Three rows of six look like a six-column table."""
    left, right = wide
    findings = join_findings(left, right, on="country")

    assert len(left.columns) == 21
    assert findings["evidence"]["example_row_columns"]["left"] == list(
        findings["evidence"]["unmatched_left_rows"][0]
    )


def test_naming_columns_overrides_the_cap_and_still_prepends_the_key(wide):
    left, right = wide
    findings = join_findings(left, right, on="country", left_columns=["c19", "country", "c00"])

    # 'country' named explicitly is not printed twice, and the two sides are
    # named separately because they are two schemas.
    assert findings["evidence"]["example_row_columns"]["left"] == ["country", "c19", "c00"]
    assert findings["evidence"]["example_row_columns"]["right"] == ["country", "place"]


def test_a_column_that_is_not_there_is_refused_with_the_near_miss(wide):
    left, right = wide
    with pytest.raises(ValueError, match="countr"):
        join_findings(left, right, on="country", left_columns=["countr"])


def test_a_narrow_table_is_unchanged_by_the_cap(findings):
    """Under the cap nothing is dropped, so the fixtures above still see it all."""
    assert findings["evidence"]["example_row_columns"]["left"] == [
        "customer_id",
        "order_id",
        "amount",
    ]


def test_the_example_rows_quote_a_key_as_the_table_spells_it_not_as_it_was_typed(con):
    """The report resolved ``ID`` to ``id`` and the rows below it quoted ``"ID"``.

    DuckDB matches a quoted name without case, so nothing here could fail on it;
    PostgreSQL answered *no such column*. The SQL is what is checked, because the
    SQL is what another engine reads.
    """
    from portia.core.table import Table

    left = Table.from_frame(pd.DataFrame({"ORDER_ID": [1, 2, 3]}), "l", con)
    right = Table.from_frame(pd.DataFrame({"id": [1]}), "r", con)
    asked: list[str] = []

    class Spy:
        def __getattr__(self, name):
            return getattr(con, name)

        def execute(self, sql):
            asked.append(sql)
            return con.execute(sql)

    found = join_findings(left.using(Spy()), right.using(Spy()), left_on="order_id", right_on="ID")
    assert [r["ORDER_ID"] for r in found["evidence"]["unmatched_left_rows"]] == [2, 3]
    assert not [sql for sql in asked if '"ID"' in sql or '"order_id"' in sql]

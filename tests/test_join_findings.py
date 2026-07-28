"""join_findings surfaces facts + example rows — no ranking, no recommendation."""

import json

import pytest

from portia.checks.join import join_findings
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

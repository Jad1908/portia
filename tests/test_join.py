"""The join check must predict, from keys alone, exactly what a join would do.

Numbers are hand-computed against the sales fixture (see its docstring):
orders(left) ⋈ customers(right) on customer_id.
"""

import json

import pandas as pd
import pytest

from portia.checks.join import join_report, render_text
from portia.core.serialize import to_json
from portia.fixtures import sales_customers, sales_orders


@pytest.fixture(scope="module")
def report() -> dict:
    return join_report(sales_orders(), sales_customers(), on="customer_id")


def test_relationship_is_many_to_many(report):
    # 1001 duplicated in customers AND has two orders -> many:many.
    assert report["relationship"] == "many:many"


def test_result_rows_by_join_type(report):
    # inner = Σ mult_left*mult_right over shared keys
    #       = 1000:1*1 + 1001:2*2 + 1002:2*1 + 1003:1*1 = 8
    assert report["joins"]["inner"]["result_rows"] == 8
    assert report["joins"]["left"]["result_rows"] == 10  # + 2 unmatched left
    assert report["joins"]["right"]["result_rows"] == 9  # + 1 unmatched right
    assert report["joins"]["outer"]["result_rows"] == 11  # + both


def test_inner_join_silently_drops_left_rows(report):
    # order 9005 (customer 7777, orphan) + order 9006 (null key) = 2 dropped.
    inner = report["joins"]["inner"]
    assert inner["left_dropped"] == 2
    assert inner["right_dropped"] == 1  # customer 1004 has no orders
    assert "left_rows_dropped" in report["flags"]


def test_fan_out_detected(report):
    assert report["fan_out"]["max_left_to_right"] == 2
    assert "fan_out" in report["flags"]


def test_null_keys_flagged(report):
    assert report["left"]["n_null_keys"] == 1
    assert "null_keys" in report["flags"]


def test_overlap_and_samples(report):
    ov = report["overlap"]
    assert ov["n_shared_keys"] == 4
    assert ov["n_left_only_keys"] == 1 and ov["n_right_only_keys"] == 1
    assert ov["left_coverage"] == 0.75  # 6 of 8 order rows match
    assert to_jsonable_ok(ov["sample_left_only"])  # 7777 present, JSON-safe


def to_jsonable_ok(values) -> bool:
    json.dumps(values)  # must not raise
    return len(values) == 1


def test_key_dtype_mismatch_flagged():
    # '123' (string) never matches 123 (int) — silent zero-match bug.
    left = pd.DataFrame({"k": ["1", "2", "3"], "v": [1, 2, 3]})
    right = pd.DataFrame({"k": [1, 2, 3], "w": [9, 8, 7]})
    rep = join_report(left, right, on="k")
    assert rep["key_dtype_match"] is False
    assert "key_dtype_mismatch" in rep["flags"]


def test_clean_one_to_one():
    left = pd.DataFrame({"id": [1, 2, 3], "a": ["x", "y", "z"]})
    right = pd.DataFrame({"id": [1, 2, 3], "b": [10, 20, 30]})
    rep = join_report(left, right, on="id")
    assert rep["relationship"] == "1:1"
    assert rep["joins"]["inner"]["result_rows"] == 3
    assert rep["flags"] == []


def test_different_key_names():
    left = pd.DataFrame({"cust": [1, 2], "a": ["x", "y"]})
    right = pd.DataFrame({"customer_id": [1, 2], "b": [10, 20]})
    rep = join_report(left, right, left_on="cust", right_on="customer_id")
    assert rep["joins"]["inner"]["result_rows"] == 2


def test_missing_key_raises():
    left = pd.DataFrame({"id": [1]})
    right = pd.DataFrame({"other": [1]})
    with pytest.raises(ValueError, match="missing key column"):
        join_report(left, right, on="id")


def test_report_is_json_serializable_and_renders(report):
    assert json.loads(to_json(report))["relationship"] == "many:many"
    assert "many:many" in render_text(report)

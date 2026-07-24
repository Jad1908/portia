"""The spec runs, records decisions, and catches drift when a source changes."""

import pandas as pd
import pytest

from portia.checks.join import join_report
from portia.fixtures import sales_customers, sales_orders
from portia.spec import join_step, load_spec, run_spec, save_spec


@pytest.fixture
def project(tmp_path):
    """A tiny project on disk: two source CSVs, paths relative to tmp_path."""
    sales_orders().to_csv(tmp_path / "orders.csv", index=False)
    sales_customers().to_csv(tmp_path / "customers.csv", index=False)
    spec = {
        "version": 1,
        "sources": {"orders": "orders.csv", "customers": "customers.csv"},
        "steps": [
            {
                "id": "joined",
                "op": "join",
                "left": "orders",
                "right": "customers",
                "keys": ["customer_id"],
                "how": "left",
                "expect": {"result_rows": 10, "left_dropped": 0, "right_dropped": 1},
            }
        ],
    }
    return tmp_path, spec


def test_run_spec_executes_and_matches(project):
    tmp_path, spec = project
    results = run_spec(spec, base_dir=tmp_path)
    assert len(results) == 1
    r = results[0]
    assert r.provenance["result_rows"] == 10
    assert r.has_drift is False
    assert r.frame is not None and len(r.frame) == 10


def test_drift_detected_when_source_changes(project):
    tmp_path, spec = project
    # A new order arrives -> the left join now yields 11 rows, not the expected 10.
    orders = sales_orders()
    orders.loc[len(orders)] = {"order_id": 9999, "customer_id": 1000, "amount": 5}
    orders.to_csv(tmp_path / "orders.csv", index=False)

    r = run_spec(spec, base_dir=tmp_path)[0]
    assert r.has_drift is True
    assert r.drift["result_rows"] == {"expected": 10, "actual": 11}


def test_join_step_records_prediction_as_expectation():
    # decide -> record: the expect block comes straight from the report.
    report = join_report(sales_orders(), sales_customers(), on="customer_id")
    step = join_step(
        "joined", left="orders", right="customers", on="customer_id", how="left", report=report
    )
    assert step["keys"] == "customer_id"
    assert step["expect"] == {"result_rows": 10, "left_dropped": 0, "right_dropped": 1}


def test_spec_yaml_round_trips(tmp_path):
    spec = {"version": 1, "sources": {"a": "a.csv"}, "steps": []}
    save_spec(spec, tmp_path / "s.yaml")
    assert load_spec(tmp_path / "s.yaml") == spec


def test_committed_example_spec_is_valid():
    from pathlib import Path

    spec = load_spec(Path(__file__).resolve().parents[1] / "specs" / "sales_join.yaml")
    assert spec["steps"][0]["op"] == "join"


def test_unknown_op_raises(tmp_path):
    pd.DataFrame({"k": [1]}).to_csv(tmp_path / "a.csv", index=False)
    spec = {"sources": {"a": "a.csv"}, "steps": [{"id": "x", "op": "pivot", "left": "a"}]}
    with pytest.raises(ValueError, match="unknown op"):
        run_spec(spec, base_dir=tmp_path)

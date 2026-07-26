"""The spec runs, records decisions, and catches drift when a source changes."""

import pandas as pd
import pytest

from portia.fixtures import sales_customers, sales_orders
from portia.spec import load_spec, render_text, run_spec, save_spec


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


def test_rationale_is_carried_and_rendered(project):
    tmp_path, spec = project
    spec["steps"][0]["rationale"] = "left join: keep unmatched orders rather than drop them"
    r = run_spec(spec, base_dir=tmp_path)[0]
    assert r.rationale == "left join: keep unmatched orders rather than drop them"
    assert "why:" in render_text([r])


def test_rationale_is_optional(project):
    tmp_path, spec = project  # no rationale on the step
    r = run_spec(spec, base_dir=tmp_path)[0]
    assert r.rationale is None
    assert "why:" not in render_text([r])


def test_spec_yaml_round_trips(tmp_path):
    spec = {"version": 1, "sources": {"a": "a.csv"}, "steps": []}
    save_spec(spec, tmp_path / "s.yaml")
    assert load_spec(tmp_path / "s.yaml") == spec


def test_committed_example_spec_is_valid():
    from pathlib import Path

    spec = load_spec(Path(__file__).resolve().parents[1] / "specs" / "sales_join.yaml")
    assert spec["steps"][0]["op"] == "join"


def test_every_step_carries_the_post_conditions_of_what_it_produced(project):
    """`provenance` says what the op did; `outcome` says what came out.

    Both are needed, and only the second would have caught a run declaring
    success on a table missing an entire source (docs/EVALUATION.md).
    """
    tmp_path, spec = project
    r = run_spec(spec, base_dir=tmp_path)[0]

    assert r.outcome["n_rows"] == 10
    assert r.outcome["contribution"]["customers"]["contributed"] is True
    assert r.blocking == []
    assert "produced 10 rows" in render_text([r])


def test_a_grain_claim_is_measured_and_rendered(project):
    """Customer 1001 is duplicated, so the join multiplies two orders."""
    tmp_path, spec = project
    spec["steps"][0]["grain"] = ["order_id"]

    r = run_spec(spec, base_dir=tmp_path)[0]
    assert r.outcome["grain"]["unique"] is False
    assert r.blocking == ["grain_not_unique"]
    assert "not unique" in render_text([r])


def test_an_acknowledged_zero_stops_blocking_but_stays_visible(project):
    tmp_path, spec = project
    spec["steps"][0]["grain"] = ["order_id"]
    spec["steps"][0]["acknowledge"] = ["grain_not_unique"]

    r = run_spec(spec, base_dir=tmp_path)[0]
    assert r.blocking == []  # the human's call, taken
    assert r.outcome["grain"]["unique"] is False  # but the fact is unchanged
    assert "acknowledged: grain_not_unique" in render_text([r])


def test_unknown_op_raises(tmp_path):
    pd.DataFrame({"k": [1]}).to_csv(tmp_path / "a.csv", index=False)
    spec = {"sources": {"a": "a.csv"}, "steps": [{"id": "x", "op": "pivot", "left": "a"}]}
    with pytest.raises(ValueError, match="unknown op"):
        run_spec(spec, base_dir=tmp_path)


def test_a_sql_step_runs_chains_and_shows_its_query(project):
    """The escape hatch through the spec: aggregate, then join the aggregate.

    Rendering the SQL in full is deliberate — for a custom step the query *is*
    the decision, and a reader skimming a run shouldn't have to open the YAML to
    see what it did.
    """
    tmp_path, spec = project
    spec["steps"] = [
        {
            "id": "orders_per_customer",
            "op": "sql",
            "inputs": ["orders"],
            "sql": "SELECT customer_id, COUNT(*) AS n_orders FROM orders GROUP BY 1",
            "grain": ["customer_id"],
        },
        {
            "id": "enriched",
            "op": "join",
            "left": "customers",
            "right": "orders_per_customer",  # the sql step's output, by id
            "keys": ["customer_id"],
            "how": "left",
        },
    ]
    results = run_spec(spec, base_dir=tmp_path)

    assert results[0].provenance["op"] == "sql"
    assert results[0].outcome["grain"]["unique"] is True
    assert "n_orders" in results[1].frame.columns  # it really chained

    text = render_text(results)
    assert "COUNT(*) AS n_orders" in text

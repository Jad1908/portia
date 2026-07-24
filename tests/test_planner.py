"""The planner turns a diagnosis into ranked decisions + a proposed step,
and refuses to propose when there's a blocker."""

import pandas as pd

from portia.fixtures import sales_customers, sales_orders
from portia.planner import propose_join_step
from portia.spec import add_step, run_spec


def _propose_sales():
    return propose_join_step(
        sales_orders(),
        sales_customers(),
        step_id="orders_customers",
        left_name="orders",
        right_name="customers",
        on="customer_id",
    )


def test_recommends_left_when_inner_would_drop_rows():
    # inner drops 2 unmatched left rows -> conservative default keeps them.
    p = _propose_sales()
    assert p.step["how"] == "left"
    assert p.blocked is False


def test_surfaces_the_decisions_that_matter():
    topics = {d.topic for d in _propose_sales().decisions}
    assert {"join_type", "fan_out", "null_keys"} <= topics


def test_decisions_ranked_by_impact():
    decisions = _propose_sales().decisions
    # join_type (2 rows dropped) outranks null_keys (1 row), both above nothing.
    order = [d.topic for d in decisions]
    assert order.index("join_type") < order.index("null_keys")


def test_dtype_mismatch_is_a_blocker():
    left = pd.DataFrame({"k": ["1", "2", "3"], "v": [1, 2, 3]})
    right = pd.DataFrame({"k": [1, 2, 3], "w": [9, 8, 7]})
    p = propose_join_step(left, right, step_id="j", left_name="l", right_name="r", on="k")
    assert p.blocked is True
    blocker = next(d for d in p.decisions if d.severity == "blocker")
    assert blocker.topic == "key_dtype"
    # blockers rank first
    assert p.decisions[0].severity == "blocker"


def test_clean_join_recommends_inner_with_minimal_decisions():
    left = pd.DataFrame({"id": [1, 2, 3], "a": ["x", "y", "z"]})
    right = pd.DataFrame({"id": [1, 2, 3], "b": [10, 20, 30]})
    p = propose_join_step(left, right, step_id="j", left_name="l", right_name="r", on="id")
    assert p.step["how"] == "inner"
    assert p.blocked is False
    assert [d.topic for d in p.decisions] == ["join_type"]  # only the base decision
    assert p.decisions[0].severity == "info"


def test_proposed_step_runs_without_drift():
    # decide -> record -> run: the recorded expect matches a fresh run.
    p = _propose_sales()
    spec = add_step(None, p.step, {"orders": "orders.csv", "customers": "customers.csv"})
    # write sources where the spec expects them, then run
    import os
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        sales_orders().to_csv(os.path.join(d, "orders.csv"), index=False)
        sales_customers().to_csv(os.path.join(d, "customers.csv"), index=False)
        results = run_spec(spec, base_dir=d)
    assert results[0].has_drift is False


def test_add_step_builds_multistep_spec():
    p = _propose_sales()
    spec = add_step(None, p.step, {"orders": "o.csv", "customers": "c.csv"})
    spec2 = add_step(
        spec,
        {"id": "s2", "op": "join", "left": "a", "right": "b", "keys": ["k"]},
        {"a": "a.csv", "b": "b.csv"},
    )
    assert len(spec2["steps"]) == 2
    assert set(spec2["sources"]) == {"orders", "customers", "a", "b"}
    assert spec2["version"] == 1

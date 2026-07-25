"""The agent's view of the engine — evidence in, JSON out.

These run without `claude-agent-sdk` and without a model: handlers are plain
functions on purpose, so the surface the copilot depends on is testable for
free. The JSON assertion is the load-bearing one — an `int64` or a `NaN` reaching
a tool result breaks the loop at runtime with no useful error.
"""

import json

import pytest
import yaml

from portia.agent import handlers
from portia.catalog import index_source, init_project
from portia.fixtures import messy_customers, sales_customers, sales_orders


@pytest.fixture
def project(tmp_path):
    """An indexed one-source project; returns the catalog dir."""
    csv = tmp_path / "customers.csv"
    messy_customers().to_csv(csv, index=False)
    d = tmp_path / ".portia"
    init_project("we run EU events and reconcile vendor signups", portia_dir=d)
    index_source(csv, portia_dir=d)
    return str(d)


@pytest.fixture
def sales(tmp_path, monkeypatch):
    """Two indexed sources built to fire: an orphan key, a null key, a dup key.

    Runs from ``tmp_path`` so the spec's relative source paths resolve the way
    they would in a real project.
    """
    monkeypatch.chdir(tmp_path)
    sales_orders().to_csv("orders.csv", index=False)
    sales_customers().to_csv("customers.csv", index=False)
    d = tmp_path / ".portia"
    init_project("order reconciliation", portia_dir=d)
    index_source("orders.csv", portia_dir=d)
    index_source("customers.csv", portia_dir=d)
    return str(d)


def test_get_context_is_compact_and_jsonable(project):
    ctx = handlers.get_context(project)
    json.dumps(ctx)  # must not raise

    assert ctx["project"].startswith("we run EU events")
    entry = ctx["sources"]["customers"]
    assert entry["n_columns"] > 0
    assert "customer_id" in entry["candidate_keys"]
    # compact by design: the source index carries no per-column detail
    assert "columns" not in entry
    # freshly indexed sources still carry the auto-drafted placeholder read
    assert entry["interpreted"] is False


def test_profile_source_returns_facts_and_role_slots(project):
    entry = handlers.profile_source("customers", project)
    json.dumps(entry)

    col = next(c for c in entry["columns"] if c["name"] == "signup_amount")
    assert "numeric_stored_as_text" in col["flags"]  # a fact
    assert col["role"] is None  # judgment, not yet written

    # the checks hand over facts unranked — no scoring/priority sneaking in
    assert not {"score", "priority", "impact", "severity"} & set(col)


def test_profile_source_gives_the_full_check_facts_not_the_stored_slice(project):
    """The catalog trims what it stores; the agent must get everything.

    Regression: with only median/std in hand the model *derived* a min-max range
    it had never been given. Anything it can't measure it will estimate, so the
    fix is more evidence, not a sterner prompt.
    """
    entry = handlers.profile_source("customers", project)
    key = next(c for c in entry["columns"] if c["name"] == "customer_id")

    for fact in ("min", "max", "q25", "median", "q75", "mean", "std", "samples"):
        assert fact in key, f"{fact} missing — the agent would have to guess it"
    assert entry["n_rows"] == 40


def test_profile_source_names_what_is_available_when_missing(project):
    with pytest.raises(ValueError, match="customers"):
        handlers.profile_source("nope", project)


def test_set_interpretation_records_judgment(project):
    out = handlers.set_interpretation(
        "customers",
        summary="Vendor signups for EU events, one row per customer.",
        roles={"customer_id": "identifier"},
        portia_dir=project,
    )
    json.dumps(out)
    assert out["summary_written"] is True
    assert out["roles_written"] == ["customer_id"]

    after = handlers.profile_source("customers", project)
    assert after["summary"].startswith("Vendor signups")
    assert next(c for c in after["columns"] if c["name"] == "customer_id")["role"] == "identifier"
    assert handlers.get_context(project)["sources"]["customers"]["interpreted"] is True


def test_set_interpretation_rejects_an_empty_write(project):
    with pytest.raises(ValueError, match="nothing to record"):
        handlers.set_interpretation("customers", portia_dir=project)


# --- the merge loop ---------------------------------------------------------


def test_join_findings_surfaces_the_problems_without_ranking_them(sales):
    out = handlers.join_findings("orders", "customers", keys=["customer_id"], portia_dir=sales)
    json.dumps(out)

    report, evidence = out["report"], out["evidence"]
    # the facts the fixtures are built to expose
    assert report["overlap"]["n_left_only_keys"] == 1  # orphan 7777
    assert report["left"]["n_null_keys"] == 1
    assert not report["right"]["unique_keys"]  # customer 1001 is duplicated
    assert "fan_out" in report["flags"]
    # example rows, so the human can see what would be dropped
    assert evidence["unmatched_left_rows"] and evidence["null_key_left_rows"]

    # facts only — the check never says which of these matters
    assert not {"score", "priority", "impact", "recommendation"} & set(report)


def test_record_step_writes_a_runnable_spec_and_registers_sources(sales, tmp_path):
    out = handlers.record_step(
        "specs/orders.yaml",
        {
            "id": "orders_with_customers",
            "op": "join",
            "left": "orders",
            "right": "customers",
            "keys": ["customer_id"],
            "how": "left",
            "rationale": "keep unmatched orders rather than silently dropping them",
            "expect": {"result_rows": 10, "left_dropped": 0},
        },
        portia_dir=sales,
    )
    json.dumps(out)

    doc = yaml.safe_load((tmp_path / "specs" / "orders.yaml").read_text())
    assert doc["sources"] == {"orders": "orders.csv", "customers": "customers.csv"}
    assert doc["steps"][0]["how"] == "left"

    # and it actually runs, with the prediction holding
    run = handlers.run_spec("specs/orders.yaml")
    json.dumps(run)
    assert run["has_drift"] is False
    assert run["steps"][0]["provenance"]["result_rows"] == 10


def test_run_spec_reports_drift_rather_than_hiding_it(sales):
    handlers.record_step(
        "specs/orders.yaml",
        {
            "id": "j",
            "op": "join",
            "left": "orders",
            "right": "customers",
            "keys": ["customer_id"],
            "how": "left",
            "expect": {"result_rows": 999},  # a prediction that won't hold
        },
        portia_dir=sales,
    )
    run = handlers.run_spec("specs/orders.yaml")
    assert run["has_drift"] is True
    assert run["steps"][0]["drift"]["result_rows"] == {"expected": 999, "actual": 10}


@pytest.mark.parametrize(
    ("step", "message"),
    [
        ({"op": "join", "left": "orders", "right": "customers"}, "needs an 'id'"),
        ({"id": "x", "op": "frobnicate"}, "unknown op"),
        ({"id": "x", "op": "join", "left": "orders"}, "needs right"),
        ({"id": "x", "op": "join", "left": "orders", "right": "customers"}, "needs 'keys'"),
        ({"id": "x", "op": "normalize", "input": "orders"}, "needs transforms"),
    ],
)
def test_record_step_rejects_a_malformed_step(sales, step, message):
    """Validation is code's job; what the step *says* is the agent's."""
    with pytest.raises(ValueError, match=message):
        handlers.record_step("specs/orders.yaml", step, portia_dir=sales)


def test_record_step_rejects_an_expect_on_a_field_the_op_never_reports(sales):
    """Regression: an invented `expect` key drifts forever and teaches you to ignore drift.

    A real run produced `expect: {left_rows_kept: 8, right_rows_joined: 5, ...}`
    — none of which a join reports — then explained the resulting drift away as
    "nominal". Which fields to assert is judgment; whether a field *exists* is a
    fact, so it's checked here rather than hoped for in the prompt.
    """
    with pytest.raises(ValueError, match="never reports"):
        handlers.record_step(
            "specs/orders.yaml",
            {
                "id": "x",
                "op": "join",
                "left": "orders",
                "right": "customers",
                "keys": ["customer_id"],
                "expect": {"result_rows": 10, "left_rows_kept": 8},
            },
            portia_dir=sales,
        )


def test_record_step_rejects_a_duplicate_id(sales):
    step = {
        "id": "dup",
        "op": "join",
        "left": "orders",
        "right": "customers",
        "keys": ["customer_id"],
    }
    handlers.record_step("specs/orders.yaml", step, portia_dir=sales)
    with pytest.raises(ValueError, match="already in this spec"):
        handlers.record_step("specs/orders.yaml", dict(step), portia_dir=sales)

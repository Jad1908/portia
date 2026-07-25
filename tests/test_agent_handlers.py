"""The agent's view of the engine — evidence in, JSON out.

These run without `claude-agent-sdk` and without a model: handlers are plain
functions on purpose, so the surface the copilot depends on is testable for
free. The JSON assertion is the load-bearing one — an `int64` or a `NaN` reaching
a tool result breaks the loop at runtime with no useful error.
"""

import json

import pytest

from portia.agent import handlers
from portia.catalog import index_source, init_project
from portia.fixtures import messy_customers


@pytest.fixture
def project(tmp_path):
    """An indexed one-source project; returns the catalog dir."""
    csv = tmp_path / "customers.csv"
    messy_customers().to_csv(csv, index=False)
    d = tmp_path / ".portia"
    init_project("we run EU events and reconcile vendor signups", portia_dir=d)
    index_source(csv, portia_dir=d)
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

"""The agent's view of the engine — evidence in, JSON out.

These run without `claude-agent-sdk` and without a model: handlers are plain
functions on purpose, so the surface the copilot depends on is testable for
free. The JSON assertion is the load-bearing one — an `int64` or a `NaN` reaching
a tool result breaks the loop at runtime with no useful error.
"""

import json

import pandas as pd
import pytest
import yaml

from portia import spec
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


def test_a_recorded_step_is_immutable_and_the_message_does_not_teach_the_workaround(sales):
    """Regression: a run tried to rewrite `expect` to match the result it got.

    It was stopped only by accident — duplicate-id checking — and the message it
    read said "pick another", which is an instruction for how to get around the
    rule. Recording `join_v2` would have worked.
    """
    step = {
        "id": "dup",
        "op": "join",
        "left": "orders",
        "right": "customers",
        "keys": ["customer_id"],
    }
    handlers.record_step("specs/orders.yaml", step, portia_dir=sales)
    with pytest.raises(ValueError, match="append-only") as exc:
        handlers.record_step("specs/orders.yaml", dict(step), portia_dir=sales)

    assert "pick another" not in str(exc.value)


# --- progressive disclosure -------------------------------------------------


def test_describe_source_is_the_cheap_semantic_rung(project):
    """L2 carries meaning, not measurements — that's what makes it cheap."""
    from portia.agent import handlers as h

    described = h.describe_source("customers", project)
    json.dumps(described)

    col = next(c for c in described["columns"] if c["name"] == "signup_amount")
    assert set(col) == {"name", "role", "inferred", "flags"}
    assert "numeric_stored_as_text" in col["flags"]

    # L3 is strictly richer — the rungs must actually differ, or the ladder is theatre
    profiled = next(
        c for c in h.profile_source("customers", project)["columns"] if c["name"] == "signup_amount"
    )
    assert set(col) < set(profiled)
    assert "samples" in profiled and "samples" not in col


def test_set_group_records_shared_context_and_membership(project):
    out = handlers.set_group(
        "vendor_feed",
        context="Everything the vendor sends us, same export quirks.",
        sources=["customers"],
        portia_dir=project,
    )
    json.dumps(out)

    ctx = handlers.get_context(project)
    group = ctx["groups"][0]
    assert group["name"] == "vendor_feed"
    assert group["sources"] == ["customers"]
    assert "export quirks" in group["context"]


def test_set_group_rejects_an_unindexed_source(project):
    with pytest.raises(ValueError, match="no indexed source"):
        handlers.set_group("g", sources=["nope"], portia_dir=project)


def test_set_group_rejects_an_empty_write(project):
    with pytest.raises(ValueError, match="nothing to record"):
        handlers.set_group("g", portia_dir=project)


# --- regressions from the hotel-fixture run ---------------------------------


def test_record_step_can_chain_from_an_earlier_step(sales, tmp_path):
    """Multi-hop work: a later step consumes an earlier step's output by id.

    Regression: `record_step`'s tool description never said this was possible, so
    the copilot concluded the spec format couldn't express a two-hop join, wrote a
    degraded single-hop version, and advised the user to go implement it in dbt.
    The engine had the capability all along; only the description was missing.
    """
    handlers.record_step(
        "specs/chain.yaml",
        {
            "id": "bridged",
            "op": "join",
            "left": "orders",
            "right": "customers",
            "keys": ["customer_id"],
            "how": "left",
        },
        portia_dir=sales,
    )
    out = handlers.record_step(
        "specs/chain.yaml",
        {
            "id": "second_hop",
            "op": "normalize",
            "input": "bridged",  # <- the earlier step, not an indexed source
            "transforms": [{"column": "name", "op": "strip"}],
        },
        portia_dir=sales,
    )
    assert out["n_steps"] == 2

    doc = yaml.safe_load((tmp_path / "specs" / "chain.yaml").read_text())
    assert "bridged" not in doc["sources"]  # a step is not registered as a source

    run = handlers.run_spec("specs/chain.yaml")
    assert [s["id"] for s in run["steps"]] == ["bridged", "second_hop"]
    assert run["has_drift"] is False


def test_record_step_names_chainable_steps_when_a_ref_is_unknown(sales):
    """A bad ref must not read as 'chaining is unsupported'."""
    handlers.record_step(
        "specs/chain.yaml",
        {
            "id": "bridged",
            "op": "join",
            "left": "orders",
            "right": "customers",
            "keys": ["customer_id"],
            "how": "left",
        },
        portia_dir=sales,
    )
    with pytest.raises(ValueError, match="Earlier steps you can chain from: bridged"):
        handlers.record_step(
            "specs/chain.yaml",
            {
                "id": "x",
                "op": "normalize",
                "input": "typo",
                "transforms": [{"column": "name", "op": "strip"}],
            },
            portia_dir=sales,
        )


def test_join_findings_can_measure_a_table_an_earlier_step_produced(sales):
    """Hop 2 must be measurable before it is committed to, like hop 1.

    Regression (EVALUATION.md, Run 3): `join_findings` resolved names through the
    catalog only, so an intermediate result — not a file, therefore not indexed —
    was unreachable. "Always measure before deciding" was impossible to obey from
    the second hop onward, and the agent recorded blind instead.
    """
    handlers.record_step(
        "specs/chain.yaml",
        {
            "id": "orders_named",
            "op": "join",
            "left": "orders",
            "right": "customers",
            "keys": ["customer_id"],
            "how": "left",
        },
        portia_dir=sales,
    )
    out = handlers.join_findings(
        "specs/chain.yaml#orders_named", "customers", keys=["customer_id"], portia_dir=sales
    )
    json.dumps(out)
    assert out["report"]["left"]["n_rows"] == 10  # the joined table, not the 8-row source


def test_a_step_reference_only_runs_the_spec_up_to_that_step(sales):
    """A later step may be the very thing being diagnosed, and may not run yet."""
    handlers.record_step(
        "specs/chain.yaml",
        {
            "id": "first",
            "op": "join",
            "left": "orders",
            "right": "customers",
            "keys": ["customer_id"],
            "how": "left",
        },
        portia_dir=sales,
    )
    # hand-append a step that cannot run; diagnosing `first` must not touch it
    from pathlib import Path

    path = Path("specs/chain.yaml")
    doc = yaml.safe_load(path.read_text())
    doc["steps"].append(
        {
            "id": "broken",
            "op": "normalize",
            "input": "first",
            "transforms": [{"column": "no_such_column", "op": "strip"}],
        }
    )
    path.write_text(yaml.safe_dump(doc))

    out = handlers.join_findings(
        "specs/chain.yaml#first", "customers", keys=["customer_id"], portia_dir=sales
    )
    assert out["report"]["left"]["n_rows"] == 10


def test_an_unknown_table_name_points_at_the_step_form(sales):
    """Otherwise the message reads 'no such table' when the truth is 'not by that name'."""
    with pytest.raises(ValueError, match="spec path.*#.*step id"):
        handlers.join_findings("otb_hotels", "customers", keys=["customer_id"], portia_dir=sales)


def test_an_unknown_step_id_names_the_steps_that_exist(sales):
    handlers.record_step(
        "specs/chain.yaml",
        {
            "id": "first",
            "op": "join",
            "left": "orders",
            "right": "customers",
            "keys": ["customer_id"],
            "how": "left",
        },
        portia_dir=sales,
    )
    with pytest.raises(ValueError, match="no step 'typo' in specs/chain.yaml — have: first"):
        handlers.join_findings(
            "specs/chain.yaml#typo", "customers", keys=["customer_id"], portia_dir=sales
        )


# --- the verification loop ---------------------------------------------------


@pytest.fixture
def orphans(tmp_path, monkeypatch):
    """Two sources whose keys match nothing — Run 2's failure, in miniature.

    The copilot normalized one side of a join key and not the other. The join
    matched nothing, it predicted the row count correctly so drift was clean,
    and it shipped a table whose event columns were null in every row.
    """
    monkeypatch.chdir(tmp_path)
    sales_orders().to_csv("orders.csv", index=False)
    pd.DataFrame({"customer_id": [90001, 90002], "name": ["Nobody", "Nowhere"]}).to_csv(
        "strangers.csv", index=False
    )
    d = tmp_path / ".portia"
    init_project("order reconciliation", portia_dir=d)
    index_source("orders.csv", portia_dir=d)
    index_source("strangers.csv", portia_dir=d)
    return str(d)


def _unmatched_join(**extra):
    return {
        "id": "orders_x_strangers",
        "op": "join",
        "left": "orders",
        "right": "strangers",
        "keys": ["customer_id"],
        "how": "left",
        # The prediction is *correct* — 8 left rows, none matched. Drift is clean
        # and says nothing at all about the table being useless.
        "expect": {"result_rows": 8},
        **extra,
    }


def test_a_step_whose_output_loses_a_source_is_refused_and_nothing_is_written(orphans, tmp_path):
    with pytest.raises(ValueError, match="source_did_not_contribute"):
        handlers.record_step("specs/x.yaml", _unmatched_join(), portia_dir=orphans)

    assert not (tmp_path / "specs" / "x.yaml").exists(), "a refused step must leave no residue"


def test_the_refusal_hands_back_the_measurements_not_just_a_verdict(orphans):
    """It has to be able to act on this, so it gets the facts, generously."""
    with pytest.raises(ValueError) as exc:
        handlers.record_step("specs/x.yaml", _unmatched_join(), portia_dir=orphans)

    message = str(exc.value)
    assert '"contributed": false' in message
    assert "name" in message  # the column that came out empty
    assert "acknowledge" in message  # and the way out, if it's deliberate


def test_a_correct_prediction_does_not_rescue_a_broken_join(orphans):
    """The exact hole: `expect` held perfectly and the table was still wrong."""
    with pytest.raises(ValueError):
        handlers.record_step("specs/x.yaml", _unmatched_join(), portia_dir=orphans)

    # prove the prediction really was right, so drift alone would have passed it
    run = spec.run_spec(
        {
            "sources": {"orders": "orders.csv", "strangers": "strangers.csv"},
            "steps": [_unmatched_join()],
        }
    )
    assert run[0].has_drift is False
    assert run[0].blocking == ["all_null_column", "source_did_not_contribute"]


def test_an_acknowledged_zero_is_written_and_visible_in_the_spec(orphans, tmp_path):
    """Override stays possible — but it lands in the YAML the user reads."""
    out = handlers.record_step(
        "specs/x.yaml",
        _unmatched_join(
            acknowledge=["source_did_not_contribute", "all_null_column"],
            rationale="strangers is a future feed; no overlap with this window yet",
        ),
        portia_dir=orphans,
    )
    json.dumps(out)
    assert out["acknowledged"] == ["source_did_not_contribute", "all_null_column"]

    doc = yaml.safe_load((tmp_path / "specs" / "x.yaml").read_text())
    assert doc["steps"][0]["acknowledge"] == ["source_did_not_contribute", "all_null_column"]


def test_acknowledging_only_one_of_two_zeros_still_refuses(orphans):
    with pytest.raises(ValueError, match="all_null_column"):
        handlers.record_step(
            "specs/x.yaml",
            _unmatched_join(acknowledge=["source_did_not_contribute"]),
            portia_dir=orphans,
        )


def test_acknowledge_must_name_a_flag_that_can_actually_block(orphans):
    with pytest.raises(ValueError, match="never blocks"):
        handlers.record_step(
            "specs/x.yaml", _unmatched_join(acknowledge=["low_overlap"]), portia_dir=orphans
        )


def test_recording_returns_what_the_table_looks_like_not_just_that_it_saved(sales):
    out = handlers.record_step(
        "specs/orders.yaml",
        {
            "id": "j",
            "op": "join",
            "left": "orders",
            "right": "customers",
            "keys": ["customer_id"],
            "how": "left",
        },
        portia_dir=sales,
    )
    json.dumps(out)
    assert out["outcome"]["n_rows"] == 10
    assert out["outcome"]["contribution"]["customers"]["contributed"] is True


def test_a_grain_claim_that_does_not_hold_refuses_the_step(sales, tmp_path):
    """Customer 1001 is duplicated, so the join fans out and orders multiply.

    The fatal trap in the hotel fixture is this shape: the result still looks
    plausible, it's just silently double-counting.
    """
    with pytest.raises(ValueError, match="grain_not_unique") as exc:
        handlers.record_step(
            "specs/orders.yaml",
            {
                "id": "j",
                "op": "join",
                "left": "orders",
                "right": "customers",
                "keys": ["customer_id"],
                "how": "left",
                "grain": ["order_id"],  # one row per order — not true after the fan-out
            },
            portia_dir=sales,
        )
    assert "9001" in str(exc.value)  # the duplicated key, named
    assert not (tmp_path / "specs" / "orders.yaml").exists()


def test_a_step_that_cannot_run_is_caught_before_it_reaches_the_spec(sales, tmp_path):
    """Regression: a step naming a column that doesn't exist used to validate,
    get written to a durable artifact, and fail only when someone re-ran it."""
    with pytest.raises(ValueError, match="missing key column"):
        handlers.record_step(
            "specs/orders.yaml",
            {"id": "j", "op": "join", "left": "orders", "right": "customers", "keys": ["nope"]},
            portia_dir=sales,
        )
    assert not (tmp_path / "specs" / "orders.yaml").exists()


def test_run_spec_carries_the_outcome_alongside_drift(sales):
    handlers.record_step(
        "specs/orders.yaml",
        {
            "id": "j",
            "op": "join",
            "left": "orders",
            "right": "customers",
            "keys": ["customer_id"],
            "how": "left",
        },
        portia_dir=sales,
    )
    run = handlers.run_spec("specs/orders.yaml")
    json.dumps(run)

    step = run["steps"][0]
    assert step["outcome"]["n_rows"] == 10
    assert step["blocking"] == []
    assert run["blocking"] == []


@pytest.mark.parametrize(
    ("grain", "message"),
    [
        ("order_id", "non-empty list"),
        ([], "non-empty list"),
        ([1, 2], "non-empty list"),
    ],
)
def test_record_step_validates_the_shape_of_a_grain_claim(sales, grain, message):
    with pytest.raises(ValueError, match=message):
        handlers.record_step(
            "specs/orders.yaml",
            {
                "id": "j",
                "op": "join",
                "left": "orders",
                "right": "customers",
                "keys": ["customer_id"],
                "grain": grain,
            },
            portia_dir=sales,
        )


@pytest.mark.parametrize(
    ("transforms", "message"),
    [
        ([{"column": "name", "transform": "strip"}], "did you mean 'op'"),
        ([{"column": "name", "op": "uppercase"}], "unknown op"),
        ([{"op": "strip"}], "needs a 'column'"),
        (["strip"], "must be an object"),
    ],
)
def test_record_step_validates_the_shape_of_each_transform(sales, transforms, message):
    """Regression: `transform: strip` (not `op:`) validated, was written, and only
    failed with a bare KeyError when the spec was re-run — potentially months later.
    Checking the container and not its contents is the same bug as accepting an
    `expect` key no op reports."""
    with pytest.raises(ValueError, match=message):
        handlers.record_step(
            "specs/orders.yaml",
            {"id": "n", "op": "normalize", "input": "orders", "transforms": transforms},
            portia_dir=sales,
        )

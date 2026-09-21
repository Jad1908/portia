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

from portia import catalog, spec
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


def test_profile_source_columns_returns_only_what_was_asked_for(project):
    """A subset of the answer, never a subset of the measurement.

    The whole-table facts have to survive it: `n_rows` and `n_cols` are
    statements about the table, not about the subset that came back.
    """
    full = handlers.profile_source("customers", project)
    subset = handlers.profile_source("customers", project, columns=["signup_amount"])

    assert [c["name"] for c in subset["columns"]] == ["signup_amount"]
    assert subset["n_cols"] == full["n_cols"]
    assert subset["n_rows"] == full["n_rows"]

    # and the facts on the column that came back are the same ones as always
    assert next(c for c in full["columns"] if c["name"] == "signup_amount") == subset["columns"][0]


def test_profile_source_columns_keeps_the_order_asked_for_and_dedupes(project):
    out = handlers.profile_source(
        "customers", project, columns=["signup_amount", "customer_id", "signup_amount"]
    )
    assert [c["name"] for c in out["columns"]] == ["signup_amount", "customer_id"]


def test_profile_source_refuses_a_column_that_is_not_there(project):
    """Refused, not silently short.

    Returning what matched is the cheaper path and the wrong one: a profile is
    what the agent reasons from, and it has already shipped a deliverable
    without noticing it never received one (docs/EVALUATION.md, the AQN build
    run). The message names the near miss rather than every column, because on
    the 191-column source that list is 5,000 characters of the same problem.
    """
    with pytest.raises(ValueError, match="customer_i"):
        handlers.profile_source("customers", project, columns=["customer_i"])

    with pytest.raises(ValueError, match="describe_source"):
        handlers.profile_source("customers", project, columns=["nothing_like_it_at_all"])


def test_profile_source_columns_matches_the_case_the_engine_reports(project, monkeypatch):
    """`columns=['signup_amount']` against a source whose column is SIGNUP_AMOUNT
    — what every warehouse column looks like — is that column, not a refusal."""
    full = handlers.profile_source("customers", project)
    named = handlers.profile_source("customers", project, columns=["SIGNUP_AMOUNT"])
    assert [c["name"] for c in named["columns"]] == ["signup_amount"]
    assert named["columns"][0] == next(c for c in full["columns"] if c["name"] == "signup_amount")


def test_profile_source_columns_works_on_a_step_output(sales):
    """The `<spec>#<step id>` path takes it too — one filter, both branches."""
    handlers.record_step(
        "specs/chain.yaml",
        {
            "id": "cleaned",
            "op": "normalize",
            "input": "customers",
            "transforms": [{"column": "name", "op": "lower"}],
        },
        portia_dir=sales,
    )
    out = handlers.profile_source("specs/chain.yaml#cleaned", sales, columns=["name"])
    assert [c["name"] for c in out["columns"]] == ["name"]
    assert out["n_cols"] > 1, "still the whole table's width"


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

    doc = yaml.safe_load((tmp_path / "specs" / "orders.yaml").read_text(encoding="utf-8"))
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
        ({"id": "x", "op": "join", "left": "orders"}, "needs 'right'"),
        ({"id": "x", "op": "join", "left": "orders", "right": "customers"}, "needs 'keys'"),
        ({"id": "x", "op": "normalize", "input": "orders"}, "needs 'transforms'"),
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
    with pytest.raises(ValueError, match="does not report"):
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


def test_a_missing_field_names_the_one_the_step_sent_instead(sales):
    """Regression (2026-08-08): "sql step needs inputs", twice, for `input`.

    The step declared its table as `input` — normalize's field — so the required
    one was absent and the message was true. It was also unactionable: it said
    what was missing and never that something one letter away was present, so the
    retry changed nothing that mattered and read the identical sentence again.
    """
    with pytest.raises(ValueError) as raised:
        handlers.record_step(
            "specs/orders.yaml",
            {"id": "x", "op": "sql", "input": "orders", "sql": "select 1"},
            portia_dir=sales,
        )
    message = str(raised.value)
    assert "'input'" in message and "normalize" in message
    assert "'inputs'" in message


def test_expect_on_n_rows_says_which_report_that_word_belongs_to(sales):
    """Regression (2026-08-08): `expect: {n_rows: 1}` rejected as never reported.

    `n_rows` is not invented — it is in the `outcome` block of the result the
    model has just read. Being told the op "never reports" it is a contradiction
    of what is on screen, so the message has to name the *other* report the word
    comes from and the op's own equivalent (`CLAUDE.md` → drift and outcome are
    different questions).
    """
    with pytest.raises(ValueError) as raised:
        handlers.record_step(
            "specs/orders.yaml",
            {
                "id": "x",
                "op": "join",
                "left": "orders",
                "right": "customers",
                "keys": ["customer_id"],
                "expect": {"n_rows": 10},
            },
            portia_dir=sales,
        )
    message = str(raised.value)
    assert "outcome" in message
    assert "'result_rows'" in message


def test_a_duplicate_id_is_refused_and_the_message_does_not_teach_the_workaround(sales):
    """Regression: a run tried to rewrite `expect` to match the result it got.

    It was stopped only by accident — duplicate-id checking — and the message it
    read said "pick another", which is an instruction for how to get around the
    rule. Recording `join_v2` would have worked.

    **Append-only went in 2026-09-02** (`docs/FINDINGS.md` §7) and this test did
    not, because the thing it protects is not append-only. A correction now has a
    named route (`supersedes`), and what the message must still never say is
    *rename it* — a spec whose predictions get rewritten to match the result
    verifies nothing, whichever mechanism does the rewriting.
    """
    step = {
        "id": "dup",
        "op": "join",
        "left": "orders",
        "right": "customers",
        "keys": ["customer_id"],
    }
    handlers.record_step("specs/orders.yaml", step, portia_dir=sales)
    with pytest.raises(ValueError, match="supersedes") as exc:
        handlers.record_step("specs/orders.yaml", dict(step), portia_dir=sales)

    assert "pick another" not in str(exc.value)
    assert "may not" in str(exc.value)


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

    doc = yaml.safe_load((tmp_path / "specs" / "chain.yaml").read_text(encoding="utf-8"))
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
    with pytest.raises(ValueError, match="Earlier steps in this spec: bridged"):
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
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    doc["steps"].append(
        {
            "id": "broken",
            "op": "normalize",
            "input": "first",
            "transforms": [{"column": "no_such_column", "op": "strip"}],
        }
    )
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")

    out = handlers.join_findings(
        "specs/chain.yaml#first", "customers", keys=["customer_id"], portia_dir=sales
    )
    assert out["report"]["left"]["n_rows"] == 10


def test_record_step_accepts_the_same_reference_form_the_checks_need(sales, tmp_path):
    """One habit must work everywhere. Run 4 tripped over the seam three times.

    `join_findings` needs `<spec>#<step id>` (a step's output is not a file, so
    there is nothing else to call it); chaining inside a spec used to take only a
    bare id. The agent guessed wrong in both directions, burning a round-trip and
    a write confirmation each time.
    """
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
    out = handlers.record_step(
        "specs/chain.yaml",
        {
            "id": "second",
            "op": "normalize",
            "input": "specs/chain.yaml#first",  # the read-only checks' form
            "transforms": [{"column": "name", "op": "strip"}],
        },
        portia_dir=sales,
    )
    assert out["n_steps"] == 2

    # ...and the spec stores the bare id: a step naming its own spec by path,
    # inside that spec, is noise in a file whose point is reading well in a diff.
    doc = yaml.safe_load((tmp_path / "specs" / "chain.yaml").read_text(encoding="utf-8"))
    assert doc["steps"][1]["input"] == "first"
    assert "chain.yaml" not in doc["steps"][1]["input"]


def test_a_step_chains_from_another_spec_by_model_name(sales, tmp_path):
    """`docs/PIPELINE.md` §2.4 — this used to be refused, and now it is the point.

    One spec produces one table, so a downstream spec names that table and portia
    resolves it. The refusal this replaces said "a step can only chain from an
    earlier step in its own spec", which made a multi-model project impossible.
    """
    handlers.record_step(
        "specs/stg_orders.yaml",
        {
            "id": "cleaned",
            "op": "normalize",
            "input": "orders",
            "transforms": [{"column": "customer_id", "op": "to_numeric"}],
        },
        portia_dir=sales,
    )
    out = handlers.record_step(
        "specs/mart_orders.yaml",
        {
            "id": "joined",
            "op": "join",
            "left": "stg_orders",  # another spec's table, by plain name
            "right": "customers",
            "keys": ["customer_id"],
            "how": "left",
        },
        portia_dir=sales,
    )

    assert out["n_steps"] == 1
    assert out["outcome"]["n_rows"] > 0
    # The upstream model is not an indexed source, so it must not have been
    # written into this spec's `sources` map as though it were a file.
    doc = yaml.safe_load((tmp_path / "specs" / "mart_orders.yaml").read_text(encoding="utf-8"))
    assert "stg_orders" not in doc["sources"]
    assert doc["steps"][0]["left"] == "stg_orders"


def test_a_hash_ref_to_another_spec_becomes_that_specs_model_name(sales, tmp_path):
    """Which step inside another spec made its table is not the caller's business."""
    handlers.record_step(
        "specs/stg_orders.yaml",
        {
            "id": "cleaned",
            "op": "normalize",
            "input": "orders",
            "transforms": [{"column": "customer_id", "op": "to_numeric"}],
        },
        portia_dir=sales,
    )
    handlers.record_step(
        "specs/mart_orders.yaml",
        {
            "id": "joined",
            "op": "join",
            "left": "specs/stg_orders.yaml#cleaned",
            "right": "customers",
            "keys": ["customer_id"],
            "how": "left",
        },
        portia_dir=sales,
    )

    doc = yaml.safe_load((tmp_path / "specs" / "mart_orders.yaml").read_text(encoding="utf-8"))
    assert doc["steps"][0]["left"] == "stg_orders"


def test_join_findings_reads_another_specs_model_by_name(sales):
    """The read-only ladder has to reach a model, or you record hop two blind."""
    handlers.record_step(
        "specs/stg_orders.yaml",
        {
            "id": "cleaned",
            "op": "normalize",
            "input": "orders",
            "transforms": [{"column": "customer_id", "op": "to_numeric"}],
        },
        portia_dir=sales,
    )

    findings = handlers.join_findings(
        "stg_orders", "customers", keys=["customer_id"], portia_dir=sales
    )

    assert findings["report"]["left"]["n_rows"] > 0


def test_profile_source_measures_a_table_an_earlier_step_produced(sales):
    """ "Did my normalize actually change the column?" needs the table, not the file."""
    handlers.record_step(
        "specs/chain.yaml",
        {
            "id": "cleaned",
            "op": "normalize",
            "input": "customers",
            "transforms": [{"column": "name", "op": "lower"}],
        },
        portia_dir=sales,
    )
    out = handlers.profile_source("specs/chain.yaml#cleaned", sales)
    json.dumps(out)

    name = next(c for c in out["columns"] if c["name"] == "name")
    assert all(v == v.lower() for v in name["samples"])  # the transform, visible
    # no catalog entry exists for an intermediate, so no judgment is invented for it
    assert out["summary"] == ""
    assert name["role"] is None


def test_an_unknown_table_name_points_at_the_step_form(sales):
    """Otherwise the message reads 'no such table' when the truth is 'not by that name'."""
    with pytest.raises(ValueError, match="spec path.*#.*step id"):
        handlers.join_findings(
            "reservations_hotels", "customers", keys=["customer_id"], portia_dir=sales
        )


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


def test_an_expect_of_the_right_field_but_the_wrong_type_is_refused(sales, tmp_path):
    """Regression (EVALUATION.md, Run 3): `expect: {transforms: 1}` was written.

    `transforms` is a real normalize field, so the name check passed — but it
    reports a *list of records*, not a count. `1` can never equal that, so the
    spec drifted on every run forever. Same disease `_EXPECTABLE` cures, one
    level down: right field, wrong kind of value.
    """
    with pytest.raises(ValueError, match="transforms") as exc:
        handlers.record_step(
            "specs/n.yaml",
            {
                "id": "clean",
                "op": "normalize",
                "input": "customers",
                "transforms": [{"column": "name", "op": "strip"}],
                "expect": {"transforms": 1},
            },
            portia_dir=sales,
        )
    assert "you predicted a number" in str(exc.value)
    assert "reports a list" in str(exc.value)
    assert not (tmp_path / "specs" / "n.yaml").exists()


def test_a_correctly_shaped_expect_passes(sales):
    out = handlers.record_step(
        "specs/n.yaml",
        {
            "id": "clean",
            "op": "normalize",
            "input": "customers",
            "transforms": [{"column": "name", "op": "strip"}],
            "expect": {"input_rows": 6, "flags": []},
        },
        portia_dir=sales,
    )
    assert out["drift"] == {}


def test_a_row_count_predicted_as_a_float_is_not_an_error(sales):
    """int and float are the same kind — 10.0 rows is a clumsy prediction, not a wrong one."""
    out = handlers.record_step(
        "specs/j.yaml",
        {
            "id": "j",
            "op": "join",
            "left": "orders",
            "right": "customers",
            "keys": ["customer_id"],
            "how": "left",
            "expect": {"result_rows": 10.0},
        },
        portia_dir=sales,
    )
    assert out["drift"] == {}


def test_a_boolean_field_predicted_as_a_number_is_refused(sales):
    """`bool` is an `int` in Python; `matches_prediction: 1` must not slip through."""
    with pytest.raises(ValueError, match="matches_prediction"):
        handlers.record_step(
            "specs/j.yaml",
            {
                "id": "j",
                "op": "join",
                "left": "orders",
                "right": "customers",
                "keys": ["customer_id"],
                "how": "left",
                "expect": {"matches_prediction": 1},
            },
            portia_dir=sales,
        )


def test_a_structured_field_predicted_flat_is_refused(sales):
    """join reports `keys` as {left: [...], right: [...]}, not a bare list."""
    with pytest.raises(ValueError, match="you predicted a list"):
        handlers.record_step(
            "specs/j.yaml",
            {
                "id": "j",
                "op": "join",
                "left": "orders",
                "right": "customers",
                "keys": ["customer_id"],
                "how": "left",
                "expect": {"keys": ["customer_id"]},
            },
            portia_dir=sales,
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

    doc = yaml.safe_load((tmp_path / "specs" / "x.yaml").read_text(encoding="utf-8"))
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


# --- the sql escape hatch ----------------------------------------------------


def test_record_step_runs_a_sql_step_and_chains_from_it(sales, tmp_path):
    """The hatch end to end: aggregate, then consume the aggregate by id.

    This is the shape the hotel fixture's fatal fan-out needs and no op could
    express — a capable model worked that out, said so, and stopped
    (docs/EVALUATION.md, Run 6).
    """
    out = handlers.record_step(
        "specs/hatch.yaml",
        {
            "id": "orders_per_customer",
            "op": "sql",
            "inputs": ["orders"],
            "sql": "SELECT customer_id, COUNT(*) AS n_orders FROM orders GROUP BY 1",
            "grain": ["customer_id"],
            "rationale": "One row per customer, so the join below cannot multiply rows.",
        },
        portia_dir=sales,
    )
    json.dumps(out)
    assert out["outcome"]["grain"]["unique"] is True

    chained = handlers.record_step(
        "specs/hatch.yaml",
        {
            "id": "customers_with_counts",
            "op": "join",
            "left": "customers",
            "right": "orders_per_customer",  # <- the sql step's output
            "keys": ["customer_id"],
            "how": "left",
        },
        portia_dir=sales,
    )
    assert chained["n_steps"] == 2

    doc = yaml.safe_load((tmp_path / "specs" / "hatch.yaml").read_text(encoding="utf-8"))
    assert "orders_per_customer" not in doc["sources"]  # a step is not a source
    assert doc["sources"]["orders"] == "orders.csv"  # its declared input is
    assert handlers.run_spec("specs/hatch.yaml")["has_drift"] is False


def test_a_sql_step_may_name_an_earlier_step_the_hash_way_too(sales, tmp_path):
    """One convention for naming a table, in `inputs` as everywhere else."""
    handlers.record_step(
        "specs/hatch.yaml",
        {
            "id": "stripped",
            "op": "normalize",
            "input": "customers",
            "transforms": [{"column": "name", "op": "strip"}],
        },
        portia_dir=sales,
    )
    handlers.record_step(
        "specs/hatch.yaml",
        {
            "id": "counted",
            "op": "sql",
            "inputs": ["specs/hatch.yaml#stripped"],
            "sql": "SELECT COUNT(*) AS n FROM stripped",
        },
        portia_dir=sales,
    )
    doc = yaml.safe_load((tmp_path / "specs" / "hatch.yaml").read_text(encoding="utf-8"))
    assert doc["steps"][1]["inputs"] == ["stripped"]  # stored bare, like every other ref


def test_a_sql_step_may_name_an_earlier_step_the_hash_way_in_its_sql_too(sales, tmp_path):
    """`docs/PIPELINE.md` §8 — the half the test above never exercised.

    A `sql` step names its inputs **twice**, in `inputs` and in the query, and
    `apply_sql` registers each one under exactly the key the step declared. The
    test above declares the `#` form and then writes the query with the bare id,
    which is the one mixture that happened to work: normalization fixed the
    declaration, and the query already agreed with the result.

    Write the query the same way you wrote the declaration — which is what the
    copilot did, and what `record_step.md` told it to do — and the step used to
    ask the sandbox for a table nobody had registered, getting back a DuckDB
    error naming `__portia_raw_stripped`.
    """
    handlers.record_step(
        "specs/hatch.yaml",
        {
            "id": "stripped",
            "op": "normalize",
            "input": "customers",
            "transforms": [{"column": "name", "op": "strip"}],
        },
        portia_dir=sales,
    )
    out = handlers.record_step(
        "specs/hatch.yaml",
        {
            "id": "counted",
            "op": "sql",
            "inputs": ["specs/hatch.yaml#stripped"],
            "sql": 'SELECT COUNT(*) AS n FROM "specs/hatch.yaml#stripped"',
        },
        portia_dir=sales,
    )
    assert out["n_steps"] == 2

    doc = yaml.safe_load((tmp_path / "specs" / "hatch.yaml").read_text(encoding="utf-8"))
    step = doc["steps"][1]
    # Both halves normalized, so the spec reads as though the bare id was written.
    assert step["inputs"] == ["stripped"]
    assert step["sql"] == 'SELECT COUNT(*) AS n FROM "stripped"'
    assert handlers.run_spec("specs/hatch.yaml")["has_drift"] is False


def test_a_sql_step_naming_an_undeclared_table_is_told_what_it_declared(sales):
    """`PIPELINE.md` §8.3 — the error that used to leak a staging name.

    The message has to name the three things a name can be, or the caller reads
    "that table does not exist" and concludes chaining is unsupported rather
    than mistyped — `handlers._table`'s rule, one layer down.
    """
    with pytest.raises(ValueError, match="Declared: orders"):
        handlers.record_step(
            "specs/hatch.yaml",
            {
                "id": "typo",
                "op": "sql",
                "inputs": ["orders"],
                "sql": "SELECT COUNT(*) AS n FROM ordres",
            },
            portia_dir=sales,
        )


def test_a_sql_step_that_is_not_a_single_read_is_never_written(sales, tmp_path):
    """Refused at record time, so it cannot reach a durable spec at all."""
    with pytest.raises(ValueError, match="single SELECT"):
        handlers.record_step(
            "specs/hatch.yaml",
            {
                "id": "escape",
                "op": "sql",
                "inputs": ["orders"],
                "sql": "COPY (SELECT * FROM orders) TO '/tmp/leak.csv'",
            },
            portia_dir=sales,
        )
    assert not (tmp_path / "specs" / "hatch.yaml").exists()


def test_a_sql_step_predicting_a_field_it_never_reports_is_refused(sales):
    """`expect` is validated against the op's own PROVENANCE_KEYS, sql included."""
    with pytest.raises(ValueError, match="left_dropped"):
        handlers.record_step(
            "specs/hatch.yaml",
            {
                "id": "counted",
                "op": "sql",
                "inputs": ["orders"],
                "sql": "SELECT COUNT(*) AS n FROM orders",
                "expect": {"left_dropped": 0},
            },
            portia_dir=sales,
        )


# --- where a recorded spec lands ----------------------------------------------


def _join_step(step_id: str = "orders_with_customers") -> dict:
    return {
        "id": step_id,
        "op": "join",
        "left": "orders",
        "right": "customers",
        "keys": ["customer_id"],
        "how": "left",
        "rationale": "keep unmatched orders rather than silently dropping them",
        "expect": {},
    }


def test_a_recorded_spec_lands_in_its_layers_folder(sales, tmp_path):
    """**The engine places the file; the argument only names it.** The path the
    copilot passes says which table, not which directory — so a spec that
    declares a layer cannot end up anywhere but that layer's folder."""
    out = handlers.record_step("specs/orders.yaml", _join_step(), layer="mart", portia_dir=sales)

    assert (tmp_path / "specs" / "mart" / "orders.yaml").exists()
    assert not (tmp_path / "specs" / "orders.yaml").exists()
    assert out["spec"].endswith("specs/mart/orders.yaml")


def test_a_recorded_spec_with_no_layer_stays_flat(sales, tmp_path):
    """The flat project is the absence of a field, never a second mode."""
    handlers.record_step("specs/orders.yaml", _join_step(), portia_dir=sales)
    assert (tmp_path / "specs" / "orders.yaml").exists()


def test_a_second_step_never_moves_the_spec(sales, tmp_path):
    """Relocating a tracked file on the next recorded step is a diff nobody asked
    for — an existing spec's path is whatever `discover_specs` already found."""
    handlers.record_step("specs/orders.yaml", _join_step(), layer="mart", portia_dir=sales)
    out = handlers.record_step(
        "specs/orders.yaml",
        {
            "id": "tidied",
            "op": "normalize",
            "input": "orders_with_customers",
            "transforms": [{"column": "customer_id", "op": "strip"}],
            "expect": {},
        },
        portia_dir=sales,
    )

    assert out["spec"].endswith("specs/mart/orders.yaml")
    assert len(list((tmp_path / "specs").rglob("orders.yaml"))) == 1


def test_a_step_may_still_name_its_own_spec_by_the_path_it_was_given(sales, tmp_path):
    """Specs are compared by **name**, not path: the caller says
    `specs/orders.yaml` for a file the engine wrote to `specs/mart/orders.yaml`,
    and a `#`-reference to its own spec still resolves to the bare step id."""
    handlers.record_step("specs/orders.yaml", _join_step(), layer="mart", portia_dir=sales)
    handlers.record_step(
        "specs/orders.yaml",
        {
            "id": "tidied",
            "op": "normalize",
            "input": "specs/orders.yaml#orders_with_customers",
            "transforms": [{"column": "customer_id", "op": "strip"}],
            "expect": {},
        },
        portia_dir=sales,
    )

    doc = yaml.safe_load((tmp_path / "specs" / "mart" / "orders.yaml").read_text(encoding="utf-8"))
    assert doc["steps"][1]["input"] == "orders_with_customers"


# --- a model gets facts when a step is recorded on it ------------------------
#
# `catalog.index_model` shipped with `pipeline.build_project` as its only caller,
# on the argument that building is the only moment the table is free to profile.
# True, and it named the wrong moment: `record_step` runs the whole spec to
# measure the step it is appending, so it is holding the same table already.


def test_recording_a_step_records_what_the_model_looks_like(sales, tmp_path):
    out = handlers.record_step(
        "specs/per_customer.yaml",
        {
            "id": "counted",
            "op": "sql",
            "inputs": ["orders"],
            "sql": "SELECT customer_id, COUNT(*) AS n_orders FROM orders GROUP BY 1",
        },
        portia_dir=sales,
    )
    assert out["model"] == "updated"

    models = catalog.load_models(sales)
    assert set(models) == {"per_customer"}
    entry = models["per_customer"]
    assert [c["name"] for c in entry["columns"]] == ["customer_id", "n_orders"]
    # Facts measured, and no drafted read — an absent summary means nobody has
    # read this model, which is a fact worth being able to see.
    assert entry["summary"] is None


def test_a_later_step_refreshes_the_model_and_keeps_the_read(sales):
    """The catalog's update rule, on a model: facts refresh, judgment survives."""
    handlers.record_step(
        "specs/per_customer.yaml",
        {
            "id": "counted",
            "op": "sql",
            "inputs": ["orders"],
            "sql": "SELECT customer_id, COUNT(*) AS n_orders FROM orders GROUP BY 1",
        },
        portia_dir=sales,
    )
    handlers.set_interpretation(
        "per_customer",
        summary="One row per customer, with how many orders they placed.",
        roles={"customer_id": "identifier"},
        portia_dir=sales,
    )
    handlers.record_step(
        "specs/per_customer.yaml",
        {
            "id": "with_total",
            "op": "sql",
            "inputs": ["counted"],
            "sql": "SELECT customer_id, n_orders, n_orders * 2 AS doubled FROM counted",
        },
        portia_dir=sales,
    )
    entry = catalog.load_models(sales)["per_customer"]
    assert entry["summary"].startswith("One row per customer")
    assert [c["name"] for c in entry["columns"]] == ["customer_id", "n_orders", "doubled"]
    assert next(c for c in entry["columns"] if c["name"] == "customer_id")["role"] == "identifier"


def test_writing_a_read_of_a_model_no_longer_says_index_it_first(sales):
    """The 2026-09-04 call that failed, as its own case.

    The copilot had a finished table and was told to *index it first*, pointing
    at `.portia/sources/<model>.yaml` — a path that could not exist for a model
    and names a step nobody can take, because a model has no file.
    """
    handlers.record_step(
        "specs/per_customer.yaml",
        {
            "id": "counted",
            "op": "sql",
            "inputs": ["orders"],
            "sql": "SELECT customer_id, COUNT(*) AS n_orders FROM orders GROUP BY 1",
        },
        portia_dir=sales,
    )
    out = handlers.set_interpretation(
        "per_customer", summary="One row per customer.", portia_dir=sales
    )
    assert out["path"].endswith("models/per_customer.yaml")


def test_an_unknown_name_says_both_places_it_looked(sales):
    with pytest.raises(ValueError, match="sources/nope.yaml or .*models/nope.yaml"):
        handlers.set_interpretation("nope", summary="x", portia_dir=sales)


# --- read_spec (`docs/PIPELINE.md` §9) -----------------------------------------


def _write_spec(root, rel: str, doc: dict) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")


_STG = {
    "version": 1,
    "layer": "staging",
    "sources": {"orders": "orders.csv"},
    "steps": [
        {
            "id": "clean",
            "op": "sql",
            "inputs": ["orders"],
            "sql": "select * from orders",
            "rationale": "one clean copy of the extract",
            "expect": {},
        }
    ],
}
_MART = {
    "version": 1,
    "layer": "mart",
    "sources": {},
    "steps": [
        {
            "id": "totals",
            "op": "sql",
            "inputs": ["stg_orders"],
            "sql": "select count(*) as n from stg_orders",
            "rationale": "how many",
            "expect": {},
        }
    ],
}


def test_read_spec_returns_the_record_verbatim_with_its_neighbours(sales, tmp_path):
    """The spec is a record; a summary of a decision is a second author."""
    _write_spec(tmp_path, "specs/staging/stg_orders.yaml", _STG)
    _write_spec(tmp_path, "specs/mart/order_totals.yaml", _MART)

    out = handlers.read_spec("stg_orders", portia_dir=sales)
    json.dumps(out)

    assert out["spec"] == "specs/staging/stg_orders.yaml"
    assert out["model"] == "stg_orders" and out["layer"] == "staging"
    assert out["sources"] == {"orders": "orders.csv"}
    assert out["steps"] == _STG["steps"]
    assert out["n_steps"] == 1
    assert out["reads"] == [] and out["read_by"] == ["order_totals"]
    # never compiled: not stale, and no file to point at
    assert out["compiled"] is None and out["compiled_stale"] is False
    # nobody has built it and nothing has been asked about it — stated, not absent
    assert out["built"] is None and out["n_findings"] == 0
    assert "journal" not in out and "measured" not in out


def test_read_spec_takes_a_path_as_well_as_a_name(sales, tmp_path):
    _write_spec(tmp_path, "specs/staging/stg_orders.yaml", _STG)
    _write_spec(tmp_path, "specs/mart/order_totals.yaml", _MART)
    out = handlers.read_spec("specs/mart/order_totals.yaml", portia_dir=sales)
    assert out["model"] == "order_totals" and out["reads"] == ["stg_orders"]


def test_read_spec_names_the_known_specs_on_an_unknown_name(sales, tmp_path):
    _write_spec(tmp_path, "specs/staging/stg_orders.yaml", _STG)
    with pytest.raises(ValueError, match="stg_orders"):
        handlers.read_spec("ghost", portia_dir=sales)


def test_read_spec_carries_the_journal_and_the_last_build_on_request(sales, tmp_path):
    """Counts always; the things themselves only when asked (`KNOWLEDGE_GRAPH.md` §4.4)."""
    from portia import findings

    handlers.record_step(
        "specs/orders.yaml",
        {
            "id": "j",
            "op": "join",
            "left": "orders",
            "right": "customers",
            "keys": ["customer_id"],
            "how": "left",
            "rationale": "keep every order",
        },
        portia_dir=sales,
    )
    findings.record(
        question="are the unmatched orders real?",
        answer="two reference customers missing from the dimension",
        so="kept a left join",
        about=["orders", "customers"],
        queries=[],
        spec_name="orders",
        root=tmp_path,
        portia_dir=sales,
    )

    base = handlers.read_spec("orders", portia_dir=sales)
    assert base["n_findings"] == 1 and base["built"]
    assert "journal" not in base and "measured" not in base

    full = handlers.read_spec("orders", journal=True, measured=True, portia_dir=sales)
    json.dumps(full)
    assert full["journal"][0]["so"] == "kept a left join"
    assert full["journal"][0]["at"]
    assert "customer_id" in {c["name"] for c in full["measured"]["columns"]}


def test_get_context_lists_the_models_beside_the_sources(sales, tmp_path):
    _write_spec(tmp_path, "specs/staging/stg_orders.yaml", _STG)
    ctx = handlers.get_context(sales)
    json.dumps(ctx)
    assert ctx["models"] == {
        "stg_orders": {"layer": "staging", "target": None, "n_steps": 1, "reads": []}
    }


# --- notes ride the rungs, and a model has a rung -------------------------------
#
# `docs/COPILOT.md` §2 and §3. A note is what a chat learned about a table that
# neither the summary nor a finding can hold; it rides `describe_source` so the
# read before a build sees it. And a model is a table this project built, so the
# same two rungs open it by name, which is what an audit of a built table starts with.


def test_a_note_is_written_alone_and_read_back_on_both_rungs(project):
    out = handlers.set_interpretation(
        "customers",
        note="signup_amount is in cents; divide by 100 before summing.",
        portia_dir=project,
    )
    json.dumps(out)
    assert out["note_written"] is True and out["summary_written"] is False

    described = handlers.describe_source("customers", project)
    assert [n["text"] for n in described["notes"]] == [
        "signup_amount is in cents; divide by 100 before summing."
    ]
    assert described["notes"][0]["at"]
    profiled = handlers.profile_source("customers", project)
    assert profiled["notes"] == described["notes"]


def test_a_source_without_notes_says_nothing_about_them(project):
    assert "notes" not in handlers.describe_source("customers", project)


def test_describe_and_profile_open_a_built_model_by_name(sales):
    handlers.record_step(
        "specs/per_customer.yaml",
        {
            "id": "counted",
            "op": "sql",
            "inputs": ["orders"],
            "sql": "SELECT customer_id, COUNT(*) AS n_orders FROM orders GROUP BY 1",
        },
        portia_dir=sales,
    )
    handlers.set_interpretation(
        "per_customer",
        summary="One row per customer with their order count.",
        note="customer_id 0 is the unknown-customer bucket.",
        portia_dir=sales,
    )

    described = handlers.describe_source("per_customer", sales)
    json.dumps(described)
    assert described["source"] == "per_customer"
    assert described["summary"].startswith("One row per customer")
    assert [c["name"] for c in described["columns"]] == ["customer_id", "n_orders"]
    assert described["notes"][0]["text"].startswith("customer_id 0")

    profiled = handlers.profile_source("per_customer", sales)
    json.dumps(profiled)
    assert profiled["source"] == "per_customer" and profiled["n_cols"] == 2
    assert {c["name"] for c in profiled["columns"]} == {"customer_id", "n_orders"}
    assert "samples" in profiled["columns"][0], "a full profile, not the catalog's slice"
    assert profiled["notes"] == described["notes"]


def test_profile_opens_a_model_nobody_has_recorded_a_step_through(sales, tmp_path):
    """A spec on disk with no catalog entry is still a table this project can produce."""
    (tmp_path / "specs").mkdir(exist_ok=True)
    (tmp_path / "specs" / "orders_only.yaml").write_text(
        "version: 1\nsources:\n  orders: orders.csv\nsteps:\n"
        "- id: all\n  op: sql\n  inputs: [orders]\n  sql: SELECT * FROM orders\n",
        encoding="utf-8",
    )
    out = handlers.profile_source("orders_only", sales)
    assert out["source"] == "orders_only" and out["n_rows"] > 0 and out["summary"] == ""


def test_a_role_lands_on_the_column_the_engine_named(project):
    """`roles={'CUSTOMER_ID': …}` on a source whose column is `customer_id`: the
    same fold as everywhere else, so a warehouse's upper case is not a refusal."""
    handlers.set_interpretation(
        "customers", roles={"CUSTOMER_ID": "identifier"}, portia_dir=project
    )
    entry = handlers.describe_source("customers", project)
    assert next(c for c in entry["columns"] if c["name"] == "customer_id")["role"] == "identifier"


def test_an_unknown_name_is_told_about_sources_and_models(sales):
    with pytest.raises(ValueError, match="no indexed source or built model") as exc:
        handlers.describe_source("nope", sales)
    assert (
        "sources:" in str(exc.value)
        and "customers" in str(exc.value)
        and "models:" in str(exc.value)
    )


def test_a_local_build_profiles_the_model_and_the_entry_says_so(sales):
    """Locally a build *is* the profile (`catalog.index_model`), and the entry
    now states it as a field, so `is_profiled` reads the same rule for a model
    as for a source rather than answering yes to every model."""
    handlers.record_step(
        "specs/per_customer.yaml",
        {
            "id": "counted",
            "op": "sql",
            "inputs": ["orders"],
            "sql": "SELECT customer_id, COUNT(*) AS n_orders FROM orders GROUP BY 1",
        },
        portia_dir=sales,
    )
    entry = catalog.load_models(sales)["per_customer"]
    assert entry["profiled"]["at"] and catalog.is_profiled(entry)
    assert "indexed" not in entry, "nothing was written anywhere, so no warehouse facts"
    described = handlers.describe_source("per_customer", portia_dir=sales)
    assert "profiled" not in described and described["columns"][0]["inferred"]
    # An entry written before the field existed still reads as profiled.
    assert catalog.is_profiled({"model": "old", "columns": []})

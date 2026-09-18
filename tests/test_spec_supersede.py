"""Correcting a recorded step, and what replaced the append-only rule.

`STEPS ARE APPEND-ONLY` was defended on the grounds that the decision record is a
record. Git is the record (`docs/FINDINGS.md` §7). The rule that survives is
**nothing enters a spec unmeasured**, and these tests are about that: a
replacement still runs, the whole spec still runs with it, and the gate still
applies.
"""

import pytest

from portia import pipeline, spec
from portia.agent import handlers
from portia.catalog import index_source, init_project
from portia.fixtures import sales_customers, sales_orders


@pytest.fixture
def sales(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    sales_orders().to_csv("orders.csv", index=False)
    sales_customers().to_csv("customers.csv", index=False)
    d = tmp_path / ".portia"
    init_project("order reconciliation", portia_dir=d)
    index_source("orders.csv", portia_dir=d)
    index_source("customers.csv", portia_dir=d)
    return str(d)


def _step(step_id, sql, **extra):
    return {"id": step_id, "op": "sql", "inputs": ["orders"], "sql": sql, **extra}


def test_a_replacement_takes_the_place_of_what_it_supersedes(sales, tmp_path):
    handlers.record_step("specs/m.yaml", _step("pick", "SELECT * FROM orders"), portia_dir=sales)
    got = handlers.record_step(
        "specs/m.yaml",
        _step("pick", "SELECT order_id FROM orders", rationale="only the id is needed"),
        supersedes="pick",
        portia_dir=sales,
    )

    assert got["superseded"] == "pick"
    assert got["n_steps"] == 1, "replaced, not appended"
    doc = spec.load_spec(tmp_path / "specs" / "m.yaml")
    assert doc["steps"][0]["sql"] == "SELECT order_id FROM orders"


def test_it_keeps_the_position_so_downstream_keeps_reading_it(sales, tmp_path):
    handlers.record_step("specs/m.yaml", _step("a", "SELECT * FROM orders"), portia_dir=sales)
    handlers.record_step(
        "specs/m.yaml",
        {"id": "b", "op": "sql", "inputs": ["a"], "sql": "SELECT order_id FROM a"},
        portia_dir=sales,
    )
    handlers.record_step(
        "specs/m.yaml",
        _step("a", "SELECT order_id, amount FROM orders", rationale="two columns are enough"),
        supersedes="a",
        portia_dir=sales,
    )

    doc = spec.load_spec(tmp_path / "specs" / "m.yaml")
    assert [s["id"] for s in doc["steps"]] == ["a", "b"]
    assert spec.unreachable_steps(doc) == [], "nothing was orphaned by the correction"


def test_the_result_returned_is_the_replacements_own_not_the_last_steps(sales):
    """`run_spec` returns every step; taking `[-1]` would report the wrong table."""
    handlers.record_step("specs/m.yaml", _step("a", "SELECT * FROM orders"), portia_dir=sales)
    handlers.record_step(
        "specs/m.yaml",
        {"id": "b", "op": "sql", "inputs": ["a"], "sql": "SELECT order_id FROM a"},
        portia_dir=sales,
    )
    got = handlers.record_step(
        "specs/m.yaml",
        _step("a", "SELECT order_id, amount FROM orders"),
        supersedes="a",
        portia_dir=sales,
    )
    assert got["outcome"]["n_cols"] == 2, "step a's two columns, not step b's one"


def test_a_replacement_that_breaks_a_later_step_fails_here(sales):
    """The whole spec re-runs with the correction in it — that is what makes it safe."""
    handlers.record_step("specs/m.yaml", _step("a", "SELECT * FROM orders"), portia_dir=sales)
    handlers.record_step(
        "specs/m.yaml",
        {"id": "b", "op": "sql", "inputs": ["a"], "sql": "SELECT amount FROM a"},
        portia_dir=sales,
    )
    with pytest.raises(Exception, match="amount"):
        handlers.record_step(
            "specs/m.yaml",
            _step("a", "SELECT order_id FROM orders"),
            supersedes="a",
            portia_dir=sales,
        )


def test_the_gate_still_applies_to_a_replacement(sales):
    """*Nothing enters a spec unmeasured* is the rule that survived append-only."""
    handlers.record_step("specs/m.yaml", _step("a", "SELECT * FROM orders"), portia_dir=sales)
    with pytest.raises(ValueError, match="empty_output"):
        handlers.record_step(
            "specs/m.yaml",
            _step("a", "SELECT * FROM orders WHERE order_id = -1"),
            supersedes="a",
            portia_dir=sales,
        )


def test_renaming_a_step_another_one_reads_is_refused(sales):
    """`cli/remove_spec`'s condition at step granularity: closed under *is read by*."""
    handlers.record_step("specs/m.yaml", _step("a", "SELECT * FROM orders"), portia_dir=sales)
    handlers.record_step(
        "specs/m.yaml",
        {"id": "b", "op": "sql", "inputs": ["a"], "sql": "SELECT order_id FROM a"},
        portia_dir=sales,
    )
    with pytest.raises(ValueError, match="'b'"):
        handlers.record_step(
            "specs/m.yaml",
            _step("a_renamed", "SELECT * FROM orders"),
            supersedes="a",
            portia_dir=sales,
        )


def test_superseding_a_step_that_is_not_there_names_the_ones_that_are(sales):
    handlers.record_step("specs/m.yaml", _step("a", "SELECT * FROM orders"), portia_dir=sales)
    with pytest.raises(ValueError, match="Steps: a"):
        handlers.record_step(
            "specs/m.yaml", _step("c", "SELECT 1"), supersedes="nope", portia_dir=sales
        )


def test_a_duplicate_id_without_supersedes_is_still_refused(sales):
    """An accident and an edit are different acts, and only one of them says so."""
    handlers.record_step("specs/m.yaml", _step("a", "SELECT * FROM orders"), portia_dir=sales)
    with pytest.raises(ValueError, match="supersedes"):
        handlers.record_step("specs/m.yaml", _step("a", "SELECT 1"), portia_dir=sales)


# --- the invariant that replaced the pill -----------------------------------


def test_an_unread_step_is_a_build_failure_rather_than_a_chip(sales, tmp_path):
    handlers.record_step("specs/m.yaml", _step("a", "SELECT * FROM orders"), portia_dir=sales)
    assert pipeline.unread_steps(tmp_path) == {}

    handlers.record_step(
        "specs/m.yaml",
        {"id": "b", "op": "sql", "inputs": ["orders"], "sql": "SELECT order_id FROM orders"},
        portia_dir=sales,
    )
    assert pipeline.unread_steps(tmp_path) == {"m": ["a"]}

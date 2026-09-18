"""The saved run report — the durable half of pressing Run.

Every previous run in this project was written up by hand from a terminal
transcript (docs/EVALUATION.md). This is the artifact that stops that, so what it
has to get right is what a reviewer needs from a diff: the four groups kept
apart, and an override impossible to skim past.
"""

from __future__ import annotations

import pytest

from portia.fixtures import sales_customers, sales_orders
from portia.spec import render_markdown, run_spec, write_report


@pytest.fixture
def results(tmp_path):
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
                "expect": {"result_rows": 10},
                "rationale": "Billing needs every order to carry its customer.",
            }
        ],
    }
    return run_spec(spec, base_dir=tmp_path), tmp_path


def test_the_report_names_the_spec_and_the_step(results):
    _results, _ = results
    md = render_markdown(_results, spec_path="specs/orders.yaml")
    assert "specs/orders.yaml" in md
    assert "## joined  (join)" in md


def test_the_four_groups_stay_four_groups(results):
    """Merging them into a status is the mistake three runs went into unlearning."""
    _results, _ = results
    md = render_markdown(_results)
    assert "### provenance" in md
    assert "### outcome" in md


def test_provenance_and_outcome_carry_the_engines_own_numbers(results):
    _results, _ = results
    md = render_markdown(_results)
    assert "| result_rows | 10 |" in md
    assert "| produced | 10 × 4 |" in md


def test_a_contribution_reads_as_a_sentence_not_a_dict(results):
    _results, _ = results
    md = render_markdown(_results)
    assert "columns_in_output" not in md
    assert "in output · contributed" in md


def test_the_header_states_whether_anything_is_blocking(results):
    _results, _ = results
    assert "no blocking flag" in render_markdown(_results)


def test_a_blocking_flag_is_named_in_the_header(results):
    _results, _ = results
    _results[0].outcome = {**_results[0].outcome, "flags": ["empty_output"]}
    assert "**blocking: empty_output**" in render_markdown(_results)


def test_an_acknowledged_override_comes_first_and_is_not_in_a_table(results):
    """It must be impossible to skim past — Run 5 shipped a 3.85%-inflated table
    because an override was fifteen characters inside a dict."""
    _results, _ = results
    _results[0].acknowledged = ["grain_not_unique"]
    md = render_markdown(_results)
    step = md[md.index("## joined") :]
    assert step.index("Acknowledged override") < step.index("### provenance")
    assert "> **Acknowledged override:** `grain_not_unique`" in md


def test_drift_states_both_sides(results):
    _results, _ = results
    _results[0].drift = {"result_rows": {"expected": 8, "actual": 10}}
    md = render_markdown(_results)
    assert "### drift" in md
    assert "expected 8 · actual 10" in md


def test_the_rationale_survives_verbatim(results):
    _results, _ = results
    assert "Billing needs every order to carry its customer." in render_markdown(_results)


def test_writing_one_lands_a_timestamped_file(results, tmp_path):
    _results, _ = results
    path = write_report(_results, tmp_path / "runs", spec_path="specs/orders.yaml")
    assert path.parent.name == "runs"
    assert path.suffix == ".md"
    assert ":" not in path.name, "colons aren't portable in filenames"
    assert path.read_text().startswith("# orders.yaml — ")
    assert "`specs/orders.yaml`" in path.read_text()

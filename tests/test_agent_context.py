"""L1 — the context the copilot always has, without asking for it.

The bug this layer exists to fix: `get_context` was a tool the agent *might*
call, and in a real merge run it didn't — so it reasoned about the join with no
idea what the project was, and its output read like generic data-engineering
advice. Presence has to be structural, which is what these tests hold.
"""

import pytest

from portia.agent import context, prompts
from portia.catalog import index_source, init_project, set_group, set_interpretation
from portia.fixtures import sales_customers, sales_orders


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    sales_orders().to_csv("orders.csv", index=False)
    sales_customers().to_csv("customers.csv", index=False)
    d = tmp_path / ".portia"
    init_project("Reconciling vendor orders against our CRM before invoicing.", portia_dir=d)
    index_source("orders.csv", portia_dir=d)
    index_source("customers.csv", portia_dir=d)
    return str(d)


def test_brief_carries_the_project_prose_and_every_source(project):
    brief = context.build_brief(project)

    assert "Reconciling vendor orders" in brief
    assert "orders" in brief and "customers" in brief
    assert "candidate keys" in brief


def test_brief_stays_one_line_per_source(project):
    """The index is on every request, so it must not grow with the summaries."""
    set_interpretation(
        "orders",
        summary=("First sentence. " + "Padding that must not reach the brief. " * 40),
        portia_dir=project,
    )
    brief = context.build_brief(project)

    assert "First sentence." in brief
    assert "must not reach the brief" not in brief
    assert len([ln for ln in brief.splitlines() if ln.startswith("- **orders**")]) == 1


def test_brief_flags_sources_that_are_not_yet_interpreted(project):
    """A placeholder must read as a gap, not as an interpretation."""
    assert "Not yet interpreted" in context.build_brief(project)

    set_interpretation(
        "orders", summary="Transactional orders, one row per line item.", portia_dir=project
    )
    brief = context.build_brief(project)
    assert "Transactional orders, one row per line item." in brief


def test_brief_carries_groups_and_their_shared_context(project):
    set_group(
        "vendor_feed",
        context="Both arrive in the same nightly vendor export.",
        sources=["orders", "customers"],
        portia_dir=project,
    )
    brief = context.build_brief(project)

    assert "vendor_feed" in brief
    assert "nightly vendor export" in brief
    assert "orders, customers" in brief


def test_brief_asks_for_context_when_there_is_none(tmp_path):
    """An uninitialized project must tell the copilot to ask, not stay silent."""
    no_context = prompts.load("brief/no_context")
    assert no_context in context.build_brief(str(tmp_path / "nope"))

    init_project("", portia_dir=tmp_path / ".portia")
    assert no_context in context.build_brief(str(tmp_path / ".portia"))


def test_system_prompt_composes_l0_and_l1(project):
    """The brief must actually reach the model, not just be renderable."""
    from portia.agent import session

    prompt = session.build_system_prompt(project)
    assert "You are **portia**" in prompt  # L0
    assert "Reconciling vendor orders" in prompt  # L1
    assert prompt.index("You are **portia**") < prompt.index("Reconciling vendor orders")


def test_effort_reaches_the_sdk_options(project):
    """`--effort` is half of "develop on a small model at low effort" (PLAN.md).

    A knob that silently does nothing is worse than no knob: a ceiling check on
    a flagship would report a low-effort result as a high-effort one.
    """
    from portia.agent import session

    assert session.build_options(portia_dir=project).effort is None
    assert session.build_options(portia_dir=project, effort="low").effort == "low"


def test_an_unknown_effort_is_refused_rather_than_ignored(project):
    from portia.agent import session

    with pytest.raises(ValueError, match="unknown effort"):
        session.build_options(portia_dir=project, effort="lowish")

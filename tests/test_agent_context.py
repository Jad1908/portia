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


def test_the_index_says_what_a_source_is_and_nothing_about_its_shape(project):
    """Phase D's trim (`KNOWLEDGE_GRAPH.md` §9.4). A column count and a
    candidate-key list are what `describe_source` and `profile_source` exist to
    serve, and pushing them into every request paid for them on every turn.

    The name and one sentence stay because together they *are* the routing
    decision — which source is this question about — and everything else is the
    answer to a question the agent has not asked yet.
    """
    set_interpretation("orders", summary="Vendor order lines.", portia_dir=project)
    line = next(
        ln for ln in context.build_brief(project).splitlines() if ln.startswith("- **orders**")
    )

    assert line == "- **orders** — Vendor order lines."
    assert "cols" not in line and "candidate keys" not in line


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


def _write_spec(root, rel: str, doc: dict) -> None:
    import yaml

    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")


def _two_specs(root) -> None:
    _write_spec(
        root,
        "specs/staging/stg_orders.yaml",
        {
            "version": 1,
            "layer": "staging",
            "sources": {"orders": "orders.csv"},
            "steps": [{"id": "clean", "op": "sql", "inputs": ["orders"], "sql": "select 1"}],
        },
    )
    _write_spec(
        root,
        "specs/mart/order_totals.yaml",
        {
            "version": 1,
            "layer": "mart",
            "sources": {},
            "steps": [
                {"id": "a", "op": "sql", "inputs": ["stg_orders"], "sql": "select 1"},
                {"id": "b", "op": "sql", "inputs": ["a"], "sql": "select 1"},
            ],
        },
    )


def test_brief_lists_the_pipeline_one_line_per_spec_in_build_order(project, tmp_path):
    """The agent cannot ask about a spec it does not know exists (`PIPELINE.md` §9).

    A Haiku session, asked about an existing spec, said it needed the spec's
    name. The same rule as the source index: the name is the routing decision.
    """
    _two_specs(tmp_path)
    brief = context.build_brief(project)

    assert "## Pipeline" in brief
    lines = [
        ln
        for ln in brief.splitlines()
        if ln.startswith("- **stg_orders**") or ln.startswith("- **order_totals**")
    ]
    assert lines == [
        "- **stg_orders** (staging) — 1 step",
        "- **order_totals** (mart) — 2 steps; reads stg_orders",
    ]
    assert brief.index("## Pipeline") < brief.index("## Indexed sources")


def test_brief_has_no_pipeline_section_when_nothing_is_specified(project):
    """An empty section is a line of chrome on every request of a fresh project."""
    assert "## Pipeline" not in context.build_brief(project)


def test_the_index_is_computed_from_the_directory_not_kept(project, tmp_path):
    _two_specs(tmp_path)
    index = context.model_index(tmp_path)
    assert list(index) == ["stg_orders", "order_totals"]
    assert index["order_totals"] == {
        "layer": "mart",
        "target": None,
        "n_steps": 2,
        "reads": ["stg_orders"],
    }
    (tmp_path / "specs" / "mart" / "order_totals.yaml").unlink()
    assert list(context.model_index(tmp_path)) == ["stg_orders"]


def test_the_brief_no_longer_promises_candidate_keys(project):
    """Retired 2026-09-01; the sentence had been read on every request since."""
    assert "candidate keys" not in context.build_brief(project)


# --- where the data lives (`docs/CONNECTOR.md` §2.9) -------------------------------


def test_the_brief_ends_with_where_the_data_lives_and_it_is_local_by_default(project):
    from portia.agent import context
    from portia.core import backend

    assert backend.active() is backend.LOCAL
    brief = context.build_brief(project)
    assert "## Where the data lives" in brief
    assert "DuckDB" in brief and "Snowflake" not in brief
    assert brief.index("## Indexed sources") < brief.index("## Where the data lives")


def test_a_warehouse_project_gets_its_own_section_with_the_connection_and_target(project):
    from portia.agent import context
    from portia.core import backend, dialect

    remote = backend.Backend(
        kind="snowflake",
        dialect=dialect.SNOWFLAKE,
        open=lambda: None,
        remote=True,
        opens_on="ANALYTICS.PORTIA",
        label="prod",
    )
    with backend.using(remote):
        section = context.where_the_data_lives({"scope": ["A.B.C", "A.B.D"]})
    assert "Snowflake" in section and "**prod**" in section and "`ANALYTICS.PORTIA`" in section
    assert "`A.B.C`, `A.B.D`" in section
    assert "{" not in section.replace("{{", ""), "every placeholder filled"


def test_the_brief_says_who_writes_tables_and_that_each_spec_names_where():
    """`CONNECTOR.md` §2.7.1: off, the agent is told only the user writes; on, that
    recording a step creates the table. Both say the target is the spec's own and
    offer the connection's database as the place to start, or say it names none."""
    from portia.agent import context
    from portia.core import backend, dialect

    def remote(**kw):
        return backend.Backend(
            kind="snowflake",
            dialect=dialect.SNOWFLAKE,
            open=lambda: None,
            remote=True,
            label="prod",
            **kw,
        )

    with backend.using(remote(opens_on="ANALYTICS")):
        off = context.where_the_data_lives({"scope": ["A.B.C"]})
    assert "Only the user writes tables" in off and "You build the tables" not in off
    assert "the connection opens on `ANALYTICS`" in off and "per spec" in off

    with backend.using(remote(agent_writes=True)):
        on = context.where_the_data_lives({"scope": ["A.B.C"]})
    assert "You build the tables" in on and "Only the user writes tables" not in on
    assert "names no database" in on
    assert "{" not in on.replace("{{", ""), "every placeholder filled"


def test_the_pipeline_index_says_where_a_spec_writes(tmp_path):
    from portia import spec
    from portia.agent import context

    (tmp_path / "specs").mkdir()
    spec.save_spec(
        {"version": 1, "target": "ANALYTICS.STAGING", "sources": {}, "steps": []},
        tmp_path / "specs" / "stg_x.yaml",
    )
    assert context.model_index(tmp_path)["stg_x"]["target"] == "ANALYTICS.STAGING"
    assert "writes to `ANALYTICS.STAGING`" in context._render_models(tmp_path)


def test_every_backend_has_a_prompt_file():
    from portia.agent import prompts
    from portia.core import dialect

    files = prompts.names("backend")
    assert "local" in files
    assert {name for name in dialect.BY_NAME if name != "duckdb"} <= files

"""Removing a spec, and the guard that stops it orphaning what reads it.

A spec is the only artifact in portia that makes others, so deleting one by
hand leaves three descriptions of a table nothing builds: the compiled `.sql`,
the measured entry `catalog.index_model` wrote, and a Model node in the graph.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest
import yaml

from portia import catalog, pipeline, spec
from portia.cli import remove_spec


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A three-model chain: `stg_orders` -> `int_orders` -> `mart_orders`."""
    data = tmp_path / "data"
    data.mkdir()
    with open(data / "orders.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["order_id", "customer_id", "amount"])
        w.writerows([[1, " C1 ", 10], [2, "C2", 20]])

    specs = tmp_path / spec.SPECS_DIR
    specs.mkdir()
    _write(specs / "stg_orders.yaml", "staging", {"orders": "data/orders.csv"}, "orders")
    _write(specs / "int_orders.yaml", "intermediate", {}, "stg_orders")
    _write(specs / "mart_orders.yaml", "mart", {}, "int_orders")

    catalog.init_project("a shop", portia_dir=tmp_path / catalog.DEFAULT_DIR)
    catalog.index_source(data / "orders.csv", portia_dir=tmp_path / catalog.DEFAULT_DIR)
    return tmp_path


def _write(path: Path, layer: str, sources: dict, reads: str) -> None:
    path.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "layer": layer,
                "sources": sources,
                "steps": [
                    {
                        "id": "cleaned",
                        "op": "normalize",
                        "input": reads,
                        "transforms": [{"column": "customer_id", "op": "strip"}],
                    }
                ],
            },
            sort_keys=False,
        )
    )


def test_removing_a_spec_takes_everything_that_exists_because_of_it(project, monkeypatch):
    monkeypatch.setattr(remove_spec, "_sync", lambda *_a, **_k: None)
    pipeline.build_project(project)
    portia_dir = project / catalog.DEFAULT_DIR

    sql = project / "models" / "mart" / "mart_orders.sql"
    entry = portia_dir / catalog.MODELS_DIR / "mart_orders.yaml"
    assert sql.exists() and entry.exists()

    plan = remove_spec.plan(["mart_orders"], project)
    assert set(plan.files) == {project / "specs" / "mart_orders.yaml", sql, entry}

    remove_spec.remove(plan, project)
    assert not sql.exists() and not entry.exists()
    assert "mart_orders" not in spec.discover_specs(project)
    # And nothing touched the data, which the spec read and did not create.
    assert (project / "data" / "orders.csv").exists()


def test_a_spec_something_downstream_reads_is_refused(project):
    """The model reading it would compile against a table nobody builds."""
    with pytest.raises(ValueError, match="int_orders reads stg_orders"):
        remove_spec.plan(["stg_orders"], project)


def test_the_refusal_names_both_ways_out(project):
    with pytest.raises(ValueError) as exc:
        remove_spec.plan(["stg_orders"], project)
    assert "naming them all" in str(exc.value)
    assert "top of the tree" in str(exc.value)


def test_the_whole_family_named_at_once_is_allowed(project, monkeypatch):
    """One of the two ways through: the set is closed under *is read by*."""
    monkeypatch.setattr(remove_spec, "_sync", lambda *_a, **_k: None)
    plan = remove_spec.plan(["stg_orders", "int_orders", "mart_orders"], project)
    remove_spec.remove(plan, project)
    assert spec.discover_specs(project) == {}


def test_top_down_one_at_a_time_is_allowed(project, monkeypatch):
    """The other way: nothing reads the leaf, so it goes, and then the next."""
    monkeypatch.setattr(remove_spec, "_sync", lambda *_a, **_k: None)
    for name in ("mart_orders", "int_orders", "stg_orders"):
        remove_spec.remove(remove_spec.plan([name], project), project)
    assert spec.discover_specs(project) == {}


def test_bottom_up_is_refused_at_the_first_step(project):
    """The same three specs in the other order, refused immediately — which is
    what makes 'work your way down' the instruction rather than a suggestion."""
    with pytest.raises(ValueError, match="cannot remove"):
        remove_spec.plan(["stg_orders"], project)


def test_an_unknown_name_says_what_there_is(project):
    with pytest.raises(ValueError, match="int_orders, mart_orders, stg_orders"):
        remove_spec.plan(["nope"], project)


def test_a_spec_that_was_never_built_still_removes_its_yaml(project, monkeypatch):
    """Nothing has compiled it, so there is no `.sql` and no measured entry —
    a shorter plan, not a failure."""
    monkeypatch.setattr(remove_spec, "_sync", lambda *_a, **_k: None)
    plan = remove_spec.plan(["mart_orders"], project)
    assert plan.files == [project / "specs" / "mart_orders.yaml"]
    remove_spec.remove(plan, project)
    assert "mart_orders" not in spec.discover_specs(project)


def test_the_model_node_goes_with_the_spec(neo4j_session, project, monkeypatch):
    """A rebuild prunes structural edges and then any node left with nothing
    attached, so a Model whose spec is gone goes with it — no delete of its own.

    That is `store.prune_writes` doing what it already did, which is why this
    branch adds no conditional delete to a module whose whole rule is that a
    measurement is never deleted (§5.2).
    """
    from portia.knowledge import build_graph, schema, store

    monkeypatch.chdir(project)
    pid = schema.project_id(project)
    store.write(build_graph(project).graph, neo4j_session)
    assert _models(neo4j_session, pid) == ["int_orders", "mart_orders", "stg_orders"]

    remove_spec.remove(remove_spec.plan(["mart_orders"], project), project)

    store.write(build_graph(project).graph, neo4j_session)
    assert _models(neo4j_session, pid) == ["int_orders", "stg_orders"]
    orphans = neo4j_session.run(
        "MATCH (c:Column) WHERE c.project = $p AND c.table = 'mart_orders' RETURN count(c) AS n",
        p=pid,
    ).single()["n"]
    assert orphans == 0


def _models(session, pid: str) -> list[str]:
    return sorted(
        r["name"]
        for r in session.run("MATCH (m:Model) WHERE m.project = $p RETURN m.name AS name", p=pid)
    )

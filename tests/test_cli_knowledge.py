"""`python -m portia.cli.knowledge` — the build command §5 says is needed anyway.

Someone cloning the repo gets the YAML from git and no graph. What these pin is
that reading and printing one costs nothing: no database, no data files, no
agent. That is how phase A is checkable on its own (§9.4).
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest
import yaml

from portia import catalog
from portia.cli import knowledge


@pytest.fixture
def project(tmp_path: Path) -> Path:
    data = tmp_path / "data"
    data.mkdir()
    with open(data / "orders.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["order_id", "amount"])
        w.writerows([[1, 10], [2, 20]])
    catalog.init_project("a shop", portia_dir=tmp_path / catalog.DEFAULT_DIR)
    catalog.index_source(data / "orders.csv", portia_dir=tmp_path / catalog.DEFAULT_DIR)

    specs = tmp_path / "specs"
    specs.mkdir()
    (specs / "stg_orders.yaml").write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "sources": {"orders": "data/orders.csv"},
                "steps": [
                    {
                        "id": "cleaned",
                        "op": "normalize",
                        "input": "orders",
                        "transforms": [{"column": "amount", "op": "to_numeric"}],
                    }
                ],
            },
            sort_keys=False,
        )
    )
    return tmp_path


def _run(monkeypatch, *args: str) -> None:
    monkeypatch.setattr("sys.argv", ["knowledge", *args])
    knowledge.main()


def test_it_prints_what_it_read(project, monkeypatch, capsys):
    _run(monkeypatch, "--root", str(project))
    out = capsys.readouterr().out
    assert "Source   1" in out and "Model    1" in out
    assert "DERIVES_FROM  2" in out


def test_cypher_shows_what_a_write_would_send_and_sends_nothing(project, monkeypatch, capsys):
    _run(monkeypatch, "--root", str(project), "--cypher")
    out = capsys.readouterr().out
    assert "CREATE CONSTRAINT source_key" in out
    assert "MERGE (a)-[r:DERIVES_FROM]->(b)" in out


def test_a_stopped_database_is_reported_as_one(project, monkeypatch, capsys):
    """§3.5 — the app must behave sensibly when the database is down, and the
    first surface that has to is this one."""
    monkeypatch.setenv("NEO4J_URI", "bolt://localhost:1")
    with pytest.raises(SystemExit, match="no Neo4j at"):
        _run(monkeypatch, "--root", str(project), "--write")


def test_what_could_not_be_read_is_reported_and_is_not_a_failure(project, monkeypatch, capsys):
    """A pipeline that uses the `sql` hatch is not a broken pipeline.

    And the report is now per **column** when only a column stalled — a model
    with one `count(*)` in it should cost one line, not the whole table.
    """
    (project / "specs" / "agg.yaml").write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "sources": {"orders": "data/orders.csv"},
                "steps": [
                    {
                        "id": "totals",
                        "op": "sql",
                        "inputs": ["orders"],
                        "sql": "SELECT order_id, count(*) AS n FROM orders GROUP BY 1",
                    }
                ],
            },
            sort_keys=False,
        )
    )
    _run(monkeypatch, "--root", str(project))
    assert "agg.n: no input column underneath it" in capsys.readouterr().out

"""Compiling a spec to SQL, and the test that keeps the two paths honest.

The load-bearing one is :func:`test_compiled_sql_matches_the_engine`. Execution
and compilation are separate code paths — the engine nests sub-queries and runs
them here, a compiled file names CTEs and runs somewhere else — and two paths that
can disagree is what this project spent a migration learning to distrust. The ops
are parameterized rather than duplicated so they *can't* drift structurally, but
"can't drift" is a claim, and `TECH_STACK.md` records that during the DuckDB
migration **the golden files did more work than the abstraction did**. So: run
both, compare the tables.
"""

from __future__ import annotations

import csv
from pathlib import Path

import duckdb
import pytest

from portia import pipeline, spec
from portia.core import store


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """Two tiny CSVs with a dirty key, a miss on each side, and a fan-out."""
    data = tmp_path / "data"
    data.mkdir()
    with open(data / "orders.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["order_id", "customer_id", "amount"])
        w.writerows(
            [
                [1, " C1 ", 10],  # whitespace: only matches after the normalize
                [2, "C2", 20],  # fans out — the right side has C2 twice
                [3, "C9", 5],  # unmatched
                [4, None, 7],  # null key
            ]
        )
    with open(data / "customers.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["customer_id", "name"])
        w.writerows([["C1", "Ann"], ["C2", "Bo"], ["C2", "Bo (dup)"], ["C7", "Zed"]])
    return tmp_path


def _doc() -> dict:
    return {
        "version": 1,
        "sources": {"orders": "data/orders.csv", "customers": "data/customers.csv"},
        "steps": [
            {
                "id": "clean_orders",
                "op": "normalize",
                "input": "orders",
                "transforms": [{"column": "customer_id", "op": "strip"}],
            },
            {
                "id": "orders_with_customers",
                "op": "join",
                "left": "clean_orders",
                "right": "customers",
                "keys": ["customer_id"],
                "how": "left",
            },
        ],
    }


def _run(doc: dict, project: Path) -> list[spec.StepResult]:
    return spec.run_spec(doc, base_dir=project, con=store.memory())


def test_every_step_compiles(project: Path) -> None:
    for r in _run(_doc(), project):
        assert r.compiled, f"{r.id} produced no compiled SQL"


def test_compiled_sql_names_its_inputs_instead_of_nesting(project: Path) -> None:
    """The whole point: a CTE body reads a name, it does not inline a sub-query."""
    results = _run(_doc(), project)
    join_step = results[-1]

    assert '"clean_orders" AS "l"' in join_step.compiled
    assert '"customers" AS "r"' in join_step.compiled
    # The executed query inlines everything, including the file reader.
    assert "read_csv" in join_step.table.query
    assert "read_csv" not in join_step.compiled


def test_compiled_sql_matches_the_engine(project: Path) -> None:
    """Run the spec; run the generated file; assert the same table comes out.

    This is the test the whole design rests on. If it fails, compilation is a
    second opinion about what the pipeline does, and the artifact is a liar.
    """
    doc = _doc()
    results = _run(doc, project)
    expected = results[-1].table.head(1000)

    pipeline.write_sources(doc["sources"], root=project)
    pipeline.write_model(results, "specs/orders_with_customers.yaml", root=project)

    # A fresh database that has never seen portia: exactly what a data team has.
    con = duckdb.connect(":memory:")
    try:
        con.execute(f"SET FILE_SEARCH_PATH='{project}'")
        for sql_file in [
            project / pipeline.MODELS_DIR / pipeline.SOURCES_FILE,
            project / pipeline.MODELS_DIR / "orders_with_customers.sql",
        ]:
            con.execute(sql_file.read_text())
        actual = con.execute("SELECT * FROM orders_with_customers").fetch_df()
    finally:
        con.close()

    assert list(actual.columns) == list(expected.columns)
    sort = list(expected.columns)
    left = expected.sort_values(sort).reset_index(drop=True)
    right = actual.sort_values(sort).reset_index(drop=True)
    assert left.equals(right), f"engine:\n{left}\n\ncompiled:\n{right}"


def test_sources_file_uses_relative_paths(project: Path) -> None:
    """An absolute path would pin the generated pipeline to one laptop."""
    sql = pipeline.compile_sources(_doc()["sources"])
    assert "'data/orders.csv'" in sql
    assert str(project) not in sql


def test_a_layer_becomes_a_subdirectory_and_no_layer_does_not(project: Path) -> None:
    flat = pipeline.model_path("specs/orders.yaml", root=project)
    layered = pipeline.model_path("specs/orders.yaml", layer="staging", root=project)

    assert flat == project / "models" / "orders.sql"
    assert layered == project / "models" / "staging" / "orders.sql"


def test_fingerprint_moves_only_when_the_pipeline_does() -> None:
    doc = _doc()
    same = _doc()
    same["steps"][0]["rationale"] = "added prose"

    changed = _doc()
    changed["steps"][0]["transforms"][0]["op"] = "lower"

    assert pipeline.fingerprint(doc) != pipeline.fingerprint(changed)
    # Prose is inside a step, so it does move the digest — documented, not a bug:
    # the guarantee is "change the SQL and it moves", not "only the SQL moves".
    assert pipeline.fingerprint(doc) != pipeline.fingerprint(same)


def test_a_missing_model_is_not_stale(project: Path) -> None:
    """Never generated is a different thing from drifted, and must not warn."""
    assert not pipeline.is_stale("specs/orders_with_customers.yaml", _doc(), root=project)


def test_an_edited_spec_makes_its_model_stale(project: Path) -> None:
    doc = _doc()
    results = _run(doc, project)
    pipeline.write_model(
        results,
        "specs/orders_with_customers.yaml",
        root=project,
        spec_fingerprint=pipeline.fingerprint(doc),
    )
    assert not pipeline.is_stale("specs/orders_with_customers.yaml", doc, root=project)

    doc["steps"][0]["transforms"][0]["op"] = "lower"
    assert pipeline.is_stale("specs/orders_with_customers.yaml", doc, root=project)


def test_a_spec_with_no_steps_refuses_to_compile() -> None:
    with pytest.raises(ValueError, match="no steps"):
        pipeline.compile_spec([], name="empty")

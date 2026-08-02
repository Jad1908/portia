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
from portia.core.io import connect


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
    return spec.run_spec(doc, base_dir=project, con=connect())


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


# --- the whole project, in dependency order ---------------------------------


def _spec_file(project: Path, name: str, doc: dict) -> None:
    directory = project / "specs"
    directory.mkdir(parents=True, exist_ok=True)
    import yaml

    (directory / f"{name}.yaml").write_text(yaml.safe_dump(doc, sort_keys=False))


def _two_layer_project(project: Path) -> None:
    _spec_file(
        project,
        "stg_orders",
        {
            "version": 1,
            "layer": "staging",
            "sources": {"orders": "data/orders.csv"},
            "steps": [
                {
                    "id": "cleaned",
                    "op": "normalize",
                    "input": "orders",
                    "transforms": [{"column": "customer_id", "op": "strip"}],
                }
            ],
        },
    )
    _spec_file(
        project,
        "mart_orders",
        {
            "version": 1,
            "layer": "mart",
            "sources": {"customers": "data/customers.csv"},
            "steps": [
                {
                    "id": "joined",
                    "op": "join",
                    "left": "stg_orders",
                    "right": "customers",
                    "keys": ["customer_id"],
                    "how": "left",
                }
            ],
        },
    )


def test_build_project_writes_a_layered_models_tree(project: Path) -> None:
    _two_layer_project(project)

    built = pipeline.build_project(project)

    assert [m.name for m in built] == ["stg_orders", "mart_orders"]  # dependency order
    assert (project / "models" / "staging" / "stg_orders.sql").exists()
    assert (project / "models" / "mart" / "mart_orders.sql").exists()
    assert (project / "models" / pipeline.SOURCES_FILE).exists()


def test_the_built_pipeline_runs_end_to_end_in_a_fresh_database(project: Path) -> None:
    """The deliverable, exercised the way whoever receives it would run it."""
    _two_layer_project(project)
    built = pipeline.build_project(project)
    expected = built[-1].results[-1].table.head(1000)

    con = duckdb.connect(":memory:")
    try:
        con.execute(f"SET FILE_SEARCH_PATH='{project}'")
        con.execute((project / "models" / pipeline.SOURCES_FILE).read_text())
        for model in built:  # dependency order matters and build_project gives it
            con.execute(model.sql_path.read_text())
        actual = con.execute("SELECT * FROM mart_orders").fetch_df()
    finally:
        con.close()

    sort = list(expected.columns)
    assert (
        expected.sort_values(sort)
        .reset_index(drop=True)
        .equals(actual.sort_values(sort).reset_index(drop=True))
    )


def test_build_reports_a_blocking_zero_rather_than_hiding_it(project: Path) -> None:
    _spec_file(
        project,
        "nothing_survives",
        {
            "version": 1,
            "sources": {"orders": "data/orders.csv"},
            "steps": [
                {
                    "id": "filtered_away",
                    "op": "sql",
                    "inputs": ["orders"],
                    "sql": "SELECT * FROM orders WHERE amount > 1000",
                }
            ],
        },
    )
    built = pipeline.build_project(project)

    assert "empty_output" in built[0].blocking
    # And the file is still written: whether to ship a pipeline with a known zero
    # in it is the human's call, not something a builder decides by hiding it.
    assert built[0].sql_path.exists()


def test_an_unknown_layer_is_refused(project: Path) -> None:
    _spec_file(project, "x", {"version": 1, "layer": "gold", "steps": []})
    with pytest.raises(ValueError, match="unknown layer"):
        pipeline.build_project(project)


def test_stale_models_lists_what_drifted(project: Path) -> None:
    _two_layer_project(project)
    pipeline.build_project(project)
    assert pipeline.stale_models(project) == []

    doc = spec.load_spec(project / "specs" / "stg_orders.yaml")
    doc["steps"][0]["transforms"][0]["op"] = "lower"
    spec.save_spec(doc, project / "specs" / "stg_orders.yaml")

    assert pipeline.stale_models(project) == ["stg_orders"]


def test_write_outputs_writes_one_file_per_model_not_per_step(project: Path) -> None:
    """A spec produces one table; its steps are the working-out (`PIPELINE.md` §2.1).

    This used to drop a CSV per step, so a twelve-step spec deposited twelve files
    and eleven of them were scaffolding. It also described a shape the compiled
    pipeline does not have — in the .sql those steps are CTEs, not tables.
    """
    results = _run(_doc(), project)  # two steps

    written = spec.write_outputs(results, project / "out", name="orders_with_customers")

    assert [p.name for p in written] == ["orders_with_customers.csv"]
    assert sorted(p.name for p in (project / "out").iterdir()) == ["orders_with_customers.csv"]


def test_write_outputs_falls_back_to_the_last_step_id(project: Path) -> None:
    """What a caller holding results but no spec path can honestly say."""
    written = spec.write_outputs(_run(_doc(), project), project / "out")
    assert [p.name for p in written] == ["orders_with_customers.csv"]


# --- building one model, and what it is allowed to touch ---------------------


def _unrelated(project: Path) -> None:
    """A model nothing else reads, so a scoped build has something to leave alone."""
    _spec_file(
        project,
        "stg_customers",
        {
            "version": 1,
            "layer": "staging",
            "sources": {"customers": "data/customers.csv"},
            "steps": [
                {
                    "id": "tidied",
                    "op": "normalize",
                    "input": "customers",
                    "transforms": [{"column": "name", "op": "strip"}],
                }
            ],
        },
    )


def test_building_one_model_builds_what_it_reads(project: Path) -> None:
    """ "Run this spec" means running its inputs — a table isn't built until the
    tables it reads are. The scope is the model plus its upstreams, nothing else.
    """
    _two_layer_project(project)
    _unrelated(project)

    built = pipeline.build_project(project, only="mart_orders")

    assert [m.name for m in built] == ["stg_orders", "mart_orders"]
    assert not (project / "models" / "staging" / "stg_customers.sql").exists()


def test_a_scoped_build_still_writes_every_source(project: Path) -> None:
    """The sources file creates the names *every* model reads. Writing only the
    scoped subset would leave the rest of the pipeline unable to run — a narrower
    build must not break a file it was not asked to touch.
    """
    _two_layer_project(project)
    _unrelated(project)

    pipeline.build_project(project, only="stg_orders")

    sources = (project / "models" / pipeline.SOURCES_FILE).read_text()
    assert '"orders"' in sources
    assert '"customers"' in sources, "a source only the unbuilt models read is still declared"


def test_a_scoped_build_produces_the_same_sql_as_a_full_one(project: Path) -> None:
    """Scope narrows what runs; it never changes what comes out."""
    _two_layer_project(project)

    pipeline.build_project(project)
    full = (project / "models" / "mart" / "mart_orders.sql").read_text()
    (project / "models" / "mart" / "mart_orders.sql").unlink()
    pipeline.build_project(project, only="mart_orders")
    scoped = (project / "models" / "mart" / "mart_orders.sql").read_text()

    assert _without_timestamp(full) == _without_timestamp(scoped)


def test_a_scoped_build_clears_that_models_staleness(project: Path) -> None:
    """Run writes the .sql for what it ran, so the deliverable cannot fall behind
    the spec from inside the app. Anything it did not run stays stale."""
    _two_layer_project(project)
    pipeline.build_project(project)

    doc = spec.load_spec(project / "specs" / "stg_orders.yaml")
    doc["steps"][0]["transforms"] = [{"column": "customer_id", "op": "lower"}]
    _spec_file(project, "stg_orders", doc)
    assert pipeline.stale_models(project) == ["stg_orders"]

    pipeline.build_project(project, only="stg_orders")

    assert pipeline.stale_models(project) == []


def test_upstream_of_orders_a_model_after_everything_it_reads(project: Path) -> None:
    _two_layer_project(project)
    models = spec.discover_specs(project)

    assert spec.upstream_of("mart_orders", models, base_dir=project) == [
        "stg_orders",
        "mart_orders",
    ]
    assert spec.upstream_of("stg_orders", models, base_dir=project) == ["stg_orders"]


def test_upstream_of_an_unknown_model_says_what_there_is(project: Path) -> None:
    _two_layer_project(project)
    models = spec.discover_specs(project)

    with pytest.raises(ValueError, match="no spec produces 'nope'"):
        spec.upstream_of("nope", models, base_dir=project)


def _without_timestamp(sql: str) -> str:
    return "\n".join(line for line in sql.splitlines() if not line.startswith("-- generated "))

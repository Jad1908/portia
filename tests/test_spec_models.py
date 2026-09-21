"""Cross-spec references — one spec reading another's table, by plain name.

`docs/PIPELINE.md` §2.4. No path, no version, no `depends_on` list: a step names
a model and portia works out which spec produces it and in what order. This is
what replaced the refusal that used to live in `agent/handlers.py`.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest
import yaml

from portia import pipeline, spec
from portia.core.io import connect


@pytest.fixture
def project(tmp_path: Path) -> Path:
    data = tmp_path / "data"
    data.mkdir()
    with open(data / "orders.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["order_id", "customer_id", "amount"])
        w.writerows([[1, " C1 ", 10], [2, "C2", 20], [3, "C1", 5]])
    with open(data / "customers.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["customer_id", "name"])
        w.writerows([["C1", "Ann"], ["C2", "Bo"]])
    return tmp_path


def _write(project: Path, name: str, doc: dict, *, subdir: str = "") -> Path:
    directory = project / spec.SPECS_DIR / subdir if subdir else project / spec.SPECS_DIR
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.yaml"
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    return path


def _staging(project: Path) -> Path:
    return _write(
        project,
        "stg_orders",
        {
            "version": 1,
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
        subdir="staging",
    )


def _mart(project: Path) -> Path:
    return _write(
        project,
        "mart_customer_orders",
        {
            "version": 1,
            "sources": {"customers": "data/customers.csv"},
            "steps": [
                {
                    "id": "joined",
                    "op": "join",
                    "left": "stg_orders",  # <- another spec, by plain name
                    "right": "customers",
                    "keys": ["customer_id"],
                    "how": "inner",
                }
            ],
        },
        subdir="marts",
    )


def test_discovery_maps_a_name_to_its_spec(project: Path) -> None:
    _staging(project)
    _mart(project)

    models = spec.discover_specs(project)

    assert set(models) == {"stg_orders", "mart_customer_orders"}
    assert models["stg_orders"].name == "stg_orders.yaml"


def test_duplicate_model_names_are_refused(project: Path) -> None:
    """The one rule referencing-by-name costs us, enforced where names are found."""
    _write(project, "stg_orders", {"version": 1, "steps": []}, subdir="staging")
    _write(project, "stg_orders", {"version": 1, "steps": []}, subdir="marts")

    with pytest.raises(ValueError, match="unique across a project"):
        spec.discover_specs(project)


def test_a_step_reads_another_specs_table_by_name(project: Path) -> None:
    _staging(project)
    mart = _mart(project)
    models = spec.discover_specs(project)

    results = spec.run_spec(spec.load_spec(mart), base_dir=project, con=connect(), models=models)

    # 3 order rows, 2 of them C1 -> both match Ann; C2 matches Bo. The upstream
    # strip is what makes " C1 " match at all, so this also proves the upstream
    # spec really ran rather than the raw file being read.
    assert results[-1].provenance["result_rows"] == 3
    assert results[-1].table is not None
    assert set(results[-1].table.columns) >= {"customer_id", "name", "amount"}


def test_an_unknown_name_says_what_is_available(project: Path) -> None:
    mart = _mart(project)  # stg_orders never written

    with pytest.raises(ValueError, match="not a source, an earlier step, or a model"):
        spec.run_spec(
            spec.load_spec(mart),
            base_dir=project,
            con=connect(),
            models=spec.discover_specs(project),
        )


def test_run_order_puts_dependencies_first(project: Path) -> None:
    _staging(project)
    _mart(project)

    order = spec.run_order(spec.discover_specs(project), base_dir=project)

    assert order.index("stg_orders") < order.index("mart_customer_orders")


def test_a_cycle_raises_rather_than_looping(project: Path) -> None:
    _write(
        project,
        "a",
        {"version": 1, "steps": [{"id": "s", "op": "normalize", "input": "b", "transforms": []}]},
    )
    _write(
        project,
        "b",
        {"version": 1, "steps": [{"id": "s", "op": "normalize", "input": "a", "transforms": []}]},
    )

    with pytest.raises(ValueError, match="cycle"):
        spec.run_order(spec.discover_specs(project), base_dir=project)


def test_a_cross_spec_reference_compiles_to_a_bare_model_name(project: Path) -> None:
    """The compiled mart must say `FROM "stg_orders"` — that is the dbt shape."""
    _staging(project)
    mart = _mart(project)
    models = spec.discover_specs(project)

    results = spec.run_spec(spec.load_spec(mart), base_dir=project, con=connect(), models=models)
    sql = pipeline.compile_spec(results, name="mart_customer_orders")

    assert '"stg_orders" AS "l"' in sql
    # The upstream spec's internals must not leak into the downstream model.
    assert "trim(" not in sql
    assert "read_csv" not in sql


# --- the app must see what the engine sees ----------------------------------


def test_the_app_lists_specs_in_subdirectories(project: Path) -> None:
    """A layered project keeps specs in subdirectories; a one-level glob missed them."""
    from portia.ui import engine
    from portia.ui.state import App

    _staging(project)
    _mart(project)
    app = App()
    app.root = project

    assert sorted(p.name for p in engine.specs_in(app)) == [
        "mart_customer_orders.yaml",
        "stg_orders.yaml",
    ]


def test_the_apps_run_resolves_cross_spec_references(project: Path) -> None:
    """`cli/` and `ui/` are two renderers of one engine (VISION.md).

    Without the model registry the app failed on a spec the CLI ran fine, which is
    exactly the seam breaking.
    """
    import asyncio

    from portia.ui import engine
    from portia.ui.state import App

    _staging(project)
    mart = _mart(project)
    app = App()
    app.root = project
    engine.select_spec(mart, app)

    asyncio.run(engine.run_scope(app, scope={mart.stem}))

    assert app.run_error is None, app.run_error
    assert app.results and app.results[-1].provenance["result_rows"] == 3


def test_the_apps_build_leaves_the_open_specs_results_behind(project: Path) -> None:
    """Build ran every model and then dropped what came out.

    *Save report* is about the spec you have open, so it is armed by
    `app.results` — and a press that compiled the whole project used to leave it
    greyed out: the project had been run and the window had no idea, which reads
    as "nothing was saved". Build and Run go through one `engine.execute` for
    exactly this reason.
    """
    import asyncio

    from portia.ui import engine
    from portia.ui.state import App

    _staging(project)
    mart = _mart(project)
    app = App()
    app.root = project
    engine.select_spec(mart, app)

    built = asyncio.run(engine.execute(app))  # no `only` — the Build button

    assert app.run_error is None, app.run_error
    assert {m.name for m in built} == {"stg_orders", "mart_customer_orders"}
    assert app.results and app.results[-1].provenance["result_rows"] == 3


def test_write_outputs_saves_a_table_per_model_not_just_the_open_one(project: Path) -> None:
    """A build produced three tables and `out/` held one of them.

    *Write outputs* wrote the open spec's table alone, and selecting another spec
    clears the run — so the only table you could ever write was the one currently
    open, over the top of the last one. A project could not accumulate outputs.
    """
    import asyncio

    from portia.ui import engine
    from portia.ui.state import App

    _staging(project)
    mart = _mart(project)
    app = App()
    app.root = project
    engine.select_spec(mart, app)

    asyncio.run(engine.execute(app))
    written = asyncio.run(engine.write_outputs(app))

    assert {p.name for p in written} == {"stg_orders.csv", "mart_customer_orders.csv"}
    # One timestamped folder, and both tables inside it (`pipeline.run_dir`).
    assert len({p.parent for p in written}) == 1
    assert next(iter(written)).parent.parent == project / engine.OUT_DIR


def test_saving_a_table_saves_the_report_of_the_run_that_made_it(project: Path) -> None:
    """One press, not two. A CSV in `out/` whose report was never written is a
    number whose provenance died with the window — and the second press was the
    quiet half nobody made. One report per table, named for it, so a saved run
    answers *which table* rather than *which press*.
    """
    import asyncio

    from portia.ui import engine
    from portia.ui.state import App

    _staging(project)
    mart = _mart(project)
    app = App()
    app.root = project
    engine.select_spec(mart, app)

    asyncio.run(engine.execute(app))
    asyncio.run(engine.write_outputs(app))

    named = sorted(app.reports)
    assert len(named) == 2
    assert any(p.name.endswith("-stg_orders.md") for p in named)
    assert any(p.name.endswith("-mart_customer_orders.md") for p in named)
    # **Inside the run's own folder**, beside the tables it describes — not in a
    # separate `runs/` tree that ages apart from them.
    meta = {p.parent for p in named}
    assert len(meta) == 1
    assert next(iter(meta)).name == pipeline.RUN_META_DIR
    assert (next(iter(meta)) / engine.RUN_INDEX).exists(), "the folder says what it is"
    assert all(p.exists() for p in named)


def test_a_scoped_run_is_the_named_models_and_everything_they_read(project: Path) -> None:
    """Run executes what the canvas is showing. An empty scope is the project —
    which is what the separate Build button used to be."""
    import asyncio

    from portia.ui import engine
    from portia.ui.state import App

    _staging(project)
    mart = _mart(project)
    app = App()
    app.root = project
    engine.select_spec(mart, app)

    one = asyncio.run(engine.run_scope(app, scope={"stg_orders"}))
    assert [m.name for m in one] == ["stg_orders"]

    everything = asyncio.run(engine.run_scope(app, scope=frozenset()))
    assert {m.name for m in everything} == {"stg_orders", "mart_customer_orders"}


def test_rebuilding_a_model_overwrites_that_models_file_and_no_other(project: Path) -> None:
    """Named for the model, which is what makes "overwrite" mean the right thing."""
    import asyncio

    from portia.ui import engine
    from portia.ui.state import App

    _staging(project)
    mart = _mart(project)
    app = App()
    app.root = project
    engine.select_spec(mart, app)

    asyncio.run(engine.execute(app))
    first = asyncio.run(engine.write_outputs(app))
    asyncio.run(engine.execute(app))
    again = asyncio.run(engine.write_outputs(app))

    assert {p.name for p in first} == {p.name for p in again}
    # **Each save is its own folder and nothing is overwritten** (2026-08-16).
    # Two saves is two runs on disk, and the earlier one is still readable —
    # portia does not delete data it produced.
    assert len(list((project / engine.OUT_DIR).glob("*/*.csv"))) == 4


def test_a_build_with_no_spec_open_still_has_tables_worth_writing(project: Path) -> None:
    """*Save report* is about the open spec, so it stays disabled. *Write
    outputs* is about what ran, and a build with nothing open still ran
    something — the tables it produced are each named for their own model, so
    there is no other model's results to borrow."""
    import asyncio

    from portia.ui import engine
    from portia.ui.state import App

    _staging(project)
    app = App()
    app.root = project

    built = asyncio.run(engine.execute(app))

    assert [m.name for m in built] == ["stg_orders"]
    assert app.results is None  # Save report: nothing open to report on
    assert app.built  # Write outputs: a table came out of the build
    written = asyncio.run(engine.write_outputs(app))
    assert [p.name for p in written] == ["stg_orders.csv"]
    assert written[0].parent.parent == project / engine.OUT_DIR


def test_opening_a_spec_disarms_write_outputs(project: Path) -> None:
    """A build the window has stopped showing is not one that button may save."""
    import asyncio

    from portia.ui import engine
    from portia.ui.state import App

    staging = _staging(project)
    mart = _mart(project)
    app = App()
    app.root = project
    engine.select_spec(mart, app)
    asyncio.run(engine.execute(app))
    assert app.built

    engine.select_spec(staging, app)

    assert app.built == []
    assert asyncio.run(engine.write_outputs(app)) == []


def test_discovery_returns_paths_relative_to_the_root_it_was_given(project: Path) -> None:
    """Every caller joins the result back onto a base, so prefixing it here is a
    double-prefix. It hid behind two accidents — joining an *absolute* path
    discards the left side, and a root of "." makes the prefix a no-op — and broke
    on the one remaining shape: a relative root that isn't ".".
    """
    _staging(project)

    models = spec.discover_specs(project)

    assert models["stg_orders"] == Path("specs/staging/stg_orders.yaml")
    assert not models["stg_orders"].is_absolute()
    assert (project / models["stg_orders"]).exists()


def test_a_relative_root_that_is_not_dot_finds_its_specs(project: Path, monkeypatch) -> None:
    """`python -m portia.cli.build --root sandbox/gui` looked under
    `sandbox/gui/sandbox/gui/specs` and found nothing."""
    _staging(project)
    monkeypatch.chdir(project.parent)

    models = spec.discover_specs(project.name)

    assert set(models) == {"stg_orders"}
    assert pipeline.stale_models(project.name) == []


# --- steps nothing reads ------------------------------------------------------


def test_unreachable_steps_are_the_ones_the_table_does_not_read() -> None:
    """A spec's table is its **last** step, so anything outside that step's
    ancestry is a CTE nothing selects from — and work every run still pays for."""
    doc = {
        "steps": [
            {"id": "first_try", "op": "sql", "inputs": ["orders"], "sql": "select 1"},
            {"id": "second_try", "op": "sql", "inputs": ["orders"], "sql": "select 2"},
            {"id": "final", "op": "sql", "inputs": ["second_try"], "sql": "select 3"},
        ]
    }
    assert spec.unreachable_steps(doc) == ["first_try"]


def test_a_chain_where_everything_is_read_has_none() -> None:
    doc = {
        "steps": [
            {"id": "a", "op": "sql", "inputs": ["orders"], "sql": "select 1"},
            {"id": "b", "op": "sql", "inputs": ["a"], "sql": "select 2"},
            {"id": "c", "op": "sql", "inputs": ["b"], "sql": "select 3"},
        ]
    }
    assert spec.unreachable_steps(doc) == []


def test_a_step_read_only_by_another_dead_step_is_dead_too() -> None:
    """Reachability from the table, not "is anything pointing at me" — a pair of
    abandoned steps that read each other would otherwise both look alive."""
    doc = {
        "steps": [
            {"id": "dead_one", "op": "sql", "inputs": ["orders"], "sql": "select 1"},
            {"id": "dead_two", "op": "sql", "inputs": ["dead_one"], "sql": "select 2"},
            {"id": "final", "op": "sql", "inputs": ["orders"], "sql": "select 3"},
        ]
    }
    assert spec.unreachable_steps(doc) == ["dead_one", "dead_two"]


def test_unreachable_steps_reads_a_join_steps_two_sides() -> None:
    doc = {
        "steps": [
            {"id": "left_side", "op": "sql", "inputs": ["orders"], "sql": "select 1"},
            {"id": "spare", "op": "sql", "inputs": ["orders"], "sql": "select 2"},
            {
                "id": "final",
                "op": "join",
                "left": "left_side",
                "right": "customers",
                "keys": ["id"],
            },
        ]
    }
    assert spec.unreachable_steps(doc) == ["spare"]


def test_an_empty_spec_has_no_unreachable_steps() -> None:
    assert spec.unreachable_steps({}) == []
    assert spec.unreachable_steps({"steps": []}) == []


def test_unreachable_steps_keeps_spec_order() -> None:
    """The order is the recorded sequence of decisions; nothing re-sorts it."""
    doc = {
        "steps": [
            {"id": "third_dead", "op": "sql", "inputs": ["orders"], "sql": "select 1"},
            {"id": "first_dead", "op": "sql", "inputs": ["orders"], "sql": "select 2"},
            {"id": "final", "op": "sql", "inputs": ["orders"], "sql": "select 3"},
        ]
    }
    assert spec.unreachable_steps(doc) == ["third_dead", "first_dead"]


# --- where a spec lives -------------------------------------------------------


def test_a_spec_is_placed_by_its_layer(tmp_path: Path) -> None:
    """The decision record and the build output get one shape between them."""
    assert spec.spec_path("stg_orders", layer="staging", root=tmp_path) == (
        tmp_path / "specs" / "staging" / "stg_orders.yaml"
    )
    assert spec.spec_path("orders", root=tmp_path) == tmp_path / "specs" / "orders.yaml"


def test_the_spec_tree_and_the_model_tree_agree(tmp_path: Path) -> None:
    """`specs/staging/x.yaml` beside `models/staging/x.sql` — the same subpath
    under two roots, because they describe the same table."""
    for layer in ("staging", "intermediate", "mart", None):
        as_spec = spec.spec_path("x", layer=layer, root=tmp_path)
        as_model = pipeline.model_path("x.yaml", layer=layer, root=tmp_path)
        assert as_spec.relative_to(tmp_path / spec.SPECS_DIR).parent == (
            as_model.relative_to(tmp_path / pipeline.MODELS_DIR).parent
        )


def test_run_with_nothing_chosen_runs_the_whole_project(project: Path) -> None:
    """`None` is *nothing chosen*, which is the canvas's whole-project state —
    the same tri-state the graph draws from, so Run and what you can see cannot
    disagree about scope. It arrived as `None` and `sorted(None)` raised."""
    import asyncio

    from portia.ui import engine
    from portia.ui.state import App

    _staging(project)
    _mart(project)
    app = App()
    app.root = project
    assert app.visible is None, "the default, and what a fresh window passes"

    built = asyncio.run(engine.run_scope(app, scope=app.visible))
    assert {m.name for m in built} == {"stg_orders", "mart_customer_orders"}

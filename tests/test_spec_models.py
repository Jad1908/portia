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
    with open(data / "orders.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["order_id", "customer_id", "amount"])
        w.writerows([[1, " C1 ", 10], [2, "C2", 20], [3, "C1", 5]])
    with open(data / "customers.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["customer_id", "name"])
        w.writerows([["C1", "Ann"], ["C2", "Bo"]])
    return tmp_path


def _write(project: Path, name: str, doc: dict, *, subdir: str = "") -> Path:
    directory = project / spec.SPECS_DIR / subdir if subdir else project / spec.SPECS_DIR
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.yaml"
    path.write_text(yaml.safe_dump(doc, sort_keys=False))
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

"""Parity: every check and op still emits exactly the evidence it emitted before.

This is the test `docs/DUCKDB_MIGRATION.md` §7 asks for before any implementation
moves. It is not a unit test — the other files in this directory assert that a
number is *right*. This one asserts that a number **did not change**, which is a
different and, for the migration, more valuable claim: the copilot reads these
dicts, so a changed shape is a changed prompt (§2.1).

When a case fails, the useful question is never "how do I make it pass". It is
"did I mean to change this?" — and usually the answer is no, and the test just
caught the thing it exists to catch. The files were written by the pandas engine
before any of it moved, which is where their authority comes from; regenerating
them is a deliberate act, guarded, and explained in `tests/golden.py`.

Also asserted here, so the case list can't quietly stop covering the engine:
every fixture is profiled, every op runs, every transform runs, every join type
runs, and **every blocking flag fires somewhere** — the last one matters most,
because `BLOCKING_FLAGS` is the only place in the system a check stops the loop.
"""

from __future__ import annotations

import json

import pytest

from portia.checks.outcome import BLOCKING_FLAGS
from portia.ops.join import HOWS
from portia.ops.normalize import TRANSFORM_OPS
from tests.golden import BACKENDS, CASES, FIXTURES, GOLDEN_DIR, normalized, run_case

_TYPE_NAMES = (
    "Backend type names, not values. pandas says `int64` and `str`; DuckDB says "
    "`BIGINT` and `VARCHAR`. Anticipated by §6.3 — `inferred` is the field that "
    "carries the meaning, and it is *not* excepted here."
)

_DATE_TYPED = (
    "DuckDB's sniffer types an ISO date column as DATE where pandas kept it as "
    "text, so `inferred` reads `datetime` rather than `categorical`. Accepted "
    "2026-07-27 as a consequence of typed ingest: a date is a date, and the "
    "vocabulary is unchanged (pandas reports `datetime` for a real date column "
    "too). Every other field on these columns still has to match."
)

_ONE_TYPE_PER_COLUMN = (
    "This case profiles the *builder's* frame, whose `mixed_ref` is a pandas "
    "`object` column holding both ints and strings. A database column has one "
    "type, so bridging it makes every value text: samples `[0, 10, 12]` become "
    "`['0', '10', '12']`. Inherent to having a schema, and confined to a fixture "
    "that exists to hold an untyped column — the CSV on disk is text either way."
)

#: Where a backend is *allowed* to differ from the frozen reference evidence, and
#: why. Keyed by backend name, then case name (``"*"`` for every case), then a
#: dotted path into the evidence — ``columns[].dtype`` means "that field, in every
#: element".
#:
#: Every entry is a decision recorded in `docs/DUCKDB_MIGRATION.md`. Nothing goes
#: in here to make a test pass; an undocumented difference is a regression, and
#: the point of §7.2 is that each exception has to be argued for in prose first.
#: An excepted path is a field the golden files stop protecting on that backend,
#: so the list is meant to stay short and each entry is scoped as narrowly as it
#: can be — a case name rather than ``"*"`` wherever the difference is local.
EXCEPTIONS: dict[str, dict[str, dict[str, str]]] = {
    "duckdb": {
        "*": {"columns[].dtype": _TYPE_NAMES},
        "profile/otb": {"columns[].inferred": _DATE_TYPED},
        "profile/city_events": {"columns[].inferred": _DATE_TYPED},
        # The same date typing, reaching the join check: `stay_date`/`event_date`
        # are `datetime` keys rather than `string` ones. `key_dtype_match` is the
        # field that drives a flag and it is deliberately *not* excepted — both
        # sides moved together, so it still reports True.
        "join_report/composite": {"key_dtypes": _DATE_TYPED},
        "join_findings/composite": {"report.key_dtypes": _DATE_TYPED},
        "profile/messy_customers_builder": {
            "columns[].samples": _ONE_TYPE_PER_COLUMN,
            "columns[].top": _ONE_TYPE_PER_COLUMN,
        },
    },
}


def _exceptions(backend_name: str, case_name: str) -> dict[str, str]:
    per_backend = EXCEPTIONS.get(backend_name, {})
    return {**per_backend.get("*", {}), **per_backend.get(case_name, {})}


def _prune(value, paths: dict[str, str]):
    """Drop the excepted paths from an evidence structure, both sides alike."""
    for path in paths:
        _drop(value, path.split("."))
    return value


def _drop(node, parts: list[str]) -> None:
    head, rest = parts[0], parts[1:]
    if head.endswith("[]"):
        for item in node.get(head[:-2]) or []:
            if rest:
                _drop(item, rest)
        return
    if not rest:
        if isinstance(node, dict):
            node.pop(head, None)
        return
    child = node.get(head) if isinstance(node, dict) else None
    if child is not None:
        _drop(child, rest)


@pytest.mark.parametrize("backend", BACKENDS, ids=lambda b: b.name)
@pytest.mark.parametrize("case", CASES, ids=lambda c: c.name)
def test_evidence_matches_golden(case, backend):
    assert case.path.exists(), (
        f"no golden file for {case.name} — generate it with "
        "`python -m tests.golden --regenerate` and commit it with the case that needs it"
    )
    excepted = _exceptions(backend.name, case.name)
    expected = _prune(json.loads(case.path.read_text()), excepted)
    actual = _prune(normalized(run_case(case, backend)), excepted)
    assert actual == expected


def test_no_orphan_golden_files():
    """A deleted case must take its file with it, or parity silently shrinks."""
    on_disk = {
        p.relative_to(GOLDEN_DIR).with_suffix("").as_posix() for p in GOLDEN_DIR.rglob("*.json")
    }
    assert on_disk == {c.name for c in CASES}


def test_case_names_are_unique():
    names = [c.name for c in CASES]
    assert len(names) == len(set(names))


def test_every_fixture_is_profiled():
    profiled = {c.payload["fixture"] for c in CASES if c.kind == "profile"}
    assert profiled == set(FIXTURES)


def _spec_steps():
    for case in CASES:
        if case.kind == "spec":
            yield from case.payload["spec"]["steps"]


def test_every_op_and_transform_runs():
    steps = list(_spec_steps())
    assert {s["op"] for s in steps} == {"join", "normalize", "sql"}
    assert {s["how"] for s in steps if s["op"] == "join" and "how" in s} >= set(HOWS)
    transforms = {t["op"] for s in steps for t in s.get("transforms", [])}
    assert transforms == set(TRANSFORM_OPS)


def test_every_blocking_flag_is_frozen_somewhere():
    """Each zero-condition has a golden case where it actually fires.

    A blocking flag with no frozen example is one the migration could stop
    raising without a single test going red.
    """
    fired = set()
    for case in CASES:
        if case.kind != "spec":
            continue
        for step in json.loads(case.path.read_text())["steps"]:
            fired |= set(step["outcome"]["flags"])
    assert BLOCKING_FLAGS <= fired


def test_prune_removes_nested_and_repeated_paths():
    """The exception mechanism itself, on a shape no live case happens to hit."""
    evidence = {
        "n_rows": 3,
        "columns": [{"name": "a", "dtype": "int64"}, {"name": "b", "dtype": "object"}],
        "grain": {"keys": ["a"], "measurable": True},
    }
    pruned = _prune(evidence, {"columns[].dtype": "why", "grain.measurable": "why"})
    assert pruned == {
        "n_rows": 3,
        "columns": [{"name": "a"}, {"name": "b"}],
        "grain": {"keys": ["a"]},
    }

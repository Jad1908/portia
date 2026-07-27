"""Parity: every check and op still emits exactly the evidence it emitted before.

This is the test `docs/DUCKDB_MIGRATION.md` §7 asks for before any implementation
moves. It is not a unit test — the other files in this directory assert that a
number is *right*. This one asserts that a number **did not change**, which is a
different and, for the migration, more valuable claim: the copilot reads these
dicts, so a changed shape is a changed prompt (§2.1).

When a case fails, the useful question is never "how do I make it pass". It is
"did I mean to change this?". If yes, run ``python -m tests.golden``, read the
diff in ``tests/fixtures/golden/``, and commit it with the reason. If no, the
test just caught the thing it exists to catch.

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

#: Where a backend is *allowed* to differ from the frozen pandas evidence, and
#: why. Keyed by backend name, then case name, then a dotted path into the
#: evidence — ``columns[].dtype`` means "that field, in every element".
#:
#: Every entry is a decision recorded in `docs/DUCKDB_MIGRATION.md`. Nothing goes
#: in here to make a test pass; an undocumented difference is a regression, and
#: the point of §7.2 is that each exception has to be argued for in prose first.
#:
#: Empty while pandas is the only backend.
EXCEPTIONS: dict[str, dict[str, dict[str, str]]] = {}


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
        f"no golden file for {case.name} — generate it with `python -m tests.golden` "
        "and commit it as part of the change that added the case"
    )
    excepted = EXCEPTIONS.get(backend.name, {}).get(case.name, {})
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
    """The exception mechanism itself — unexercised until a second backend lands."""
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

"""The hotel answer key and the hotel case agree, and the key's numbers are the fixture's.

`BENCHMARK_EVAL.md` §7.3: a trap only a person can settle has to be closed by
the scripted user holding the fact, and the key's one truth (136,240) counted
a booking the fixture itself calls a slip. Now the key lists the judgement
calls by id with two variants over the outliers, the case holds a scripted
answer under each id, and both totals are recomputed here from the fixture,
so no number in either file is ever retyped.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from devtools.bench import case as cases
from portia.fixtures import reservations

ROOT = Path(__file__).resolve().parents[1]
KEY = ROOT / "tests" / "fixtures" / "hotels.answers.yaml"
CASE = ROOT / "devtools" / "bench" / "cases" / "hotel.yaml"


def _key() -> dict:
    return yaml.safe_load(KEY.read_text(encoding="utf-8"))


def test_every_judgement_call_in_the_key_has_a_scripted_answer_in_the_case():
    """§7.3 — a trap closed neither by the data nor by the script is a bug in the case."""
    asked = {item["id"] for item in _key()["should_ask_about"]}
    scripted = {fact.id for fact in cases.load(CASE).facts}
    assert asked <= scripted, f"no scripted answer for {asked - scripted}"


def test_the_case_and_the_key_name_the_same_variants_with_the_same_words():
    key = _key()
    outliers = next(item for item in key["should_ask_about"] if item["id"] == "outliers")
    case = cases.load(CASE)
    assert set(outliers["variants"]) == set(case.variants) == {"A", "B"}
    for name, variant in outliers["variants"].items():
        fact = next(f for f in case.with_variant(name).facts if f.id == "outliers")
        assert fact.answer.strip() == variant["answer"].strip()


def test_the_case_and_the_key_share_the_brief():
    assert cases.load(CASE).brief.strip() == _key()["brief"].strip()


def test_each_variants_total_revenue_is_the_fixtures_under_that_answer():
    """The truth is computed from the clean base, never written down first."""
    frame = reservations()
    outliers = next(item for item in _key()["should_ask_about"] if item["id"] == "outliers")
    variants = outliers["variants"]
    assert variants["A"]["total_revenue"] == int(frame["revenue"].sum())
    kept = frame[~frame["booking_id"].isin(variants["B"]["dropped"])]
    assert variants["B"]["total_revenue"] == int(kept["revenue"].sum())
    assert variants["A"]["total_revenue"] != variants["B"]["total_revenue"]

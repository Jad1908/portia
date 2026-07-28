"""Frozen evidence — the mechanism that makes "nothing the copilot reads changes".

`docs/DUCKDB_MIGRATION.md` §7 asks for this before any implementation moves. The
migration's whole promise (§2) is that every evidence dict survives the swap to
DuckDB byte for byte, and that promise is worthless as an intention: it has to be
a file on disk that a test compares against.

So this module is two things and nothing else:

- **A case list** — every check and every op, over every fixture, as data rather
  than as code. A case names what to run and on what; it does not know how.
- **A backend** — the *how*. :class:`PandasBackend` is today's implementation.
  The DuckDB one is added beside it and the same cases run against both, which
  is what §7.3 means by "keep both implementations alive during the migration".

Regenerate the files with ``python -m tests.golden``. **Regenerating is not a
way to fix a failing test** — a diff in ``tests/fixtures/golden/`` is the finding.
Commit it only when the change is intended and the reason is written down.

Two deliberate choices worth knowing:

- **Cases load from ``data/mock/*.csv``, not from the builders.** That is the
  path the migration replaces (``load_frame`` → ``load_table``) and the one the
  copilot exercises. The CSV round-trip changes dtypes, so the one signal that
  only exists in memory — ``mixed_types``, which needs a pandas ``object``
  column — gets its own explicit case.
- **Ops are exercised through ``run_spec`` rather than called directly.** A spec
  is how an op is actually reached in production, it carries the ``outcome``
  measurement and the drift check with it, and it exercises chaining. One kind of
  case covers ``ops/``, ``checks/outcome.py`` and ``spec.py`` at once.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from portia.checks import join as join_checks
from portia.checks import profiling
from portia.core import store
from portia.core.io import load_frame
from portia.core.serialize import to_json, to_jsonable
from portia.core.table import Table
from portia.ops.join import HOWS
from portia.spec import StepResult, load_spec, run_spec

ROOT = Path(__file__).resolve().parents[1]
GOLDEN_DIR = Path(__file__).parent / "fixtures" / "golden"

#: Where a spec's ``sources`` point, relative to the repo root. Cases use the
#: tracked CSVs so a golden file means the same thing on every machine.
MOCK_DIR = "data/mock"

#: Rows of a step's output frozen alongside its provenance. Enough to catch a
#: join that produced the wrong rows, short enough that the files stay readable.
#: Deliberately not `present.PREVIEW_ROWS` — that number is a UI decision.
OUTPUT_ROWS = 5

FIXTURES = (
    "messy_customers",
    "sales_customers",
    "sales_orders",
    "hotels",
    "otb",
    "city_events",
)


@dataclass(frozen=True)
class Case:
    """One frozen evidence dict: what to run, on what, under what name."""

    name: str
    kind: str
    payload: dict = field(default_factory=dict)

    @property
    def path(self) -> Path:
        return GOLDEN_DIR / f"{self.name}.json"


# --- the backends -----------------------------------------------------------


#: Every kind of case there is. A backend declares the subset it can run, and
#: `test_golden` skips the rest — that is how both implementations stay alive
#: while the migration lands module by module (§7.3). The DuckDB set grows by one
#: entry per step; when it holds all of these, the pandas path can be deleted.
ALL_KINDS = frozenset({"profile", "null_rates", "join_report", "join_findings", "spec"})


class PandasBackend:
    """Today's implementation. The DuckDB backend is a sibling of this class.

    Every method here is a seam the migration moves. Nothing else in this module
    imports pandas, so the second backend is a matter of writing the same eight
    methods against `core.table.Table` — not of rewriting the cases.
    """

    name = "pandas"
    kinds = ALL_KINDS

    def source(self, fixture: str):
        return load_frame(ROOT / MOCK_DIR / f"{fixture}.csv")

    def builder(self, fixture: str):
        from portia import fixtures

        return getattr(fixtures, fixture)()

    def profile(self, data) -> dict:
        return profiling.profile_frame(data)

    def null_rates(self, data) -> dict:
        return profiling.null_rates(data)

    def join_report(self, left, right, **keys) -> dict:
        return join_checks.join_report(left, right, **keys)

    def join_findings(self, left, right, **keys) -> dict:
        return join_checks.join_findings(left, right, **keys)

    def run_spec(self, spec: dict) -> list[StepResult]:
        return run_spec(spec, base_dir=ROOT)

    def output(self, frame) -> dict:
        """A step's produced table, small and comparable across backends.

        Rows are **ordered by their own values**, not left in the order the op
        emitted them, because emission order is not a fact about the data. DuckDB
        already proves it: the same ``GROUP BY`` over the same seven rows returns
        three different orderings across six runs (parallel hash aggregation), so
        an order-sensitive preview fails at random today and would fail on every
        case once the engine is DuckDB throughout. Sorted, this still catches the
        thing it is here for — a join that produced the *wrong rows*.
        """
        if frame is None:
            return {}
        rows = [
            {str(k): to_jsonable(v) for k, v in record.items()}
            for record in frame.to_dict("records")
        ]
        rows.sort(key=_row_key)
        return {
            "n_rows": int(len(frame)),
            "columns": [str(c) for c in frame.columns],
            "head": rows[:OUTPUT_ROWS],
        }


class DuckDBBackend:
    """The scale tier. Same cases, same evidence — measured in SQL.

    Sources are **ingested**, exactly as a real project's are, so what the golden
    files compare is the path the product actually takes rather than a view over
    a CSV that nothing else uses.
    """

    name = "duckdb"
    #: Grows a step at a time as `docs/DUCKDB_MIGRATION.md` §8 lands. Everything
    #: absent from here still runs on pandas and is still frozen by the same files.
    kinds = frozenset({"profile", "null_rates", "join_report", "join_findings"})

    def __init__(self):
        self._con = None
        self._ingested: set[str] = set()

    @property
    def con(self):
        if self._con is None:
            self._con = store.memory()
        return self._con

    def source(self, fixture: str) -> Table:
        if fixture not in self._ingested:
            store.ingest(self.con, ROOT / MOCK_DIR / f"{fixture}.csv", name=fixture)
            self._ingested.add(fixture)
        return store.table(self.con, fixture)

    def builder(self, fixture: str) -> Table:
        from portia import fixtures

        return Table.from_frame(getattr(fixtures, fixture)(), f"{fixture}__frame", self.con)

    def profile(self, table: Table) -> dict:
        return profiling.profile_table(table)

    def null_rates(self, table: Table) -> dict:
        return profiling.null_rates_table(table)

    def join_report(self, left: Table, right: Table, **keys) -> dict:
        return join_checks.join_report_table(left, right, **keys)

    def join_findings(self, left: Table, right: Table, **keys) -> dict:
        return join_checks.join_findings_table(left, right, **keys)

    def run_spec(self, spec: dict):
        raise NotImplementedError("spec cases are still pandas — see DuckDBBackend.kinds")

    def output(self, table) -> dict:
        raise NotImplementedError("spec cases are still pandas — see DuckDBBackend.kinds")


def _row_key(row: dict) -> str:
    """A total order over rows that survives mixed types and nulls.

    Sorting the columns themselves would raise the moment a column holds both an
    int and a string — `messy_customers.mixed_ref` does exactly that — so rows are
    ordered by their serialized form instead. Arbitrary, but stable and total,
    which is all a preview needs.
    """
    return json.dumps(row, sort_keys=True, default=str)


BACKENDS = (PandasBackend(), DuckDBBackend())


# --- running a case ---------------------------------------------------------


def run_case(case: Case, backend: Any) -> dict:
    """The evidence a case produces, on one backend."""
    kind, payload = case.kind, case.payload

    if kind == "profile":
        source = backend.builder if payload.get("from_builder") else backend.source
        return backend.profile(source(payload["fixture"]))

    if kind == "null_rates":
        return backend.null_rates(backend.source(payload["fixture"]))

    if kind in ("join_report", "join_findings"):
        left = backend.source(payload["left"])
        right = backend.source(payload["right"])
        keys = {k: payload[k] for k in ("on", "left_on", "right_on") if k in payload}
        run = backend.join_report if kind == "join_report" else backend.join_findings
        return run(left, right, **keys)

    if kind == "spec":
        results = backend.run_spec(payload["spec"])
        return {"steps": [_step_evidence(r, backend) for r in results]}

    raise ValueError(f"unknown case kind {kind!r}")


def _step_evidence(result: StepResult, backend: Any) -> dict:
    """One step, as everything a reader of a run is told about it.

    `provenance` and `outcome` stay separate here for the same reason the run
    report keeps them separate: they answer different questions and they fail
    independently (docs/EVALUATION.md, Run 2).
    """
    return {
        "id": result.id,
        "op": result.op,
        "provenance": result.provenance,
        "drift": result.drift,
        "outcome": result.outcome,
        "acknowledged": result.acknowledged,
        "blocking": result.blocking,
        "rationale": result.rationale,
        "output": backend.output(result.frame),
    }


# --- the case list ----------------------------------------------------------


def _spec(name: str, sources: dict[str, str], steps: list[dict]) -> Case:
    return Case(
        name=f"spec/{name}",
        kind="spec",
        payload={
            "spec": {
                "version": 1,
                "sources": {alias: f"{MOCK_DIR}/{f}.csv" for alias, f in sources.items()},
                "steps": steps,
            }
        },
    )


#: Key pairs worth a report. Between them: fan-out on both sides, a null key,
#: unmatched rows each way, differently-named keys, a dtype mismatch that means
#: zero real matches, and a composite key (the tuple path in `_key_sig`).
JOIN_PAIRS: dict[str, dict] = {
    "sales": {"left": "sales_orders", "right": "sales_customers", "on": ["customer_id"]},
    "otb_hotels": {"left": "otb", "right": "hotels", "on": ["hotel_id"]},
    "hotels_events": {
        "left": "hotels",
        "right": "city_events",
        "left_on": ["city"],
        "right_on": ["city_name"],
    },
    # numeric key against a string key: `key_dtype_mismatch` + `no_matches`. The
    # check never merges, so it reports this where `pd.merge` would raise.
    "dtype_mismatch": {
        "left": "sales_orders",
        "right": "hotels",
        "left_on": ["order_id"],
        "right_on": ["hotel_id"],
    },
    "composite": {
        "left": "otb",
        "right": "city_events",
        "left_on": ["hotel_id", "stay_date"],
        "right_on": ["city_name", "event_date"],
    },
}

_CLEAN_EVENTS = {
    "id": "events_clean",
    "op": "normalize",
    "input": "city_events",
    "transforms": [
        {"column": "city_name", "op": "strip"},
        {"column": "city_name", "op": "lower"},
    ],
}

_CLEAN_HOTELS = {
    "id": "hotels_clean",
    "op": "normalize",
    "input": "hotels",
    "transforms": [{"column": "city", "op": "lower"}],
}

_BOOKINGS = {
    "id": "bookings",
    "op": "join",
    "left": "otb",
    "right": "hotels_clean",
    "keys": ["hotel_id"],
    "how": "left",
    "grain": ["booking_id"],
}

CASES: tuple[Case, ...] = (
    *(Case(f"profile/{f}", "profile", {"fixture": f}) for f in FIXTURES),
    # The builders' frames, not the CSVs. `mixed_types` needs a pandas `object`
    # column holding more than one python type, and a CSV round-trip erases it —
    # so this is the only case where that flag can fire (§6.3 of the migration).
    Case(
        "profile/messy_customers_builder",
        "profile",
        {"fixture": "messy_customers", "from_builder": True},
    ),
    Case("null_rates/messy_customers", "null_rates", {"fixture": "messy_customers"}),
    Case("null_rates/hotels", "null_rates", {"fixture": "hotels"}),
    *(Case(f"join_report/{n}", "join_report", p) for n, p in JOIN_PAIRS.items()),
    *(Case(f"join_findings/{n}", "join_findings", p) for n, p in JOIN_PAIRS.items()),
    # The tracked example spec, run exactly as `python -m portia.cli.run` runs it.
    Case("spec/sales_join", "spec", {"spec": load_spec(ROOT / "specs" / "sales_join.yaml")}),
    _spec(
        "sales_inner_fanout",
        {"orders": "sales_orders", "customers": "sales_customers"},
        [
            {
                "id": "orders_customers",
                "op": "join",
                "left": "orders",
                "right": "customers",
                "keys": ["customer_id"],
                "how": "inner",
                "grain": ["order_id"],
            }
        ],
    ),
    # §6.2, the trap: both sides have a `name` column, so pandas suffixes them
    # `_x`/`_y` and `checks.outcome` traces contribution through those suffixes.
    # SQL has no such convention — this case is what catches its loss.
    _spec(
        "collision_suffixes",
        {"messy": "messy_customers", "sales": "sales_customers"},
        [
            {
                "id": "joined",
                "op": "join",
                "left": "messy",
                "right": "sales",
                "keys": ["customer_id"],
                "how": "inner",
                "grain": ["customer_id"],
                "acknowledge": ["grain_not_unique"],
                "rationale": "customer 1001 is duplicated in the dimension table — a known "
                "data-quality bug, kept rather than silently deduped.",
            }
        ],
    ),
    # The hotel fixture's designed trap, end to end: clean the city spelling,
    # bridge bookings to cities through `hotels`, then join events — which
    # fans out, because cleaning creates a second same-day Paris event.
    _spec(
        "hotels_chain_fanout",
        {"otb": "otb", "hotels": "hotels", "city_events": "city_events"},
        [
            _CLEAN_EVENTS,
            _CLEAN_HOTELS,
            _BOOKINGS,
            {
                "id": "bookings_events",
                "op": "join",
                "left": "bookings",
                "right": "events_clean",
                "left_on": ["city", "stay_date"],
                "right_on": ["city_name", "event_date"],
                "how": "left",
                "grain": ["booking_id"],
            },
        ],
    ),
    # The same project handled correctly: the escape hatch reduces events to one
    # row per city-date *before* the join, so the grain claim holds.
    _spec(
        "hotels_chain_aggregated",
        {"otb": "otb", "hotels": "hotels", "city_events": "city_events"},
        [
            _CLEAN_EVENTS,
            {
                "id": "events_per_city_date",
                "op": "sql",
                "inputs": ["events_clean"],
                "sql": "SELECT city_name, event_date, count(*) AS n_events, "
                "sum(expected_attendance) AS total_attendance "
                "FROM events_clean GROUP BY city_name, event_date",
                "grain": ["city_name", "event_date"],
            },
            _CLEAN_HOTELS,
            _BOOKINGS,
            {
                "id": "bookings_events",
                "op": "join",
                "left": "bookings",
                "right": "events_per_city_date",
                "left_on": ["city", "stay_date"],
                "right_on": ["city_name", "event_date"],
                "how": "left",
                "grain": ["booking_id"],
            },
        ],
    ),
    _spec(
        "normalize_coercion",
        {"messy_customers": "messy_customers"},
        [
            {
                "id": "amounts",
                "op": "normalize",
                "input": "messy_customers",
                "transforms": [
                    {"column": "signup_amount", "op": "strip"},
                    # 'N/A' and 'pending' fail to convert; `fill` then hides the
                    # nulls, which is exactly why n_failed is reported separately.
                    {"column": "signup_amount", "op": "to_numeric", "fill": 0},
                    {"column": "country", "op": "lower"},
                    {"column": "customer_id", "op": "to_string"},
                ],
            }
        ],
    ),
    # A filter that matches nothing -> `empty_output`, the first blocking flag.
    _spec(
        "empty_output",
        {"otb": "otb"},
        [
            {
                "id": "no_such_hotel",
                "op": "sql",
                "inputs": ["otb"],
                "sql": "SELECT * FROM otb WHERE hotel_id = 'H000'",
            }
        ],
    ),
    # Run 2's failure mode, reproduced on purpose (docs/EVALUATION.md): a left
    # join whose keys never match, so an entire source is present in the schema
    # and absent from the data. Covers the two remaining blocking flags at once —
    # every column it brought is `newly_all_null`, so it contributed nothing.
    _spec(
        "no_contribution",
        {"otb": "otb", "city_events": "city_events"},
        [
            {
                "id": "bookings_events",
                "op": "join",
                "left": "otb",
                "right": "city_events",
                "left_on": ["hotel_id"],
                "right_on": ["city_name"],
                "how": "left",
            }
        ],
    ),
    # All four `how`s on one pair, so each one's prediction is pinned against the
    # result it produced. `matches_prediction` is the drift signal; a join type
    # whose formula quietly stopped agreeing would otherwise show up nowhere.
    _spec(
        "join_hows",
        {"orders": "sales_orders", "customers": "sales_customers"},
        [
            {
                "id": f"orders_customers_{how}",
                "op": "join",
                "left": "orders",
                "right": "customers",
                "keys": ["customer_id"],
                "how": how,
            }
            for how in HOWS
        ],
    ),
    # A grain claim naming a column that isn't there -> `grain_columns_missing`.
    _spec(
        "grain_not_measurable",
        {"otb": "otb", "hotels": "hotels"},
        [
            {
                "id": "bookings",
                "op": "join",
                "left": "otb",
                "right": "hotels",
                "keys": ["hotel_id"],
                "how": "left",
                "grain": ["reservation_id"],
            }
        ],
    ),
)


# --- reading and writing the files ------------------------------------------


def dumps(evidence: dict) -> str:
    """Evidence as the bytes that go on disk.

    ``allow_nan=False`` is the point of the extra pass: NaN and Infinity are not
    JSON, and a check that leaks one has broken a promise `core.serialize` makes
    in its own docstring. Better to fail here than to ship a golden file that no
    strict parser can read.
    """
    json.dumps(evidence, allow_nan=False)
    return to_json(evidence) + "\n"


def normalized(evidence: dict) -> Any:
    """Evidence through a JSON round-trip, so tuples and lists compare equal."""
    return json.loads(dumps(evidence))


#: The backend the files are written from. Every other backend is compared
#: *against* them, with its differences declared in `test_golden.EXCEPTIONS` —
#: so the reference survives the deletion of the implementation that produced it.
REFERENCE_BACKEND = BACKENDS[0]


def write_all(backend: Any | None = None) -> list[Path]:
    """(Re)generate every golden file. Returns the paths written."""
    backend = backend or REFERENCE_BACKEND
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    written = []
    for case in CASES:
        case.path.parent.mkdir(parents=True, exist_ok=True)
        case.path.write_text(dumps(run_case(case, backend)))
        written.append(case.path)
    return written


if __name__ == "__main__":
    for path in write_all():
        print(path.relative_to(ROOT))

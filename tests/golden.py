"""Frozen evidence — the mechanism that makes "nothing the copilot reads changes".

`docs/DUCKDB_MIGRATION.md` §7 asks for this before any implementation moves. The
migration's whole promise (§2) is that every evidence dict survives the swap to
DuckDB byte for byte, and that promise is worthless as an intention: it has to be
a file on disk that a test compares against.

So this module is two things and nothing else:

- **A case list** — every check and every op, over every fixture, as data rather
  than as code. A case names what to run and on what; it does not know how.
- **A backend** — the *how*. The cases don't know it exists.

**The files are the pre-migration reference.** They were written by the pandas
engine, before any of it moved (commit "Freeze today's evidence"), and that is
where their authority comes from — they are evidence from an implementation that
could not have been wrong in the same way the new one is. That implementation no
longer exists, so **regenerating them now would rewrite the reference from the
thing it is supposed to be checking.** ``python -m tests.golden`` therefore
demands ``--regenerate`` and says so. A diff under ``tests/fixtures/golden/`` is
a finding, not a chore; commit one only with the reason written down.

Two deliberate choices worth knowing:

- **Cases load from ``data/mock/*.csv``, not from the builders.** That is the
  path the copilot exercises, and the one whose type inference the migration
  changed. One case profiles a builder's frame instead, to pin what bridging an
  untyped pandas column into a typed store does to it.
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
from portia.core.io import connect, load_table
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

#: How large a step's output may get before the harness refuses to sort it in
#: python. Generous for a fixture, small enough to be an obvious mistake.
SORTABLE_ROWS = 10_000

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


class DuckDBBackend:
    """The engine, as the cases reach it. Same evidence, measured in SQL.

    Sources are read **in place**, exactly as a real project's are since the store
    was retired (`docs/PIPELINE.md` §2.7), so what the golden files compare is the
    path the product actually takes. The evidence did not move when that changed:
    ingesting was ``CREATE TABLE … AS <read_query(path)>``, so the reader and its
    null tokens were always the same ones — only the materialization differed.

    The pandas backend that used to sit beside this one is gone with the
    implementations it called (`docs/DUCKDB_MIGRATION.md` §8, step 10). The class
    stays because it is the seam a second tier plugs into — Snowflake is the
    stated next one — and because `EXCEPTIONS` is keyed on its name.
    """

    name = "duckdb"

    def __init__(self):
        self._con = None

    @property
    def con(self):
        if self._con is None:
            self._con = connect()
        return self._con

    def source(self, fixture: str) -> Table:
        return load_table(ROOT / MOCK_DIR / f"{fixture}.csv", self.con, name=fixture)

    def builder(self, fixture: str) -> Table:
        from portia import fixtures

        return Table.from_frame(getattr(fixtures, fixture)(), f"{fixture}__frame", self.con)

    def profile(self, table: Table) -> dict:
        return profiling.profile(table)

    def null_rates(self, table: Table) -> dict:
        return profiling.null_rates(table)

    def join_report(self, left: Table, right: Table, **keys) -> dict:
        return join_checks.join_report(left, right, **keys)

    def join_findings(self, left: Table, right: Table, **keys) -> dict:
        return join_checks.join_findings(left, right, **keys)

    def run_spec(self, spec: dict) -> list[StepResult]:
        return run_spec(spec, base_dir=ROOT, con=connect())

    def output(self, table) -> dict:
        """A step's produced table, ordered by the row's own values.

        Ordered rather than left as the op emitted it, because emission order is
        not a fact about the data: the same ``GROUP BY`` over the same seven rows
        returns three different orderings across six runs. Sorted, this still
        catches the thing it is here for — a step that produced the *wrong rows*.

        Reading every row is safe here and nowhere else: these are fixtures. The
        cap makes that assumption fail loudly if someone adds a big case.
        """
        if table is None:
            return {}
        n_rows = table.count()
        assert n_rows <= SORTABLE_ROWS, (
            f"{table.name} produced {n_rows} rows — golden cases are fixtures, and this "
            "one is large enough that reading it all to sort a 5-row preview is wrong"
        )
        columns = table.columns
        rows = [
            {c: to_jsonable(v) for c, v in zip(columns, row, strict=True)}
            for row in table.rows(n_rows)
        ]
        rows.sort(key=_row_key)
        return {"n_rows": n_rows, "columns": columns, "head": rows[:OUTPUT_ROWS]}


def _row_key(row: dict) -> str:
    """A total order over rows that survives mixed types and nulls.

    Sorting the columns themselves would raise the moment a column holds both an
    int and a string — `messy_customers.mixed_ref` does exactly that — so rows are
    ordered by their serialized form instead. Arbitrary, but stable and total,
    which is all a preview needs.
    """
    return json.dumps(row, sort_keys=True, default=str)


BACKENDS = (DuckDBBackend(),)


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
        "output": backend.output(result.table),
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


def write_all(backend: Any | None = None) -> list[Path]:
    """(Re)generate every golden file. Returns the paths written.

    Read the module docstring first: this overwrites the reference the migration
    is checked against.
    """
    backend = backend or BACKENDS[0]
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    written = []
    for case in CASES:
        case.path.parent.mkdir(parents=True, exist_ok=True)
        case.path.write_text(dumps(run_case(case, backend)))
        written.append(case.path)
    return written


if __name__ == "__main__":
    import sys

    if "--regenerate" not in sys.argv:
        print(__doc__.split("**The files are the pre-migration reference.**")[1].strip())
        print("\nPass --regenerate if that is genuinely what you mean.")
        raise SystemExit(1)
    for path in write_all():
        print(path.relative_to(ROOT))

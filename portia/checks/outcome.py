"""Post-conditions on a produced table — the measuring end of the verification loop.

Every other check in this package reads *inputs*. `profiling` measures a source;
`join` measures what two sources' keys would do to each other. **Nothing measured
what actually came out.** That is how a run shipped a training table whose
`event_name` column was 100% null, with an entire data source silently absent,
and reported no drift while doing it — the prediction had been correct, so the
only post-hoc check in the system was satisfied (docs/EVALUATION.md, "Run 2").

This module is that missing measurement: given the table a step produced and the
tables that went into it, what is true of the result?

Facts only, as everywhere in `checks` — it states what came out, never whether
that is acceptable. The one place it comes close is `BLOCKING_FLAGS`, and the
line drawn there is deliberate: **a flag blocks only when it is a zero.** An
empty table; a column that went in with data and came out entirely null; an
input that contributed no value at all; a declared grain that is not unique.
Those need no threshold to be facts. A 62% null rate and a 1.04x row inflation
are reported as numbers and never block, because deciding whether *those* are
acceptable needs the goal, which the engine does not have (CLAUDE.md, facts vs
judgment). There is no tunable number in the blocking set, on purpose: the
moment one appears, code is deciding what counts as bad.

**Two implementations, one set of rules**, as elsewhere in `checks`. Everything
here except the measuring is name arithmetic — which output column came from
which input — so `_assemble` takes counts and column lists and is shared. That
matters more here than anywhere: the attribution is what makes
`source_did_not_contribute` fire, and it is the subtlest thing in the package.
"""

from __future__ import annotations

import pandas as pd

from portia.checks.profiling import null_rates
from portia.core.present import count
from portia.core.serialize import to_jsonable
from portia.core.table import Table, quote_ident

GRAIN_EXAMPLES = 5  # worst-offending grain keys shown when a grain claim fails

#: The collision suffixes a join gives colliding column names, and the order they
#: are handed out in — left side first.
#:
#: These were ``pandas.merge``'s defaults, inherited for free. SQL has no such
#: convention: a join with colliding names either errors or requires explicit
#: aliasing, so `ops.join` now **produces** them deliberately to keep this working
#: (`docs/DUCKDB_MIGRATION.md` §6.2). Insertion order of ``inputs`` is load-bearing
#: — it is how an output column is traced back to the side it came from, and that
#: attribution is what computes `source_did_not_contribute`.
MERGE_SUFFIXES = ("_x", "_y")

EMPTY_OUTPUT = "empty_output"
ALL_NULL_COLUMN = "all_null_column"
NO_CONTRIBUTION = "source_did_not_contribute"
GRAIN_NOT_UNIQUE = "grain_not_unique"
GRAIN_COLUMNS_MISSING = "grain_columns_missing"

#: The flags a recording gate refuses to write past without an explicit
#: acknowledgement. Every one is a zero-condition — see the module docstring for
#: why nothing threshold-shaped is allowed in here.
BLOCKING_FLAGS = frozenset(
    {EMPTY_OUTPUT, ALL_NULL_COLUMN, NO_CONTRIBUTION, GRAIN_NOT_UNIQUE, GRAIN_COLUMNS_MISSING}
)


def outcome_report(
    frame: pd.DataFrame,
    *,
    inputs: dict[str, pd.DataFrame],
    keys: dict[str, list[str]] | None = None,
    grain: list[str] | None = None,
) -> dict:
    """What is true of ``frame``, given the ``inputs`` that produced it.

    ``inputs`` maps each referenced name to the table that went in, **in the
    order the op consumed them** (left, then right) — that ordering is what makes
    the ``_x``/``_y`` collision suffixes traceable back to a side.

    ``keys`` names each input's join key columns, which are excluded from the
    contribution measurement: a key exists on both sides by construction, so
    counting it would make a join that matched nothing look as though both sides
    had contributed.

    ``grain`` is the caller's claim about what one output row is meant to be. The
    claim is theirs; whether it holds is measured here.
    """
    n_rows = int(len(frame))
    return _assemble(
        n_rows=n_rows,
        columns=[str(c) for c in frame.columns],
        non_null={str(c): int(frame[c].notna().sum()) for c in frame.columns},
        rates=null_rates(frame),
        input_columns={name: [str(c) for c in df.columns] for name, df in inputs.items()},
        input_non_null={
            name: {str(c): int(df[c].notna().sum()) for c in df.columns}
            for name, df in inputs.items()
        },
        keys=keys or {},
        grain=_frame_grain(frame, grain) if (grain and n_rows) else None,
    )


def outcome_report_table(
    table: Table,
    *,
    inputs: dict[str, Table],
    keys: dict[str, list[str]] | None = None,
    grain: list[str] | None = None,
) -> dict:
    """:func:`outcome_report`, measured in SQL. See it for what the fields mean."""
    n_rows, non_null = _non_null_counts(table)
    return _assemble(
        n_rows=n_rows,
        columns=list(non_null),
        non_null=non_null,
        rates={col: _rate(n_rows - present, n_rows) for col, present in non_null.items()},
        input_columns={name: t.columns for name, t in inputs.items()},
        input_non_null={name: _non_null_counts(t)[1] for name, t in inputs.items()},
        keys=keys or {},
        grain=_table_grain(table, grain) if (grain and n_rows) else None,
    )


def _non_null_counts(table: Table) -> tuple[int, dict[str, int]]:
    """Row count and per-column non-null count, in one pass.

    Everything this module measures about a table's *values* comes from here:
    which columns are all-null, what the null rates are, and whether an input put
    anything in. One scan answers all three.
    """
    columns = table.columns
    if not columns:
        return table.count(), {}
    exprs = {"n_rows": "count(*)"}
    exprs |= {f"c{i}": f"count({quote_ident(c)})" for i, c in enumerate(columns)}
    stats = table.row(exprs)
    return int(stats["n_rows"]), {c: int(stats[f"c{i}"]) for i, c in enumerate(columns)}


def _rate(n_null: int, n: int) -> float:
    from portia.core.serialize import round_float

    return round_float(n_null / n) if n else 0.0


# --- the rules, shared by both implementations ------------------------------


def _assemble(
    *,
    n_rows: int,
    columns: list[str],
    non_null: dict[str, int],
    rates: dict[str, float],
    input_columns: dict[str, list[str]],
    input_non_null: dict[str, dict[str, int]],
    keys: dict[str, list[str]],
    grain: dict | None,
) -> dict:
    report: dict = {
        "n_rows": n_rows,
        "n_cols": len(columns),
        "empty": n_rows == 0,
        "null_rates": {c: r for c, r in rates.items() if r},
        "all_null_columns": [],
        "newly_all_null_columns": [],
        "contribution": {},
        # Stays None on an empty table: a grain claim over zero rows is trivially
        # unique, and reporting that as verified would be the exact kind of
        # vacuous pass this module exists to stop.
        "grain": None,
    }

    if n_rows:
        report["grain"] = grain
        attribution = _attribute(columns, input_columns)
        report["all_null_columns"] = [c for c in columns if not non_null[c]]
        report["newly_all_null_columns"] = [
            col
            for col in report["all_null_columns"]
            if any(input_non_null[name][src] for name, src in attribution.get(col, []))
        ]
        report["contribution"] = {
            name: _contribution(
                columns, non_null, cols, excluded=keys.get(name, []), position=position
            )
            for position, (name, cols) in enumerate(input_columns.items())
        }

    report["flags"] = _flags(report)
    return report


def _flags(report: dict) -> list[str]:
    flags: list[str] = []
    if report["empty"]:
        return [EMPTY_OUTPUT]  # nothing else is measurable, so don't pile on
    if report["newly_all_null_columns"]:
        flags.append(ALL_NULL_COLUMN)
    if any(c["contributed"] is False for c in report["contribution"].values()):
        flags.append(NO_CONTRIBUTION)
    grain = report["grain"]
    if grain and not grain["measurable"]:
        flags.append(GRAIN_COLUMNS_MISSING)
    elif grain and not grain["unique"]:
        flags.append(GRAIN_NOT_UNIQUE)
    return flags


def _suffix(position: int) -> str:
    """The collision suffix this input's columns would have been given."""
    return MERGE_SUFFIXES[position] if position < len(MERGE_SUFFIXES) else ""


def _output_column(col: str, suffix: str, output_columns) -> str | None:
    """Which output column, if any, carries this input column's values.

    Suffixed first: a join only renames on collision, so when ``col_x`` exists it
    *is* this side's copy and a bare ``col`` would be something else entirely.
    (A source column genuinely named ``revenue_x`` could be mis-attributed here;
    the cost is confined to one step's contribution report, and the alternative
    is threading the join's internals through the whole layer.)
    """
    if suffix and f"{col}{suffix}" in output_columns:
        return f"{col}{suffix}"
    if col in output_columns:
        return str(col)
    return None


def _attribute(columns: list[str], input_columns: dict[str, list[str]]) -> dict[str, list]:
    """Output column -> the ``(input name, input column)`` pairs feeding it."""
    attribution: dict[str, list] = {}
    for position, (name, cols) in enumerate(input_columns.items()):
        for col in cols:
            out_col = _output_column(str(col), _suffix(position), columns)
            if out_col is not None:
                attribution.setdefault(out_col, []).append((name, col))
    return attribution


def _contribution(
    columns: list[str],
    non_null: dict[str, int],
    input_cols: list[str],
    *,
    excluded: list[str],
    position: int,
) -> dict:
    """Whether this input actually put any data into the output.

    ``contributed`` is None rather than False when the input has no non-key
    columns to judge — a key-only lookup table contributes by filtering, and
    calling that "did not contribute" would be a false alarm.
    """
    suffix = _suffix(position)
    skip = set(excluded)
    reached: dict[str, str] = {}
    missing: list[str] = []
    for col in input_cols:
        if col in skip:
            continue
        out_col = _output_column(str(col), suffix, columns)
        if out_col is None:
            missing.append(str(col))
        else:
            reached[str(col)] = out_col

    return {
        "columns_in_output": sorted(reached.values()),
        "n_columns": len(reached),
        "columns_dropped": missing,
        "contributed": (
            any(non_null[out_col] for out_col in reached.values()) if reached else None
        ),
    }


def _grain(keys: list[str], missing: list[str]) -> dict | None:
    """The not-measurable half of a grain report — shared, and returned early."""
    return {"keys": keys, "measurable": False, "missing_columns": missing} if missing else None


def _grain_report(
    keys: list[str], *, n_distinct: int, n_duplicated: int, worst: list[tuple[list, int]]
) -> dict:
    """A measured grain claim.

    ``worst`` is the duplicated keys with the most rows, biggest first, already
    capped at :data:`GRAIN_EXAMPLES` — so ``n_duplicated`` is passed separately
    rather than inferred from its length, which would silently cap the count too.
    """
    return {
        "keys": keys,
        "measurable": True,
        "unique": n_duplicated == 0,
        "n_distinct": n_distinct,
        "n_duplicated_keys": n_duplicated,
        "max_multiplicity": worst[0][1] if worst else 1,
        "examples": [{**dict(zip(keys, values, strict=True)), "n_rows": n} for values, n in worst],
    }


# --- measuring a grain claim ------------------------------------------------


def _table_grain(table: Table, grain: list[str]) -> dict:
    keys = [str(c) for c in grain]
    early = _grain(keys, [c for c in keys if c not in table.columns])
    if early:
        return early

    quoted = ", ".join(quote_ident(c) for c in keys)
    # No `dropna` to think about: SQL groups NULLs together, which is what
    # pandas' `dropna=False` was asking for. A null in the grain key is a fact
    # worth seeing, not a row to quietly leave out of the count.
    grouped = f"SELECT {quoted}, count(*) AS n FROM ({table.query}) GROUP BY {quoted}"
    n_distinct = int(table.con.execute(f"SELECT count(*) FROM ({grouped})").fetchone()[0])
    # Ties broken by the key, so which duplicates get shown doesn't depend on
    # hash order — the same run twice must name the same examples.
    rows = table.con.execute(
        f"SELECT * FROM ({grouped}) WHERE n > 1 ORDER BY n DESC, {quoted} LIMIT {GRAIN_EXAMPLES}"
    ).fetchall()
    n_duplicated = int(
        table.con.execute(f"SELECT count(*) FROM ({grouped}) WHERE n > 1").fetchone()[0]
    )
    return _grain_report(
        keys,
        n_distinct=n_distinct,
        n_duplicated=n_duplicated,
        worst=[([to_jsonable(v) for v in row[:-1]], int(row[-1])) for row in rows],
    )


def _frame_grain(frame: pd.DataFrame, grain: list[str]) -> dict:
    keys = [str(c) for c in grain]
    early = _grain(keys, [c for c in keys if c not in frame.columns])
    if early:
        return early

    counts = frame.groupby(keys, dropna=False).size()
    duplicated = counts[counts > 1]
    duplicated = duplicated.loc[sorted(duplicated.index, key=str)].sort_values(
        ascending=False, kind="stable"
    )
    return _grain_report(
        keys,
        n_distinct=int(len(counts)),
        n_duplicated=int(len(duplicated)),
        worst=[(_key_values(key), int(n)) for key, n in duplicated.head(GRAIN_EXAMPLES).items()],
    )


def _key_values(key) -> list:
    """A groupby index entry as a list — scalar for one key, tuple for several."""
    values = key if isinstance(key, tuple) else (key,)
    return [to_jsonable(v) for v in values]


def render_outcome(outcome: dict) -> str:
    """Human-readable post-conditions, for the CLI. Indented to sit under a step."""
    lines = [f"    produced {outcome['n_rows']} rows × {outcome['n_cols']} cols"]

    for name, c in outcome["contribution"].items():
        if c["contributed"] is False:
            lines.append(f"    ✗ {name} contributed no values ({c['n_columns']} columns, all null)")
        if c["columns_dropped"]:
            lines.append(f"    · {name} columns not in output: {', '.join(c['columns_dropped'])}")

    if outcome["newly_all_null_columns"]:
        lines.append(f"    ✗ became all-null: {', '.join(outcome['newly_all_null_columns'])}")

    grain = outcome["grain"]
    if grain and not grain["measurable"]:
        lines.append(
            f"    ✗ grain {grain['keys']} not measurable — no such column(s): "
            f"{', '.join(grain['missing_columns'])}"
        )
    elif grain and not grain["unique"]:
        lines.append(
            f"    ✗ grain {grain['keys']} not unique — {grain['n_duplicated_keys']} duplicated "
            f"key(s), up to {grain['max_multiplicity']} rows each"
        )
        lines += [f"        {ex}" for ex in grain["examples"]]
    elif grain:
        lines.append(f"    ✓ grain {grain['keys']} is unique ({grain['n_distinct']} rows)")

    if outcome["flags"]:
        lines.append(f"    ⚑ {', '.join(outcome['flags'])}")
    return "\n".join(lines)


def describe_contribution(contribution: dict) -> str:
    """What one input actually put into the output, on one line.

    Paired with `outcome_report` rather than living in a renderer, because every
    surface says this and they must say it the same way: the app's report pane,
    the saved markdown report, and anything else that grows one.
    """
    reached = contribution.get("n_columns")
    contributed = contribution.get("contributed")
    parts = [count(reached, "column") + " in output" if reached is not None else "—"]
    if contributed is False:
        parts.append("contributed nothing")
    elif contributed is True:
        parts.append("contributed")
    else:
        parts.append("no non-key columns to judge")
    if contribution.get("columns_dropped"):
        parts.append(f"dropped {', '.join(contribution['columns_dropped'])}")
    return " · ".join(parts)


def describe_grain(grain: dict) -> str:
    """The caller's grain claim, and whether it held. Facts, not a verdict."""
    keys = ", ".join(grain.get("keys") or [])
    if not grain.get("measurable"):
        missing = ", ".join(grain.get("missing_columns") or [])
        return f"[{keys}] · not measurable · missing {missing}"
    if grain.get("unique"):
        return f"[{keys}] · unique · {count(grain.get('n_distinct', 0), 'row')}"
    return (
        f"[{keys}] · not unique · {count(grain.get('n_duplicated_keys', 0), 'duplicated key')}"
        f" · up to {count(grain.get('max_multiplicity', 0), 'row')} each"
    )

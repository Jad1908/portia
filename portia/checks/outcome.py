"""Post-conditions on a produced table — the measuring end of the verification loop.

Every other check in this package reads *inputs*. `profiling` measures a source;
`join` measures what two sources' keys would do to each other. **Nothing measured
what actually came out.** That is how a run shipped a training table whose
`event_name` column was 100% null, with an entire data source silently absent,
and reported no drift while doing it — the prediction had been correct, so the
only post-hoc check in the system was satisfied (docs/EVALUATION.md, "Run 2").

This module is that missing measurement: given the frame a step produced and the
frames that went into it, what is true of the result?

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
"""

from __future__ import annotations

import pandas as pd

from portia.checks.profiling import null_rates
from portia.core.present import count
from portia.core.serialize import to_jsonable

GRAIN_EXAMPLES = 5  # worst-offending grain keys shown when a grain claim fails

#: ``pandas.merge``'s default collision suffixes. ``ops.join`` does not override
#: them, so this is how an output column is traced back to the side it came from.
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

    ``inputs`` maps each referenced name to the frame that went in, **in the
    order the op consumed them** (left, then right) — that ordering is what makes
    pandas' ``_x``/``_y`` collision suffixes traceable back to a side.

    ``keys`` names each input's join key columns, which are excluded from the
    contribution measurement: a key exists on both sides by construction, so
    counting it would make a join that matched nothing look as though both sides
    had contributed.

    ``grain`` is the caller's claim about what one output row is meant to be. The
    claim is theirs; whether it holds is measured here.
    """
    key_columns = keys or {}
    n_rows = int(len(frame))
    report: dict = {
        "n_rows": n_rows,
        "n_cols": int(frame.shape[1]),
        "empty": n_rows == 0,
        "null_rates": {c: r for c, r in null_rates(frame).items() if r},
        "all_null_columns": [],
        "newly_all_null_columns": [],
        "contribution": {},
        # Stays None on an empty frame: a grain claim over zero rows is trivially
        # unique, and reporting that as verified would be the exact kind of
        # vacuous pass this module exists to stop.
        "grain": None,
    }

    if n_rows:
        report["grain"] = _grain_report(frame, grain) if grain else None
        attribution = _attribute(frame, inputs)
        report["all_null_columns"] = [str(c) for c in frame.columns if frame[c].isna().all()]
        report["newly_all_null_columns"] = [
            col
            for col in report["all_null_columns"]
            if any(inputs[name][src].notna().any() for name, src in attribution.get(col, []))
        ]
        report["contribution"] = {
            name: _contribution(frame, df, excluded=key_columns.get(name, []), position=i)
            for i, (name, df) in enumerate(inputs.items())
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
    """The collision suffix pandas would have given this input's columns."""
    return MERGE_SUFFIXES[position] if position < len(MERGE_SUFFIXES) else ""


def _output_column(col: str, suffix: str, output_columns) -> str | None:
    """Which output column, if any, carries this input column's values.

    Suffixed first: pandas only renames on collision, so when ``col_x`` exists it
    *is* this side's copy and a bare ``col`` would be something else entirely.
    (A source column genuinely named ``revenue_x`` could be mis-attributed here;
    the cost is confined to one step's contribution report, and the alternative
    is threading pandas' internals through the whole layer.)
    """
    if suffix and f"{col}{suffix}" in output_columns:
        return f"{col}{suffix}"
    if col in output_columns:
        return str(col)
    return None


def _attribute(frame: pd.DataFrame, inputs: dict[str, pd.DataFrame]) -> dict[str, list]:
    """Output column -> the ``(input name, input column)`` pairs feeding it."""
    attribution: dict[str, list] = {}
    for position, (name, df) in enumerate(inputs.items()):
        for col in df.columns:
            out_col = _output_column(str(col), _suffix(position), frame.columns)
            if out_col is not None:
                attribution.setdefault(out_col, []).append((name, col))
    return attribution


def _contribution(
    frame: pd.DataFrame, df: pd.DataFrame, *, excluded: list[str], position: int
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
    for col in df.columns:
        if col in skip:
            continue
        out_col = _output_column(str(col), suffix, frame.columns)
        if out_col is None:
            missing.append(str(col))
        else:
            reached[str(col)] = out_col

    return {
        "columns_in_output": sorted(reached.values()),
        "n_columns": len(reached),
        "columns_dropped": missing,
        "contributed": (
            any(frame[out_col].notna().any() for out_col in reached.values()) if reached else None
        ),
    }


def _grain_report(frame: pd.DataFrame, grain: list[str]) -> dict:
    """Measure the caller's claim that one output row is one ``grain`` key."""
    keys = [str(c) for c in grain]
    missing = [c for c in keys if c not in frame.columns]
    if missing:
        return {"keys": keys, "measurable": False, "missing_columns": missing}

    # dropna=False: a null in the grain key is a fact worth seeing, not a row to
    # quietly leave out of the count.
    counts = frame.groupby(keys, dropna=False).size()
    duplicated = counts[counts > 1].sort_values(ascending=False)
    return {
        "keys": keys,
        "measurable": True,
        "unique": bool(duplicated.empty),
        "n_distinct": int(len(counts)),
        "n_duplicated_keys": int(len(duplicated)),
        "max_multiplicity": int(counts.max()) if len(counts) else 0,
        "examples": [
            {**dict(zip(keys, _key_values(key), strict=True)), "n_rows": int(n)}
            for key, n in duplicated.head(GRAIN_EXAMPLES).items()
        ],
    }


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

"""Execute a join — the decided counterpart to ``checks.join.join_report``.

The op takes the *resolved decision* (which keys, which ``how``) that the report
provoked, builds the join, and re-emits the drop report on the actual result.
Because ``join_report`` predicted the outcome from the keys alone, the provenance
records both the prediction and the reality and asserts they agree — so if the
data shifted under a re-run, the mismatch is visible, not silent.

The agent never writes this join by hand; it selects `how` and `keys` and this
deterministic op does the rest. Rigor lives here (docs/PLAN.md).

**The output is a relation, not rows.** Only ``result_rows`` is computed, which
is a ``count(*)`` — so the 80M-row fan-out the check warns about is confirmed
rather than materialized, and a step that produces it costs no more to record
than one that filters.
"""

from __future__ import annotations

from portia.checks.join import join_report
from portia.checks.outcome import MERGE_SUFFIXES
from portia.core.table import Table, quote_ident
from portia.ops.base import OpResult, named_from

HOWS = ("inner", "left", "right", "outer")

#: portia's join vocabulary, in SQL. ``outer`` is SQL's ``FULL OUTER`` — the spec
#: format kept pandas' word, and a spec written a year ago has to keep working.
_SQL_JOIN = {
    "inner": "INNER JOIN",
    "left": "LEFT JOIN",
    "right": "RIGHT JOIN",
    "outer": "FULL JOIN",
}

#: Every field this op reports. Declared so a spec's ``expect`` block can be
#: validated against what the op *actually* measures — an expectation on a field
#: that doesn't exist drifts on every run and teaches everyone to ignore drift.
#: `tests/test_ops_join.py` asserts this matches a real run, so it can't rot.
PROVENANCE_KEYS = frozenset(
    {
        "op",
        "how",
        "keys",
        "relationship",
        "input_rows",
        "result_rows",
        "predicted_rows",
        "matches_prediction",
        "left_dropped",
        "right_dropped",
        "flags",
    }
)


def apply_join(
    left: Table,
    right: Table,
    *,
    how: str = "inner",
    on: str | list[str] | None = None,
    left_on: str | list[str] | None = None,
    right_on: str | list[str] | None = None,
    name: str = "join",
) -> OpResult:
    """Build a join and return the table plus its provenance drop report."""
    if how not in HOWS:
        raise ValueError(f"how must be one of {HOWS}, got {how!r}")

    diagnosis = join_report(left, right, on=on, left_on=left_on, right_on=right_on)
    lkeys = diagnosis["keys"]["left"]
    rkeys = diagnosis["keys"]["right"]

    select = _select_list(left, right, lkeys, rkeys, shared_names=on is not None)
    condition = " AND ".join(
        f"l.{quote_ident(lk)} = r.{quote_ident(rk)}" for lk, rk in zip(lkeys, rkeys, strict=True)
    )

    def build(left_from: str, right_from: str) -> str:
        return f"SELECT {select} FROM {left_from} {_SQL_JOIN[how]} {right_from} ON {condition}"

    merged = Table(
        name=name,
        query=build(f"({left.query}) AS l", f"({right.query}) AS r"),
        con=left.con,
    )
    # The same builder, told to name its inputs instead of inlining them. See
    # `OpResult.compiled` for why this is one implementation and not two.
    compiled = build(named_from(left, "l"), named_from(right, "r"))

    result_rows = merged.count()
    predicted = diagnosis["joins"][how]
    provenance = {
        "op": "join",
        "how": how,
        "keys": diagnosis["keys"],
        "relationship": diagnosis["relationship"],
        "input_rows": {"left": diagnosis["left"]["n_rows"], "right": diagnosis["right"]["n_rows"]},
        "result_rows": result_rows,
        "predicted_rows": predicted["result_rows"],
        # The prediction and the built result must agree; if not, the source data
        # changed since the report was formed (drift signal).
        "matches_prediction": result_rows == predicted["result_rows"],
        "left_dropped": predicted["left_dropped"],
        "right_dropped": predicted["right_dropped"],
        "flags": diagnosis["flags"],
    }
    return OpResult(table=merged, provenance=provenance, compiled=compiled)


def _select_list(
    left: Table, right: Table, lkeys: list[str], rkeys: list[str], *, shared_names: bool
) -> str:
    """The output columns, and what they are called.

    **This function is why `checks.outcome` still works.** Nothing in SQL renames
    a colliding column for you — a join naming ``name`` on both sides either errors
    or needs explicit aliasing. `outcome` traces an output column back to the input
    that produced it through the ``_x``/``_y`` suffixes, and that attribution is
    what fires `source_did_not_contribute`. So the suffixes are produced here,
    deliberately, reproducing what `pandas.merge` did for free
    (`docs/DUCKDB_MIGRATION.md` §6.2). `MERGE_SUFFIXES` is imported rather than
    spelled again: two copies of a convention is how a convention dies.

    Column order matches what the frozen evidence expects: every left column in
    its original position, then the right's — minus the key, when both sides call
    it the same thing and it therefore appears once.
    """
    left_suffix, right_suffix = MERGE_SUFFIXES
    lcols = left.columns
    right_out = [c for c in right.columns if not (shared_names and c in rkeys)]
    collisions = set(lcols) & set(right_out)

    key_of = dict(zip(lkeys, rkeys, strict=True))
    select = []
    for col in lcols:
        q = quote_ident(col)
        if shared_names and col in key_of:
            # One key column, taking whichever side has it: a right or outer join
            # leaves the left side null on rows the left didn't have.
            select.append(f"coalesce(l.{q}, r.{quote_ident(key_of[col])}) AS {q}")
        elif col in collisions:
            select.append(f"l.{q} AS {quote_ident(col + left_suffix)}")
        else:
            select.append(f"l.{q} AS {q}")
    for col in right_out:
        q = quote_ident(col)
        alias = quote_ident(col + right_suffix) if col in collisions else q
        select.append(f"r.{q} AS {alias}")
    return ", ".join(select)

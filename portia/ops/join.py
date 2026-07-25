"""Execute a join — the decided counterpart to ``checks.join.join_report``.

The op takes the *resolved decision* (which keys, which ``how``) that the report
provoked, materializes the join, and re-emits the drop report on the actual
result. Because ``join_report`` predicted the outcome from the keys alone, the
provenance records both the prediction and the reality and asserts they agree —
so if the data shifted under a re-run, the mismatch is visible, not silent.

The agent never writes this merge by hand; it selects `how` and `keys` and this
deterministic op does the rest. Rigor lives here (docs/PLAN.md).
"""

from __future__ import annotations

import pandas as pd

from portia.checks.join import join_report
from portia.ops.base import OpResult

HOWS = ("inner", "left", "right", "outer")

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
    left: pd.DataFrame,
    right: pd.DataFrame,
    *,
    how: str = "inner",
    on: str | list[str] | None = None,
    left_on: str | list[str] | None = None,
    right_on: str | list[str] | None = None,
) -> OpResult:
    """Materialize a join and return the table plus its provenance drop report."""
    if how not in HOWS:
        raise ValueError(f"how must be one of {HOWS}, got {how!r}")

    diagnosis = join_report(left, right, on=on, left_on=left_on, right_on=right_on)

    merge_keys: dict = {"on": on} if on is not None else {"left_on": left_on, "right_on": right_on}
    merged = pd.merge(left, right, how=how, **merge_keys)

    predicted = diagnosis["joins"][how]
    provenance = {
        "op": "join",
        "how": how,
        "keys": diagnosis["keys"],
        "relationship": diagnosis["relationship"],
        "input_rows": {"left": int(len(left)), "right": int(len(right))},
        "result_rows": int(len(merged)),
        "predicted_rows": predicted["result_rows"],
        # The prediction and the materialized result must agree; if not, the
        # source data changed since the report was formed (drift signal).
        "matches_prediction": int(len(merged)) == predicted["result_rows"],
        "left_dropped": predicted["left_dropped"],
        "right_dropped": predicted["right_dropped"],
        "flags": diagnosis["flags"],
    }
    return OpResult(frame=merged, provenance=provenance)

"""Compact, JSON-safe serialization — shared by every check and tool.

Deterministic checks emit structured *evidence* that the copilot reads instead
of the raw data (docs/PLAN.md). That evidence has two hard requirements:

- **JSON round-trippable** — numpy/pandas scalars become plain python.
- **token-lean** — floats are rounded; we never dump full value lists.

This is the single place those rules live. A check that hand-rolls its own
numpy→python coercion is a bug waiting to happen (``int64`` isn't JSON
serializable, ``NaN`` isn't valid JSON) — always go through here.
"""

from __future__ import annotations

import json
import math
from decimal import Decimal
from typing import Any

FLOAT_ROUND = 4  # decimal places for every reported float, everywhere


def round_float(x: float) -> float:
    return round(float(x), FLOAT_ROUND)


def to_jsonable(v: Any) -> Any:
    """Coerce a single scalar to a JSON-serializable python value.

    Handles what both tiers hand back: numpy and pandas scalars from a frame, and
    DuckDB's own types from a query. ``Decimal`` is called out because it is the
    one that would otherwise land in the evidence as a *string* — the ``str()``
    fallback is right for a date and wrong for a number, and a price the copilot
    reads as ``"1.5"`` rather than ``1.5`` is a quiet type error in a prompt.
    Dates and UUIDs do want the fallback: ISO text is the JSON form.
    """
    if v is None:
        return None
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    if isinstance(v, Decimal):
        return None if v.is_nan() or v.is_infinite() else round_float(float(v))
    item = getattr(v, "item", None)
    if callable(item):  # numpy scalar -> python scalar
        try:
            v = v.item()
        except (ValueError, TypeError):
            v = str(v)
    if isinstance(v, bool):
        return v
    if isinstance(v, float):
        return round_float(v)
    if isinstance(v, (int, str)) or v is None:
        return v
    return str(v)


def to_json(obj: Any) -> str:
    """Serialize an already-jsonable evidence dict to a stable, readable string."""
    return json.dumps(obj, indent=2, ensure_ascii=False)


def to_json_line(obj: Any) -> str:
    """One object, one line — the JSONL form, for the run log (`portia/runlog.py`).

    Two things differ from `to_json` and both are the format's doing. The
    readable indent is wrong when a newline ends the record, and ``default=str``
    is the last-resort coercion for whatever the SDK hands back inside an
    event's payload — nested, not scalar, so `to_jsonable` (which stringifies
    anything that isn't a scalar) can't do the job. A log line that raises
    mid-turn would lose the transcript it exists to keep.
    """
    return json.dumps(obj, ensure_ascii=False, default=str)

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
from typing import Any

FLOAT_ROUND = 4  # decimal places for every reported float, everywhere


def round_float(x: float) -> float:
    return round(float(x), FLOAT_ROUND)


def format_rate(rate: float | None) -> str:
    """A 0–1 rate as a percentage, for any surface that shows one to a human.

    Here rather than in a renderer because both edges show rates and they must
    not disagree — a null rate read off the terminal and the same rate read off
    the app have to be the same string.

    Rounds to whole percent, with one exception: a rate that is **not zero** but
    rounds to zero renders ``<1%``. "0%" for a column that does have nulls is the
    kind of quiet lie this project exists to prevent.
    """
    if rate is None:
        return "—"
    if rate > 0 and round(rate * 100) == 0:
        return "<1%"
    return f"{rate:.0%}"


def to_jsonable(v: Any) -> Any:
    """Coerce a single numpy/pandas scalar to a JSON-serializable python value."""
    if v is None:
        return None
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
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

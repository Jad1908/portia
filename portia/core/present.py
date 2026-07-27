"""One way to turn a measured value into a short human string.

Every surface shows the same numbers — the terminal, the app, and the saved run
report — and the day two of them disagree about a null rate is the day someone
has to work out which one to believe. So the formatting lives here, once,
next to the serialization rules rather than inside any one renderer.

This is presentation, not evidence: nothing here computes, rounds for storage, or
decides what matters. It turns a value the engine already produced into
something short enough to read in a table cell.
"""

from __future__ import annotations

from typing import Any

import yaml


def count(n: int, word: str) -> str:
    """`1 step` / `2 steps`. A count is a measured number; it should read like one."""
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def format_rate(rate: float | None) -> str:
    """A 0–1 rate as a percentage.

    Rounds to whole percent, with one exception: a rate that is **not zero** but
    rounds to zero renders ``<1%``. "0%" for a column that does have nulls is the
    kind of quiet lie this project exists to prevent.
    """
    if rate is None:
        return "—"
    if rate > 0 and round(rate * 100) == 0:
        return "<1%"
    return f"{rate:.0%}"


def inline(value: Any) -> str:
    """A measured value on one line, in words rather than punctuation.

    ``{"left": 8, "right": 6}`` becomes ``left 8 · right 6`` and
    ``{"left": ["customer_id"], "right": ["customer_id"]}`` becomes
    ``left customer_id · right customer_id``. Both used to be dumped as inline
    YAML, braces and all, which is most of what made the run report unreadable.

    Anything genuinely nested still falls back to YAML rather than being
    flattened into something that reads clearer than it is.
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return scalar(value)
    if isinstance(value, dict) and all(not isinstance(v, dict) for v in value.values()):
        return " · ".join(f"{k} {inline(v)}" for k, v in value.items()) or "—"
    if isinstance(value, list) and all(not isinstance(v, (dict, list)) for v in value):
        return ", ".join(scalar(v) for v in value) or "—"
    return as_yaml(value, flow=True)


def scalar(value: Any) -> str:
    """One value, spelled the way the YAML artifacts spell it."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return "—" if value is None else str(value)


class _NoAliases(yaml.SafeDumper):
    """Never emit `&id001` / `*id001`.

    A join step names the same key list on both sides, and PyYAML's anchors
    turned that into ``keys: &id001 [customer_id] … right: *id001`` on screen.
    Correct YAML, unreadable evidence.
    """

    def ignore_aliases(self, data: Any) -> bool:
        return True


def as_yaml(value: Any, *, flow: bool = False) -> str:
    """A structured value as YAML — the shape the durable artifacts are written in.

    A scalar goes through as itself: ``yaml.safe_dump(8)`` is ``"8\\n...\\n"``, and
    a row count rendering as ``8 ...`` reads like a truncation of the number the
    whole product exists to be trusted about.
    """
    if isinstance(value, str):
        return value
    if value is None or isinstance(value, (bool, int, float)):
        return scalar(value)
    dumped = yaml.dump(
        value,
        Dumper=_NoAliases,
        sort_keys=False,
        default_flow_style=flow,
        allow_unicode=True,
        width=10_000 if flow else 80,
    )
    return dumped.strip()

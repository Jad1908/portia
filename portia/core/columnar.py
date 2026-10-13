"""A report too wide to send whole, at a coarser grain: long runs given by their count and ends.

**What is left of a module that was an encoding** *(2026-10-13)*. It began as
one way to hand the model a list of records, a header line and a tab-separated
line each, because per-column evidence pretty-printed as JSON repeated every
field name once per column: on the 191-column source of `docs/EVALUATION.md`'s
AQN build run, `profile_source` was 83,079 characters, the SDK refused it, and
the copilot built the whole deliverable without its main source's numbers.
That encoding (``render``) went when `describe_source` and `profile_source`
became cards: a list is now ``fields`` once and a row of values each
(`core/listing.py`), which is JSON and as lean, and their answers no longer
grow with the table. `devtools/traces` still reads the old format back out of
the logs written before. The grouped form (``by_type``), a table's columns as
counts and spreads per type, went when `read_spec` became a card and stopped
sending the built table's columns at all.

**What stays is the shortened form** (:func:`shorten`), for the reports that
are still sent whole and can pass `tools.RESULT_BUDGET` on a very wide table:
`record_step`'s and `run_spec`'s. It is the same report at a coarser grain,
every item counted, rather than the first few that fit.
"""

from __future__ import annotations

import statistics
from collections.abc import Iterable, Mapping
from typing import Any

from portia.core.serialize import to_jsonable

#: A run of this many items or fewer is kept whole, because a summary of three
#: names is longer than the three.
LISTED_GROUP = 10

#: Items shown from each end of a run too long to keep, in the report's own
#: order. The first and the last rather than any chosen few: which items are
#: worth a look is the reader's call, and the report's order is the one order
#: that makes none.
GROUP_EXAMPLES = 5

#: A run of numbers' spread, named as the profile names its own quartiles so
#: the two read the same.
SPREAD = ("min", "q25", "median", "q75", "max")


def shorten(value: Any) -> Any:
    """``value`` with every long run of names or numbers given as its count and its ends.

    **For a report whose width is the table's** *(2026-10-08)*: `record_step`'s
    outcome names every column with a null rate and every column each input put
    into the output, so a step over a 1,579-column table came back at 80,633
    characters, most of it two lists of column names.

    So any list of plain values, and any map of names to plain values, longer
    than :data:`LISTED_GROUP` becomes ``count``, the ``first`` and ``last``
    :data:`GROUP_EXAMPLES` in the order the report had them, and, when the
    values are numbers, their ``spread``. Everything shorter, and everything that
    is not a run of plain values, is kept as it was and walked into. Nothing is
    dropped without being counted, and nothing is reordered by what it holds.
    """
    if isinstance(value, Mapping):
        if len(value) > LISTED_GROUP and all(map(_plain, value.values())):
            names = list(value)
            run: dict[str, Any] = {
                "count": len(names),
                "first": {name: value[name] for name in names[:GROUP_EXAMPLES]},
                "last": {name: value[name] for name in names[-GROUP_EXAMPLES:]},
            }
            numbers = _numbers(value.values())
            if len(numbers) == len(names):
                run["spread"] = _spread(numbers)
            return run
        return {key: shorten(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        if len(value) > LISTED_GROUP and all(map(_plain, value)):
            return {
                "count": len(value),
                "first": list(value[:GROUP_EXAMPLES]),
                "last": list(value[-GROUP_EXAMPLES:]),
            }
        return [shorten(item) for item in value]
    return value


def _plain(value: Any) -> bool:
    return value is None or isinstance(value, str | int | float | bool)


def _numbers(values: Iterable[Any]) -> list[int | float]:
    return [v for v in values if isinstance(v, int | float) and not isinstance(v, bool)]


def _spread(values: list[int | float]) -> dict[str, Any]:
    """Min, quartiles and max, interpolated the way the profiler's own are.

    ``inclusive`` is linear interpolation between the values, which is what
    ``quantile_cont`` computes for a profile's ``q25``/``median``/``q75``, so a
    spread of null rates and a column's quartiles mean the same thing.
    """
    ordered = sorted(values)
    quartiles = (
        statistics.quantiles(ordered, n=4, method="inclusive") if len(ordered) > 1 else ordered * 3
    )
    return dict(zip(SPREAD, map(to_jsonable, [ordered[0], *quartiles, ordered[-1]]), strict=True))

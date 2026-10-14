"""A report too wide to send whole, at a coarser grain: records by type, runs by their ends.

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
the logs written before.

**What stays is the grouped and the shortened form**, for the answers that are
still sent whole and can pass `tools.RESULT_BUDGET` on a very wide table:
`read_spec` with a built table's columns (:func:`by_type`), and the reports of
`record_step` and `run_spec` (:func:`shorten`). Each is the same evidence at a
coarser grain, every record and every item counted, rather than the first few
that fit.
"""

from __future__ import annotations

import statistics
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from portia.core.serialize import to_jsonable

#: A type with this many columns or fewer is listed record by record in the
#: grouped form, because a summary of three columns is longer than the three.
LISTED_GROUP = 10

#: Names shown from each end of a group too big to list, in the table's own
#: order. The first and the last rather than any chosen few: which columns are
#: worth a look is the reader's call, and file order is the one order that
#: makes none.
GROUP_EXAMPLES = 5

#: A measured number's spread across a group, named as the profile names its own
#: quartiles so the two read the same.
SPREAD = ("min", "q25", "median", "q75", "max")


def by_type(
    payload: Mapping[str, Any],
    *,
    records: str,
    typed_by: Sequence[str],
    tallied: Sequence[str] = (),
    spread: Sequence[str] = (),
    lowest: Sequence[str] = (),
    highest: Sequence[str] = (),
) -> dict[str, Any]:
    """One evidence dict with its records grouped by type, for when a line each does not fit.

    The head is kept whole: everything but the records. The records become one
    group per type, in the order each type first appears, under
    ``<records>_by_type``; a record's type is the first of ``typed_by`` it
    carries, so a warehouse table nobody has profiled groups on its ``dtype``.
    Every record lands in exactly one group, so the counts add up to the table.

    A group of :data:`LISTED_GROUP` or fewer carries its records as they are. A
    bigger one carries its count, the first and last :data:`GROUP_EXAMPLES`
    names, and each named field across its records: ``tallied`` as how many
    carry each value, ``spread`` as :data:`SPREAD`, ``lowest`` and ``highest``
    as the extreme value any of them holds. **Which fields, and in which form,
    is the caller's**: this module would otherwise have to know which rung
    measures what.

    Nothing is ordered by a measurement. Groups follow the table, the names are
    its ends, and a tally keeps the order its values first appear in.
    """
    head = {key: value for key, value in payload.items() if key != records}
    types: dict[tuple[str, Any], list[Mapping[str, Any]]] = {}
    for record in payload.get(records) or []:
        field = next((f for f in typed_by if f in record), typed_by[0])
        types.setdefault((field, record.get(field)), []).append(record)

    groups = []
    for (field, kind), members in types.items():
        group: dict[str, Any] = {field: kind, f"n_{records}": len(members)}
        if len(members) <= LISTED_GROUP:
            groups.append({**group, records: list(members)})
            continue
        names = [member["name"] for member in members]
        group["first"] = names[:GROUP_EXAMPLES]
        group["last"] = names[-GROUP_EXAMPLES:]
        for name in tallied:
            group[name] = dict(Counter(_items(member.get(name) for member in members)))
        for name in spread:
            if values := _numbers(member.get(name) for member in members):
                group[name] = _spread(values)
        for name in lowest:
            if values := _numbers(member.get(name) for member in members):
                group[name] = min(values)
        for name in highest:
            if values := _numbers(member.get(name) for member in members):
                group[name] = max(values)
        groups.append(group)
    return {**head, f"{records}_by_type": groups}


def shorten(value: Any) -> Any:
    """``value`` with every long run of names or numbers given as its count and its ends.

    **For a report whose width is the table's** *(2026-10-08)*: `record_step`'s
    outcome names every column with a null rate and every column each input put
    into the output, so a step over a 1,579-column table came back at 80,633
    characters, most of it two lists of column names. :func:`by_type` cannot
    help there, because those are not records to group; they are runs.

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


def _items(values: Iterable[Any]) -> Iterable[Any]:
    """Every value worth a tally: a list's items one by one, and no nulls."""
    for value in values:
        for item in value if isinstance(value, list | tuple) else [value]:
            if item is not None:
                yield item


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

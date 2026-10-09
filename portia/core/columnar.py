"""One way to hand the model a list of records: a header line and one line each.

`present.py` is the same idea aimed at a person; this one is aimed at the model,
and the difference in audience is the whole reason it is a separate module. A
person reads a table cell at a time and wants a null to look like a null
(`present.NULL_CELL`); the model reads the entire payload every turn and pays for
each character of it.

**Why it exists.** Per-column evidence pretty-printed as JSON repeats every field
name once per column and pads the result with indentation. On the 191-column
source in `docs/EVALUATION.md`'s AQN build run, `profile_source` produced 83,079
characters, of which 30,057 were whitespace and roughly 17,000 more were the same
twelve to nineteen key names retyped 191 times. The SDK refused it, the copilot
never obtained it, and it built the whole deliverable without its main source's
numbers. The same evidence through here is 28,086 characters, a 66% cut, with
nothing dropped and no number changed.

**Both column-shaped rungs use it, and the quieter one was the larger.**
`describe_source` was never refused once, so nothing flagged it — but counting
that session rather than its worst call puts it first at 101,601 characters over
31 calls, ahead of `record_step`, `profile_source` and `measure_overlaps`. The
two rungs together go 139,102 → 58,641 there. Ranking payloads by the biggest
call finds what *fails*; ranking by session total finds what *costs*.

**Blocks come from the data, not from a list of kinds we keep here.** Records are
grouped by exactly which fields they carry, so a profile arrives as one block per
field set: the numeric columns with their quartiles, the text columns with their
modal value, and the booleans with neither. The alternative was naming those
three groups in this module, which would couple an encoder to
`checks/profiling.py`'s branching and silently drop any field added there. It
also means no cell is ever blank *because the field does not apply to this
record* — an absent field is absent from the header, which is a stronger
statement than an empty cell and a cheaper one.

**Two characters are load-bearing.** A cell is escaped, so a value that contains
a tab or a newline survives as one cell: on that same source, 15 sample values
contain a tab, a newline or a `\\x07`, and those are the dirty values a profile
exists to surface. And a null is `\\N`, never blank, because a blank cell and an
empty string are different facts (`present.py` settled the same point for the
human surfaces). Escaping the backslash first is what keeps a value that is
literally ``\\N`` distinguishable from a null.

**A table too wide for one line per column is grouped instead** (:func:`by_type`,
2026-10-08). A line per column is still a line per column: a 1,579-column table
was 56,289 characters described and 211,563 profiled, and both were refused, so
the copilot could read that table only by naming columns it had never been shown.
The grouped form is the same records at a coarser grain, every one of them
counted, rather than the first few that fit.
"""

from __future__ import annotations

import json
import statistics
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from portia.core.serialize import to_jsonable

#: The cell separator. Tab rather than comma because the values are real data —
#: station names and addresses carry commas far more often than tabs, and the
#: tabs that do occur are escaped below rather than quoted around.
DELIMITER = "\t"

#: A null cell, and never the empty string: `null_rate` absent and `null_rate`
#: empty would otherwise read the same. The TSV convention, so it needs no
#: explaining to a model that has read a great deal of TSV.
NULL = "\\N"

#: Escaped in this order — the backslash first, or escaping a tab would produce a
#: sequence the next rule rewrites again.
_ESCAPES = (("\\", "\\\\"), ("\t", "\\t"), ("\n", "\\n"), ("\r", "\\r"))


def _escape(text: str) -> str:
    for raw, escaped in _ESCAPES:
        text = text.replace(raw, escaped)
    return text


def _cell(value: Any) -> str:
    """One value as one cell.

    A list stays a list, written as compact JSON rather than joined on a
    separator. Joining is what a reader would reach for first and it is wrong on
    real data: 11 of the sample values on the AQN stations table contain a comma,
    so a joined cell cannot say whether ``A&L ROYAUME UNI, EUROPE CENTR`` is one
    value or two — on the column whose entire job is showing what the values look
    like.
    """
    if value is None:
        return NULL
    if isinstance(value, Mapping):
        return json.dumps(
            {k: to_jsonable(v) for k, v in value.items()}, ensure_ascii=False, separators=(",", ":")
        )
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return json.dumps(
            [to_jsonable(v) for v in value], ensure_ascii=False, separators=(",", ":")
        )
    coerced = to_jsonable(value)
    return _escape(coerced) if isinstance(coerced, str) else str(coerced)


def _row(record: Mapping[str, Any], fields: tuple[str, ...]) -> str:
    return DELIMITER.join(_cell(record[field]) for field in fields)


def render(payload: Mapping[str, Any], *, records: str) -> str:
    """One evidence dict as its head, then a table per field set.

    ``records`` names the key holding the list, and doubles as the noun in each
    block's heading — ``columns`` gives *"48 of 191 columns"*. Everything else in
    the payload is the head and stays JSON: it is a handful of scalars plus a
    prose summary, which a table would not shorten.

    Field order inside a block is the order the records already carry, because
    the check that built them put them in a deliberate one and re-sorting here
    would be this module having an opinion about evidence.
    """
    head = {key: value for key, value in payload.items() if key != records}
    rows = payload.get(records) or []

    groups: dict[tuple[str, ...], list[Mapping[str, Any]]] = {}
    for record in rows:
        groups.setdefault(tuple(record), []).append(record)

    blocks = [json.dumps(head, ensure_ascii=False)]
    for fields, group in groups.items():
        blocks.append(
            f"\n## {len(group)} of {len(rows)} {records}\n"
            + "\n".join([DELIMITER.join(fields), *(_row(r, fields) for r in group)])
        )
    return "\n".join(blocks)


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

    The head is kept whole, as :func:`render` keeps it. The records become one
    group per type, in the order each type first appears, under
    ``<records>_by_type``; a record's type is the first of ``typed_by`` it
    carries, so a warehouse table nobody has profiled groups on its ``dtype``.
    Every record lands in exactly one group, so the counts add up to the table.

    A group of :data:`LISTED_GROUP` or fewer carries its records as they are. A
    bigger one carries its count, the first and last :data:`GROUP_EXAMPLES`
    names, and each named field across its records: ``tallied`` as how many
    carry each value, ``spread`` as :data:`SPREAD`, ``lowest`` and ``highest``
    as the extreme value any of them holds. **Which fields, and in which form,
    is the caller's**, for the reason :func:`render` derives its blocks: this
    module would otherwise have to know which rung measures what.

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

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
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
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

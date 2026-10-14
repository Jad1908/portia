"""One shape for every list a tool answers with, and one rule for naming flagged columns.

**Why it exists** *(2026-10-13, the tool revamp)*. A tool's answer grew with the
data: a 1,579-column table described one record per column, sixty sources were
sixty entries in the index, thirty questions were thirty answers resent. Past
`tools.RESULT_BUDGET` each was refused or summarised in a way that lost the fact
that mattered, "4 columns are constant" without the four names. The rule the
revamp settled on (`sandbox/limits/REVAMP_SPEC.md`) is that an answer's shape
does not grow with the data: a list is cut at a fixed size, the cut is always
announced, and the announcement says how to ask a narrower question. This
module is the shape; the sentence that announces the cut is prose, so it comes
from `agent/prompts/` and is handed in (`core` loads no prompt).

**`listing` is the header once and the rows as lists.** A list of records as
JSON repeats every key in every record; on a column list that is most of the
payload. ``fields`` says what each position is, once, and each row is the values
in that order. Values go through `core.serialize`, so a number reads the same
here as in every other piece of evidence.

**`flagged` names the exceptions, never a summary of them.** Every flagged
column is named while that fits, by the side of the flag that is shorter: a flag
three columns carry lists the three, and a flag all but three carry lists the
three that do not. Only past a cap does a flag become a count, and then the
longest list goes first, because the cap is about size and size is the only
thing this module may look at. Which flag *matters* is the reader's call
(`CLAUDE.md`, facts versus judgment).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from difflib import SequenceMatcher
from fnmatch import fnmatchcase
from typing import Any

from portia.core.serialize import to_jsonable_nested

#: What ``shown`` says when a list holds nothing at this position: an empty
#: list, or an offset past its end.
NONE_SHOWN = "none"

#: How many names one flag may list, on its rarer side, before it is given as a
#: count. **A placeholder, to be tuned by the scale test** (`tests/test_result_budget.py`).
#: Measured on a public energy dataset of 18 real tables: every flagged column
#: was named, with at most 91 names on one card.
PER_FLAG_NAMES = 100

#: How many names all the flags of one card may list together. Past it, the
#: longest list becomes a count, then the next longest, until the rest fit.
#: **A placeholder**, as `PER_FLAG_NAMES` is.
CARD_NAMES = 300

#: How many near misses a refusal of an unknown name gives (:func:`near_misses`).
CLOSE_MATCHES = 3

#: How alike two names must be to count as a near miss: `difflib`'s ratio, at
#: `difflib.get_close_matches`' own default, which this module used until it
#: needed its own tie-break (:func:`near_misses`).
CLOSENESS = 0.6

#: The characters that make a name typed by the copilot a pattern
#: (:func:`matches`): `fnmatch`'s any-run, any-one, and the opening of a set.
WILDCARDS = frozenset("*?[")

#: The keys a flag's entry uses, named once so a reader of the answer and a
#: test agree on them.
WITH = "columns"
WITHOUT = "all_but"
LISTED = "listed"


def listing(
    fields: Sequence[str],
    rows: Sequence[Sequence[Any]],
    *,
    n: int,
    start: int = 0,
    more: str | None = None,
) -> dict:
    """A list as ``{"n", "shown", "fields", "rows", "more"}``, every cut announced.

    ``n`` is how many things the list has in all, ``rows`` the ones shown, which
    start at position ``start`` (0-based) of the whole. ``shown`` is the range
    shown, 1-based and inclusive (``"21-40"``), or ``"none"``.

    ``more`` is **required whenever fewer rows are shown than there are**, and
    absent otherwise. It is the caller's sentence, from `agent/prompts/`, and it
    says how many were left out and how to ask a narrower question. A list cut
    with nothing saying so is a short answer that reads as a whole one, which is
    the failure this module exists to stop, so it is refused here rather than
    trusted to every caller.

    A row of the wrong width is refused too: a value under the wrong field is a
    wrong fact, not a formatting slip.
    """
    width = len(fields)
    out_rows = []
    for row in rows:
        if len(row) != width:
            raise ValueError(f"a row has {len(row)} values for {width} fields: {list(fields)}")
        out_rows.append([to_jsonable_nested(value) for value in row])
    if start < 0 or n < 0:
        raise ValueError(
            f"a listing starts at 0 or later and counts 0 or more (start={start}, n={n})"
        )
    if start + len(out_rows) > n:
        raise ValueError(f"rows {start + 1}-{start + len(out_rows)} are past the {n} there are")
    shown = f"{start + 1}-{start + len(out_rows)}" if out_rows else NONE_SHOWN
    out: dict[str, Any] = {"n": n, "shown": shown, "fields": list(fields), "rows": out_rows}
    if len(out_rows) < n:
        if not more:
            raise ValueError(
                f"{n - len(out_rows)} of {n} are left out and nothing says how to ask for them: "
                "pass `more`, a sentence from agent/prompts"
            )
        out["more"] = more
    return out


def flagged(columns: Sequence[Mapping[str, Any]], *, order: Sequence[str] = ()) -> dict:
    """Per flag the columns carry, its count and its columns named by its rarer side.

    ``columns`` is the table's columns in the table's order, each a mapping with
    ``name`` and ``flags`` (a list; absent or ``None`` counts as none). For each
    flag present, ``n`` is how many columns carry it and ``N`` how many columns
    there are. The rarer side is the smaller of *with* and *without*; *with* is
    a majority only past half, so a flag on exactly half lists the columns that
    carry it.

    - the rarer side is *with*, and at most `PER_FLAG_NAMES`: ``{"n", "columns": [...]}``
    - the rarer side is *without*, and at most `PER_FLAG_NAMES`: ``{"n", "all_but": [...]}``
      (``"all_but": []`` when every column carries it)
    - otherwise ``{"n", "listed": False}``

    If the names together are more than `CARD_NAMES`, the longest list becomes
    ``{"n", "listed": False}``, then the next longest, until the rest fit. Two
    lists of one length go in the order they appear in the answer. **Size only**:
    nothing here weighs which flag matters, and nothing could, without ranking.
    A flag given as a count is listed by a narrower call (`describe_source`'s
    ``flag`` filter); the card says so, in prompt text.

    A column with two flags is named under both. Names keep the table's order.

    ``order`` is the vocabulary the flags appear in, which for a profile is
    `checks.profiling.FLAGS`; a flag it does not name follows, in the order the
    columns first carry it. A parameter rather than an import because `core`
    knows nothing of the checks, for the reason `core/columnar.py` gives: an
    encoder coupled to one check's branching drops what another check adds.
    """
    total = len(columns)
    carriers: dict[str, list[str]] = {}
    for column in columns:
        for flag in column.get("flags") or ():
            carriers.setdefault(str(flag), []).append(str(column["name"]))
    ranked = {flag: i for i, flag in enumerate(order)}
    seen = list(carriers)
    flags = sorted(carriers, key=lambda f: (ranked.get(f, len(ranked)), seen.index(f)))

    names_by_flag = {}
    out: dict[str, dict[str, Any]] = {}
    for flag in flags:
        with_it = carriers[flag]
        n = len(with_it)
        if n <= total - n:
            entry: dict[str, Any] = {"n": n, WITH: with_it} if n <= PER_FLAG_NAMES else {}
        else:
            carrying = set(with_it)
            without = [str(c["name"]) for c in columns if str(c["name"]) not in carrying]
            entry = {"n": n, WITHOUT: without} if len(without) <= PER_FLAG_NAMES else {}
        out[flag] = entry or {"n": n, LISTED: False}
        if entry:
            names_by_flag[flag] = len(entry.get(WITH, entry.get(WITHOUT, [])))

    listed = sum(names_by_flag.values())
    # Longest first; a tie goes in the answer's own order (`sorted` is stable).
    for flag in sorted(names_by_flag, key=lambda f: -names_by_flag[f]):
        if listed <= CARD_NAMES:
            break
        listed -= names_by_flag[flag]
        out[flag] = {"n": out[flag]["n"], LISTED: False}
    return out


def near_misses(name: str, names: Iterable[str], *, n: int = CLOSE_MATCHES) -> list[str]:
    """The few ``names`` closest to ``name``, for a refusal of a name that is not there.

    **The closest few, never every name** *(2026-10-13)*. A refusal that listed
    every candidate grew with the project: an unknown table at 80 sources was
    1,694 characters of names, and a miss on a 1,579-column table's column was
    longer than a harness passes whole. The names that differ from ``name``
    only in case come first, because a name typed in the other case is the
    commonest miss; `difflib`'s closest follow, at most ``n`` in all. Closeness
    of spelling is all this measures. An empty list means nothing is close, and
    the caller's sentence says how many there are and where they are listed.

    **A tie goes to the name given first** *(2026-10-14)*. `get_close_matches`
    breaks a tie by comparing the names themselves and keeps the larger, so a
    miss of ``t07`` among ``t001`` … ``t100`` offered ``t057`` before ``t007``,
    both one character away. The caller's order is the table's, the spec's or
    the project's, which is the order the reader knows them in, and it costs
    nothing to keep.
    """
    pool = list(dict.fromkeys(names))
    folded = [other for other in pool if other.lower() == name.lower()]
    matcher = SequenceMatcher(b=name)
    scored = []
    for at, other in enumerate(pool):
        matcher.set_seq1(other)
        # `get_close_matches`' three gates, cheapest first: the two quick ones
        # are upper bounds on the ratio, so they only ever skip work.
        if (
            matcher.real_quick_ratio() >= CLOSENESS
            and matcher.quick_ratio() >= CLOSENESS
            and matcher.ratio() >= CLOSENESS
        ):
            scored.append((-matcher.ratio(), at, other))
    close = [other for _, _, other in sorted(scored)]
    return list(dict.fromkeys([*folded, *close]))[:n]


def is_pattern(name: str) -> bool:
    """Whether ``name`` holds a wildcard, which makes it a pattern rather than a name."""
    return not WILDCARDS.isdisjoint(name)


def matches(name: str, pattern: str | None) -> bool:
    """Whether ``name`` matches a shell-style ``pattern``, without case.

    Without case because every engine portia runs on folds an unquoted name
    (`core/dialect.resolve_columns`): ``meter_*`` is the pattern a copilot
    types for ``METER_01`` on a warehouse. ``fnmatchcase`` on both lowered,
    because ``fnmatch`` itself folds or not by operating system. No pattern
    matches everything.

    **One rule for every pattern the copilot types** *(2026-10-14)*: the
    filters of `describe_source`, `profile_source`, `get_context` and
    `read_spec`, and a role given to many columns at once
    (`catalog.set_interpretation`). It lived in `agent/handlers.py` until the
    catalog needed it, and the catalog sits below that layer.
    """
    return not pattern or fnmatchcase(name.lower(), pattern.strip().lower())

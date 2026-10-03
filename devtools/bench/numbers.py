"""Numbers in a reply, and whether each could have been copied from what the model saw.

The rule this measures is the first line of `copilot.md`: every number in a
reply comes from a tool result or from something the user said
(`docs/BENCHMARK_EVAL.md` §6.3, §14.2). The check decides nothing about
truth. It asks one narrower question, *does this number appear anywhere in
the evidence*, where the evidence is closed: the prompts the model read, the
user's messages and answers, every tool result, and the arguments the model
itself typed into a tool call. Matching a number against a closed set is
mechanical; verifying arithmetic or meaning is not, and is not attempted.

**Stricter than truth, on purpose.** A correctly computed "14% of rows" is
flagged when only the two counts were in a result, because the rule is that
the engine computes and the model copies; mental arithmetic, even right, is
what the rule exists to stop. Rounding is not arithmetic: one evidence number
in, one reply number out, precision only, so "~20%" for a result of 19% is a
match (:func:`rounds_to`).

**Two kinds of error, costed differently.** A false alarm costs a reader one
glance at a line in a list; a miss is the status quo. So the tokens that are
not numbers are named here as rules rather than guessed at: an identifier
(``H001``, ``toolu_01``), a date, a version string, a list marker, an ordinal,
code. Number words ("three tables") are out of scope, and that gap is stated.
Small integers match something in any log, so they are counted apart and
never claimed as checked (:data:`SMALL_INTEGER`).

Every decision that could go the other way is a named constant or a knob with
its counts beside it, because the calibration is the reader's: run it over the
logs, label the flags, and turn the knob that produced the false alarms.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal, InvalidOperation

#: Bare integers below this are counted, never checked: a "5" matches something
#: in every log, so a match would say nothing and a flag would say less.
SMALL_INTEGER = 10

#: How much text either side of a flagged number the report shows.
CONTEXT_CHARS = 70

#: How far back before a number a hedge word still counts as hedging it.
HEDGE_WINDOW = 24

#: Words that say a number is approximate. With one, a truncation or a ceiling
#: is accepted too ("over 19%" for 19.6). Without one, the strict setting
#: accepts rounding only, and the loose setting one unit of the last digit.
HEDGE = re.compile(
    r"(~|≈|about|around|roughly|nearly|almost|approx\.?|approximately|circa|c\.|close to|"
    r"just (?:under|over|above|below)|under|over|above|below|some|up to|at least|at most)\s*$",
    re.IGNORECASE,
)

#: What multiplies a number when written after it.
SUFFIXES: dict[str, Decimal] = {
    "k": Decimal(1_000),
    "m": Decimal(1_000_000),
    "b": Decimal(1_000_000_000),
    "thousand": Decimal(1_000),
    "million": Decimal(1_000_000),
    "billion": Decimal(1_000_000_000),
}

#: A number as prose writes it, with its suffix. Not glued to a letter, an
#: underscore or a digit on either side, so ``B0012``, ``Q1_2024`` and the
#: ``220`` of ``2.1.220`` never read as numbers. A trailing full stop is the
#: sentence's, not the number's.
NUMBER = re.compile(
    r"(?<![\w.])([-+]?\d(?:[\d,]*\d)?(?:\.\d+)?)"
    r"(?:\s?(%|thousand|million|billion|[kKmMbB](?![\w=])))?"
    r"(?!\w)(?!\.\d)"
)

#: Tokens removed before numbers are read out of a reply. Each is a decision:
#: a date is checked whole (:func:`dates_in`), a version and an ordinal are not
#: claims about data, a list marker numbers the prose, code is what the model
#: typed and not what it asserts.
ISO_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?)?\b")
VERSION = re.compile(r"\b\d+(?:\.\d+){2,}\b")
ORDINAL = re.compile(r"\b\d+(?:st|nd|rd|th)\b")
LIST_MARKER = re.compile(r"(?m)^[ \t]*\d+[.)][ \t]")
FOOTNOTE = re.compile(r"\[\d+\]")
FENCED_CODE = re.compile(r"```.*?```", re.DOTALL)
INLINE_CODE = re.compile(r"`[^`\n]*`")

#: A numeric token in evidence: JSON values, and numbers inside strings, with
#: the same rule against digits glued to letters.
EVIDENCE_NUMBER = re.compile(
    r"(?<![\w.])[-+]?\d(?:[\d,]*\d)?(?:\.\d+)?(?:[eE][-+]?\d+)?(?!\w)(?!\.\d)"
)

#: How many characters of an ISO date token are the date: a result may carry a
#: time part (``2024-07-26T00:00:00``) where a reply says the day.
DATE_CHARS = 10

#: How a number matched, in the order a report lists them. ``exact`` is the
#: same value; ``rounded`` the evidence rounded at the reply's own precision;
#: ``within`` the evidence cut, not rounded, at that precision: a truncation
#: ("19%" for 19.6), which the loose setting allows always and the strict one
#: only under a hedge, or a ceiling ("over 1.2M" for 1,234,567), which needs
#: the hedge on either setting.
EXACT = "exact"
ROUNDED = "rounded"
WITHIN = "within"
HOWS = (EXACT, ROUNDED, WITHIN)


@dataclass(frozen=True)
class Number:
    """One number as a reply wrote it."""

    written: str
    value: Decimal
    percent: bool
    #: Where in the text it starts, for the context window.
    start: int
    hedged: bool

    @property
    def small(self) -> bool:
        """A bare integer under :data:`SMALL_INTEGER`: counted, never checked."""
        return (
            not self.percent
            and self.value == self.value.to_integral_value()
            and abs(self.value) < SMALL_INTEGER
        )


@dataclass
class Evidence:
    """What the model has seen so far: every number, and every date, as written.

    Grows as a log is walked, because the model can only have copied from what
    came before the reply being checked.
    """

    values: set[Decimal] = field(default_factory=set)
    dates: set[str] = field(default_factory=set)

    def add(self, text: str) -> None:
        self.dates.update(token[:DATE_CHARS] for token in ISO_DATE.findall(text))
        for token in EVIDENCE_NUMBER.findall(text):
            try:
                self.values.add(Decimal(token.replace(",", "")))
            except InvalidOperation:
                continue


def prose_only(text: str) -> str:
    """A reply with the tokens that are not number claims blanked, lengths kept.

    Lengths are kept so a match's ``start`` still indexes the original text
    for the context window.
    """

    def blank(match: re.Match[str]) -> str:
        return " " * len(match.group(0))

    for pattern in (FENCED_CODE, INLINE_CODE, ISO_DATE, VERSION, ORDINAL, LIST_MARKER, FOOTNOTE):
        text = pattern.sub(blank, text)
    return text


def dates_in(text: str) -> list[str]:
    """Every ISO date a reply states, outside code, as the day alone."""
    stripped = FENCED_CODE.sub(" ", INLINE_CODE.sub(" ", text))
    return [token[:DATE_CHARS] for token in ISO_DATE.findall(stripped)]


def numbers_in(text: str) -> list[Number]:
    """Every number a reply states, as prose, with its written form and hedge."""
    cleaned = prose_only(text)
    found: list[Number] = []
    for match in NUMBER.finditer(cleaned):
        raw, suffix = match.group(1), (match.group(2) or "")
        try:
            value = Decimal(raw.replace(",", ""))
        except InvalidOperation:
            continue
        percent = suffix == "%"
        if suffix and not percent:
            value *= SUFFIXES[suffix.lower()]
        before = cleaned[max(0, match.start() - HEDGE_WINDOW) : match.start()]
        found.append(
            Number(
                written=match.group(0).strip(),
                value=value,
                percent=percent,
                start=match.start(),
                hedged=bool(HEDGE.search(before)),
            )
        )
    return found


def significant_figures(written: str) -> int:
    """Significant figures as written: ``20`` is 1, ``19.4`` is 3, ``350K`` is 2, ``1.2M`` is 2.

    A decimal point makes every digit significant; the trailing zeros of an
    integer are not, which is what lets ``~20%`` stand for 19.
    """
    digits = written.replace(",", "").lstrip("-+~≈ ").lower()
    digits = re.sub(r"[^0-9.]", "", digits)
    mantissa = digits.replace(".", "").lstrip("0")
    if "." in digits:
        return len(mantissa) or 1
    return len(mantissa.rstrip("0")) or 1


def rounds_to(
    evidence: Decimal,
    reply: Decimal,
    figures: int,
    *,
    truncated: bool = False,
    ceiling: bool = False,
) -> str | None:
    """How ``reply`` stands for ``evidence``, or ``None``.

    ``figures`` is the precision the reply wrote. ``truncated`` also accepts
    the evidence cut down at that precision, ``ceiling`` cut up; both are
    one number in and one number out, and neither is arithmetic.
    """
    if evidence == reply:
        return EXACT
    if evidence == 0 or reply == 0:
        return None
    magnitude = evidence.adjusted()
    unit = Decimal(10) ** (magnitude - figures + 1)
    scaled = evidence / unit
    if scaled.quantize(Decimal(1), rounding=ROUND_HALF_UP) * unit == reply:
        return ROUNDED
    if truncated and scaled.quantize(Decimal(1), rounding=ROUND_FLOOR) * unit == reply:
        return WITHIN
    if ceiling and scaled.quantize(Decimal(1), rounding=ROUND_CEILING) * unit == reply:
        return WITHIN
    return None


def candidates(number: Number) -> tuple[Decimal, ...]:
    """The values a written number may be standing for.

    A percent is one number written two ways: ``20%`` may be copying a result
    that said ``0.2`` or one that said ``20``. Both are tried; neither is
    arithmetic.
    """
    if number.percent:
        return (number.value / 100, number.value)
    return (number.value,)


def match(number: Number, evidence: Evidence, *, strict: bool) -> str | None:
    """How ``number`` matches the evidence, or ``None`` when it is in none of it."""
    figures = significant_figures(number.written)
    truncated, ceiling = number.hedged or not strict, number.hedged
    for wanted in candidates(number):
        if wanted in evidence.values:
            return EXACT
    best: str | None = None
    for wanted in candidates(number):
        for seen in evidence.values:
            how = rounds_to(seen, wanted, figures, truncated=truncated, ceiling=ceiling)
            if how == EXACT:
                return how
            if how and (best is None or HOWS.index(how) < HOWS.index(best)):
                best = how
    return best


def nearest(number: Number, evidence: Evidence) -> tuple[Decimal, Decimal] | None:
    """The evidence value closest to a flagged number and how far off, relatively.

    So a wrong knob shows up as a column of near misses rather than as a
    verdict: a list of flags all one unit off is a tolerance to turn, and a
    list with nothing near is the rule's positive class.
    """
    if not evidence.values:
        return None
    wanted = candidates(number)[0]
    scale = max(abs(wanted), Decimal("1e-9"))
    closest = min(evidence.values, key=lambda seen: abs(seen - wanted))
    return closest, (abs(closest - wanted) / scale)


def context(text: str, start: int, width: int = CONTEXT_CHARS) -> str:
    """The sentence around a number, on one line."""
    lo, hi = max(0, start - width), min(len(text), start + width)
    return " ".join(text[lo:hi].split())

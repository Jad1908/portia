"""A case: the estate, the threads, and the facts a person would bring (`docs/BENCHMARK_EVAL.md` §5.2, §8).

One YAML file per case. It holds everything a run needs and nothing a run
decides: the brief the project opens with, where the data comes from, the
messages of each thread in order, the facts the scripted user holds with the
anchors that route a question to each, a fallback for a question no fact
covers, and the caps that end a run nobody is watching. **Variants** are the
same data under two scripts (§7.3): a variant overrides the answer of a fact
by id, so a copilot that never asks can pass at most one of them.

Every string the model will read is in the file, never in this module. A
prompt that lives in Python is the failure `tests/test_agent_prompts.py`
exists to catch, and a case is a prompt.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

#: Which fixture builders make up a named family, for a case whose ``data`` is
#: a fixture rather than a folder. The hotel family is the one with an answer
#: key (`tests/fixtures/hotels.answers.yaml`).
FAMILIES: dict[str, tuple[str, ...]] = {
    "hotels": ("hotels", "reservations", "city_events"),
    "sales": ("sales_customers", "sales_orders"),
}

#: How a case's project gets its catalog before the first thread. ``facts``
#: is the deterministic half of indexing alone (`catalog.index_source`), free
#: and the same every run; ``copilot`` runs the interpretation job too, which
#: costs a model turn per case and counts toward portia's total (§5.3).
INDEX_FACTS = "facts"
INDEX_COPILOT = "copilot"
INDEXINGS = (INDEX_FACTS, INDEX_COPILOT)

#: The caps a case carries, and the defaults when it names none. Both are the
#: SDK's own limits on one exchange (`session.build_options`); a case may set
#: either, and an unattended run is refused without both (`check`).
DEFAULT_TURNS = 80
DEFAULT_BUDGET_USD = 5.0

#: The characters an anchor and a question are compared without: case,
#: thousands separators, runs of whitespace. ``52000`` in the script has to
#: find ``52,000`` in the question.
_SEPARATORS = re.compile(r"(?<=\d),(?=\d)")
_SPACES = re.compile(r"\s+")


def normalise(text: str) -> str:
    """Text as anchors are matched: lower case, no thousands separators, one space."""
    return _SPACES.sub(" ", _SEPARATORS.sub("", text)).strip().lower()


@dataclass(frozen=True)
class Fact:
    """One thing only a person knows, and the words that mean a question is about it."""

    id: str
    answer: str
    anchors: tuple[str, ...]

    @property
    def keys(self) -> tuple[str, ...]:
        return tuple(normalise(a) for a in self.anchors)


@dataclass(frozen=True)
class Thread:
    """One session, its messages sent in order, each when the previous reply ends."""

    name: str
    messages: tuple[str, ...]


@dataclass(frozen=True)
class Baseline:
    """What the plain-Claude-Code arm gets (§5.5), written into the case because fairness lives here."""

    #: The built-in tools it may use, by the SDK's names.
    tools: tuple[str, ...] = ()
    #: Which settings the binary reads: ``project`` for the project's own
    #: `CLAUDE.md`, never ``user``, because the user's global instructions are
    #: nobody's benchmark.
    setting_sources: tuple[str, ...] = ("project",)
    #: The message the diligent version ends each thread with; empty means the
    #: diligent arm cannot run for this case.
    diligent: str = ""


@dataclass(frozen=True)
class Case:
    name: str
    brief: str
    data: str
    threads: tuple[Thread, ...]
    facts: tuple[Fact, ...]
    fallback: str
    index: str = INDEX_FACTS
    seed: int | None = None
    max_turns: int = DEFAULT_TURNS
    max_budget_usd: float = DEFAULT_BUDGET_USD
    variants: dict[str, dict[str, str]] = field(default_factory=dict)
    baseline: Baseline = field(default_factory=Baseline)
    path: Path | None = None

    def with_variant(self, name: str | None) -> Case:
        """The case under one variant's answers; ``None`` is the case as written."""
        if not name:
            return self
        if name not in self.variants:
            raise ValueError(f"case {self.name!r} has no variant {name!r}")
        overrides = self.variants[name]
        facts = tuple(
            Fact(f.id, overrides.get(f.id, f.answer), f.anchors) if f.id in overrides else f
            for f in self.facts
        )
        return Case(**{**self.__dict__, "facts": facts})

    @property
    def fixture_family(self) -> tuple[str, ...] | None:
        return FAMILIES.get(self.data)


def load(path: str | Path) -> Case:
    """Read a case file. Shape problems are `ValueError`, named."""
    path = Path(path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: a case is a mapping")
    facts = tuple(
        Fact(str(f["id"]), str(f["answer"]), tuple(str(a) for a in f.get("anchors") or ()))
        for f in raw.get("facts") or ()
    )
    threads = tuple(
        Thread(str(t["name"]), tuple(str(m) for m in t.get("messages") or ()))
        for t in raw.get("threads") or ()
    )
    caps = raw.get("caps") or {}
    base = raw.get("baseline") or {}
    case = Case(
        name=str(raw.get("name") or path.stem),
        brief=str(raw.get("brief") or ""),
        data=str(raw.get("data") or ""),
        threads=threads,
        facts=facts,
        fallback=str(raw.get("fallback") or ""),
        index=str(raw.get("index") or INDEX_FACTS),
        seed=int(raw["seed"]) if raw.get("seed") is not None else None,
        max_turns=int(caps.get("turns", DEFAULT_TURNS)),
        max_budget_usd=float(caps.get("budget_usd", DEFAULT_BUDGET_USD)),
        variants={
            str(k): {str(i): str(a) for i, a in (v or {}).items()}
            for k, v in (raw.get("variants") or {}).items()
        },
        baseline=Baseline(
            tools=tuple(str(t) for t in base.get("tools") or ()),
            setting_sources=tuple(str(s) for s in base.get("setting_sources") or ("project",)),
            diligent=str(base.get("diligent") or ""),
        ),
        path=path,
    )
    problems = check(case)
    if problems:
        raise ValueError(f"{path}: " + "; ".join(problems))
    return case


def check(case: Case) -> list[str]:
    """What makes the case unrunnable, each a sentence. Empty when it can run.

    Runs before any model does (§7.3): a fact with no anchors can never be
    reached, a variant naming no fact changes nothing, a thread with no
    message is a session nobody opens, and a missing fallback leaves the
    copilot's question unanswered on the first miss.
    """
    problems: list[str] = []
    if not case.brief.strip():
        problems.append("no brief: the project has to open with what it is for")
    if not case.data or (case.fixture_family is None and not Path(case.data).is_dir()):
        problems.append(
            f"data {case.data!r} is neither a fixture family ({', '.join(FAMILIES)}) nor a folder"
        )
    if case.index not in INDEXINGS:
        problems.append(f"index {case.index!r} is not one of {', '.join(INDEXINGS)}")
    if not case.threads:
        problems.append("no threads")
    for thread in case.threads:
        if not thread.messages:
            problems.append(f"thread {thread.name!r} has no message")
    seen: set[str] = set()
    for fact in case.facts:
        if fact.id in seen:
            problems.append(f"fact {fact.id!r} is declared twice")
        seen.add(fact.id)
        if not fact.anchors:
            problems.append(f"fact {fact.id!r} has no anchors, so no question can reach it")
        if not fact.answer.strip():
            problems.append(f"fact {fact.id!r} has no answer")
    for name, overrides in case.variants.items():
        for fact_id in overrides:
            if fact_id not in seen:
                problems.append(
                    f"variant {name!r} answers a fact {fact_id!r} the case does not hold"
                )
    if not case.fallback.strip():
        problems.append("no fallback: the first question no fact covers would hang")
    if case.max_turns <= 0 or case.max_budget_usd <= 0:
        problems.append("caps must be positive: an unattended run needs both")
    return problems


def as_dict(case: Case) -> dict[str, Any]:
    """The case as a run records it, so a result file says what it ran."""
    return {
        "name": case.name,
        "path": str(case.path) if case.path else None,
        "data": case.data,
        "index": case.index,
        "seed": case.seed,
        "threads": [t.name for t in case.threads],
        "facts": [f.id for f in case.facts],
        "variants": sorted(case.variants),
        "caps": {"turns": case.max_turns, "budget_usd": case.max_budget_usd},
    }

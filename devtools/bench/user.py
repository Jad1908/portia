"""The scripted user: the person's information, fixed, so the model is the only variable (`docs/BENCHMARK_EVAL.md` §2.2, §8).

It stands in for one thing a human brings to a session, the facts only a
person knows, and for nothing else. It answers a question from the case's
fact table, approves every write without being asked (`auto_allow`, logged
``auto`` by `ask.decide_write`, so no log claims a person decided), and sends
the next scripted message when a reply ends.

**How a question finds its fact** (§8.2): anchors. Each fact lists the words
you cannot ask about it without using; the fact with the most anchors in the
question or its options answers, and a tie goes to the fact declared first.
No anchor anywhere, and the fallback answers and is counted, because a high
count means the script is too thin, not that the copilot asked badly. Every
routing decision is kept with its reason, so a failed run can be blamed on
the script or on the copilot.

The answer is free text. The SDK's question carries options, and a real
person types past them as often as not; the script does the same, and never
picks an option label the case did not write.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from devtools.bench.case import Fact, normalise

#: Why a question was answered the way it was. A kind, never a rank.
BY_ANCHOR = "anchor"
FALLBACK = "fallback"


@dataclass(frozen=True)
class Routing:
    """One question, the fact it went to, and why."""

    question: str
    fact: str | None
    hits: tuple[str, ...]
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "fact": self.fact,
            "hits": list(self.hits),
            "reason": self.reason,
        }


@dataclass
class ScriptedUser:
    facts: tuple[Fact, ...]
    fallback: str
    routing: list[Routing] = field(default_factory=list)

    @property
    def fallbacks(self) -> int:
        return sum(1 for r in self.routing if r.reason == FALLBACK)

    def route(self, text: str) -> tuple[Fact | None, tuple[str, ...]]:
        """The fact ``text`` is about, by anchors hit, and which anchors those were."""
        haystack = normalise(text)
        best: Fact | None = None
        best_hits: tuple[str, ...] = ()
        for fact in self.facts:
            hits = tuple(
                a for a, key in zip(fact.anchors, fact.keys, strict=True) if key in haystack
            )
            if len(hits) > len(best_hits):
                best, best_hits = fact, hits
        return best, best_hits

    def reply(self, question: dict[str, Any]) -> str:
        """The answer to one of the SDK's questions, and a routing record kept."""
        text = str(question.get("question") or "")
        options = question.get("options") or []
        searched = " ".join(
            [text, str(question.get("header") or "")]
            + [
                f"{o.get('label', '')} {o.get('description', '')}"
                for o in options
                if isinstance(o, dict)
            ]
        )
        fact, hits = self.route(searched)
        if fact is None:
            self.routing.append(Routing(text, None, (), FALLBACK))
            return self.fallback
        self.routing.append(Routing(text, fact.id, hits, BY_ANCHOR))
        return fact.answer

    async def answer(self, questions: list[dict]) -> dict[str, Any]:
        """`ask.AnswerFn`: ``{question text: answer}`` for every question in the payload."""
        return {str(q.get("question") or ""): self.reply(q) for q in questions}

    @staticmethod
    def auto_allow(tool_name: str) -> bool:
        """`ask.AutoAllowFn`: every write goes through, and the log says ``auto``."""
        return True

    @staticmethod
    async def confirm(tool_name: str, tool_input: dict) -> bool:
        """`ask.ConfirmFn`, never reached while `auto_allow` says yes to everything.

        Kept as a refusal rather than a yes: a path through here would be
        logged as a human approval with a wait time, and the log would then
        claim a person decided (§8.1).
        """
        raise RuntimeError(
            f"the scripted user was asked to confirm {tool_name}; auto_allow should have"
        )

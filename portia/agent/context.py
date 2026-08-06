"""The project brief — the context the copilot always has, without asking for it.

Context reaches the model in layers, cheapest first, and only this one is
guaranteed:

- **L0** — who portia is and how to work (``prompts/copilot.md``). Static.
- **L1** — *this* project: the user's description, the groups, and a one-line
  index of every source. **Composed here and injected into the system prompt**,
  so it is structurally present rather than something the agent might fetch.
- **L2** — one source's semantic map (``describe_source``). Opt in.
- **L3** — one source's full measured facts (``profile_source``). Opt in.
- **L4** — cross-source evidence (``join_findings``). Opt in.

The reason L1 is pushed rather than pulled: a tool the agent *may* call is a tool
it will sometimes skip, and when it skips the project context its judgment turns
generic — which is the one thing this layer exists to prevent. Everything above
L1 stays pull-based, because that's the point: the agent decides when it needs
more, and pays for it only then.

Keep this brief **small**. It is on every request. Prose belongs to the user;
detail belongs to the tools.
"""

from __future__ import annotations

from portia import catalog
from portia.agent import prompts


def build_brief(portia_dir: str = catalog.DEFAULT_DIR) -> str:
    """Render this project's L1 context as markdown for the system prompt.

    The wording lives in ``prompts/brief/`` — this function only decides which
    parts apply and fills them in.
    """
    no_context = prompts.load("brief/no_context")
    try:
        cat = catalog.load_catalog(portia_dir)
    except FileNotFoundError:
        cat = {}

    sources = cat.get("sources") or {}
    grouped = _render_groups(cat.get("groups") or [], sources)
    return prompts.load("brief/template").format(
        project=cat.get("project") or no_context,
        groups="\n" + "\n".join(["## Groups", "", *grouped, ""]) if grouped else "",
        sources="\n".join(_render_sources(sources))
        if sources
        else prompts.load("brief/no_sources"),
    )


def _render_groups(groups: list[dict], sources: dict) -> list[str]:
    out = []
    for group in groups:
        members = ", ".join(group.get("sources") or []) or "(no members)"
        context = (group.get("context") or "").strip()
        out.append(f"- **{group.get('name')}** ({members})" + (f" — {context}" if context else ""))
    return out


def _render_sources(sources: dict) -> list[str]:
    """One line per source: its name, and one sentence of what it is.

    **The structural facts used to be here and are not any more** (2026-08-04,
    `KNOWLEDGE_GRAPH.md` §9.4 phase D). A column count and a candidate-key list
    are exactly what `describe_source` and `profile_source` exist to serve, so
    pushing them into every request paid for them on every turn whether or not
    that source was ever mentioned — and they are the half of the line that
    grows with the *shape* of a project rather than with its size.

    What stays is what cannot be pulled cheaply enough to be worth pulling: the
    **name**, because the agent cannot ask about a source it does not know
    exists, and **one sentence of meaning**, because that is what lets it choose
    which source to climb into without spending a call to find out. Those two
    are the routing decision itself; everything else is the answer to a question
    it has not asked yet.

    §1.4 wants this to go further — an index that lists everything is the wrong
    shape at 50 sources, and a neighbourhood you walk outward from is the right
    one. It is not taken further here on purpose: "unproven at 50" is not a
    measurement, this repo has a standing rule about acting on evidence that was
    never designed to support the claim, and there is no re-runnable fixture that
    would show whether dropping the sentence helped or hurt. `BACKLOG.md` holds
    what the fuller change looks like and what would have to be true to take it.
    """
    return [
        f"- **{name}** — {_first_sentence(entry.get('summary') or '')}"
        for name, entry in sources.items()
    ]


def _first_sentence(summary: str) -> str:
    """One line per source, so the index stays cheap however long a summary grows.

    The full stop is added only when the split took one off. This used to append
    one unconditionally, so a summary that was a single sentence — which is what
    a well-written one usually is — reached every system prompt ending in ``..``
    Invisible until something asserted the exact line.
    """
    if "(auto-drafted from checks" in summary:
        return "*Not yet interpreted — this is an auto-drafted placeholder.*"
    head = summary.strip().split(". ")[0].strip()
    if not head:
        return "*No summary yet.*"
    return head if head.endswith(".") else head + "."

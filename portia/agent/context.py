"""The project brief — the context the copilot always has, without asking for it.

Context reaches the model in layers, cheapest first, and only this one is
guaranteed:

- **L0** — who portia is and how to work (``prompts/copilot.md``). Static.
- **L1** — *this* project: the user's description, the groups, a one-line
  index of every source and one of every spec. **Composed here and injected
  into the system prompt**,
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

from pathlib import Path

from portia import catalog, spec
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
    brief = prompts.load("brief/template").format(
        project=cat.get("project") or no_context,
        groups="\n" + "\n".join(["## Groups", "", *grouped, ""]) if grouped else "",
        models=_render_models(catalog.project_root(portia_dir)),
        sources="\n".join(_render_sources(sources))
        if sources
        else prompts.load("brief/no_sources"),
    )
    return f"{brief}\n\n{where_the_data_lives(cat)}"


def where_the_data_lives(cat: dict | None = None) -> str:
    """The section of the brief that says which engine this project runs on.

    One file per backend under ``prompts/backend/`` (`docs/CONNECTOR.md` §2.9):
    the local one names DuckDB and files, the Snowflake one names the dialect,
    the credits, the role and the scope. **Part of L1, not of L0**, because it is
    a fact about *this project*, and because a tool description is loaded once
    per process while the project's engine is not — so the descriptions that
    used to say *DuckDB dialect* point here instead.

    Read off `core.backend.active()`, which is what the engine itself runs on,
    so the prompt cannot describe a warehouse the checks are not using.
    """
    from portia.core import backend

    active = backend.active()
    if not active.remote:
        return prompts.load("backend/local")
    scope = list((cat or {}).get("scope") or [])
    return prompts.load(f"backend/{active.kind}").format(
        label=active.label or active.kind,
        scope=_render_scope(scope),
        writes=_render_writes(active),
    )


def _render_writes(active) -> str:
    """The paragraph on who writes tables (`CONNECTOR.md` §2.7.1).

    Two files, because the two modes are two different instructions and not one
    with a flag in it: on the hand-off, recording a step **is** the build of that
    table and the audit reads it where the team will; off, only a person's Run
    or Build writes, and the agent has to say so rather than promise a table.
    Both say that where a table goes is the spec's own ``target``, the agent's
    call per table, and offer the connection's own database as a place to start
    when the registry names one.
    """
    which = "agent_writes" if active.agent_writes else "build_writes"
    where = (
        f"the connection opens on `{active.opens_on}`"
        if active.opens_on
        else "the connection names no database, so ask the user which one to build in"
    )
    return prompts.load(f"backend/{which}").format(where=where)


#: How many scoped tables the brief names before it counts the rest. The index
#: above it already names every source; this is the *warehouse* list, and on a
#: 23-table project both in full would be the same list twice.
SCOPE_NAMED = 8


def _render_scope(scope: list[str]) -> str:
    if not scope:
        return "none yet, nothing has been brought into scope"
    shown = ", ".join(f"`{name}`" for name in scope[:SCOPE_NAMED])
    rest = len(scope) - SCOPE_NAMED
    return f"{shown} and {rest} more" if rest > 0 else shown


def model_index(root: str | Path) -> dict[str, dict]:
    """Every spec in the project, in build order: its layer, step count and inputs.

    **Computed from the directory on every request, never kept.** A second list
    of the project's models is one that can disagree with `discover_specs`, and
    the brief is the one place the agent learns a spec exists at all. Raises
    `ValueError` as `discover_specs` does, on two specs sharing a name; the
    caller decides what the brief says about that.
    """
    root = Path(root)
    models = spec.discover_specs(root)
    if not models:
        return {}
    deps = spec.dependencies(models, base_dir=root)
    try:
        order = spec.run_order(models, base_dir=root)
    except ValueError:
        # A cycle is `cli/build`'s to report; the index still has to list them.
        order = sorted(models)
    index = {}
    for name in order:
        doc = spec.load_spec(root / models[name])
        index[name] = {
            "layer": doc.get("layer"),
            "target": doc.get("target"),
            "n_steps": len(doc.get("steps") or []),
            "reads": sorted(deps.get(name, ())),
        }
    return index


def _render_models(root: Path) -> str:
    """One line per spec, or nothing at all when the project has none.

    **The agent could not read its own decision record** *(2026-09-04,
    `docs/PIPELINE.md` §9)*. The brief indexed sources and stopped, so a spec
    the user could see in the left pane was one the copilot could not know
    existed; asked about one, a Haiku session said it needed the spec's name and
    could only run it. The same rule as the source index: the name is the
    routing decision, and everything else is `read_spec`'s to answer.
    """
    try:
        index = model_index(root)
    except ValueError as exc:
        return "\n" + "\n".join(["## Pipeline", "", f"*The specs could not be listed: {exc}*", ""])
    if not index:
        return ""
    lines = []
    for name, model in index.items():
        n = model["n_steps"]
        line = f"- **{name}**" + (f" ({model['layer']})" if model["layer"] else "")
        line += f" — {n} step" + ("" if n == 1 else "s")
        if model["reads"]:
            line += f"; reads {', '.join(model['reads'])}"
        if model["target"]:
            line += f"; writes to `{model['target']}`"
        lines.append(line)
    return "\n" + "\n".join(["## Pipeline", "", *lines, ""])


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

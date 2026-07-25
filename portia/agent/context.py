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

NO_CONTEXT = (
    "The user has not described this project yet. That description is what makes "
    "a column's meaning decidable, so ask for it before interpreting anything."
)

NO_SOURCES = "No data sources have been indexed yet."


def build_brief(portia_dir: str = catalog.DEFAULT_DIR) -> str:
    """Render this project's L1 context as markdown for the system prompt."""
    try:
        cat = catalog.load_catalog(portia_dir)
    except FileNotFoundError:
        return f"# This project\n\n{NO_CONTEXT}\n"

    lines = ["# This project", ""]
    lines.append(cat.get("project") or NO_CONTEXT)

    sources = cat.get("sources") or {}
    grouped = _render_groups(cat.get("groups") or [], sources)
    if grouped:
        lines += ["", "## Groups", "", *grouped]

    lines += ["", "## Indexed sources", ""]
    lines += _render_sources(sources) if sources else [NO_SOURCES]
    lines += [
        "",
        "That is the whole index — one line each. For a source's columns and "
        "roles call `describe_source`; for its measured facts call `profile_source`.",
        "",
    ]
    return "\n".join(lines)


def _render_groups(groups: list[dict], sources: dict) -> list[str]:
    out = []
    for group in groups:
        members = ", ".join(group.get("sources") or []) or "(no members)"
        context = (group.get("context") or "").strip()
        out.append(f"- **{group.get('name')}** ({members})" + (f" — {context}" if context else ""))
    return out


def _render_sources(sources: dict) -> list[str]:
    out = []
    for name, entry in sources.items():
        shape = f"{len(entry.get('columns') or [])} cols"
        keys = ", ".join(entry.get("candidate_keys") or []) or "none"
        summary = _first_sentence(entry.get("summary") or "")
        out.append(f"- **{name}** — {shape}, candidate keys: {keys}. {summary}")
    return out


def _first_sentence(summary: str) -> str:
    """One line per source, so the index stays cheap however long a summary grows."""
    if "(auto-drafted from checks" in summary:
        return "*Not yet interpreted — this is an auto-drafted placeholder.*"
    head = summary.strip().split(". ")
    return (head[0] + ".") if head and head[0] else "*No summary yet.*"

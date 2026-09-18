"""The context map: what the agent is given, as one self-contained page.

Split from `context.py` for the reason `page.py` is split from `audit.py` — the
gathering is testable without rendering, and the rendering without a disk.

**The page is ordered by when the model meets a thing, not by where it lives in
the repo.** The system prompt is first because it is on every request; the tool
list is second because it arrives with it; the ladder is third because it is the
one piece of structure the prompts spend their words teaching; a tool's own card
is fourth. The files under `prompts/` are grouped the way `prompts/README.md`
groups them, and last comes the text that is *not* under `prompts/` — the
refusals and validation messages raised as plain strings, which the model reads
at the moment it is choosing its next move and which nobody edits as prose.

It obeys the same rule the audit page does, for the same reason: **it counts and
it never scores.** Every number here is a character count, a call count or
something a log recorded. There is no "quality" column, no tool is ranked
against another, and the ladder is drawn in the order the prompts teach it
rather than in any order this page worked out. Where a tool has never been
called, that is stated rather than filled with a plausible example — an invented
tool result on the page built to show real ones would be the one lie that
matters.
"""

from __future__ import annotations

import json
from datetime import datetime

from devtools.context import Delivered, Injected, Message, Observation, PromptFile
from devtools.html import asset, clip, esc, folded, value_html
from portia.core.present import count, duration

#: How much of a recorded tool result the collapsed line shows.
PREVIEW_CHARS = 150

#: How many recorded calls a tool card shows before folding the rest away. Ten
#: `describe_source` calls is a scroll; three is enough to see what the shape of
#: an answer is, which is what the card is for.
SHOWN_CALLS = 3

#: The sections, in page order: anchor, heading. The sidebar is generated from
#: this so a section cannot exist without a way to reach it.
SECTIONS = (
    ("overview", "Overview"),
    ("system", "The system prompt"),
    ("toolbox", "The tool list"),
    ("ladder", "The ladder"),
    ("tools", "Tools, one by one"),
    ("tasks", "Opening instructions"),
    ("errors", "Refusals"),
    ("brief", "Brief fragments"),
    ("messages", "Text outside prompts/"),
)

#: What each kind of prompt file is, and when the model meets it — the table in
#: `prompts/README.md`, kept beside the files it describes. Prose for a human
#: reading this page; the *derived* half of the same question is each file's
#: call sites, which are read out of the code and cannot go stale.
KIND_NOTES = {
    "system": "Composed into the system prompt. Every request, whatever the surface.",
    "brief": "Composed into the system prompt beneath L0, from this project's .portia/.",
    "tools": "Delivered in the tool list, alongside the schema. Every request.",
    "tasks": "Sent as the opening message of one invocation, with what the operator typed.",
    "errors": "Handed back when the engine refuses a call — read as the model picks its next move.",
}


def render_context_page(injected: Injected) -> str:
    """Everything the agent is given, in one file you can keep and compare."""
    parts = [
        "<!doctype html>",
        '<html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>portia context · {esc(injected.project.name)}</title>",
        f"<style>{asset('base.css')}</style>",
        f"<style>{asset('context.css')}</style>",
        "</head><body>",
        _topbar(injected),
        '<div class="layout">',
        _sidebar(injected),
        '<main class="main">',
        _overview(injected),
        _system(injected),
        _toolbox(injected),
        _ladder(injected),
        _tools(injected),
        _prompt_section("tasks", "Opening instructions", injected),
        _prompt_section("errors", "Refusals", injected),
        _prompt_section("brief", "Brief fragments", injected),
        _messages(injected),
        "</main>",
        "</div>",
        f"<script>{asset('context.js')}</script>",
        "</body></html>",
    ]
    return "\n".join(parts)


# --- chrome -------------------------------------------------------------------


def _topbar(injected: Injected) -> str:
    return (
        '<header class="topbar">'
        '<div class="brand">what the agent is given</div>'
        f'<div class="where">{esc(injected.project)}</div>'
        '<input class="find" type="search" placeholder="filter by name or text" '
        'data-find aria-label="filter">'
        '<div class="acts">'
        '<button data-act="expand">expand all</button>'
        '<button data-act="collapse">collapse all</button>'
        "</div>"
        f'<div class="stamp">rendered {esc(datetime.now().strftime("%Y-%m-%d %H:%M"))}</div>'
        "</header>"
    )


def _sidebar(injected: Injected) -> str:
    rows = []
    for anchor, heading in SECTIONS:
        rows.append(f'<a class="jump" href="#{anchor}">{esc(heading)}</a>')
        if anchor == "tools":
            rows.append(
                '<div class="sub">'
                + "".join(
                    f'<a class="jump small" href="#tool-{esc(t.name)}">'
                    f'{esc(t.name)}<span class="size">{len(t.description):,}</span></a>'
                    for t in injected.tools
                )
                + "</div>"
            )
    return f'<nav class="sidebar"><h2>Sections</h2>{"".join(rows)}</nav>'


# --- overview -----------------------------------------------------------------


def _overview(injected: Injected) -> str:
    tool_chars = sum(len(t.description) for t in injected.tools)
    gated = [t for t in injected.tools if not t.auto_approved]
    calls = sum(len(v) for v in injected.observed.values())
    stats = [
        ("project", injected.project),
        ("portia", _sha(injected) or "—"),
        ("always on", f"{injected.always_on_chars:,} chars"),
        ("system prompt", f"{len(injected.system_prompt):,} chars"),
        ("tool descriptions", f"{tool_chars:,} chars"),
        ("tools", f"{len(injected.tools)} ({len(gated)} stop for confirmation)"),
        ("prompt files", len(injected.prompts)),
        ("logs read", count(len(injected.logs), "log")),
        ("recorded calls", calls),
    ]
    tiles = "".join(
        f'<div class="stat"><span class="lab">{esc(label)}</span>'
        f'<span class="val">{esc(value)}</span></div>'
        for label, value in stats
    )
    return (
        f'<section class="sec" id="overview">{_head("Overview", "")}'
        f'<p class="lede">Every request carries the system prompt and the whole tool list, '
        f"whatever the model is asked. That is <b>{injected.always_on_chars:,} characters</b> "
        f"before the first word of anyone's message — "
        f"{len(injected.l0):,} of L0, {len(injected.l1):,} of this project's brief, "
        f"and {tool_chars:,} of tool descriptions. Everything else on this page is "
        "pulled by the agent, one call at a time.</p>"
        f'<div class="stats">{tiles}</div>'
        f"{_measured(injected)}{_orphans(injected)}"
        '<p class="note">Character counts, not tokens: characters are a fact this page can '
        "read, and tokens are what a run measures. The measured ones below come from logs.</p>"
        "</section>"
    )


def _measured(injected: Injected) -> str:
    """What the logs recorded about the cost of carrying all this.

    Tokens, not an estimate of tokens. `runlog.token_totals` is the one
    arithmetic — the SDK's `input_tokens` excludes the cached part, and nearly
    all of a portia turn's input is exactly the cached part.
    """
    if not injected.logs:
        return ""
    rows = []
    for summary in injected.logs:
        sent, cached = summary.get("input_tokens"), summary.get("cached_tokens")
        if sent is None:
            continue
        rows.append(
            f'<tr><td class="mono">{esc(summary.get("name"))}</td>'
            f"<td>{esc(summary.get('kind'))}</td>"
            f"<td>{esc(summary.get('model') or '?')}</td>"
            f"<td>{sent:,}</td><td>{(cached or 0):,}</td>"
            f"<td>{esc(count(summary.get('tools') or 0, 'call'))}</td></tr>"
        )
    if not rows:
        return ""
    return (
        '<details class="fold"><summary>what those characters cost, as the logs measured it'
        f'<span class="size">{len(rows)} logs</span></summary>'
        '<table class="grid"><thead><tr><th>log</th><th>kind</th><th>model</th>'
        "<th>input tokens</th><th>of which cached</th><th>tool calls</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
        '<p class="note">Input tokens are the whole input, cached and fresh — the pushed '
        "context is what the cache holds, so the SDK's raw field reports almost none of it.</p>"
        "</details>"
    )


def _orphans(injected: Injected) -> str:
    if not injected.orphans:
        return ""
    items = "".join(f"<li>{esc(o)}</li>" for o in injected.orphans)
    return (
        '<div class="warn"><b>Wired up wrong</b>'
        "<p>A tool with no description is still offered to the model, and a prompt nothing "
        "loads is text you will edit for nothing.</p>"
        f"<ul>{items}</ul></div>"
    )


# --- the always-on half --------------------------------------------------------


def _system(injected: Injected) -> str:
    l0, l1 = injected.l0.strip(), injected.l1.strip()
    body = [
        _head(
            "The system prompt",
            "L0 (how to work) + L1 (this project), composed by agent/session.py and sent on "
            "every request. The second half does not exist until a project is indexed, which "
            "is why this page is rendered against one.",
        )
    ]
    if not injected.composed_as_expected:
        body.append(
            '<div class="warn"><b>Composed differently than this page assumed</b>'
            "<p>The sent prompt is not simply <code>copilot.md</code> then the brief. "
            "The verbatim version below is the one to trust; the two halves are this page's "
            "reconstruction.</p></div>"
        )
    body.append(
        f'<div class="card"><div class="card-head"><b>L0 · copilot.md</b>'
        f'<span class="tag">static</span>'
        f'<span class="size">{len(l0):,} chars</span></div>'
        f'<pre class="block prose">{esc(l0)}</pre></div>'
    )
    body.append(
        f'<div class="card"><div class="card-head"><b>L1 · this project</b>'
        f'<span class="tag">built from {esc(injected.portia_dir)}</span>'
        f'<span class="size">{len(l1):,} chars</span></div>'
        f'<pre class="block prose">{esc(l1)}</pre>'
        '<p class="note">Composed by <code>agent/context.py</code> from '
        "<code>brief/template.md</code>, the project's own description, its groups, and one "
        "line per indexed source. Index another source and this grows.</p></div>"
    )
    body.append(folded("the composed string, exactly as sent", injected.system_prompt, cls="fold"))
    return f'<section class="sec" id="system">{"".join(body)}</section>'


def _toolbox(injected: Injected) -> str:
    """The tool list as a whole — what arrives with the prompt, and what else is set."""
    rows = "".join(
        f'<tr><td class="mono"><a href="#tool-{esc(t.name)}">{esc(t.name)}</a></td>'
        f'<td><span class="rung r-{esc(t.rung)}">{esc(t.rung or "—")}</span></td>'
        f"<td>{_permission(t)}</td>"
        f"<td>{len(t.description):,}</td>"
        f"<td>{esc(_fields(t))}</td>"
        f"<td>{len(injected.observed.get(t.name, ()))}</td></tr>"
        for t in injected.tools
    )
    from portia.agent import providers, session

    settings = [
        ("providers", ", ".join(providers.KINDS) + f" (default {providers.DEFAULT_KIND})"),
        ("default model", session.DEFAULT_MODEL),
        ("efforts offered", ", ".join(session.EFFORTS)),
        ("built-in tools", "AskUserQuestion, and nothing else"),
        ("filesystem / shell", "none — the agent cannot open a file"),
        ("auto-approved", ", ".join(t.name for t in injected.tools if t.auto_approved)),
        ("stops for a yes/no", ", ".join(t.name for t in injected.tools if not t.auto_approved)),
        ("this repo's CLAUDE.md", "not inherited (setting_sources=[])"),
    ]
    config = "".join(
        f'<div class="kv"><span class="k">{esc(k)}</span><span class="v">{esc(v)}</span></div>'
        for k, v in settings
    )
    return (
        '<section class="sec" id="toolbox">'
        + _head(
            "The tool list",
            "Delivered with the system prompt: every tool, its description and its schema, "
            "on every request whether or not it is called.",
        )
        + '<table class="grid"><thead><tr><th>tool</th><th>rung</th><th>permission</th>'
        "<th>chars</th><th>arguments</th><th>calls in these logs</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>"
        f'<div class="card"><div class="card-head"><b>the rest of the options</b></div>{config}'
        '<p class="note">From <code>agent/session.py</code>. The empty tool list is the '
        "non-negotiable, not a default: with no filesystem tools the agent's whole view of "
        "the data is what the checks return.</p></div>"
        "</section>"
    )


def _permission(tool: Delivered) -> str:
    if tool.auto_approved:
        mark = "read-only" if tool.read_only else "auto"
        return f'<span class="perm auto">{mark}</span>'
    return '<span class="perm gated">confirm</span>'


def _fields(tool: Delivered) -> str:
    properties = tool.schema.get("properties") or {}
    required = set(tool.schema.get("required") or ())
    if not properties:
        return "none"
    return ", ".join(f"{n}*" if n in required else n for n in properties)


# --- the ladder ----------------------------------------------------------------


def _ladder(injected: Injected) -> str:
    """The climb, in the order the prompts teach it — not an order this page chose."""
    order = ["graph_lookup", "get_context", "describe_source", "profile_source", "join_findings"]
    by_name = {t.name: t for t in injected.tools}
    rungs = []
    for name in order:
        tool = by_name.get(name)
        if tool is None:
            continue
        calls = len(injected.observed.get(name, ()))
        rungs.append(
            f'<div class="rung-row">'
            f'<span class="rung r-{esc(tool.rung)}">{esc(tool.rung)}</span>'
            f'<a class="mono" href="#tool-{esc(name)}">{esc(name)}</a>'
            f'<span class="rung-note">{esc(tool.rung_note)}</span>'
            f'<span class="size">{esc(count(calls, "call"))} here</span>'
            "</div>"
        )
    rest = [t for t in injected.tools if t.name not in order]
    others = "".join(
        f'<div class="rung-row">'
        f'<span class="rung r-{esc(t.rung)}">{esc(t.rung)}</span>'
        f'<a class="mono" href="#tool-{esc(t.name)}">{esc(t.name)}</a>'
        f'<span class="rung-note">{esc(t.rung_note)}</span>'
        f'<span class="size">{esc(count(len(injected.observed.get(t.name, ())), "call"))} here</span>'
        "</div>"
        for t in rest
    )
    return (
        '<section class="sec" id="ladder">'
        + _head(
            "The ladder",
            "Context arrives cheapest-first and the agent decides when to climb. L1 is pushed; "
            "everything above it is pulled. The router is not a rung — it is what tells you "
            "which ladder to climb, so it sits before L2.",
        )
        + f'<div class="ladder">{"".join(rungs)}</div>'
        + f'<h3>Not on the ladder</h3><div class="ladder">{others}</div>'
        + '<p class="note">Placement is the one thing on this page that is written down rather '
        "than read out of the code — no tool description states its own rung. It restates "
        "<code>copilot.md</code>, and a test fails if a tool is added without one.</p>"
        "</section>"
    )


# --- one tool at a time ---------------------------------------------------------


def _tools(injected: Injected) -> str:
    cards = "".join(_tool_card(t, injected.observed.get(t.name, [])) for t in injected.tools)
    return (
        '<section class="sec" id="tools">'
        + _head(
            "Tools, one by one",
            "The description as the model receives it, the schema the SDK builds from "
            "agent/tools.py, and what the tool actually handed back in real runs.",
        )
        + cards
        + "</section>"
    )


def _tool_card(tool: Delivered, seen: list[Observation]) -> str:
    head = (
        f'<div class="card-head"><b class="tool-name">{esc(tool.name)}</b>'
        f'<span class="rung r-{esc(tool.rung)}">{esc(tool.rung or "—")}</span>'
        f"{_permission(tool)}"
        f'<span class="qualified">{esc(tool.qualified)}</span>'
        f'<span class="size">{len(tool.description):,} chars</span></div>'
    )
    body = [f'<pre class="block prose">{esc(tool.description)}</pre>']
    if tool.raw_prompt.strip() and _flat(tool.raw_prompt) != _flat(tool.description):
        body.append(
            folded(
                f"the file it came from · {tool.prompt_path.name if tool.prompt_path else '?'}"
                " (placeholders unfilled)",
                tool.raw_prompt.strip(),
            )
        )
    body.append(
        folded(
            "input schema, as the SDK sends it",
            json.dumps(tool.schema, indent=2),
        )
    )
    if tool.handler_doc:
        body.append(
            folded(
                "what it returns — the handler's docstring (the model never reads this)",
                tool.handler_doc,
            )
        )
    body.append(_observed(tool, seen))
    return f'<div class="card tool" id="tool-{esc(tool.name)}" data-name="{esc(tool.name)}">{head}{"".join(body)}</div>'


def _observed(tool: Delivered, seen: list[Observation]) -> str:
    """Real results, or an honest blank.

    What a tool returns depends on the data, so there is nothing in the code to
    read it off. Either a run called it and this shows what came back, or none
    did and this says so. The one thing it must not do is show a plausible
    example: the page exists to say what the model was actually handed.
    """
    if not seen:
        return (
            '<div class="none-seen">Never called in the logs read for this page — '
            "so nothing here can say what it returns. Run it and re-render.</div>"
        )
    shown = "".join(_one_call(o) for o in seen[:SHOWN_CALLS])
    more = ""
    if len(seen) > SHOWN_CALLS:
        more = (
            f'<details class="fold"><summary>the other '
            f'{len(seen) - SHOWN_CALLS} calls<span class="size">recorded</span></summary>'
            f"{''.join(_one_call(o) for o in seen[SHOWN_CALLS:])}</details>"
        )
    errors = sum(1 for o in seen if o.is_error)
    mark = f" · {errors} errored" if errors else ""
    return (
        f'<div class="seen"><div class="seen-head">what it returned · '
        f"{esc(count(len(seen), 'recorded call'))}{esc(mark)}</div>{shown}{more}</div>"
    )


def _one_call(observation: Observation) -> str:
    took = (
        f'<span class="took">{esc(duration(observation.seconds))}</span>'
        if observation.seconds is not None
        else ""
    )
    gate = ""
    if observation.allowed is not None:
        word = "allowed" if observation.allowed else "refused"
        gate = f'<span class="gate {word}">write · {word}</span>'
    result = observation.result
    label = "error" if observation.is_error else "result"
    return (
        f'<div class="call-seen{" has-error" if observation.is_error else ""}">'
        f'<div class="seen-meta"><span class="mono">{esc(observation.log)}</span>{took}{gate}</div>'
        f'<div class="call-in">{value_html(observation.input)}</div>'
        f'<details class="res {label}"><summary><span class="tag">{label}</span>'
        f'<span class="peek">{esc(clip(result, PREVIEW_CHARS))}</span>'
        f'<span class="size">{len(result):,} chars</span></summary>'
        f'<pre class="block">{esc(result)}</pre></details></div>'
    )


# --- the files under prompts/ ---------------------------------------------------


def _prompt_section(kind: str, heading: str, injected: Injected) -> str:
    files = [p for p in injected.prompts if p.kind == kind]
    if not files:
        return ""
    cards = "".join(_prompt_card(p) for p in files)
    return (
        f'<section class="sec" id="{esc(kind)}">'
        + _head(heading, KIND_NOTES.get(kind, ""))
        + cards
        + "</section>"
    )


def _prompt_card(prompt: PromptFile) -> str:
    sites = (
        "".join(
            f'<span class="chip">{esc(s.path)}:{s.line} <b>{esc(s.call)}</b></span>'
            for s in prompt.sites
        )
        or '<span class="chip none">nothing loads it</span>'
    )
    placeholders = (
        f'<span class="chip">filled with {esc(", ".join(prompt.placeholders))}</span>'
        if prompt.placeholders
        else ""
    )
    text = prompt.rendered if prompt.rendered is not None else prompt.raw.strip()
    return (
        f'<div class="card" id="prompt-{esc(prompt.name.replace("/", "-"))}" '
        f'data-name="{esc(prompt.name)}">'
        f'<div class="card-head"><b class="tool-name">{esc(prompt.name)}.md</b>'
        f'<span class="size">{len(text):,} chars</span></div>'
        f'<div class="mix">{placeholders}{sites}</div>'
        f'<pre class="block prose">{esc(text)}</pre></div>'
    )


# --- text that is not under prompts/ ---------------------------------------------


def _messages(injected: Injected) -> str:
    by_file: dict[str, list[Message]] = {}
    for message in injected.messages:
        by_file.setdefault(message.path, []).append(message)
    blocks = []
    for path, found in by_file.items():
        rows = "".join(_message_row(m) for m in found)
        blocks.append(
            f'<div class="card" data-name="{esc(path)}">'
            f'<div class="card-head"><b class="mono">{esc(path)}</b>'
            f'<span class="size">{len(found)} messages</span></div>'
            f'<table class="grid msgs"><tbody>{rows}</tbody></table></div>'
        )
    return (
        '<section class="sec" id="messages">'
        + _head(
            "Text outside prompts/",
            "Refusals and validation messages raised as plain strings. tools.py turns any "
            "exception out of a handler into a tool result, so these are read at the exact "
            "moment the model is choosing what to do next — which makes them prompt text, "
            "wherever they live.",
        )
        + "".join(blocks)
        + '<p class="note">Found by parsing, not by judgment: whether a given line is '
        "reachable from a tool call depends on the path, so each is shown with its file and "
        "line rather than filtered by a guess. A row pointing at <code>errors/</code> is "
        "already in the section above and is not repeated here.</p>"
        "</section>"
    )


def _message_row(message: Message) -> str:
    if message.prompt:
        body = (
            f'<a href="#prompt-{esc(message.prompt.replace("/", "-"))}" class="mono">'
            f"{esc(message.prompt)}.md</a>"
        )
    else:
        body = f'<span class="msg">{esc(message.text)}</span>'
    return (
        f'<tr><td class="line">{message.line}</td>'
        f'<td class="kind">{esc(message.kind)}</td><td>{body}</td></tr>'
    )


# --- small ------------------------------------------------------------------------


def _head(title: str, note: str) -> str:
    lede = f'<p class="lede">{esc(note)}</p>' if note else ""
    return f"<h1>{esc(title)}</h1>{lede}"


def _sha(injected: Injected) -> str | None:
    for summary in injected.logs:
        if summary.get("portia_sha"):
            return str(summary["portia_sha"])
    from portia import runlog

    return runlog.portia_sha()


def _flat(text: str) -> str:
    return " ".join(str(text).split())

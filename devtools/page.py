"""The run-audit page: transcripts in, one self-contained HTML string out.

Split from `audit.py` so the rendering is testable without touching the disk or
a browser — the same reason `portia/ui/state.py` imports no NiceGUI.

Three things this shows that the terminal replay does not, and each is why the
tool exists:

* **A call and its result are one block.** In the stream they interleave — two
  `describe_source` calls, then two results — so reading a log linearly means
  holding tool-use ids in your head. They are paired back up by id here.
* **A write confirmation is folded onto the call it gated.** `approval` repeats
  the call's whole input, which for a `record_step` carrying SQL is the largest
  thing in the file, printed twice. It becomes a badge.
* **Nothing is clipped.** `cli/history.py` cuts a tool result at 400 characters
  on purpose, because a terminal replay is for finding the moment. Tuning the
  prompts is the opposite job: the evidence the copilot actually read is the
  thing that explains what it did next, so it is all here, behind a `<details>`.

**It counts and it never scores** — `portia/runlog.py`'s rule, and `CLAUDE.md`'s
facts-vs-judgment line, apply to a dev tool reading the log exactly as they
apply to the log. There is no "quality" column, nothing is sorted by how bad it
looks, and an errored call is coloured because an error is a *kind* of outcome,
not because it ranks below a successful one. Whether the copilot should have
asked three times is your call to make with the page open, not a number the page
computes for you.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from devtools.html import asset as _asset
from devtools.html import clip as _clip
from devtools.html import esc as _esc
from devtools.html import money as _money
from devtools.html import value_html as _value_html
from portia import runlog
from portia.agent import events
from portia.core.present import count, duration

#: How much of a tool result the collapsed line shows. Long enough to tell two
#: profiles apart, short enough that thirty calls still scroll.
PREVIEW_CHARS = 180

#: A tool result under this many characters is rendered open — collapsing a
#: two-line answer costs a click and saves no space.
INLINE_RESULT_CHARS = 400

#: Kinds that get a body toggle in the toolbar. The copilot's prose and its
#: tool calls are the spine of the page and are never hidden.
FILTERS = (
    ("thinking", "thinking"),
    ("results", "tool results"),
    ("errors-only", "errors only"),
)


@dataclass
class Call:
    """One tool call with everything the stream later said about it."""

    call: events.Event
    #: Where in the call sequence this one is, so the chip strip can link to it.
    seq: int = 0
    result: events.Event | None = None
    approval: events.Event | None = None
    allowed: bool | None = None
    #: Whether this write ran without stopping for anyone (`agent/ask.py`).
    automatic: bool = False
    #: Seconds the loop sat waiting for the human on this one write, measured by
    #: `agent/ask.py` because the stamps cannot say (`events.approval_result_event`).
    #: ``None`` on a log written before that existed.
    waited: float | None = None
    #: When the call went out and when its result came back, as logged.
    at: str | None = None
    result_at: str | None = None

    @property
    def name(self) -> str:
        return events.tool_label(str(self.call.data.get("name", "")))

    @property
    def errored(self) -> bool:
        return bool(self.result and self.result.data.get("is_error"))

    @property
    def seconds(self) -> float | None:
        """How long the tool took. ``None`` on a log written before stamps."""
        return runlog.elapsed(self.at, self.result_at)


@dataclass
class Timed:
    """An event and when it was logged — everything that is not a call."""

    event: events.Event
    at: str | None = None


@dataclass
class Exchange:
    """One message sent and everything that happened because of it."""

    prompt: events.Event | None = None
    at: str | None = None
    nodes: list[Any] = field(default_factory=list)


def render_page(transcripts: list[runlog.Transcript], *, title: str, source: str) -> str:
    """Every log in one page: a sidebar to pick, a timeline to read."""
    summaries = [runlog.summary(t) for t in transcripts]
    parts = [
        "<!doctype html>",
        '<html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{_esc(title)}</title>",
        f"<style>{_asset('base.css')}</style>",
        f"<style>{_asset('audit.css')}</style>",
        "</head><body>",
        _topbar(title, source, len(transcripts)),
        '<div class="layout">',
        _sidebar(summaries),
        '<main class="main">',
    ]
    if not transcripts:
        parts.append('<p class="empty">No chats or indexing jobs found.</p>')
    for index, (transcript, summary) in enumerate(zip(transcripts, summaries, strict=True)):
        parts.append(_log_section(index, transcript, summary))
    parts += ["</main>", "</div>", f"<script>{_asset('audit.js')}</script>", "</body></html>"]
    return "\n".join(parts)


# --- chrome -----------------------------------------------------------------


def _topbar(title: str, source: str, n_logs: int) -> str:
    toggles = "".join(
        f'<label class="toggle"><input type="checkbox" data-filter="{key}">'
        f"<span>{_esc(label)}</span></label>"
        for key, label in FILTERS
    )
    generated = datetime.now().strftime("%Y-%m-%d %H:%M")
    return (
        '<header class="topbar">'
        f'<div class="brand">{_esc(title)}</div>'
        f'<div class="where">{_esc(source)} · {_esc(count(n_logs, "log"))}</div>'
        f'<div class="filters">{toggles}</div>'
        '<div class="acts">'
        '<button data-act="expand">expand all</button>'
        '<button data-act="collapse">collapse all</button>'
        "</div>"
        f'<div class="stamp">rendered {_esc(generated)}</div>'
        "</header>"
    )


def _sidebar(summaries: list[dict]) -> str:
    """Two lists, never one — a chat is a conversation, an indexing is a job.

    `docs/CONVERSATION.md` §3, and the reason the app's left pane pins them
    apart: the only thing a merged list could sort on is recency, which buries
    the conversation you had under twenty files the app profiled.
    """
    groups = []
    for kind, heading in ((runlog.CHAT, "Chats"), (runlog.INDEXING, "Indexing")):
        rows = [
            _sidebar_row(index, summary)
            for index, summary in enumerate(summaries)
            if summary.get("kind") == kind
        ]
        body = "".join(rows) or '<div class="none">none</div>'
        groups.append(f'<h2>{heading}</h2><div class="group">{body}</div>')
    return f'<nav class="sidebar">{"".join(groups)}</nav>'


def _sidebar_row(index: int, summary: dict) -> str:
    errors = summary.get("tool_errors") or 0
    mark = f'<span class="pill err">{errors}</span>' if errors else ""
    meta = " · ".join(
        p
        for p in (
            summary.get("model") or "?",
            f"{summary.get('tools', 0)} calls",
            _money(summary.get("cost_usd")),
        )
        if p
    )
    return (
        f'<button class="pick" data-target="log-{index}">'
        f'<span class="pick-name">{_esc(summary.get("name") or "")}{mark}</span>'
        f'<span class="pick-meta">{_esc(meta)}</span>'
        f'<span class="pick-prompt">{_esc(_clip(summary.get("prompt") or "", 90))}</span>'
        "</button>"
    )


# --- one log ----------------------------------------------------------------


def _log_section(index: int, transcript: runlog.Transcript, summary: dict) -> str:
    body = [_log_header(transcript, summary), _sequence(index, transcript)]
    for number, exchange in enumerate(_exchanges(transcript), start=1):
        body.append(_exchange_html(index, number, exchange))
    return f'<section class="log" id="log-{index}">{"".join(body)}</section>'


def _log_header(transcript: runlog.Transcript, summary: dict) -> str:
    """What this log is and what it cost. Every field is stated by the stream."""
    effort = summary.get("effort")
    models = summary.get("models") or []
    also = f" (also {', '.join(models[1:])})" if len(models) > 1 else ""
    stats = [
        ("kind", summary.get("kind")),
        ("model", f"{summary.get('model') or '?'}{f'/{effort}' if effort else ''}{also}"),
        ("started", summary.get("started")),
        ("portia", summary.get("portia_sha") or "?"),
        ("messages", summary.get("exchanges")),
        ("tool calls", summary.get("tools")),
        ("errored", summary.get("tool_errors")),
        ("asked", f"{summary.get('asked')}× / {summary.get('questions')} questions"),
        (
            "writes",
            f"{summary.get('approved')} allowed, {summary.get('refused')} refused"
            + (f", {summary['auto_approved']} not asked" if summary.get("auto_approved") else ""),
        ),
        ("ended", summary.get("subtype")),
        # Beside cost and tokens on purpose, because that is the kind of fact it
        # is (`runlog.summary`). It is the field here a reader is most likely to
        # mistake for a score, so it is placed among the other spend descriptors
        # rather than given a line of its own, and nothing on this page sorts by
        # it.
        ("took", duration(summary["wall_seconds"]) if summary.get("wall_seconds") else ""),
        # Beside "took" because it is a slice of it, and among the spend
        # descriptors for the same reason "took" is (`runlog.summary`).
        (
            "waiting on you",
            # `is not None`, not truthiness: a measured 0.0 is a measurement and
            # an absent one is a log written before this existed. The line above
            # predates that distinction.
            duration(summary["waited_seconds"])
            if summary.get("waited_seconds") is not None
            else "",
        ),
        ("cost", _money(summary.get("cost_usd")) or "—"),
        ("tokens", _tokens(summary)),
    ]
    cells = "".join(
        f'<div class="stat"><span class="lab">{_esc(label)}</span>'
        f'<span class="val">{_esc(str(value))}</span></div>'
        for label, value in stats
        if value not in (None, "")
    )
    mix = summary.get("by_tool") or {}
    mix_html = (
        '<div class="mix">'
        + "".join(f'<span class="chip">{_esc(n)} <b>{c}</b></span>' for n, c in mix.items())
        + "</div>"
        if mix
        else ""
    )
    # Said out loud rather than left as blank columns. A log written before
    # `runlog` stamped its records has no times at all, and a page that simply
    # showed nothing there would read as "everything was instant".
    gaps = []
    if not any(transcript.times):
        gaps.append(
            "This log has no per-event timestamps, so nothing here says how long "
            "anything took — it was written before portia recorded them."
        )
    if not transcript.prompts:
        gaps.append(
            "This log did not record the prompts it read. What the copilot was "
            f"given is whatever portia {summary.get('portia_sha') or '?'} said."
        )
    gap_html = "".join(f'<div class="gap">{_esc(g)}</div>' for g in gaps)
    return (
        '<div class="loghead">'
        f"<h1>{_esc(transcript.name)}</h1>"
        f'<div class="path">{_esc(str(transcript.path))}</div>'
        f'<div class="stats">{cells}</div>{mix_html}{gap_html}'
        f"{_prompts_html(transcript)}"
        "</div>"
    )


def _prompts_html(transcript: runlog.Transcript) -> str:
    """What the copilot was reading, verbatim, folded shut.

    The half of a run that never appeared in any transcript: you could see every
    tool the copilot called and not the sentence that told it when to call one.
    Comparing two runs is the whole job of this page, and until the log carried
    these, comparing what they were *told* meant checking out two shas.

    Collapsed, and last in the header, because it is identical on every run
    between two prompt edits — it is reference, not narrative.
    """
    read = transcript.prompts or {}
    system, tools = str(read.get("system") or ""), dict(read.get("tools") or {})
    if not system and not tools:
        return ""
    blocks = []
    if system:
        blocks.append(_prompt_block("system prompt (L0 + this project's brief)", system))
    blocks += [_prompt_block(f"tool · {name}", text) for name, text in sorted(tools.items())]
    total = len(system) + sum(len(t) for t in tools.values())
    return (
        f'<details class="prompts"><summary>what it read'
        f'<span class="size">{len(tools)} tools, {total:,} chars</span></summary>'
        f"{''.join(blocks)}</details>"
    )


def _prompt_block(label: str, text: str) -> str:
    return (
        f'<details class="prompt-one"><summary>{_esc(label)}'
        f'<span class="size">{len(text):,} chars</span></summary>'
        f'<pre class="block">{_esc(text)}</pre></details>'
    )


def _sequence(index: int, transcript: runlog.Transcript) -> str:
    """Every call in order, as chips that jump to it.

    The climb *is* the finding — which rung it pulled, and whether the router
    routed anywhere — so it is kept whole and in order rather than tallied.
    """
    calls = [e for e in transcript.events if e.kind == events.TOOL_CALL]
    if not calls:
        return ""
    errors = {
        e.data.get("id")
        for e in transcript.events
        if e.kind == events.TOOL_RESULT and e.data.get("is_error")
    }
    chips = []
    for n, event in enumerate(calls):
        name = events.tool_label(str(event.data.get("name", "")))
        subject = runlog.call_subject(event.data.get("input"))
        bad = " bad" if event.data.get("id") in errors else ""
        chips.append(
            f'<a class="seq-chip{bad}" href="#call-{index}-{n}">'
            f"<b>{_esc(name)}</b>{f'<span>{_esc(subject)}</span>' if subject else ''}</a>"
        )
    return f'<div class="sequence">{"".join(chips)}</div>'


def _exchange_html(log_index: int, number: int, exchange: Exchange) -> str:
    parts = []
    if exchange.prompt is not None:
        data = exchange.prompt.data
        badge = data.get("model") or ""
        if data.get("effort"):
            badge = f"{badge}/{data['effort']}"
        parts.append(
            f'<div class="ev prompt"><div class="who">message {number}'
            f'<span class="model">{_esc(badge)}</span>{_exchange_took(exchange)}</div>'
            f'<div class="said">{_esc(str(data.get("text") or ""))}</div></div>'
        )
    for node in exchange.nodes:
        parts.append(_call_html(log_index, node) if isinstance(node, Call) else _plain_html(node))
    return f'<div class="exchange">{"".join(parts)}</div>'


def _exchange_took(exchange: Exchange) -> str:
    """How long from the message going out to the last thing logged for it.

    Wall-clock, so a question the copilot asked is counted as time — the reader
    is waiting either way, and silently excluding the human's thinking would make
    the number mean something other than what it says.
    """
    stamps = [n.at for n in exchange.nodes if isinstance(n, Timed) and n.at]
    stamps += [n.result_at or n.at for n in exchange.nodes if isinstance(n, Call)]
    last = max((s for s in stamps if s), default=None)
    seconds = runlog.elapsed(exchange.at, last)
    return f'<span class="took">{_esc(duration(seconds))}</span>' if seconds is not None else ""


def _call_html(log_index: int, node: Call) -> str:
    anchor = f"call-{log_index}-{node.seq}"
    classes = "ev call" + (" has-error" if node.errored else "")
    gate = ""
    if node.approval is not None or node.allowed is not None:
        if node.allowed is None:
            gate = '<span class="gate pending">write · unanswered</span>'
        else:
            word = "allowed" if node.allowed else "refused"
            # The wait rides on the gate rather than beside the call's own
            # timing, because they measure two different things: `took` is
            # portia working, this is portia stopped. Stated flatly — nobody is
            # slow for reading a `record_step` payload, and that reading is what
            # the product is for.
            sat = f" · {_esc(duration(node.waited))}" if node.waited is not None else ""
            if node.automatic:
                # Not `allowed`: nobody allowed it. The page counts these apart
                # in its header too (`runlog.summary` → `auto_approved`).
                gate = '<span class="gate allowed">write · not asked</span>'
            else:
                gate = f'<span class="gate {word}">write · {word}{sat}</span>'
    took = node.seconds
    # Before the gate, so a `write · allowed` badge stays hard right where it is
    # on every card. How long it took and whether it was allowed are two facts
    # about the same call, and neither is a comment on the other.
    timing = f'<span class="took">{_esc(duration(took))}</span>' if took is not None else ""
    head = (
        f'<div class="call-head"><span class="dot"></span>'
        f"<b>{_esc(node.name)}</b>{timing}{gate}</div>"
    )
    body = _value_html(_call_input(node.call))
    return (
        f'<div class="{classes}" id="{anchor}">{head}'
        f'<div class="call-in">{body}</div>{_result_html(node)}</div>'
    )


def _call_input(event: events.Event) -> dict:
    """The call's arguments, minus the one every call carries.

    `portia_dir` is on every tool and is the same value every time; it is noise
    in a page you are reading to see what the copilot chose to ask for.
    """
    data = event.data.get("input") or {}
    return {k: v for k, v in data.items() if k != "portia_dir"} if isinstance(data, dict) else {}


def _result_html(node: Call) -> str:
    if node.result is None:
        return '<div class="no-result">no result recorded</div>'
    text = str(node.result.data.get("text") or "")
    error = bool(node.result.data.get("is_error"))
    mark = "error" if error else "result"
    # Errors and short answers open by default; a profile does not, because ten
    # open profiles is a page you scroll past rather than read.
    is_open = " open" if error or len(text) <= INLINE_RESULT_CHARS else ""
    chars = f"{len(text):,} chars"
    preview = _clip(text, PREVIEW_CHARS)
    return (
        f'<details class="res {mark}"{is_open}>'
        f'<summary><span class="tag">{mark}</span>'
        f'<span class="peek">{_esc(preview)}</span>'
        f'<span class="size">{chars}</span></summary>'
        f'<pre class="block">{_esc(text)}</pre></details>'
    )


def _plain_html(node: Timed) -> str:
    """Everything that is not a tool call."""
    event = node.event
    data = event.data
    if event.kind == events.TEXT:
        return f'<div class="ev text">{_esc(str(data.get("text") or ""))}</div>'
    if event.kind == events.THINKING:
        text = str(data.get("text") or "")
        return (
            '<details class="ev thinking"><summary>thinking'
            f'<span class="size">{len(text):,} chars</span></summary>'
            f'<pre class="block">{_esc(text)}</pre></details>'
        )
    if event.kind == events.QUESTION:
        blocks = []
        for question in data.get("questions") or []:
            options = "".join(
                f"<li><b>{_esc(str(o.get('label') or ''))}</b>"
                f"<span>{_esc(str(o.get('description') or ''))}</span></li>"
                for o in question.get("options") or []
            )
            blocks.append(
                f'<div class="q"><div class="q-head">{_esc(str(question.get("header") or ""))}</div>'
                f'<div class="q-text">{_esc(str(question.get("question") or ""))}</div>'
                f"<ul>{options}</ul></div>"
            )
        return f'<div class="ev question">{"".join(blocks)}</div>'
    if event.kind == events.ANSWER:
        rows = "".join(
            f'<div class="kv"><span class="k">{_esc(str(q))}</span>'
            f'<span class="v">{_esc(str(a))}</span></div>'
            for q, a in (data.get("answers") or {}).items()
        )
        return f'<div class="ev answer"><div class="who">you answered</div>{rows}</div>'
    if event.kind == events.RESULT:
        bits = [str(data.get("subtype") or "")]
        if data.get("cost_usd"):
            bits.append(_money(data.get("cost_usd")) or "")
        counted = runlog.token_totals(data.get("usage") or {})
        if counted.get("input_tokens") is not None:
            bits.append(
                f"{counted['input_tokens']:,} in "
                f"({counted.get('cached_tokens') or 0:,} cached)"
                f" / {counted.get('output_tokens') or 0:,} out"
            )
        return f'<div class="ev result">ended · {_esc(" · ".join(b for b in bits if b))}</div>'
    if event.kind == events.ERROR:
        return f'<div class="ev error has-error">{_esc(str(data.get("text") or data))}</div>'
    # Anything the stream grows that this page has not been taught yet — shown
    # raw rather than dropped, because a silent omission in an audit tool is the
    # one failure it cannot afford.
    return (
        f'<details class="ev raw"><summary>{_esc(event.kind)}</summary>'
        f'<pre class="block">{_esc(json.dumps(data, indent=2, default=str))}</pre></details>'
    )


# --- shaping the stream -----------------------------------------------------


def _exchanges(transcript: runlog.Transcript) -> list[Exchange]:
    """Group the stream by the message that opened each part of it.

    A log written before `CONVERSATION.md` §5 has no `PROMPT` events at all, so
    everything lands in one unopened exchange — read, never migrated.
    """
    grouped: list[Exchange] = []
    for node in nodes(transcript):
        if isinstance(node, Timed) and node.event.kind == events.PROMPT:
            grouped.append(Exchange(prompt=node.event, at=node.at))
            continue
        if not grouped:
            grouped.append(Exchange())
        grouped[-1].nodes.append(node)
    return grouped


def nodes(transcript: runlog.Transcript) -> list[Any]:
    """Fold results and write confirmations onto the call they belong to.

    Results pair by tool-use id, which the SDK gives us. Approvals carry only a
    tool name — `can_use_tool` fires between the call and its result, so the
    pairing is positional: the most recent call of that name still waiting for
    one. Anything that fails to pair is emitted on its own rather than dropped.

    **Public because `contextpage.py` asks the same question of the same logs**:
    it shows, per tool, what that tool actually handed the model, which needs
    every call paired with its result. Two implementations of this pairing is
    how one page attributes a profile to the call before it and the other does
    not.
    """
    timed = transcript.timed()
    results = {
        e.data.get("id"): (e, at)
        for e, at in timed
        if e.kind == events.TOOL_RESULT and e.data.get("id")
    }
    consumed: set[str] = set()
    nodes: list[Any] = []
    calls: list[Call] = []

    for event, at in timed:
        if event.kind == events.TOOL_CALL:
            found, found_at = results.get(event.data.get("id"), (None, None))
            if found is not None:
                consumed.add(str(event.data.get("id")))
            node = Call(call=event, seq=len(calls), result=found, at=at, result_at=found_at)
            calls.append(node)
            nodes.append(node)
        elif event.kind == events.TOOL_RESULT:
            if str(event.data.get("id")) not in consumed:
                nodes.append(Timed(event, at))
        elif event.kind == events.APPROVAL:
            target = _awaiting(calls, str(event.data.get("name") or ""), gated=False)
            if target is None:
                nodes.append(Timed(event, at))
            else:
                target.approval = event
        elif event.kind == events.APPROVAL_RESULT:
            target = _awaiting(calls, str(event.data.get("name") or ""), gated=True)
            if target is None:
                nodes.append(Timed(event, at))
            else:
                target.allowed = bool(event.data.get("allowed"))
                target.waited = event.data.get("waited")
                target.automatic = bool(event.data.get("auto"))
        else:
            nodes.append(Timed(event, at))
    return nodes


def _awaiting(calls: list[Call], name: str, *, gated: bool) -> Call | None:
    """The latest call of this name that has not been paired with a gate yet."""
    for call in reversed(calls):
        if str(call.call.data.get("name") or "") != name:
            continue
        if gated and call.approval is not None and call.allowed is None:
            return call
        if not gated and call.approval is None:
            return call
    return None


# --- small helpers ----------------------------------------------------------


def _tokens(summary: dict) -> str:
    sent, cached, got = (summary.get(k) for k in ("input_tokens", "cached_tokens", "output_tokens"))
    if sent is None or got is None:
        return ""
    return f"{sent:,} in ({cached or 0:,} cached) / {got:,} out"

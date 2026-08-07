"""Read what the copilot did:
    python -m portia.cli.history list [--kind chat|indexing] [--dir .portia]
    python -m portia.cli.history show [<name>] [--dir .portia] [--json]

No agent, no model spend — this reads chats and indexing jobs that already
happened (`portia/runlog.py`). `list` is the index you scan to find the one you
mean; `show` replays it, which is the transcript you would have scrolled back
through, except that it is still there tomorrow.

**Two kinds, listed apart** (`docs/CONVERSATION.md` §3). A *chat* is a
conversation you had with the copilot; an *indexing* is a job the app ran on
your behalf. They read differently and are worth finding separately, which is
what `--kind` is for. **It is not called `chats`** — that sits one letter from
`cli/chat.py`, which drives the copilot rather than reading it back.

Rendering a replay reuses `cli.chat.render`, so a run reads the way it read
live. What it adds is the half the live terminal drops on purpose: tool results,
questions, answers and write confirmations. Those are what the run is worth
reading for, and `docs/BACKLOG.md` asks for the live view to stay as it was so
transcripts stay comparable to the runs already scored against them.

Both verbs read one project — the one `--dir` points at, defaulting to `.portia`
under the current directory. It takes an absolute path, so another project's
history needs no copying:

    python -m portia.cli.history --dir ~/portia-run7/.portia list
    python -m portia.cli.history show ~/portia-run7/.portia/chats/2026-07-29T16-32-57.jsonl

``show`` checks whether its argument is a file before treating it as a name,
so a log that has been moved is still readable — its header names the model,
effort, prompt, cwd and portia sha, which is what makes it self-describing
enough to read away from the project that produced it.
"""

from __future__ import annotations

import argparse

from portia import runlog
from portia.agent import events
from portia.cli.chat import render as render_live
from portia.core.present import count
from portia.core.serialize import to_json

#: A tool result is a whole profile; a replay is for finding the moment, not
#: reading the evidence in full. `--json` is there when you want all of it.
RESULT_CHARS = 400
PROMPT_CHARS = 60


def main() -> None:
    parser = argparse.ArgumentParser(description="Read the copilot's chats and indexing jobs.")
    parser.add_argument("--dir", default=".portia", help="catalog directory (default: .portia)")
    sub = parser.add_subparsers(dest="command", required=True)

    listing = sub.add_parser("list", help="everything logged in this project, newest first")
    listing.add_argument(
        "--kind", default=None, choices=list(runlog.KINDS), help="only chats, or only indexing"
    )

    show = sub.add_parser("show", help="replay one chat or indexing job")
    show.add_argument("name", nargs="?", default=None, help="name or prefix (default: latest)")
    show.add_argument("--json", action="store_true", help="emit the summary as JSON")

    args = parser.parse_args()
    if args.command == "list":
        list_logs(args.dir, kind=args.kind)
    else:
        show_log(args.name, args.dir, as_json=args.json)


def list_logs(portia_dir: str, *, kind: str | None = None) -> None:
    paths = runlog.logs_in(portia_dir, kind)
    if not paths:
        where = runlog.DIR_FOR_KIND[kind] if kind else "/".join(runlog.DIR_FOR_KIND.values())
        print(f"Nothing logged in {portia_dir}/{where}.")
        return

    rows = [runlog.summary(runlog.read(p)) for p in paths]
    print(_header_line())
    for row in rows:
        print(_list_line(row))


def show_log(name: str | None, portia_dir: str, *, as_json: bool = False) -> None:
    paths = runlog.logs_in(portia_dir)
    if name is None:
        path = paths[0] if paths else None
    else:
        path = runlog.find(name, portia_dir)
    if path is None:
        print(f"Nothing to show{'' if name is None else f': {name}'}.")
        return

    run = runlog.read(path)
    if as_json:
        print(to_json(runlog.summary(run)))
        return

    print(render_summary(runlog.summary(run)))
    print()
    for event in run.events:
        render_replay(event)


# --- rendering --------------------------------------------------------------


def render_summary(summary: dict) -> str:
    """The header of a replay: what this was, and what it did.

    Counts, not a verdict. Nothing here says one went well — see
    `runlog.summary`, and `CLAUDE.md` → facts vs judgment.
    """
    effort = f", effort {summary['effort']}" if summary.get("effort") else ""
    models = summary.get("models") or []
    # Every model it ran on, because a chat can change model mid-way and the one
    # in the brackets is only the first (`runlog.summary`).
    also = f"  (also {', '.join(models[1:])})" if len(models) > 1 else ""
    lines = [
        f"{summary['name']}  [{summary.get('kind')}]  [{summary.get('model')}{effort}]{also}",
        f"  started   {summary.get('started')}   portia {summary.get('portia_sha') or '?'}",
        f"  exchanges {count(summary.get('exchanges') or 0, 'message')}",
        f"  opened    {summary.get('prompt')}",
        f"  tools     {count(summary['tools'], 'call')}"
        + (f"  ({_mix(summary['by_tool'])})" if summary["by_tool"] else "")
        + (f"  {summary['tool_errors']} errored" if summary["tool_errors"] else ""),
        f"  asked     {count(summary['asked'], 'time')}, {count(summary['questions'], 'question')}",
        f"  writes    {count(summary['writes'], 'confirmation')} —"
        f" {summary['approved']} allowed, {summary['refused']} refused",
        f"  ended     {summary.get('subtype')}{_cost(summary)}",
    ]
    return "\n".join(lines)


def render_replay(event: events.Event) -> None:
    """One logged event, on the terminal.

    The kinds the live renderer already handles go through it unchanged, so a
    replay and the thing it replays don't drift into two different-looking things.
    """
    if event.kind == events.PROMPT:
        # Each exchange opens with the human. In a chat of six that is the only
        # thing separating one from the next, so it gets the rule.
        model = event.data.get("model") or ""
        print(f"\n{'─' * 60}\n> {event.data.get('text', '')}  [{model}]")
    elif event.kind == events.TOOL_RESULT:
        mark = "!" if event.data.get("is_error") else "←"
        print(f"  {mark} {_clip(event.data.get('text', ''), RESULT_CHARS)}")
    elif event.kind == events.QUESTION:
        for question in event.data.get("questions") or []:
            print(f"\n  ? {question.get('question')}")
            for option in question.get("options") or []:
                print(f"      - {option.get('label')}")
    elif event.kind == events.ANSWER:
        for question, answer in (event.data.get("answers") or {}).items():
            print(f"  > {_clip(str(question), PROMPT_CHARS)}: {answer}")
    elif event.kind == events.APPROVAL:
        print(f"\n  ! {events.tool_label(str(event.data.get('name')))} wants to write:")
        for key, value in (event.data.get("input") or {}).items():
            if key != "portia_dir":
                print(f"       {key}: {value}")
    elif event.kind == events.APPROVAL_RESULT:
        print(f"     → {'allowed' if event.data.get('allowed') else 'refused'}")
    else:
        render_live(event)


def _header_line() -> str:
    return (
        f"{'name':<21} {'kind':<9} {'model':<22} {'msg':>4} {'tools':>5} {'ask':>4} {'w':>4}"
        f" {'ref':>4} {'cost':>9}  opened with"
    )


def _list_line(summary: dict) -> str:
    effort = f"/{summary['effort']}" if summary.get("effort") else ""
    model = f"{summary.get('model') or '?'}{effort}"
    cost = f"${summary['cost_usd']:.4f}" if summary.get("cost_usd") else "—"
    return (
        f"{summary['name']:<21} {summary.get('kind', ''):<9} {model[:22]:<22} "
        f"{summary.get('exchanges', 0):>4} {summary['tools']:>5} {summary['asked']:>4} "
        f"{summary['approved']:>4} {summary['refused']:>4} {cost:>9}  "
        f"{_clip(summary.get('prompt') or '', PROMPT_CHARS)}"
    )


def _mix(by_tool: dict[str, int]) -> str:
    return ", ".join(f"{name} {n}" for name, n in by_tool.items())


def _cost(summary: dict) -> str:
    """What it spent. The cached share is shown because most of a portia
    exchange's input is the pushed L0/L1 context, and a bare input count reads as
    if it weren't there (`runlog._tokens`)."""
    cost = summary.get("cost_usd")
    sent, cached, got = (summary.get(k) for k in ("input_tokens", "cached_tokens", "output_tokens"))
    parts = []
    if cost:
        parts.append(f"~${cost:.4f}")
    if sent is not None and got is not None:
        cache_note = f", {cached:,} cached" if cached else ""
        parts.append(f"{sent:,} in{cache_note} / {got:,} out")
    return f"   ({', '.join(parts)})" if parts else ""


def _clip(text: str, chars: int) -> str:
    flat = " ".join(str(text).split())
    return flat if len(flat) <= chars else flat[:chars] + "…"


if __name__ == "__main__":
    main()

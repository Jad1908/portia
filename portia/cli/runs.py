"""Read the run log:
    python -m portia.cli.runs list [--dir .portia]
    python -m portia.cli.runs show [<run>] [--dir .portia] [--json]

No agent, no model spend — this reads turns that already happened
(`portia/runlog.py`). `list` is the index you scan to find the one you mean;
`show` replays it, which is the transcript you would have scrolled back
through, except that it is still there tomorrow.

Rendering a replay reuses `cli.chat.render`, so a run reads the way it read
live. What it adds is the half the live terminal drops on purpose: tool results,
questions, answers and write confirmations. Those are what the run is worth
reading for, and `docs/BACKLOG.md` asks for the live view to stay as it was so
transcripts stay comparable to the runs already scored against them.

Both verbs read one project — the one `--dir` points at, defaulting to `.portia`
under the current directory. It takes an absolute path, so another project's
turns need no copying:

    python -m portia.cli.runs --dir ~/portia-run7/.portia list
    python -m portia.cli.runs show ~/portia-run7/.portia/runs/2026-07-29T16-32-57.jsonl

``show`` checks whether its argument is a file before treating it as a run name,
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
    parser = argparse.ArgumentParser(description="Read the copilot's run log.")
    parser.add_argument("--dir", default=".portia", help="catalog directory (default: .portia)")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="every logged turn in this project, newest first")

    show = sub.add_parser("show", help="replay one turn")
    show.add_argument("run", nargs="?", default=None, help="run name or prefix (default: latest)")
    show.add_argument("--json", action="store_true", help="emit the run's summary as JSON")

    args = parser.parse_args()
    if args.command == "list":
        list_runs(args.dir)
    else:
        show_run(args.run, args.dir, as_json=args.json)


def list_runs(portia_dir: str) -> None:
    paths = runlog.runs_in(portia_dir)
    if not paths:
        print(f"No runs logged in {portia_dir}/{runlog.RUNS_DIR}.")
        return

    rows = [runlog.summary(runlog.read(p)) for p in paths]
    print(_header_line())
    for row in rows:
        print(_list_line(row))


def show_run(name: str | None, portia_dir: str, *, as_json: bool = False) -> None:
    paths = runlog.runs_in(portia_dir)
    if name is None:
        path = paths[0] if paths else None
    else:
        path = runlog.find(name, portia_dir)
    if path is None:
        print(f"No such run{'' if name is None else f': {name}'}.")
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
    """The header of a replay: what this run was, and what it did.

    Counts, not a verdict. Nothing here says a run went well — see
    `runlog.summary`, and `CLAUDE.md` → facts vs judgment.
    """
    effort = f", effort {summary['effort']}" if summary.get("effort") else ""
    lines = [
        f"{summary['run']}  [{summary.get('model')}{effort}]",
        f"  started   {summary.get('started')}   portia {summary.get('portia_sha') or '?'}",
        f"  prompt    {summary.get('prompt')}",
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
    replay and the run it replays don't drift into two different-looking things.
    """
    if event.kind == events.TOOL_RESULT:
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
        f"{'run':<21} {'model':<22} {'tools':>5} {'ask':>4} {'w':>4} {'ref':>4} {'cost':>9}  prompt"
    )


def _list_line(summary: dict) -> str:
    effort = f"/{summary['effort']}" if summary.get("effort") else ""
    model = f"{summary.get('model') or '?'}{effort}"
    cost = f"${summary['cost_usd']:.4f}" if summary.get("cost_usd") else "—"
    return (
        f"{summary['run']:<21} {model[:22]:<22} {summary['tools']:>5} {summary['asked']:>4} "
        f"{summary['approved']:>4} {summary['refused']:>4} {cost:>9}  "
        f"{_clip(summary.get('prompt') or '', PROMPT_CHARS)}"
    )


def _mix(by_tool: dict[str, int]) -> str:
    return ", ".join(f"{name} {n}" for name, n in by_tool.items())


def _cost(summary: dict) -> str:
    """What the turn spent. The cached share is shown because most of a portia
    turn's input is the pushed L0/L1 context, and a bare input count reads as if
    it weren't there (`runlog._tokens`)."""
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

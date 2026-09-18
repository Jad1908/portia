"""Talk to the copilot:
    python -m portia.cli.chat interpret <source> [--dir .portia] [--model ...] [--effort ...]
    python -m portia.cli.chat ask "<anything>" [--dir .portia] [--model ...] [--effort ...]

The human edge of the agent loop. Renders the engine's event stream to a
terminal and collects answers from stdin; everything it prints is formatting of
events `portia.agent.events` produced. The three-panel app (docs/VISION.md) will
consume the same stream — this file is one renderer, not the interface.

Requires the agent extra:  uv sync --extra agent
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from typing import Any

from portia import runlog
from portia.agent import events, prompts

# --- rendering (formatting lives at the edge, never in the engine) -----------


def render(event: events.Event) -> None:
    if event.kind == events.PROMPT:
        print(f"\n> {event.data['text']}")
    elif event.kind == events.TEXT:
        print(f"\n{event.data['text']}\n")
    elif event.kind == events.THINKING:
        print("  · thinking…")
    elif event.kind == events.TOOL_CALL:
        name = events.tool_label(event.data["name"])
        detail = event.data.get("input") or {}
        args = ", ".join(f"{k}={v!r}" for k, v in detail.items() if k != "portia_dir")
        print(f"  → {name}({args[:120]})")
    elif event.kind == events.RESULT:
        cost = event.data.get("cost_usd")
        note = f"  (~${cost:.4f})" if cost else ""
        if event.data["subtype"] != "success":
            print(f"\n[ended: {event.data['subtype']}]{note}")
        else:
            print(f"[done]{note}")
    elif event.kind == events.ERROR:
        print(f"\n[error] {event.data.get('message')}")


# --- collecting the human's side --------------------------------------------


def _read(prompt: str) -> str:
    """Prompt for one line, discarding anything typed before we asked.

    Confirmations and questions both arrive mid-stream, so the human is often
    still typing at the previous prompt when the next one appears. Without the
    flush, that keystroke satisfies the *next* `input()`: in one run a `Y` meant
    for a write confirmation was consumed as the answer to a question, and the
    agent — correctly — reported the answer as unusable and re-asked. An answer
    the human didn't give to the question they were asked is worse than no
    answer, so drop the buffer rather than trust it.
    """
    if sys.stdin.isatty():
        try:
            import termios

            termios.tcflush(sys.stdin, termios.TCIFLUSH)
        except (ImportError, OSError):  # non-POSIX, or not a real terminal
            pass
    return input(prompt)


async def answer_questions(questions: list[dict]) -> dict[str, Any]:
    """Render the copilot's questions and collect answers.

    `input()` blocks, so it runs off the event loop — the SDK stream is live
    while we wait.
    """
    answers: dict[str, Any] = {}
    for q in questions:
        print(f"\n  ?  {q.get('header', 'Question')}: {q['question']}")
        options = q.get("options") or []
        for i, opt in enumerate(options, 1):
            print(f"       {i}. {opt['label']} — {opt.get('description', '')}")
        multi = q.get("multiSelect")
        hint = "numbers separated by commas" if multi else "a number"
        reply = await asyncio.to_thread(_read, f"     [{hint}, or type your own] ")
        answers[q["question"]] = _parse(reply.strip(), options)
    return answers


def _parse(reply: str, options: list[dict]) -> Any:
    """A number picks an option; anything else is taken as free text verbatim."""
    labels = []
    for part in reply.split(","):
        part = part.strip()
        if part.isdigit() and 1 <= int(part) <= len(options):
            labels.append(options[int(part) - 1]["label"])
        else:
            return reply  # not a clean selection — treat the whole reply as the answer
    return labels[0] if len(labels) == 1 else labels


async def confirm_write(tool_name: str, tool_input: dict) -> bool:
    name = events.tool_label(tool_name)
    print(f"\n  !  {name} wants to write:")
    for key, value in tool_input.items():
        if key == "portia_dir":
            continue
        print(f"       {key}: {value}")
    reply = await asyncio.to_thread(_read, "     allow? [Y/n] ")
    return reply.strip().lower() in ("", "y", "yes")


# --- entrypoint --------------------------------------------------------------


async def run_and_render(
    prompt: str,
    *,
    model: str,
    effort: str | None = None,
    cwd: str,
    portia_dir: str,
    kind: str = runlog.CHAT,
    provider: str | None = None,
) -> None:
    """Drive one copilot exchange, render its events, and log them.

    The banner is not decoration: the default is deliberately a small model
    (`PLAN.md` → Budget & model discipline) and a run on a flagship costs orders
    of magnitude more. A turn should never be able to spend that silently.

    The log is a tee, and it belongs here rather than in the engine: this is the
    edge, and `agent/events.py` stays a seam rather than becoming a logging
    framework (`portia/runlog.py`). What the terminal *shows* is unchanged — the
    log keeps the events the renderer drops, which is most of the point.
    """
    from portia.agent import session

    provider = provider or session.DEFAULT_PROVIDER
    print(f"  [{provider} · {model}{', effort ' + effort if effort else ''}]")
    log = runlog.start(portia_dir, cwd=cwd, kind=kind)
    print(f"  [logging to {log.path}]")

    async for event in session.run(
        prompt,
        answer=answer_questions,
        confirm=confirm_write,
        model=model,
        effort=effort,
        cwd=cwd,
        portia_dir=portia_dir,
        provider=provider,
    ):
        log.event(event)
        render(event)


def run_turn(
    prompt: str,
    *,
    model: str,
    effort: str | None,
    cwd: str,
    portia_dir: str,
    kind: str = runlog.CHAT,
    provider: str | None = None,
) -> None:
    """One exchange, start to finish. Shared entry point for `chat` and `index`.

    ``kind`` says which history it lands in — `index` passes ``INDEXING``, because
    a job the app ran is not a conversation you had (`CONVERSATION.md` §3).

    Ctrl-C is how a human ends a turn they've seen enough of — an ordinary exit,
    not a crash. Without this it unwinds through the SDK's stream and prints
    forty lines of `anyio` traceback over the transcript they wanted to read.
    """
    try:
        asyncio.run(
            run_and_render(
                prompt,
                model=model,
                effort=effort,
                cwd=cwd,
                portia_dir=portia_dir,
                kind=kind,
                provider=provider,
            )
        )
    except KeyboardInterrupt:
        print("\n[interrupted]")


def main() -> None:
    parser = argparse.ArgumentParser(description="Talk to the portia copilot.")
    parser.add_argument("--dir", default=".portia", help="catalog directory (default: .portia)")
    add_spend_arguments(parser)
    sub = parser.add_subparsers(dest="command", required=True)

    interpret = sub.add_parser("interpret", help="have the copilot read what a source is")
    interpret.add_argument("source", help="name of an indexed source (its file stem)")

    merge = sub.add_parser("merge", help="have the copilot work out a join between two sources")
    merge.add_argument("left", help="name of an indexed source")
    merge.add_argument("right", help="name of an indexed source")
    merge.add_argument("--spec", default=None, help="spec to write to (default: specs/<left>.yaml)")

    freeform = sub.add_parser("ask", help="ask the copilot anything about the project")
    freeform.add_argument("prompt", help="what to ask")

    args = parser.parse_args()

    if args.command == "interpret":
        prompt = prompts.task("interpret", source=args.source)
    elif args.command == "merge":
        prompt = prompts.task(
            "merge",
            left=args.left,
            right=args.right,
            spec=args.spec or f"specs/{args.left}.yaml",
        )
    else:
        prompt = args.prompt
    run_turn(prompt, **spend_from(args), cwd=".", portia_dir=args.dir)


def add_spend_arguments(parser: argparse.ArgumentParser) -> None:
    """``--provider``, ``--model`` and ``--effort``, the same three on every copilot CLI.

    `providers.KINDS` is importable without the SDK, so the provider is checked
    by argparse; the model is free-form (anything the provider serves) and the
    effort is checked by `session.EFFORTS` when the turn starts, because that
    module cannot be imported before the subcommand runs without dragging the
    SDK in.
    """
    from portia.agent import providers

    parser.add_argument(
        "--provider",
        default=None,
        choices=providers.KINDS,
        help=f"where the model comes from (default: {providers.DEFAULT_KIND})",
    )
    parser.add_argument("--model", default=None, help="model to run the copilot on")
    parser.add_argument("--effort", default=None, help="how hard it thinks (low … max)")


def spend_from(args: argparse.Namespace) -> dict:
    """The three spend keywords `run_turn` takes, from parsed arguments.

    The model defaults to the **provider's** default, not portia's one global
    one: ``--provider ollama`` with no ``--model`` should run on a model Ollama
    can serve, not on a Claude name it will refuse.
    """
    from portia.agent import providers

    kind = args.provider or providers.DEFAULT_KIND
    return {
        "provider": kind,
        "model": args.model or providers.get(kind).default_model,
        "effort": args.effort,
    }


if __name__ == "__main__":
    main()

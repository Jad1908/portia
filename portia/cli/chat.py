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
from typing import Any

from portia.agent import events, prompts

# --- rendering (formatting lives at the edge, never in the engine) -----------


def render(event: events.Event) -> None:
    if event.kind == events.TEXT:
        print(f"\n{event.data['text']}\n")
    elif event.kind == events.THINKING:
        print("  · thinking…")
    elif event.kind == events.TOOL_CALL:
        name = event.data["name"].replace("mcp__portia__", "")
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
        reply = await asyncio.to_thread(input, f"     [{hint}, or type your own] ")
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
    name = tool_name.replace("mcp__portia__", "")
    print(f"\n  !  {name} wants to write:")
    for key, value in tool_input.items():
        if key == "portia_dir":
            continue
        print(f"       {key}: {value}")
    reply = await asyncio.to_thread(input, "     allow? [Y/n] ")
    return reply.strip().lower() in ("", "y", "yes")


# --- entrypoint --------------------------------------------------------------


async def run_and_render(
    prompt: str, *, model: str, effort: str | None = None, cwd: str, portia_dir: str
) -> None:
    """Drive one copilot turn and render its events. Shared with `cli.index`.

    The banner is not decoration: the default is deliberately a small model
    (`PLAN.md` → Budget & model discipline) and a run on a flagship costs orders
    of magnitude more. A turn should never be able to spend that silently.
    """
    from portia.agent import session

    print(f"  [{model}{', effort ' + effort if effort else ''}]")

    async for event in session.run(
        prompt,
        answer=answer_questions,
        confirm=confirm_write,
        model=model,
        effort=effort,
        cwd=cwd,
        portia_dir=portia_dir,
    ):
        render(event)


def main() -> None:
    parser = argparse.ArgumentParser(description="Talk to the portia copilot.")
    parser.add_argument("--dir", default=".portia", help="catalog directory (default: .portia)")
    parser.add_argument("--model", default=None, help="model to run the copilot on")
    # No argparse `choices=`: the valid set lives in `session.EFFORTS`, which
    # can't be imported before the subcommand runs without dragging the SDK in.
    parser.add_argument("--effort", default=None, help="how hard it thinks (low … max)")
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

    from portia.agent.session import DEFAULT_MODEL

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
    asyncio.run(
        run_and_render(
            prompt,
            model=args.model or DEFAULT_MODEL,
            effort=args.effort,
            cwd=".",
            portia_dir=args.dir,
        )
    )


if __name__ == "__main__":
    main()

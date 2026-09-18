"""Read a run the way you would audit it:

    python -m devtools.audit sandbox/gui
    python -m devtools.audit sandbox/gui --kind chat -o before-prompt-edit.html
    python -m devtools.audit ~/some-project/.portia/chats/2026-08-08T22-10-52.jsonl

A dev tool for tuning the copilot, not part of the product — see
`devtools/__init__.py` for why it lives outside the package. It renders a
project's chats and indexing jobs into **one self-contained HTML file** and
opens it: no server, no dependencies beyond portia itself, nothing to keep
running.

Self-contained is the point rather than a shortcut. A prompt change is evaluated
by reading the run before it beside the run after it, and `docs/EVALUATION.md`
records what that costs when the evidence is a terminal buffer — run 7's
write-up says outright that only its last 90 lines survived and that a finding
degraded because of it. Two files you can keep, name, and open in two tabs is
the whole idea; each defaults into `devtools/out/`, stamped, so generating one
never overwrites the one you were comparing it with.

It reads through `portia.runlog`, so there is exactly one parser for the log
format and this page cannot drift from what `cli.history` shows.
"""

from __future__ import annotations

import argparse
import sys
import webbrowser
from datetime import datetime
from pathlib import Path

from devtools.page import render_page
from portia import runlog
from portia.catalog import DEFAULT_DIR
from portia.core.present import count

#: Where a page lands when you do not say. Gitignored, and stamped rather than
#: overwritten — the comparison this tool exists for needs the previous one.
OUT_DIR = Path(__file__).parent / "out"

STAMP = "%Y-%m-%dT%H-%M-%S"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Render a project's copilot logs into one HTML page you can audit."
    )
    parser.add_argument(
        "project",
        nargs="?",
        default=".",
        help="a project directory, its .portia/, or a single .jsonl log (default: .)",
    )
    parser.add_argument(
        "--kind", default=None, choices=list(runlog.KINDS), help="only chats, or only indexing"
    )
    parser.add_argument("-o", "--out", default=None, help="where to write the page")
    parser.add_argument(
        "--no-open", action="store_true", help="write the file without opening a browser"
    )
    args = parser.parse_args(argv)

    target = Path(args.project).expanduser()
    try:
        paths = collect(target, kind=args.kind)
    except ValueError as problem:
        print(problem, file=sys.stderr)
        return 2

    if not paths:
        print(f"No logs under {target}.", file=sys.stderr)
        return 1

    transcripts = [runlog.read(path) for path in paths]
    page = render_page(transcripts, title=_title(target), source=str(target))

    out = Path(args.out).expanduser() if args.out else _default_out(target)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page, encoding="utf-8")

    calls = sum(1 for t in transcripts for e in t.events if e.kind == "tool_call")
    print(f"{out}  ({count(len(transcripts), 'log')}, {count(calls, 'tool call')})")
    if not args.no_open:
        webbrowser.open(out.resolve().as_uri())
    return 0


def collect(target: Path, *, kind: str | None = None) -> list[Path]:
    """Every log the argument names, newest first.

    Three things a human might reasonably type, because a path is what you have
    in hand: a project directory, the `.portia/` inside it, or one `.jsonl`
    straight out of `.portia/chats/`. Resolving all three here keeps the caller
    from having to know which shape it holds.
    """
    if target.is_file():
        if target.suffix != ".jsonl":
            raise ValueError(f"{target} is not a .jsonl log.")
        return [target]
    if not target.is_dir():
        raise ValueError(f"No such project or log: {target}")
    portia_dir = target if target.name == DEFAULT_DIR else target / DEFAULT_DIR
    if not portia_dir.is_dir():
        raise ValueError(f"No {DEFAULT_DIR}/ under {target} — is that a portia project?")
    return runlog.logs_in(portia_dir, kind)


def _title(target: Path) -> str:
    name = target.resolve().name
    if name in (DEFAULT_DIR, "chats", "indexing"):
        name = target.resolve().parent.name
    return f"portia audit · {name}"


def _default_out(target: Path) -> Path:
    resolved = target.resolve()
    name = resolved.stem if resolved.is_file() else resolved.name
    return OUT_DIR / f"{name}-{datetime.now().strftime(STAMP)}.html"


if __name__ == "__main__":
    raise SystemExit(main())

"""`python -m devtools.traces [roots…]`: start the viewer and open it."""

from __future__ import annotations

import argparse
import sys
import webbrowser
from pathlib import Path

from devtools.traces.logs import DEFAULT_ROOTS, Library, default_claude_home
from devtools.traces.notes import Notes, default_path
from devtools.traces.server import Viewer, serve
from portia.core.ports import is_free

HOST = "127.0.0.1"
DEFAULT_PORT = 8747

#: How many ports past the default to try before giving up.
PORT_TRIES = 20


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read portia and Claude Code logs, one step at a time."
    )
    parser.add_argument(
        "roots",
        nargs="*",
        type=Path,
        help=f"folders to look for logs under, or single .jsonl files (default: {DEFAULT_ROOTS[0]})",
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--notes", type=Path, default=None, help=f"notes file (default: {default_path()})"
    )
    parser.add_argument(
        "--claude-home",
        type=Path,
        default=None,
        help=f"Claude Code's session folders (default: {default_claude_home()})",
    )
    parser.add_argument("--no-open", action="store_true", help="do not open a browser")
    args = parser.parse_args(argv)

    port = next((p for p in range(args.port, args.port + PORT_TRIES) if is_free(HOST, p)), None)
    if port is None:
        print(f"No free port from {args.port} to {args.port + PORT_TRIES - 1}.", file=sys.stderr)
        return 1

    library = Library(args.roots or None, args.claude_home)
    notes = Notes(args.notes)
    server = serve(Viewer(library, notes), HOST, port)
    url = f"http://{HOST}:{port}/"
    print(f"Traces at {url}  (notes in {notes.path})  Ctrl-C to stop.")
    if not args.no_open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

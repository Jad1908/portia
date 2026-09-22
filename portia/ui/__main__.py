"""Launch the window:  python -m portia.ui [--project DIR] [--port 8080]

``--project`` is a convenience for a repeat run, not a requirement: with no
arguments the app opens on its own project picker, which is what the no-terminal
bar asks for (docs/VISION.md).
"""

from __future__ import annotations

import argparse
import socket

from nicegui import app as nicegui_app
from nicegui import ui

from portia.ui import app, theme
from portia.ui.state import APP

DEFAULT_PORT = 8080
#: How many ports above the default are tried before giving up. 8080 is the
#: most contested development port there is; twenty in a row being taken means
#: something else is wrong, and saying so beats walking the whole range.
PORT_TRIES = 20


def is_free(host: str, port: int) -> bool:
    """Whether the window could listen here, found by binding and letting go."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind((host, port))
        except OSError:
            return False
    return True


def pick_port(host: str, asked: int | None) -> int:
    """The port to serve on.

    **A port somebody asked for is theirs or it is refused**: they have a
    bookmark, a tunnel or a forwarding rule pointing at it, and serving
    somewhere else would be the app disagreeing with them quietly. With nothing
    asked, the default is a preference and the next free port above it does as
    well, which is the first thing a tester on Windows with WSL ran into.
    """
    if asked is not None:
        if not is_free(host, asked):
            raise ValueError(f"port {asked} on {host} is already in use; pass another --port")
        return asked
    for port in range(DEFAULT_PORT, DEFAULT_PORT + PORT_TRIES):
        if is_free(host, port):
            return port
    last = DEFAULT_PORT + PORT_TRIES - 1
    raise ValueError(f"no free port from {DEFAULT_PORT} to {last} on {host}; pass one with --port")


async def _close_chats() -> None:
    """Close any chat the window is holding, on the way out.

    Defined above `main` and not below it: everything under the
    ``if __name__`` block runs *after* `main` has been called, so a handler
    registered from inside `main` and defined down there does not exist yet.
    """
    from portia.agent import drawn
    from portia.agent.providers import llamacpp
    from portia.ui import exchange

    await exchange.close_all()
    # A host reading `.portia/window.json` is told nobody is watching any more.
    drawn.withdraw(APP.catalog_dir)
    # And the local server this window started (`PROVIDERS.md` §4.9): ten
    # gigabytes of model must not outlive the app that loaded them.
    llamacpp.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description="Open the portia app.")
    parser.add_argument("--project", default=None, help="project directory to open on start")
    parser.add_argument("--dir", default=APP.portia_dir, help="catalog directory in the project")
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help=f"port to serve on (default: {DEFAULT_PORT}, or the next free one above it)",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--no-show", action="store_true", help="don't open a browser")
    args = parser.parse_args()

    try:
        port = pick_port(args.host, args.port)
    except ValueError as refusal:
        raise SystemExit(str(refusal)) from None
    # NiceGUI's own welcome is switched off below, so this is the only line
    # that says where the window is, and a browser does not always open by
    # itself (`--no-show`, WSL, a remote shell).
    print(f"portia is at http://{args.host}:{port}", flush=True)

    APP.portia_dir = args.dir
    APP.url = f"http://{args.host}:{args.port}"
    if args.project:
        app.open_at_start(args.project)

    # A chat holds a live SDK subprocess (`docs/CONVERSATION.md` §4). Closing
    # the window is the last way out of a project, so it is the last place one
    # can be closed on purpose rather than left to the process dying.
    nicegui_app.on_shutdown(_close_chats)

    ui.run(
        host=args.host,
        port=port,
        title=app.TITLE,
        favicon=theme.LOGO_FILE,
        # Auto: Quasar resolves it from prefers-color-scheme, and the toolbar's
        # override rides the same mechanism (portia/ui/theme.py).
        dark=None,
        show=not args.no_show,
        reload=False,
        show_welcome_message=False,
        # The copilot's prose is markdown; the transcript renders it as such.
        markdown=True,
        # **The margin, not the fix.** `agent/tools.py` and `ui/engine.py` keep
        # the blocking work off the loop; this covers what threading cannot,
        # which is that DuckDB holds the GIL in bursts *inside* a worker — a
        # 72s build of the demo project stalled the loop 1.16s at its worst
        # while correctly threaded. NiceGUI's default is 3.0s, near enough to
        # that to lose the race on a slower machine, and the failure it produces
        # is the worst kind of lie the window can tell: a "trying to reconnect"
        # card over a server that is fine and working. Ten seconds is still well
        # inside the time a human waits before suspecting the app has died.
        reconnect_timeout=10.0,
    )


if __name__ == "__main__":
    main()

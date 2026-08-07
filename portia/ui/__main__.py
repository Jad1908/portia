"""Launch the window:  python -m portia.ui [--project DIR] [--port 8080]

``--project`` is a convenience for a repeat run, not a requirement: with no
arguments the app opens on its own project picker, which is what the no-terminal
bar asks for (docs/VISION.md).
"""

from __future__ import annotations

import argparse

from nicegui import app as nicegui_app
from nicegui import ui

from portia.ui import app, theme
from portia.ui.state import APP

DEFAULT_PORT = 8080


async def _close_chats() -> None:
    """Close any chat the window is holding, on the way out.

    Defined above `main` and not below it: everything under the
    ``if __name__`` block runs *after* `main` has been called, so a handler
    registered from inside `main` and defined down there does not exist yet.
    """
    from portia.ui import exchange

    await exchange.close_all()


def main() -> None:
    parser = argparse.ArgumentParser(description="Open the portia app.")
    parser.add_argument("--project", default=None, help="project directory to open on start")
    parser.add_argument("--dir", default=APP.portia_dir, help="catalog directory in the project")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--no-show", action="store_true", help="don't open a browser")
    args = parser.parse_args()

    APP.portia_dir = args.dir
    if args.project:
        app.open_at_start(args.project)

    # A chat holds a live SDK subprocess (`docs/CONVERSATION.md` §4). Closing
    # the window is the last way out of a project, so it is the last place one
    # can be closed on purpose rather than left to the process dying.
    nicegui_app.on_shutdown(_close_chats)

    ui.run(
        host=args.host,
        port=args.port,
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
    )


if __name__ == "__main__":
    main()

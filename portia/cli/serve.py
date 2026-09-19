"""portia's tools as a server of their own, for a host that is not portia.

`agent/tools.py` builds the MCP server **in-process**, for a loop portia drives
through the Agent SDK. This is the same server over stdin and stdout, for a loop
somebody else drives: Claude Code starts `portia-mcp` in a project folder and
gets the tools the app's copilot has, with the same descriptions and the same
handlers behind them. :func:`build` reuses `create_sdk_mcp_server` for
exactly that reason. The tool list is identical by construction, and a test
pins it.

What the app does *around* the tools is what a host does not do, and this module
is where each piece of it is put back:

- **The chat log.** `runlog` is teed at the edges (`cli/chat.run_and_render`,
  `ui/exchange`), and with a foreign host there is no edge of ours in the
  conversation. There is one around each tool call, so every call and its result
  is written here, in the shapes `agent/events.py` gives them. `findings.review`
  copies SQL and results out of that file verbatim; with no log the journal
  returns nothing and says nothing, which is the failure this exists to stop.
  The log holds calls only. The host's prose, its tokens and its prompt are the
  host's, and the header says which host it was (`runlog.HOSTED`).
- **Which chat is this one.** `review_queries` and `record_finding` default to
  the newest chat, which is right when one process holds one conversation. With
  the window open beside a host, or two hosts on one project, the newest log is
  not necessarily ours. This process knows its own, so it names it.
- **Charts.** `plot_data` publishes its rows to whatever surface is listening,
  and in this process nothing is. They are stashed under `.portia/drawn/`, a
  window open on the project shows them as the unsaved charts they are, the
  receipt tells the model whether such a window exists, and a render that
  failed there comes back on the next receipt (`agent/drawn.py`).
- **Signing in to a warehouse.** The app has a dialog for it. Here the session
  opens on the first call that needs it, the brief says so before a browser
  window surprises anybody, and a connection that wants a password typed is
  refused in words a model can act on (:func:`signing_in`, :func:`_readable`).
- **Stop.** The host cancels a request; MCP delivers that as a cancelled task,
  and a cancelled `await` does nothing to the thread a DuckDB query is on. Each
  call runs under its own `core.cancel` scope, which is what Run and Build use,
  and the scope is cancelled when the request is.

The log is opened on the first call and not at start-up: a host starts every
server it is configured with, in every session, and a chat list full of
sessions that never touched portia is a list nobody reads.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import os
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from portia import catalog, runlog
from portia.agent import drawn, events, prompts
from portia.core import cancel

#: The hosts this server has been driven from. One today; the header carries the
#: name so a reader of the log knows whose prose is missing from it.
CLAUDE_CODE = "claude-code"

#: Tools whose ``chat`` argument means *which log*, and defaults to this one.
_CHAT_SCOPED = ("review_queries", "record_finding")

#: The tool whose receipt carries a window's render failures back.
_PLOT_TOOL = "plot_data"

Handler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


class Session:
    """One host session: its log, opened on first use, and the calls written to it."""

    def __init__(self, portia_dir: str = catalog.DEFAULT_DIR, *, host: str = CLAUDE_CODE) -> None:
        self.portia_dir = portia_dir
        self.host = host
        self._log: runlog.Log | None = None

    @property
    def log(self) -> runlog.Log:
        if self._log is None:
            self._log = runlog.start(
                self.portia_dir, cwd=".", host=self.host, prompts=_prompts_read()
            )
        return self._log

    def wrap(self, name: str, handler: Handler) -> Handler:
        """``handler`` with the call logged either side of it and Stop wired in."""
        from portia.agent import tools

        async def logged(args: dict[str, Any]) -> dict[str, Any]:
            call_id = uuid.uuid4().hex
            self.log.event(
                events.Event(
                    events.TOOL_CALL,
                    {"name": tools.qualified(name), "input": args, "id": call_id},
                )
            )
            follow_the_project(self.portia_dir)
            if name == _PLOT_TOOL:
                drawn.collect_failures(self.portia_dir)
            # Where the catalog is was settled when this process started. See
            # `_without_the_catalog_argument` for what a host's model did with
            # the chance to say otherwise.
            sent = {**args, _CATALOG_ARG: self.portia_dir}
            if name in _CHAT_SCOPED and not sent.get("chat"):
                sent["chat"] = self.log.path.stem
            try:
                result = _readable(await _stoppable(handler, sent))
            except asyncio.CancelledError:
                self._result(call_id, prompts.error("tool_stopped"), failed=True)
                raise
            self._result(
                call_id,
                events.tool_result_text(result.get("content")),
                bool(result.get("is_error")),
            )
            return result

        return logged

    def _result(self, call_id: str, text: str, failed: bool) -> None:
        self.log.event(
            events.Event(events.TOOL_RESULT, {"id": call_id, "text": text, "is_error": failed})
        )


async def _stoppable(handler: Handler, args: dict[str, Any]) -> dict[str, Any]:
    """Run one call under a cancel scope of its own, cancelled when the request is.

    **A scope per call and the ambient `ContextVar`, not `tools.stopping`.** The
    app has one exchange at a time and installs one scope for it in a module
    slot, because the SDK runs tool bodies on a task that never sees the caller's
    context. Here the handler is awaited from this task, so the context reaches
    `asyncio.to_thread` intact, and a host may run several read-only calls at
    once, which one slot cannot hold.

    **Shielded, so the scope outlives the await.** `interrupt` is edge-triggered
    (`core/cancel.py`): closing the scope the moment the request is cancelled
    loses the race against a query that has not started yet. The inner task runs
    on until its thread ends, which after a cancel is one interrupt interval, and
    only then is the scope closed.
    """
    scope = cancel.Scope()
    with cancel.scope(scope):
        task = asyncio.ensure_future(handler(args))
    try:
        result = await asyncio.shield(task)
    except asyncio.CancelledError:
        scope.cancel()
        task.add_done_callback(lambda _: scope.close())
        raise
    scope.close()
    return result


def _offered() -> list[Any]:
    """The tools a host is offered: all of them but the one that needs the window's memory.

    `view_chart` hands the copilot the browser's own picture of a tab, and
    `agent/drawn` holds that picture **in memory and nowhere else**, on purpose:
    nothing is saved. In the app the tool and the window are one process. Here
    the window, if there is one, is another, so the tool could only ever refuse,
    and a tool that always refuses is a turn spent learning that. `tools.offered`
    already has the list without it, for a model that cannot take an image in;
    the reason differs and the list is the same.
    """
    from portia.agent import tools

    return tools.offered(sees_images=False)


def _prompts_read() -> dict[str, Any]:
    """What this host's model is given by portia: the instructions and the tools.

    Not `runlog.prompts_read`, which composes the app's system prompt. A host
    writes its own, and recording ours beside a chat it never reached would be
    the log saying something false about what was read.
    """
    from portia.agent import tools

    return {
        "system": prompts.load("headless/instructions"),
        "tools": {
            **tools.descriptions(sees_images=False),
            BRIEF_TOOL: prompts.load("headless/get_context"),
        },
    }


def build(session: Session) -> Any:
    """The MCP server: the tools a host is offered, each handler wrapped by ``session``."""
    from claude_agent_sdk import create_sdk_mcp_server

    from portia.agent import tools

    wrapped = [
        dataclasses.replace(
            tool,
            handler=session.wrap(tool.name, tool.handler),
            input_schema=_without_the_catalog_argument(tool.input_schema),
        )
        for tool in (_pushed_brief(t, session.portia_dir) for t in _offered())
    ]
    server = create_sdk_mcp_server(name=tools.SERVER_NAME, version="0.1.0", tools=wrapped)[
        "instance"
    ]
    # A chart has no window to go to in this process, so it goes to disk, where
    # a window on the project picks it up (`agent/drawn.py`), and the receipt
    # says whether one is open.
    drawn.subscribe(drawn.to_disk(session.portia_dir))
    drawn.set_audience(lambda: drawn.watching(session.portia_dir))
    # Read by the host when it connects and kept in its context for the whole
    # session, which a skill is not: a skill is fetched when the model thinks to.
    server.instructions = prompts.load("headless/instructions")
    return server


#: The argument every tool takes for where the catalog is.
_CATALOG_ARG = "portia_dir"


def _without_the_catalog_argument(schema: Any) -> Any:
    """A tool's input schema with ``portia_dir`` taken out of what the host is offered.

    **Found by the first real drive, 2026-09-19.** Every tool takes an optional
    ``portia_dir`` and the app's copilot has never filled it. A host's model
    knows its working directory, and Haiku passed ``"portia_dir": "."`` on every
    call, helpfully and wrongly: the catalog was read from the project root,
    where there is none, the brief said *nothing is indexed* about a project
    with two sources, and the model went to the shell to index them again. For
    this process the catalog's place is a fact settled at start-up, so it is not
    a question a host is asked, and `Session.wrap` overwrites it if it is
    answered anyway.
    """
    if not isinstance(schema, dict) or _CATALOG_ARG not in (schema.get("properties") or {}):
        return schema
    properties = {k: v for k, v in schema["properties"].items() if k != _CATALOG_ARG}
    required = [k for k in schema.get("required") or [] if k != _CATALOG_ARG]
    return {**schema, "properties": properties, "required": required}


#: The one tool a host is offered differently from the app's copilot.
BRIEF_TOOL = "get_context"


def _pushed_brief(tool: Any, portia_dir: str) -> Any:
    """`get_context`, returning the brief the app would have put in the system prompt.

    In the app L0 and L1 are **pushed**: composed into the system prompt, because
    a tool the agent may call is one it will sometimes skip (`CLAUDE.md`). A host
    composes its own system prompt, so here the brief can only be pulled, and two
    things change to make the pull as close to a push as it gets. The description
    stops saying *you already have this*, which would be false, and says to call
    it first. And the answer is `context.build_brief` itself, the text the app's
    copilot reads, and not the handler's dict: one rendering of the brief, so the
    two copilots cannot be told different things about one project.
    """
    if tool.name != BRIEF_TOOL:
        return tool
    from portia.agent import context

    async def brief(args: dict[str, Any]) -> dict[str, Any]:
        text = await asyncio.to_thread(context.build_brief, portia_dir)
        if signing := signing_in():
            text = f"{text}\n\n{signing}"
        return {"content": [{"type": "text", "text": text}]}

    return dataclasses.replace(
        tool, handler=brief, description=prompts.load("headless/get_context")
    )


def _warehouse() -> Any | None:
    """The pool behind this process's warehouse, or ``None`` on files."""
    from portia.connectors import pool
    from portia.core import backend

    active = backend.active()
    return pool.pool_of(active) if active.remote else None


def signing_in() -> str:
    """What a host's model is told about signing in to the warehouse. ``""`` on files.

    **The one thing in a host's brief the app's does not say**, because in the
    app it is the window's business: the connect dialog asks for a password, and
    a browser sign-in is started by a person pressing a button. Here the session
    opens on the first tool call that needs it (`pool.Pool.handle`), so a
    browser window appears on the user's screen because of something the model
    did, and they should have been told. And a connection that wants a password
    typed cannot open at all, which is worth saying before the first refusal
    rather than after it (`docs/HEADLESS.md` §7).

    Nothing once a session is open: by then none of it is news.
    """
    from portia.connectors import registry

    for label, why in _unusable.items():
        return prompts.load("headless/signin/missing").format(label=label, why=why)
    held = _warehouse()
    if held is None or held.connected:
        return ""
    connection = held.connection
    if held.needs_secret:
        what = (connection.secret_label or "secret").lower()
        return prompts.load("headless/signin/typed").format(label=connection.name, what=what)
    if connection.auth == registry.BROWSER:
        return prompts.load("headless/signin/browser").format(label=connection.name)
    if connection.auth == registry.FILE:
        return prompts.load("headless/signin/file").format(label=connection.name)
    return prompts.load("headless/signin/quiet").format(label=connection.name)


def _readable(result: dict[str, Any]) -> dict[str, Any]:
    """A result a host's model can act on, where the app's wording assumed a window.

    `pool.SecretRequired` says *enter it to connect*, which is right where a
    dialog is about to ask and an instruction nobody can follow here. It
    reaches this edge already turned into text by `tools._failed`, so it is
    recognised by the exception's name, which `_failed` always puts first.
    """
    from portia.connectors import pool

    held = _warehouse()
    text = events.tool_result_text(result.get("content"))
    if held is None or not text.startswith(f"{pool.SecretRequired.__name__}:"):
        return result
    what = (held.connection.secret_label or "secret").lower()
    said = prompts.error("warehouse_needs_typing", label=held.connection.name, what=what)
    return {"content": [{"type": "text", "text": said}], "is_error": True}


def follow_the_project(portia_dir: str) -> None:
    """Re-read which connection the project names, and switch if it changed.

    A host's session usually *starts* before the project has a connection: the
    model sets one up with `connect use` halfway through, in another process,
    and this one would go on answering from DuckDB until somebody restarted it.
    The window never had the problem because its own button makes the change.
    One small YAML read per call, against a query on a warehouse.
    """
    from portia.connectors import registry
    from portia.core import backend

    try:
        named = catalog.project_settings(portia_dir)["connection"]
    except Exception:  # noqa: BLE001 - an unreadable project file changes nothing
        return
    active = backend.active()
    if named in _unusable and named not in registry.names():
        return  # already tried and still not there; the brief says why
    if (active.label if active.remote else None) != named:
        activate(portia_dir)


#: Why the connection the project names could not be installed, by name, or
#: empty. Kept so the brief can say it: a project that quietly fell back to
#: DuckDB answers *no such source* about tables that are in its own catalog.
_unusable: dict[str, str] = {}


def activate(portia_dir: str) -> None:
    """Name the project's warehouse for this process, and open nothing.

    What the window does on opening a project (`ui/engine.open_project`), for the
    same reason: a session may open a browser for SSO, and a server started
    because a host was started must not. A project naming a connection this
    machine does not have still serves, on files, and :func:`signing_in` says so.
    """
    from portia import connectors

    _unusable.clear()
    connectors.deactivate()
    try:
        connectors.activate(portia_dir, connect=False)
    except Exception as exc:  # noqa: BLE001 - said in the brief, and the server still serves
        named = catalog.project_settings(portia_dir).get("connection") or ""
        _unusable[str(named)] = str(exc)


async def serve(portia_dir: str) -> None:
    from mcp.server.stdio import stdio_server

    server = build(Session(portia_dir))
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Serve portia's tools over stdio, for Claude Code or any MCP host."
    )
    parser.add_argument(
        "--project",
        default=os.environ.get("CLAUDE_PROJECT_DIR") or ".",
        help="the project folder (default: the host's project, else here)",
    )
    parser.add_argument("--dir", default=catalog.DEFAULT_DIR, help="catalog directory")
    args = parser.parse_args()
    # Every handler resolves `.portia`, `specs/` and `findings/` against the
    # working directory, as they do under `cli/chat`. One `chdir` here is that
    # rule kept, where a project argument on seventeen tools would be a second.
    os.chdir(Path(args.project).expanduser())
    activate(args.dir)
    asyncio.run(serve(args.dir))


if __name__ == "__main__":
    main()

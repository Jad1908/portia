"""Portia's tools on a loopback port, for a harness that is not in this process.

`agent/tools.build_server` builds the MCP server **in-process** and the Claude
Agent SDK bridges it to its binary over the SDK's own control channel. Codex
has no such bridge: its ``app-server`` connects to MCP servers by a command or
by a URL (`docs/PROVIDERS.md` §9.1). A command would be `cli/serve.py`'s stdio
server in a second process, and then the question tool, the write gate, the
cancel scope and the chart that reaches the window would all be in the wrong
process. So the same server object is served over streamable HTTP on
``127.0.0.1``, on a port the OS picks, for the life of one conversation, and
every tool body still runs here, on the loop that owns the window.

One `mcp` package serves both: `create_sdk_mcp_server` wraps a plain
``mcp.server.Server``, and ``StreamableHTTPSessionManager`` takes one. The
transport's rebinding guard is kept and told the loopback names, because a
listener with no allowed hosts refuses every request.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from typing import Any

HOST = "127.0.0.1"
PATH = "/mcp"


@dataclass
class Served:
    """A running loopback server: where it is, and how to stop it."""

    url: str
    _server: Any
    _task: asyncio.Task
    _manager: Any

    async def close(self) -> None:
        self._server.should_exit = True
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await asyncio.wait_for(self._task, timeout=5)


async def serve(instance: Any) -> Served:
    """Serve ``instance`` (an ``mcp.server.Server``) over HTTP on a free loopback port."""
    import uvicorn
    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
    from mcp.server.transport_security import TransportSecuritySettings
    from starlette.applications import Starlette
    from starlette.routing import Mount

    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[f"{HOST}:*", "localhost:*"],
        allowed_origins=[f"http://{HOST}:*", "http://localhost:*"],
    )
    manager = StreamableHTTPSessionManager(app=instance, stateless=True, security_settings=security)

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette):
        async with manager.run():
            yield

    app = Starlette(routes=[Mount(PATH, app=manager.handle_request)], lifespan=lifespan)
    config = uvicorn.Config(app, host=HOST, port=0, log_level="warning", lifespan="on")
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    while not server.started:
        if task.done():
            task.result()
            raise RuntimeError("the loopback server stopped before it started")
        await asyncio.sleep(0.01)
    port = server.servers[0].sockets[0].getsockname()[1]
    return Served(f"http://{HOST}:{port}{PATH}", server, task, manager)

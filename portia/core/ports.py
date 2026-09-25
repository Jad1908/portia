"""One way to ask whether a port on this machine is free.

Two things in portia listen on a port it chooses: the window (`ui/__main__`)
and the llama.cpp server it can start (`agent/providers/llamacpp`). Each had
its own test for *free*, and they disagreed: the window bound the port and let
go, the server asked for ``/health`` and so saw only something that speaks
HTTP. A database, an SSH tunnel or a second copy of anything that is not a web
server passed the second test, and the server then loaded gigabytes of model
before failing to listen.

**Free means both: it can be bound, and nothing accepts a connection on it.**
Binding alone is not enough on macOS, where a socket may bind ``127.0.0.1:N``
while another program holds ``*:N``, and the two then split the traffic.

**The bind sets ``SO_REUSEADDR``, as the server that will listen there does**
*(2026-09-25)*. Without it, a port whose last server closed less than about
thirty seconds ago still has connections in ``TIME_WAIT``, and the bind fails
though nothing listens: restarting portia straight after closing it refused
``--port 8190`` as in use, and the default search moved the window to the next
port up. uvicorn binds with the flag and would have been fine. A real listener
is still found, by the connection half.
"""

from __future__ import annotations

import socket
from collections.abc import Collection

LOOPBACK = "127.0.0.1"
#: How long a connection attempt waits. Local: an answer takes microseconds,
#: and a refusal is immediate.
CONNECT_TIMEOUT = 0.2


def is_free(host: str, port: int) -> bool:
    """Whether something could listen on ``host:port`` now, found by trying."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((host, port))
        except OSError:
            return False
    return not _accepts(host, port)


def _accepts(host: str, port: int) -> bool:
    target = LOOPBACK if host in ("", "0.0.0.0") else host
    try:
        with socket.create_connection((target, port), timeout=CONNECT_TIMEOUT):
            return True
    except OSError:
        return False


def next_free(host: str, start: int, *, tries: int, skip: Collection[int] = ()) -> int | None:
    """The first free port from ``start`` up, passing over ``skip``; ``None`` when none is."""
    for port in range(start, min(start + tries, 65536)):
        if port not in skip and is_free(host, port):
            return port
    return None

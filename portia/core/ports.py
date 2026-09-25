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

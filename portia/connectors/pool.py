"""One session per backend, opened once and handed out as siblings.

The half of a connector that is the same for every warehouse
(`docs/CONNECTORS.md` §3). Opening a session may open a browser or run a
credential flow, so a `Pool` opens it **once** when the project is activated,
`Backend.open` hands out siblings over it, and the thing typed to open it (a
password, a token) is held here **in memory** so a session that drops can be
reopened without asking again, and nowhere else.

A provider supplies one function: how its session is opened from a
`registry.Connection` and an optional secret. Everything else — the lock, the
secret, the backend value and the map from a backend back to its pool — lives
here once, so the second connector could not drift from the first on any of
it.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

from portia.connectors.registry import Connection
from portia.core import dialect as dialects
from portia.core.backend import Backend


class InFlight:
    """What every handle on one session shares: what is running, and when we last cancelled.

    `core/cancel.py` calls ``interrupt`` every 50 ms once Stop is pressed
    (`CONNECTORS.md` §2.10), and each call to a warehouse is a round trip, so
    an adapter fires only while a statement is in flight (`busy`) and at most
    once per `CANCEL_EVERY` (`due`). ``handles`` is whatever the adapter needs
    to cancel with — a BigQuery job, say; Snowflake cancels by session and
    passes nothing.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._running: list[Any] = []
        self._last_cancel = 0.0
        self.closed = False

    def running(self, handle: Any = None):
        return _Running(self, handle)

    @property
    def busy(self) -> bool:
        with self._lock:
            return bool(self._running)

    @property
    def handles(self) -> list[Any]:
        with self._lock:
            return [h for h in self._running if h is not None]

    def due(self) -> bool:
        with self._lock:
            now = time.monotonic()
            if now - self._last_cancel < CANCEL_EVERY:
                return False
            self._last_cancel = now
            return True

    def _enter(self, handle: Any) -> None:
        with self._lock:
            self._running.append(handle)

    def _exit(self, handle: Any) -> None:
        with self._lock:
            self._running.remove(handle)


class _Running:
    def __init__(self, shared: InFlight, handle: Any) -> None:
        self._shared, self._handle = shared, handle

    def __enter__(self) -> None:
        self._shared._enter(self._handle)

    def __exit__(self, *_: object) -> None:
        self._shared._exit(self._handle)


#: How often ``interrupt`` will actually send a cancel while the scope keeps
#: asking. A round trip per 50 ms would be the watchdog costing more than the
#: query it is stopping.
CANCEL_EVERY = 1.0


def split(qualified: str) -> tuple[str, str, str]:
    """``DATABASE.SCHEMA.TABLE`` as three parts, refusing anything else.

    Portia always qualifies fully and always with three levels, whatever the
    warehouse calls them (`CONNECTORS.md` §2.5): the connection's default
    database and schema are conveniences for the picker, never something a
    name relies on.
    """
    parts = qualified.split(".")
    if len(parts) != 3 or not all(parts):
        raise ValueError(f"expected DATABASE.SCHEMA.TABLE, got {qualified!r}")
    return parts[0], parts[1], parts[2]


class SecretRequired(ValueError):
    """The connection signs in with something typed, and nothing was given.

    Raised **before** the driver is asked, so a project that names such a
    connection opens without a prompt and the window asks at the moment it
    connects (`ui/artifacts.connect_in_background`). Nothing here stores it.
    """


#: How a provider opens its session: ``(connection, secret) -> session``.
Opener = Callable[[Connection, "str | None"], Any]


class Pool:
    """One session, opened once, handed out as siblings (`Backend.open`)."""

    def __init__(self, connection: Connection, opener: Opener) -> None:
        self.connection = connection
        self._open = opener
        self._session: Any = None
        self._secret: str | None = None
        self._lock = threading.Lock()

    @property
    def connected(self) -> bool:
        return self._session is not None and not self._session.closed

    def connect(self, secret: str | None = None) -> Any:
        """Open the session — a browser may open. Idempotent while it is alive.

        ``secret`` is the password or token for a connection that needs one.
        It is kept **on this pool, in memory**, so a session that drops can be
        reopened without asking again, and nowhere else.
        """
        with self._lock:
            if self.connected:
                return self._session
            if secret:
                self._secret = secret
            if self.connection.needs_secret and not self._secret:
                what = (self.connection.secret_label or "secret").lower()
                raise SecretRequired(
                    f"{self.connection.name} signs in with a {what}; enter it to connect"
                )
            self._session = self._open(self.connection, self._secret)
            return self._session

    @property
    def needs_secret(self) -> bool:
        return self.connection.needs_secret and not self._secret

    def handle(self) -> Any:
        """A handle for one piece of work, connecting first if nobody has."""
        return self.connect().cursor()

    def close(self) -> None:
        with self._lock:
            if self._session is not None:
                self._session.close()
                self._session = None


_POOLS: dict[int, Pool] = {}


def build(
    pool: Pool, *, kind: str, dialect: dialects.Dialect, agent_writes: bool = False
) -> Backend:
    """A remote `Backend` over ``pool``, remembered so `pool_of` can find it again."""
    built = Backend(
        kind=kind,
        dialect=dialect,
        open=pool.handle,
        remote=True,
        agent_writes=agent_writes,
        opens_on=opens_on(pool.connection),
        label=pool.connection.name,
    )
    _POOLS[id(built)] = pool
    return built


def rewrite(built: Backend, *, agent_writes: bool) -> Backend:
    """The same backend, same pool, with the hand-off set as asked.

    A `Backend` is a frozen value and the flag rides on it, so a new value is a
    new backend — but the session behind it was opened by a browser login and
    must not be dropped for a settings edit.
    """
    pool = pool_of(built)
    return build(pool, kind=built.kind, dialect=built.dialect, agent_writes=agent_writes)


def pool_of(built: Backend) -> Pool:
    """The pool behind a backend a connector made."""
    try:
        return _POOLS[id(built)]
    except KeyError:
        raise ValueError(f"{built.kind!r} backend was not made by a connector") from None


def opens_on(connection: Connection) -> str | None:
    """``DATABASE`` or ``DATABASE.SCHEMA`` off the registry entry, or nothing.

    Prose only: the brief offers it as the place to start building, and
    nothing writes there because of it (`CONNECTOR.md` §2.7.1). A provider
    that calls its levels something else maps them here through the same two
    field names, which is §2.5's rule applied to a form.
    """
    first, second = connection.provider.levels
    database = connection.fields.get(first)
    if not database:
        return None
    schema = connection.fields.get(second)
    return database + (f".{schema}" if schema else "")

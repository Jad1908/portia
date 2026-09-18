"""Stopping work that has already started.

A build is on a worker thread, and **a thread cannot be cancelled** —
`asyncio.to_thread` hands the awaiting task its control back and leaves the
thread running to completion, so a Stop button built on task cancellation would
free the window and leave a 47-second query chewing a core behind it. What can
actually be stopped is the *database*: `DuckDBPyConnection.interrupt` is safe to
call from another thread and raises inside whatever query that connection is
running, measured at ~10ms to take effect.

So stopping is two things, and it needs both:

- **Interrupt** whatever is executing now, which ends the wait.
- **A flag**, checked between steps, which stops the *next* one from starting.
  Without it an interrupt during the gap between two queries is simply missed,
  and the build carries on into the step you pressed Stop to avoid.

**Why the scope is ambient.** The connection running the long query is not the
one the caller has a handle on: `ops/sql.apply_sql` opens its own sandbox with
external access off (deliberately — it is the agent's SQL hatch), and that is
exactly where the expensive query runs. Threading a token down through every op
and check to reach it would put a cancellation parameter on the signature of
code that has nothing to do with cancellation. A `ContextVar` reaches it without
that, and it reaches it correctly across the thread hop: `asyncio.to_thread`
copies the current context into the worker, so a scope installed on the loop is
visible to everything the worker calls.

Nothing here is a timeout and nothing polls. A scope does something only when a
human presses Stop.
"""

from __future__ import annotations

import contextlib
import threading
from collections.abc import Iterator
from contextvars import ContextVar
from typing import Any


class Cancelled(RuntimeError):
    """The work was stopped on purpose.

    **Not a failure**, and surfaces must not draw it as one: it is the answer to
    a button the human pressed, so a stack trace or a red banner would be the app
    apologising for doing what it was told.
    """


#: How often a cancelled scope re-interrupts what it is watching. See
#: :meth:`Scope.cancel` for why this is a loop and not a single call. Small
#: enough that a press feels immediate, and it only ever runs after one.
INTERRUPT_EVERY = 0.05


class Scope:
    """One cancellable piece of work, and every connection it is running on.

    Connections register themselves as they are made (`core/io.connect`) or for
    the length of one query (`ops/sql.apply_sql`). :meth:`cancel` interrupts all
    of them, because which one is executing at the moment of the press is not
    knowable from here — and interrupting an idle connection is a no-op, so
    asking all of them costs nothing and misses nothing.

    **`interrupt` is edge-triggered, and that is the whole difficulty.** It
    raises inside a query that is *running now*; against an idle connection it
    does nothing at all, and the next query to start runs to completion
    unbothered. A single call at the moment of the press therefore loses a race
    it will usually lose: the gap between two steps is exactly when a human
    reacts to the step name that just appeared. Measured on the demo project,
    a Stop pressed as `events_nearby_hotels` was announced took **45 seconds** to
    take effect, because the press landed a few milliseconds before the query it
    was meant to kill had started.

    So a cancelled scope keeps interrupting, every `INTERRUPT_EVERY`, until it is
    :meth:`close`\\ d. That makes the guarantee level-triggered rather than
    edge-triggered: once Stop is pressed, nothing on a watched connection runs
    for longer than one interval, whenever it starts.
    """

    def __init__(self) -> None:
        self._cancelled = False
        self._connections: list[Any] = []
        # Held across `cancel` and `watch` both: the presser is on the event loop
        # and the registrar is on the worker, so this list genuinely has two
        # threads on it.
        self._lock = threading.Lock()
        self._closed = threading.Event()
        self._watchdog: threading.Thread | None = None

    @property
    def cancelled(self) -> bool:
        with self._lock:
            return self._cancelled

    def cancel(self) -> None:
        """Stop this work. Safe to call from any thread, and safe to call twice.

        Pressing Stop on a run that has already finished is an ordinary race —
        the button is drawn from state that is one refresh old — so it has to be
        harmless rather than an error.
        """
        with self._lock:
            already = self._cancelled
            self._cancelled = True
        self._interrupt_all()
        if not already and not self._closed.is_set():
            self._watchdog = threading.Thread(
                target=self._keep_interrupting, name="portia-cancel", daemon=True
            )
            self._watchdog.start()

    def close(self) -> None:
        """The work is over — stop interrupting.

        Called by whoever made the scope, in a `finally`. Without it the watchdog
        would go on interrupting connections that the next run might reuse.
        """
        self._closed.set()

    def _keep_interrupting(self) -> None:
        """Re-interrupt until closed. See the class docstring for why this loops."""
        while not self._closed.wait(INTERRUPT_EVERY):
            self._interrupt_all()

    def _interrupt_all(self) -> None:
        with self._lock:
            connections = list(self._connections)
        for con in connections:
            # A connection closed since it registered raises here. It is already
            # not running our query, which is all we wanted.
            with contextlib.suppress(Exception):
                con.interrupt()

    def watch(self, con: Any) -> None:
        """Interrupt ``con`` too, when this scope is cancelled.

        Registration is one-way and the list dies with the scope, which lives for
        one run. Unregistering would buy nothing: the cost is a reference per
        connection for a few seconds, and the bug it would introduce — a query
        that is running but no longer watched — is the one thing this file exists
        to prevent.

        A connection registering *after* the press is interrupted immediately, so
        a step that opens a sandbox a millisecond too late does not get to run.
        """
        with self._lock:
            self._connections.append(con)
            already = self._cancelled
        if already:
            with contextlib.suppress(Exception):
                con.interrupt()

    def check(self) -> None:
        """Raise :class:`Cancelled` if Stop has been pressed. The cooperative half."""
        if self.cancelled:
            raise Cancelled("stopped")


#: The scope the current work belongs to, or ``None`` when nothing can be
#: stopped — which is every call from the CLI and from the agent's tools.
CURRENT: ContextVar[Scope | None] = ContextVar("portia_cancel_scope", default=None)


@contextlib.contextmanager
def scope(active: Scope | None) -> Iterator[Scope | None]:
    """Install ``active`` for the duration, so nested code can find it.

    ``None`` installs nothing and every helper below becomes a no-op, which is
    what keeps this invisible to the terminal: `cli/build.py` passes no scope and
    pays one `ContextVar.get` per step for the privilege.

    **What comes out of a cancelled scope is `Cancelled`, whatever went in.** An
    interrupted query raises DuckDB's `InterruptException`, which is true and
    useless: shown to the operator it reads as a database error in a build they
    stopped on purpose. Once this scope is cancelled, an exception escaping it is
    a *consequence* of the press, so it is reported as one. Translating here
    rather than at each call site is what lets `ui/engine.execute` tell a stop
    from a failure with one `except`.
    """
    if active is None:
        yield None
        return
    token = CURRENT.set(active)
    try:
        yield active
    except Cancelled:
        raise
    except Exception as exc:
        if active.cancelled:
            raise Cancelled("stopped") from exc
        raise
    finally:
        CURRENT.reset(token)


def watch(con: Any) -> None:
    """Register ``con`` with the ambient scope, if there is one."""
    active = CURRENT.get()
    if active is not None:
        active.watch(con)


def check() -> None:
    """Raise :class:`Cancelled` if the ambient scope has been cancelled."""
    active = CURRENT.get()
    if active is not None:
        active.check()

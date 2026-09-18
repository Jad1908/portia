"""Stopping work that has already started.

The test that earns its keep is
:func:`test_a_query_that_starts_after_the_press_is_still_stopped`. `interrupt` is
edge-triggered — it raises inside a query running *now* and does nothing to an
idle connection — so the obvious implementation (interrupt once, at the press)
loses the race it will usually face: a human presses Stop when they see the step
name appear, which is a few milliseconds before that step's query starts. On the
demo project that cost 45 seconds of build after the press, and the whole of
`core/cancel.py`'s watchdog exists to close it.
"""

from __future__ import annotations

import threading
import time

import duckdb
import pytest

from portia.core import cancel

#: A query long enough to still be running when another thread reaches it.
_SLOW = "select count(*) from range(20000000000) t(i) where i % 7 = 0"

#: Comfortably more than `INTERRUPT_EVERY`, and far less than `_SLOW` takes.
_SETTLE = 1.0


@pytest.fixture
def scope():
    """A scope that is always closed, so no watchdog outlives its test."""
    active = cancel.Scope()
    try:
        yield active
    finally:
        active.close()


def test_a_fresh_scope_is_not_cancelled(scope):
    assert scope.cancelled is False
    scope.check()  # does not raise


def test_check_raises_once_stop_has_been_pressed(scope):
    scope.cancel()

    assert scope.cancelled is True
    with pytest.raises(cancel.Cancelled):
        scope.check()


def test_cancelling_twice_is_harmless(scope):
    """Pressing Stop on a run that has already finished is an ordinary race —
    the button is drawn from state one refresh old."""
    scope.cancel()
    scope.cancel()

    assert scope.cancelled is True


def test_a_running_query_is_interrupted(scope):
    con = duckdb.connect(":memory:")
    scope.watch(con)
    threading.Thread(target=lambda: (time.sleep(0.3), scope.cancel()), daemon=True).start()

    started = time.monotonic()
    with pytest.raises(duckdb.InterruptException):
        con.execute(_SLOW).fetchall()

    assert time.monotonic() - started < 10, "the interrupt did not land"


def test_a_query_that_starts_after_the_press_is_still_stopped(scope):
    """**The regression.** `interrupt` against an idle connection does nothing.

    Pressed in the gap between two steps — which is exactly when a human reacts
    to the step name that just appeared — a single interrupt is a no-op and the
    next query runs to completion. Measured at 45 seconds of unwanted build on
    the demo project before the watchdog existed.
    """
    con = duckdb.connect(":memory:")
    scope.watch(con)

    scope.cancel()  # nothing is running yet, so this interrupt hits nothing
    time.sleep(_SETTLE)  # and the press is well in the past by the time we start

    started = time.monotonic()
    with pytest.raises(duckdb.InterruptException):
        con.execute(_SLOW).fetchall()

    assert time.monotonic() - started < 10, "a query begun after the press ran unimpeded"


def test_a_connection_registered_after_the_press_is_interrupted_too(scope):
    """A step that opens its sandbox a moment too late does not get to run."""
    scope.cancel()
    con = duckdb.connect(":memory:")
    scope.watch(con)

    with pytest.raises(duckdb.InterruptException):
        con.execute(_SLOW).fetchall()


def test_closing_stops_the_interrupting(scope):
    """The watchdog must not outlive its run and interrupt the next one."""
    con = duckdb.connect(":memory:")
    scope.watch(con)
    scope.cancel()
    scope.close()
    time.sleep(_SETTLE)

    # Short enough to finish inside one interval had anything still been firing.
    assert con.execute("select 42").fetchall() == [(42,)]


def test_a_closed_scope_does_not_leave_a_thread_behind(scope):
    before = threading.active_count()
    scope.cancel()
    scope.close()
    deadline = time.monotonic() + 5
    while threading.active_count() > before and time.monotonic() < deadline:
        time.sleep(0.05)

    assert threading.active_count() == before


# --- the ambient scope -------------------------------------------------------


def test_without_a_scope_every_helper_is_a_no_op():
    """What the CLI and the agent's tools get: no scope, and nothing to pay."""
    with cancel.scope(None):
        cancel.check()  # does not raise
        cancel.watch(duckdb.connect(":memory:"))  # does not explode

    assert cancel.CURRENT.get() is None


def test_the_ambient_scope_is_found_by_nested_code(scope):
    with cancel.scope(scope):
        scope.cancel()
        with pytest.raises(cancel.Cancelled):
            cancel.check()


def test_the_ambient_scope_is_restored_afterwards(scope):
    with cancel.scope(scope):
        assert cancel.CURRENT.get() is scope
    assert cancel.CURRENT.get() is None


def test_an_interrupted_query_is_reported_as_a_stop_not_a_database_error(scope):
    """`InterruptException` is true and useless: shown to the operator it reads
    as a database failure in a build they stopped on purpose."""
    with pytest.raises(cancel.Cancelled):
        with cancel.scope(scope):
            scope.cancel()
            raise duckdb.InterruptException("Interrupted!")


def test_a_real_failure_during_an_uncancelled_scope_is_left_alone(scope):
    """Translating unconditionally would bury every build error under 'stopped'."""
    with pytest.raises(ValueError, match="the join failed"):
        with cancel.scope(scope):
            raise ValueError("the join failed")

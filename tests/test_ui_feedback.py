"""The window's half of a report: where an error is kept, and what the form writes."""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("nicegui")

from portia.core import cancel, feedback  # noqa: E402
from portia.ui import engine, state  # noqa: E402
from portia.ui import feedback as panel  # noqa: E402


@pytest.fixture(autouse=True)
def _nothing_remembered():
    feedback.forget()
    yield
    feedback.forget()


def _run(app: state.App, monkeypatch, raises: Exception) -> None:
    async def build(*args, **kwargs):
        raise raises

    monkeypatch.setattr(engine, "build", build)
    asyncio.run(engine.execute(app))


def test_a_failed_run_keeps_the_error_for_the_button_under_it(monkeypatch):
    app = state.App()

    _run(app, monkeypatch, ValueError("no such column READING_ID"))

    assert app.run_error == "ValueError: no such column READING_ID"
    assert app.run_problem is feedback.latest()
    assert app.run_problem.where == "run"


def test_a_stopped_run_leaves_nothing_to_report(monkeypatch):
    app = state.App()

    _run(app, monkeypatch, cancel.Cancelled("stopped"))

    assert app.run_error is None and app.run_problem is None
    assert feedback.latest() is None


def test_the_next_run_clears_the_last_runs_problem(monkeypatch):
    app = state.App()
    _run(app, monkeypatch, ValueError("first"))

    async def build(*args, **kwargs):
        return []

    monkeypatch.setattr(engine, "build", build)
    asyncio.run(engine.execute(app))

    assert app.run_problem is None


def test_the_form_writes_the_error_only_while_its_switch_is_on(monkeypatch):
    try:
        raise KeyError("READING_ID")
    except KeyError as caught:
        problem = feedback.remember(caught, "chat")
    monkeypatch.setattr(panel, "_FORM", panel._Form(problem=problem, include=False))

    assert "KeyError" not in panel._written()

    panel._FORM.include = True
    assert "KeyError" in panel._written()
    assert "Error during chat" in panel._written()


def test_neither_route_is_the_primary_action():
    """Public or private is the user's call, so the fill that marks *the* action
    goes on neither (`DESIGN.md`: at most one primary per view, and here none)."""
    import inspect

    assert 'kind="primary"' not in inspect.getsource(panel._panel.func)


def test_the_email_route_is_drawn_only_while_there_is_an_address():
    """An empty `feedback.EMAIL` means no private route, and a button that opens
    a message to nobody is worse than no button."""
    import inspect

    source = inspect.getsource(panel._panel.func)
    assert source.count("if feedback.EMAIL:") == 2  # the button, and the address in words


def test_the_address_shipped_is_one_a_mail_link_can_be_built_for():
    url = feedback.mail_url("It broke", "said", "written")

    assert feedback.EMAIL and " " not in feedback.EMAIL and feedback.EMAIL.count("@") == 1
    assert url.startswith(f"mailto:{feedback.EMAIL}?subject=%5Bportia%5D%20It%20broke&body=")

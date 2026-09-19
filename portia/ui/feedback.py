"""Report a problem — the window's edge of `core/feedback`.

One dialog, reached two ways. From a **failure on screen** (a failed run, a
failed exchange) it opens with that error already in the report. From
**Settings** it opens with nothing, and offers the last remembered error with
its switch off, because somebody who came in through Settings may be writing
about something else.

**The window sends nothing.** Each button hands the text to something the user
then submits themselves: GitHub's issue page in a new tab, or their mail
program. So the two routes are drawn as equals and named for the one thing
that separates them, who can read the report afterwards. Neither is the
primary action: only the user knows what is in their text.

What portia wrote is in a box the user can edit, and that is the privacy
mechanism, not a courtesy (`core/feedback`'s docstring). It computes nothing:
every line in that box comes from `feedback.details`.

Built once at page level, like every dialog here (`screens.build_add_dialog`).
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from nicegui import ui

from portia.core import backend, feedback
from portia.ui import components as c
from portia.ui.state import APP

TITLE = "Report a problem"
OPEN = "Report a problem"
OPEN_THIS = "Report this"
SAID_LABEL = "What happened"
SAID_HINT = "What you were doing, and what you expected instead."
WRITTEN_LABEL = "Sent with it"
INCLUDE = "Include the {kind} from {at:%H:%M}, during {where}"
NOTHING_SENT = "portia sends nothing. Each button opens a page or a message for you to submit."
ON_GITHUB = "Post on GitHub"
ON_GITHUB_TIP = "Public. Anyone can read it, and it needs a GitHub account."
BY_EMAIL = "Email"
BY_EMAIL_TIP = "Private. Opens your mail program."
COPY = "Copy"
COPIED = "Copied."
CLOSE = "Close"
NO_DIALOG = "The report panel did not load. Reload the page."
#: Said when the link could not carry the details (`feedback.issue_url`).
TRIMMED = "The details were too long for the link. They are copied: paste them into the issue."
#: Said beside the address, for the reader whose mail is a browser tab and for
#: whom a `mailto:` link opens nothing.
ADDRESS = "Or write to {address}"

#: What the backend's kind is called in a report.
_LOCAL = "local files (duckdb)"

_DIALOG: ui.dialog | None = None


@dataclass
class _Form:
    """What the dialog holds while it is open. Page state, like `settings._TAB`."""

    said: str = ""
    written: str = ""
    #: The error this report may carry, and whether it does.
    problem: feedback.Problem | None = None
    include: bool = False
    notice: str = ""


_FORM = _Form()


def build_dialog() -> None:
    """Create the panel. **Called once per page, never from a pane.**"""
    global _DIALOG
    with ui.dialog().props("transition-duration=0") as dialog:
        _panel()
    _DIALOG = dialog


def open_dialog(problem: feedback.Problem | None = None) -> None:
    """Show it, on ``problem`` when the press came from a failure on screen.

    With no ``problem`` the last remembered one is *offered*, switched off.
    """
    if _DIALOG is None or _DIALOG.is_deleted:
        ui.notify(NO_DIALOG)
        return
    _FORM.said = ""
    _FORM.notice = ""
    _FORM.problem = problem or feedback.latest()
    _FORM.include = problem is not None
    _FORM.written = _written()
    _panel.refresh()
    _DIALOG.open()


def report_button(problem: feedback.Problem | None) -> None:
    """The way in from a failure: drawn beside the error it would report."""
    c.button(OPEN_THIS, lambda: open_dialog(problem), icon="outlined_flag", micro=True)


def _written() -> str:
    """What portia adds to the report, for the state the form is in."""
    kind = backend.active().kind
    facts = feedback.environment(
        provider=APP.provider,
        model=APP.model,
        backend=_LOCAL if kind == backend.LOCAL.kind else kind,
    )
    return feedback.details(facts, _FORM.problem if _FORM.include else None, root=APP.root)


@ui.refreshable
def _panel() -> None:
    with ui.element("div").classes("p-panel p-panel--prose"):
        with ui.element("div").classes("p-panel-head"):
            ui.label(TITLE).classes("t-heading-md")
        with ui.element("div").classes("p-panel-body"):
            with ui.element("div").classes("field"):
                ui.label(SAID_LABEL).classes("field-label")
                said = ui.textarea(placeholder=SAID_HINT).classes("p-field p-editor w-full")
                said.props("borderless autogrow autofocus").bind_value(_FORM, "said")
            if _FORM.problem is not None:
                problem = _FORM.problem
                switch = ui.switch(
                    INCLUDE.format(kind=problem.kind, at=problem.at, where=problem.where)
                ).classes("p-toggle")
                switch.value = _FORM.include
                switch.on_value_change(lambda e: _set_include(bool(e.value)))
            with ui.element("div").classes("field"):
                ui.label(WRITTEN_LABEL).classes("field-label")
                written = ui.textarea().classes("p-field p-editor p-field-mono w-full")
                written.props("borderless autogrow").bind_value(_FORM, "written")
            c.caption(NOTHING_SENT)
            if _FORM.notice:
                c.alert(_FORM.notice, kind="info")
        with ui.element("div").classes("p-panel-actions"):
            with ui.element("div").classes("row-gap-sm"):
                c.button(ON_GITHUB, _post, kind="secondary", icon="public").tooltip(ON_GITHUB_TIP)
                if feedback.EMAIL:
                    c.button(BY_EMAIL, _mail, kind="secondary", icon="mail").tooltip(BY_EMAIL_TIP)
                c.button(COPY, _copy, kind="secondary", icon="content_copy")
                ui.element("div").classes("flex-1")
                c.button(CLOSE, _close, kind="secondary")
            if feedback.EMAIL:
                c.caption(ADDRESS.format(address=feedback.EMAIL))


def _set_include(on: bool) -> None:
    """The switch moved: rewrite what portia wrote, and keep what the user typed."""
    _FORM.include = on
    _FORM.written = _written()
    _panel.refresh()


def _heading() -> str:
    return feedback.title(_FORM.said, _FORM.problem if _FORM.include else None)


def _post() -> None:
    url, trimmed = feedback.issue_url(_heading(), _FORM.said, _FORM.written)
    if trimmed:
        ui.clipboard.write(_FORM.written)
        _FORM.notice = TRIMMED
        _panel.refresh()
    ui.navigate.to(url, new_tab=True)


def _mail() -> None:
    """Hand the message to the mail program, as a pressed link would.

    An anchor clicked from script and not `ui.navigate.to`: that one assigns the
    window's location, and a `mailto:` there makes some browsers fire
    `beforeunload`, which is NiceGUI's cue to drop the socket.
    """
    url = feedback.mail_url(_heading(), _FORM.said, _FORM.written)
    ui.run_javascript(
        f"const a = document.createElement('a'); a.href = {json.dumps(url)}; a.click();"
    )


def _copy() -> None:
    ui.clipboard.write(feedback.compose(_FORM.said, _FORM.written))
    ui.notify(COPIED)


def _close() -> None:
    if _DIALOG is not None and not _DIALOG.is_deleted:
        _DIALOG.close()

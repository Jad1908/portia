"""One way to tell us something broke: what a report holds, and where it goes.

portia sends nothing anywhere, and this module keeps it that way. It composes
a report as **text the user reads and edits**, and turns that text into a link:
a prefilled GitHub issue, or a prefilled email. The browser or the mail program
sends it, after the user pressed the button that says so. Same rule as
`ollama pull` in the model picker: shown, never run.

**What a report may hold is an allowlist**, because nearly everything portia
knows at the moment of a failure carries somebody's confidential names. The
chat log has their SQL and their results, a path has their employer in it, and
a DuckDB `CatalogException` names the table. So a report is: what the user
typed, the environment (`environment`), and at most one remembered error
(`Problem`). Never the chat, never a query, never a result, never the project's
path. The one field that can still carry a name is the error's own message,
which is why the whole text sits in an editable box before it goes.

**Errors are remembered in memory and nowhere else** (`remember`): the last
`KEPT`, gone when the process ends. A file of errors would be a file of table
names on somebody's disk that nothing prunes. `Cancelled` is never remembered:
a stop is not a failure (`core/cancel.py`).

No NiceGUI and no engine in here, so a terminal edge can offer the same report.
"""

from __future__ import annotations

import platform
import re
import sys
import traceback
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from functools import cache
from importlib import metadata
from pathlib import Path
from urllib.parse import quote, urlencode

from portia.core import cancel

#: Where a public report goes. The issue form is GitHub's own; portia fills two
#: query parameters and never calls the API.
ISSUES_URL = "https://github.com/Jad1908/portia/issues/new"

#: Where a private report goes: a forwarding alias, never somebody's own
#: address, because this line is public the moment it is committed and an alias
#: can be switched off once the scrapers find it. **Empty means there is no
#: private route**, and the window then draws no email button rather than one
#: that goes nowhere. An installed copy keeps the address it shipped with, so
#: changing this strands every older build's Email button.
EMAIL = "portia.feedback.math706@slmail.me"

#: How many errors are remembered. A report carries one; the rest are there so
#: the error the user means is still around after the one that followed it.
KEPT = 5

#: GitHub refuses a URL much past 8,000 characters. Percent-encoding triples a
#: newline, so the budget is on the encoded link, not on the text.
URL_BUDGET = 6000

#: What the link's body says where the details did not fit. The text is still
#: whole in the window, and Copy takes all of it.
TRIMMED = "(The details were too long for a link. Paste them from the app with Copy.)"

#: A title when the user typed nothing. An issue with no title cannot be filed.
UNTITLED = "Report from the app"
TITLE_LENGTH = 72

#: The packages whose version explains a bug, when they are installed.
PACKAGES = ("duckdb", "nicegui", "claude-agent-sdk", "sqlglot")

_PACKAGE_ROOT = Path(__file__).resolve().parent.parent

#: Where DuckDB starts quoting the failed query back (`_said_by`).
_QUOTED_QUERY = re.compile(r"^LINE \d+:", re.MULTILINE)


@dataclass(frozen=True)
class Problem:
    """One remembered error: when, doing what, and how it travelled through portia."""

    at: datetime
    where: str
    kind: str
    message: str
    frames: tuple[str, ...]

    def headline(self) -> str:
        return f"{self.kind}: {self.message}" if self.message else self.kind


_RECENT: deque[Problem] = deque(maxlen=KEPT)


def remember(exc: BaseException, where: str) -> Problem | None:
    """Keep ``exc`` for a report the user may send. Returns what was kept.

    ``where`` is what the user was doing, in the app's own word (*run*, *chat*).
    Called at the places the window already catches an error to draw it, and
    from the window's uncaught-exception hook. It never raises: a reporter that
    can fail inside an ``except`` replaces the error somebody needs to read.
    """
    if isinstance(exc, cancel.Cancelled):
        return None
    try:
        problem = Problem(
            at=datetime.now(),
            where=where,
            kind=type(exc).__name__,
            message=_said_by(exc),
            frames=_frames(exc),
        )
    except Exception:  # noqa: BLE001 — see the docstring
        return None
    _RECENT.append(problem)
    return problem


def _said_by(exc: BaseException) -> str:
    """The error's message, without the query DuckDB quotes back under it.

    A DuckDB error ends on ``LINE 1: SELECT …`` with a caret: the user's own
    SQL, which the allowlist keeps out. The sentence above it says what went
    wrong and stays.
    """
    message = str(exc)
    quoted = _QUOTED_QUERY.search(message)
    return (message[: quoted.start()] if quoted else message).strip()


def recent() -> list[Problem]:
    """What is remembered, newest first."""
    return list(reversed(_RECENT))


def latest() -> Problem | None:
    return _RECENT[-1] if _RECENT else None


def forget() -> None:
    _RECENT.clear()


def _frames(exc: BaseException) -> tuple[str, ...]:
    """The traceback as portia's own lines, with everything else as a package name.

    A full path holds a user name and often an employer; a frame inside portia
    is the same on every machine, so it is written relative to the package. A
    frame outside it is reduced to which package it was in, and a run of those
    is one line: what somebody fixing the bug needs is where portia handed over
    and to what.
    """
    lines: list[str] = []
    for frame in traceback.extract_tb(exc.__traceback__):
        path = Path(frame.filename)
        if path.resolve().is_relative_to(_PACKAGE_ROOT):
            inside = path.resolve().relative_to(_PACKAGE_ROOT.parent)
            line = f"{inside.as_posix()}:{frame.lineno} in {frame.name}"
        else:
            line = f"({_package_of(path)})"
        if not lines or lines[-1] != line:
            lines.append(line)
    return tuple(lines)


def _package_of(path: Path) -> str:
    """Which installed package a frame was in. Never a path: see `_frames`."""
    parts = path.parts
    if "site-packages" in parts[:-1]:
        return parts[parts.index("site-packages") + 1].removesuffix(".py")
    return "outside portia"


@cache
def version() -> str:
    """The installed version, and the checkout's sha beside it when there is one.

    Cached: the sha is a `git` subprocess, and this is read while a pane is
    drawn. Neither half can change under a running process.
    """
    try:
        number = metadata.version("portia")
    except metadata.PackageNotFoundError:
        number = "not installed"
    from portia import runlog  # lazily: it pulls the agent's event types in

    sha = runlog.portia_sha()
    return f"{number} ({sha})" if sha else number


def environment(*, provider: str = "", model: str = "", backend: str = "") -> dict[str, str]:
    """What a report says about the machine. Every value here is safe to publish.

    ``platform.platform()`` is the system and its version and holds no host
    name. The provider, the model and the backend are the caller's, because
    they are the window's state and this module has none.
    """
    facts = {
        "portia": version(),
        "python": sys.version.split()[0],
        "os": platform.platform(),
    }
    if provider or model:
        facts["model"] = " / ".join(part for part in (provider, model) if part)
    if backend:
        facts["data"] = backend
    packages = [f"{name} {found}" for name in PACKAGES if (found := _installed(name))]
    if packages:
        facts["packages"] = ", ".join(packages)
    return facts


def _installed(name: str) -> str:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return ""


def scrub(text: str, *, root: Path | None = None) -> str:
    """Take the two paths out of ``text`` that say who and where the user is.

    The project's folder becomes ``<project>`` and the home directory ``~``.
    Nothing else is touched: a table name in a message cannot be told from any
    other word, which is what the editable box is for.
    """
    if root is not None and str(root) not in ("", ".", "/"):
        text = text.replace(str(root.resolve()), "<project>").replace(str(root), "<project>")
    return text.replace(str(Path.home()), "~")


def details(
    facts: dict[str, str], problem: Problem | None = None, *, root: Path | None = None
) -> str:
    """The part of a report portia writes: the environment, then the error if any."""
    lines = [f"- {name}: {value}" for name, value in facts.items()]
    if problem is not None:
        lines += [
            "",
            f"Error during {problem.where}, {problem.at:%Y-%m-%d %H:%M}:",
            "",
            "```",
            scrub(problem.headline(), root=root),
            *problem.frames,
            "```",
        ]
    return "\n".join(lines)


def compose(said: str, written: str) -> str:
    """The whole report: what the user said, then what portia wrote under it."""
    said = said.strip()
    written = written.strip()
    return "\n\n".join(part for part in (said, written) if part)


def title(said: str, problem: Problem | None = None) -> str:
    """The user's first line, cut to a title. The error's kind when they said nothing."""
    first = next((line.strip() for line in said.splitlines() if line.strip()), "")
    if not first:
        first = f"{problem.kind} during {problem.where}" if problem else UNTITLED
    return first if len(first) <= TITLE_LENGTH else first[: TITLE_LENGTH - 1].rstrip() + "…"


def issue_url(heading: str, said: str, written: str) -> tuple[str, bool]:
    """A prefilled GitHub issue, and whether the details had to be left off it.

    The link is built whole first. If it runs past `URL_BUDGET` the details are
    swapped for `TRIMMED`, and the caller says so: a report that arrives with
    its error silently missing is the five-questions email all over again.
    """
    url = _issue(heading, compose(said, written))
    if len(url) <= URL_BUDGET:
        return url, False
    return _issue(heading, compose(said, TRIMMED)), True


def _issue(heading: str, body: str) -> str:
    return f"{ISSUES_URL}?{urlencode({'title': heading, 'body': body})}"


def mail_url(heading: str, said: str, written: str, *, address: str = "") -> str:
    """A prefilled email to `EMAIL`, or ``""`` when there is no address to send to.

    ``quote`` and not ``urlencode``: a mail program reads a ``+`` as a plus.
    """
    address = address or EMAIL
    if not address:
        return ""
    subject = quote(f"[portia] {heading}")
    return f"mailto:{address}?subject={subject}&body={quote(compose(said, written))}"

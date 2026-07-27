"""What the app is currently looking at. No engine calls, no NiceGUI import.

Deliberately dumb: paths, selections, and the rows the transcript has collected.
Anything that *measures* something belongs to the engine (`checks`/`ops`/`spec`)
and arrives here already computed — see docs/VISION.md, "No computation in the
UI, ever".

There is one ``APP`` per process, not per browser tab. The app opens one project
directory and changes the process working directory to it (the engine resolves
spec and source paths relative to cwd, exactly as the CLI does), so a second tab
showing a second project could not be honest about which one it was writing to.
Two tabs on one project is the intended case, and they share this state.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from portia.core.present import PREVIEW_ROWS

#: How much of a table a preview shows — one number for every surface, so
#: "showing 15 of 40" means the same thing in the app and in a saved report.
__all__ = ["PREVIEW_ROWS", "APP", "App", "Decision", "Turn", "SOURCE", "SPEC", "OUTPUT", "RUN"]

#: What a left-pane selection can be. ``None`` means the workflow is in view.
SOURCE = "source"
SPEC = "spec"
OUTPUT = "output"
RUN = "run"


@dataclass
class Decision:
    """A moment the loop stopped for the human — a question, or a write.

    The UI both *renders* these and *resolves* them, so it holds the future the
    engine's ``answer``/``confirm`` callback is waiting on. That is the whole
    reason V0 drives rather than views (docs/VISION.md).
    """

    kind: str  # events.QUESTION or events.APPROVAL
    payload: dict[str, Any]
    future: asyncio.Future
    resolved: bool = False
    #: The answers dict for a question; True/False for a write confirmation.
    outcome: Any = None
    #: What the human has typed or picked so far, per question. Kept on the
    #: decision rather than in a widget so an event arriving mid-answer redraws
    #: the form without throwing away a half-written objection.
    draft: dict[str, Any] = field(default_factory=dict)

    def resolve(self, outcome: Any) -> None:
        self.resolved = True
        self.outcome = outcome
        if not self.future.done():
            self.future.set_result(outcome)


@dataclass
class Turn:
    """One copilot turn: what it was asked, what it is spending, how it ended."""

    prompt: str
    model: str
    effort: str | None
    running: bool = True
    subtype: str | None = None
    cost_usd: float | None = None
    error: str | None = None

    @property
    def ended(self) -> bool:
        return not self.running


@dataclass
class App:
    """The single open project and everything the panes read."""

    root: Path = field(default_factory=Path.cwd)
    portia_dir: str = ".portia"
    #: Whether a directory has actually been chosen. ``root`` has a value from
    #: the start, so it can't answer this on its own.
    opened: bool = False

    catalog: dict = field(default_factory=dict)
    spec_path: Path | None = None
    spec: dict | None = None
    results: list | None = None  # list[spec.StepResult] once a run has happened
    run_error: str | None = None
    outputs: list[Path] = field(default_factory=list)

    selection: tuple[str, str] | None = None  # (kind, name) — None = the workflow
    selected_step: str | None = None
    #: The source whose interpretation is being edited by hand, if any. Editing is
    #: a mode rather than a dialog: the check facts have to stay on screen while
    #: you write the prose, because they are what the prose is a read *of*.
    editing: str | None = None
    #: The source the operator is writing a note about, for the copilot to re-read.
    asking: str | None = None
    show_transcript: bool = True
    show_files: bool = True

    #: The transcript, in the order things happened: `events.Event | Decision`.
    rows: list[Any] = field(default_factory=list)
    turn: Turn | None = None

    #: Turn settings, remembered between turns.
    goal: str = ""
    model: str = ""
    effort: str | None = "low"
    interpret: bool = True

    @property
    def catalog_dir(self) -> Path:
        return self.root / self.portia_dir

    @property
    def sources(self) -> dict[str, dict]:
        return self.catalog.get("sources") or {}

    @property
    def project_context(self) -> str:
        return (self.catalog.get("project") or "").strip()

    @property
    def spec_has_steps(self) -> bool:
        """Whether there is anything to run. Decides which action carries the
        accent: with no steps recorded the copilot is the way forward, so **Go**
        is primary; once a spec has steps, **Run** is. Never both at once."""
        return bool((self.spec or {}).get("steps"))

    @property
    def busy(self) -> bool:
        """A turn is live. Starting a second one would interleave two streams."""
        return self.turn is not None and self.turn.running

    @property
    def pending(self) -> Decision | None:
        """The decision the loop is currently blocked on, if any."""
        for row in reversed(self.rows):
            if isinstance(row, Decision) and not row.resolved:
                return row
        return None

    def select(self, kind: str | None, name: str = "") -> None:
        self.selection = None if kind is None else (kind, name)

    def is_selected(self, kind: str, name: str) -> bool:
        return self.selection == (kind, name)

    def start_turn(self, prompt: str, *, model: str, effort: str | None) -> Turn:
        self.rows = []
        self.turn = Turn(prompt=prompt, model=model, effort=effort)
        return self.turn


#: The one open project. See the module docstring for why it is a singleton.
APP = App()

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
__all__ = [
    "PREVIEW_ROWS",
    "APP",
    "App",
    "Decision",
    "Turn",
    "SOURCE",
    "SPEC",
    "MODEL",
    "OUTPUT",
    "RUN",
    "TURN",
    "GOAL",
    "INDEXING",
    "REREAD",
    "CHAT",
    "INDEX",
    "TABS",
    "Stream",
    "WIDE",
    "MEDIUM",
    "band_for",
]

#: What a left-pane selection can be. ``None`` means the workflow is in view.
#:
#: ``RUN`` and ``TURN`` are two different artifacts and get two different words
#: on screen: a **run** executed a spec and was saved as markdown, a **turn** was
#: the copilot working and was logged as events (`portia/runlog.py`). One is what
#: the recipe did, the other is how the recipe got decided. Collapsing them into
#: one list would make "run" mean two things in the pane that exists to say what
#: portia knows about.
SOURCE = "source"
SPEC = "spec"
#: A compiled ``models/*.sql`` — **the deliverable**, and deliberately not an
#: OUTPUT. A run's CSV is a result of executing the pipeline; this is the
#: pipeline (`docs/PIPELINE.md` §2.2). One list holding both would make "output"
#: mean two things in the pane whose whole job is saying what portia knows about.
MODEL = "model"
OUTPUT = "output"
RUN = "run"
TURN = "turn"


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


#: What a turn was started *for*.
GOAL = "goal"
INDEXING = "indexing"
REREAD = "reread"

#: The right pane's two tabs. A goal you typed and the catalog work the app runs
#: on your behalf are different jobs with different rhythms, and interleaving
#: them in one scroll made each harder to read than it is alone.
CHAT = "chat"
INDEX = "index"
TABS = (CHAT, INDEX)

#: Which tab a turn's transcript belongs to.
TAB_FOR_KIND = {GOAL: CHAT, INDEXING: INDEX, REREAD: INDEX}

#: `DESIGN.md` → Width behaviour, as three bands. The workflow pane and Run stay
#: reachable at every width, so it is always a side pane that gives way.
#:
#: These set **defaults**, not constraints: crossing a threshold changes what is
#: showing, and the toolbar toggles still win afterwards. A hard rule would take
#: the transcript — which holds the question form and the write confirmation, the
#: two things this app exists for — away from anyone on a 1280px screen.
WIDE = 1400
MEDIUM = 1024
WIDE_BAND = "wide"
MEDIUM_BAND = "medium"
NARROW_BAND = "narrow"


def band_for(width: int) -> str:
    if width >= WIDE:
        return WIDE_BAND
    return MEDIUM_BAND if width >= MEDIUM else NARROW_BAND


@dataclass
class Turn:
    """One copilot turn: what it was asked, what it is spending, how it ended."""

    prompt: str
    model: str
    effort: str | None
    kind: str = GOAL
    label: str = ""
    running: bool = True
    subtype: str | None = None
    cost_usd: float | None = None
    error: str | None = None

    @property
    def ended(self) -> bool:
        return not self.running


@dataclass
class Stream:
    """One tab's transcript: its rows, and the turn that produced them.

    Two streams, one engine. The engine is single-turn, so at most one of these
    is ever live — the split is about *reading* them apart, not about running
    two at once.
    """

    rows: list[Any] = field(default_factory=list)
    turn: Turn | None = None

    @property
    def busy(self) -> bool:
        return self.turn is not None and self.turn.running

    @property
    def pending(self) -> Decision | None:
        """The decision this stream is blocked on, if any."""
        for row in reversed(self.rows):
            if isinstance(row, Decision) and not row.resolved:
                return row
        return None


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
    #: Every model the last run built — the open spec plus everything it reads.
    #: `results` is the open spec's steps; this is what else was executed, so the
    #: run header can say so rather than implying one spec ran alone.
    built: list = field(default_factory=list)  # list[pipeline.BuiltModel]
    run_error: str | None = None
    outputs: list[Path] = field(default_factory=list)

    selection: tuple[str, str] | None = None  # (kind, name) — None = the workflow
    selected_step: str | None = None
    #: Which model cards are open on the canvas, showing the steps that build them.
    #: A frozenset because it is replaced rather than mutated, which is what makes
    #: "did the graph change?" answerable by comparing two values.
    expanded: frozenset[str] = frozenset()
    #: A model to bring into view on the next render, then forget. Picking a spec
    #: on the left pans the canvas to its card instead of replacing the view —
    #: the canvas is the one place both zoom levels are true at once.
    focus_model: str | None = None
    #: The source whose interpretation is being edited by hand, if any. Editing is
    #: a mode rather than a dialog: the check facts have to stay on screen while
    #: you write the prose, because they are what the prose is a read *of*.
    editing: str | None = None
    #: The source the operator is writing a note about, for the copilot to re-read.
    asking: str | None = None
    #: The source whose removal is waiting on a confirmation.
    removing: str | None = None
    #: Whether the operator chose to get on with it without adding data yet.
    skipped_sources: bool = False
    #: What indexing is doing right now, as a sentence to put on screen — empty
    #: when nothing is running. Profiling twenty real extracts takes a minute,
    #: and a window that says nothing for a minute reads as broken.
    indexing_status: str = ""
    #: Sources profiled but not yet read by the copilot. The interpretation turn
    #: is deferred until the workspace is open, because that is the only screen
    #: with a transcript to show it in — running it on the add-data screen meant
    #: paying for a turn nobody could see.
    pending_interpret: list[str] = field(default_factory=list)
    #: Whether they have left the add-data screen on purpose.
    #:
    #: Adding data used to move the screen on by itself, the moment the first
    #: source landed. That reads as a teleport when you are adding twenty files:
    #: the screen you are working on vanishes mid-task, and you never see what
    #: arrived. Leaving is now a decision — `Continue` — so the screen can show
    #: what it added and you can add another batch before moving on.
    left_add_data: bool = False
    show_transcript: bool = True
    show_files: bool = True
    #: How wide the window is, reported by `assets/viewport.js`. Layout only — the
    #: splitters need it to work out how much room they may give a side pane
    #: before the workflow pane stops being usable, and CSS cannot express that
    #: once a splitter is setting inline pixel widths on its panels.
    width: int = WIDE
    #: Which width band that was, so the band's defaults are applied when you
    #: cross a threshold and **not** on every resize event. Dragging a window
    #: narrower should not keep reopening a pane you just closed.
    band: str = WIDE_BAND

    #: One transcript per tab, and which tab is showing.
    streams: dict[str, Stream] = field(default_factory=lambda: {t: Stream() for t in TABS})
    tab: str = CHAT

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

    def stream(self, tab: str | None = None) -> Stream:
        """One tab's transcript — the showing one unless another is named."""
        return self.streams[tab or self.tab]

    def stream_for(self, kind: str) -> Stream:
        """Where a turn of this kind writes."""
        return self.streams[TAB_FOR_KIND.get(kind, CHAT)]

    @property
    def rows(self) -> list[Any]:
        return self.stream().rows

    @property
    def turn(self) -> Turn | None:
        return self.stream().turn

    @property
    def busy(self) -> bool:
        """A turn is live **anywhere**. Starting a second would interleave two."""
        return any(s.busy for s in self.streams.values())

    def resize(self, width: int) -> bool:
        """Record the window width; return whether the layout has to be redrawn.

        Only a **band change** reapplies defaults. Resizing within a band leaves
        the panes exactly as you left them, which is the difference between a
        layout that adapts and one that keeps overruling you.
        """
        self.width = width
        band = band_for(width)
        if band == self.band:
            return False
        self.band = band
        self.show_files = band != NARROW_BAND
        self.show_transcript = band == WIDE_BAND
        return True

    def select(self, kind: str | None, name: str = "") -> None:
        self.selection = None if kind is None else (kind, name)

    def is_selected(self, kind: str, name: str) -> bool:
        return self.selection == (kind, name)

    def start_turn(
        self,
        prompt: str,
        *,
        model: str,
        effort: str | None,
        kind: str = GOAL,
        label: str = "",
    ) -> Stream:
        """Begin a turn in the stream its kind belongs to, and show that tab.

        Switching is not a nicety: the turn is about to ask questions and request
        writes, and a loop blocked behind a hidden tab is a loop that looks hung.
        """
        stream = self.stream_for(kind)
        stream.rows = []
        stream.turn = Turn(prompt=prompt, model=model, effort=effort, kind=kind, label=label)
        self.tab = TAB_FOR_KIND.get(kind, CHAT)
        return stream


#: The one open project. See the module docstring for why it is a singleton.
APP = App()

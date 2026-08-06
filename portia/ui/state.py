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
from collections.abc import Collection
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from portia.core.present import PREVIEW_ROWS
from portia.ui import tree

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
    "UNINDEXED",
    "BRIEF",
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
#: A file in the tree that portia can read and has never profiled. It is a
#: selection because the tree shows such files, and a row you can click into
#: nothing is a dead end — the inspector says what it is and offers to index it.
UNINDEXED = tree.DATA
#: The project brief. Not a file in the tree — ``.portia/`` is not walked — but
#: pinned above it, because it is the most consequential text in the product and
#: it was previously reachable only from a toolbar button.
BRIEF = "brief"
#: The knowledge graph — **not** the project canvas, and pinned rather than in
#: the tree for the same reason the brief is: it lives in Neo4j, not on disk.
#: The canvas draws what we specified; this draws what the data is to itself
#: (`docs/KNOWLEDGE_GRAPH.md` §6.9), so they are two rows, never two modes of
#: one.
KNOWLEDGE = "knowledge"


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
#: showing, and what you do to a pane afterwards still wins — dragging its edge
#: past its floor to close it, or the rail to bring it back. A hard rule would take
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
    #: What the turn sent and received, from `runlog.token_totals` — the same
    #: arithmetic `cli.runs` prints, so the window and the terminal cannot quote
    #: two different numbers for one turn. `input_tokens` is the whole input
    #: including the cached part, which on a portia turn is nearly all of it.
    input_tokens: int | None = None
    cached_tokens: int | None = None
    output_tokens: int | None = None
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
    #: Whether the knowledge explorer is showing columns as well as tables.
    #: A view, not a preference: it is about what is on screen right now, so it
    #: lives here rather than in `ui/settings.py`.
    knowledge_columns: bool = False
    #: Which sources the indexing tab has ticked. A selection, not a setting —
    #: it is about what you are doing right now, so it lives here rather than in
    #: `ui/settings.py` and is not persisted.
    index_ticks: frozenset[str] = frozenset()
    selected_step: str | None = None
    #: Which folders in the left tree the operator has opened, and which they have
    #: shut. Two sets rather than one because the default is neither: **the top
    #: level is open and everything below it is closed**, so "open" and "closed"
    #: are both overrides of a rule, and one set could not say which.
    #:
    #: Frozensets because they are replaced rather than mutated, and paths rather
    #: than nodes because the tree is rebuilt from disk on every render — a folder
    #: that exists in two consecutive renders is the same row to the operator, and
    #: `rel` is the only thing that survives the rebuild.
    open_folders: frozenset[str] = frozenset()
    closed_folders: frozenset[str] = frozenset()
    #: Which model cards are open on the canvas, showing the steps that build them.
    #: A frozenset because it is replaced rather than mutated, which is what makes
    #: "did the graph change?" answerable by comparing two values.
    expanded: frozenset[str] = frozenset()
    #: A model to bring into view. Picking a spec on the left pans the canvas to
    #: its card instead of replacing the view — the canvas is the one place both
    #: zoom levels are true at once.
    focus_model: str | None = None
    #: Bumped on each explicit focus request, and rendered onto the marked card so
    #: the client can act on it **once**. It is not a counter of anything and
    #: nothing reads it as one.
    #:
    #: The obvious version — clear `focus_model` as the render consumes it — was
    #: wrong: the workflow pane renders more than once per click, so the first
    #: render ate the flag and the render that reached the DOM had nothing to mark.
    #: A token makes a repeated render harmless instead of making it a race.
    focus_token: int = 0
    #: The source whose interpretation is being edited by hand, if any. Editing is
    #: a mode rather than a dialog: the check facts have to stay on screen while
    #: you write the prose, because they are what the prose is a read *of*.
    editing: str | None = None
    #: The source the operator is writing a note about, for the copilot to re-read.
    asking: str | None = None
    #: The source whose removal is waiting on a confirmation.
    removing: str | None = None
    #: The source whose full column list is unfolded, if any. Thirty columns is
    #: the normal case for a real extract, and a source inspector that opens on a
    #: screenful of them buries the prose read and the actions under it. One name
    #: rather than a set: there is one inspector, showing one source.
    columns_open: str | None = None
    #: Whether the operator chose to get on with it without adding data yet.
    skipped_sources: bool = False
    #: Which folder the add-data picker is looking inside, repo-relative. Where
    #: you are in a browser, not what you chose — choosing writes ``data_dir`` to
    #: the catalog, and this is forgotten the moment the screen closes.
    browse_at: str = ""
    #: Files under the data folder the operator has **un**-ticked, as repo-relative
    #: paths. The negative, deliberately: the default is that everything readable
    #: under the folder you picked is what you meant, and a set of exclusions is
    #: the only shape in which that default survives the list being rebuilt when
    #: a file is imported into the middle of it.
    unpicked: frozenset[str] = frozenset()
    #: Whether the folder picker is showing again over an already-chosen data
    #: folder. A mode rather than clearing the setting, so "change the folder"
    #: can be abandoned — clearing first would make it a button whose only
    #: possible outcome is losing what you had.
    repicking: bool = False
    #: Whether the external-import section is unfolded. Folded by default: it is
    #: the second route in, and a project whose data is already in the repo should
    #: not have to read past it.
    import_open: bool = False
    #: Whether an import lands in the project's data folder (the default) or in a
    #: destination typed below. Two fields rather than one sentinel string, so
    #: "put it with the rest of the data" survives the folder being re-picked.
    import_to_data_dir: bool = True
    #: Where an import will put what it copies when the above is off, relative to
    #: the project root. Data lives in the repo (`docs/PIPELINE.md` §2.7), so this
    #: is a place inside it, never a way out of it.
    import_destination: str = "data"
    #: The pending import, as ``(from, to)`` pairs — exactly what will be copied
    #: and where. Held so the confirmation shows the real thing rather than a
    #: description of one, and cleared the moment it is acted on or abandoned.
    import_plan: list = field(default_factory=list)  # list[tuple[Path, Path]]
    #: Why the last import could not be planned, in the operator's terms.
    import_error: str = ""
    #: What indexing is doing right now, as a sentence to put on screen — empty
    #: when nothing is running. Profiling twenty real extracts takes a minute,
    #: and a window that says nothing for a minute reads as broken.
    indexing_status: str = ""
    #: Sources profiled but not yet read by the copilot. The interpretation turn
    #: is deferred until the workspace is open, because that is the only screen
    #: with a transcript to show it in — running it on the add-data screen meant
    #: paying for a turn nobody could see.
    pending_interpret: list[str] = field(default_factory=list)
    #: How many sources the last indexing run profiled, or ``None`` if none has
    #: finished on this screen. It is what turns the primary action from "index
    #: these" into "open the workspace" — the screen has to say the work is done
    #: before it offers the way out of it, or the CTA reads as a skip.
    indexed: int | None = None
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
    def data_dir(self) -> str:
        """The folder in the repo that holds this project's data, or ``""``.

        Read off the catalog rather than held as a field, for the reason every
        other catalog value is: it is written to ``project.yaml`` and a second
        copy in memory is a second answer waiting to disagree with the first.
        Empty means nobody has said, which reads as the whole repo.
        """
        return (self.catalog.get("data_dir") or "").strip()

    def import_dir(self, fallback: str) -> str:
        """Where an import lands: the data folder, or the destination typed below.

        ``fallback`` is what to use when neither is set — `engine.DATA_DIR`, so
        an import with nothing chosen anywhere creates ``data/`` rather than
        landing at the project root.

        **The project root is a scope and not a destination.** ``"."`` is a
        legitimate answer to "which folder is my data" — the whole repo — and a
        nonsensical one to "where should this copy land", because it drops
        imported files loose at the top of the project. So it falls through here.
        """
        if self.import_to_data_dir:
            return self.data_dir if self.data_dir not in ("", ".") else fallback
        # **An empty box means the project root** (2026-08-06). It used to fall
        # through to `data/`, on the reading above that dropping files loose at
        # the top of a project is not a decision anyone makes deliberately. That
        # is wrong for the project whose data *is* at its root — a folder of
        # CSVs opened directly — where `data/` is the surprise and the root is
        # the obvious answer. The checkbox above is still the default, so the
        # empty box is now something you have to clear on purpose.
        return (self.import_destination or "").strip()

    def reveal_for_decision(self) -> bool:
        """A pending question or write opens the workspace. Did it need opening?

        The **one exit** from the first-run block. The add-data screen holds you
        while the opening interpretation runs, so you cannot walk into a
        workspace describing sources nobody has read yet — but the copilot may
        stop and ask, and the form it asks with lives in the transcript. Blocking
        without this is a screen waiting forever for an answer it gives you no
        way to give.

        A rule rather than two lines inside `turn._stop` because it is worth
        stating, and because stating it makes it testable without a browser.
        """
        if self.left_add_data:
            return False
        self.left_add_data = True
        return True

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

    def focus(self, model: str) -> None:
        """Ask the canvas to bring ``model`` into view on the next render."""
        self.focus_model = model
        self.focus_token += 1

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

    def folder_open(self, rel: str, depth: int) -> bool:
        """Whether a folder in the left tree is showing its contents.

        Open if you opened it, or if it is top level and you have not closed it.
        The default is deliberately shallow: a project's own folders are the
        thing you want to see on opening, and everything under them is a walk you
        asked for.
        """
        if rel in self.open_folders:
            return True
        return depth == 0 and rel not in self.closed_folders

    def tick(self, rel: str, on: bool) -> None:
        """Include or exclude one file from what the add-data screen will profile.

        Recorded as an **exclusion** either way, which is what makes the default
        survive: "everything under the folder I chose" has to keep meaning that
        as files arrive, and a set of selections would freeze the answer at the
        moment the list was last drawn.
        """
        self.unpicked = (self.unpicked - {rel}) if on else (self.unpicked | {rel})

    def tick_all(self, rels: Collection[str], on: bool) -> None:
        self.unpicked = (self.unpicked - set(rels)) if on else (self.unpicked | set(rels))

    def toggle_folder(self, rel: str, depth: int) -> None:
        if self.folder_open(rel, depth):
            self.open_folders -= {rel}
            self.closed_folders |= {rel}
        else:
            self.closed_folders -= {rel}
            self.open_folders |= {rel}

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

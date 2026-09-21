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
import time
from collections.abc import Collection
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from portia.agent import providers
from portia.core.cancel import Scope
from portia.core.present import PREVIEW_ROWS
from portia.ui import tree

#: How much of a table a preview shows — one number for every surface, so
#: "showing 15 of 40" means the same thing in the app and in a saved report.
__all__ = [
    "PREVIEW_ROWS",
    "APP",
    "App",
    "Chart",
    "Decision",
    "Exchange",
    "SOURCE",
    "SPEC",
    "MODEL",
    "OUTPUT",
    "RUN",
    "UNINDEXED",
    "BRIEF",
    "FIGURES",
    "CANVAS",
    "GOAL",
    "INDEXING",
    "REREAD",
    "LIST",
    "SOURCES",
    "JOB_KINDS",
    "Chat",
    "WIDE",
    "MEDIUM",
    "band_for",
    "ASK",
    "AUTOPILOT",
    "MODES",
    "WRITE_LABELS",
]

#: What a left-pane selection can be. ``None`` means the workflow is in view.
#:
#: A **run** executed a spec and was saved as markdown, so it is a file in the
#: tree. A **chat** and an **indexing** are not selections any more: they are
#: the right pane's own list (`docs/CHAT_SESSIONS.md` §3.2), which is where a
#: conversation can be picked up rather than only read.
SOURCE = "source"
SPEC = "spec"
#: A compiled ``models/*.sql`` — **the deliverable**, and deliberately not an
#: OUTPUT. A run's CSV is a result of executing the pipeline; this is the
#: pipeline (`docs/PIPELINE.md` §2.2). One list holding both would make "output"
#: mean two things in the pane whose whole job is saying what portia knows about.
MODEL = "model"
#: A table the project **built**, as the catalog knows it — the entry under
#: ``.portia/models/``, read the way a source's is (2026-09-07). `MODEL` is the
#: compiled file; this is what portia measured about the table it produces.
BUILT = "built"
OUTPUT = "output"
RUN = "run"
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
#: The gallery — saved figures, in the folders the user arranged them into
#: (`portia/figures.py`). Pinned for the same reason the brief and the graph are:
#: it is a thing this project has, and it is drawn whether or not anything is in
#: it yet. **An empty section is the point** *(2026-09-03)* — a heading that only
#: appears once you have used the feature cannot tell you the feature is there.
FIGURES = "figures"

#: **There is no selection kind for one saved figure**, and there was
#: *(2026-09-03)*. A figure opens as a tab now (`docs/VISUALIZATION.md` §6.4), so
#: it is a `Chart` keyed by its path rather than something tab zero is about — and
#: a selection kind nothing selects is a trap for whoever adds the next surface.

#: The two halves of the middle pane, in the order they are drawn
#: (`docs/VISUALIZATION.md` §3.7). **Groups, not a pane and a pinned thing**
#: *(2026-09-03)*: each is a strip of tabs with its own active one, and a tab
#: belongs to exactly one. The first design held a single pinned tab on the
#: right, which is one drag short of what an editor does — you could put a
#: second chart beside a first, and then not keep both.
#:
#: There are two and there is no third. Nobody has asked for one, and the right
#: half is already narrow at the widths this window is used at.
LEFT = "left"
RIGHT = "right"
GROUPS = (LEFT, RIGHT)

#: Tab zero, as a tab key (`docs/VISUALIZATION.md` §3.2 and §3.7).
#:
#: **The canvas needs a name now that the pane can be split.** It is a tab like
#: any other — it moves between groups, it takes a position on a strip — and the
#: only thing it does not do is close. The empty string can never collide with a
#: chart, which is named by the agent, or with a figure, which is named by its
#: path.
CANVAS = ""


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

    @property
    def interrupted(self) -> bool:
        """Whether this was cut short rather than answered.

        The SDK cancels the parked `can_use_tool` task when a message is
        interrupted (`docs/CONVERSATION.md` §8, measured), so the future ends
        cancelled with nobody waiting on it. A panel that cannot tell that apart
        from *still waiting* draws a form backed by nothing.
        """
        return not self.resolved and self.future.cancelled()

    def resolve(self, outcome: Any) -> None:
        self.resolved = True
        self.outcome = outcome
        if not self.future.done():
            self.future.set_result(outcome)


#: What a connection is doing, as the pinned section says it. A **kind** of
#: state, never a rank: `failed` is drawn the same size as `connected`.
NOT_CONNECTED = "not connected"
CONNECTING = "connecting"
CONNECTED = "connected"
#: `App.server_status` while a local server portia started is loading its model.
STARTING = "starting"
CONNECTION_STATES = (NOT_CONNECTED, CONNECTING, CONNECTED)

#: Where a project's data is, as the first-run screen asks it: files in the
#: repo, or a warehouse. **One or the other**, never both on one screen — a
#: project reads from one place (`docs/CONNECTOR.md` §2.2), and the screen that
#: offered both at once read as two half-forms. Derived from the project when it
#: has said (a connection, or a data folder); asked when it has not.
LOCAL_DATA = "local"
WAREHOUSE_DATA = "warehouse"

#: What a turn was started *for*.
GOAL = "goal"
INDEXING = "indexing"
REREAD = "reread"

#: The exchange kinds that are **jobs** rather than conversations: the app
#: started them on your behalf, they have a defined end, and there is nothing to
#: reply to (`docs/CONVERSATION.md` §6). A job is a `Chat` like any other in the
#: list, told apart by its glyph and by having no composer.
JOB_KINDS = (INDEXING, REREAD)

#: What the right pane shows when no chat is open (`docs/CHAT_SESSIONS.md`
#: §3.2). ``LIST`` is the home state: every chat and job, newest first, with a
#: composer at the foot that starts a new one. ``SOURCES`` is the pinned row at
#: the top of that list — what portia knows about each source and the Index
#: button, which used to be the Indexing tab's header.
LIST = "list"
SOURCES = "sources"

#: What the window says while a run action is on a thread. The **word** is what
#: `App.running` holds, not a key naming the button, so the action bar and the
#: report half cannot end up with two vocabularies for one press. Present tense
#: and lower case, like every other caption in the app.
#:
#: Two words rather than four *(2026-08-15)*: Run and Build became one action
#: scoped by the canvas, and Save writes the tables and their reports together —
#: so "building the project" and "saving the report" no longer name anything a
#: human can press. `RUNNING` says what it is doing rather than what it is doing
#: it to, because the scope is on the canvas in front of it.
RUNNING = "running"
WRITING = "writing outputs"

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
class Exchange:
    """One exchange: what it was asked, what it is spending, how it ended.

    One human message and the agent's work in response — not a whole chat, which
    is the file this lands in (`docs/CONVERSATION.md` §3). It was ``Turn``, and
    "turn" retired because the word was doing two jobs.
    """

    prompt: str
    model: str
    effort: str | None
    kind: str = GOAL
    label: str = ""
    #: Where the model came from (`agent/providers/`). Beside the model on the
    #: exchange for `CONVERSATION.md` §5's reason: a chat can span providers.
    provider: str = providers.DEFAULT_KIND
    running: bool = True
    #: Whether the exchange's own `PROMPT` event has arrived. Until it has, the
    #: message exists only here: `Conversation.send` yields it after the client
    #: has connected, and on a local provider connecting is the SDK binary's
    #: whole startup, a minute on the 2026-09-08 drive. The transcript draws the
    #: message and a *starting* mark off this exchange meanwhile, so the pane
    #: never says *no messages yet* over a message that was just sent.
    opened: bool = False
    subtype: str | None = None
    cost_usd: float | None = None
    #: What the turn sent and received, from `runlog.token_totals` — the same
    #: arithmetic `cli.history` prints, so the window and the terminal cannot quote
    #: two different numbers for one turn. `input_tokens` is the whole input
    #: including the cached part, which on a portia turn is nearly all of it.
    input_tokens: int | None = None
    cached_tokens: int | None = None
    output_tokens: int | None = None
    error: str | None = None
    #: The same failure as `core.feedback` remembered it, for *Report this*
    #: beside the alert. None on an interrupt: a stop is not a failure.
    problem: Any = None  # feedback.Problem

    @property
    def ended(self) -> bool:
        return not self.running


@dataclass
class Timing:
    """When the window saw a tool call go out, and when it saw the result.

    The window's clock and nobody else's: `events.Event` carries no time on
    purpose (`runlog.Log.write` — the event is the engine's, when it arrived is
    the observer's), and `time.monotonic` rather than a wall clock for
    `Progress.started`'s reason. A replayed log gets the same number off its
    stamps instead (`runlog.call_durations`).
    """

    started: float = field(default_factory=time.monotonic)
    ended: float | None = None

    @property
    def elapsed(self) -> float:
        return (self.ended if self.ended is not None else time.monotonic()) - self.started

    @classmethod
    def of(cls, seconds: float) -> Timing:
        """A finished call that took ``seconds`` — a replayed log's shape."""
        return cls(started=0.0, ended=float(seconds))


@dataclass
class Chat:
    """One chat, or one job, as the window holds it (`docs/CHAT_SESSIONS.md`).

    Many of these are open at once and **at most one is ever live** — the split
    is about reading them apart and coming back to one, not about running two
    at once (§3.1). A chat's rows accumulate across exchanges; a job's are one
    exchange, because a job is one-shot (`docs/CONVERSATION.md` §6).

    Three states, and the row in the list does not distinguish the first two
    (§3.3): **open**, with a client parked in this process; **closed**, only
    the log on disk, picked up through the SDK's `resume` on the next send;
    **legacy**, nothing to resume, so read-only and the surface says why.
    """

    #: The exchange kind this holds: `GOAL` for a conversation, one of
    #: `JOB_KINDS` for a job the app ran.
    kind: str = GOAL
    #: The log file, once one exists. ``None`` until the first message goes, so
    #: a chat opened and never written to leaves nothing on disk.
    path: Path | None = None
    #: What the list calls it: the name a human gave it, else the first line of
    #: the first prompt. Set by the driver when the log opens and by a rename.
    title: str = ""
    #: The model and effort *this* chat runs on. `App.model` and `App.effort`
    #: are the defaults a new chat starts with, not the value of any open one.
    model: str = ""
    effort: str | None = None
    #: Fixed for the life of a client, like effort and more so: it is the
    #: environment the SDK's binary was started in (`PROVIDERS.md` §4.2).
    provider: str = ""
    #: The SDK's id for the session, off the log when a closed chat is opened
    #: and off each result while it runs. What a resume hands the SDK.
    session_id: str | None = None
    #: Whether this process reopened a chat another process wrote (§3.3). The
    #: composer footer states the cost once — the cache is cold and the SDK
    #: reads the history back at full price — and never hides it.
    resumed: bool = False
    #: Why this chat cannot be continued, or ``""`` when it can. A log with no
    #: session id, anything under the pre-rename folder, or a resume the SDK
    #: refused. The row carries a chip and the composer's place says which.
    legacy: str = ""
    #: The log as read back from disk for a chat opened from the list, drawn
    #: through `transcript.replay` above the live ``rows``. ``None`` for a chat
    #: this process started.
    logged: Any = None
    #: `runlog.summary` of the logged half of a chat opened from disk — the
    #: exchanges and the cost this process did not see, so the footer's totals
    #: cover the whole chat and not only what happened since it was picked up.
    prior: dict[str, Any] = field(default_factory=dict)
    #: A per-process identity for entrance keys and element ids, given by
    #: `App.new_chat`. A chat has no path until its first message, and a key
    #: has to exist before that.
    ident: str = ""
    rows: list[Any] = field(default_factory=list)
    #: The exchange in flight, or the last one that ran.
    exchange: Exchange | None = None
    #: Every exchange in this chat, so the footer can total them without the
    #: panel doing arithmetic the log would do differently.
    exchanges: list[Exchange] = field(default_factory=list)
    #: The live `agent.session.Conversation`, held opaquely: this module imports
    #: no engine, and holding a handle is not calling one. ``None`` means no
    #: client is parked for this chat — it has never sent, or it was closed and
    #: the next send resumes it.
    conversation: Any = None
    #: The `runlog.Log` this chat is being written to, opened once and reused by
    #: every exchange — the file *is* the chat (§5).
    log: Any = None
    #: The one-shot `Conversation` an indexing job is running on, for as long as
    #: it runs, so Stop can reach it. Apart from ``conversation`` because that
    #: field means *a chat is open* (`open`), and a job is not one (§6).
    job: Any = None
    #: `Timing` by SDK call id — how long each tool call has been running, or
    #: took. Kept beside the rows rather than on them: the rows are the engine's
    #: events verbatim, and this is what the window measured about them.
    timings: dict[str, Timing] = field(default_factory=dict)
    #: What the chat is holding, last time the SDK was asked (`totalTokens`,
    #: `maxTokens`). Read after an exchange ends rather than during a render,
    #: because asking costs an await and a render may not have one.
    context: dict[str, Any] | None = None
    #: Bumped whenever ``rows`` is replaced rather than appended to — a new
    #: indexing job, a cleared chat. Positions are stable *within* a generation
    #: because rows only append, so ``generation:index`` is a row identity that
    #: a new job's row zero does not inherit from the last job's. It exists for
    #: the transcript's entrance keys and nothing else reads it.
    generation: int = 0
    #: How far the transcript has drawn this chat's rows as settled, and the
    #: element it drew them into (`transcript.stream_view`). The slot is
    #: NiceGUI's and this module never touches it — it is held here because a
    #: streamed event has to find it, and the chat is the one thing an event
    #: knows. Both reset with the rows.
    settled: int = 0
    settled_slot: Any = None

    @property
    def busy(self) -> bool:
        """A message is **in flight** — not that a chat exists.

        An open chat sitting idle is not busy, or it would block indexing for as
        long as it stays open (`docs/CONVERSATION.md` §9).
        """
        return self.exchange is not None and self.exchange.running

    @property
    def open(self) -> bool:
        """Whether a client is parked for this chat. Independent of `busy`."""
        return self.conversation is not None

    @property
    def is_job(self) -> bool:
        """A job the app ran, rather than a conversation: no composer, one exchange."""
        return self.kind in JOB_KINDS

    @property
    def continuable(self) -> bool:
        """Whether a message can be sent here: a conversation that is not legacy."""
        return not self.is_job and not self.legacy

    @property
    def key(self) -> str:
        """What names this chat in a keyed list — the file when there is one."""
        return self.path.stem if self.path is not None else self.ident

    @property
    def started(self) -> bool:
        """Whether anything has been sent — a chat with nothing in it is not listed."""
        return bool(self.exchanges) or self.path is not None

    @property
    def spent(self) -> float | None:
        """What this chat has cost, summed over its exchanges.

        A total, never a verdict: whether it was expensive needs a goal, and this
        panel has no way to know one (`runlog`).
        """
        costs = [e.cost_usd for e in self.exchanges if e.cost_usd is not None]
        if self.prior.get("cost_usd"):
            costs.append(float(self.prior["cost_usd"]))
        return sum(costs) if costs else None

    @property
    def messages(self) -> int:
        """How many messages the whole chat holds, the logged half included."""
        return int(self.prior.get("exchanges") or 0) + len(self.exchanges)

    @property
    def pending(self) -> Decision | None:
        """The decision this chat is blocked on, if any."""
        for row in reversed(self.rows):
            if isinstance(row, Decision) and not row.resolved:
                return row
        return None


@dataclass
class Progress:
    """What a run is on, while it is on it — the window's copy of `BuildProgress`.

    The engine's record plus the one thing only the window can know: when the
    press happened. `elapsed` is what makes this worth showing on a build whose
    slowest step is 47s — it is the difference between a step that is working and
    a step that is stuck, and no count can say which.

    **Everything here is measured, and nothing here is derived.** No percentage
    and no estimate: `pipeline.BuildProgress` says why, and it is the same reason
    the mark it is drawn on never grows (`DESIGN.md` → `in-flight`).
    """

    model: str = ""
    models_done: int = 0
    models_total: int = 0
    step: str = ""
    steps_done: int = 0
    steps_total: int = 0
    #: `time.monotonic` at the press, never a wall clock: this is a duration, and
    #: a duration measured off a clock that can be adjusted under it is a number
    #: that can go backwards.
    started: float = field(default_factory=time.monotonic)

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started

    @property
    def scoped(self) -> bool:
        """Is more than one model in this run? Run scopes to one *plus its inputs*.

        The outer count is worth drawing only when it can change — a single-spec
        project would otherwise carry a permanent `1 of 1` beside the count that
        is actually moving.
        """
        return self.models_total > 1


#: The write tools whose confirmation the human may switch off, and the only
#: names `App.auto_allow` will accept. Both write **catalog prose** — a summary,
#: a column role, a group and its context — which is judgment you can correct in
#: place afterwards and which the catalog's update rule preserves when facts
#: refresh (`CLAUDE.md` → `portia/catalog.py`).
#:
#: **`record_step` is deliberately absent and this is the whole point of the
#: set.** It is not that a spec step is more important prose; it is that
#: recording one **runs the op first**, so its confirmation is the only moment
#: anyone sees a step before it exists. `measure_overlaps` is absent for the
#: opposite reason — it never asks at all, being in `tools.READ_TOOLS` already.
#:
#: **`App.autopilot` lets it through anyway and is not a hole in this** (2026-09-04).
#: This set is per-tool and sticky: you switch one off and forget. Autopilot is
#: one mode you turn on for a task you are watching, and the argument for keeping
#: `record_step` out of *here* survives it intact — see the field for the part of
#: that argument a real session did not support, which is the *instant* it names
#: rather than the distinction it draws.
AUTO_ALLOWABLE = ("set_interpretation", "set_group")

#: What each switchable write **writes**, in the words a control is labelled with.
#: A switch reading `set_interpretation` was a tool name where a person expected a
#: sentence *(2026-09-04)*; these say what the tool changes, and nothing about
#: whether stopping for it is worth the interruption.
WRITE_LABELS = {
    "set_interpretation": "source summaries, column roles and notes",
    "set_group": "source groups",
}

#: The two approval modes as a control names them (`ui/components.approval_mode`).
#: Both are stated, always: a chip that appears only when autopilot is on says
#: nothing about the normal case, and a mode picker with two values says which
#: one you are in whichever it is. VS Code's shape — the mode sits beside the
#: model in the composer, and the same control is in Settings.
ASK = "ask"
AUTOPILOT = "autopilot"
MODES = (ASK, AUTOPILOT)


@dataclass
class Chart:
    """One chart the copilot drew, and the middle-pane tab it lives in.

    ``docs/VISUALIZATION.md`` §2 and §3. Three fields carry the whole design.

    **``name`` is the identity**, not just the label (§3.3). The agent names its
    tabs, and drawing under a name that already exists replaces that chart in
    place rather than opening a second tab — `record_step(supersedes=)`'s idiom,
    and here for the same reason: correcting a thing in place is what an author
    does, and the alternative is twelve tabs by minute forty. Portia does not
    guess which two charts are the same question; the agent says so by reusing
    the name.

    **``rows`` never went through the model** (§2.3). A tool result is pasted
    into the conversation as text and `tools.RESULT_BUDGET` refuses it over
    30,000 characters, so a 5,000-point scatter is either refused or is the agent
    reading coordinates for a picture it will not look at. Portia runs the SQL
    and puts the rows here, on their way to the browser; the agent got a receipt.
    That is also why nothing caps them (§2.7) — the model's context is not the
    constraint, so there is no number for code to decide.

    **``vega`` cannot compute** (§2.4). It is a Vega-Lite spec the *agent* wrote
    — marks, layers, scales, palettes, axes — and `agent.chartspec.check` refused
    every key in it that would work a number out in the browser. Every chart
    grammar is partly a transform language, and a ``GROUP BY`` done in an
    encoding is a number no ``SELECT`` produced, that nothing logged and
    `findings.review` cannot read back. The aggregate stays in ``sql``.
    """

    #: The tab's label and its identity (§3.3).
    name: str
    #: Why it was drawn, in the agent's words. Required for the same reason
    #: `query_data`'s ``question`` is: `findings.review` reads these back, and a
    #: bare chart spec is not reviewable.
    question: str = ""
    #: The agent's Vega-Lite spec (§2.9). `assets/chart.js` adds the rows, fills
    #: in the types it left out and the theme it did not ask about, and draws it.
    vega: dict = field(default_factory=dict)
    #: What the SELECT returned, on its way to the browser. Not capped (§2.7).
    rows: list[dict] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)
    #: The query behind it, kept so a saved chart re-renders from what was
    #: measured rather than from a picture (§6).
    sql: str = ""
    inputs: list[str] = field(default_factory=list)
    #: Where it lives in the gallery, repo-relative, once it lives anywhere.
    #: Empty while it is only a session chart.
    #:
    #: **A saved figure keeps its tab** *(2026-09-03)*, and this is the whole of
    #: that. Saving used to retire the chart and drop you back on tab zero
    #: showing the file, which is one artifact — the thing §6.4 was after — and
    #: the wrong way round: pressing *keep* on a picture you are reading is not a
    #: request to be taken away from it. So the chart stays where it was and
    #: gains a path, and the tab is the file from then on.
    path: str = ""
    #: The note whoever kept it typed, and when it was written. Both come off the
    #: file; a chart nobody has kept has neither.
    notes: str = ""
    at: str = ""
    #: Which `query_data` call this replots, if it reuses one (§2.6).
    from_query: int | None = None
    #: What broke, in the operator's terms. A failed query is a failed tab that
    #: says so and stays open — there is no retry (§8).
    error: str | None = None
    #: True between the press and the rows arriving. The tab draws in one pass and
    #: the query does not (§3.6), so a chart is on screen as a pending tab before
    #: it is a chart.
    pending: bool = False
    #: Whether the Keep form is open on this chart, and what has been typed into
    #: it (`docs/VISUALIZATION.md` §6). Held on the chart rather than in one
    #: app-wide slot because a half-written note belongs to the chart it is about:
    #: switching tabs to look at something else and coming back should find it
    #: where you left it, not gone or attached to the wrong picture.
    keeping: bool = False
    keep_notes: str = ""
    keep_folder: str = ""
    #: Whether the *where to put it* half of the form is unfolded. **Shut by
    #: default** *(2026-09-03)*: the top level is the right answer almost always,
    #: and a folder field open beside the notes box read as a second thing you had
    #: to fill in.
    keep_where: bool = False
    keep_error: str = ""
    #: Which half of the pane it is in (§3.7). A tab belongs to exactly one
    #: group, and dragging it across is what moves it.
    group: str = LEFT
    #: Whether it is *this group's* preview tab — the italic one in VS Code
    #: *(2026-09-03, the user's call)*. A single click on the left opens a
    #: preview and the next single click **replaces** it; a double click keeps
    #: it. One per group at most, which is what makes clicking down a list of
    #: twenty figures cost one tab rather than twenty.
    preview: bool = False
    #: How tall the picture is, in pixels, once somebody has dragged the grip
    #: under it. ``None`` fills the pane, which is the default and the right
    #: answer until it is not.
    height: int | None = None
    #: Whether its tab has been closed. **It stays in this list either way**
    #: (§3.5): a closed chart cost a query and two minutes of the copilot's
    #: attention, so the left pane lists it and clicking reopens the tab. Without
    #: that, closing destroys work, and a control you hesitate to press is not a
    #: control.
    closed: bool = False

    @property
    def key(self) -> str:
        """What its tab is called on the strip, and what `App.chart` looks up.

        **The name while it is a session chart, its path once it is a file.**
        The name is the identity the agent controls (§3.3) and it is the right
        one for as long as portia is the only thing holding the chart. A saved
        figure's identity belongs to the gallery instead: two figures may be
        named the same in two folders, and moving one is a rename portia did not
        author. The path is what is unique among files and what a drag changes.
        """
        return self.path or self.name

    @property
    def saved(self) -> bool:
        """Whether this picture is on disk."""
        return bool(self.path)

    @property
    def drawable(self) -> bool:
        """Whether there is a chart here to draw, as opposed to a state to report."""
        return not self.pending and not self.error and bool(self.rows)


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
    #: `run_error` as `core.feedback` remembered it, for *Report this* under it.
    run_problem: Any = None  # feedback.Problem
    outputs: list[Path] = field(default_factory=list)
    #: The reports written beside those tables, one per model. Saving a table and
    #: saving the account of the run that produced it is one press, so these two
    #: lists are written together and neither can exist without the other.
    reports: list[Path] = field(default_factory=list)
    #: The folder the last save landed in, so the window can name it. One per
    #: save (`pipeline.new_run_dir`) — the tables, the reports and this all refer
    #: to the same directory or the run has been split across two.
    run_folder: Path | None = None
    #: What the window is doing, as the word it says (`RUNNING`, `WRITING`), or
    #: `None`. It is what the window says while
    #: `pipeline.build_project` is on a thread — a real extract's join is a
    #: minute of work behind a button that
    #: otherwise looks exactly as it did before it was pressed, which is
    #: indistinguishable from a press that missed.
    #:
    #: **Deliberately not folded into `busy`.** That property is about the
    #: copilot's streams, and the two block different things: a chat in flight
    #: does not stop you running a spec, and a run in flight does not stop you
    #: typing the next message.
    running: str | None = None
    #: Where the run in flight has got to, or `None` when the work has no steps
    #: to report — the two save actions, and the moment between the press and the
    #: first callback. `running` says *that* something is happening and is the
    #: thing the buttons key off; this says *what*, and only Run and Build
    #: produce it (`Progress`).
    progress: Progress | None = None
    #: How to stop the run in flight, or `None` when there is nothing to stop.
    #: Held here rather than in the action that made it because **the button that
    #: cancels is drawn by a different function than the one that started the
    #: work** — `run_controls` renders every frame from `App`, and a closure it
    #: cannot see is not something it can offer.
    stop: Scope | None = None
    #: The same, for indexing. Two fields and not one because they are already
    #: two runs with two indicators on two screens (`running` / `indexing_status`),
    #: and one shared handle is how the wrong Stop ends up cancelling the wrong
    #: work the first time both are somehow live.
    indexing_stop: Scope | None = None

    selection: tuple[str, str] | None = None  # (kind, name) — None = the workflow
    #: Whether the knowledge explorer is showing columns as well as tables.
    #: A view, not a preference: it is about what is on screen right now, so it
    #: lives here rather than in `ui/settings.py`.
    knowledge_columns: bool = False
    #: Which transcript disclosures are open, by key — a tool call's id, or a
    #: thinking block's position in its stream.
    #:
    #: It has to live here rather than in the widget because the transcript is a
    #: refreshable and every event rebuilds it: an expansion holding its own
    #: value would shut the moment the next tool call arrived, which is exactly
    #: when a reader is in the middle of the result they just opened. Not
    #: persisted — what you have open is about right now.
    open_rows: frozenset[str] = frozenset()
    #: Which sources the indexing tab has ticked. A selection, not a setting —
    #: it is about what you are doing right now, so it lives here rather than in
    #: `ui/settings.py` and is not persisted.
    index_ticks: frozenset[str] = frozenset()
    #: What the artifact panes drew from disk, last time an exchange synced them
    #: (`engine.artifact_stamp`). Compared, never read: it exists so a tool
    #: result that wrote nothing does not rebuild three panes under a reader —
    #: most of the copilot's calls are questions, not writes.
    artifact_stamp: tuple | None = None
    selected_step: str | None = None
    #: Bumped each time a step is picked, and rendered onto its block in the run
    #: report so the client can bring it into view **once**. The same shape as
    #: `focus_token`, and for the same reason: the workflow pane renders more
    #: than once per click, so a flag the render clears is a race and a token a
    #: repeated render carries again is simply the same request.
    step_token: int = 0
    #: Whether the gallery is unfolded. **Its caret is what says the section is
    #: empty** *(2026-09-03)* — open with nothing under it says so more plainly
    #: than a sentence explaining that nothing is under it, and it says it without
    #: a line of prose in a pane made of one-line rows.
    figures_open: bool = True
    #: Which gallery folders are unfolded, as paths relative to ``figures/``.
    #: **Open by default, unlike the project tree**: a gallery is small and its
    #: shape is the whole point of having folders, so showing it folded hides the
    #: arrangement the user made. This is the set they have *closed*.
    figures_closed: frozenset[str] = frozenset()
    #: The gallery folder a new folder is being named inside, or ``None`` when
    #: nothing is being named. The field draws **inside that folder**, where the
    #: new one will appear, rather than at the top of the section: a form that
    #: opens somewhere other than where it will act makes you check afterwards.
    figures_adding: str | None = None
    figures_new_name: str = ""
    #: The gallery folder whose delete is waiting on a yes, or ``None``. A folder
    #: with figures in it is the one press in the gallery that takes more than
    #: one thing, so it asks first, **inline, under the row it is about**: the
    #: same place the naming field opens, and for the same reason. A dialog
    #: would also be the fourth one built at page level to survive a refresh.
    figures_deleting: str | None = None

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
    #: Which model cards are open on the canvas, showing what they read and the
    #: steps that build them. A frozenset because it is replaced rather than
    #: mutated, which is what makes "did the graph change?" answerable by
    #: comparing two values.
    expanded: frozenset[str] = frozenset()
    #: Which tables the canvas is narrowed to. **``None`` means nothing has been
    #: chosen**, which draws the whole project; an empty *set* means everything
    #: was turned off, which draws nothing. Two values rather than one falsy one,
    #: because the filter's select-all is a toggle and "off" is reachable — they
    #: look the same and mean opposite things (`graph._visible`).
    #:
    #: What is drawn is this set closed over its ancestry (`graph.ancestry`): a
    #: table cannot be shown without the tables it is built from. Un-choosing one
    #: takes its `graph.descendants` with it, because a card whose incoming arrow
    #: points at nothing is a wrong picture rather than a shorter one. Restored
    #: per project on open and written back on every change
    #: (`engine.remembered_view`).
    visible: frozenset[str] | None = None
    #: Where the reader dragged each card, as ``name -> (dx, dy)`` in layout
    #: pixels on top of the place the grid gives it (`graph.moved`). Replaced,
    #: never mutated, like `expanded`. Session state that is also written per
    #: project (`engine.remember_layout`), because an arrangement you spent a
    #: minute on is not something to lose to a restart; empty means nothing was
    #: moved and the Reset control is disabled.
    card_offsets: dict[str, tuple[int, int]] = field(default_factory=dict)
    #: The incoming table whose columns are unfolded on an open card, as
    #: ``(model, input)``. One at a time and one card at a time: this is what an
    #: arrow means, and the question is always about one arrow.
    open_input: tuple[str, str] | None = None
    #: The spec whose **missing** upstream tables are being shown as ghosts, if
    #: any. Picking a spec on the left that the canvas is not fully drawing shows
    #: what it would take to draw it — translucent cards, not on the canvas yet,
    #: so *this is what you are not looking at* is answerable without changing
    #: what is drawn. Derived from here rather than stored as a set: the ghosts
    #: are that spec's ancestry minus what is already drawn, and holding both
    #: would be two facts that can disagree.
    #:
    #: Cleared when you look at something else — the preview belongs to "I am
    #: looking at this spec" and dies with it.
    previewing: str | None = None
    #: Whether the run report under the canvas is showing. Open by default — it
    #: is the evidence, and a screen that starts by hiding it would be hiding the
    #: point. Shut, the canvas takes the whole pane and a rail at the foot names
    #: the spec it would report on, which is the same shape a closed side pane
    #: leaves behind.
    report_open: bool = True
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

    # --- the warehouse (`docs/CONNECTOR.md`) ---------------------------------------

    #: Where the project's connection stands, as the word the pane draws:
    #: ``""`` when the project names none, else one of `CONNECTION_STATES` or
    #: ``failed: <why>``. Not persisted — a session is per process (§2.4).
    connection_status: str = ""
    #: Whether the pinned Warehouse section is unfolded, and which databases and
    #: schemas inside it are shut (``"DB"`` or ``"DB.SCHEMA"``). Open by default
    #: for the gallery's reason: the shape of the scope is the point of drawing it.
    warehouse_open: bool = True
    warehouse_closed: frozenset[str] = frozenset()
    #: The connect form's draft, by field name, and what the last attempt said.
    #: On `App` rather than in the widgets because the add-data panel is a
    #: refreshable and every progress tick rebuilds it.
    connect_form: dict[str, str] = field(default_factory=dict)
    connect_error: str = ""
    #: Which of the dialog's two views is up. A saved connection is picked from a
    #: list and connected as it is; the form is for a new one, and is the first
    #: view only when nothing is saved (`screens._initial_view`).
    connect_new: bool = False
    connect_pick: str = ""
    #: The scope picker (`picktree.py`): which databases and schemas are unfolded
    #: (``"DB"`` or ``"DB.SCHEMA"``, shut by default because opening one is a
    #: query), what has already been listed at each place (a browse is a query
    #: and is not repeated on every redraw), which tables are ticked, what the
    #: filter box holds, and the place a listing is on its way for (``None`` when
    #: none is; ``""`` is the databases themselves).
    scope_open: frozenset[str] = frozenset()
    scope_listing: dict[str, list] = field(default_factory=dict)
    scope_ticks: frozenset[str] = frozenset()
    scope_filter: str = ""
    scope_loading: str | None = None
    #: The connect dialog's project half (`CONNECTOR.md` §2.7.1): whether the
    #: copilot may write, as a draft until Connect saves it, and the sentence
    #: the dialog opens with when an action sent you there.
    connect_agent_writes: bool = False
    connect_note: str = ""
    #: `LOCAL_DATA`, `WAREHOUSE_DATA`, or ``""`` when the project has not said and
    #: nobody has chosen yet — which is when `needs_data_choice` draws the choice.
    data_mode: str = ""
    #: A password or token typed for this session's connection. Lives here only
    #: between the form and `engine.connect_project`, and is cleared after.
    connect_secret: str = ""

    @property
    def needs_data_choice(self) -> bool:
        """Whether the first-run screen should ask *where is the data* first."""
        return self.on_add_data and not self.data_mode

    @property
    def connection(self) -> str | None:
        """The connection this project names, or ``None`` for a local project."""
        return self.catalog.get("connection") or None

    @property
    def agent_writes(self) -> bool:
        """The hand-off: whether recording a step creates its table in the warehouse."""
        return bool(self.catalog.get("agent_writes"))

    @property
    def scope(self) -> list[str]:
        return list(self.catalog.get("scope") or [])

    @property
    def connected(self) -> bool:
        return self.connection_status == CONNECTED

    def tick_scope(self, qualified: str, on: bool) -> None:
        self.tick_scope_all([qualified], on)

    def tick_scope_all(self, qualified: Collection[str], on: bool) -> None:
        """A schema's box, or a database's: every table under it, in one press."""
        names = frozenset(qualified)
        self.scope_ticks = (self.scope_ticks | names) if on else (self.scope_ticks - names)

    def toggle_scope_node(self, key: str) -> bool:
        """Unfold or shut a database or a schema. Returns whether it is now open."""
        opened = key not in self.scope_open
        self.scope_open = (self.scope_open | {key}) if opened else (self.scope_open - {key})
        return opened

    def toggle_pick_folder(self, rel: str) -> None:
        if rel in self.pick_closed:
            self.pick_closed -= {rel}
        else:
            self.pick_closed |= {rel}

    def toggle_warehouse_node(self, rel: str) -> None:
        if rel in self.warehouse_closed:
            self.warehouse_closed -= {rel}
        else:
            self.warehouse_closed |= {rel}

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
    #: The file tree under the data folder: which folders are shut, and what the
    #: filter box holds. Shut is the exception here, unlike the warehouse's tree:
    #: the files are already on disk, and the shape of the folder is what the
    #: list is drawn for.
    pick_closed: frozenset[str] = frozenset()
    pick_filter: str = ""
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
    #: What could not be indexed, as ``{label: the error's first line}``: a
    #: file's stem, a table's name. Written by `engine._hops`, which skips a
    #: failed item and goes on, and cleared for a label by its next success.
    #: Held rather than toasted because a warehouse profile is minutes and
    #: nobody is looking when the eleventh of thirty fails. In memory only.
    indexing_failed: dict[str, str] = field(default_factory=dict)
    #: The Index button's label as it was pressed, held while the press is
    #: being acted on. The counts it was built from empty out during the run
    #: (an import plan is consumed by the copy), and a button that changed
    #: its words or gave its place to the way out mid-run is the button
    #: moving under the hand that just pressed it.
    indexing_pressed: str = ""
    #: Sources profiled but not yet read by the copilot. The read **starts as
    #: soon as profiling finishes** (2026-08-07) rather than waiting for the way
    #: out to be pressed: the turn is the slow half, and a screen that holds it
    #: back until you click is a screen that spends your wait twice. This is the
    #: queue between the two halves, so a second batch indexed while the first is
    #: still being read is picked up rather than dropped.
    pending_interpret: list[str] = field(default_factory=list)
    #: The kind of decision the copilot has stopped on while the human is still
    #: on the add-data screen — an `events` kind, or ``""`` for none. It is what
    #: the invitation popup reads, and clearing it is what closes that popup.
    decision_waiting: str = ""
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
    #: Back was pressed on a first-run screen, so the shell draws the brief
    #: again over a project that already has one. Cleared by Continue.
    editing_brief: bool = False
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

    #: Every chat and job this process holds, in the order they were started
    #: (`docs/CHAT_SESSIONS.md` §3.1). Open ones keep their client here; a
    #: closed one read back from disk joins when it is picked.
    chats: list[Chat] = field(default_factory=list)
    #: The chat on screen, or ``None`` when the right pane shows ``right``.
    open: Chat | None = None
    #: What the right pane shows with no chat open: `LIST` or `SOURCES`.
    right: str = LIST
    #: Counts `new_chat` calls, for `Chat.ident`.
    chat_count: int = 0
    #: A list row mid-rename or mid-delete, by log file name; ``""`` for none.
    #: Kept here rather than in a widget so the pane can be rebuilt under it.
    chat_renaming: str = ""
    chat_deleting: str = ""

    #: Every chart the copilot has drawn this session, open tabs and closed ones
    #: alike (`Chart.closed`, `docs/VISUALIZATION.md` §3.5).
    #:
    #: **Session state, deliberately** (§6). In memory, gone on restart, not
    #: persisted — the durable half is the journal, and the chat log holds every
    #: `plot_data` call regardless, which is `FINDINGS.md` §5.1's point again:
    #: there is no staging area because the log already is one.
    charts: list[Chart] = field(default_factory=list)
    #: Which tab is showing in each group, by `Chart.key`. `CANVAS` is tab zero.
    #:
    #: **One active tab per group** (§3.7), which is what makes the right half a
    #: half of the window rather than a pinned thing: you can switch what is on
    #: the right without disturbing what is on the left.
    #:
    #: The *middle* pane's tabs. The right pane has none any more: it is a list
    #: of chats with one open at a time (`docs/CHAT_SESSIONS.md` §3.2).
    active: dict[str, str] = field(default_factory=lambda: {LEFT: CANVAS, RIGHT: ""})
    #: Which group a new chart opens in, and which one a gesture with no group of
    #: its own means. VS Code's *active editor group*.
    focus_group: str = LEFT
    #: Which group holds tab zero. **The canvas is not in ``charts`` because it
    #: cannot be closed** (§3.2) — making it a member of the list would make an
    #: empty middle pane representable, which is a state with nothing to say and
    #: no obvious way out of it — so where it lives is a field of its own.
    canvas_group: str = LEFT

    #: Exchange settings, remembered between exchanges.
    goal: str = ""
    model: str = ""
    effort: str | None = "low"
    #: Where a new chat's model comes from (`docs/PROVIDERS.md`). The default a
    #: chat starts with, not the value of any open one, like `model`.
    provider: str = providers.DEFAULT_KIND
    #: What each provider said it serves, and whether it could be reached, as
    #: `engine.list_models` last read them. Read by the picker, never in a
    #: render: listing is a network call.
    provider_models: dict[str, list[Any]] = field(default_factory=dict)
    provider_status: dict[str, Any] = field(default_factory=dict)
    #: Providers being listed right now, so the picker can say so.
    models_listing: frozenset[str] = frozenset()
    #: A refused preflight, as ``(reason, remedy)``, drawn in the composer until
    #: the next Send (`PROVIDERS.md` §5). A refusal is not an exchange, so it
    #: opens no log and draws no prompt row.
    spend_alert: tuple[str, str] | None = None
    #: What a preflight is doing, while it does it: *loading qwen3:8b*. Send
    #: is refused for the duration, the way it is while a message is in flight.
    preflight_status: str = ""
    #: The start panel for a provider portia can start (`PROVIDERS.md` §4.9):
    #: the typed fields, whether the server is being started right now, and
    #: the last refusal, drawn in the panel. The saved configuration is the
    #: provider's (`llamacpp.CONFIG`); this is the form over it.
    server_form: dict[str, str] = field(default_factory=dict)
    server_status: str = ""
    server_error: str = ""
    interpret: bool = True
    #: Whether adding a warehouse table also scans it (`CONNECTOR.md` §2.6,
    #: revised 2026-09-07). Off, a table arrives as metadata for nothing; on,
    #: each one is profiled on the meter before the read. The add-data screen
    #: draws the switch beside the interpret one, because they are the two
    #: things that cost something and neither may be spent without being said.
    profile_on_add: bool = False
    #: Which write tools may run **without stopping to ask**, by tool name.
    #:
    #: The read/write line is drawn already and is not this
    #: (`agent/session.build_options` passes the rungs as ``allowed_tools``, so
    #: not one of them reaches a confirmation). This is the line *inside* the
    #: writes, and the 2026-08-31 runs are the argument for it: 25 of the
    #: indexing pass's 31 approvals were `set_interpretation` writing catalog
    #: prose, while the chat's 8 were `record_step` and cost the human 468 of
    #: 1123 seconds — reading a payload, which is the review this product exists
    #: for. So the two are not one decision.
    #:
    #: **`record_step` is not in `AUTO_ALLOWABLE` and cannot be put here.** It
    #: runs the op and *then* writes the spec (`CLAUDE.md` → `record_step`), so
    #: skipping its confirmation is skipping the only look anyone gets at a step
    #: before it exists. A catalog write is prose you can correct in place, and
    #: the update rule already preserves corrections.
    #:
    #: A set rather than a bool, so the panel offers the choice per tool and a
    #: third catalog write later is an entry rather than a second flag. Not
    #: persisted: it lasts as long as the window, which is the same rule the
    #: rest of this dataclass follows.
    auto_allow: frozenset[str] = frozenset()

    #: **Every write proceeds; `AskUserQuestion` still stops.** The user's call,
    #: 2026-09-04 (`docs/CONVERSATION.md` §14) — *"easier to review a finished
    #: task than each step along the way."*
    #:
    #: A separate flag rather than `record_step` joining :data:`AUTO_ALLOWABLE`,
    #: and the difference is the whole safety of it. That set is a list of things
    #: you can switch off one at a time and then forget you switched off; this is
    #: one mode, turned on for a task somebody is watching, and it is the reason
    #: the paragraph above stays true. **`record_step` still cannot be put in
    #: that set** — the argument there is about which writes are alike, and this
    #: is not a claim that they are.
    #:
    #: What that argument turned out not to cover is *when*. It defends the
    #: confirmation as the only moment anyone sees a step before it exists, and
    #: `can_use_tool` fires **before** the tool body runs — so what is shown is an
    #: unexecuted payload. In the 2026-09-04 session **8 of 14 approved
    #: `record_step` calls then errored**, which is fourteen interruptions buying
    #: six looks at something real. Moving the gate to after the run is a
    #: different and larger change (`BACKLOG.md` → Agent), and autopilot does not
    #: make it and must not be read as having made it.
    #:
    #: **Removes the pause, not the consequence.** `record_step` runs the op
    #: whether or not anybody approves it, so a step that produces a wrong table
    #: is now one you find in a diff. That is the trade, and it is why this is a
    #: mode and not the default. Not persisted, for the reason the rest of this
    #: dataclass is not: a mode that survived a restart is one you can be in
    #: without having chosen to be.
    autopilot: bool = False

    @property
    def mode(self) -> str:
        """Which approval mode the window is in, as the picker names it."""
        return AUTOPILOT if self.autopilot else ASK

    def set_mode(self, mode: str) -> None:
        """The picker's half of one setting: `autopilot` is the flag, this names it."""
        if mode not in MODES:
            return
        self.autopilot = mode == AUTOPILOT

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

    @property
    def on_add_data(self) -> bool:
        """Whether the add-data screen is the thing on screen.

        One rule, read by `app.shell` (which draws it) and by
        `prompt_for_decision` (which is only true *because* it is drawn). Two
        copies of it is how a popup ends up floating over a workspace saying the
        transcript is somewhere else.
        """
        return not self.left_add_data and not self.skipped_sources

    def prompt_for_decision(self, kind: str) -> bool:
        """The copilot has stopped for a human. Do they have to be invited in?

        The read now runs **while** you are still on the add-data screen, so the
        copilot can stop and ask with the transcript nowhere on screen. The form
        it asks with lives in the workspace, so something has to bridge that —
        and this records that a bridge is owed rather than building one.

        **It asks rather than teleports** (2026-08-07). This used to set
        ``left_add_data`` itself: a question arriving swapped the whole window
        for a workspace mid-click, while you were half-way through ticking the
        next batch of files. Moving screens is the human's move to make, so the
        answer here is a popup that says what is waiting and offers the way
        through — and ``False`` means they are already there and it is on screen
        anyway.

        A rule rather than two lines inside `turn._stop` because it is worth
        stating, and because stating it makes it testable without a browser.
        """
        if not self.on_add_data:
            return False
        self.decision_waiting = kind
        return True

    def enter_workspace(self) -> None:
        """Through to the three panes, with nothing left inviting you there."""
        self.decision_waiting = ""
        self.left_add_data = True

    def leave_project(self) -> None:
        """Drop the middle pane's tabs on the way out of a project.

        **An unsaved chart belongs to the project it was drawn in** *(2026-09-04,
        reported from a session)*. Its SQL names that project's tables and its
        rows came out of them, so a chart carried into the next project is a
        picture of data that project does not have, listed as unsaved beside
        figures it has nothing to do with. A saved one is a file under the old
        root and no better off. So both go, and the strip's arrangement with
        them: which half had focus and where the canvas sat are statements about
        a pane that no longer exists.

        Nothing is written and nothing is lost that cannot come back. A saved
        figure reopens from its file in its own project, and an unsaved chart's
        query is in that project's chat log, where the copilot can draw it again.
        """
        self.charts = []
        self.active = {LEFT: CANVAS, RIGHT: ""}
        self.focus_group = LEFT
        self.canvas_group = LEFT
        self.figures_closed = frozenset()
        self.figures_adding = None
        self.figures_new_name = ""

    # --- the middle pane's tabs (`docs/VISUALIZATION.md` §3) ----------------

    @property
    def tabs(self) -> list[Chart]:
        """Every open tab, both groups, in order. Tab zero is not one of them."""
        return [c for c in self.charts if not c.closed]

    def group_tabs(self, group: str) -> list[Chart]:
        """One group's chart tabs, in the order they sit on its strip."""
        return [c for c in self.charts if not c.closed and c.group == group]

    def keys(self, group: str) -> list[str]:
        """Every tab in a group as a key, **canvas first when it lives here**.

        The canvas keeps its place at the head of its own strip rather than
        taking a position in the order (§3.2 — always there, always first). It is
        the one tab you cannot lose, so it is the one whose position is worth
        more as a constant than as a preference.
        """
        keys = [c.key for c in self.group_tabs(group)]
        return ([CANVAS] + keys) if self.canvas_group == group else keys

    @property
    def is_split(self) -> bool:
        """Whether both halves have something in them.

        A group with no tabs is not drawn: closing the last thing in a half is
        how the split ends, which is the editor behaviour and means the ✕ on the
        right strip and the last close are one mechanism rather than two.
        """
        return bool(self.keys(LEFT)) and bool(self.keys(RIGHT))

    def showing(self, group: str) -> Chart | None:
        """The chart a group is drawing, or ``None`` — which means the canvas."""
        return self.chart(self.active.get(group, ""))

    def chart(self, key: str | None) -> Chart | None:
        """A tab by its key — a chart's name, or a saved figure's path."""
        return next((c for c in self.charts if c.key == key), None) if key else None

    def group_of(self, key: str) -> str:
        """Which half a tab is in. The canvas has its own field; ask that."""
        if key == CANVAS:
            return self.canvas_group
        chart = self.chart(key)
        return chart.group if chart else self.focus_group

    def tab_at(self, group: str, index: int) -> str | None:
        """The key of the ``index``-th tab on a group's strip.

        **How a drag says which tab it was about** (§3.7). The key itself would
        be the obvious thing to put in the DOM, and a chart's name is a sentence
        the agent wrote — quoting it into a NiceGUI prop is a parser and an
        escaping rule between here and the client, which is the seam that has
        cost this module two bugs already. A position is a small integer with
        nothing to escape, and it is stable for the length of a gesture: nothing
        reaches the server between the press and the drop.
        """
        keys = self.keys(group)
        return keys[index] if 0 <= index < len(keys) else None

    def show_chart(self, chart: Chart) -> Chart:
        """Put a chart on a strip and focus it, replacing one of the same key.

        **Reusing a name replaces** (§3.3) — in place, keeping its position and
        **its group**, because a corrected chart that jumped to the other half of
        the pane is a chart you have to go and find.

        **It always takes focus** (§3.4), with no rule distinguishing "you asked
        for this" from "I drew this on my way to something else". Such a rule is
        `KNOWLEDGE_GRAPH.md` §4.4's hazard in a new place: three situations
        nobody can tell apart from the outside.

        **A chart the agent drew is never a preview.** The preview tab is what a
        single click in the left pane opens, and it exists so that browsing does
        not fill the strip. Work the copilot did on purpose is not browsing.
        """
        existing = self.chart(chart.key)
        if existing is None:
            chart.group = self.focus_group
            self.charts.append(chart)
        else:
            chart.group = existing.group
            chart.preview = existing.preview
            self.charts[self.charts.index(existing)] = chart
        if chart.preview:
            self._replace_preview(chart)
        self.focus_tab(chart.key)
        return chart

    def focus_tab(self, key: str) -> None:
        """Show a tab **where it already is**, and make that group the current one.

        *(2026-09-03, reported from a session.)* Clicking a figure in the left
        pane used to make it the main pane's tab wherever it was showing — so
        clicking the one you were already reading in the split half moved it, and
        the half you had arranged went away. Opening a thing that is on screen is
        not a request to rearrange the screen.
        """
        group = self.group_of(key)
        self.active[group] = key
        self.focus_group = group

    def open_tab(self, key: str, *, preview: bool = False) -> None:
        """Open a tab from the left pane — as a preview, or for keeps.

        **VS Code's rule** *(2026-09-03, the user's call)*: a single click opens a
        preview and the next single click reuses that tab, so reading down a list
        of twenty figures costs one tab. A double click pins it. Anything that is
        already open is simply focused — an open tab is never demoted back to a
        preview by clicking its row again.
        """
        chart = self.chart(key)
        if chart is None:
            return
        if chart.closed:
            chart.closed = False
            chart.group = self.focus_group
            chart.preview = preview
            if preview:
                self._replace_preview(chart)
        elif not preview:
            chart.preview = False
        self.focus_tab(chart.key)

    def pin_tab(self, key: str) -> None:
        """Stop a tab being the preview — a double click, or an edit to it."""
        chart = self.chart(key)
        if chart is not None:
            chart.preview = False

    def _replace_preview(self, keeping: Chart) -> None:
        """One preview per group. The old one goes rather than accumulating.

        **A saved one is dropped and an unsaved one is only closed**, and the
        asymmetry is the same one the gallery draws: a figure is a file, so
        letting go of it costs a read to get back; a session chart is the only
        copy there is, and retiring it would make browsing destroy work.
        """
        for chart in list(self.charts):
            if chart is keeping or not chart.preview or chart.group != keeping.group:
                continue
            if chart.saved:
                self.retire_chart(chart.key)
            else:
                self.close_chart(chart.key)

    def retire_chart(self, key: str) -> None:
        """Drop a tab from the session entirely: its tab and its row.

        **Two things used to survive a save** *(2026-09-03)*: the session chart,
        still on the strip and still in the gallery marked unsaved, and the file
        it had just been written to. That is one picture claiming to be two
        things, and the wrong one is the one you would go on working with. What
        settled it is `figure_saved`, which turns the one chart into the file;
        this is what discarding an unsaved chart does, and what a deleted figure
        does to the tab that was showing it.
        """
        group = self.group_of(key)
        self.charts = [c for c in self.charts if c.key != key]
        self._after_close(key, group)

    def close_chart(self, key: str) -> None:
        """Shut a tab, keeping the chart in the left pane's list (§3.5).

        A closed chart cost a query and two minutes of the copilot's attention,
        so the gallery still lists it and clicking reopens it. That is what makes
        closing cheap enough to do.
        """
        chart = self.chart(key)
        if chart is None:
            return
        chart.closed = True
        chart.preview = False
        self._after_close(key, chart.group)

    def _after_close(self, key: str, group: str) -> None:
        """Where focus lands, and what happens to a group that is now empty.

        **The neighbour, not the canvas** *(2026-09-03)*. Falling back to tab zero
        was defensible while there was one strip — it is the one destination that
        always exists — and it is not what an editor does, which is what the user
        asked for. The tab beside the one you shut is where you were looking.

        A group with nothing left in it stops being drawn, and if that was the
        left one the right one slides over: two empty halves is not a layout, and
        a right-hand group beside a gap is a split with nothing on one side of it.
        """
        if self.active.get(group) != key:
            return
        keys = self.keys(group)
        self.active[group] = keys[0] if keys else ""
        if keys:
            return
        self.focus_group = LEFT
        if group == LEFT:
            for chart in self.charts:
                chart.group = LEFT
            if self.canvas_group == RIGHT:
                self.canvas_group = LEFT
            self.active[LEFT] = self.active.get(RIGHT, "")
            self.active[RIGHT] = ""

    def reopen_chart(self, key: str) -> None:
        """Put a closed tab back on the strip and focus it."""
        self.open_tab(key)

    def figure_saved(self, chart: Chart, path: str) -> None:
        """The chart becomes the file it was just written to (§6.4).

        One artifact, which is what retiring the chart was after, and **the tab
        survives**, which retiring it got wrong: the picture on screen is the
        picture that was saved, and taking it away at the moment you keep it is
        the window disagreeing with the press.

        Saving is also what pins a preview. You have said this one is worth
        keeping; a tab that vanished on the next click would be arguing.
        """
        was = chart.key
        chart.path = path
        chart.keeping = False
        chart.keep_error = ""
        chart.preview = False
        self._rekey(was, chart.key)

    def figure_moved(self, old: str, new: str) -> None:
        """A figure dragged into a folder, and the tab showing it goes along."""
        chart = self.chart(old)
        if chart is None:
            return
        chart.path = new
        self._rekey(old, new)

    def _rekey(self, old: str, new: str) -> None:
        for group, key in self.active.items():
            if key == old:
                self.active[group] = new

    def show_canvas(self) -> None:
        """Back to tab zero, wherever it is."""
        self.focus_tab(CANVAS)

    def move_tab(self, key: str, group: str, at: int | None = None) -> None:
        """Put a tab in a group, at a position — the whole of drag and drop.

        One operation for three gestures, because they are one gesture: dragging
        a tab along its own strip reorders it, dragging it to the other strip
        moves it, and dragging it to a half that has no strip yet opens one. The
        client decides which of those it was and this does not have to know.

        **Nothing is left in both halves.** A tab has one group, so the split
        cannot be a way to have one picture in two places — which is §6.4's rule,
        and it is no better when the two places are side by side.
        """
        if group not in GROUPS:
            return
        if key == CANVAS:
            self._move_canvas(group)
            return
        chart = self.chart(key)
        if chart is None or chart.closed:
            return
        was = chart.group
        self.charts.remove(chart)
        chart.group = group
        chart.preview = False
        self._insert(chart, group, at)
        self.focus_tab(key)
        if was != group:
            self._after_close(key, was)

    def _insert(self, chart: Chart, group: str, at: int | None) -> None:
        """Splice a tab into the flat list at a position *within its group*.

        `charts` is one list holding both groups, so a position on a strip is not
        an index into it. Rebuilding the list around the neighbour is shorter than
        keeping two lists in step, and two lists is how the pane ends up with a
        tab in neither.
        """
        siblings = self.group_tabs(group)
        offset = 1 if self.canvas_group == group else 0
        cut = None if at is None else max(0, at - offset)
        if cut is None or cut >= len(siblings):
            self.charts.append(chart)
            return
        self.charts.insert(self.charts.index(siblings[cut]), chart)

    def _move_canvas(self, group: str) -> None:
        """Tab zero across the split. It cannot close, so it can only move.

        If it was the only thing in its half, that half goes with it — the same
        rule every other last tab obeys.
        """
        was = self.canvas_group
        if was == group:
            self.focus_tab(CANVAS)
            return
        self.canvas_group = group
        self.focus_tab(CANVAS)
        self._after_close(CANVAS, was)

    def split(self, key: str) -> None:
        """Send a tab to the right half (§3.7), which is a move to that group."""
        self.move_tab(key, RIGHT)

    def unsplit(self) -> None:
        """One pane again: everything comes back to the left, nothing is closed.

        The ✕ on the right strip. It shuts a *half*, and a half is a layout — so
        what it does is put the tabs back, never throw them away.
        """
        for chart in self.charts:
            chart.group = LEFT
        self.canvas_group = LEFT
        if not self.active.get(LEFT):
            self.active[LEFT] = self.active.get(RIGHT, "") or CANVAS
        self.active[RIGHT] = ""
        self.focus_group = LEFT

    @property
    def busy(self) -> bool:
        """An exchange is live **anywhere**. Starting a second would interleave two."""
        return self.live is not None

    @property
    def live(self) -> Chat | None:
        """The one chat with a message in flight, if any (§3.1)."""
        return next((chat for chat in self.chats if chat.busy), None)

    @property
    def live_job(self) -> Chat | None:
        """The running job, if the live exchange is one. What the add-data screen asks."""
        live = self.live
        return live if live is not None and live.is_job else None

    @property
    def waiting(self) -> list[Chat]:
        """Every chat stopped on a decision — what the badges draw (§3.5)."""
        return [chat for chat in self.chats if chat.pending is not None]

    def chat_at(self, path: Path) -> Chat | None:
        """The in-process chat holding this log, if it has been opened here."""
        wanted = Path(path).resolve()
        return next(
            (c for c in self.chats if c.path is not None and Path(c.path).resolve() == wanted),
            None,
        )

    @property
    def default_model(self) -> str:
        """The model a chat starts on when none was picked: the **provider's** default.

        Not one global default. A Claude name sent to Ollama is a guaranteed
        refusal, so switching provider moves the model with it.
        """
        return providers.get(self.provider or providers.DEFAULT_KIND).default_model

    def new_chat(self, kind: str = GOAL, **fields: Any) -> Chat:
        """A fresh chat or job, holding this window's current provider, model and effort."""
        self.chat_count += 1
        chat = Chat(kind=kind, ident=f"chat-{self.chat_count}", **fields)
        chat.provider = chat.provider or self.provider
        chat.model = chat.model or self.model
        chat.effort = chat.effort if "effort" in fields else self.effort
        self.chats.append(chat)
        return chat

    def show_chat(self, chat: Chat | None) -> None:
        """Put one chat on screen, or none — back to the list.

        Leaving a chat that never sent anything drops it: there is no log, so
        nothing lists it, and a client was never opened for it.
        """
        leaving = self.open
        self.open = chat
        if chat is None:
            self.right = LIST
        if leaving is not None and leaving is not chat and not leaving.started:
            self.chats.remove(leaving)

    def show_sources(self) -> None:
        self.show_chat(None)
        self.right = SOURCES

    def forget(self, chat: Chat) -> None:
        """Drop a chat from the window — after its log is gone, or its client closed."""
        if chat in self.chats:
            self.chats.remove(chat)
        if self.open is chat:
            self.show_chat(None)

    def focus(self, model: str) -> None:
        """Ask the canvas to bring ``model`` into view on the next render."""
        self.focus_model = model
        self.focus_token += 1

    def pick_step(self, step_id: str) -> None:
        """Open a step's block in the run report, or shut the one already open.

        The token is bumped either way, so opening the *same* block again after
        shutting it is a new request rather than one the client has already
        acted on. Nothing carries it while nothing is open, which is what makes
        shutting a block leave the report exactly where you were reading.
        """
        self.selected_step = None if self.selected_step == step_id else step_id
        self.step_token += 1

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
        """Pick what tab zero is about, and go there.

        **Selecting also returns to tab zero** (`docs/VISUALIZATION.md` §3.2).
        The left pane and the tab strip are two ways of saying what is on screen,
        and a click on a source that changed tab zero while a chart stayed in
        front of it would be the pane ignoring you. The reverse is not true:
        picking a chart tab leaves the selection alone, so closing it drops you
        back on what you were reading rather than somewhere you never chose.

        A saved figure is the exception and does not come through here: it *is* a
        tab (§6.4), so clicking one in the gallery opens that tab rather than
        replacing what tab zero is about.
        """
        self.selection = None if kind is None else (kind, name)
        self.focus_tab(CANVAS)

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

    def toggle_row(self, key: str, on: bool) -> None:
        """Remember that a transcript disclosure is open, or that it is shut.

        No refresh: the expansion has already opened itself in the browser, and
        rebuilding the pane under the click that opened it is how the panel
        loses the scroll position of the thing being read.
        """
        self.open_rows = (self.open_rows | {key}) if on else (self.open_rows - {key})

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

    def start_exchange(
        self,
        prompt: str,
        *,
        model: str,
        effort: str | None,
        kind: str = GOAL,
        label: str = "",
        chat: Chat | None = None,
        provider: str | None = None,
    ) -> Chat:
        """Begin an exchange: in ``chat``, else in the open chat, else in a new one.

        A **goal** sent with nothing open starts a chat and opens it — you sent
        it from the list's composer, and the reply belongs in front of you. A
        **job** always gets a chat of its own and never takes the screen
        (`docs/CHAT_SESSIONS.md` §3.5): it is a row in the list with the running
        dot, and the add-data screen already says it is reading.
        """
        job = kind in JOB_KINDS
        if chat is None:
            chat = (
                self.open if not job and self.open is not None and self.open.continuable else None
            )
        if chat is None:
            chat = self.new_chat(
                kind, model=model, effort=effort, provider=provider or self.provider
            )
        # The chat's own provider wins once it has one: a parked client cannot
        # change server, and the control is not drawn for it.
        provider = chat.provider or provider or self.provider
        exchange = Exchange(
            prompt=prompt, model=model, effort=effort, kind=kind, label=label, provider=provider
        )
        chat.exchange = exchange
        chat.exchanges.append(exchange)
        chat.model = model
        chat.provider = provider
        if not job:
            self.open = chat
        return chat


#: The one open project. See the module docstring for why it is a singleton.
APP = App()

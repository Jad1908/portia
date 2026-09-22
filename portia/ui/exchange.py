"""Driving a copilot exchange from the window.

V0 drives; it is not a viewer. `agent/ask.py` injects ``answer`` and ``confirm``
as callables precisely so something other than stdin can supply them — its
docstring names this panel as the third consumer. So there is no engine change
here: a form-backed ``answer`` and a payload-rendering ``confirm``, and every
question and every write confirmation lands on screen.

**Many chats, one in flight** (`docs/CHAT_SESSIONS.md` §3.1). Each `state.Chat`
holds its own client and its own log, and the callbacks are made *per chat*
(`_callbacks`), so a question lands in the rows of the chat that asked it by
identity rather than in whichever chat happens to be busy. `APP.busy` stays
global: a second message anywhere is refused rather than queued
(`CONVERSATION.md` §7).

The one thing to know about ordering. ``ask.build_can_use_tool`` emits its
question event into a buffer that `Conversation.send` drains from the *outer*
loop — which is blocked on the message stream while the callback waits for an
answer. So the form is rendered from inside the callback, exactly as
`cli/chat.py` prints from inside ``answer_questions``. Waiting for the yielded
event instead would deadlock: the event that renders the form only arrives once
the form has been answered.

Those two callbacks therefore append their own rows, and `_record` drops the
question/answer/approval events when they later arrive, so the transcript keeps
one entry per thing that happened, in the order it happened.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

from portia import runlog
from portia.agent import events, providers
from portia.core import feedback
from portia.ui import engine, state
from portia.ui.state import APP, Chat, Decision

#: Which history a kind of work is logged into. `GOAL` is a conversation you
#: started; `INDEXING` and `REREAD` are jobs the app ran on your behalf, and
#: `docs/CONVERSATION.md` §3 is why those are two folders rather than one. The
#: list draws both and tells them apart by glyph (`CHAT_SESSIONS.md` §3.2).
_LOG_KIND_FOR = {
    state.GOAL: runlog.CHAT,
    state.INDEXING: runlog.INDEXING,
    state.REREAD: runlog.INDEXING,
}

#: Why a chat from the list cannot be continued (`state.Chat.legacy`). Three
#: reasons and the composer's place says which, because a read-only chat with
#: no explanation reads as a broken one.
LEGACY_PRE_RENAME = "written before chats could be picked up"
LEGACY_NO_SESSION = "no session id was recorded for it"
LEGACY_REFUSED = "the SDK could not resume it: {error}"
#: A chat somebody else drove (`runlog.HOSTED`). Not legacy in age, and read-only
#: for the plainest reason of the four: the conversation is not portia's to
#: continue, and the tool calls are the only part of it that came through here.
LEGACY_HOSTED = "it was held in {host} and goes on there; these are its tool calls"

#: What the composer says while a local model loads before the first message.
_LOADING = "loading {model}"

#: Kinds the panel has **already drawn from inside the callback that produced
#: them** — they arrive as a `Decision` the pane is rendering, so appending the
#: event too would draw the same moment twice.
#:
#: An **automatic** write is the exception and is kept (`_owned`): no `Decision`
#: is ever made for one, because the loop did not stop, so the event is the only
#: record the panel has that a durable write happened. Dropping it lost the row
#: entirely — the write went to disk and the transcript said nothing.
_OWNED = (events.QUESTION, events.ANSWER, events.APPROVAL)

#: Tool results are where a durable artifact changes on disk, so the panes catch
#: up there — the graph fills in as steps are recorded, not after the turn.
_SYNC_ON = (events.TOOL_RESULT, events.RESULT)


async def start(
    prompt: str,
    *,
    model: str,
    effort: str | None,
    kind: str = state.GOAL,
    label: str = "",
    chat: Chat | None = None,
    provider: str | None = None,
    show: bool = False,
) -> None:
    """Send one message, streaming its events into a chat's transcript.

    ``show`` puts the chat on screen as it starts. A goal is shown anyway; this
    is for a **job started by a press in the right pane**, which is the one
    case where opening it is what the person asked for. A job never takes the
    screen by itself (`CHAT_SESSIONS.md` §3.5), and it still does not: the
    add-data screen's read starts without anyone pressing anything in this
    pane, and passes nothing.

    **A goal continues a chat; a job is one-shot** (`CONVERSATION.md` §6). The
    same recording, the same callbacks, the same log tee — but a chat keeps its
    client and its file between messages, and a job opens and closes both.

    **A chat picked from the list is resumed on its first send**
    (`CHAT_SESSIONS.md` §3.3): its log is reopened for append and its client is
    connected with the recorded session id. Opening it to read costs nothing;
    the resume happens here, when there is something to say.

    **The provider is asked first** (`PROVIDERS.md` §4.5, §4.6). A local model
    is loaded before the message goes, and a refusal, in the server's own
    words or with the context it loaded with, is drawn in the composer and
    opens no exchange: nothing was sent, so there is nothing to log.
    """
    from portia.ui import transcript

    if APP.busy or APP.preflight_status:
        return

    try:
        from portia.agent import session
    except ImportError as exc:  # the `agent` extra isn't installed
        _fail_before_starting(prompt, model, effort, kind, label, chat, exc)
        return

    provider = provider or (chat.provider if chat is not None and chat.provider else APP.provider)
    effort = spend_effort(provider, effort)
    if not await _preflight(provider, model):
        return

    chat = APP.start_exchange(
        prompt, model=model, effort=effort, kind=kind, label=label, chat=chat, provider=provider
    )
    exchange = chat.exchange
    assert exchange is not None
    if show:
        APP.show_chat(chat)
    transcript.pane.refresh()

    # The window's copy dies with the window; this is the durable one
    # (`portia/runlog.py`). Teed here, at the edge, for the same reason the CLI
    # tees in `run_turn` — the engine must not learn it is being observed.
    _open_log(chat, kind, prompt)

    try:
        if chat.is_job:
            # `session.run`'s shape, opened here rather than through it, so the
            # job's conversation is on the chat while it runs and Stop has
            # something to reach (`interrupt`). Before this a job had no
            # handle: the button interrupted nothing and the copilot's tool
            # threads ran on, on the meter, after the pane said it had ended.
            answer, confirm = _callbacks(chat)
            chat.job = session.Conversation(
                answer=answer,
                confirm=confirm,
                auto_allow=auto_allow,
                model=model,
                effort=effort,
                cwd=str(APP.root),
                portia_dir=APP.portia_dir,
                provider=provider,
            )
            async with chat.job as job:
                await _drain(job.send(prompt), chat)
        else:
            await _open_chat(chat, session, model=model, effort=effort, provider=provider)
            await _drain(chat.conversation.send(prompt), chat)
    except asyncio.CancelledError:
        exchange.error = "interrupted"
        raise
    except Exception as exc:  # noqa: BLE001 — shown to the operator, not swallowed
        exchange.error = f"{type(exc).__name__}: {exc}"
        exchange.problem = feedback.remember(exc, "indexing" if chat.is_job else "chat")
        if chat.resumed and exchange.subtype is None:
            await _refuse_resume(chat, exc)
    finally:
        exchange.running = False
        chat.job = None
        _resolve_orphans()
        _sync_artifacts()
        if not chat.is_job:
            await _read_context(chat)
        transcript.pane.refresh()


def _open_log(chat: Chat, kind: str, prompt: str) -> None:
    """The chat's file: opened on the first message, reopened on a resume.

    **Appending, never a second file** (`CHAT_SESSIONS.md` §3.9). A chat opened
    from the list has a path and no log; `runlog.resume` reopens that path and
    marks where this process picked it up.
    """
    if chat.log is not None:
        return
    if chat.path is not None:
        chat.log = runlog.resume(chat.path, APP.catalog_dir, provider=chat.provider or None)
        return
    # The catalog dir in full, so the path this chat holds is the one the list
    # reads back through `engine.logs_in` — a relative `.portia` here and an
    # absolute one there made `App.chat_at` miss, and the row lost its dot.
    chat.log = runlog.start(
        APP.catalog_dir,
        cwd=str(APP.root),
        kind=_LOG_KIND_FOR.get(kind, runlog.CHAT),
        provider=chat.provider or None,
    )
    chat.path = chat.log.path
    # Off the prompt, not the listing: the `PROMPT` event is written by the
    # drain, after this, so the listing would still read the file's stem.
    chat.title = chat.title or runlog.first_line(prompt) or chat.path.stem


async def _drain(stream_of_events, chat: Chat) -> None:
    async for event in stream_of_events:
        _record(event, chat)


def spend_effort(provider: str, effort: str | None) -> str | None:
    """The effort a send carries: the picked one, or none where the provider ignores it.

    The picker stops drawing effort on such a provider, but `App.effort` still
    holds the last value picked for Claude, and `session.build_options` refuses
    an effort it would have to drop silently. Until 2026-09-08 that refusal
    was the whole of what a send on Ollama did in the window: the exchange died
    before the client started, and the pane, which drew a failed exchange only
    for a job, said *no messages yet*.
    """
    return effort if providers.get(provider).honours_effort else None


async def _preflight(provider: str, model: str) -> bool:
    """Load and measure before sending; draw the refusal where the message was typed.

    The composer says what is loading for the duration, on the Send button's
    side, because a local model's first load is seconds and a button that does
    nothing for ten of them reads as broken. Only a provider that ``warms``
    gets the line: a remote API's preflight is nothing, and a status for
    nothing would flash on every send.
    """
    from portia.ui import transcript

    APP.spend_alert = None
    if providers.get(provider).warms:
        APP.preflight_status = _LOADING.format(model=model)
        transcript.pane.refresh()
    try:
        check = await engine.preflight(APP, provider, model)
    except Exception as exc:  # noqa: BLE001 — shown where the message was typed, not swallowed
        check = providers.Preflight(False, reason=f"{type(exc).__name__}: {exc}")
    finally:
        APP.preflight_status = ""
    if not check.ok:
        APP.spend_alert = (check.reason, check.remedy)
        transcript.pane.refresh()
        return False
    return True


async def _open_chat(chat: Chat, session, *, model: str, effort: str | None, provider: str) -> None:
    """Reuse this chat's client, or start one. Switch models if asked.

    **Effort is not switchable** and the composer says so: it is a
    `ClaudeAgentOptions` field, so it is fixed for the life of a client, and the
    SDK has no runtime equivalent of `set_model` for it. A chat picked up from
    disk gets a *new* client, so effort is choosable again there (§3.3).
    """
    if chat.conversation is None:
        answer, confirm = _callbacks(chat)
        resume = chat.session_id if chat.logged is not None else None
        chat.resumed = resume is not None
        chat.conversation = session.Conversation(
            answer=answer,
            confirm=confirm,
            auto_allow=auto_allow,
            model=model,
            effort=effort,
            cwd=str(APP.root),
            portia_dir=APP.portia_dir,
            resume=resume,
            provider=provider,
        )
        await chat.conversation.connect()
    elif chat.conversation.model != model:
        await chat.conversation.set_model(model)


async def _refuse_resume(chat: Chat, exc: Exception) -> None:
    """The SDK would not pick the session up. The chat is legacy from here.

    Read-only rather than silently a fresh session: a new client that answered
    from nothing would look like the same chat having forgotten everything.
    """
    chat.legacy = LEGACY_REFUSED.format(error=exc)
    await close_chat(chat)


async def _read_context(chat: Chat) -> None:
    """How full the chat is, asked once per exchange rather than per render.

    A **fact** — token counts are measured, so a surface may show them
    (`CONVERSATION.md` §9). No policy sits on top of it; §13 is why.
    """
    if chat.conversation is None:
        return
    try:
        chat.context = await chat.conversation.context_usage()
    except Exception:  # noqa: BLE001 — a number for the corner, never worth failing over
        chat.context = None


def _fail_before_starting(prompt, model, effort, kind, label, chat, exc: Exception) -> None:
    """The `agent` extra isn't installed. Say so where the transcript is read."""
    from portia.ui import transcript

    chat = APP.start_exchange(prompt, model=model, effort=effort, kind=kind, label=label, chat=chat)
    assert chat.exchange is not None
    chat.exchange.error = f"{type(exc).__name__}: {exc}"
    chat.exchange.running = False
    transcript.pane.refresh()


async def interrupt() -> None:
    """Stop the message in flight. **The only way to cut one short** (§7).

    Nothing has to be resolved first — the SDK cancels the parked `can_use_tool`
    task itself and a result arrives (§8, measured). What this owes is the
    *reaction*: `_resolve_orphans` runs when the exchange ends, and the
    transcript draws a cancelled decision as interrupted rather than as a form
    still waiting to be filled in.
    """
    live = APP.live
    handle = live.conversation or live.job if live is not None else None
    if handle is not None:
        await handle.interrupt()


def open_from_disk(path: Path) -> Chat:
    """Put a logged chat on screen, reading it once (`CHAT_SESSIONS.md` §3.3).

    If this process already holds it, that chat is shown — its client may be
    parked and its rows are in memory. Otherwise the log is read back and drawn
    through `transcript.replay`, and nothing is connected: opening a chat to
    read it must cost nothing, and the resume waits for the first send.
    """
    held = APP.chat_at(path)
    if held is not None:
        APP.show_chat(held)
        return held
    logged = engine.read_log(path)
    listing = engine.log_listing(APP, path)
    summary = engine.log_summary(logged)
    kind = state.INDEXING if logged.kind == runlog.INDEXING else state.GOAL
    legacy = ""
    if listing["legacy"]:
        legacy = LEGACY_PRE_RENAME
    elif listing.get("host"):
        legacy = LEGACY_HOSTED.format(host=runlog.host_label(listing["host"]))
    elif kind == state.GOAL and not summary.get("session_id"):
        legacy = LEGACY_NO_SESSION
    chat = APP.new_chat(
        kind,
        path=path,
        title=listing["title"],
        model=listing.get("model") or APP.model,
        effort=listing.get("effort"),
        provider=listing.get("provider") or providers.DEFAULT_KIND,
        session_id=summary.get("session_id"),
        logged=logged,
        prior=summary,
        legacy=legacy,
        host=listing.get("host") or "",
    )
    APP.show_chat(chat)
    return chat


async def close_chat(chat: Chat) -> None:
    """Release this chat's client. The rows and the log stay; the next send resumes."""
    conversation, chat.conversation = chat.conversation, None
    if conversation is not None:
        await conversation.close()
    if chat.logged is None and chat.path is not None:
        # This process wrote the log; the next send goes through `resume`, so
        # the chat has to look like one opened from disk.
        chat.logged = engine.read_log(chat.path)
        chat.prior = engine.log_summary(chat.logged)
        chat.session_id = chat.prior.get("session_id") or chat.session_id
        chat.rows = []
        chat.exchanges = []
        chat.timings = {}
        chat.settled = 0
        chat.settled_slot = None
        chat.generation += 1
    chat.log = None


async def delete_chat(chat: Chat) -> bool:
    """Remove a chat: its client, its log, its name. Refused while it is in flight."""
    if chat.busy:
        return False
    await close_chat(chat)
    if chat.path is not None:
        engine.delete_log(APP, chat.path)
    APP.forget(chat)
    return True


async def close_all() -> None:
    """Every open chat, on the way out of a project or the window."""
    for chat in APP.chats:
        conversation, chat.conversation = chat.conversation, None
        if conversation is not None:
            await conversation.close()


def _owned(event: events.Event) -> bool:
    """Whether the panel drew this from inside the callback that made it."""
    if event.kind == events.APPROVAL:
        return not event.data.get("auto")
    return event.kind in _OWNED


def _record(event: events.Event, chat: Chat) -> None:
    from portia.ui import transcript

    # Logged before the panel's own bookkeeping: the events the transcript drops
    # are ones it has *already rendered* from inside the callback that produced
    # them, and a log missing every question and every write confirmation would
    # be missing the decisions the run is worth reading for.
    if chat.log is not None:
        chat.log.event(event)

    if _owned(event):
        return
    if event.kind == events.PROMPT and chat.exchange is not None:
        chat.exchange.opened = True
    if event.kind == events.RESULT and chat.exchange is not None:
        chat.exchange.subtype = event.data.get("subtype")
        # The log keeps the engine's number; the window draws it only where it
        # means something (`Provider.metered`). On Ollama the binary priced
        # qwen2.5:3b as if it were Claude and the pane said *~$0.0076*.
        metered = providers.get(chat.provider or providers.DEFAULT_KIND).metered
        chat.exchange.cost_usd = event.data.get("cost_usd") if metered else None
        totals = runlog.token_totals(event.data.get("usage") or {})
        chat.exchange.input_tokens = totals["input_tokens"]
        chat.exchange.cached_tokens = totals["cached_tokens"]
        chat.exchange.output_tokens = totals["output_tokens"]
        chat.session_id = event.data.get("session_id") or chat.session_id
    chat.rows.append(event)
    _clock(event, chat)
    if event.kind in _SYNC_ON:
        _sync_artifacts()
    # The rows and nothing else. The whole pane refreshed here for a year, and
    # what that cost was the composer: rebuilding it per event destroyed the
    # textarea a few times a second, caret and focus with it, while somebody was
    # writing the follow-up §7 calls the normal case. The chrome around the rows
    # changes at human pace and refreshes at those moments (`transcript.pane`).
    #
    # And of the rows, **only the tail** *(2026-09-07)*: what has settled is
    # appended to the slot the pane keeps, and what is still moving is redrawn
    # (`transcript.stream_view`). A chat that is not on screen has no slot and
    # nothing to redraw; its row in the list carries the dot.
    if chat is not APP.open:
        return
    if transcript.settle(chat):
        transcript.tail_view.refresh()
    else:
        transcript.stream_view.refresh()


def _clock(event: events.Event, chat: Chat) -> None:
    """Start a call's clock as it goes out; stop it as its result lands.

    Keyed by the SDK's call id, the one identity a call keeps through every
    regrouping the transcript does. Measured here, at the edge where the log
    stamps the same moment, and nowhere in the engine.
    """
    call_id = str(event.data.get("id") or "")
    if not call_id:
        return
    if event.kind == events.TOOL_CALL:
        chat.timings[call_id] = state.Timing()
    elif event.kind == events.TOOL_RESULT and call_id in chat.timings:
        chat.timings[call_id].ended = time.monotonic()


# --- the two moments the loop stops for the human ---------------------------


def _callbacks(chat: Chat):
    """``answer`` and ``confirm`` for one chat, so a decision lands in *its* rows.

    Made per chat rather than found per call: with several chats open, "the
    busy one" is a search that is right only while there is one, and the
    identity is known here for free (`CHAT_SESSIONS.md` §3.1).
    """

    async def answer(questions: list[dict]) -> dict[str, Any]:
        """Render the copilot's questions as a form and wait for the human."""
        from portia.ui import transcript

        decision = _stop(chat, events.QUESTION, {"questions": questions})
        answers = await decision.future
        transcript.pane.refresh()
        return answers

    async def confirm(tool_name: str, tool_input: dict) -> bool:
        """Lay out a pending write and wait for allow or deny.

        Only reached for a write the human has **not** switched off:
        `auto_allow` is handed to `ask.py`, which never calls this for one of
        those. The check is there and not here on purpose — a write that does
        not stop is not a confirmation this function answered instantly, and
        `ask.py` is the module that decides whether the loop stops.
        """
        from portia.ui import transcript

        decision = _stop(chat, events.APPROVAL, {"name": tool_name, "input": tool_input})
        allowed = await decision.future
        transcript.pane.refresh()
        return bool(allowed)

    return answer, confirm


def auto_allow(tool_name: str) -> bool:
    """Whether this write runs without asking, per the window's own setting.

    Read at the moment of the call rather than captured when the chat opened, so
    turning it on mid-conversation applies to the next write — `Conversation`
    holds one client across exchanges (`docs/CONVERSATION.md` §4), and a
    preference frozen at connect time would be one you have to end the chat to
    change. That is what lets autopilot be turned **off** mid-run too, which is
    the direction that matters.

    **Autopilot is a third state, not a third entry in `AUTO_ALLOWABLE`**
    (`CONVERSATION.md` §14). The set stays what it was — two catalog writes the
    human may forget they switched off — and this is one deliberate mode that
    lets every write through, including `record_step`. `AskUserQuestion` is
    unaffected because it is not a write and never reached here: `ask.py`
    branches on it before the confirmation path exists. That is by construction
    rather than by remembering to exclude it, which is the only reason a mode
    like this is safe to have at all.
    """
    return APP.autopilot or events.tool_label(tool_name) in APP.auto_allow


def _stop(chat: Chat, kind: str, payload: dict) -> Decision:
    """Park a decision in its chat's rows, and say so — without moving anyone.

    The loop is now blocked on a human. **Nothing switches to the chat that
    asked** (`CHAT_SESSIONS.md` §3.5): the row in the list and the header of
    whatever chat is on screen carry a dot, and the human comes when they come.
    Following the decision pulled the composer out from under whoever was
    typing in another chat.

    One bridge stays. If the human is still on the add-data screen, no
    transcript is on screen at all, so they are **invited** to come and see it
    (`state.prompt_for_decision`) — an invitation, not a switch.
    """
    from portia.ui import screens, transcript

    decision = Decision(kind, payload, asyncio.get_running_loop().create_future())
    chat.rows.append(decision)
    if APP.prompt_for_decision(kind):
        screens.offer_workspace()
    transcript.pane.refresh()
    return decision


def _resolve_orphans() -> None:
    """A turn that ended mid-question leaves a form nobody can answer.

    It happens when the stream errors or the SDK gives up while the callback is
    still waiting. Cancel the future rather than leave the panel showing a
    decision that will never be read.
    """
    for chat in APP.chats:
        for row in chat.rows:
            if isinstance(row, Decision) and not row.resolved and not row.future.done():
                row.future.cancel()


# --- keeping the other panes honest -----------------------------------------


def listen_for_charts() -> None:
    """Draw the charts the copilot publishes, in the tab strip.

    **The one place `agent/drawn` meets the window.** `plot_data`'s rows do not
    come back through the tool result — the model gets a receipt, and 5,000 rows
    of text would be refused by `tools.RESULT_BUDGET` anyway
    (`docs/VISUALIZATION.md` §2.3) — so they arrive by subscription instead.

    **The hop onto the loop is here** and not in `agent/tools.py`, because the
    sink runs on the worker thread `_evidence` put the handler on, and NiceGUI's
    element tree may only be touched from the loop that owns it. `ui/engine.py`
    already holds that rule for the builder's callbacks; this is the same rule for
    the same reason, in the module that owns the app's side of the seam. Nothing
    in `agent/` learns that the app has an event loop.

    Registered per page, so the second browser tab on one project gets its charts
    too — `drawn.subscribe` keeps a list for exactly that.
    """
    from portia.agent import drawn
    from portia.ui import artifacts, workflow

    loop = asyncio.get_event_loop()

    def show(chart: dict) -> None:
        APP.show_chart(
            state.Chart(
                name=chart["tab"],
                question=chart["question"],
                vega=chart["vega"],
                rows=chart["rows"],
                columns=chart["columns"],
                sql=chart["sql"],
                inputs=chart["inputs"],
            )
        )
        workflow.pane.refresh()
        artifacts.pane.refresh()

    def on_chart(chart: dict) -> None:
        loop.call_soon_threadsafe(show, chart)

    drawn.subscribe(on_chart)


def note_chart_failed(chart: state.Chart) -> None:
    """Put a failed render in the chat's log (`VISUALIZATION.md` §11.2).

    **Teed at the edge, like everything else `runlog` holds.** The rule is that
    the log is written where the app and the engine meet and never inside the
    engine, and this is the one record that travels the other way — the browser
    tells the server something happened. It goes through the same `Log` and lands
    beside the `plot_data` call it belongs to.

    Best effort and never fatal: there may be no chat open at all, because a
    figure opened from the gallery can fail to draw with nobody talking to the
    copilot.
    """
    chat = APP.open
    if chat is None or chat.is_job or chat.log is None:
        return
    chat.log.write(runlog.CHART_FAILED, {"tab": chart.key, "message": chart.error or ""})


def _sync_artifacts() -> None:
    """Re-read what the copilot may have just written — and redraw only if it did.

    **The stamp is what keeps the window still.** This runs after every tool
    result, and most of the copilot's calls are questions: a `query_data`, a
    `describe_source`, a `graph_lookup` write nothing, and redrawing three panes
    to show the same pixels flashed the whole window on every one of them —
    worst where a pane holds an async renderer, because a rebuilt Vega chart
    blanks and repaints however identical it is. `engine.artifact_stamp` is the
    files those panes draw from; when it has not moved, nothing has, and the
    panes are left exactly as the reader has them.
    """
    from portia.ui import app as app_module
    from portia.ui import artifacts, workflow

    # The stamp first *(2026-09-07)*. It is a walk of stat calls; the catalog
    # reload under it parses every source's YAML, and it ran on every tool
    # result whether or not anything had been written — 16 ms on six sources,
    # on the loop, per `describe_source`. The stamp covers the catalog files,
    # so an unchanged stamp means there is nothing to reload.
    stamp = engine.artifact_stamp(APP)
    if stamp == APP.artifact_stamp:
        return
    APP.artifact_stamp = stamp
    engine.refresh_catalog(APP)
    if APP.spec_path is None:
        specs = engine.specs_in(APP)
        if specs:
            engine.select_spec(specs[0], APP)
    else:
        engine.reload_spec(APP)
    artifacts.pane.refresh()
    workflow.pane.refresh()
    app_module.run_controls.refresh()

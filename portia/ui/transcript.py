"""Right pane — the copilot.

A composer at the foot and the event stream above it, live over the websocket as
`session.run` yields it. Model and effort are pickable in the composer's own bar,
and the chat states what it is spending: an expensive exchange must never be
silent.

**A tool call and the result it got back are drawn as one row** (`_rows`). They
arrive as two events under one id, and drawn as two they doubled the length of
every transcript with a line that says nothing alone — a bare `result` whose only
clue about what it is the result *of* is the row above it.

Two of these event kinds are not rows in a log — they are the loop stopping for
the human, and they are the whole reason the UI exists:

- **`question-form`.** The evidence panels stay visible and interactive while it
  is pending. Never a modal — the reason to answer here instead of in a terminal
  is that the profile you need is still on screen. Options render in the order
  the agent gave them and are never re-ordered or recommended-badged: which one
  is best is the human's call, and the screen is not a participant.
- **`write-confirm`.** The payload is laid out, not dumped, and an `acknowledge`
  becomes a banner above everything else. Allow carries the accent and Deny is
  always present. What has to stop this training a reflex is the banner — what
  the engine measured, said before the buttons are reached — and never a quieter
  button; a write approved without reading is approved either way
  (docs/EVALUATION.md, Run 5).

**The pane is a list of chats with one open at a time** (`docs/CHAT_SESSIONS.md`
§3.2). With nothing open it is the list, newest first, grouped by day, with a
composer at the foot that starts a new chat; inside a chat it is the transcript
and the composer under a one-line header. A chat has no single ending, so its
cost lands per exchange and its totals sit under the box. An indexing is a job
the app ran: a row like any other, told apart by its glyph, with a banner and
no composer, because there is nothing to reply to (`CONVERSATION.md` §6).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterator
from datetime import date, datetime
from pathlib import Path
from typing import Any

from nicegui import ui

from portia.agent import events, providers
from portia.core import cancel, present
from portia.ui import components as c
from portia.ui import engine, state
from portia.ui import exchange as exchange_driver
from portia.ui.state import APP, Decision

#: Free text always goes through verbatim, and typing an objection is a
#: first-class action rather than a fallback.
FREE_TEXT = "text"
CHOSEN = "chosen"

#: How much of a call's remaining arguments fits on its head, and how much of any
#: single value. Two numbers rather than one, so a call whose first argument is a
#: page of SQL still shows the *names* of the three after it.
ARG_CHARS = 160
VALUE_CHARS = 48

#: The shortest run of finished calls worth folding into one row. **Two**,
#: because one is not a run and *"1 tool call"* is a longer way of saying what
#: the card underneath it already says.
#:
#: There is no threshold above this on purpose. A run of two and a run of
#: forty-six get the same treatment, because the alternative is a number
#: deciding when a stretch of work becomes uninteresting, which is the screen
#: making a judgment call about the loop (`DESIGN.md` → kind, never rank). What
#: the two tabs measured is very different — the 2026-08-31 indexing pass was 98
#: cards in 16 runs with two of them 23 and 46 long, and the chat was 35 cards in
#: 22 runs averaging 1.6 — and that difference is a fact about the two jobs, not
#: a reason to render them by different rules.
GROUP_MIN = 2

#: What a tool call is *about*, in the order to look for it. The first of these
#: a call carries becomes the subject on its head — `profile_source` is about a
#: source, `run_spec` about a spec file — so the head answers "which tool, for
#: what" without being opened.
SUBJECT_FIELDS = ("source", "table", "spec_path", "name", "left", "column")

#: One glyph per tool, so the eye finds the shape of a run — three profiles, a
#: join, a write — before it reads a single name. **Kind, never rank**: every
#: icon is the same size and the same colour, and no tool is drawn louder than
#: another. A tool with no entry here gets the fallback rather than nothing, so
#: adding one to `agent/tools.py` can never leave a card with a hole in it.
TOOL_ICONS = {
    "get_context": "menu_book",
    "describe_source": "notes",
    "profile_source": "query_stats",
    "join_findings": "join_inner",
    "graph_lookup": "hub",
    "measure_overlaps": "compare_arrows",
    "set_interpretation": "edit_note",
    "set_group": "folder_open",
    "record_step": "playlist_add",
    "run_spec": "play_arrow",
}
TOOL_ICON = "build"

#: A folded run of allowed writes. Its own mark rather than the tool's, because
#: the row is about the confirmations and not about which tool asked for them —
#: the tally beside it already says that.
WRITE_ICON = "playlist_add_check"

#: The keystrokes that send what is in the composer, beside the button that does
#: the same thing. Enter is not one of them: it writes a newline, because a goal
#: is a paragraph and a box that sends on Enter is a box you write one line in.
#:
#: **Two, not one** — the composer is the same box on every platform, and a
#: shortcut bound only to the modifier the machine it was written on uses is a
#: shortcut half the app does not have.
#:
#: The word order is load-bearing. Vue reads the modifiers left to right and
#: stops at the first that does not match, so ``meta`` before ``prevent`` means
#: a bare Enter falls out before anything calls ``preventDefault`` on it — the
#: other order swallows every newline in the box.
SEND_KEYS = ("keydown.meta.enter.prevent", "keydown.ctrl.enter.prevent")

#: The keystroke carries the text it is sending, rather than the server reading
#: `APP.goal` and trusting it to be current.
#:
#: **Measured, not defensive** *(2026-09-06, browser)*. Quasar defers
#: `update:model-value` through a `setTimeout`, so the last character typed and
#: the key that sends it are two round trips whose order is not guaranteed: a
#: keydown dispatched in the same JavaScript tick as the input before it was
#: handled with `APP.goal` still empty, and sent nothing. A keyboard cannot
#: produce that — Cmd goes down between the two — but a paste, a dictation tool
#: or a text expander can, and the failure is silent in the worst way: a message
#: that goes without its last word reads as one the human wrote that way.
SEND_JS = "(e) => emit(e.target.value ?? '')"

#: How many dots the thinking row animates.
_DOTS = 3
_THOUSAND = 1000


@ui.refreshable
def pane() -> None:
    """The list, or one chat: header, transcript, and the composer under it.

    **The composer sits at the foot**, where a chat's next message goes. On the
    list it starts a new chat; inside one it continues it. A job has none, and
    a legacy chat has a line saying why instead.

    **A streamed event refreshes `stream_view`, never this.** This pane holds
    the composer, and rebuilding the whole of it per event destroyed the
    textarea a few times a second — the caret and the focus went with it, which
    made *"half a thought written while the copilot works"* (`CONVERSATION.md`
    §7's normal case) a thing the window fought. The chrome here changes at
    human pace — a message sent, a chat opened, an exchange ending — and those
    are the moments that refresh it.
    """
    chat = APP.open
    if chat is None:
        if APP.right == state.SOURCES:
            _sources_view()
            return
        _chat_list()
        _composer(None)
        return
    _chat_header(chat)
    if chat.is_job:
        _job_header(chat)
    # **Keyed and sticking, not driven from here** — every chat is a thing to
    # keep a place in, and following the newest row is `scroll.js`'s job
    # (`c.scroll_area`). This pane used to pin itself with a `run_javascript`
    # on every event, which is the race `canvas.js` documents.
    with c.scroll_area(f"transcript:{chat.key}", classes="p-pad stack-sm", stick=True):
        stream_view()
    if chat.continuable:
        _composer(chat)
    elif chat.legacy:
        _legacy_note(chat)


@ui.refreshable
def stream_view() -> None:
    """The rows of the open chat, in two parts: what has settled, and the tail.

    **A streamed event used to rebuild every row** *(until 2026-09-07)*. The
    stream is append-only, so a chat of 239 events rebuilt 2,635 elements and
    sent 567 KB to the browser per event, while the loop was held for the
    render and the browser for the patch — and a click landing on a row being
    replaced was a click that did nothing. That is the pane the user reads
    *while* the copilot works, so it was the pane that answered worst.

    Now the rows before `settled_before` are drawn **once**, into a slot this
    keeps on the chat, and `settle` appends to that slot as the boundary moves
    on; the rows after it — the group still growing, the call still running,
    the question still open — are `tail_view`, the only part a streamed event
    redraws. A chat opened from the list draws its log first, through `replay`
    (the same renderers, so the logged half and the live half cannot
    disagree), and that part never changes either.

    Rebuilt whole only at human pace, with `pane`: a message sent, a chat
    opened, an exchange ending.
    """
    chat = APP.open
    if chat is None:
        return
    if chat.logged is not None:
        replay(chat.logged)
        if chat.rows:
            c.caption(_PICKED_UP)
    elif not chat.rows and not _starting(chat) and not _failed(chat):
        c.empty_note(_IDLE_JOB if chat.is_job else _IDLE)
    chat.settled = settled_before(chat.rows, busy=chat.busy)
    # `contents`, so the rows inside sit in the scroll region's own stack
    # rather than in a box of their own.
    chat.settled_slot = ui.element("div").classes("contents")
    with chat.settled_slot:
        _draw(chat, 0, chat.settled)
    tail_view()


@ui.refreshable
def tail_view() -> None:
    """The rows still in motion — the per-event refresh (`stream_view`)."""
    chat = APP.open
    if chat is None:
        return
    keys = _key_prefix(chat)
    _draw(chat, chat.settled, None)
    if _starting(chat):
        _starting_rows(chat)
    # At the foot, where the newest card is and where the scroll already
    # sits: what is running, and what is lined up behind it.
    _tool_queue(chat, keys=keys)
    if chat.is_job and chat.exchange and chat.exchange.ended:
        _turn_ended(chat)
    elif _failed(chat):
        _exchange_failed(chat)


def _failed(chat) -> bool:
    """A chat's last exchange ended on an error rather than a result."""
    return (
        not chat.is_job
        and chat.exchange is not None
        and chat.exchange.ended
        and bool(chat.exchange.error)
    )


def _exchange_failed(chat) -> None:
    """What stopped the exchange, where the reply would have been.

    A job draws its error in `_turn_ended`; a chat drew nothing, so an exchange
    that died before the client started left the pane saying *no messages yet*
    over the message that was sent (2026-09-08, every send on Ollama, because
    the window passed an effort the provider refuses). The message itself is
    drawn above this from the exchange when no event arrived (`_starting_rows`'s
    prompt row), so the pane reads as a message and what happened to it.
    """
    exchange = chat.exchange
    if not exchange.opened:
        _prompt_row(
            {
                "text": exchange.prompt,
                "model": exchange.model,
                "effort": exchange.effort,
                "provider": exchange.provider,
            }
        )
    c.alert(_FAILED.format(error=exchange.error), kind="error")


def _starting(chat) -> bool:
    """An exchange is under way and its own first event has not arrived."""
    return chat.exchange is not None and chat.exchange.running and not chat.exchange.opened


def _starting_rows(chat) -> None:
    """The message just sent, and a mark saying the client is starting.

    Drawn **off the exchange**, not inserted into the stream: the `PROMPT`
    event stays the one place the message enters the log and the rows
    (`CONVERSATION.md` §5), and when it arrives, `tail_view` redraws and the
    real row takes this one's place. Before this existed the pane said *no
    messages yet* from Send until the SDK binary had started, which on a
    local provider is the better part of a minute with nothing moving
    (2026-09-08, reported from a session on `mistral:latest`).
    """
    exchange = chat.exchange
    if not chat.is_job:
        _prompt_row(
            {
                "text": exchange.prompt,
                "model": exchange.model,
                "effort": exchange.effort,
                "provider": exchange.provider,
            }
        )
    c.in_flight(_STARTING, f"{providers.get(exchange.provider).label} · {exchange.model}")


def settle(chat) -> bool:
    """Draw what has settled since the last event into its slot; say if that worked.

    Appending, not rebuilding: the slot is the element `stream_view` left on
    the chat, and a row drawn into it is a small patch beside rows that are
    not touched. ``False`` when the slot is gone — the pane was rebuilt under
    it or this chat is not the one on screen — and the caller falls back to a
    whole redraw.
    """
    slot = chat.settled_slot
    if slot is None or slot.is_deleted:
        return False
    boundary = settled_before(chat.rows, busy=chat.busy)
    if boundary > chat.settled:
        with slot:
            _draw(chat, chat.settled, boundary)
        chat.settled = boundary
    return True


def _draw(chat, start: int, stop: int | None) -> None:
    """One chat's rows in ``[start, stop)``, with the keys and clocks it carries."""
    _rows(
        chat.rows,
        busy=chat.busy,
        job=chat.is_job,
        keys=_key_prefix(chat),
        timings=chat.timings,
        start=start,
        stop=stop,
    )


def _key_prefix(chat) -> str:
    """The stem of this chat's entrance keys (`components.enters`).

    ``chat:generation``, so a row's key is stable for as long as the row is —
    positions only ever append within a generation — and one chat's row zero
    does not inherit another's key, which would arrive as *seen* and never
    animate.
    """
    return f"{chat.key}:{chat.generation}"


# --- the list (`docs/CHAT_SESSIONS.md` §3.2) ---------------------------------


def _chat_list() -> None:
    """Every chat and job, newest first, grouped by day, under one pinned row.

    Time order is the one order a list of conversations has and it ranks
    nothing. The glyph does the telling-apart: a chat you had, a job the app
    ran. The list is read off the files each time it is drawn — a chat this
    process holds is looked up by path so its row can carry what only this
    process knows (running, waiting, legacy).
    """
    with c.scroll_area("chats", classes="chat-list"):
        _pinned_sources_row()
        paths = engine.logs_in(APP)
        if not paths:
            c.empty_note(_NO_CHATS)
            return
        today = date.today()
        group = None
        for path in paths:
            listing = engine.log_listing(APP, path)
            label = _day_label(listing.get("started"), today)
            if label != group:
                group = label
                ui.label(label).classes("chat-group")
            _chat_row(path, listing)


def _pinned_sources_row() -> None:
    """What portia knows about each source, and the Index button — one row up top.

    Pinned above the day groups the way the brief is pinned above the tree:
    it is not a chat and it is not in the history, it is where a job starts.
    """
    with ui.element("div").classes("chat-row chat-row--pinned").on("click", _show_sources):
        ui.icon(c.INDEX_ICON).classes("chat-row-icon")
        with ui.element("div").classes("chat-row-body"):
            ui.label(_SOURCES_TITLE).classes("chat-row-title")
            ui.label(_SOURCES_META).classes("chat-row-meta")
        job = APP.live_job
        if job is not None:
            _dot(job)


def _day_label(started: str | None, today: date) -> str:
    """Today, yesterday, else the date. A caption per group, never a rank."""
    if not started:
        return _UNDATED
    try:
        day = datetime.fromisoformat(started).date()
    except ValueError:
        return _UNDATED
    if day == today:
        return _TODAY
    if (today - day).days == 1:
        return _YESTERDAY
    return day.isoformat()


def _chat_row(path: Path, listing: dict) -> None:
    """One chat or job. Kind, never rank: same size, same colour, one glyph.

    The row carries a dot while something is happening in it (§3.5), a
    `legacy` chip when it cannot be continued (§3.3), and two quiet actions on
    hover: rename and delete. Mid-rename the title is a field; mid-delete the
    body is the question, inline, in the shape the gallery uses for a folder.
    """
    held = APP.chat_at(path)
    name = path.name
    title = (held.title if held is not None and held.title else "") or listing["title"]
    legacy = listing["legacy"] or (held is not None and bool(held.legacy))
    icon = _JOB_ICON if listing["kind"] == "indexing" else _CHAT_ICON
    row = ui.element("div").classes("chat-row")
    c.enters(row, f"chat:{name}")
    with row:
        ui.icon(icon).classes("chat-row-icon")
        if APP.chat_deleting == name:
            _delete_prompt(path)
            return
        with ui.element("div").classes("chat-row-body"):
            if APP.chat_renaming == name:
                _rename_field(path, title)
            else:
                ui.label(title).classes("chat-row-title")
                with ui.element("div").classes("chat-row-spend"):
                    c.provider_glyph(listing.get("provider") or providers.DEFAULT_KIND)
                    ui.label(_row_meta(listing)).classes("chat-row-meta")
        if held is not None and (held.pending is not None or held.busy):
            _dot(held)
        if legacy:
            ui.label(_LEGACY).classes("chat-chip")
        with ui.element("div").classes("chat-row-actions"):
            c.button("", lambda p=path: _start_rename(p), icon="edit", micro=True).tooltip(
                _RENAME_TIP
            )
            c.button("", lambda p=path: _start_delete(p), icon="delete", micro=True).tooltip(
                _DELETE_TIP
            )
    if APP.chat_renaming != name:
        row.on("click", lambda p=path: _open_row(p))


def _row_meta(listing: dict) -> str:
    model = str(listing.get("model") or "").replace("claude-", "")
    effort = listing.get("effort")
    return " · ".join(part for part in (model, effort) if part)


def _dot(chat) -> None:
    """Running, or waiting on you. `DESIGN.md`'s tab dot, on a row now.

    Kind, never rank: the waiting dot takes the accent because it is asking for
    you, not because it is worse than anything else on screen.
    """
    if chat.pending is not None:
        ui.element("div").classes("chat-dot chat-dot--waiting").tooltip(_WAITING)
    elif chat.busy:
        ui.element("div").classes("chat-dot").tooltip(_RUNNING)


def _open_row(path: Path) -> None:
    exchange_driver.open_from_disk(path)
    pane.refresh()


def _show_sources() -> None:
    APP.show_sources()
    pane.refresh()


def _show_list() -> None:
    APP.show_chat(None)
    pane.refresh()


def _new_chat() -> None:
    """A fresh, empty chat on screen — while another runs, if one does (§3.1)."""
    APP.show_chat(APP.new_chat())
    pane.refresh()


def _start_rename(path: Path) -> None:
    APP.chat_renaming, APP.chat_deleting = path.name, ""
    pane.refresh()


def _rename_field(path: Path, title: str) -> None:
    field = ui.input(value=title).classes("chat-rename").props("dense borderless autofocus")
    field.on("keydown.enter", lambda e, p=path, f=field: _finish_rename(p, str(f.value or "")))
    field.on("keydown.escape", lambda e: _cancel_row_action())
    field.on("blur", lambda e, p=path, f=field: _finish_rename(p, str(f.value or "")))


def _finish_rename(path: Path, title: str) -> None:
    if APP.chat_renaming != path.name:
        return
    APP.chat_renaming = ""
    engine.rename_log(APP, path, title)
    held = APP.chat_at(path)
    if held is not None:
        held.title = engine.log_listing(APP, path)["title"]
    pane.refresh()


def _start_delete(path: Path) -> None:
    held = APP.chat_at(path)
    if held is not None and held.busy:
        ui.notify(_DELETE_BUSY)
        return
    APP.chat_deleting, APP.chat_renaming = path.name, ""
    pane.refresh()


def _delete_prompt(path: Path) -> None:
    with ui.element("div").classes("chat-row-body"):
        ui.label(_DELETE_ASK).classes("chat-row-title")
        with ui.element("div").classes("row-gap-sm"):
            c.button(_DELETE_GO, lambda p=path: _finish_delete(p), kind="secondary", micro=True)
            c.button(_DELETE_CANCEL, _cancel_row_action, micro=True)


async def _finish_delete(path: Path) -> None:
    APP.chat_deleting = ""
    held = APP.chat_at(path)
    if held is not None:
        await exchange_driver.delete_chat(held)
    else:
        engine.delete_log(APP, path)
    pane.refresh()


def _cancel_row_action() -> None:
    APP.chat_renaming = APP.chat_deleting = ""
    pane.refresh()


# --- the header inside a chat --------------------------------------------------


def _chat_header(chat) -> None:
    """Back to the list, the chat's name, and a new chat. One line.

    **The back control carries the badge** (§3.5): a dot when another chat is
    waiting on you or running, so nothing has to switch you there to say so.
    """
    with ui.element("div").classes("chat-header"):
        back = ui.element("div").classes("chat-back").on("click", _show_list)
        with back:
            ui.icon("arrow_back").classes("chat-back-icon")
            ui.label(_CHATS).classes("chat-back-label")
            elsewhere = _elsewhere(chat)
            if elsewhere is not None:
                _dot(elsewhere)
        ui.label(chat.title or _UNTITLED).classes("chat-header-title")
        c.button("", _new_chat, icon="add", micro=True).tooltip(_NEW_CHAT_TIP)


def _elsewhere(chat):
    """Another chat that needs you, or one that is running. Waiting first —
    that is the one asking."""
    waiting = [other for other in APP.waiting if other is not chat]
    if waiting:
        return waiting[0]
    live = APP.live
    return live if live is not None and live is not chat else None


def _job_header(chat) -> None:
    """A job's banner, and Stop while it runs — it has no composer to carry one."""
    with ui.element("div").classes("p-pad stack-md index-header"):
        _exchange_banner(chat.exchange)
        if chat.busy:
            with ui.element("div").classes("row-gap-sm"):
                ui.spinner(size="sm")
                c.caption("working")
                c.button("Stop", _stop, kind="tertiary", icon="stop")
    c.rule()


def _legacy_note(chat) -> None:
    """Where the composer would be: why there is none."""
    c.rule()
    with ui.element("div").classes("p-pad row-gap-sm"):
        ui.label(_LEGACY).classes("chat-chip")
        c.caption(_READ_ONLY.format(why=chat.legacy))


def _sources_view() -> None:
    """The pinned row, opened: every source, its state, and the Index button."""
    with ui.element("div").classes("chat-header"):
        with ui.element("div").classes("chat-back").on("click", _show_list):
            ui.icon("arrow_back").classes("chat-back-icon")
            ui.label(_CHATS).classes("chat-back-label")
        ui.label(_SOURCES_TITLE).classes("chat-header-title")
    job = APP.live_job
    with ui.element("div").classes("p-pad stack-md index-header index-header--list"):
        if job is not None:
            _exchange_banner(job.exchange)
            with ui.element("div").classes("row-gap-sm"):
                ui.spinner(size="sm")
                c.caption("working")
                c.button("Stop", _stop, kind="tertiary", icon="stop")
        else:
            c.caption(_INDEX_WHAT)
            _source_states()
    c.rule()


#: What each state is called on screen, and the icon that repeats down the rows.
#: Words once at the top would be a legend; these are short enough to repeat.
_STATE_LABEL = {
    engine.UNINDEXED: "not indexed",
    engine.UNREAD: "not read",
    engine.INTERPRETED: "read",
}
_STATE_ICON = {
    engine.UNINDEXED: "radio_button_unchecked",
    engine.UNREAD: "pending",
    engine.INTERPRETED: "check_circle",
}


@ui.refreshable
def _source_states() -> None:
    """Every source, what portia knows about it, and what you can do next.

    **The list scrolls and the actions do not.** A project with twenty-two
    sources ran the list off the bottom of the pane and took the two buttons
    with it — the one part of this tab you cannot do without, because it is
    where the free half and the paid half are told apart. So the rows are their
    own scrolling region (keyed, like every other one — `c.scroll_area`) and
    everything that says what a press will cost stays put beneath them.

    Refreshable because **Select all** has to redraw the checkboxes. That is
    the opposite of `_tick_source`'s rule and does not contradict it: the
    danger there is rebuilding rows *under a cursor that is mid-click*, and a
    press on a button outside the list is not that.
    """
    states = engine.source_states(APP)
    if not states:
        c.empty_note(_NO_SOURCES)
        return

    _select_all(states)
    with c.scroll_area("index-sources", classes="index-list-scroll"):
        with ui.element("div").classes("index-list"):
            for source in states:
                _source_state_row(source)
    _index_actions()


def _select_all(states) -> None:
    """Tick every source, or clear them — one control that says which it will do.

    Two buttons would leave one of them dead on every press. The label is the
    action and the count beside it is the state, so nothing has to be inferred
    from whether a button looks pressed.
    """
    every = {s.name for s in states}
    all_ticked = every <= APP.index_ticks
    with ui.element("div").classes("index-select-all"):
        c.button(
            "Clear" if all_ticked else "Select all",
            lambda: _tick_every(set() if all_ticked else every),
            micro=True,
            icon="deselect" if all_ticked else "select_all",
        )
        c.caption(f"{len(APP.index_ticks & every)} of {len(every)} selected")


def _tick_every(names: set[str]) -> None:
    APP.index_ticks = frozenset(names)
    _source_states.refresh()


def _source_state_row(source) -> None:
    ticked = source.name in APP.index_ticks
    with c.enters(ui.element("div").classes(f"index-row is-{source.state}"), f"idx:{source.name}"):
        (
            ui.checkbox(value=ticked)
            .classes("p-check")
            .props("dense")
            .on_value_change(lambda e, n=source.name: _tick_source(n, bool(e.value)))
        )
        ui.icon(_STATE_ICON[source.state]).classes("index-row-icon")
        if getattr(source, "kind", state.SOURCE) == state.MODEL:
            # Kind, never rank: a built table is a different kind of row from
            # a source, and the glyph says which without moving it in the list.
            ui.icon(_MODEL_GLYPH).classes("index-row-kind")
        ui.label(source.name).classes("index-row-name")
        ui.label(_STATE_LABEL[source.state]).classes("index-row-state")
        if not source.profiled:
            # The second axis (`engine.SourceState.profiled`): a warehouse
            # table nobody has scanned. Same words the left pane and the
            # inspector use, so three surfaces do not name one state three ways.
            ui.label(_METADATA_ONLY).classes("index-row-state")
        if source.stale:
            ui.label("changed").classes("index-row-stale")


def _tick_source(name: str, on: bool) -> None:
    """Only the buttons redraw, **not** the list.

    Refreshing the whole pane rebuilt the rows, which deletes the checkbox you
    are in the middle of clicking — so ticking two boxes quickly registered one.
    The checkbox holds its own value; the only thing a tick changes on screen is
    what the two buttons say they will do.
    """
    APP.index_ticks = (APP.index_ticks | {name}) if on else (APP.index_ticks - {name})
    _index_actions.refresh()


@ui.refreshable
def _index_actions() -> None:
    """Two actions, because they cost different things.

    Profiling is deterministic and free; reading costs a model turn. A single
    button doing both would hide which of the two you were about to spend.

    **What the paid one will spend is picked here too.** `Interpret` reads
    `APP.model` and `APP.effort` and always did; until now the only places to
    set them were the composer and Settings, so the button that spends them
    was the one screen that could not say what they were. It is `c.model_effort`
    rather than a pair of selects for the reason that control exists — three
    hand-rolled copies is how they stop agreeing — and it sits *with* the cost
    caption, the same shape the add-data screen uses.
    """
    ticked = [s for s in engine.source_states(APP) if s.name in APP.index_ticks]
    files, remote = engine.to_index(ticked)
    to_index = [*files, *remote]
    to_read = [s for s in ticked if s.indexed]
    running = bool(APP.indexing_status)
    _index_progress()
    with ui.element("div").classes("index-actions"):
        c.button(
            f"Index {c.count(len(to_index), 'source')}",
            _index_ticked,
            kind="secondary",
            enabled=bool(to_index) and not running,
            icon=c.INDEX_ICON,
        )
        c.button(
            f"Interpret {c.count(len(to_read), 'source')}",
            _interpret_ticked,
            kind="primary",
            enabled=bool(to_read) and not running,
            icon="auto_awesome",
        )
    with ui.element("div").classes("index-cost"):
        c.model_effort(
            APP,
            _set_indexing_effort,
            on_provider=_set_indexing_provider,
            on_refresh=_list_models_clicked,
            on_start=_open_server_dialog,
        )
        # The scan is the one cost on this tab that is not a model turn, and
        # it is named only when a press would incur it.
        c.caption(_INDEX_COST_REMOTE if remote else _INDEX_COST)


def _index_progress() -> None:
    """What Index is doing, while it does it. The add-data panel's line, here.

    One field (`APP.indexing_status`), drawn wherever indexing can be started
    from: a warehouse profile is minutes on the meter, and this tab used to
    run one with nothing on it moving (2026-09-07).
    """
    if not APP.indexing_status:
        return
    with ui.element("div").classes("row-gap-sm indexing-status"):
        ui.spinner(size="sm")
        c.text(APP.indexing_status, color="c-mute")
        if APP.indexing_stop is not None:
            c.button("Stop", _stop_indexing, kind="tertiary", icon="stop")


def _stop_indexing() -> None:
    if APP.indexing_stop is not None:
        APP.indexing_stop.cancel()


def _set_indexing_effort(effort: str) -> None:
    """Only the actions redraw — the list has not changed and rebuilding it
    would scroll it back to the top for a control that sits below it."""
    APP.effort = effort
    _index_actions.refresh()


def _set_indexing_provider(kind: str) -> None:
    from nicegui import background_tasks

    APP.provider = kind
    APP.model = providers.get(kind).default_model
    _index_actions.refresh()
    background_tasks.create(_list_models(kind))


async def _index_ticked() -> None:
    """Profile the ticked files, then scan the ticked warehouse tables. No model exchange.

    Two loops with one status line and one Stop, because the two cost
    different things: the files are free and the tables are on the meter.
    The count in the toast is what finished, not what was ticked, since Stop
    lands between hops (`engine.index`, `engine.profile_tables`).
    """
    from portia.ui import artifacts

    ticked = [s for s in engine.source_states(APP) if s.name in APP.index_ticks]
    files, remote = engine.to_index(ticked)
    if not files and not remote:
        return

    def say(verb: str):
        def _say(done: int, total: int, name: str) -> None:
            APP.indexing_status = f"{verb} {name}, {done + 1} of {total}"
            _index_actions.refresh()

        return _say

    APP.indexing_status = f"Indexing {c.count(len(files) + len(remote), 'source')}…"
    stop = APP.indexing_stop = cancel.Scope()
    _index_actions.refresh()
    done: list[str] = []
    try:
        if files:
            paths = [APP.root / s.rel for s in files]
            done += await engine.index(paths, APP, on_progress=say("Profiling"), stop=stop)
        if remote and not stop.cancelled:
            names = [s.name for s in remote]
            done += await engine.profile_tables(APP, names, on_progress=say("Profiling"), stop=stop)
    finally:
        APP.indexing_status = ""
        APP.indexing_stop = None
        stop.close()
    artifacts.pane.refresh()
    pane.refresh()
    ui.notify(f"Profiled {c.count(len(done), 'source')}.")


async def _interpret_ticked() -> None:
    """Spend a turn reading the ticked sources — including ones already read.

    Re-reading is the same act as reading: `set_interpretation` writes judgment
    and never touches a measured fact, so running it again over a source whose
    context has changed is a correction, not a conflict.
    """
    from portia.agent import prompts
    from portia.ui import exchange as exchange_module

    names = [s.name for s in engine.source_states(APP) if s.name in APP.index_ticks and s.indexed]
    if not names:
        return
    APP.index_ticks = frozenset()
    await exchange_module.start(
        prompts.task("index_batch", names=", ".join(repr(n) for n in names)),
        model=APP.model or APP.default_model,
        effort=APP.effort,
        provider=APP.provider,
        kind=state.INDEXING,
        label=", ".join(names),
        # The press was made in this pane, so the job opens in it. The sources
        # view draws a job's banner and no rows, and it looked like a thread
        # with no tool calls until you went back to the list and opened the
        # job from there (the user, 2026-09-18).
        show=True,
    )


# --- the goal box -----------------------------------------------------------


def _composer(chat) -> None:
    """Where the next message is written. **The send rule lives here**
    (`docs/CONVERSATION.md` §7).

    ``chat`` is the open chat, or ``None`` on the list, where a send starts a
    new one and opens it.

    - **The box is always editable**, in flight or not. Half a thought written
      while the copilot works is the normal case, and a disabled textarea throws
      it away.
    - **Send is dark while a message is in flight anywhere**, and there is no
      queue. A queued message would have to arrive either before or after
      whatever the agent does next, and neither is defensible when what it does
      next might be to ask you a question. With several chats open the rule is
      the same and the caption names the chat that is running
      (`CHAT_SESSIONS.md` §3.1).
    - **Stop is the only way to make a composed message go now.** Explicit,
      never something a keystroke does: `record_step` runs an op and *then*
      writes the spec, so a half-completed durable write has to be a deliberate
      act (§8). It is drawn in the chat that is running, and only there.
    - **Cmd+Enter sends, and Enter writes a newline** *(2026-09-06, §7.1)*. It
      is not the exception to the line above: that one is about a message
      arriving while the copilot is *working*, and this keystroke ends in `_go`
      like the button, returning on `APP.busy` exactly as the button does. Enter
      keeps its newline because a goal is a paragraph — "the tables are the same
      customers under different codes, and here is what I want out of them" —
      and a box that sends on Enter is a box you write one line in. The
      keystroke brings its own text rather than reading the bound value; §7.1
      is the browser measurement that made it, and `SEND_JS` is the mechanism.

    A pending question is in flight, so the form in the transcript is the only
    live channel while one is open — there is never a moment with two boxes that
    both look like the place to answer.

    **Send carries the accent, always** *(2026-08-13)*. The scarcity rule is
    **per pane**: the composer's forward action is Send, the workflow pane's is
    Run, and a chat whose send button greys out because a *different* pane has
    something to run is a chat that looks closed. Inside this pane the rule
    still holds — Send becomes Stop while a message is in flight, so it can
    never be on screen beside the Answer or Allow the loop is stopped on.

    **The pickers are the chat's** (`CHAT_SESSIONS.md` §3.6). On the list they
    bind the app's defaults, which is what a new chat starts with; inside a
    chat they bind that chat's model and effort, so two open chats on two
    models say so. Effort is fixed for the life of a client and stops being
    offered once one is parked, rather than being offered and quietly ignored.
    """
    live = APP.live
    busy_here = chat is not None and chat.busy
    busy_elsewhere = live is not None and not busy_here
    target = chat if chat is not None else APP
    c.rule()
    with ui.element("div").classes("p-pad stack-sm"):
        with ui.element("div").classes("composer"):
            field = (
                ui.textarea(placeholder=_placeholder(chat))
                .classes("composer-field w-full")
                .props("borderless autogrow")
                .bind_value(APP, "goal")
            )
            for combination in SEND_KEYS:
                field.on(combination, _go_from_key, js_handler=SEND_JS)
            if APP.spend_alert is not None:
                _spend_alert(*APP.spend_alert)
            with ui.element("div").classes("composer-bar"):
                with ui.element("div").classes("composer-spend"):
                    parked = chat is not None and chat.open
                    c.model_effort(
                        target,
                        _set_effort,
                        effort_disabled=parked,
                        on_provider=_set_provider,
                        on_refresh=_list_models_clicked,
                        on_start=_open_server_dialog,
                        provider_fixed=parked,
                    )
                    # The third fact about how this message will run, beside
                    # the other two (`c.approval_mode`). Read live by
                    # `exchange.auto_allow`, so changing it applies to the
                    # next write rather than the next chat.
                    c.approval_mode(APP, _mode_changed)
                with ui.element("div").classes("composer-send"):
                    if busy_here:
                        # The spinner says it is alive and Stop names the only
                        # thing to do about it.
                        ui.spinner(size="sm")
                        c.button("Stop", _stop, kind="tertiary", icon="stop")
                    elif APP.preflight_status:
                        # A local model loading before the first message
                        # (`PROVIDERS.md` §5). Nothing to stop yet, and Send
                        # would be a second message before the first went.
                        ui.spinner(size="sm")
                        c.caption(APP.preflight_status)
                    else:
                        send = c.button(
                            "Send",
                            _go,
                            kind="primary",
                            icon="arrow_upward",
                            enabled=not busy_elsewhere,
                        )
                        send.tooltip(_busy_tip(live) if busy_elsewhere else _SEND_TIP)
        with ui.element("div").classes("composer-meta"):
            # Always a container, even when there is nothing to count: without
            # one, `space-between` puts a lone control on the left.
            with ui.element("div").classes("composer-meta-facts"):
                if chat is not None:
                    _chat_footer(chat)
                if busy_elsewhere:
                    c.caption(_busy_tip(live))


def _mode_changed() -> None:
    """The mode picker moved. Redraw the pane, and the settings panel if it is up.

    Two places show one setting (`CLAUDE.md` → `ui/settings.py`), and §14.4 is
    what happens when one of them is left showing yesterday's value.
    """
    from portia.ui import settings

    pane.refresh()
    settings.refresh_if_open()


def _placeholder(chat) -> str:
    return _FOLLOW_UP_PLACEHOLDER if chat is not None and chat.started else _GOAL_PLACEHOLDER


def _busy_tip(live) -> str:
    return _BUSY_ELSEWHERE.format(title=(live.title if live is not None else "") or _UNTITLED)


def _chat_footer(chat) -> None:
    """What the chat has cost and how full it is. Counted, never judged.

    Both are measured facts, which is the only reason they may be on screen at
    all (`CLAUDE.md` → facts vs judgment). Nothing here says a chat is too long
    or too expensive: that needs a goal, and this panel has no way to know one.
    `CONVERSATION.md` §13 is why no policy sits on top of the context number.
    """
    parts = []
    if chat.messages > 1:
        parts.append(c.count(chat.messages, "message"))
    spent = chat.spent
    if spent:
        parts.append(f"~${spent:.4f}")
    used = (chat.context or {}).get("totalTokens")
    limit = (chat.context or {}).get("maxTokens")
    if used and limit:
        parts.append(f"{used:,} / {limit:,} context")
    if chat.resumed:
        # Stated once and never hidden (`CHAT_SESSIONS.md` §3.3): a resumed
        # chat's history is read back at full price the first time.
        parts.append(_RESUMED)
    if parts:
        c.caption(" · ".join(parts))


def _exchange_banner(turn) -> None:
    """What this turn is, and which half of indexing is actually running.

    Profiling already happened and was free; what costs a turn is the
    interpretation. A panel that merges the two is the "one merged spinner"
    docs/DESIGN.md forbids.
    """
    if turn.kind == state.GOAL:
        return
    with ui.element("div").classes("exchange-banner"):
        with ui.element("div").classes("row-gap-sm"):
            ui.icon(_BANNER_ICON[turn.kind]).classes("fact-icon")
            ui.label(_BANNER_TITLE[turn.kind]).classes("t-body-strong c-ink")
        # **Under the title, and bounded.** Twenty-three source names is a
        # paragraph, not a caption: on the title's own row it wrapped to eight
        # lines and drove the banner taller than the space the header had, and
        # the header — shrinkable so the source list can scroll — let the
        # overflow spill straight through the transcript underneath it. It
        # scrolls rather than clipping, because the batch is exactly what you
        # would go to this banner to read.
        if turn.label:
            with ui.element("div").classes("exchange-banner-names"):
                c.mono(turn.label, color="c-mute", small=True)
        c.caption(_BANNER_WHY[turn.kind])


def _set_effort(effort: str) -> None:
    """The open chat's effort, or the default a new one starts with."""
    if APP.open is not None and APP.open.continuable:
        APP.open.effort = effort
    else:
        APP.effort = effort
    pane.refresh()


def _set_provider(kind: str) -> None:
    """The open chat's provider, or the default a new one starts with.

    The model moves with it, to the provider's default: a Claude name sent to
    Ollama is a guaranteed refusal (`PROVIDERS.md` §4.3). And the list is
    asked for again, so a server started since the window opened is seen.
    """
    from nicegui import background_tasks

    target = APP.open if APP.open is not None and APP.open.continuable else APP
    target.provider = kind
    target.model = providers.get(kind).default_model
    APP.spend_alert = None
    pane.refresh()
    background_tasks.create(_list_models(kind))


def _list_models_clicked(kind: str) -> None:
    from nicegui import background_tasks

    background_tasks.create(_list_models(kind))


async def _list_models(kind: str) -> None:
    """Read one provider's list off the loop and redraw whatever shows it."""
    from portia.ui import settings

    pane.refresh()
    await engine.list_models(APP, kind)
    pane.refresh()
    settings.refresh_if_open()


def list_models_in_background() -> None:
    """Every provider that has a server: asked once when the page opens.

    So the picker has Ollama's list the moment someone switches to it, rather
    than a select with one name in it. A provider that is not running answers
    at once with a refusal, which the picker draws as its remedy.
    """
    from nicegui import background_tasks

    for kind in providers.KINDS:
        if providers.get(kind).warms:
            background_tasks.create(_list_models(kind))


def _spend_alert(reason: str, remedy: str) -> None:
    """A refused preflight: the provider's reason, and the one thing to do.

    In the composer, above the bar, because it is about the message that did
    not go; not a transcript row, because no exchange happened (§5).
    """
    with ui.element("div").classes("composer-alert"):
        c.alert(f"{reason} {remedy}".strip(), kind="warning")


async def _go_from_key(event) -> None:
    """Send on `SEND_KEYS`, from the text the keystroke brought with it.

    The box is bound to `APP.goal` and the button reads it, which is right for
    the button: a pointer travelling to it is several frames the value has to
    arrive in. A keystroke has no such gap, so this takes the client's own value
    and puts it on `APP.goal` before `_go` reads it — the binding still owns the
    field, this only refuses to race it. Everything after is `_go`, including
    the `APP.busy` guard: there is one send rule and one place it is written.
    """
    if isinstance(event.args, str):
        APP.goal = event.args
    await _go()


async def _go() -> None:
    goal = (APP.goal or "").strip()
    if not goal or APP.busy or APP.preflight_status:
        return
    # Cleared before sending, not after: the message is now in the transcript,
    # and a box still holding it reads as though it had not gone.
    APP.goal = ""
    chat = APP.open if APP.open is not None and APP.open.continuable else None
    model = chat.model if chat is not None and chat.model else APP.model
    effort = chat.effort if chat is not None else APP.effort
    provider = chat.provider if chat is not None and chat.provider else APP.provider
    await exchange_driver.start(goal, model=model, effort=effort, chat=chat, provider=provider)


async def _stop() -> None:
    await exchange_driver.interrupt()


# --- one row per event ------------------------------------------------------


#: What a tool call is doing, as the transcript draws it. **Four states, not a
#: scale** — the order they are listed in is the order the SDK reported the
#: calls, and nothing here sorts, colours or sizes one above another.
RUNNING = "running"
QUEUED = "queued"
DONE = "done"
ERRORED = "errored"
#: The exchange ended with no result under this call — an interrupt, usually.
DROPPED = "dropped"


def _rows(
    rows: list[Any],
    *,
    busy: bool = False,
    job: bool = False,
    keys: str = "",
    timings: dict[str, state.Timing] | None = None,
    start: int = 0,
    stop: int | None = None,
) -> None:
    """The stream, with each tool call drawn together with its own result.

    ``keys`` is the stream's entrance-key prefix (`_key_prefix`): each drawn row
    is wrapped in a `c.enter_slot` so a genuinely new row animates in and a
    rebuilt one stays still. Positions are stable within a generation because
    rows only append, so a row's index is its identity — except a tool call,
    which carries the SDK's own id and keeps it through every regrouping.

    **A call and the evidence it got back are one act, and they are one card.**
    They arrive as two events — the SDK reports the invocation, then the result
    under the same `id` — and drawn as two they doubled the length of every
    transcript with a line that says nothing on its own: a bare `result` whose
    only clue about what it is the result *of* is the row above it. Paired, the
    head says which tool and what about, and the evidence is what it opens to.

    **Consecutive thinking blocks are one row**, for the same reason: the model
    surfaces reasoning in as many blocks as it likes, and one row each turned a
    chat into a column of identical labels.

    **And consecutive finished calls are one row too** — the same treatment, one
    level up. A card was already shut; what it was not was *skippable*, so a run
    of forty-six of them was forty-six rows to scroll past to reach the sentence
    after them. What breaks a run is anything worth stopping at: a thinking
    block, a message, a write waiting on you, and any call that is still running,
    still queued, errored, or never came back (`_tool_run`).

    Nothing is dropped. A result whose call is missing from the stream — a
    replayed log that starts mid-exchange — still draws on its own below.

    ``timings`` is the window's clock on each call (`state.Timing`), by call
    id; a replay supplies the same off the log's stamps.

    ``start`` and ``stop`` draw a slice of the stream **in the stream's own
    segments** (`_segments`): the answers and states are still computed over
    every row, and positions are absolute, so a slice folds and keys exactly
    as the whole would. `stream_view` draws the settled part once and the tail
    per event through this.
    """
    answers = _results_by_call(rows)
    states = _call_states(rows, answers, busy=busy)
    timings = timings or {}
    for index, _end in _segments(rows, answers, start=start, stop=stop):
        row = rows[index]
        if _is(row, events.THINKING):
            # Keyed by where the run *starts*: the group at the stream's foot
            # grows as blocks join it, and a key that moved with its content
            # would re-animate a row the reader is already looking at.
            with c.enter_slot(f"{keys}:think:{index}"):
                _thinking_group(rows, index, busy=busy)
        elif _is(row, events.TOOL_CALL):
            _tool_run(rows, index, answers, states, timings)
        elif _is_allowed_write(row):
            _decision_run(rows, index, answers, keys=keys)
        elif not _drawn_elsewhere(row, answers):
            # A result is drawn with its call, and an approval result with the
            # request it resolves — the same two exclusions `replay` makes.
            # Without the second, every automatic write left an empty row.
            with c.enter_slot(f"{keys}:{index}"):
                _row(row, job=job)


def _drawn_elsewhere(row: Any, answers: dict[str, dict]) -> bool:
    """A row that is part of another row's card rather than a row of its own."""
    return (_is(row, events.TOOL_RESULT) and str(row.data.get("id") or "") in answers) or _is(
        row, events.APPROVAL_RESULT
    )


def _segments(
    rows: list[Any], answers: dict[str, dict], *, start: int = 0, stop: int | None = None
) -> Iterator[tuple[int, int]]:
    """``(start, end)`` of each drawn unit of the stream, in order.

    **The one place the stream is cut into rows**, used by `_rows` to draw and
    by `settled_before` to say which rows can still change. A run of thinking,
    a run of calls and a run of allowed writes are each one unit; everything
    else is a unit of one — including a row drawn inside another's card, which
    `_rows` then skips. Two functions that cut the stream would be two
    opinions about where a row begins.
    """
    index = start
    limit = len(rows) if stop is None else stop
    while index < limit:
        row = rows[index]
        if _is(row, events.THINKING):
            end = _thinking_end(rows, index)
        elif _is(row, events.TOOL_CALL):
            end = _tool_run_end(rows, index, answers)
        elif _is_allowed_write(row):
            end = _writes_end(rows, index, answers)
        else:
            end = index + 1
        yield index, end
        index = end


def settled_before(rows: list[Any], *, busy: bool = False) -> int:
    """The position before which nothing drawn can change.

    A unit is settled when it is not the last one — the last can still grow —
    and nothing in it is waiting: no call still running or queued, no decision
    the human has not made. The answer is the start of the **earliest** unit
    that is not settled, because a result landing there moves the state of
    every call drawn after it. Rows only append, so this only moves forward
    within a generation.
    """
    answers = _results_by_call(rows)
    states = _call_states(rows, answers, busy=busy)
    units = list(_segments(rows, answers))
    if not units:
        return 0
    for start, end in units[:-1]:
        if any(_waiting(rows[at], states) for at in range(start, end)):
            return start
    return units[-1][0]


def _waiting(row: Any, states: dict[str, str]) -> bool:
    """Whether this row's drawing depends on something that has not happened yet."""
    if isinstance(row, Decision):
        return not (row.resolved or row.interrupted)
    if _is(row, events.TOOL_CALL):
        return states.get(str(row.data.get("id") or "")) in (RUNNING, QUEUED)
    return False


def _tool_run(
    rows: list[Any],
    start: int,
    answers: dict[str, dict],
    states: dict[str, str],
    timings: dict[str, state.Timing] | None = None,
) -> int:
    """Draw the run of tool calls at ``start``; return the index after it.

    One row for a stretch of calls, the treatment `_thinking_group` gives a
    stretch of reasoning. Asked for after the 2026-08-31 runs, where the
    indexing pass drew 98 cards and the chat 35, each in full.

    **A run only folds when every call in it is `DONE`.** Anything else is
    something to look at rather than something to scroll past: a call still
    running or queued is where the loop is right now, an errored one is a fact
    the copilot is about to reason from, and a `DROPPED` one never came back at
    all. Folding those would hide the three states a reader is scanning for, and
    put `_tool_queue`'s whole job behind a click.

    A `TOOL_RESULT` already drawn with its call is transparent here — it is not
    a row, so it cannot break a run — and everything else does break one.
    """
    end = _tool_run_end(rows, start, answers)
    timings = timings or {}
    cards = []
    for row in rows[start:end]:
        if _is(row, events.TOOL_CALL):
            call_id = str(row.data.get("id") or "")
            cards.append(
                (row.data, answers.get(call_id), states.get(call_id, DONE), timings.get(call_id))
            )
    _tool_cards(cards)
    return end


def _tool_run_end(rows: list[Any], start: int, answers: dict[str, dict]) -> int:
    """Where the run of calls at ``start`` ends: the first row that is not a
    call, or a result drawn with its call."""
    end = start
    while end < len(rows):
        row = rows[end]
        if _is(row, events.TOOL_CALL):
            pass
        elif not (_is(row, events.TOOL_RESULT) and str(row.data.get("id") or "") in answers):
            break
        end += 1
    return end


#: One call to draw: its event data, its result if any, its state, and the
#: window's clock on it if any.
Card = tuple[dict, "dict | None", str, "state.Timing | None"]


def _tool_cards(cards: list[Card]) -> None:
    """A run of calls: one folded row, or each card in full.

    Each unfolded card enters under **the SDK call id**, which is the one key
    that survives everything a run does to a card — the state moving from
    running to done, the position sliding as earlier rows fold, the whole pane
    rebuilding per event. Keyed by position, a card would re-animate every time
    the row above it changed shape.
    """
    if len(cards) < GROUP_MIN or any(state_name != DONE for _, _, state_name, _ in cards):
        for call, result, state_name, timing in cards:
            with c.enter_slot(f"call:{call.get('id') or ''}"):
                _tool_card(call, result, state_name, timing)
        return
    _tool_group(cards)


def _tool_group(cards: list[Card]) -> None:
    """*"N tool calls"*, and which ones, opening onto the cards themselves.

    Shut, it says the two things that decide whether you open it: **how many**,
    and **which tools** — a run of twenty-three `describe_source` calls is the
    app reading a folder, and a run of three that includes a `join_findings` is
    not, and the count alone cannot tell them apart. The tally is a count and
    orders by first appearance, not by size: what ran most is not what matters
    most (`DESIGN.md` → kind, never rank).

    **The open key is the first call's id and nothing else.** It has to be
    something that does not move as the run grows: keyed on the count or on the
    position, a group you opened at twenty-three cards becomes a different group
    when the twenty-fourth arrives and shuts itself under you — which is the bug
    `c.disclosure` exists to prevent, arriving by a new route. An SDK call id is
    unique per call, so it needs no `_thinking_group`-style length suffix.
    """
    key = f"calls:{cards[0][0].get('id') or ''}"
    names = [events.tool_label(str(call.get("name", ""))) for call, _, _, _ in cards]

    def header() -> None:
        ui.icon(TOOL_ICON).classes("tool-card-icon")
        ui.label(_N_CALLS.format(n=len(cards))).classes("tool-card-name")
        ui.label(_tool_tally(names)).classes("tool-card-args")

    def body() -> None:
        with ui.element("div").classes("tool-group-body"):
            for call, result, state_name, timing in cards:
                _tool_card(call, result, state_name, timing)

    with c.enter_slot(f"enter:{key}"):
        group = c.disclosure(header, body, open=key in APP.open_rows, on_toggle=_remember(key))
        group.classes("tool-card tool-group")


def _is_allowed_write(row: Any) -> bool:
    """A settled, allowed write in a live stream — decided, or never asked.

    **Resolved and allowed, or it is not one.** A pending decision is the loop
    stopped on a human and is never folded (`_write_group`), and a refusal is
    one of the two outcomes worth reading.

    The two shapes are here together because they are the same row to a reader
    and only one of them can be pending: a `Decision` exists *because* the loop
    stopped, and an automatic write arrives as a bare `APPROVAL` event that no
    `Decision` was ever made for (`exchange._owned`).
    """
    if _is(row, events.APPROVAL):
        return bool(row.data.get("auto"))
    return (
        isinstance(row, Decision)
        and row.kind == events.APPROVAL
        and row.resolved
        and row.outcome is True
    )


def _settled_write(row: Any) -> tuple[str, dict, bool]:
    """``(tool name, payload, was automatic)`` for either shape."""
    if _is(row, events.APPROVAL):
        return str(row.data.get("name", "")), row.data.get("input") or {}, True
    return str(row.payload["name"]), row.payload["input"], False


def _decision_run(rows: list[Any], start: int, answers: dict[str, dict], *, keys: str = "") -> int:
    """Draw the run of settled, allowed writes at ``start``; return the index after it.

    The live half of `_write_run`. The two exist separately because a live
    stream holds `Decision` objects — which carry the future the engine is
    waiting on — where a log holds the two events they were built from, and
    only one of those can be pending. They fold through the same
    `_write_group`, so the window and a replay of it cannot disagree.

    It takes ``answers`` for `_write_run`'s reason, and it is the same trap:
    **a row that draws nothing must not break a run.** An automatic write puts
    its `APPROVAL_RESULT` in the stream and the call it gated puts a
    `TOOL_RESULT` there, and both are drawn elsewhere — so without this, five
    automatic writes in a row are five rows.
    """
    end = _writes_end(rows, start, answers)
    settled = [(rows[at], at) for at in range(start, end) if _is_allowed_write(rows[at])]
    writes = [(_settled_write(row), position) for row, position in settled]
    if len(writes) < GROUP_MIN:
        for (name, payload, auto), position in writes:
            with c.enter_slot(f"{keys}:{position}"):
                _resolved_write_view(name, payload, True, auto=auto)
        return end
    with c.enter_slot(f"{keys}:writes:{start}"):
        _write_group(
            [write for write, _ in writes],
            # The id of the call it gated would be a steadier key, but a `Decision`
            # does not carry one — `ask.py` is handed the tool name and its input,
            # not the SDK's call id. The position is what there is, and a run of
            # resolved writes only grows at its end.
            key=f"writes:{start}",
        )
    return end


def _writes_end(rows: list[Any], start: int, answers: dict[str, dict]) -> int:
    """Where the run of allowed writes at ``start`` ends.

    An approval result is drawn with the request it resolves and a tool result
    with its call, so neither breaks the run (`_decision_run`).
    """
    end = start
    while end < len(rows):
        row = rows[end]
        if _is_allowed_write(row):
            pass
        elif _is(row, events.APPROVAL_RESULT):
            pass  # drawn with the request it resolves
        elif _is(row, events.TOOL_RESULT) and str(row.data.get("id") or "") in answers:
            pass  # drawn with its call
        else:
            break
        end += 1
    return end


def _write_run(rows: list[Any], start: int, answers: dict[str, dict]) -> int:
    """Draw the run of logged writes at ``start``; return the index after it.

    The replay half of `_tool_run`, and it takes ``answers`` for the same reason
    that one does: **a row that draws nothing cannot break a run.** An
    `APPROVAL_RESULT` is drawn with the request it resolves, and a `TOOL_RESULT`
    is drawn with its call. Both are transparent here, and the second is not a
    detail — the real 2026-08-31 indexing log interleaves the two events of
    every write with the *result of the call it gated*
    (`approval → tool_result → approval_result → approval → …`), so without this
    every run is one write long and the fold never fires. That is what the first
    version of this did, and driving the app is what showed it.
    """
    end, writes, foldable = start, [], True
    while end < len(rows):
        row = rows[end]
        if row.kind == events.APPROVAL:
            allowed = _outcome_after(rows, end)
            auto = bool(row.data.get("auto"))
            writes.append(
                (str(row.data.get("name", "")), row.data.get("input") or {}, allowed, auto)
            )
            # An automatic write folds with the allowed ones — it is the most
            # skippable row on the panel, being the one nobody was asked about.
            foldable = foldable and allowed is True
        elif row.kind == events.TOOL_RESULT:
            if str(row.data.get("id") or "") not in answers:
                break  # an orphan result draws on its own, so it is a row
        elif row.kind != events.APPROVAL_RESULT:
            break
        end += 1
    if len(writes) < GROUP_MIN or not foldable:
        for name, payload, allowed, auto in writes:
            _resolved_write_view(name, payload, allowed, auto=auto)
    else:
        _write_group([(n, p, a) for n, p, _, a in writes], key=f"replay:writes:{start}")
    return end


def _write_group(writes: list[tuple[str, dict, bool]], *, key: str) -> None:
    """*"N writes"* over a run of writes the human already allowed.

    The calls fold and the confirmations did not, which on a replayed indexing
    log left one *"22 tool calls"* line followed by twenty-two near-identical
    `set_interpretation / allowed / payload` blocks — the fold doing its job on
    cards and buying nothing on the screen. The SDK emits a message's tool_use
    blocks together and the approvals arrive after them, so the `APPROVAL`
    breaks a run rule fires correctly here and has nothing left to break.

    **Only writes that were allowed.** A refusal and a write the turn died on
    are the two outcomes worth reading — the second is how an interrupted run
    looks and `EVALUATION.md` cares about it — so either one stays a row of its
    own, on the same argument that keeps an errored call out of a fold.

    This is not `DESIGN.md`'s write-confirm being made quieter. That rule is
    about the live moment: a confirmation must never become a reflex, so the
    banner and the buttons stay as they are and a **pending** decision is never
    folded. What folds is a decision already taken, which is history you are
    auditing rather than a question you are being asked.
    """
    names = [events.tool_label(name) for name, _, _ in writes]
    # `allowed` and `allowed automatically` are two different claims, so a run
    # holding both says the weaker one. It never says "22 allowed" over writes
    # nobody was asked about.
    every_one_automatic = all(auto for _, _, auto in writes)

    def header() -> None:
        ui.icon(WRITE_ICON).classes("tool-card-icon")
        ui.label(_N_WRITES.format(n=len(writes))).classes("tool-card-name")
        ui.label(_tool_tally(names)).classes("tool-card-args")
        ui.label(_ALLOWED_AUTO if every_one_automatic else _ALLOWED).classes("tool-state")

    def body() -> None:
        with ui.element("div").classes("tool-group-body"):
            for name, payload, auto in writes:
                _resolved_write_view(name, payload, True, auto=auto)

    group = c.disclosure(header, body, open=key in APP.open_rows, on_toggle=_remember(key))
    group.classes("tool-card tool-group")


def _tool_tally(names: list[str]) -> str:
    """`describe_source ×23 · profile_source ×2`, in the order they first ran."""
    counts = Counter(names)
    return " · ".join(
        name if counts[name] == 1 else f"{name} ×{counts[name]}" for name in dict.fromkeys(names)
    )


def _call_states(rows: list[Any], answers: dict[str, dict], *, busy: bool) -> dict[str, str]:
    """Which call is running, which are waiting behind it, and which came back.

    The model asks for several tools in one message and the results arrive one
    at a time, so at any moment a live exchange has **one call in flight and a
    queue behind it**. Without that distinction every open call drew the same
    marker, and a card that has not started looked exactly like the one that is
    taking the time.

    It is arrival order and nothing else — the SDK decides what runs next, not
    this panel, and the queue is a report of that rather than a plan.
    """
    states: dict[str, str] = {}
    in_flight = False
    for row in rows:
        if not _is(row, events.TOOL_CALL):
            continue
        call_id = str(row.data.get("id") or "")
        result = answers.get(call_id)
        if result is not None:
            states[call_id] = ERRORED if result.get("is_error") else DONE
        elif not busy:
            # Nothing is coming: the exchange is over and this call never
            # answered. Saying "running" about it would be a spinner that spins
            # forever.
            states[call_id] = DROPPED
        elif in_flight:
            states[call_id] = QUEUED
        else:
            states[call_id] = RUNNING
            in_flight = True
    return states


def _pending_calls(rows: list[Any], *, busy: bool) -> list[tuple[str, str, str]]:
    """``(call id, tool name, state)`` for every call that has not come back, in order."""
    answers = _results_by_call(rows)
    states = _call_states(rows, answers, busy=busy)
    pending = []
    for row in rows:
        if not _is(row, events.TOOL_CALL):
            continue
        call_id = str(row.data.get("id") or "")
        state_name = states.get(call_id)
        if state_name in (RUNNING, QUEUED):
            pending.append((call_id, events.tool_label(str(row.data.get("name", ""))), state_name))
    return pending


def _tool_queue(stream, *, keys: str = "") -> None:
    """What the copilot is doing right now, and what is lined up behind it.

    One strip at the foot of the stream rather than a mark on each card: the
    cards say what each call *is*, and this says where the loop has got to. It
    is the answer to "is this hung?" without scrolling back up through the
    evidence to find the one card that has not returned.

    It only exists while something is in flight, and it disappears with the last
    result — there is no idle state to keep on screen. Its entrance key counts
    the exchanges, so it slides in once per message rather than once per chat.
    """
    pending = _pending_calls(stream.rows, busy=stream.busy)
    if not pending:
        return
    running = [(call_id, name) for call_id, name, kind in pending if kind == RUNNING]
    queued = [name for _, name, kind in pending if kind == QUEUED]
    with c.enter_slot(f"{keys}:queue:{len(stream.exchanges)}"):
        with ui.element("div").classes("tool-queue"):
            c.pulse()
            ui.label(running[0][1] if running else _WORKING).classes("tool-queue-now")
            timing = stream.timings.get(running[0][0]) if running else None
            if timing is not None:
                with ui.element("div").classes("tool-state"):
                    tool_clock(timing)
            if queued:
                c.caption(f"{len(queued)} queued")
                with ui.element("div").classes("tool-queue-chips"):
                    for name in queued:
                        ui.label(name).classes("tool-queue-chip")


def _is(row: Any, kind: str) -> bool:
    return isinstance(row, events.Event) and row.kind == kind


def _results_by_call(rows: list[Any]) -> dict[str, dict]:
    """Tool results keyed by the id of the call each one answers."""
    called = {str(r.data.get("id") or "") for r in rows if _is(r, events.TOOL_CALL)} - {""}
    return {
        str(r.data["id"]): r.data
        for r in rows
        if _is(r, events.TOOL_RESULT) and str(r.data.get("id") or "") in called
    }


def _row(row: Any, *, job: bool = False) -> None:
    if isinstance(row, Decision):
        _decision(row)
    elif isinstance(row, events.Event):
        _event(row, job=job)


def _event(event: events.Event, *, key: str = "think", job: bool = False) -> None:
    kind = event.kind
    if kind == events.PROMPT:
        # An indexing job's prompt is the app's own template, not a message —
        # so it is a shut disclosure rather than the thing you read first.
        _job_instruction(event.data) if job else _prompt_row(event.data)
        return
    if kind == events.TOOL_CALL:
        _tool_card(event.data, None, DROPPED)
        return
    if kind == events.TOOL_RESULT:
        _orphan_result(event.data)
        return
    if kind == events.THINKING:
        _thinking_row(str(event.data.get("text") or ""), key=key, live=False)
        return
    with ui.element("div").classes("transcript-row"):
        if kind == events.TEXT:
            # The copilot writes markdown, so it is rendered as markdown. Showing
            # `**sales_orders**` literally is the panel failing to read what it
            # was handed.
            c.markdown(event.data.get("text", ""))
        elif kind == events.ERROR:
            c.text(str(event.data.get("message", "")), color="c-error")
        elif kind == events.RESULT:
            _exchange_end(event.data)


# --- the copilot thinking ---------------------------------------------------


def _thinking_group(rows: list[Any], start: int, *, busy: bool) -> int:
    """Draw one row for the run of thinking blocks at ``start``; return the end.

    The model surfaces its reasoning in as many blocks as it feels like, and one
    row each stacked a column of identical labels down a chat — the same word
    six times, saying nothing about which of them held the part worth reading.
    Joined, it is one thing that happened and one thing to open.
    """
    end = _thinking_end(rows, start)
    parts = [str(row.data.get("text") or "").strip() for row in rows[start:end]]
    joined = "\n\n".join(part for part in parts if part)
    # The length is in the key so a rebuilt indexing transcript — whose rows are
    # replaced per job — cannot inherit the open state of a different block that
    # happened to land at the same position.
    key = f"think:{start}:{len(joined)}"
    _thinking_row(joined, key=key, live=busy and end == len(rows))
    return end


def _thinking_end(rows: list[Any], start: int) -> int:
    """Where the run of thinking blocks at ``start`` ends."""
    end = start
    while end < len(rows) and _is(rows[end], events.THINKING):
        end += 1
    return end


def _thinking_row(text: str, *, key: str, live: bool) -> None:
    """Reasoning, as one quiet line you can open.

    **It is shut by default and it says only that it is thinking.** The reasoning
    is worth having — it is why a question got asked the way it did — and it is
    not what the pane is for: printed in full it pushed the copilot's actual
    sentences, and the two components the loop stops on, off the bottom of the
    screen. So the row states the fact and holds the text one click away.

    While the exchange is live the ellipsis animates, and that is the whole of
    the motion: this is the one row that has to say *something is happening* in a
    pane that can otherwise sit still for a minute mid-profile.
    """

    def header() -> None:
        ui.label(_THINKING).classes("thinking-word")
        # The phase is stated once on the container and inherits to the three
        # dots, which each subtract their own stagger from it (`portia.css`).
        # This row rebuilds on **every** event of a live exchange, so without it
        # the ellipsis restarts several times a second — the twitch that got
        # reported first.
        with ui.element("div").classes("thinking-dots").style(c.pulse_phase()):
            for _ in range(_DOTS):
                ui.element("span")

    def body() -> None:
        c.text(text or _NO_THOUGHT, color="c-mute")

    row = c.disclosure(header, body, open=key in APP.open_rows, on_toggle=_remember(key))
    row.classes("thinking-row" + (" thinking-row--live" if live else ""))


def _remember(key: str) -> Callable[[bool], None]:
    """Record that this disclosure is open, or that it is shut.

    A factory rather than a lambda with a default argument, so the key is bound
    by the closure rather than by a parameter every caller could pass over.
    """
    return lambda on: APP.toggle_row(key, on)


def _exchange_end(data: dict) -> None:
    """How one exchange finished, in the middle of a chat that may carry on.

    A chat has no single ending until it is closed, so the cost lands per
    exchange rather than once at the foot. An interrupted one says so plainly:
    `error_during_execution` is what the SDK reports for a stop, and a message
    the human cut short is not an error to apologise for.
    """
    subtype = str(data.get("subtype") or "")
    cost = data.get("cost_usd")
    spend = f" · ~${cost:.4f}" if cost else ""
    if subtype == _STOPPED:
        c.caption(f"stopped{spend}")
    elif subtype and subtype != "success":
        c.caption(f"ended ({subtype}){spend}")
    elif spend:
        c.caption(spend.removeprefix(" · "))


def _job_instruction(data: dict) -> None:
    """The task prompt the **app** sent to open a job — kept, and shut.

    `prompt-row` is *what the human said* (`DESIGN.md`), and on an indexing job
    nobody said it: the text is `prompts/tasks/index_batch.md`, sent by the
    window on your behalf. Drawn as a message it was forty lines of instruction
    standing exactly where a human's own sentence stands in a chat — above
    everything the job then did, and the first thing the eye lands on.

    The `exchange-banner` above already says what this job is and why, so the
    instruction is a detail rather than the message. Nothing is dropped: prompt
    text is the least stable, most performance-sensitive part of the system
    (`CLAUDE.md`), and being able to read what a run was actually given is the
    point of logging it at all.
    """
    with ui.element("div").classes("transcript-row"):
        c.collapsed(_INSTRUCTION, lambda: c.markdown(str(data.get("text", ""))))
        c.caption(_model_line(data))


def _model_line(data: dict) -> str:
    """What an exchange ran on. A chat can change model between messages, so it
    is stated per exchange rather than once in the log's header."""
    model = str(data.get("model") or "")
    if not model:
        return ""
    effort = f" · {data['effort']}" if data.get("effort") else ""
    return f"{model.replace('claude-', '')}{effort}"


def _prompt_row(data: dict) -> None:
    """What the human said, opening an exchange.

    Its own row kind rather than a `transcript-row`, because in a chat of six
    messages this is the only thing separating one exchange from the next. It
    states the model it ran on: a chat can change model mid-way, and the banner
    above only says what the *next* one will use.

    **It is a block on the elevated surface, not a rule across the pane.** What
    separates one exchange from the next is that a message has an author, so the
    thing to draw is the message; a horizontal rule says "new section" and left
    the human's own words set exactly like the copilot's. The surface step is a
    statement of *kind* — who said this — and carries no colour, no accent and no
    prominence over the reply beneath it (`DESIGN.md`).
    """
    with ui.element("div").classes("prompt-row"):
        c.text(str(data.get("text", "")), color="c-ink")
        line = _model_line(data)
        if line:
            with ui.element("div").classes("prompt-row-spend"):
                c.provider_glyph(str(data.get("provider") or providers.DEFAULT_KIND))
                c.caption(line)


def _tool_card(
    call: dict, result: dict | None, state_name: str, timing: state.Timing | None = None
) -> None:
    """One tool call as a card: which tool, what about, and what came back.

    Shut, it answers the three questions a reader scanning past has — **which
    tool** (an icon and the name), **for what** (its subject, the one argument
    the call is *about*, pulled out of the payload and set in `{colors.ink}`),
    and **where it got to** (running, queued, or how much evidence came back).
    The rest of the arguments trail behind in `{colors.mute}` and are what the
    line gives up first when the pane is narrow.

    Opened, it is two labelled sections — what was asked, laid out through the
    same `payload_view` the write confirmation uses rather than dumped as a
    repr, and what came back. The result is one click away rather than inline
    because it is the evidence the copilot acted on (`docs/VISION.md`) and it is
    also, routinely, a page of JSON between two sentences.

    **No tick on a call that returned.** A tool coming back is not a finding, and
    a green mark beside every check is the screen deciding it went well
    (`DESIGN.md` → green is the rarest colour in the app). What a finished card
    states instead is a *count* — how much came back — which is a fact and not a
    verdict.
    """
    name = events.tool_label(str(call.get("name", "")))
    call_id = str(call.get("id") or name)
    arguments = {k: v for k, v in (call.get("input") or {}).items() if k not in c.HIDDEN_FIELDS}
    subject_key = next((f for f in SUBJECT_FIELDS if arguments.get(f)), None)
    subject = str(arguments[subject_key]) if subject_key else ""
    rest = {k: v for k, v in arguments.items() if k != subject_key}

    def header() -> None:
        ui.icon(TOOL_ICONS.get(name, TOOL_ICON)).classes("tool-card-icon")
        ui.label(name).classes("tool-card-name")
        if subject:
            ui.label(subject).classes("tool-card-subject")
        if rest:
            ui.label(_summarize(rest)).classes("tool-card-args")
        _tool_state(state_name, result, timing)

    def body() -> None:
        with ui.element("div").classes("tool-card-body"):
            if arguments:
                _card_section(_ASKED, lambda: c.payload_view(arguments))
            _card_section(_CAME_BACK, lambda: _tool_result(result, state_name))

    card = c.disclosure(header, body, open=call_id in APP.open_rows, on_toggle=_remember(call_id))
    card.classes(f"tool-card tool-card--{state_name}")


def _tool_state(state_name: str, result: dict | None, timing: state.Timing | None = None) -> None:
    """Where this call got to, at the right of its head.

    Kind, never rank: `queued` and `running` are two different things happening,
    not two rungs of a severity ladder, and neither is coloured against the
    other. Only `error` takes a colour, because a tool that failed is a fact the
    copilot is about to reason from.

    **And how long**, when the window has a clock on the call. A running call
    carries a ticking one (`tool_clock`); a finished one says what it took,
    beside the volume that came back, in the same quiet type. A three-minute
    profile with nothing moving on screen reads as a hang (2026-09-06, on
    BigQuery), and the difference between working and stuck is the clock.
    """
    if state_name == RUNNING:
        with ui.element("div").classes("tool-state tool-state--running"):
            c.pulse()
            ui.label(RUNNING)
            if timing is not None:
                tool_clock(timing)
    elif state_name == QUEUED:
        with ui.element("div").classes("tool-state tool-state--queued"):
            ui.icon("schedule").classes("tool-state-icon")
            ui.label(QUEUED)
    elif state_name == ERRORED:
        with ui.element("div").classes("tool-state tool-state--error"):
            ui.label(_ERROR)
            _took(timing)
    elif state_name == DROPPED:
        ui.label(_NO_RESULT).classes("tool-state")
    elif result is not None:
        with ui.element("div").classes("tool-state"):
            ui.label(_volume(str(result.get("text") or "")))
            _took(timing)


def _took(timing: state.Timing | None) -> None:
    """What a finished call took, if the window measured it. Static: no tick."""
    if timing is not None and timing.ended is not None:
        ui.label(present.duration(timing.elapsed)).classes("tool-clock")


@ui.refreshable
def tool_clock(timing: state.Timing) -> None:
    """How long a running call has been running, moving once a second.

    `app.progress_note`'s shape, for `app.progress_note`'s reason: its own
    refreshable, so the tick rebuilds one label per running card and never
    the rows around it. Every instance refreshes with the `Timing` it was
    drawn with, and a card rebuilt by a streamed event makes a fresh instance;
    NiceGUI prunes the ones whose card is gone before each refresh.
    """
    ui.label(present.duration(timing.elapsed)).classes("tool-clock")


def tick_clocks() -> None:
    """Move every running call's clock on. Registered once per page (`app.page`)."""
    if APP.busy:
        tool_clock.refresh()


def _tool_result(result: dict | None, state_name: str) -> None:
    """The evidence, or one line saying why there isn't any yet."""
    if result is not None:
        c.code_block(str(result.get("text") or _NO_RESULT_YET))
        return
    c.caption(_WAITING_FOR.get(state_name, _NO_RESULT))


def _card_section(label: str, draw: Callable[[], Any]) -> None:
    with ui.element("div").classes("tool-card-section"):
        ui.label(label).classes("tool-card-section-label")
        draw()


def _summarize(arguments: dict) -> str:
    """The arguments that aren't the subject, short enough to sit on one line.

    Each value is clipped on its own rather than the whole string at the end, so
    a call whose first argument is a page of SQL still shows the *names* of the
    three arguments after it. The line then truncates in CSS at whatever width
    the pane happens to be.
    """
    parts = []
    for key, value in arguments.items():
        rendered = value if isinstance(value, str) else repr(value)
        parts.append(f"{key}={_clip(str(rendered), VALUE_CHARS)}")
    return _clip(" · ".join(parts), ARG_CHARS)


def _volume(text: str) -> str:
    """How much evidence came back. **A count, never a verdict** — nothing here
    says a big result is a good one, and nothing sorts by it."""
    if len(text) < _THOUSAND:
        return f"{len(text)} chars"
    return f"{len(text) / _THOUSAND:.1f}k chars"


def _orphan_result(data: dict) -> None:
    label = "result (error)" if data.get("is_error") else "result"
    text = data.get("text") or ""
    with ui.element("div").classes("transcript-row"):
        c.collapsed(label, lambda: c.code_block(text))


def _clip(value: str, limit: int) -> str:
    return value if len(value) <= limit else f"{value[:limit]}…"


# --- the decisions ----------------------------------------------------------


def _decision(decision: Decision) -> None:
    """A moment the loop stopped — as a live form, an answer, or an interruption.

    **A cancelled decision is drawn as interrupted, never as a form.** When a
    message is stopped mid-question the SDK cancels the parked callback, so the
    future behind that form is one nobody will ever read (`CONVERSATION.md` §8).
    Leaving it looking answerable is the "silently does the wrong thing" this
    project forbids, in the one place the human is being asked to act.
    """
    if decision.interrupted:
        _interrupted_decision(decision)
    elif decision.kind == events.QUESTION:
        _question_form(decision) if not decision.resolved else _answered(decision)
    else:
        _write_confirm(decision) if not decision.resolved else _resolved_write(decision)


def _decision_head(icon: str, title: str, pill: str, *, accent: bool = False) -> None:
    """What kind of moment this is, at the top of the card it opens.

    The two components the loop stops on used to open on a lone `{colors.mute}`
    caption, which reads as a note about the thing under it rather than as the
    thing itself. A card with a named head says *what is being asked of you*
    before the eye has to work it out from the controls below.

    The pill takes the accent on a question and stays uncoloured on a write. That
    is kind and not rank: a question is the loop waiting on you, and a write is
    the loop offering to make something durable — neither is worse than the
    other, and the one that wants a *sentence* out of you is the one that says so.
    """
    with ui.element("div").classes("decision-head"):
        ui.icon(icon).classes("decision-head-icon")
        ui.label(title).classes("decision-head-title")
        ui.label(pill).classes("decision-pill" + (" decision-pill--accent" if accent else ""))


def _interrupted_decision(decision: Decision) -> None:
    if decision.kind == events.QUESTION:
        _asked_view(decision.payload.get("questions") or [])
    else:
        _resolved_write_view(
            str(decision.payload.get("name", "")), decision.payload.get("input") or {}, None
        )
    with ui.element("div").classes("transcript-row"):
        c.caption(_INTERRUPTED)


def _question_form(decision: Decision) -> None:
    """The form, with its actions ruled off at the foot.

    **The footer is the shape of a decision.** Everything above it is what is
    being asked; the one thing below it is what settles it, and separating them
    is what stops a form reading as a stack of controls you scroll through. It is
    the same footer as `write-confirm`, because both are the loop stopping for a
    human and two footers that had to be kept looking alike would drift.
    """
    with ui.element("div").classes("question-form decision-card"):
        _decision_head("help_outline", _QUESTION_TITLE, _WAITING_ON_YOU, accent=True)
        for question in decision.payload["questions"]:
            _one_question(decision, question)
        with ui.element("div").classes("decision-actions"):
            # **The accent is on the thing that resumes the loop.** A pending
            # question is the one moment the app is stopped waiting on a human,
            # and it is not a write: the accent's scarcity rule is satisfied
            # because the composer is showing Stop, not Send, while a message is
            # in flight. Approving a *write* is still deliberately not this
            # (`_write_confirm`, DESIGN.md).
            c.button("Answer", lambda: _submit_answers(decision), kind="primary", icon="check")


def _one_question(decision: Decision, question: dict) -> None:
    key = question["question"]
    draft = decision.draft.setdefault(key, {CHOSEN: [], FREE_TEXT: ""})
    multi = bool(question.get("multiSelect"))

    rows: dict[str, ui.element] = {}
    with ui.element("div").classes("stack-sm"):
        if question.get("header"):
            c.caption(str(question["header"]).upper())
        # The copilot writes markdown, and it writes a column name as `code`.
        # Set as a plain label, the most important sentence in the app showed
        # its own punctuation — the same failure `TEXT` was fixed for, in the
        # one place a reader cannot skim past it. The wrapper is what keeps it
        # `{typography.heading-sm}` rather than a markdown paragraph.
        with ui.element("div").classes("question-head"):
            c.markdown(str(key))
        # Rendered in the order the agent gave them. Never re-ordered, never
        # recommended-badged: which option is best is the human's call, and the
        # screen is not a participant.
        with ui.element("div").classes("option-list"):
            for option in question.get("options") or []:
                label = str(option.get("label", ""))
                rows[label] = _option_row(option, draft, rows, multi=multi)
        ui.textarea(placeholder=_ANSWER_PLACEHOLDER).classes(
            "p-field p-editor answer-field w-full"
        ).props("borderless autogrow").bind_value(draft, FREE_TEXT)


def _option_row(option: dict, draft: dict, rows: dict, *, multi: bool) -> ui.element:
    """One option, with a mark saying whether it is picked.

    **The mark is drawn, not merely implied by the border.** Selection used to be
    a border colour and a wash, which is the same signal the focused-input state
    uses and is invisible at a glance on a list of four. Its *shape* is what says
    how many you may pick — a circle for one, a box for several — so the rule is
    stated by the control rather than in a caption nobody reads twice.
    """
    label = str(option.get("label", ""))
    selected = label in draft[CHOSEN]
    classes = "option-row" + (" option-row--multi" if multi else "")
    classes += " option-row--selected" if selected else ""
    with ui.element("div").classes(classes) as row:
        ui.element("div").classes("option-mark")
        with ui.element("div").classes("option-body"):
            ui.label(label).classes("t-body-strong c-ink pre-wrap")
            if option.get("description"):
                # Markdown for the same reason the question is: a description
                # naming a column names it in backticks.
                with ui.element("div").classes("option-description"):
                    c.markdown(str(option["description"]))
    row.on("click", lambda: _pick(draft, rows, label, multi=multi))
    return row


def _pick(draft: dict, rows: dict, label: str, *, multi: bool) -> None:
    """Repaint the rows in place rather than rebuilding the pane.

    A refresh here would scroll the transcript back to the top, throwing away
    the evidence the human is reading in order to answer — which is the one
    thing this panel exists to keep on screen.
    """
    chosen = draft[CHOSEN]
    if label in chosen:
        chosen.remove(label)
    elif multi:
        chosen.append(label)
    else:
        draft[CHOSEN] = [label]

    for name, row in rows.items():
        row.classes(remove="option-row--selected")
        if name in draft[CHOSEN]:
            row.classes(add="option-row--selected")


def _submit_answers(decision: Decision) -> None:
    """Free text wins and goes through verbatim; otherwise the labels picked.

    **An unanswered question says so.** An empty answer is worse than no answer,
    so this refuses — but it used to refuse by returning, which on screen is a
    button that does nothing when pressed and is indistinguishable from a hung
    loop. It notifies rather than disabling the button, because free text is
    typed into a bound field that reports nothing until it is read, and a Send
    that greys out only for the *options* half would be lying about the other.
    """
    answers = {}
    for question in decision.payload["questions"]:
        key = question["question"]
        draft = decision.draft.get(key) or {}
        typed = (draft.get(FREE_TEXT) or "").strip()
        chosen = draft.get(CHOSEN) or []
        if typed:
            answers[key] = typed
        elif len(chosen) == 1 and not question.get("multiSelect"):
            answers[key] = chosen[0]
        elif chosen:
            answers[key] = list(chosen)
        else:
            ui.notify(_NOTHING_SAID)
            return
    decision.resolve(answers)
    pane.refresh()


def _answered(decision: Decision) -> None:
    _answers_view(decision.outcome or {})


def _answers_view(answers: dict) -> None:
    """What was asked and what was said, live or replayed from a log."""
    for question, answer in answers.items():
        with ui.element("div").classes("transcript-row"):
            # Markdown, as the form rendered it. A question that showed its own
            # backticks only *after* it was answered would read as a second,
            # rougher copy of the one the human actually replied to.
            c.markdown(str(question))
            ui.label(_as_text(answer)).classes("t-body c-accent pre-wrap")


#: Where each write names what it is writing to. Shown once, in the header.
TARGET_FIELDS = ("spec_path", "source", "name")


def _write_confirm(decision: Decision) -> None:
    name = events.tool_label(str(decision.payload["name"]))
    payload = decision.payload["input"]
    step = payload.get("step") or {}
    acknowledge = list(step.get("acknowledge") or [])
    target = next((f for f in TARGET_FIELDS if payload.get(f)), None)

    # Said once each: the target is in the header, and an acknowledged step's
    # flags and rationale are in the banner. Repeating either buries the parts
    # that differ — and `acknowledge` was being stated twice, as a badge in the
    # banner and again as a YAML block under it, which is the one field on this
    # panel where a second, quieter copy reads as a second, lesser thing.
    skip = c.HIDDEN_FIELDS + tuple(f for f in (target,) if f)
    if acknowledge:
        skip += ("rationale", "acknowledge")

    with ui.element("div").classes("write-confirm decision-card"):
        if acknowledge:
            c.acknowledged_banner(acknowledge, rationale=step.get("rationale"))
        # The pill is uncoloured here and accented on a question, which is the
        # difference between *this is waiting on you* and *this wants to write*.
        _decision_head("edit_note", _WRITE_TITLE, _NEEDS_APPROVAL)
        with ui.element("div").classes("write-confirm-head"):
            with ui.element("div").classes("row-gap-sm"):
                ui.icon(TOOL_ICONS.get(name, TOOL_ICON)).classes("tool-card-icon")
                ui.label(name).classes("tool-card-name")
                if target:
                    ui.label(str(payload[target])).classes("tool-card-subject")
        c.payload_view(payload, skip=skip)
        # Deny first and always present; Allow takes the accent, as Answer does
        # on a question. The accent marks the loop waiting on a human, and both
        # of these are that — what stops Allow reading as *the* thing to press is
        # the banner above it, not a quieter button (DESIGN.md → `write-confirm`).
        with ui.element("div").classes("decision-actions"):
            c.button("Deny", lambda: _resolve_write(decision, False), kind="secondary")
            _allow_controls(decision, name)


def _allow_controls(decision: Decision, name: str) -> None:
    """Allow, and beside it the ways to stop being asked (`CONVERSATION.md` §14.5).

    **VS Code's shape**: one primary button that allows this write, and a caret
    on it opening the wider scopes — this tool for the rest of the window, or
    every write from here on. They were a second labelled button beside Allow,
    which made the footer two sentences wide and put *stop asking* at the same
    weight as the decision the loop is stopped on.

    **Only a switchable tool gets the per-tool row** (`state.AUTO_ALLOWABLE`).
    `record_step` never offers it, because recording a step runs the step, so
    there is no version of *stop asking* about it that is not *stop looking*.
    Autopilot is offered on every write, because it is one mode you turn on for
    a task you are watching, and the card is where you find out you want it.
    """
    with ui.element("div").classes("btn-group"):
        c.button("Allow", lambda: _resolve_write(decision, True), kind="primary")
        more = c.button("", None, icon="arrow_drop_down", kind="primary").classes("btn-group-more")
        # No tooltip *(2026-09-18, the user's call)*. It popped a box over the
        # card on the way to a menu whose two rows say what they do, which is
        # `artifact_row`'s finding about a tooltip repeating its own line. The
        # words stay where a screen reader finds them.
        more.props(f"aria-label={c.prop_value(_ALLOW_MORE_TIP)}")
        with more, ui.menu().classes("p-menu"):
            with ui.element("div").classes("p-menu-panel p-menu-actions"):
                if name in state.AUTO_ALLOWABLE:
                    c.menu_row(
                        "check",
                        _ALLOW_ALWAYS.format(what=state.WRITE_LABELS[name]),
                        lambda: _allow_always(decision),
                    )
                c.menu_row("bolt", _ALLOW_ALL, lambda: _allow_all(decision))


def _allow_all(decision: Decision) -> None:
    """Allow this write and switch to autopilot for the rest of the window.

    The same flag the composer's picker and Settings set, changed from the card
    where somebody found out they wanted it. `exchange.auto_allow` reads it at
    the next write, so nothing after this one stops until the picker is moved
    back.
    """
    from portia.ui import settings

    APP.autopilot = True
    _resolve_write(decision, True)
    settings.refresh_if_open()


def _allow_always(decision: Decision) -> None:
    """Allow this write, and stop asking about this tool for the rest of the window.

    The same setting `ui/settings.py` holds, changed from where the question is
    being asked — which is where anyone actually decides they are tired of it.
    Two places to change one setting, and never two settings (`CLAUDE.md` →
    `ui/settings.py`).

    It does **not** reach back and auto-allow anything already on screen. The
    writes above this one were decisions somebody made, and rewriting them as
    automatic would put a claim in the log that is not true.
    """
    APP.auto_allow |= {events.tool_label(str(decision.payload["name"]))}
    _resolve_write(decision, True)


def _resolve_write(decision: Decision, allowed: bool) -> None:
    decision.resolve(allowed)
    pane.refresh()


def _resolved_write(decision: Decision) -> None:
    _resolved_write_view(
        str(decision.payload["name"]), decision.payload["input"], bool(decision.outcome)
    )


def _resolved_write_view(
    name: str, payload: dict, allowed: bool | None, *, auto: bool = False
) -> None:
    """A write and what the human did with it — live, or replayed from a log.

    One renderer for both, because a replay that laid a write out differently
    from the panel it happened in would be a second opinion about the same
    moment. ``allowed`` is None when the log has the request and not the
    outcome: a turn killed at the confirmation prompt, which is a real thing
    that happens and is not the same as a refusal.

    ``auto`` is a **third** outcome and not a quieter kind of allowed: nobody
    allowed it. It is still drawn in full, because the write happened and a
    transcript that hid it would be hiding a durable change — what it must not
    do is read as a decision (`runlog.summary` → ``auto_approved``).
    """
    if auto:
        outcome = _ALLOWED_AUTO
    else:
        outcome = "unanswered" if allowed is None else ("allowed" if allowed else "declined")
    with ui.element("div").classes("transcript-row"):
        with ui.element("div").classes("row-gap-sm"):
            c.mono(events.tool_label(name), color="c-ink")
            c.caption(outcome, color="c-accent" if allowed else "c-mute")
        c.collapsed("payload", lambda: c.payload_view(payload))


# --- a turn that already happened -------------------------------------------


def replay(run: Any) -> None:
    """A logged turn, rendered where a live one would be (`portia/runlog.py`).

    Same renderers as the live panel, deliberately: the log stores the events
    the panel was drawing, so replaying them through anything else would be a
    second opinion about a turn that is already written down. What differs is
    that nothing here is answerable — the questions were answered months ago,
    and a form you can fill in on a dead turn is a lie about what it would do.

    Questions and writes each arrive as two events — asked, then answered — and
    each becomes **one** row, because that is what the live panel shows once a
    decision resolves: the answer, not the form that collected it. Drawing both
    halves listed the same question twice, which reads as the copilot having
    asked it twice.
    """
    from portia import runlog

    answers = _results_by_call(run.events)
    states = _call_states(run.events, answers, busy=False)
    # The same clock a live card carries, read back off the log's stamps.
    timings = {
        call_id: state.Timing.of(seconds) for call_id, seconds in runlog.call_durations(run).items()
    }
    # A replayed **indexing** log opens on the same app-written instruction the
    # live tab does, and it reads the same way there: shut.
    job = getattr(run, "kind", runlog.CHAT) == runlog.INDEXING
    # A `while` rather than `enumerate`, because a run of calls is drawn by one
    # pass that consumes several events. `at` stays the position of the event in
    # hand, which is what `_answered_after`, `_outcome_after` and the replay keys
    # are all asking about.
    index = 0
    while index < len(run.events):
        at = index
        event = run.events[at]
        if event.kind == events.TOOL_CALL:
            # The same fold as the live pane, through the same helper. A saved
            # log that laid out ninety-eight cards where the window shows six
            # rows would be the second opinion `replay` exists to avoid.
            index = _tool_run(run.events, at, answers, states, timings)
            continue
        if event.kind == events.APPROVAL:
            index = _write_run(run.events, at, answers)
            continue
        index = at + 1
        if event.kind == events.TOOL_RESULT and str(event.data.get("id") or "") in answers:
            continue  # drawn with the call it answers
        if event.kind == events.QUESTION:
            if _answered_after(run.events, at):
                continue  # the ANSWER draws it, exactly as the live panel does
            # No answer under it: the turn ended with the question on screen.
            # That is the shape of an interrupted run and `EVALUATION.md` cares
            # about it, so the options it was offering stay visible.
            _asked_view(event.data.get("questions") or [])
            c.empty_note(_UNANSWERED)
        elif event.kind == events.ANSWER:
            _answers_view(event.data.get("answers") or {})
        elif event.kind == events.APPROVAL_RESULT:
            continue  # drawn with the request it resolves
        else:
            # The key is the event's position in this log, so two thinking rows
            # in one replay are two disclosures and not one shared toggle.
            _event(event, key=f"replay:{at}", job=job)


def _answered_after(rows: list, index: int) -> bool:
    """Whether the question at ``index`` ever got an answer."""
    for event in rows[index + 1 :]:
        if event.kind == events.ANSWER:
            return True
        if event.kind == events.QUESTION:
            break  # the next question — this one was never answered
    return False


def _outcome_after(rows: list, index: int) -> bool | None:
    """The allow/deny that resolved the write at ``index``, if the log has one."""
    for event in rows[index + 1 :]:
        if event.kind == events.APPROVAL_RESULT:
            return bool(event.data.get("allowed"))
        if event.kind == events.APPROVAL:
            break  # the next write's request — this one was never resolved
    return None


def _asked_view(questions: list[dict]) -> None:
    """The question as it was put, with its options in the order given.

    Never re-ordered and never badged, replayed or live: which option was best
    is what the human was being asked, and the screen is not a participant.
    """
    for question in questions:
        with ui.element("div").classes("transcript-row"):
            c.markdown(str(question.get("question", "")))
            for option in question.get("options") or []:
                c.caption(str(option.get("label", "")))


# --- the end of an indexing job ---------------------------------------------
#
# Only a job draws this. A **chat** has no single ending, so its cost lands per
# exchange in the transcript (`_exchange_end`) and its totals sit under the
# composer (`_chat_footer`).


def _turn_ended(chat) -> None:
    turn = chat.exchange
    assert turn is not None
    with ui.element("div").classes("chat-ended"):
        if turn.error:
            c.text(turn.error, color="c-error")
        c.caption(_ended_line(turn))
        spend = _spend_line(turn)
        if spend:
            c.caption(spend)
        c.caption(_JOB_IS_ONE_SHOT)


def _ended_line(turn: Any) -> str:
    cost = f" · ~${turn.cost_usd:.4f}" if turn.cost_usd else ""
    return f"ended ({turn.subtype or 'stopped'}) · {turn.model}{cost}"


def _spend_line(turn: Any) -> str:
    """What the turn cost, in tokens — a **count**, next to the cost in money.

    The numbers are `runlog.token_totals`', not this panel's, so the window and
    `cli.history` cannot disagree about one turn. `in` is the whole input including
    what came from cache, which on a portia turn is nearly all of it: the L0
    prompt and the L1 brief go on every request, and the SDK's raw
    `input_tokens` counts only the part that was not cached — one real run
    reported 17 for a turn that sent 14,651.

    Reported, never judged. Whether a turn was expensive needs a goal, and this
    panel has no way to know one.
    """
    if turn.input_tokens is None:
        return ""
    cached = f" ({c.count(turn.cached_tokens or 0, 'cached')})" if turn.cached_tokens else ""
    return (
        f"{c.count(turn.input_tokens, 'token')} in{cached}"
        f" · {c.count(turn.output_tokens or 0, 'token')} out"
    )


def _as_text(answer: Any) -> str:
    return ", ".join(str(a) for a in answer) if isinstance(answer, list) else str(answer)


#: What each non-goal turn is, in the panel it shares with the chat.
_BANNER_ICON = {state.INDEXING: "inventory_2", state.REREAD: "autorenew"}
_BANNER_TITLE = {state.INDEXING: "Indexing", state.REREAD: "Re-reading"}
_BANNER_WHY = {
    state.INDEXING: "Profiled. The copilot is reading them now.",
    state.REREAD: "The copilot is re-reading this source with your note.",
}

_RUNNING = "running"
_WAITING = "waiting for you"

#: The list (`docs/CHAT_SESSIONS.md` §3.2). One glyph per kind, and the same
#: two the left pane used for the same two things.
_CHAT_ICON = "forum"
_JOB_ICON = "inventory_2"
_CHATS = "Chats"
_NO_CHATS = "No chats yet. Describe your goal below to start one."
_TODAY = "Today"
_YESTERDAY = "Yesterday"
_UNDATED = "Undated"
_UNTITLED = "Untitled chat"
_SOURCES_TITLE = "Sources"
_SOURCES_META = "what portia knows about each one, and indexing"
_LEGACY = "legacy"
_READ_ONLY = "Read-only: {why}."
_PICKED_UP = "picked up here"
_RESUMED = "resumed · history re-read at full price once"
_BUSY_ELSEWHERE = "{title} is running. Stop it there, or wait."
_NEW_CHAT_TIP = "New chat"
_RENAME_TIP = "Rename"
_DELETE_TIP = "Delete"
_DELETE_ASK = "Delete this chat? Nothing can bring its log back."
_DELETE_GO = "Delete"
_DELETE_CANCEL = "Cancel"
_DELETE_BUSY = "Stop it first, then delete it."

_UNANSWERED = "The exchange ended with this question unanswered."
_INTERRUPTED = "stopped before this was answered"

#: What each of the two decision components says it is. One quiet line each: the
#: accent border already finds the eye, and neither is a warning — a question is
#: the loop working, and a write waiting on a yes/no is the loop working too.
_WAITING_ON_YOU = "waiting for you"
_NEEDS_APPROVAL = "needs your approval"
_NOTHING_SAID = "Pick an option or type an answer."

#: A tool call whose result has not come back. Only the open one is marked; a
#: finished card says how much came back and nothing about whether that is good.
_NO_RESULT_YET = "…"
_NO_RESULT = "no result"
_ERROR = "error"
_WORKING = "working"
_ASKED = "asked"
_CAME_BACK = "came back"
_WAITING_FOR = {
    RUNNING: "waiting for this result",
    QUEUED: "queued behind the call above",
    DROPPED: "the exchange ended before this returned",
}

#: What an indexing job was told to do. The app wrote it, not the human, so it
#: is named as an instruction rather than shown as a message.
_INSTRUCTION = "the instruction this job was given"

#: A folded run of finished calls, and one of writes already allowed. Plural
#: throughout: `GROUP_MIN` is 2, so neither has to say "1".
_N_CALLS = "{n} tool calls"
_N_WRITES = "{n} writes"
_ALLOWED = "allowed"
#: Under Allow's caret. It names what the tool *writes* rather than saying
#: "always", because what it switches off is this tool's confirmations and not
#: this project's, and a tool name is not a sentence (`state.WRITE_LABELS`).
_ALLOW_ALWAYS = "Allow, and stop asking about {what}"
_ALLOW_ALL = "Allow every write from here on: autopilot"
_ALLOW_MORE_TIP = "More ways to allow"
#: What a write that never stopped says in a transcript. Not "allowed": nobody
#: allowed it, and the log counts the two apart (`runlog.summary`).
_ALLOWED_AUTO = "allowed automatically"

#: The one row that animates, and the word it animates under.
_THINKING = "thinking"
_NO_THOUGHT = "nothing recorded"

#: What each decision card is, in its head. The pill under it says what it wants.
_QUESTION_TITLE = "Question"
_WRITE_TITLE = "Write"

#: What the SDK reports for an interrupted message — not an error to apologise
#: for, since the human asked for it (`CONVERSATION.md` §8).
_STOPPED = "error_during_execution"

_IDLE = "No messages yet. Describe your goal below."
#: The client is being started for the message just sent; nothing has come
#: back yet. The word is the whole claim, and the detail is where it is going.
_STARTING = "starting"
#: An exchange that ended without a reply, with the exception in the engine's
#: own words. Not a refusal the copilot made; nothing reached it.
_FAILED = "The message was not answered. {error}"
_IDLE_JOB = "Nothing recorded for this job."
_NO_SOURCES = "No data in this project yet. Add data from the left pane."
_INDEX_COST = "Profiling is free. Reading spends a model exchange."
_INDEX_COST_REMOTE = "Profiling a warehouse table scans it there, once, on its meter. Reading spends a model exchange."
#: A warehouse table nobody has scanned (`catalog.is_profiled`). The same two
#: words on the left pane's row and in the inspector.
_METADATA_ONLY = "metadata only"
_INDEX_WHAT = (
    "Reads appear here, from Add data or from Ask the copilot on a table. "
    "Tables the pipeline built are listed beside the sources."
)
#: The compiled model's glyph (`artifacts.ICON[MODEL]`), on a built table's row.
_MODEL_GLYPH = "code"
#: The keystroke that sends, named where a pointer will look for it. The button
#: says what it does and this says how else to do it; a shortcut nothing draws
#: is one only the person who wrote it knows about.
_SEND_TIP = "Send · \u2318\u21a9 or Ctrl\u21a9"
_GOAL_PLACEHOLDER = "What do you want from this data?"
_FOLLOW_UP_PLACEHOLDER = "Reply, or ask for something else…"
_ANSWER_PLACEHOLDER = "Or answer in your own words"
#: Indexing is a **job**, not a conversation (`docs/CONVERSATION.md` §6): the app
#: ran it on your behalf, it has a defined end, and there is nothing to reply to.
#: Correcting what it decided is `Ask the copilot` on the source itself, which is
#: the route that already existed.
_JOB_IS_ONE_SHOT = (
    "Indexing is a job, not a conversation. To correct a source's read, open the "
    "source and ask the copilot to re-read it."
)


def _open_server_dialog(kind: str) -> None:
    """The start panel for a provider portia can start (`PROVIDERS.md` §4.9)."""
    from portia.ui import screens

    screens.open_server_dialog(kind)

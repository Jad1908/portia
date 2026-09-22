"""The component vocabulary from DESIGN.md, once each.

Every pane builds from these, so a rule lives in one place: a `flag-badge` is the
same size whatever number produced it, an `acknowledged-banner` cannot collapse,
and `table-preview` says how many rows it is showing. Reuse before you add.

Two rules are enforced here rather than trusted to each caller:

- **No ranking.** Nothing sorts by severity, sizes a badge by its number, or
  rolls findings up into a score. Colour says *kind*: `error` is a blocking zero,
  `warning` is drift, the accent is an acknowledged override, and everything else
  is uncoloured.
- **No computing.** These render values the engine produced. The only arithmetic
  is `len(frame)` for the preview's honest `showing N of M`.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any

import pandas as pd
from nicegui import ui

from portia.checks.outcome import BLOCKING_FLAGS
from portia.core.present import as_yaml, count, inline
from portia.ui.state import PREVIEW_ROWS

#: `flag-badge` variants. Three, and no others (DESIGN.md).
BLOCKING = "blocking"
DRIFT = "drift"
ACKNOWLEDGED = "ack"

#: Step fields that are decisions in their own right and get their own block in a
#: laid-out payload, rather than a one-line value.
BLOCK_FIELDS = ("sql", "expect", "transforms", "rationale", "acknowledge")

#: Fields a human wrote as English, not as data. The test from DESIGN.md: if a
#: human typed it as data or an identifier it is mono; if they wrote it as
#: English it is not. A source summary in monospace reads like a machine said it.
PROSE_FIELDS = ("rationale", "summary", "context")

#: Never shown: it is plumbing the operator did not choose.
HIDDEN_FIELDS = ("portia_dir",)

#: Indexing, wherever it is offered — the add-data screen and one un-indexed file
#: in the workflow pane. A rack of drives, because what the action produces is a
#: *catalogued* source; the lightning bolt it replaced named the speed of an
#: operation that on real extracts takes a minute. `storage` is the other stock
#: database glyph and was rejected at button size: three plain bars read as a
#: hamburger menu, and `dns`'s indicator lights do not.
INDEX_ICON = "dns"
#: *Data behind a connection*, and a database in a tree: the cylinder, from
#: Material Symbols Rounded, drawn as an outline. It was ``dns`` on the card and
#: ``storage`` in the tree, two filled server racks, which read as rough beside
#: everything else and, for ``dns``, was already the glyph for Index (the
#: user's call, 2026-09-20: leaner, rounder).
DATABASE_GLYPH = "sym_r_database"

_NULL = "·"


# --- text -------------------------------------------------------------------


def text(
    value: str, *, style: str = "t-body", color: str = "c-body", wrap: bool = True
) -> ui.label:
    label = ui.label(value).classes(f"{style} {color}")
    return label.classes("pre-wrap") if wrap else label


def mono(value: str, *, color: str = "c-body", small: bool = False) -> ui.label:
    return text(value, style="t-mono-sm" if small else "t-mono", color=color)


def caption(value: str, *, color: str = "c-mute") -> ui.label:
    return text(value, style="t-caption", color=color)


def empty_note(value: str) -> ui.label:
    """What an empty section says. It states the fact rather than disappearing."""
    return ui.label(value).classes("empty-note pre-wrap")


def field(
    label: str,
    *,
    required: bool = False,
    hint: str = "",
    value: str = "",
    placeholder: str = "",
    mono: bool = False,
    secret: bool = False,
    on_change: Callable[..., Any] | None = None,
) -> ui.input:
    """One labelled input: the label, **whether it is required**, the box, a hint.

    A form field says up front what it needs (`DESIGN.md` → `field`): the word
    *required* or *optional* sits beside the label in caption size, so nobody
    finds out from a refusal. The box is `p-input` — 30px, hairline, the accent
    wash on focus — rather than Quasar's 56px default, which read as a form
    built from someone else's kit. ``secret`` draws a password box with the
    reveal toggle; what is typed there is never written anywhere by portia.
    """
    with ui.element("div").classes("field"):
        with ui.element("div").classes("field-label"):
            ui.label(label)
            ui.label("required" if required else "optional").classes(
                "field-required" if required else "field-optional"
            )
        box = ui.input(
            value=value, placeholder=placeholder, password=secret, password_toggle_button=secret
        )
        box.classes("p-field p-input w-full" + (" p-field-mono" if mono else ""))
        box.props("dense borderless hide-bottom-space")
        if on_change is not None:
            box.on_value_change(on_change)
        if hint:
            ui.label(hint).classes("field-hint")
    return box


#: The glyph each alert kind carries. Kind, never rank: an error is not louder
#: than a note, it is a different colour.
_ALERT_ICONS = {"error": "error_outline", "info": "info", "warning": "warning_amber"}


def alert(text: str, kind: str = "error") -> ui.element:
    """A sentence the reader must not miss: an error, a warning, a note.

    A bordered block on the kind's soft tint, with the kind's glyph, in body
    size — where a refusal used to be a mute caption under a button, which is
    how a real connection failure went unread (`DESIGN.md` → `alert`).
    """
    with ui.element("div").classes(f"p-alert p-alert--{kind}") as box:
        ui.icon(_ALERT_ICONS.get(kind, "info")).classes("p-alert-icon")
        ui.label(text).classes("p-alert-text pre-wrap")
    return box


def failures(failed: Mapping[str, str]) -> ui.element | None:
    """What indexing could not do, by name, with what each error said. Nothing when all went.

    One block for every surface indexing starts from (`engine._hops`). Names in
    the order they failed and every one of them listed: which failure matters
    is not this block's call.
    """
    if not failed:
        return None
    lines = [FAILED_HEAD.format(n=count(len(failed), "source"))]
    lines += [f"{name}: {said}" for name, said in failed.items()]
    return alert("\n".join(lines), kind="error")


#: The first line of `failures`.
FAILED_HEAD = "Could not index {n}. The rest went ahead."


def choice_card(
    title: str,
    *,
    icon: str,
    marks: Iterable[str] = (),
    on_click: Callable[..., Any] | None = None,
) -> ui.element:
    """One of a few large options, for a screen that asks one question.

    A glyph and a title, and no sentence under them (the user's call,
    2026-09-20): ``marks`` is a row of `connector_glyph`s where the title alone
    would leave *which ones* unsaid.
    """
    with ui.element("div").classes("choice-card") as card:
        ui.icon(icon).classes("choice-card-icon")
        ui.label(title).classes("choice-card-title")
        kinds = list(marks)
        if kinds:
            with ui.element("div").classes("choice-card-marks"):
                for kind in kinds:
                    connector_glyph(kind)
    if on_click is not None:
        card.on("click", on_click)
    return card


def connector_glyph(kind: str, *, tip: bool = True) -> ui.element:
    """One database's mark, in the ink of wherever it sits. Kind, never rank.

    `provider_glyph`'s mechanism over `assets/connectors/<kind>.svg`. The
    tooltip is the provider's name, which is the whole of what a mark owes.
    """
    from portia.connectors import registry

    glyph = ui.element("span").classes(f"connector-glyph connector-glyph-{kind}")
    provider = registry.PROVIDERS.get(kind)
    if tip and provider is not None:
        glyph.tooltip(provider.label)
    return glyph


def section_header(value: str) -> ui.label:
    return ui.label(value).classes("p-section-header")


def path_row(path: Path, *, icon: str = "description") -> ui.element:
    """A file this surface writes, as a path you can read at a glance.

    The file name is the part that identifies it and the folders are where it
    sits, so they are set differently — name in `{colors.ink}`, folders quiet,
    and the folders are what gets truncated when there is no room. A path dumped
    as one 12px caption is a line nobody's eye finds the end of.

    ``$HOME`` becomes ``~``: it is a third of the string on every machine and
    tells the reader nothing they don't know.
    """
    home = str(Path.home())
    shown = str(path)
    shown = f"~{shown[len(home) :]}" if shown.startswith(home) else shown
    parent, _, name = shown.rpartition("/")
    row = ui.element("div").classes("path-row")
    with row:
        ui.icon(icon).classes("path-row-icon")
        if parent:
            ui.label(f"{parent}/").classes("path-row-dir")
        ui.label(name).classes("path-row-name")
    hint(row, str(path))
    return row


def pane_title(value: str) -> ui.label:
    return ui.label(value).classes("t-heading-lg")


def rule(strong: bool = False) -> ui.element:
    return ui.element("div").classes("p-rule-strong" if strong else "p-rule")


#: One beat of `p-pulse`, in seconds. **It has to match `assets/portia.css`**,
#: because the animation runs there and its phase is computed here.
PULSE_PERIOD = 1.4


def pulse() -> ui.element:
    """The 6px dot that says *this is happening now*, resuming mid-beat.

    **NiceGUI replaces a refreshable's elements rather than patching them**, so
    every event in a live exchange destroys this dot and builds another one —
    and a CSS animation on a brand new element starts at 0%. The one piece of
    motion in the app, whose own CSS says it *fades rather than travels*, was
    therefore snapping back to full opacity a few times a second at whatever
    rate the stream happened to arrive. That is not a slow animation; it is no
    animation, restarted. It is the same class of bug as the canvas glide, which
    `assets/canvas.js` fixed by tweening in JS.

    The fix here is cheaper because the beat is periodic: state where in the beat
    the replacement should *start*, as a negative `animation-delay`. A dot built
    0.9 s into the cycle is handed `-0.9s` and picks up where its predecessor
    was, so a run of rebuilds is one continuous pulse.

    **Declarative, in the DOM, and never a `run_javascript` during a render** —
    that races the DOM patch, which is the rule `scroll.js` and `canvas.js` are
    both built around.

    The round trip does not spoil it. The browser starts the animation when the
    element lands rather than when this ran, so every phase is late by the
    network latency — but by the *same* latency each time, so the offset is
    constant and the continuity between one dot and the next survives it. What
    would break it is a varying delay, and a varying delay is indistinguishable
    from the jitter this replaces.
    """
    return ui.element("div").classes("tool-pulse").style(pulse_phase())


#: One beat of `p-live`, the status light's fade. Matches `assets/portia.css`,
#: for `PULSE_PERIOD`'s reason.
LIVE_PERIOD = 3.2

#: What a status light can say. A closed list, and each entry is a kind: the
#: light never grows, never counts and never sorts a row.
LIVE = "live"
WAITING = "waiting"
ON = "on"
OFF = "off"
LIGHTS = (LIVE, WAITING, ON, OFF)


def status_light(kind: str, tip: str = "") -> ui.element:
    """A small round light beside the word for a state (`DESIGN.md` → `status-light`).

    ``live`` is work going on right now: blue, fading slowly between two
    shades of it, and the one light that moves. ``waiting`` is the accent and
    still, a chat stopped on you. ``on`` is green and ``off`` is grey, for a
    session that is open or is not. The word beside it carries the state and
    the light repeats it, so nothing is said in colour alone.

    The live light resumes mid-fade, for `pulse`'s reason: a rebuilt row
    builds a new element, and a CSS animation on a new element starts at 0%.
    """
    if kind not in LIGHTS:
        raise ValueError(f"unknown status light {kind!r}; one of {', '.join(LIGHTS)}")
    light = ui.element("span").classes(f"status-light status-light--{kind}")
    if kind == LIVE:
        light.style(f"--live-phase: {-(time.monotonic() % LIVE_PERIOD):.2f}s")
    if tip:
        light.tooltip(tip)
    return light


def pulse_phase() -> str:
    """``--pulse-phase``: where in the beat an element built *now* should start.

    Always negative, so the animation begins already underway rather than
    waiting. Set on the element or on any ancestor, since custom properties
    inherit — the thinking row states it once on the container its three dots
    live in, and each dot subtracts its own stagger from it.
    """
    return f"--pulse-phase: {-(time.monotonic() % PULSE_PERIOD):.2f}s"


def in_flight(word: str, detail: str = "", *, ident: str = "") -> ui.element:
    """The one mark that says *this is happening now*: a pulsing dot and a word.

    The same 6px `tool-pulse` the running tool card and the tool queue use, so
    the app has one way of saying it rather than a spinner here and a dot there.
    It is a **kind, not a rank** — nothing about it grows, sorts or scores, and
    the word is the whole of what it claims.

    ``detail`` is where the run says *where* it is — `step 4 of 10`, and the
    outer count when a build is on more than one model. ``ident`` is the name of
    the thing currently executing and is drawn in **mono**, because a step id is
    an identifier and the split between Inter and mono in this app is semantic
    (`DESIGN.md`). The word stays the same size and the same colour whatever
    those say; a mark that grew as it learned more would be the ranking the
    engine refuses to do, performed by the progress indicator.
    """
    with ui.element("div").classes("in-flight") as row:
        pulse()
        caption(word)
        # Name first, then where it is: "building the project · bookings_monthly
        # · step 4 of 10 · 12s" reads as one sentence, and the flex gap is the
        # separator, so nothing here has to invent punctuation between elements.
        if ident:
            mono(ident, color="c-mute", small=True)
        if detail:
            caption(detail)
    return row


def scroll_area(key: str, *, classes: str = "", stick: bool = False) -> ui.element:
    """A scrolling region whose position survives the pane being rebuilt.

    NiceGUI replaces a refreshable's elements rather than patching them, and a
    replaced element starts at the top — so every click threw the file list and
    the run report back to row one. The offset is **client state**, exactly like
    the canvas's pan and zoom: the server states a key here and
    `assets/scroll.js` puts the position back on whatever element now carries it.
    Nothing measures it, nothing persists it, and no render asks for it.

    ``key`` names *what is being scrolled*, not which pane it is in — two saved
    runs shown in the same pane are two things to keep a place in, and sharing a
    key would drop you into the second one at the first one's offset.

    ``stick`` marks a region that grows at the bottom — the transcript. It
    follows the newest row **only while you are already at the bottom**: scroll
    up to read the evidence a question is about and the region keeps your place
    like any other, which is the difference between a live log and one that
    fights you. It is a flag in the DOM for the same reason the offset is a key:
    a `run_javascript` fired from a render races the DOM patch and lands on the
    element that is about to be discarded.
    """
    element = ui.element("div").classes(f"p-scroll {classes}".strip())
    element.props(f'data-scroll-key="{key}"')
    if stick:
        element.props('data-scroll-stick="bottom"')
    return element


def enters(element: ui.element, key: str) -> ui.element:
    """Mark an element as something that *arrives* — `assets/motion.js` animates
    it in the first time its key is seen, and never again.

    **New versus rebuilt is the client's question**, which is why the server only
    states a key. NiceGUI replaces a refreshable's elements rather than patching
    them, so from here every render looks like everything arriving at once — the
    mechanical reason `DESIGN.md` used to forbid entry animations outright. The
    client is the one party that can remember what was already on screen, and it
    remembers by key, not by element, because the element is exactly the thing
    that does not survive (`pick.js`'s rule, `pick.js`'s reason).

    ``key`` names the *thing*, not its position on screen: a tool call's SDK id,
    a figure's path, a table's name. A thing redrawn keeps its key and stays
    still; a thing that was never on screen animates in once. Keys are cosmetic
    — a collision costs one missed animation, never a wrong number — so they are
    sanitised rather than escaped.
    """
    element.props(f"data-enter={prop_value(_enter_key(key))}")
    return element


def enter_slot(key: str) -> ui.element:
    """A keyed wrapper for content that has no single element to key.

    ``display: contents``, so it takes no part in layout — the transcript's
    stack gap runs between the rows inside it exactly as if the wrapper were not
    there — and `assets/motion.js` animates its *children* when the key is new.
    For the transcript's rows, which are drawn by a dozen renderers that would
    otherwise each need the key threaded through them.
    """
    slot = ui.element("div").classes("p-enter")
    slot.props(f"data-enter={prop_value(_enter_key(key))}")
    return slot


def _enter_key(key: str) -> str:
    """A key fit for a prop however it was named.

    A chart is named by a sentence the agent wrote and a figure by a path through
    a folder somebody named, so a raw key can hold both quote kinds at once —
    the one case `prop_value` refuses to serve. An entrance key only has to be
    *stable and distinct*, never read back, so the odd characters are folded to
    underscores instead of quoted.
    """
    return "".join(ch if ch.isalnum() or ch in "._:/-" else "_" for ch in key)


# --- controls ---------------------------------------------------------------


def button(
    label: str,
    on_click: Callable[..., Any] | None = None,
    *,
    kind: str = "tertiary",
    icon: str | None = None,
    micro: bool = False,
    split: bool = False,
    enabled: bool = True,
    busy: bool = False,
) -> ui.button:
    """One button. ``kind`` is primary | secondary | tertiary.

    The teal fill is scarce on purpose — at most one per view. It marks the
    action the loop is waiting on, which on a stopped turn is Answer or Allow
    (DESIGN.md → `write-confirm`).

    **An icon with no label is an icon button**, and gets square padding rather
    than a text button's asymmetric one. That is a statement of fact about the
    arguments rather than a flag to remember: a caller that passes both gets a
    labelled button, and one that passes only an icon owes it a tooltip.

    ``split`` rules a hairline between the icon and the word, which reads as one
    control doing one thing rather than a glyph that happens to sit beside some
    text. It is opt-in rather than automatic for every icon-plus-label button:
    most of those are ordinary buttons that merely have an icon, and a rule
    through all of them would be decoration.

    ``busy`` is a press being acted on: the same button in the same place with
    the same words, washed out, and no click gets through. A press that leaves
    the button exactly as it was reads as a press that missed, and it invites
    a second one. **Not Quasar's ``loading``**, which the first build used: it
    hides the label, and its overlay over the accent came out a murky dark
    green with a spinner too small to read (the user, 2026-09-18). Whatever is
    running says so in its own status line; the button only has to say it has
    been pressed.
    """
    classes = f"btn btn-{kind}" + (" btn-micro" if micro else "")
    if icon and not label:
        classes += " btn-icon"
    elif split:
        classes += " btn-split"
    # color=None so Quasar doesn't add `bg-primary`: which fill a button gets is
    # a portia decision (there is at most one accent fill per view), not a
    # framework default.
    b = ui.button(label, on_click=on_click, icon=icon, color=None).props("unelevated no-caps dense")
    b.classes(classes)
    b.set_enabled(enabled)
    if busy:
        # Not `set_enabled(False)`: Quasar's disabled state repaints the fill,
        # and the point is that the colour stays.
        b.classes("btn-busy")
        b.props("aria-disabled=true tabindex=-1")
    return b


def segmented(options, current, on_pick: Callable[[str], Any]) -> None:
    """A `segmented-control`, built from our own buttons.

    Quasar's toggle paints its active segment with a solid brand fill, which
    would be a second accent fill in a view that is allowed exactly one. The
    selected segment here is the soft accent wash DESIGN.md specifies instead.
    """
    with ui.element("div").classes("row-gap-xs segmented-control"):
        for option in options:
            picked = option == current
            b = button(str(option), lambda o=option: on_pick(o), micro=True)
            if picked:
                b.classes("seg-active")


def provider_glyph(kind: str, *, tip: bool = True) -> ui.element:
    """One provider's mark, in the ink of wherever it sits. Kind, never rank.

    A CSS mask over `assets/providers/<kind>.svg`, so the mark takes
    ``currentColor`` like every other icon and follows the theme; an ``<img>``
    would be the one glyph on screen that did not (`DESIGN.md` → `provider-glyph`).
    """
    from portia.agent import providers

    glyph = ui.element("span").classes(f"provider-glyph provider-glyph-{kind}")
    if tip:
        glyph.tooltip(providers.get(kind).label)
    return glyph


def model_effort(
    app,
    on_effort: Callable[[str], Any],
    *,
    effort_disabled: bool = False,
    on_provider: Callable[[str], Any] | None = None,
    on_refresh: Callable[[str], Any] | None = None,
    on_start: Callable[[str], Any] | None = None,
    provider_fixed: bool = False,
) -> None:
    """What an exchange will spend: where the model comes from, the model, the effort.

    Picked in three places — the composer, the add-data screen, and Settings —
    and it is **one setting in all three**, bound to the same fields. Three
    hand-rolled copies is how they stop agreeing: an option added to one list
    and not the others, or a select that writes a field nothing reads. ``app``
    is the thing whose fields are bound (the app, or one chat), passed in rather
    than imported so this stays a control rather than a thing that knows about
    the open project.

    **The provider comes first, as a glyph per kind** (`docs/PROVIDERS.md` §5),
    and the model list is that provider's: three Claude names, or whatever the
    Ollama server says it serves, read off the loop by `engine.list_models` and
    drawn from `App.provider_models`. Switching provider moves the model to
    the new provider's default, because a Claude name sent to Ollama is a
    guaranteed refusal.

    ``effort_disabled`` and ``provider_fixed`` are for a chat already under way.
    The model can change between exchanges (`set_model`); effort is a
    `ClaudeAgentOptions` field and the provider is the binary's environment,
    so both stop being offered rather than being offered and quietly ignored
    (`docs/CONVERSATION.md` §7). Effort is not drawn at all on a provider that
    ignores it (`Provider.honours_effort`), for the same reason.

    **The control keeps one shape whichever provider is picked** (`DESIGN.md` →
    Layout stability). The spinner and the refresh button share one fixed
    `spend-slot` that exists whether or not either is drawn, and the effort
    segments sit in a `spend-detail` row that keeps its height, empty, when a
    provider ignores effort — measured before this, switching Anthropic → Ollama in the
    composer moved the textarea and every control above it, and the refresh
    button wrapped onto a line of its own because the select claimed the row.
    """
    from portia.agent import providers
    from portia.agent.session import EFFORTS
    from portia.ui.state import APP

    kind = app.provider or providers.DEFAULT_KIND
    provider = providers.get(kind)
    app.model = app.model or provider.default_model
    listed = APP.provider_models.get(kind)
    if listed is None and provider.static_models:
        # Nothing lists a provider with no server when the page opens, so until
        # 2026-09-18 Anthropic's select held the current model alone, and the
        # other two appeared only after switching provider and back, because a
        # switch is what asked for the list. A list written in the provider's
        # own module costs no call, so it is drawn from the first render.
        listed = list(provider.static_models)
    # Two groups on one row that never wraps: the provider, then the model with
    # its refresh beside it. Each group is one unit sharing one centre line, and
    # squeezed, the model group gives way first (2026-09-14, measured: as a flat
    # wrapping row the model select wrapped alone and the button sat 12px below
    # its centre).
    with ui.element("div").classes("row-gap-sm spend-row"):
        with ui.element("div").classes("spend-group spend-provider"):
            _provider_pick(kind, on_provider, fixed=provider_fixed or on_provider is None)
        with ui.element("div").classes("spend-group spend-model"):
            # One-way on purpose. A two-way binding pushes every assignment to
            # ``app.model`` into this select, and a provider switch assigns a
            # name the old options do not hold, which NiceGUI answers by
            # recursing until Python gives up (2026-09-08, in the browser). The
            # switch redraws the pane, so the select is rebuilt with the new
            # list anyway.
            # `fill-input hide-selected`: with `use-input` alone the selected
            # label and the (empty) filter input share the field's width, and
            # the 50px the input reserves ellipsized every model name beside
            # free space. Filled, the input *is* the display — one element,
            # the full width.
            # **A server with no models offers none** *(2026-09-18, the user:
            # "the UI says no model but there is one in the picker?")*. The
            # select showed the provider's default name, `qwen3:8b`, over a
            # line saying nothing is installed. The name was a placeholder
            # for a model that was never pulled, and a select is read as a
            # list of what exists. A parked chat keeps its name: it ran on it.
            nothing = listed == [] and not provider_fixed
            select = ui.select(
                {"": _NO_MODELS_SHORT} if nothing else _model_options(app.model, listed),
                value="" if nothing else app.model,
                on_change=lambda e: setattr(app, "model", e.value),
            )
            if nothing:
                select.props("borderless dense options-dense")
                select.set_enabled(False)
            else:
                select.props(
                    "borderless dense options-dense new-value-mode=add-unique use-input"
                    " fill-input hide-selected"
                )
            select.classes("p-field p-field-mono model-select")
            with ui.element("span").classes("spend-slot"):
                if kind in APP.models_listing:
                    ui.spinner(size="xs")
                elif on_refresh is not None and provider.warms and not provider_fixed:
                    button("", lambda: on_refresh(kind), icon="refresh", micro=True).tooltip(
                        _LIST_AGAIN
                    )
    status = APP.provider_status.get(kind)
    note = _provider_note(provider, listed, status)
    if note:
        caption(note)
    # A provider portia can start gets the panel where the remedy would be a
    # command to type (`PROVIDERS.md` §4.9), and never on a parked chat, whose
    # provider is stated rather than offered. One control, always opening the
    # panel: *Start the server…* while nothing answers, *Stop the server…*
    # while the one this window started is up, and the sentence with a
    # spinner in between, so a press is never followed by silence (the user,
    # 2026-09-14). Starting and stopping happen in the panel and only there.
    if provider.starts and on_start is not None and not provider_fixed:
        from portia.ui.state import STARTING

        if APP.server_status == STARTING:
            with ui.element("div").classes("row-gap-xs"):
                ui.spinner(size="xs")
                caption(STARTING_SERVER)
        elif provider.started():
            button(STOP_SERVER, lambda: on_start(kind), icon="stop", micro=True)
        elif status is not None and status.reachable is False:
            button(START_SERVER, lambda: on_start(kind), icon="play_arrow", micro=True)
    with ui.element("div").classes("spend-detail"):
        if not provider.honours_effort:
            # Nothing, at the row's height. It said *Effort is a Claude
            # setting. Ollama ignores it.* until 2026-09-18, on the argument
            # that saying why beats a blank. The user read it as noise about a
            # control that is not there, and the row is what layout stability
            # needs, not the sentence.
            pass
        elif effort_disabled:
            caption(f"effort {app.effort}" if app.effort else "default effort")
        else:
            segmented(EFFORTS, app.effort, on_effort)


def _provider_pick(kind: str, on_provider, *, fixed: bool) -> None:
    """The picked provider's glyph, then a select over every kind (the user's call, 2026-09-14).

    A select rather than one segment per provider: three marks in a row were
    three things to read before the model, and a fourth provider would have
    made it four. The glyph stays, so what a chat runs on is still one glance.
    Fixed, it is the glyph alone, stated rather than offered, which is
    `segmented`'s rule for a parked chat applied to a mark. One-way on
    purpose, for the reason the model select gives: the switch redraws the pane.
    """
    from portia.agent import providers

    if fixed:
        provider_glyph(kind)
        return
    # Closed, the control is the glyph and the arrow: the display value is
    # blank and the mark sits in the field's prepend slot. Open, the options
    # are the names (the user's call, 2026-09-14).
    # The kinds the machine's settings enable (`providers.offered_kinds`, the
    # dashboard in Settings), plus the one picked, so a chat on a kind since
    # switched off still says what it runs on.
    kinds = list(providers.offered_kinds())
    if kind not in kinds:
        kinds.append(kind)
    select = ui.select(
        {each: providers.get(each).label for each in kinds},
        value=kind,
        on_change=lambda e: on_provider(e.value),
    ).props('borderless dense options-dense display-value=""')
    select.classes("p-field provider-select")
    with select.add_slot("prepend"):
        provider_glyph(kind)


def _model_options(current: str, listed: list | None) -> list[str] | dict[str, str]:
    """The select's options: the provider's list with the vendor's size beside
    each local model, or the current name alone while nothing has been listed."""
    from portia.core.present import size

    if not listed:
        return [current]
    options = {m.name: f"{m.name} · {size(m.size)}" if m.size else m.name for m in listed}
    if current and current not in options:
        options[current] = current
    return options


def _provider_note(provider, listed: list | None, status) -> str:
    """The one line under the picker when the list could not be read, or is empty."""
    if status is not None and status.reachable is False:
        return provider.start_remedy or status.detail
    if listed == []:
        command = provider.add_command("<name>")
        return _NO_MODELS.format(command=command) if command else _NO_MODELS_PLAIN
    return ""


def add_model_line(kind: str) -> None:
    """How a model is added: the command, shown and never run (`PROVIDERS.md` §4.4)."""
    from portia.agent import providers

    command = providers.get(kind).add_command("<name>")
    if not command:
        return
    with ui.element("div").classes("row-gap-xs add-model-line"):
        caption(_ADD_MODEL)
        mono(command, color="c-body", small=True)
        button("", lambda: ui.clipboard.write(command), icon="content_copy", micro=True).tooltip(
            _COPY
        )


_LIST_AGAIN = "List the server's models again"
START_SERVER = "Start the server…"
STOP_SERVER = "Stop the server…"
STARTING_SERVER = "starting llama-server…"
_NO_MODELS = "No models installed. Run {command} in a terminal."
_NO_MODELS_PLAIN = "No models."
_NO_MODELS_SHORT = "no models"
_ADD_MODEL = "Add a model in a terminal:"
_COPY = "Copy"


#: What each approval mode is called on the picker, and what it does in one line.
#: Two values, both always offered: the control states the mode whichever one is
#: on, which is what a chip drawn only for *autopilot* could not do.
MODE_LABELS = {"ask": "Ask before writes", "autopilot": "Autopilot"}
MODE_TIPS = {
    "ask": "Every write to a spec or the catalog stops for you first.",
    "autopilot": "Every write goes through, recording a step included. Questions still stop.",
}


def approval_mode(app, on_change: Callable[[], Any] | None = None) -> ui.select:
    """Which writes stop for you: ask first, or autopilot.

    One control in two places, the composer and Settings, bound to the same
    `App.autopilot` field either way. **The picker replaced a chip** *(2026-09-04)*:
    the chip was drawn only while autopilot was on, so the normal case had no
    control at all and the mode could only be changed from a dialog. A picker
    beside the model and the effort is the third fact about *how this message
    will run*, and it is VS Code's placement for the same control.

    The value is a word rather than the bool so the select has two named
    options; `App.set_mode` translates.
    """
    from portia.ui import state

    select = ui.select(
        {mode: MODE_LABELS[mode] for mode in state.MODES},
        value=app.mode,
    ).props("borderless dense options-dense")
    select.classes("p-field approval-mode")

    def picked(e) -> None:
        app.set_mode(str(e.value))
        if on_change is not None:
            on_change()

    select.on_value_change(picked)
    hint(select, MODE_TIPS[app.mode])
    return select


def setting(title: str, description: str = "") -> ui.element:
    """One setting: what it is, what it does, and the control that changes it.

    **One row shape for every preference** *(2026-09-04)*. The settings panel
    was captions and controls in whatever order each tab happened to stack them,
    so the same kind of thing read three ways. A setting is a title, a line
    saying what it does, and the control under them — the shape every editor's
    settings page uses — and the caller opens this and puts the control inside.
    The description is a fact about the setting, never advice about its value.
    """
    row = ui.element("div").classes("setting")
    with row:
        ui.label(title).classes("setting-title")
        if description:
            ui.label(description).classes("setting-why pre-wrap")
    return row


def menu_row(icon: str, label: str, on_click: Callable[..., Any]) -> ui.element:
    """One line in a `p-menu`: a glyph and a sentence, in Inter rather than mono.

    `menu-row-name` is mono because the view menu lists table names. A menu of
    *actions* is prose, so it gets its own text class — one row shape, two kinds
    of content, and the font says which (`DESIGN.md` → typography is semantic).
    """
    with ui.element("div").classes("menu-row") as row:
        ui.icon(icon).classes("menu-row-mark")
        ui.label(label).classes("menu-row-text")
    row.on("click", on_click)
    return row


#: How long a pointer has to rest before a tooltip appears, in ms. Quasar's
#: default is instant, which is right for a button you aimed at and wrong for
#: anything you cross on the way somewhere else — a list of rows especially,
#: where instant tooltips fire all the way down the pane as you scan it.
TOOLTIP_DELAY = 600


def hint(element, text: str) -> None:
    """A tooltip that waits to be asked for.

    Use this instead of ``element.tooltip(...)`` on anything in a list. Only for
    text that is *not already on screen*: a tooltip repeating the row you are
    pointing at is a thing that appears, is read, and says nothing.
    """
    if not text:
        return
    with element:
        ui.tooltip(text).props(f"delay={TOOLTIP_DELAY}")


def chip(value: str) -> ui.label:
    """`type-chip` — a source's or step's kind (`csv`, `join`, `normalize`, `sql`)."""
    return ui.label(value).classes("type-chip")


def fact(icon: str, value: Any, label: str) -> ui.element:
    """One measured value, as a small icon and the number itself.

    For places where the same handful of facts repeats down a long list and a
    labelled line each would bury the values in their own labels. The icon is
    shorthand, never the whole story — ``label`` names the fact in a tooltip, so
    nothing on screen is a number whose meaning you have to guess.
    """
    with ui.element("div").classes("fact") as row:
        ui.icon(icon).classes("fact-icon")
        ui.label("—" if value is None else str(value)).classes("fact-value")
    row.tooltip(label)
    return row


def flag_badge(name: str, variant: str = "") -> ui.label:
    """One flag, named exactly as the engine names it.

    Uniform size regardless of the number behind it. A non-blocking flag is
    uncoloured — visible, not ranked.
    """
    suffix = f" flag-badge--{variant}" if variant else ""
    return ui.label(name).classes(f"flag-badge{suffix}")


#: How prose is rendered wherever it appears. ``code-friendly`` is not cosmetic:
#: it stops `_` from starting emphasis, and without it `customer_id and name`
#: came out as "customer" followed by italics with the underscores eaten. Column
#: names are the identifiers this whole product is about; a renderer that
#: silently rewrites them is worse than one that shows raw asterisks.
MARKDOWN_EXTRAS = ["fenced-code-blocks", "tables", "code-friendly"]


def markdown(value: str) -> ui.markdown:
    """Prose as its author wrote it — the copilot's, or a saved report's."""
    return ui.markdown(value, extras=MARKDOWN_EXTRAS).classes("p-markdown t-body c-body")


def code_block(value: str) -> ui.element:
    with ui.element("div").classes("code-block") as block:
        ui.html(_escape(value))
    return block


def collapsed(summary: str, body: Callable[[], Any]) -> ui.expansion:
    """A row that opens to show its evidence. Collapsed by default.

    The body is drawn **when the row is first opened**, not when the row is
    (`_lazy_body`). A shut row used to carry its whole body to the browser on
    every rebuild of the pane around it.
    """
    exp = ui.expansion(summary).classes("p-expansion w-full").props("dense dense-toggle")
    _lazy_body(exp, body, open=False)
    return exp


def _lazy_body(exp: ui.expansion, body: Callable[[], Any], *, open: bool, on_toggle=None) -> None:
    """Draw ``body`` inside ``exp`` the first time it opens, and only then.

    **A shut disclosure shipped its body anyway** *(2026-09-07)*. A tool card
    holds a page of evidence and a thinking row a page of reasoning; drawn shut,
    both were still built server-side and sent — and the transcript rebuilds on
    every streamed event, so a 239-event chat sent 567 KB to the browser per
    event, most of it the insides of rows nobody had opened. Now a shut row is
    its header. Opening it asks the server for the body, which lands a round
    trip later inside the row that asked; the expansion is already open by
    then, and the rows around it were not touched.

    ``on_toggle`` is the caller's own bookkeeping (which rows are open) and runs
    on every change, as before; the body is drawn once and never redrawn.
    """
    drawn = open

    def changed(event) -> None:
        nonlocal drawn
        on = bool(event.value)
        if on and not drawn:
            drawn = True
            with exp:
                body()
        if on_toggle is not None:
            on_toggle(on)

    exp.on_value_change(changed)
    if open:
        with exp:
            body()


def disclosure(
    header: Callable[[], Any],
    body: Callable[[], Any],
    *,
    open: bool = False,
    on_toggle: Callable[[bool], Any] | None = None,
) -> ui.expansion:
    """`collapsed`, but the summary is **drawn** rather than labelled.

    For a row that has to say more than one thing before it is opened — a tool
    call is a name, its subject, its arguments and whether the evidence has come
    back, and a plain string summary can only set all of that in one colour.
    Quasar's `header` slot replaces the whole header including its caret, so this
    draws its own; `assets/portia.css` rotates it on `.q-expansion-item--expanded`.

    ``open`` and ``on_toggle`` are how a row **stays** open. The transcript is a
    refreshable and every streamed event rebuilds it, so an expansion left
    holding its own value shuts itself the moment the next tool call arrives —
    which is precisely when someone is reading the result they just opened. The
    caller keeps the answer (`state.App.open_rows`) and hands it back here.
    """
    exp = ui.expansion(value=open).classes("p-expansion p-disclosure w-full").props("dense")
    with exp.add_slot("header"):
        ui.icon("chevron_right").classes("p-disclosure-caret")
        header()
    # The body waits for the first open (`_lazy_body`): a shut card is its head.
    _lazy_body(exp, body, open=open, on_toggle=on_toggle)
    return exp


def kv_list() -> ui.element:
    """A container whose `kv` rows share one label column, so values line up.

    Ragged labels put every number at a different indent, which is most of what
    made the first run report unreadable.
    """
    return ui.element("div").classes("kv-list")


def kv(key: str, value: Any = None, *, body: Callable[[], Any] | None = None) -> None:
    """One row of a `kv_list`: the engine's field name, then what it measured.

    The key is the engine's own spelling (`left_dropped`, not "rows dropped from
    the left"), because those are the names an `expect` block has to use — the
    report is where you learn the vocabulary.
    """
    ui.label(key).classes("kv-key")
    if body is not None:
        with ui.element("div").classes("kv-value-slot"):
            body()
    else:
        ui.label(inline(value)).classes("kv-value")


# --- artifact rows ----------------------------------------------------------


def prop_value(value: str) -> str:
    """A value fit for `element.props("key=<this>")` whatever characters it holds.

    NiceGUI's prop parser splits on whitespace, so ``data-folder=palette test``
    arrives as ``data-folder="palette"`` plus a boolean prop called ``test``.
    Three comments in `ui/` used to say a quoted value did not survive the
    parser either; **measured otherwise** *(2026-09-04)*: `Props.parse` returns
    a double- or single-quoted value whole, and the test beside this pins it.
    What it cost before anyone measured: a gallery folder named with a space
    took drops for a folder of its first word, and a figure inside it could not
    be opened from its row.

    Double quotes unless the value has one, then single. A name holding both is
    not a case worth a parser; it is refused upstream (`figures.make_folder`).
    """
    quote = "'" if '"' in value else '"'
    return f"{quote}{value}{quote}"


def artifact_row(
    *,
    name: str,
    icon: str,
    meta: str = "",
    note: str = "",
    selected: bool = False,
    depth: int = 0,
    caret: str = "",
    on_click: Callable[..., Any] | None = None,
    pick: str | None = None,
    opens: str | None = None,
    light: str = "",
) -> ui.element:
    """One file portia knows about. Selected is one of the accent's three jobs.

    ``light`` draws a `status_light` of that kind before the meta word, for the
    one row whose meta is a state that changes while you watch: the warehouse
    connection.

    ``depth`` indents it inside the left tree and ``caret`` gives it a disclosure
    triangle, so a folder and a file are **one row type at two settings** rather
    than two components that have to be kept looking alike. The indent is handed
    to CSS as a custom property rather than computed into a padding here: how far
    a level steps in is a look, and looks live in ``assets/portia.css``.
    """
    classes = "artifact-row" + (" artifact-row--selected" if selected else "")
    row = ui.element("div").classes(classes).style(f"--depth:{depth}")
    if pick is not None:
        # **The row's identity, in the DOM.** `assets/pick.js` reads it to tell a
        # click from a double click before either reaches the server, and it has
        # to be the spec's *path* rather than the element: the element is exactly
        # the thing that does not survive the refresh between the two presses.
        # Quoted through `prop_value` — see it for the comment this replaces,
        # which said a quoted value did not survive the parser and was wrong.
        row.props(f"data-spec={prop_value(pick)}")
    if opens is not None:
        # The same mechanism for a row that opens a **tab** rather than selecting
        # a spec: `<kind>:<id>`, read by `assets/pick.js`, which tells one press
        # from two before either reaches the server. An id is a figure's path or
        # a position — never a name somebody wrote a sentence into — but a path
        # runs through a folder somebody named, and that can hold a space.
        row.props(f"data-opens={prop_value(opens)}")
    with row:
        if caret:
            ui.icon(caret).classes("artifact-caret")
        ui.icon(icon).classes("artifact-icon")
        # Own class rather than utility classes: this wrapper's job is to be the
        # thing that shrinks, and a long path is exactly what it holds.
        with ui.element("div").classes("artifact-body"):
            ui.label(name).classes("artifact-name")
            if note:
                # No tooltip. It repeated the line it was attached to, which
                # meant every row in the tree popped a box saying what the row
                # already said, on the way past to somewhere else.
                ui.label(note).classes("artifact-note")
        if light:
            status_light(light)
        if meta:
            ui.label(meta).classes("artifact-meta")
    if on_click is not None:
        row.on("click", on_click)
    # **No `dblclick` handler, deliberately.** Clicking a row refreshes this pane,
    # which replaces the row — so the browser sees two clicks on two different
    # elements and never dispatches one. A row that needs the distinction passes
    # `pick` and is driven by `assets/pick.js` instead.
    return row


# --- the acknowledged banner ------------------------------------------------


def acknowledged_banner(
    flags: list[str],
    *,
    rationale: str | None = None,
    measured: dict | None = None,
) -> ui.element:
    """A blocking flag a human waved through. It never collapses.

    A spec once shipped a table with 3.85% too much revenue because an override
    was a fifteen-character fragment buried mid-dict in a terminal prompt
    (docs/EVALUATION.md, Run 5). On screen it is a banner, at the top of its
    step, and if a step is both acknowledged and clean-looking the banner wins.

    ``measured`` is the outcome report, when there is one. Before a write there
    isn't — the step has not run — so the banner names the flags and says what
    they mean without inventing a number the engine never produced.
    """
    with ui.element("div").classes("ack-banner") as banner:
        ui.label("acknowledged override").classes("t-caption c-accent uppercase")
        with ui.element("div").classes("row-gap-xs"):
            for flag in flags:
                flag_badge(flag, ACKNOWLEDGED)
        for flag in flags:
            caption(flag_meaning(flag), color="c-body")
        if measured is not None:
            _measured_facts(measured, flags)
        if rationale:
            ui.label(rationale).classes("t-body c-body pre-wrap")
    return banner


def _measured_facts(outcome: dict, flags: list[str]) -> None:
    """What the engine measured about the waived flags — its numbers, not ours."""
    grain = outcome.get("grain")
    with kv_list():
        if grain and grain.get("measurable") and not grain.get("unique"):
            kv("grain", grain["keys"])
            kv("duplicated keys", grain.get("n_duplicated_keys"))
            kv("max multiplicity", grain.get("max_multiplicity"))
            for example in grain.get("examples") or []:
                kv("example", example)
        if grain and not grain.get("measurable"):
            kv("missing columns", grain.get("missing_columns"))
        if outcome.get("newly_all_null_columns"):
            kv("became all-null", outcome["newly_all_null_columns"])
        for name, contribution in (outcome.get("contribution") or {}).items():
            if contribution.get("contributed") is False:
                kv("contributed nothing", name)
        if "empty_output" in flags:
            kv("rows", outcome.get("n_rows"))


#: What each blocking flag means, in one line. Every one is a zero-condition —
#: that is the whole reason it can block (`checks.outcome.BLOCKING_FLAGS`).
_FLAG_MEANING = {
    "empty_output": "The step produced no rows at all.",
    "all_null_column": "A column arrived with data and came out entirely null.",
    "source_did_not_contribute": "An input put no values into the result.",
    "grain_not_unique": "The declared grain is not one row per key.",
    "grain_columns_missing": "The declared grain names columns the result does not have.",
}
_UNKNOWN_FLAG = "A zero the engine will not write past without acknowledgement."


def flag_meaning(flag: str) -> str:
    """One plain line for a blocking flag. Covered for every flag, by test."""
    return _FLAG_MEANING.get(flag, _UNKNOWN_FLAG)


def flag_variant(flag: str, acknowledged: list[str]) -> str:
    """Which of the three badge treatments a flag gets. Kind, never rank."""
    if flag in acknowledged:
        return ACKNOWLEDGED
    return BLOCKING if flag in BLOCKING_FLAGS else ""


# --- payloads ---------------------------------------------------------------


def payload_view(tool_input: dict, *, skip: tuple[str, ...] = HIDDEN_FIELDS) -> None:
    """A write's payload laid out, not dumped.

    A step is a decision being made on the record, so it reads as a form: one
    labelled line per field, with `sql`, `expect`, `transforms` and `rationale`
    given their own blocks. Never a `dict` repr.
    """
    step = tool_input.get("step")
    for key, value in tool_input.items():
        if key not in skip and key != "step":
            _field(key, value)
    # A `record_step` payload nests the decision one level down. It is unwrapped
    # rather than shown as an object, because the step *is* what is being agreed
    # to — `skip` applies at both levels so nothing is stated twice.
    for key, value in (step or {}).items() if isinstance(step, dict) else ():
        if key not in skip:
            _field(key, value)


def _field(key: str, value: Any) -> None:
    if value is None or value == [] or value == {}:
        return
    if key in PROSE_FIELDS and isinstance(value, str):
        with ui.element("div").classes("report-group"):
            ui.label(key).classes("report-group-label")
            ui.label(value).classes("t-body c-body pre-wrap")
    elif key in BLOCK_FIELDS or _nested(value):
        with ui.element("div").classes("report-group"):
            ui.label(key).classes("report-group-label")
            code_block(value if isinstance(value, str) else as_yaml(value))
    else:
        # A flat list is one line — `present.inline` already renders it as one,
        # and `on: [customer_id]` set as a labelled YAML box was a bordered
        # block, a rule and an uppercase heading spent on eleven characters.
        # `BLOCK_FIELDS` still get theirs: those are decisions in their own
        # right, and their size is not what earns them the block.
        with kv_list():
            kv(key, value)


def _nested(value: Any) -> bool:
    """Whether a value has structure a single line would flatten away."""
    if isinstance(value, dict):
        return any(isinstance(v, (dict, list)) for v in value.values())
    if isinstance(value, list):
        return any(isinstance(v, (dict, list)) for v in value)
    return False


# --- table preview ----------------------------------------------------------


def table_preview(data, *, limit: int = PREVIEW_ROWS, shape: tuple | None = None) -> None:
    """The produced table. Nulls are visible, never blank and never zero.

    Takes a `core.table.Table` or a DataFrame. Given a Table it reads `limit`
    rows and one count — so previewing a step that produced 80 million rows costs
    what previewing ten costs, and `workflow._table` no longer has to load a
    whole output to put a shape in a label.

    ``shape`` is that count and those rows **already measured** — off the loop,
    by `engine.measure_shape` — and a pane that holds one passes it so the
    render pass runs no query at all. Without it the shape is measured here,
    which is the right thing for a DataFrame and a scan for a `Table`.
    """
    if data is None:
        empty_note("no table")
        return
    total, head = shape if shape is not None else table_shape(data, limit)
    if total is None:
        empty_note(UNCOUNTED)
        return
    if total == 0:
        empty_note(f"0 rows × {count(head.shape[1], 'column')}")
        return

    frame = head
    numeric = {c for c in head.columns if pd.api.types.is_numeric_dtype(head[c])}
    with ui.element("div").classes("table-preview"):
        ui.html(_table_html(head, numeric))
    caption(f"showing {len(head)} of {count(total, 'row')} · {count(frame.shape[1], 'column')}")


UNCOUNTED = "this table could not be read"


def table_shape(data, limit: int = PREVIEW_ROWS):
    """`engine.table_shape`, kept here so a component never has to import the engine."""
    from portia.ui import engine

    return engine.table_shape(data, limit)


def _table_html(head: pd.DataFrame, numeric: set) -> str:
    columns = "".join(
        f"<th><span class='col-name'>{_escape(str(c))}</span>"
        f"<span class='col-dtype'>{_escape(str(head[c].dtype))}</span></th>"
        for c in head.columns
    )
    rows = "".join(
        "<tr>"
        + "".join(
            f"<td class='{'numeric' if c in numeric else ''}'>{_cell(value)}</td>"
            for c, value in zip(head.columns, row, strict=True)
        )
        + "</tr>"
        for row in head.itertuples(index=False, name=None)
    )
    return f"<table><thead><tr>{columns}</tr></thead><tbody>{rows}</tbody></table>"


def _cell(value: Any) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)) or value is pd.NaT:
        return f"<span class='null'>{_NULL}</span>"
    return _escape(str(value))


# --- helpers ----------------------------------------------------------------


def _escape(value: str) -> str:
    return (
        value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )

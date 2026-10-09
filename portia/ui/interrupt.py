"""*Interrupt query*: the press on a data call's card, and the menu that asks why.

`docs/CONVERSATION.md` §16. Stop ends the exchange; this ends one call that
reads data and nothing else, and the copilot carries on with what it is told
instead of the result (`agent/tools._interrupted`). The rules the user set:

- **Ask first, then interrupt.** A press opens a small menu at the control:
  *Wrong query*, *Takes too long*, *Other* with an optional sentence, and a
  *Don't ask again* tick. **The query keeps running while it is open.** A reason
  picked interrupts with that reason; under *Other* the interrupt goes when the
  sentence is sent. **Closing the menu without picking cancels the press**, as
  if it had not been made, the tick with it.
- **Don't ask again** is kept with the pick that made it (`App.ask_before_interrupt`,
  remembered by `ui/prefs.py`, turned back on in Settings): every later press
  interrupts at once, with no menu and no reason.
- **A second press, once interrupted, skips the measuring** portia does before
  the copilot is told: no menu, ever.

**One menu per tab, built once at page level** (`screens.build_add_dialog`'s
reason): every streamed event redraws the running card, and a menu hanging from
the card's own control would shut with it. So the control is a plain element
naming its card (``data-interrupt``), the press is resolved on the client and
reaches one page-level `ui.on` (`assets/interrupt.js`), and the menu floats over
the transcript, placed by the script against whichever control names its card
right now (`CLAUDE.md` → Window: keyed by identity, never by element). The
server says which card it is about in the DOM (``data-anchor``) and nothing
else.

Nothing here computes or calls the engine: what a press does to the call is
`exchange.interrupt_call`'s.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from nicegui import context, ui

from portia.ui import components as c
from portia.ui import exchange
from portia.ui.state import APP

#: The menu, in the user's words. The reason picked is what the copilot reads,
#: verbatim (`prompts/errors/interrupted_reason.md`).
HEADING = "Why interrupt it?"
REASONS = ("Wrong query", "Takes too long")
OTHER = "Other"
OTHER_PLACEHOLDER = "Optional: say what is wrong"
SEND = "Interrupt"
SEND_TIP = "Interrupt · ⌘↩ or Ctrl↩"
DONT_ASK = "Don't ask again"
#: A press that came too late: the step had started writing itself.
TOO_LATE = "Too late to interrupt: the step is being written."

_REASON_ICONS = {"Wrong query": "block", "Takes too long": "hourglass_bottom", OTHER: "edit_note"}
_SEND_KEYS = ("keydown.meta.enter.prevent", "keydown.ctrl.enter.prevent")


@dataclass
class _Menu:
    """One tab's menu, as built, and the card it is open for."""

    card: ui.element
    other: ui.element
    field: ui.textarea
    tick: ui.checkbox
    open_for: str | None = None


#: Each tab's menu, by NiceGUI client id, dropped when its elements are.
_MENUS: dict[str, _Menu] = {}


def build_menu() -> None:
    """The menu, once per page, hidden until a press opens it at a card."""
    client_id = context.client.id
    with ui.element("div").classes("interrupt-menu choice-menu") as card:
        ui.label(HEADING).classes("choice-menu-heading")
        for reason in REASONS:
            _reason_row(reason, lambda r=reason: _picked(client_id, r))
        _reason_row(OTHER, lambda: _other(client_id))
        with ui.element("div").classes("answer-box interrupt-other") as other:
            field = (
                ui.textarea(placeholder=OTHER_PLACEHOLDER)
                .props("autogrow borderless dense")
                .classes("answer-field")
            )
            for key in _SEND_KEYS:
                field.on(key, lambda: _send_other(client_id))
            c.button(SEND, lambda: _send_other(client_id)).tooltip(SEND_TIP)
        other.set_visibility(False)
        tick = ui.checkbox(DONT_ASK).classes("p-check interrupt-dont-ask").props("dense")
    card.set_visibility(False)
    _MENUS[client_id] = _Menu(card=card, other=other, field=field, tick=tick)


def _reason_row(reason: str, on_pick: Any) -> None:
    with ui.element("div").classes("choice-row") as row:
        ui.icon(_REASON_ICONS[reason]).classes("choice-row-icon")
        with ui.element("div").classes("choice-row-text"):
            ui.label(reason).classes("choice-row-name")
    row.on("click", on_pick)


def _menu(client_id: str) -> _Menu | None:
    menu = _MENUS.get(client_id)
    if menu is None or menu.card.is_deleted:
        _MENUS.pop(client_id, None)
        return None
    return menu


def pressed(card: str) -> None:
    """A press on a card's *Interrupt query* (`assets/interrupt.js` → ``portia:interrupt``).

    Pressed again while its menu is open, the menu shuts and nothing happens.
    Pressed once the call is interrupted, it skips the measuring. Otherwise it
    asks why, or, with *Don't ask again*, interrupts at once.
    """
    if exchange.running_call(card) is None:
        return
    menu = _menu(context.client.id)
    if menu is not None and menu.open_for == card:
        _close(menu)
        return
    if exchange.is_interrupted(card):
        _act(card)
        return
    if not APP.ask_before_interrupt or menu is None:
        _act(card)
        return
    if menu.open_for is not None:
        _close(menu)
    _open(menu, card)


def closed(card: str) -> None:
    """The menu was shut without a pick (a click away, Escape): the press is undone."""
    menu = _menu(context.client.id)
    if menu is not None and menu.open_for is not None and menu.open_for == (card or menu.open_for):
        _close(menu)


def _open(menu: _Menu, card: str) -> None:
    menu.open_for = card
    menu.card.props(f"data-anchor={c.prop_value(card)}")
    menu.card.set_visibility(True)


def _close(menu: _Menu) -> None:
    """Shut it and forget what was in it: the next press starts from nothing."""
    menu.open_for = None
    menu.card.props(remove="data-anchor")
    menu.card.set_visibility(False)
    menu.other.set_visibility(False)
    menu.field.value = ""
    menu.tick.value = False


def _other(client_id: str) -> None:
    """*Other*: the sentence box opens in place, and the interrupt waits for it."""
    menu = _menu(client_id)
    if menu is not None and menu.open_for is not None:
        menu.other.set_visibility(True)
        menu.field.run_method("focus")


def _send_other(client_id: str) -> None:
    menu = _menu(client_id)
    if menu is not None:
        _picked(client_id, OTHER, note=str(menu.field.value or ""))


def _picked(client_id: str, reason: str, note: str = "") -> None:
    """A reason picked: interrupt with it, and keep *Don't ask again* if it was ticked."""
    menu = _menu(client_id)
    if menu is None or menu.open_for is None:
        return
    card = menu.open_for
    if menu.tick.value:
        APP.ask_before_interrupt = False
    _close(menu)
    _act(card, reason=reason, note=note)


def _act(card: str, *, reason: str | None = None, note: str = "") -> None:
    """Interrupt the call, or skip its measuring, and redraw what says so."""
    from portia.ui import transcript

    outcome = exchange.interrupt_call(card, reason=reason, note=note)
    if outcome == exchange.TOO_LATE:
        ui.notify(TOO_LATE)
    transcript.call_moved()


def tick() -> None:
    """Shut this tab's menu if its call answered while it was open. Once a second."""
    menu = _menu(context.client.id)
    if menu is not None and menu.open_for is not None:
        if exchange.running_call(menu.open_for) is None:
            _close(menu)

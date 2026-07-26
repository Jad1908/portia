"""Loading the look, and the light/dark override.

`DESIGN.md` is written as tokens and component definitions rather than NiceGUI
APIs, precisely so the UI stays swappable (docs/TECH_STACK.md). It lives here as
a plain stylesheet — ``assets/portia.css`` — and this module does nothing but
attach it. Keeping the CSS in a file rather than a Python string is also what
keeps the look diffable as a look.

Light and dark are equal first-class modes. Quasar resolves ``auto`` from
``prefers-color-scheme`` and puts ``body--dark`` on the body; the stylesheet
hangs the dark token block off that class, so the manual override is the same
mechanism as the system preference rather than a second one.
"""

from __future__ import annotations

from pathlib import Path

from nicegui import ui

CSS = Path(__file__).parent / "assets" / "portia.css"

_PRECONNECT = '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
_INTER = "https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&display=swap"

#: auto → light → dark → auto. ``None`` is auto; the label says which is in play,
#: because a toggle that only shows an icon can't distinguish "dark" from
#: "auto, and it's night".
_CYCLE: dict[bool | None, bool | None] = {None: False, False: True, True: None}
MODE_ICON = {None: "brightness_auto", False: "light_mode", True: "dark_mode"}
MODE_LABEL = {None: "auto", False: "light", True: "dark"}


def apply() -> ui.dark_mode:
    """Attach the stylesheet and the font, and return the mode control."""
    ui.add_head_html(_PRECONNECT)
    ui.add_head_html(f'<link rel="stylesheet" href="{_INTER}">')
    ui.add_css(CSS)
    return ui.dark_mode(None)


def next_mode(current: bool | None) -> bool | None:
    return _CYCLE[current]

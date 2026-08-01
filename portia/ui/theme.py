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

from nicegui import app as ng_app
from nicegui import ui

ASSETS = Path(__file__).parent / "assets"
CSS = ASSETS / "portia.css"

#: The mark. Lives in the package rather than the repo's `assets/` so it ships
#: with a `pip install`, and downscaled from the 1024px original because it is
#: never drawn larger than a few dozen pixels.
LOGO_FILE = ASSETS / "cute-portia.png"
LOGO_ROUTE = "/portia-assets"
LOGO = f"{LOGO_ROUTE}/{LOGO_FILE.name}"

ng_app.add_static_files(LOGO_ROUTE, ASSETS)

_PRECONNECT = '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
_INTER = "https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&display=swap"

#: auto → light → dark → auto. ``None`` is auto; the label says which is in play,
#: because a toggle that only shows an icon can't distinguish "dark" from
#: "auto, and it's night".
_CYCLE: dict[bool | None, bool | None] = {None: False, False: True, True: None}
MODE_ICON = {None: "brightness_auto", False: "light_mode", True: "dark_mode"}
MODE_LABEL = {None: "auto", False: "light", True: "dark"}


#: Drag-to-pan for the workflow canvas, and the window-width reporter behind
#: `DESIGN.md`'s width behaviour. Files rather than inline strings for the same
#: reason the CSS is one: behaviour worth reading is behaviour worth diffing.
PAN_JS = ASSETS / "pan.js"
VIEWPORT_JS = ASSETS / "viewport.js"


def apply() -> ui.dark_mode:
    """Attach the stylesheet, the font and the canvas panning; return the mode control."""
    ui.add_head_html(_PRECONNECT)
    ui.add_head_html(f'<link rel="stylesheet" href="{_INTER}">')
    ui.add_css(CSS)
    ui.add_body_html(f"<script>{PAN_JS.read_text()}</script>")
    ui.add_body_html(f"<script>{VIEWPORT_JS.read_text()}</script>")
    return ui.dark_mode(None)


def next_mode(current: bool | None) -> bool | None:
    return _CYCLE[current]


def logo(small: bool = False) -> ui.html:
    """The mark.

    A plain ``<img>`` rather than ``ui.image``: Quasar's QImg fades in on an
    animation frame, and a logo that depends on a frame firing is a logo that is
    sometimes missing.
    """
    css = "p-logo-sm" if small else "p-logo"
    return ui.html(f'<img class="{css}" src="{LOGO}" alt="portia">')

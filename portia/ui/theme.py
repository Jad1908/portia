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

#: The path the stylesheet writes an asset under. **Never served as written**:
#: `apply` rewrites it to :data:`ASSET_ROUTE`, which carries a stamp of every
#: asset's contents, so a changed mark gets a new URL. Without that the browser
#: kept the old provider marks for as long as its cache liked (2026-09-08): the
#: SVGs changed, the URLs the mask rules named did not, and a subresource a
#: stylesheet names is exactly what a browser reuses without asking.
ASSET_TOKEN = "/portia-assets"


def _asset_stamp() -> str:
    """Eight hex characters over every file under `ASSETS`, names included."""
    import hashlib

    digest = hashlib.sha256()
    for path in sorted(ASSETS.rglob("*")):
        if path.is_file():
            digest.update(str(path.relative_to(ASSETS)).encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()[:8]


ASSET_ROUTE = f"{ASSET_TOKEN}/{_asset_stamp()}"
LOGO_ROUTE = ASSET_ROUTE
LOGO = f"{LOGO_ROUTE}/{LOGO_FILE.name}"

ng_app.add_static_files(ASSET_ROUTE, ASSETS)

_PRECONNECT = '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
_INTER = "https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&display=swap"

#: auto → light → dark → auto. ``None`` is auto; the label says which is in play,
#: because a control that only shows an icon can't distinguish "dark" from
#: "auto, and it's night".
_CYCLE: dict[bool | None, bool | None] = {None: False, False: True, True: None}
MODE_ICON = {None: "brightness_auto", False: "light_mode", True: "dark_mode"}
MODE_LABEL = {None: "auto", False: "light", True: "dark"}
#: The three, in the order the settings panel offers them, and the way back from
#: the label a segmented control hands you.
MODES: tuple[bool | None, ...] = (None, False, True)
MODE_VALUE = {label: value for value, label in MODE_LABEL.items()}

#: The page's light/dark control. It lives here rather than in `app.py` because
#: two surfaces now change it — the settings panel and nothing else, since the
#: toolbar toggle is gone — and a mode held by whichever module happened to build
#: the page is a mode the other one has to reach into `app` for.
_DARK: ui.dark_mode | None = None


#: The client-side behaviour, one file each. Files rather than inline strings for
#: the same reason the CSS is one: behaviour worth reading is behaviour worth
#: diffing. All three exist because they hold something the *client* owns — where
#: the canvas is looking, how wide the window is, where a pane is scrolled to —
#: which is state a render would either race or throw away.
CANVAS_JS = ASSETS / "canvas.js"
VIEWPORT_JS = ASSETS / "viewport.js"
SCROLL_JS = ASSETS / "scroll.js"
KNOWLEDGE_JS = ASSETS / "knowledge.js"
#: A chart, drawn from portia's own encoding rather than a Vega-Lite spec
#: (`docs/VISUALIZATION.md` §5). It holds client state for the same reason the
#: others do: a rendered canvas does not survive the pane refresh that replaced
#: the element under it, so redrawing is watched for rather than pushed.
CHART_JS = ASSETS / "chart.js"
#: Dragging a figure into a folder, resolved on the client before anything
#: reaches the server — `pick.js`'s rule, for `pick.js`'s reason: the rows are
#: rebuilt between the press and the release.
GALLERY_JS = ASSETS / "gallery.js"
#: Dragging a tab into the right half of the middle pane, which is how the pane
#: splits (`docs/VISUALIZATION.md` §3.7). Client-side for `gallery.js`'s reason:
#: the strip is rebuilt between the press and the release, so a drag
#: reconstructed from server events is about the wrong tab by the time it ends.
TABS_JS = ASSETS / "tabs.js"
#: Which gesture a click on a spec row was. It belongs to the client for a
#: sharper reason than the others: acting on the first press re-renders the pane
#: and moves the rows, so the second press of a double click can land on a
#: different spec. Resolving it here means nothing is asked of the server until
#: the gesture is known, and the DOM has no reason to move in between.
PICK_JS = ASSETS / "pick.js"
#: Which elements are *new* — the question an entry animation depends on, and
#: one only the client can answer: NiceGUI replaces a refreshable's elements, so
#: to the server every render is everything arriving at once. It remembers keys,
#: not elements, for `pick.js`'s reason.
MOTION_JS = ASSETS / "motion.js"
#: Where the open model picker is looking: the provider on its rail, the
#: search, the legacy fold, the row under the arrow keys. The canvas's rule:
#: a round trip per keystroke would rebuild the box being typed into.
MODELPICK_JS = ASSETS / "modelpick.js"
BEHAVIOUR = (
    CANVAS_JS,
    VIEWPORT_JS,
    SCROLL_JS,
    KNOWLEDGE_JS,
    CHART_JS,
    GALLERY_JS,
    TABS_JS,
    PICK_JS,
    MOTION_JS,
    MODELPICK_JS,
)

#: The graph explorer's renderer — the one third-party script the window loads.
#: `KNOWLEDGE_GRAPH.md` §6.9: the knowledge graph is not the project canvas, and
#: force layout, expand-on-click and hairball management are the whole job of a
#: library that already exists. This is the engine `neovis.js` wraps, used
#: directly so the database password never has to reach the browser.
_VIS_NETWORK = "https://unpkg.com/vis-network@9.1.9/standalone/umd/vis-network.min.js"

#: The chart renderer — the second third-party script, and it is three files
#: because Vega-Lite compiles to Vega and `vega-embed` is what mounts it.
#: `docs/VISUALIZATION.md` §5 picked it over Plotly on three counts: its theme is
#: JSON so `DESIGN.md`'s tokens compose into a config rather than a Python dict,
#: its schema is small enough that `assets/chart.js` can refuse an encoding that
#: computes, and it is declarative — so nothing about a chart is a callback that
#: has to survive a pane refresh, which is the class of bug this window has paid
#: for three times.
_VEGA = (
    "https://cdn.jsdelivr.net/npm/vega@5.25.0/build/vega.min.js",
    "https://cdn.jsdelivr.net/npm/vega-lite@5.16.3/build/vega-lite.min.js",
    "https://cdn.jsdelivr.net/npm/vega-embed@6.22.2/build/vega-embed.min.js",
)


def apply() -> ui.dark_mode:
    """Attach the stylesheet, the font and the client-side behaviour; return the mode control."""
    global _DARK
    ui.add_head_html(_PRECONNECT)
    ui.add_head_html(f'<link rel="stylesheet" href="{_INTER}">')
    ui.add_head_html(f'<script src="{_VIS_NETWORK}"></script>')
    for src in _VEGA:
        ui.add_head_html(f'<script src="{src}"></script>')
    ui.add_css(CSS.read_text(encoding="utf-8").replace(f"{ASSET_TOKEN}/", f"{ASSET_ROUTE}/"))
    for script in BEHAVIOUR:
        ui.add_body_html(f"<script>{script.read_text(encoding='utf-8')}</script>")
    _DARK = ui.dark_mode(None)
    return _DARK


def mode() -> bool | None:
    """Which of the three is in play: ``None`` auto, ``False`` light, ``True`` dark."""
    return _DARK.value if _DARK is not None else None


def set_mode(value: bool | None) -> None:
    if _DARK is not None:
        _DARK.value = value


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

"""Settings → Providers: every place the copilot's model can come from, and how each one is.

The composer's picker says which provider a chat runs on; it cannot say why a
provider is missing from it, whether the Codex binary was found, or which
account the Claude binary is signed in as. This section does (`docs/PROVIDERS.md`
§9.5). It is one list and one detail: down the left, every kind portia knows
with a light and one line for its state and a switch for whether the picker
offers it; on the right, the picked one in full. What it draws is what the
providers measured (`Provider.status`, run off the loop by
`engine.check_providers`) and what the machine's settings file says
(`providers.SETTINGS`), and it changes nothing but that file.

**Kind, never rank.** The rows are in `providers.KINDS` order, every glyph is
the same size, and a provider that cannot be reached is a grey light with the
provider's own sentence beside it, never a red badge. Enabled is a preference
and reachable is a measurement; the switch is the first and the light is the
second, and the two are drawn apart.

The state on `App` is read here and written by `ui/engine.py`; this module
computes nothing (`CLAUDE.md` → `ui/engine.py`).
"""

from __future__ import annotations

import time

from nicegui import ui

from portia.agent import providers
from portia.ui import components as c
from portia.ui import engine
from portia.ui.state import APP

TITLE = "Providers"
WHY = "Where the copilot's model can come from, and whether each one can be reached now."
NOT_CHECKED = "Not checked yet"
CHECKING = "checking…"
CHECK = "Check again"
ENABLED_TIP = "Offered in the picker"
NOT_MEASURED = "not measured"
REACHABLE = "reachable"
UNREACHABLE = "not reachable"
DISABLED = "not offered"
RUNTIME = "Runtime"
VARIABLES = "Variables"
VARIABLES_WHY = "Set in the provider's process, on top of the machine's own. Written in the clear."
ADD_VARIABLE = "Add variable"
ADD = "Add"
REMOVE = "Remove"
NAME = "Name"
VALUE = "Value"
KNOWN = "portia reads:"
BINARY_WHAT = "Binary path"
BINARY_WHY = "The program this provider runs. Empty means the bundled one, else the one on PATH."
HOME_WHAT = "Sign-in home"
HOME_WHY = "Where its own sign-in lives. Empty means the vendor's default."
SETTINGS_FILE = "Saved in"
START_SERVER = "Start the server…"

#: The keys `engine.save_provider_settings` takes for the two runtime fields,
#: and what each is called on screen.
_FIELDS = {"binary": (BINARY_WHAT, BINARY_WHY), "home": (HOME_WHAT, HOME_WHY)}


def ago(seconds: float | None) -> str:
    """*just now*, *3m ago*, *2h ago*: when the last check ran, for the header."""
    if seconds is None:
        return NOT_CHECKED
    if seconds < 60:
        return "Checked just now"
    if seconds < 3600:
        return f"Checked {int(seconds // 60)}m ago"
    return f"Checked {int(seconds // 3600)}h ago"


def state_words(kind: str) -> tuple[str, str]:
    """The light and the word for one provider: its measured state, else *not measured*."""
    status = APP.provider_status.get(kind)
    if status is None or status.reachable is None:
        return c.OFF, NOT_MEASURED
    return (c.ON, REACHABLE) if status.reachable else (c.OFF, UNREACHABLE)


def section(on_change) -> None:
    """The whole section, inside the settings row that names it: the header, the list, the detail.

    ``on_change`` redraws the settings panel; every control calls it after the
    engine has written, so the panel never shows a value the file does not.
    """
    settings = APP.provider_settings or engine.load_provider_settings(APP)
    if APP.providers_checked_at is None and not APP.providers_checking:
        # The first look runs the check by itself: a dashboard that opens on
        # *not checked yet* and a button is a dashboard that says nothing.
        _check(on_change)
    with ui.element("div").classes("row-gap-sm providers-head"):
        checked = (
            None
            if APP.providers_checked_at is None
            else time.monotonic() - APP.providers_checked_at
        )
        if APP.providers_checking:
            ui.spinner(size="xs")
            c.caption(CHECKING)
        else:
            c.caption(ago(checked))
            c.button("", lambda: _check(on_change), icon="refresh", micro=True).tooltip(CHECK)
    with ui.element("div").classes("providers-layout"):
        with ui.element("div").classes("providers-list"):
            for kind in providers.KINDS:
                _row(kind, settings[kind], on_change)
        with ui.element("div").classes("provider-detail"):
            _detail(APP.provider_pick, settings[APP.provider_pick], on_change)
    c.caption(f"{SETTINGS_FILE} {providers.SETTINGS}")


def _row(kind: str, settings: providers.Settings, on_change) -> None:
    provider = providers.get(kind)
    light, word = state_words(kind)
    status = APP.provider_status.get(kind)
    selected = kind == APP.provider_pick
    row = ui.element("div").classes(
        "provider-row" + (" provider-row--selected" if selected else "")
    )
    with row:
        c.provider_glyph(kind, tip=False)
        with ui.element("div").classes("provider-row-body"):
            ui.label(provider.label).classes("provider-row-name")
            with ui.element("div").classes("row-gap-xs provider-row-state"):
                c.status_light(light)
                line = word if settings.enabled else DISABLED
                if status is not None and status.detail:
                    line = f"{line} · {status.detail}"
                c.caption(line).classes("provider-row-line")
        switch = ui.switch().classes("p-toggle provider-row-switch")
        switch.value = settings.enabled
        switch.on_value_change(lambda e, k=kind: _set_enabled(k, bool(e.value), on_change))
        # The switch's own click must not also pick the row.
        switch.on("click.stop", lambda: None)
    row.on("click", lambda k=kind: _pick(k, on_change))


def _detail(kind: str, settings: providers.Settings, on_change) -> None:
    provider = providers.get(kind)
    status = APP.provider_status.get(kind)
    light, word = state_words(kind)
    with ui.element("div").classes("row-gap-sm provider-detail-head"):
        c.provider_glyph(kind, tip=False)
        ui.label(provider.label).classes("t-heading-sm")
    with c.kv_list():
        with ui.element("div").classes("row-gap-xs"):
            c.status_light(light)
            ui.label(word).classes("kv-key")
        ui.label(status.detail if status is not None and status.detail else "").classes("kv-value")
        if status is not None and status.version:
            c.kv("version", status.version)
        if status is not None and status.account:
            c.kv("account", status.account)
        c.kv("harness", provider.harness)
    if (
        status is not None
        and status.reachable is False
        and (status.remedy or provider.start_remedy)
    ):
        c.caption(status.remedy or provider.start_remedy)
    if provider.starts and status is not None and status.reachable is False:
        from portia.ui import screens

        c.button(
            START_SERVER, lambda: screens.open_server_dialog(kind), icon="play_arrow", micro=True
        )
    if provider.runtime_fields:
        ui.label(RUNTIME).classes("setting-title")
        for name in provider.runtime_fields:
            what, why = _FIELDS[name]
            c.field(
                what,
                hint=why,
                value=getattr(settings, name),
                mono=True,
                on_change=lambda e, k=kind, n=name: _set_field(k, n, str(e.value or ""), on_change),
            )
    ui.label(VARIABLES).classes("setting-title")
    c.caption(VARIABLES_WHY)
    notes = provider.env_notes()
    for key, value in sorted(settings.env.items()):
        with ui.element("div").classes("row-gap-sm provider-var"):
            c.mono(key, small=True)
            box = c.field(
                VALUE,
                value=value,
                mono=True,
                secret=key.upper().endswith("KEY"),
                on_change=lambda e, k=kind, n=key: _set_var(k, n, str(e.value or ""), on_change),
            )
            box.classes("provider-var-value")
            c.button(
                "", lambda k=kind, n=key: _drop_var(k, n, on_change), icon="close", micro=True
            ).tooltip(REMOVE)
        if key in notes:
            c.caption(notes[key])
    _adder(kind, on_change)
    unknown = [k for k in notes if k not in settings.env]
    if unknown:
        c.caption(KNOWN)
        for key in unknown:
            c.caption(f"{key} — {notes[key]}")
    if provider.add_command("<name>"):
        c.add_model_line(kind)


def _adder(kind: str, on_change) -> None:
    """Two boxes and a button: a new variable, written on Add and never on a keystroke."""
    draft: dict[str, str] = {"name": "", "value": ""}
    with ui.element("div").classes("row-gap-sm provider-var provider-var-new"):
        c.field(NAME, mono=True, on_change=lambda e: draft.__setitem__("name", str(e.value or "")))
        c.field(
            VALUE, mono=True, on_change=lambda e: draft.__setitem__("value", str(e.value or ""))
        )
        c.button(ADD, lambda: _add_var(kind, draft, on_change), icon="add", micro=True)


# --- what the controls do --------------------------------------------------------


def _check(on_change) -> None:
    from nicegui import background_tasks

    async def go() -> None:
        on_change()
        await engine.check_providers(APP)
        on_change()

    background_tasks.create(go())


def _pick(kind: str, on_change) -> None:
    APP.provider_pick = kind
    on_change()


def _set_enabled(kind: str, on: bool, on_change) -> None:
    engine.save_provider_settings(APP, kind, enabled=on)
    on_change()


def _set_field(kind: str, name: str, value: str, on_change) -> None:
    engine.save_provider_settings(APP, kind, **{name: value.strip()})


def _set_var(kind: str, key: str, value: str, on_change) -> None:
    env = dict(APP.provider_settings[kind].env)
    env[key] = value
    engine.save_provider_settings(APP, kind, env=env)


def _drop_var(kind: str, key: str, on_change) -> None:
    env = dict(APP.provider_settings[kind].env)
    env.pop(key, None)
    engine.save_provider_settings(APP, kind, env=env)
    on_change()


def _add_var(kind: str, draft: dict[str, str], on_change) -> None:
    key = draft.get("name", "").strip()
    if not key:
        return
    env = dict(APP.provider_settings[kind].env)
    env[key] = draft.get("value", "")
    engine.save_provider_settings(APP, kind, env=env)
    on_change()

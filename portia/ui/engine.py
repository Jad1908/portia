"""The only place in `portia/ui/` that touches the engine.

`cli/` and `ui/` are two renderers of one engine, and the day they disagree about
a number is the day the seam broke (docs/VISION.md). Keeping every engine call in
one small module is how that stays checkable: the whole list is right here, and
nothing below it computes anything.

What the app is allowed to call, and why each is on the list:

- ``catalog.init_project`` — the mandatory context panel writes ``project.yaml``
- ``catalog.set_data_dir`` — which folder in the repo is this project's data,
  picked on the add-data screen and read back by the left pane
- ``catalog.index_source`` — a dropped file is profiled (free, deterministic)
- ``catalog.load_catalog`` — what the left pane and the source inspector show
- ``spec.load_spec`` / ``spec.run_spec`` / ``spec.write_report`` — the Run button
  and the report it can save
- ``pipeline.write_outputs`` — the tables a build produced, one CSV per model
- ``spec.discover_specs`` — the project's models, so the panel and the engine
  agree on what a spec is and a cross-spec reference resolves
- ``pipeline.build_project`` / ``stale_models`` — compiling to SQL, and whether a
  generated file still matches the spec that produced it
- ``core.io.load_table`` — previewing a produced table (the one way to load data)
- ``core.io.find_data_files`` — resolving what an import points at
- ``cli.import_data.plan`` — what a set of files would be copied to, and where.
  Shared with the terminal deliberately (`docs/PIPELINE.md` §6): the window shows
  the same plan `import_data` shows because it *is* the same plan, not because
  two surfaces were written to agree about one
- ``agent.session.run`` — an exchange, driven with the app's own answer/confirm
- ``runlog.logs_in`` / ``read`` / ``read_header`` / ``summary`` — past chats and
  indexing jobs for their two sections and the replay. The summary in particular: those
  counts are the engine's, so the window and `cli.history` cannot end up quoting
  two different numbers for how often the copilot asked.

The one thing here that isn't the engine is ``browse_for_folder``: the OS's own
folder chooser, because picking a directory by typing its absolute path is not a
thing anyone should be asked to do.

Blocking work (profiling a source, executing a spec) hits the database and would freeze the
websocket, so it goes through ``asyncio.to_thread``. Reading a file the panes
draw — a report, a compiled model, a chat's log — deliberately does **not**: the
middle pane draws in one pass so that a click never paints a blank frame, and an
`await` in the middle of a render is exactly what that costs (`workflow.pane`).
Nothing here formats anything for a human — that is the panes' job.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Callable, Collection
from dataclasses import dataclass
from datetime import datetime
from functools import partial
from pathlib import Path
from typing import Any

from portia import catalog, figures, findings, pipeline, runlog
from portia import spec as spec_module
from portia.agent import drawn
from portia.cli.import_data import plan as plan_copy
from portia.core import cancel, feedback
from portia.core.io import connect, find_data_files, load_table, source_table, supported_suffixes
from portia.core.table import Table
from portia.ui import graph, prefs, tree
from portia.ui import state as State
from portia.ui.state import App

#: Where an import lands when the project has not named a data folder, and where
#: a run writes its tables. Relative to the project root, so the catalog and the
#: spec stay portable.
DATA_DIR = "data"
OUT_DIR = "out"

#: Where saved *spec run* reports used to live, at the project root. **Read, never
#: written** *(2026-08-16)* — a run's account now sits inside the run's own output
#: folder (`pipeline.RUN_META_DIR`), because the tables and the account of how they
#: were made are two halves of one result and two trees let them age apart. Kept so
#: the Runs section still lists what a previous version wrote, the same way
#: `.portia/runs/` is kept in `runlog.py`.
RUNS_DIR = "runs"

#: The one file in a run folder that says what the folder is, without opening a
#: table.
RUN_INDEX = "index.md"

#: How many recent projects the picker lists. Where they are kept, and where
#: the canvas filter and the dragged cards are kept beside them, is `prefs`:
#: one file for everything the window remembers about the person using it.
RECENTS_KEPT = prefs.RECENTS_KEPT


# --- opening a project ------------------------------------------------------


def open_project(path: str | Path, app: App) -> Path:
    """Point the app at a directory, creating it if it doesn't exist yet.

    Testing means a fresh directory per run, so a path that isn't there yet is
    created rather than rejected (docs/VISION.md → `project-open`).

    The process working directory moves with it. That is not incidental: the
    engine resolves a catalog's ``source:`` paths and a spec's ``sources:`` paths
    relative to the current directory — the way a project config normally is —
    and the agent's tools do the same. Running the app from elsewhere without
    this would make every relative path in the project mean something different
    to the UI than it means to the copilot.
    """
    root = Path(path).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    # Whoever is drawing charts into the project being left must stop being told
    # a window is watching it (`agent/drawn.py`).
    drawn.withdraw(app.catalog_dir)
    os.chdir(root)
    app.root = root
    app.spec_path = None
    app.spec = None
    app.results = None
    app.run_error = None
    app.selection = None
    app.selected_step = None
    # The charts too. An unsaved chart followed you into the next project and
    # sat there marked *unsaved* beside figures it had nothing to do with; its
    # SQL names tables this project does not have (`state.App.leave_project`).
    app.leave_project()
    app.chats = []
    app.open = None
    app.right = State.LIST
    app.skipped_sources = False
    app.editing = app.asking = app.removing = None
    # The add-data screen, back to the top. Where the picker was browsing and
    # which files were ticked are statements about the last project's directory,
    # and a half-planned import into it must not follow you into this one.
    app.browse_at = ""
    app.unpicked = app.pick_closed = frozenset()
    app.pick_filter = ""
    app.repicking = False
    app.import_open = False
    app.import_to_data_dir = True
    app.import_plan, app.import_error = [], ""
    app.indexed = None
    app.indexing_failed = {}
    # Which folders are open belongs to the project you are looking at, not to
    # the window: `data/` opened in the last project says nothing about this one.
    app.open_folders = app.closed_folders = frozenset()
    # The canvas as you left it: which tables it was narrowed to, and where you
    # dragged the cards. **The first of these was written and never read back**
    # until 2026-09-07 — `remember_view` had a caller and `remembered_view` had
    # none, so the filter was a file nothing opened.
    app.visible = remembered_view(root)
    app.card_offsets = remembered_layout(root)
    # Charts a host drew here while no window was open, as the unsaved charts
    # they are: listed in the gallery, and **not opened**, because forty tabs
    # from last week is not what opening a project asks for.
    for chart in take_drawn(app):
        chart.closed = True
        app.charts.append(chart)
    drawn.announce(app.catalog_dir, app.url)
    app.previewing = None
    refresh_catalog(app)
    # A project that already has data is not being set up, so it opens on the
    # workspace. The add-data screen is for the first time, and for whenever
    # someone asks for it from the left pane.
    app.left_add_data = bool(app.sources)
    # The warehouse, if the project names one: the backend is installed now and
    # the session is opened later, off the loop, because opening one may open a
    # browser (`docs/CONNECTOR.md` §2.4).
    app.warehouse_closed = frozenset()
    app.connect_form, app.connect_error, app.connect_secret = {}, "", ""
    forget_scope_picker(app)
    install_backend(app)
    # Where the data is, when the project has already said (`state.data_mode`).
    if app.connection:
        app.data_mode = State.WAREHOUSE_DATA
    elif app.data_dir or app.sources:
        app.data_mode = State.LOCAL_DATA
    else:
        app.data_mode = ""
    remember(root)
    return root


def choose_data(mode: str, app: App) -> None:
    """The answer to *where is the data*. Session state until the project says.

    **Choosing files on a project that names a connection un-names it**, which is
    the one durable half of changing your mind: `open_project` reads the kind
    back off ``connection``, so a name left in ``project.yaml`` would put the
    project back on the warehouse at the next open. Only reachable while
    `can_change_data`, so there is no scoped table to strand.
    """
    if mode not in (State.LOCAL_DATA, State.WAREHOUSE_DATA):
        raise ValueError(f"unknown data mode {mode!r}")
    if mode == State.LOCAL_DATA and app.connection and can_change_data(app):
        catalog.set_connection(None, portia_dir=app.portia_dir)
        refresh_catalog(app)
        forget_scope_picker(app)
        install_backend(app)
    app.data_mode = mode


def can_change_data(app: App) -> bool:
    """Whether *where is the data* can still be answered differently.

    A project reads from one place (`CONNECTOR.md` §2.2): a file cannot be
    joined to a warehouse table without one side crossing the wire. So the
    answer is open until something is indexed and held after, because the
    first source is what would have to be mixed with. Until 2026-09-18 it was
    held from the moment a card was pressed, with nothing indexed and no way
    back to the cards.
    """
    return not app.sources


def reopen_data_choice(app: App) -> None:
    """Back to the two cards. Refused once a source pins the project."""
    if not can_change_data(app):
        raise ValueError("this project already has sources, and a project reads from one place")
    app.data_mode = ""


def needs_secret(app: App) -> bool:
    """Whether connecting needs a password or token typed first (`connectors.needs_secret`)."""
    from portia import connectors
    from portia.core import backend as backends

    return bool(app.connection) and connectors.needs_secret(backends.active())


# --- the warehouse ----------------------------------------------------------


def forget_scope_picker(app: App) -> None:
    """Drop what the scope picker listed and ticked: it was another connection's.

    A browse is a query, so `browse_scope` keeps each answer on the app and the
    picker draws from there. **That answer belongs to the connection that gave
    it** *(2026-09-20, the user's report)*: connect to BigQuery, then connect to
    PostgreSQL from the same screen, and the tree still drew BigQuery's
    datasets, because the databases were already listed and were never asked
    for again. The ticks go too: a ticked table of the last connection is a
    name this one may not have. Called wherever the project's connection is
    set, never from a render.
    """
    app.scope_open, app.scope_listing, app.scope_ticks = frozenset(), {}, frozenset()
    app.scope_filter, app.scope_loading = "", None


def install_backend(app: App) -> None:
    """Point the process at the project's engine, **without connecting**.

    `core/backend.py` has one active backend per process and it is set by
    whoever opens a project; this is that call for the window. A project that
    names a connection nobody on this machine has is reported in
    `connection_status` rather than raised: the window still opens, on DuckDB,
    and the pane says what is wrong and where to fix it.
    """
    from portia import connectors

    connectors.deactivate()
    if not app.connection:
        app.connection_status = ""
        return
    try:
        connectors.activate(app.portia_dir, connect=False)
    except connectors.ConnectorUnavailable as exc:
        app.connection_status = f"failed: {exc}"
        return
    app.connection_status = State.NOT_CONNECTED


async def list_models(app: App, kind: str) -> None:
    """Ask one provider what it serves, off the loop, and keep the answer on the app.

    `Provider.models` is a network call (`docs/PROVIDERS.md` §4.3), so the
    picker never runs it in a render: it draws `App.provider_models` and this
    fills it. A provider that cannot be reached leaves an empty list and a
    status saying why; the picker draws the provider's own remedy.
    """
    from portia.agent import providers

    provider = providers.get(kind)
    app.models_listing |= {kind}
    before = provider.default_model
    try:
        status, models = await asyncio.to_thread(_ask_provider, provider)
    except Exception as exc:  # noqa: BLE001 - a picker left spinning is worse than any error
        # A listing that raised used to die inside the background task, so
        # `models_listing` was cleared and nothing redrew the pane: the spinner
        # stayed for as long as the picker did (2026-09-14, port 8080 answered
        # as the window). Whatever went wrong is the status now.
        status, models = providers.Status(reachable=False, detail=str(exc)), []
    finally:
        app.models_listing -= {kind}
    app.provider_status[kind] = status
    app.provider_models[kind] = models
    # A provider whose default is the server's own name has no name until it
    # has been listed (`llamacpp.SERVED`): a chat opened before the listing
    # holds the placeholder. When the default moved and the window was on it,
    # follow it; a name someone picked on purpose is left alone.
    if app.provider == kind and app.model == before and provider.default_model != before:
        app.model = provider.default_model


def _ask_provider(provider: Any) -> tuple[Any, list[Any]]:
    from portia.agent import providers

    status = provider.status()
    if status.reachable is False:
        return status, []
    try:
        return status, provider.models()
    except RuntimeError as exc:
        # `ProviderUnavailable` and a provider's own error are both this.
        return providers.Status(reachable=False, detail=str(exc)), []


async def check_providers(app: App) -> None:
    """Ask every provider how it is, off the loop, and stamp when (`ui/providers.py`).

    `Provider.status` is a subprocess for the two harnesses (`codex --version`,
    `codex login status`, the Claude binary's own report) and a request for
    the two servers, so it never runs in a render. Each kind's answer lands in
    `App.provider_status` as it comes; a provider that raises is recorded as
    not reachable in its own words, for `list_models`' reason.
    """
    from portia.agent import providers

    app.providers_checking = True
    try:
        for kind in providers.KINDS:
            try:
                app.provider_status[kind] = await asyncio.to_thread(providers.get(kind).status)
            except Exception as exc:  # noqa: BLE001 - one provider failing is that provider's line
                app.provider_status[kind] = providers.Status(reachable=False, detail=str(exc))
        app.providers_checked_at = time.monotonic()
    finally:
        app.providers_checking = False


def load_provider_settings(app: App) -> dict[str, Any]:
    """The machine's provider settings, read once onto the app and reread after a save."""
    from portia.agent import providers

    app.provider_settings = providers.load_settings()
    return app.provider_settings


def save_provider_settings(app: App, kind: str, **changes: Any) -> None:
    """Change one provider's settings and write the file (`providers.SETTINGS`).

    ``changes`` are `providers.Settings` fields: ``enabled``, ``binary``,
    ``home`` or ``env``. Everything else in the file is kept as it was.
    """
    import dataclasses

    from portia.agent import providers

    settings = app.provider_settings or load_provider_settings(app)
    current = settings.get(kind, providers.Settings())
    settings[kind] = dataclasses.replace(current, **changes)
    providers.save_settings(settings)
    load_provider_settings(app)


async def start_server(app: App) -> bool:
    """Start the llama.cpp server from `App.server_form`, off the loop, and list it.

    The form is saved first (`llamacpp.save_config`, one file for the machine),
    then the process is started and the loop waits on ``/health`` in a thread:
    a first load reads gigabytes off disk and a first ``-hf`` fetch downloads
    them, and the panel says *starting* for the duration. A refusal, in the
    provider's words with the log's last lines, lands in `App.server_error`
    and the process is stopped rather than left half up. Returns whether the
    server came up; the list is read either way so the picker is current.
    """
    from portia.agent import providers
    from portia.agent.providers import llamacpp

    app.server_error = ""
    try:
        config = llamacpp.ServerConfig.from_form(app.server_form)
    except ValueError as exc:
        app.server_error = str(exc)
        return False
    # This window's own port is refused before anything is saved: saved, the
    # picker would go on asking the window for models (`PROVIDERS.md` §4.9.1).
    # Every other refusal comes from `llamacpp.start`, after the save, since a
    # server already on the port is one the picker should now be pointed at.
    if config.port == window_port(app):
        app.server_error = llamacpp.port_problem(config.port, window=config.port)
        return False
    llamacpp.save_config(config)
    app.server_status = State.STARTING
    ok = False
    try:
        await asyncio.to_thread(llamacpp.start, config)
        await asyncio.to_thread(llamacpp.wait_ready)
        ok = True
    except (providers.ProviderUnavailable, OSError) as exc:
        app.server_error = str(exc)
        await asyncio.to_thread(llamacpp.stop)
    finally:
        app.server_status = ""
    await list_models(app, llamacpp.PROVIDER.kind)
    return ok


def window_port(app: App) -> int | None:
    """The port this window serves on, off `App.url`; ``None`` when unknown."""
    from urllib.parse import urlsplit

    try:
        return urlsplit(app.url).port if app.url else None
    except ValueError:
        return None


async def stop_server(app: App) -> None:
    """Stop the server this window started, and list again so the picker says so."""
    from portia.agent.providers import llamacpp

    await asyncio.to_thread(llamacpp.stop)
    await list_models(app, llamacpp.PROVIDER.kind)


async def preflight(app: App, kind: str, model: str) -> Any:
    """What can be measured about ``model`` before a message goes to it.

    `Provider.preflight` with the length of everything portia composes for the
    model (`session.prompt_chars`), both off the loop: composing the brief
    reads the catalog, and a local provider loads the model, which is seconds
    the first time (§4.5, §4.6). Returns the provider's `Preflight`; the
    caller draws the refusal.
    """
    from portia.agent import providers, session

    provider = providers.get(kind)
    return await asyncio.to_thread(
        partial(
            provider.preflight,
            model,
            prompt_chars=lambda: session.prompt_chars(app.portia_dir, kind),
        )
    )


def needs_connection(app: App) -> bool:
    """Whether the project names a warehouse the window has not reached yet."""
    return bool(app.connection) and app.connection_status == State.NOT_CONNECTED


async def connect_project(app: App, secret: str | None = None) -> bool:
    """Open the warehouse session, off the loop. Says how it went in `connection_status`.

    ``secret`` is what the form was given this session, for a connection that
    signs in with a password or a token; it is handed to the pool and cleared
    from `App` here, whichever way the attempt goes.
    """
    from portia import connectors

    if not app.connection or app.connection_status == State.CONNECTING:
        return app.connected
    app.connection_status = State.CONNECTING
    app.connect_secret = ""
    try:
        await asyncio.to_thread(partial(connectors.activate, app.portia_dir, secret=secret))
    except Exception as exc:  # noqa: BLE001 — the pane says why, whatever the driver said
        app.connection_status = f"failed: {_plain(exc)}"
        feedback.remember(exc, "connecting")
        return False
    app.connection_status = State.CONNECTED
    return True


def _plain(exc: Exception) -> str:
    """The driver's sentence, without its code prefix, and with the one case we know named.

    ``390190 (08001): … SAML Identity Provider account parameter`` is what an
    account with no identity provider says to browser SSO; the sentence is
    true and the fix is not in it.
    """
    text = str(exc)
    if "SAML Identity Provider" in text:
        return (
            "this account has no single sign-on identity provider, so browser sign-in "
            "cannot work here. Sign in with a password or an access token instead."
        )
    if "default credentials were not found" in text.lower() or "DefaultCredentialsError" in text:
        # What `google.auth.default()` says on a machine where nobody has run
        # the SDK's login; its sentence links a page, and the fix is one command.
        return (
            "no Google credentials on this machine. Run `gcloud auth application-default "
            "login`, or sign in with a service account key file or an access token instead."
        )
    return _DRIVER_CODE.sub("", text).strip()


#: The connector prefixes its messages with ``390190 (08001): `` — a code and a
#: SQLSTATE, both of which mean nothing to the person reading the sentence.
_DRIVER_CODE = re.compile(r"^\d{4,7} \([0-9A-Z]{5}\): ")


def save_connection(fields: dict[str, str], *, app: App, agent_writes: bool = False) -> str:
    """Save a connection in the user's registry and make this project run on it.

    The registry refuses a missing required field with a sentence naming it,
    and nothing is written to the project until the registry accepted it.
    ``fields`` is the form as the dialog holds it: ``name``, ``kind``, ``auth``
    and the provider's own keys, which the provider says (`CONNECTORS.md`
    §2.4). ``agent_writes`` is the hand-off (`CONNECTOR.md` §2.7.1).
    """
    from portia.connectors import registry

    kind = (fields.get("kind") or registry.DEFAULT_KIND).strip()
    provider = registry.PROVIDERS.get(kind)
    if provider is None:
        raise ValueError(f"no connector for {kind!r}; portia knows {', '.join(registry.PROVIDERS)}")
    connection = registry.Connection(
        name=(fields.get("name") or "").strip(),
        kind=kind,
        auth=(fields.get("auth") or provider.default_auth).strip(),
        **{key: (fields.get(key) or "").strip() for key in provider.keys},
    )
    registry.save(connection)
    catalog.set_connection(connection.name, agent_writes=agent_writes, portia_dir=app.portia_dir)
    refresh_catalog(app)
    forget_scope_picker(app)
    install_backend(app)
    return connection.name


def clear_connection(app: App) -> None:
    """Make the project local again. The scope stays; forgetting it is a separate act."""
    catalog.set_connection(None, portia_dir=app.portia_dir)
    refresh_catalog(app)
    forget_scope_picker(app)
    install_backend(app)


def connection_suggestions() -> list[dict[str, str]]:
    """Every connection the form can fill from: portia's own, then each vendor's own files."""
    from portia.connectors import registry

    out = [_form_of(c) for c in registry.load().values()]
    known = {c["name"] for c in out}
    out += [_form_of(c) for c in registry.suggestions() if c.name not in known]
    return out


def _form_of(connection) -> dict[str, str]:
    """A connection as the dialog's form holds it: every key the provider has, blank when unset."""
    form = {"name": connection.name, "kind": connection.kind, "auth": connection.auth}
    form |= {key: str(connection.fields.get(key) or "") for key in connection.provider.keys}
    form["summary"] = connection.provider.summary(connection)
    return form


def set_agent_writes(on: bool, app: App) -> None:
    """The hand-off, durably and on the held backend (`CONNECTOR.md` §2.7.1).

    The flag rides the `Backend` value, so the installed one is rebuilt —
    keeping its session, which `install_backend` would drop.
    """
    catalog.set_connection(app.connection, agent_writes=on, portia_dir=app.portia_dir)
    refresh_catalog(app)
    from portia import connectors

    connectors.set_agent_writes(app.agent_writes)


async def browse_remote(app: App, at: str, on_start: Callable[[], Any] | None = None) -> list:
    """List what is at ``at`` — databases, a database's schemas, a schema's tables.

    Cached per place on `App`: a browse is a query on the warehouse, and the
    picker redraws far more often than a schema changes. ``on_start`` is called
    once the place is marked as loading and before the query goes, which is how
    the row being listed gets its spinner; a cached place never calls it.
    """
    if at in app.scope_listing:
        return app.scope_listing[at]
    parts = [p for p in at.split(".") if p]

    def ask() -> list:
        con = connect()
        if not parts:
            return con.databases()
        if len(parts) == 1:
            return con.schemas(parts[0])
        return con.tables(parts[0], parts[1])

    app.scope_loading = at
    if on_start is not None:
        on_start()
    try:
        listing = await asyncio.to_thread(ask)
    finally:
        app.scope_loading = None
    app.scope_listing[at] = listing
    return listing


async def list_under(app: App, at: str, on_start: Callable[[], Any] | None = None) -> None:
    """List ``at`` and every database or schema below it that nothing has listed.

    What ticking a schema or a database needs first (`picktree.unlisted`): a
    tick is a statement about tables, and an unlisted schema has none to make
    it about. One place at a time through `browse_remote`, so every answer
    lands in the cache the tree is drawn from. Metadata only: a whole database
    is one listing per schema and no scan.
    """
    parts = [p for p in at.split(".") if p]
    if len(parts) >= 3:
        return
    below = await browse_remote(app, at, on_start)
    if len(parts) < 2:
        for name in below:
            await list_under(app, f"{at}.{name}".strip("."), on_start)


@dataclass(frozen=True)
class Indexed:
    """What one indexing run did, item by item.

    ``names`` are the catalog names that finished and ``items`` what each was
    asked as (a path, a qualified table), in the same order. Two lists because
    a failure in the middle means the finished ones are **not a prefix** of
    what was asked: the callers used to slice ``asked[: len(names)]``, which is
    right for a Stop and wrong the moment one table fails and the next does not.
    ``failed`` is the labels that raised; their sentences are on
    `App.indexing_failed`, where the panes draw them.
    """

    names: list[str]
    items: list
    failed: list[str]


async def _hops(
    app: App,
    items: list,
    work: Callable[[Any], str],
    *,
    label: Callable[[Any], str],
    where: str,
    on_progress=None,
    stop: cancel.Scope | None = None,
    reload_each: bool = False,
) -> Indexed:
    """One item per hop off the loop: the shape `index`, `scope` and `profile_tables` share.

    **One item failing is that item's failure, not the run's** *(2026-09-21,
    the first drive on a real Snowflake account with thirty tables)*. The
    eleventh table's profile raised in the warehouse, the exception left the
    loop, and everything after it was skipped: nineteen tables never tried, the
    catalog never reloaded so the ten that had finished drew as *metadata only*
    while their profiles sat on disk, no toast, and a spinner in the Indexing
    tab that nothing was left to take down. So a failure is remembered for a
    report, kept as a sentence on `App.indexing_failed` under the item's label,
    and the loop goes on. A later success on the same label clears it.

    `Cancelled` still ends the run, and is still not a failure. The catalog is
    reloaded in a ``finally``: what reached the disk is what the panes must
    draw, whatever ended the loop.

    ``reload_each`` reloads it after every hop as well, for the one caller
    whose hop is a scan on a warehouse: thirty of those is most of an hour, and
    a left pane opened in the middle said *metadata only* about tables whose
    profile was on disk. Off elsewhere, because a reload reads every entry and a
    local project indexing two hundred files would spend longer reloading than
    profiling.
    """
    names: list[str] = []
    finished: list = []
    failed: list[str] = []
    try:
        for done, item in enumerate(items):
            if stop is not None and stop.cancelled:
                break
            if on_progress is not None:
                on_progress(done, len(items), label(item))
            try:
                names.append(await asyncio.to_thread(work, item))
            except cancel.Cancelled:
                break
            except Exception as exc:  # noqa: BLE001 — one item's failure is a sentence on its row
                feedback.remember(exc, where)
                failed.append(label(item))
                app.indexing_failed = {**app.indexing_failed, label(item): _sentence(exc)}
                continue
            finished.append(item)
            app.indexing_failed = {k: v for k, v in app.indexing_failed.items() if k != label(item)}
            if reload_each:
                refresh_catalog(app)
    finally:
        refresh_catalog(app)
    # The graph is the catalog restated, so a run that stopped or half failed
    # still syncs what it did manage rather than leaving the two disagreeing.
    await asyncio.to_thread(sync_knowledge, app)
    return Indexed(names=names, items=finished, failed=failed)


def _sentence(exc: BaseException) -> str:
    """An error as one line for a row: its kind, and the first line it said.

    The first line only. DuckDB quotes the whole statement back under it, and a
    profile's statement is a page (`core/feedback` cuts the same thing).
    """
    said = str(exc).strip().splitlines()
    return f"{type(exc).__name__}: {said[0]}" if said else type(exc).__name__


async def scope(
    app: App, names: list[str], *, on_progress=None, stop: cancel.Scope | None = None
) -> Indexed:
    """Bring tables into scope, one hop each, as metadata (`catalog.scope_table`).

    The same shape as `index`: one table per hop so the screen can say which,
    what was done stays done when Stop lands, and what made it is returned so
    the caller reports what happened rather than what was asked (`_hops`).
    """
    return await _hops(
        app,
        names,
        lambda qualified: _scope_one(qualified, app.portia_dir, stop),
        label=str,
        where="scoping a table",
        on_progress=on_progress,
        stop=stop,
    )


def _scope_one(qualified: str, portia_dir: str, stop: cancel.Scope | None) -> str:
    with cancel.scope(stop):
        return catalog.scope_table(qualified, connect(), portia_dir=portia_dir).stem


async def profile_remote(app: App, name: str) -> dict:
    """The opt-in scan of one warehouse table — scoped, or built there — off the loop.

    `docs/CONNECTOR.md` §2.6 for a source; the same call takes a model's name
    since 2026-09-07 (`catalog.profile_remote`). The graph is refreshed after,
    because a Column node's facts come off the entry this just filled in.
    """
    profile = await asyncio.to_thread(
        lambda: catalog.profile_remote(name, connect(), portia_dir=app.portia_dir)
    )
    refresh_catalog(app)
    await asyncio.to_thread(sync_knowledge, app)
    return profile


async def profile_tables(
    app: App, names: list[str], *, on_progress=None, stop: cancel.Scope | None = None
) -> Indexed:
    """Profile several warehouse tables, one hop each: `scope`'s shape over `profile_remote`.

    The add-data screen asks for this when its profile switch is on, and the
    Indexing tab when a metadata-only table is ticked (2026-09-07). Both need
    what `index` needs: a sentence per table so the window can say which one is
    on the meter, and a Stop that lands between tables *and* interrupts the scan
    in flight. What was profiled stays profiled, a table whose scan the
    warehouse refused is skipped with its reason kept, and what made it is
    returned so the caller reports what happened, not what was asked (`_hops`).
    """
    return await _hops(
        app,
        names,
        lambda name: _profile_one(name, app.portia_dir, stop),
        label=str,
        where="profiling a table",
        on_progress=on_progress,
        stop=stop,
        reload_each=True,
    )


def _profile_one(name: str, portia_dir: str, stop: cancel.Scope | None) -> str:
    # The scope is installed inside the worker, as `_index_one` installs its:
    # `connect()` registers the session with the ambient scope, and that is the
    # handle Stop needs to reach a scan already running.
    with cancel.scope(stop):
        catalog.profile_remote(name, connect(), portia_dir=portia_dir)
    return name


def written_tables(app: App) -> dict[str, str]:
    """``{qualified table: model name}`` for what this project built in the warehouse."""
    return catalog.written_tables(app.portia_dir)


def read_model(entry: dict, app: App):
    """A built table's rows for the preview: the written table, when the session is open.

    ``None`` locally and when nothing was written — reaching the table would
    mean running the spec, and a preview must never be the thing that does.
    """
    written = entry.get("table")
    if not written or not app.connected:
        return None
    return source_table({"table": written}, connect(), name=str(entry.get("model") or written))


def read_source(entry: dict, app: App):
    """A source's rows for the preview — a file, or a warehouse table if connected.

    ``None`` when there is nothing to show: the file has moved, or the session
    is not open. A preview must never be the thing that opens a browser.
    """
    if entry.get(catalog.REMOTE):
        if not app.connected:
            return None
        return source_table(catalog.source_ref_of(entry), connect(), name=str(entry["source"]))
    path = app.root / str(entry.get("source", ""))
    return read_table(path) if path.exists() else None


def warehouse_tree(app: App) -> tuple[tree.Node, ...]:
    """The scoped tables and the built ones, as the pinned section draws them (`tree.warehouse`)."""
    return tree.warehouse(
        app.sources,
        catalog.load_models(app.portia_dir),
        table_kind=State.SOURCE,
        model_kind=State.MODEL,
    )


#: Ask the OS for a folder, or for files. macOS only, and deliberately so: the
#: app is local-first (`TECH_STACK.md` — `pip install` → localhost), so the
#: machine running the server is the machine with the Finder. Elsewhere the path
#: field is the way in, which is why it is still there.
_CHOOSE_FOLDER = 'POSIX path of (choose folder with prompt "Choose a project folder")'

#: The file chooser is long enough to be worth reading as AppleScript rather than
#: as a Python string, so it lives beside the CSS and the canvas behaviour.
_CHOOSE_FILES = Path(__file__).parent / "assets" / "choose_files.applescript"


def can_browse() -> bool:
    return sys.platform == "darwin" and shutil.which("osascript") is not None


async def browse_for_folder() -> Path | None:
    """Open the native folder chooser. ``None`` if it isn't available or was cancelled."""
    if not can_browse():
        return None
    return await asyncio.to_thread(_choose_folder)


def _choose_folder() -> Path | None:
    chosen = _osascript(_CHOOSE_FOLDER)
    return Path(chosen[0]) if chosen else None


async def browse_for_files() -> list[Path]:
    """Open the native file chooser. Empty if it isn't available or was cancelled."""
    if not can_browse():
        return []
    return await asyncio.to_thread(_choose_files)


def _choose_files() -> list[Path]:
    return [Path(line) for line in _osascript(_CHOOSE_FILES.read_text(encoding="utf-8"))]


def _osascript(script: str) -> list[str]:
    """Run a chooser and return its lines, or nothing at all.

    A cancelled dialog exits non-zero with "User canceled." on stderr — not an
    error worth surfacing, just an answer of "no".
    """
    try:
        done = subprocess.run(
            ["osascript", "-e", script], capture_output=True, text=True, check=False
        )
    except OSError:
        return []
    if done.returncode != 0:
        return []
    return [line for line in done.stdout.strip().splitlines() if line.strip()]


def recents() -> list[tuple[Path, str]]:
    """Recently opened project directories, newest first, with when they were opened."""
    return prefs.recents()


def remember(root: Path) -> None:
    prefs.remember_opened(root)


def remembered_view(root: Path) -> frozenset[str] | None:
    """Which tables this project's canvas was last narrowed to.

    ``None`` — no entry — means nothing was ever chosen, which draws the whole
    project and is what an unknown project gets. A stored **list** is an explicit
    choice, and an empty one means everything was turned off; the two are
    different states and the file distinguishes them by absence rather than by
    emptiness (`graph._visible`).
    """
    names = prefs.project(root).get("view")
    return frozenset(str(n) for n in names) if isinstance(names, list) else None


def remember_view(root: Path, names: frozenset[str] | None) -> None:
    prefs.update_project(root, {"view": None if names is None else sorted(names)})


def remembered_layout(root: Path) -> dict[str, tuple[int, int]]:
    """Where this project's cards were last dragged to; empty when nothing was moved."""
    saved = prefs.project(root).get("layout")
    if not isinstance(saved, dict):
        return {}
    out = {}
    for name, delta in saved.items():
        try:
            dx, dy = (int(v) for v in delta)
        except (TypeError, ValueError):
            continue
        out[str(name)] = (dx, dy)
    return out


def remember_layout(root: Path, offsets: dict[str, tuple[int, int]]) -> None:
    """Write the arrangement back. An empty one removes the entry rather than storing it."""
    layout = {name: list(delta) for name, delta in sorted(offsets.items())}
    prefs.update_project(root, {"layout": layout or None})


def has_context(app: App) -> bool:
    return bool(app.project_context)


def set_context(text: str, app: App) -> None:
    catalog.init_project(text.strip(), portia_dir=app.portia_dir)
    refresh_catalog(app)


def refresh_catalog(app: App) -> None:
    app.catalog = catalog.load_catalog(app.portia_dir)
    # A schema a build just wrote into is re-listed the next time the picker
    # looks: the listing is cached per place (`browse_remote`), and a table
    # created after the cache was filled would otherwise never appear there.
    for entry in catalog.load_models(app.portia_dir).values():
        parts = str(entry.get("table") or "").split(".")
        if len(parts) == 3:
            app.scope_listing.pop(parts[0], None)
            app.scope_listing.pop(f"{parts[0]}.{parts[1]}", None)


#: The three history folders under `.portia/`. Their files count by **name
#: only** in `artifact_stamp`: the log of the exchange doing the asking grows on
#: every event, and stamping its size would make the stamp move exactly as often
#: as the refreshes it exists to prevent. A *new* log is still a new row in the
#: left pane, and the name alone says that.
_HISTORY_DIRS = frozenset({"chats", "indexing", "runs"})


def artifact_stamp(app: App) -> tuple:
    """Everything the artifact panes draw off disk, as one comparable value.

    `exchange._sync_artifacts` re-reads the project after every tool result, and
    it used to redraw three panes whether or not that result had written
    anything — most of the copilot's calls are questions, not writes, so a
    twenty-call exchange flashed the whole window twenty times to show the same
    pixels. This is the cheap answer to *did anything change*: each file those
    panes are drawn from, as ``(path, mtime, size)``, compared before any pane
    is refreshed.

    The data itself is deliberately not scanned. The copilot has no filesystem,
    so nothing it does can add a data file — and walking a real extract's
    ``data/`` to decide whether to redraw a pane would cost more than the
    redraw. The same globs the panes render from are what is stamped here, so
    the stamp cannot say *unchanged* about a file the panes would draw.
    """
    stamps: list[tuple[str, int, int]] = []

    def add(path: Path) -> None:
        try:
            stat = path.stat()
        except OSError:
            return
        stamps.append((path.as_posix(), stat.st_mtime_ns, stat.st_size))

    for path in (*specs_in(app), *models_in(app), *outputs_in(app), *runs_in(app)):
        add(path)
    for folder in (app.root / figures.FIGURES_DIR, app.root / findings.FINDINGS_DIR):
        if folder.is_dir():
            for path in sorted(folder.rglob("*")):
                if path.is_file():
                    add(path)
    portia_dir = app.catalog_dir
    if portia_dir.is_dir():
        for path in sorted(portia_dir.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(portia_dir)
            if rel.name == drawn.WINDOW_FILE:
                # This window's own announcement. Stamping it would have the
                # window redraw because it said it was open.
                continue
            if rel.parts and rel.parts[0] in _HISTORY_DIRS:
                stamps.append((path.as_posix(), 0, 0))
            else:
                add(path)
    return tuple(sorted(stamps))


# --- the project's data folder ----------------------------------------------


def set_data_dir(rel: str, app: App) -> None:
    """Record which folder in the repo holds this project's data, durably.

    A project setting rather than a window setting: it decides what the left pane
    draws as data and where an import lands by default, and both of those have to
    survive the process. `catalog.set_data_dir` puts it in ``project.yaml``, where
    it is read in a diff beside the brief it belongs with.

    **The project root is stored as ``"."``, never as ``""``.** They are the same
    *scope* — every path in the repo, which `tree` collapses back to "anywhere" —
    and different *answers*: ``""`` is "nobody has said". Normalising here rather
    than at the button is what keeps that distinction one fact; the add-data
    screen reads the setting back to decide whether to draw the picker, and an
    empty string sent it round the loop again.
    """
    catalog.set_data_dir(rel or ".", portia_dir=app.portia_dir)
    refresh_catalog(app)


def folder_choices(app: App, at: str) -> tuple[tree.Choice, ...]:
    """The sub-folders of ``at`` that hold readable data, for the in-page picker.

    Rooted at the project, always. Choosing the data folder is choosing a *scope
    inside the repo* — an outside folder is not a thing this control can express,
    which is the same rule `index` enforces and one fewer error to write.
    """
    return tree.choices(app.root, at, readable_suffixes())


def data_files_in(app: App, rel: str) -> list[Path]:
    """Every readable data file under a repo-relative folder, at any depth.

    What the add-data screen ticks and profiles. Recursive, because a data folder
    with a year per sub-folder is the ordinary shape and asking someone to pick
    each one is not a scope, it is a chore.
    """
    return list(tree.data_files(app.root / rel if rel else app.root, readable_suffixes()))


def crumbs(app: App, at: str) -> list[tuple[str, str]]:
    """The picker's path trail, with the project's own directory name at the root."""
    trail = tree.crumbs(at)
    root_name = app.root.name or str(app.root)
    return [(rel, name or root_name) for rel, name in trail]


def remove_source(name: str, app: App) -> None:
    """Un-index a source. The file stays on disk — see `catalog.remove_source`."""
    catalog.remove_source(name, portia_dir=app.portia_dir)
    refresh_catalog(app)


def set_interpretation(
    source: str, *, summary: str | None, roles: dict[str, str], app: App
) -> None:
    """Record the human's read of a source — the same write the copilot makes.

    ``catalog.set_interpretation`` is judgment-only by construction: it writes the
    prose and the roles and never touches a measured fact. So a correction typed
    here and one the copilot proposes land in the same place, in the same shape,
    and a re-index preserves both (`catalog` → the update rule).
    """
    catalog.set_interpretation(source, summary=summary, roles=roles, portia_dir=app.portia_dir)
    refresh_catalog(app)


# --- adding data ------------------------------------------------------------


def plan_import(target: str, destination: str, app: App) -> list[tuple[Path, Path]]:
    """What importing ``target`` into ``destination`` would copy, and to where.

    The deliberate half of `docs/PIPELINE.md` §2.7. Nothing is written: this is
    what the confirmation shows, so that what you agree to is the real thing
    rather than a description of one, and so a name collision surfaces before the
    first byte moves rather than halfway through a batch.

    ``plan_copy`` is `cli.import_data.plan` — the same function the terminal
    calls, which is why the two cannot disagree about where a file is going.
    Raises ``ValueError`` for anything the operator can fix: nothing matched, the
    destination is outside the project, a name is already taken.
    """
    sources = find_data_files(target)
    return plan_copy(sources, destination_in(destination, app), app.root)


def destination_in(raw: str, app: App) -> Path:
    """A destination the operator typed, as a path inside the project.

    Relative to the project root, always — an absolute path that happens to point
    inside is accepted, and one that points outside is refused by ``plan``. Data
    lives in the repo (`PIPELINE.md` §2.7) and the destination field is not the
    place to make an exception to that.

    **Empty means the project root** (2026-08-06), where it used to mean
    ``data/``. See `state.App.import_dir` for the reversal and why: a project
    whose data *is* its root is an ordinary shape, and inventing a folder for it
    is the surprising answer rather than the safe one.
    """
    typed = Path((raw or "").strip() or ".")
    return typed if typed.is_absolute() else app.root / typed


async def import_files(pairs: list[tuple[Path, Path]], app: App) -> list[Path]:
    """Do what the plan said, and nothing it didn't.

    **A copy, never a move.** The original is not touched, not deleted, not
    rewritten — a tool that relocates someone's data is not a data-harmonization
    concern (`CLAUDE.md`), and the one time it matters is the time you find out
    afterwards.
    """
    return await asyncio.to_thread(_copy_all, pairs)


def _copy_all(pairs: list[tuple[Path, Path]]) -> list[Path]:
    copied = []
    for src, dst in pairs:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        copied.append(dst)
    return copied


async def index(
    paths: list[Path], app: App, *, on_progress=None, stop: cancel.Scope | None = None
) -> Indexed:
    """Profile each file into the catalog. Deterministic, free, always happens.

    The *interpretation* half is a model turn and is deliberately not here — the
    UI has to show them as two things because one of them costs money.

    **One file per hop, so the screen can say which one.** This used to hand the
    whole list to a single thread and come back when it was done, which for twenty
    real extracts is a minute of a window that says nothing. ``on_progress(done,
    total, name)`` is called before each file, on the event loop, so a caller can
    redraw between them.

    ``stop`` makes it interruptible, and the per-file hop is what makes that
    cheap: the loop checks between files, so a press always stops the *next* one,
    and the scope interrupts the profile of the current one so a 143 MB CSV is
    not something you have to sit through after pressing Stop.

    **What was already profiled stays profiled.** Each file is written to the
    catalog as it completes, so stopping halfway leaves a project that knows
    about the first four of twenty sources — which is true, and is what the left
    pane should show. The names are returned so the caller can say how far it
    got rather than treating a stop as having done nothing.
    """
    return await _hops(
        app,
        paths,
        lambda path: _index_one(path, app.portia_dir, stop),
        label=lambda path: path.stem,
        where="indexing a file",
        on_progress=on_progress,
        stop=stop,
    )


def _index_one(path: Path, portia_dir: str, stop: cancel.Scope | None = None) -> str:
    """One source into the catalog. Nothing is copied — see `catalog.index_source`.

    There is no shared connection to thread through any more: with the store
    retired, indexing profiles the file where it lies and `catalog.source_ref`
    records the path relative to the project (`docs/PIPELINE.md` §2.7).

    The scope is installed *here*, inside the worker, rather than around the
    loop: `catalog.index_source` opens its own connection through
    `core/io.connect`, which registers it, and that is the handle Stop needs to
    reach a profile already running.
    """
    with cancel.scope(stop):
        catalog.index_source(path, portia_dir=portia_dir)
    return path.stem


# --- specs, runs, outputs ---------------------------------------------------


def readable_suffixes() -> tuple[str, ...]:
    """The formats `core.io` registers a reader for.

    Read off the loader rather than written down anywhere in `ui/`: the left tree
    shows un-indexed data files, and a hard-coded format list there would have
    stopped showing Parquet the day it landed.
    """
    return supported_suffixes()


def known_files(app: App) -> dict[str, tuple[str, str]]:
    """Every file portia knows about: repo-relative path → ``(kind, ident)``.

    This is the left pane's **filter** and its **bridge back to a selection**. The
    tree is a real directory walk (`ui/tree.py`), so a row arrives as a path —
    but the panes address a source by its catalog name and a saved run by its
    filename, and this is what turns one into the other.

    Built from the same calls the sections used to be built from, so the tree
    cannot contain a different set of specs than `cli.build` does.
    """
    known: dict[str, tuple[str, str]] = {}
    for path in runs_in(app):
        known[_rel(path, app)] = (State.RUN, path.name)
    for path in outputs_in(app):
        known[_rel(path, app)] = (State.OUTPUT, path.name)
    for path in models_in(app):
        known[_rel(path, app)] = (State.MODEL, _rel(path, app))
    for path in specs_in(app):
        known[_rel(path, app)] = (State.SPEC, path.name)
    # Sources last, and deliberately: a data file that a run also wrote into
    # `out/` is a source first, because that is the entry with a profile and an
    # interpretation behind it.
    for name, entry in app.sources.items():
        recorded = entry.get("source")
        # A warehouse table is not in the tree; the pinned section draws it.
        if recorded and not entry.get(catalog.REMOTE):
            known[Path(recorded).as_posix()] = (State.SOURCE, name)
    return known


def _rel(path: Path, app: App) -> str:
    """A path as the project sees it. Outside the root it stays as it is."""
    try:
        return path.relative_to(app.root).as_posix()
    except ValueError:
        return path.as_posix()


def project_tree(app: App) -> tuple[tree.Node, ...]:
    """The project directory, filtered to what portia reads. The left pane.

    ``data_dir`` scopes the *readable* half of that filter and nothing else: an
    un-indexed CSV is drawn if it is under the project's data folder, while every
    artifact portia wrote — a spec, a model, an output, a saved run — is drawn
    wherever it lives. See `tree` for why the two halves are scoped differently.
    """
    # A warehouse project draws no un-indexed *file* as data: its data is the
    # scope, in the pinned section (`docs/CONNECTOR.md` §2.2, no mixing). The
    # artifacts portia wrote are still drawn wherever they live.
    readable = () if app.connection else readable_suffixes()
    return tree.build(app.root, known_files(app), readable, app.data_dir or None)


def specs_in(app: App) -> list[Path]:
    """Every spec in the project, in the order the engine finds them.

    Goes through `spec.discover_specs` rather than globbing, for the reason this
    whole module exists: a layered project keeps its specs in subdirectories, and
    a left panel that globbed one level would show a different set of specs than
    the engine builds. It also means the project's duplicate-name rule is enforced
    in one place, and the app inherits the error instead of quietly listing two
    specs that cannot both exist.
    """
    return sorted(app.root / path for path in spec_module.discover_specs(app.root).values())


def project_docs(app: App) -> dict[str, dict]:
    """Every spec in the project, loaded, as ``model name -> doc``.

    What the project graph is drawn from. The name is the spec's filename because
    one spec produces one table, so this mapping is also what resolves a
    cross-spec reference — the same `spec.discover_specs` the engine builds from,
    which is the point: the window must not have a different idea of what the
    project contains than `cli.build` does.
    """
    docs = {}
    for name, path in spec_module.discover_specs(app.root).items():
        try:
            docs[name] = spec_module.load_spec(app.root / path) or {}
        except (OSError, ValueError):
            # One unreadable spec is that spec's problem to report; it is not a
            # reason for the whole graph to refuse to draw.
            continue
    return docs


def journal_for(app: App, model: str) -> tuple[list[dict], list[dict]]:
    """A model's journal: what was recorded for it, and what is known about its inputs.

    The one call in `ui/` that reaches the journal, as everything else engine-ish
    goes through this module. Reads YAML and runs nothing, so it is cheap enough
    for the render path the report region sits on.

    Two lists, not one *(2026-09-04)*. The first is `findings.for_spec` — records
    that named this model. The second is every finding about a table upstream of
    it, which the spec declares and `graph.upstream_ids` walks, so a finding about
    ``orders`` is under every model built from ``orders`` whether or not the agent
    named one. The journal was empty in every real session; the second list is
    the half of it that needs nobody to remember anything.
    """
    docs = project_docs(app)
    upstream = graph.upstream_ids(docs, model) - {model} if model in docs else set()
    journal = findings.for_spec(model, root=app.root)
    around = findings.around_model(model, sorted(upstream), root=app.root)
    # Each record carries which of its tables moved since it was measured
    # (`findings.stale_marks`): a read-time comparison the pane draws as a fact
    # beside the finding, never as a reason to drop it (`KNOWLEDGE_GRAPH.md`
    # §4.5). One catalog read for both lists.
    stale = findings.stale_marks(journal + around, root=app.root, portia_dir=app.portia_dir)
    mark = lambda f: {**f, "stale": stale.get(f.get("path", ""), [])}  # noqa: E731
    return [mark(f) for f in journal], [mark(f) for f in around]


def spec_path_for(app: App, model: str) -> Path | None:
    """Where the spec that produces ``model`` lives, or None if nothing does."""
    found = spec_module.discover_specs(app.root).get(model)
    return app.root / found if found else None


def models_in(app: App) -> list[Path]:
    """The compiled ``.sql`` files — the pipeline, which is the deliverable.

    Not an output like ``out/*.csv``: a run's CSV is a result, and these are the
    thing you hand someone (`docs/PIPELINE.md` §2.2).
    """
    models = app.root / pipeline.MODELS_DIR
    return sorted(models.rglob("*.sql")) if models.is_dir() else []


def stale_models(app: App) -> list[str]:
    """Models whose ``.sql`` no longer matches the spec that produced it.

    Cheap — it reads a header, it runs nothing — so a panel may ask on any render.
    """
    try:
        return pipeline.stale_models(app.root)
    except (OSError, ValueError):
        # A malformed spec is the spec pane's problem to report, not a reason for
        # the whole left panel to fail to draw.
        return []


async def build(
    app: App,
    *,
    only: str | Collection[str] | None = None,
    on_progress: Callable[[pipeline.BuildProgress], None] | None = None,
    stop: cancel.Scope | None = None,
) -> list[pipeline.BuiltModel]:
    """Run specs and write their ``.sql`` — the app's half of ``cli.build``.

    ``only`` scopes it to one model and everything it reads, which is what the
    Run button does; without it this is the whole project, which is Build. One
    call for both, because they are one mechanism at two scopes — and because two
    code paths for "execute the pipeline" is exactly how the window and the
    terminal end up disagreeing about a number.

    **``on_progress`` is called on the event loop, not on the builder.** The
    build is on a worker, so `pipeline` calls back from that thread — and a
    callback that refreshed a pane from there would be mutating NiceGUI's element
    tree from outside the loop that owns it. `call_soon_threadsafe` is the one
    hop that fixes it, and it lives here because this module is where the thread
    is created and therefore the only place that knows one was.
    """
    hop = None
    if on_progress is not None:
        loop = asyncio.get_running_loop()

        def hop(progress: pipeline.BuildProgress) -> None:
            loop.call_soon_threadsafe(on_progress, progress)

    return await asyncio.to_thread(
        partial(pipeline.build_project, app.root, only=only, on_progress=hop, stop=stop)
    )


def count_steps(path: Path) -> int | None:
    """How many steps a spec records, or ``None`` if it can't be read."""
    try:
        return len(spec_module.load_spec(path).get("steps") or [])
    except (OSError, ValueError, AttributeError):
        return None


def outputs_in(app: App) -> list[Path]:
    out = app.root / OUT_DIR
    return sorted(out.glob("*.csv")) if out.is_dir() else []


def select_spec(path: Path | None, app: App) -> None:
    """Load a spec into the workflow pane. A run's results belong to one spec.

    ``built`` goes with them. It is what *Write outputs* writes, and a build the
    window is no longer showing is not a thing that button should still be able
    to save — the honest state of a freshly-opened spec is that nothing has run.
    """
    app.spec_path = path
    app.spec = spec_module.load_spec(path) if path and path.exists() else None
    app.results = None
    app.built = []
    app.run_error = None
    app.selected_step = None


async def execute(
    app: App,
    *,
    only: str | Collection[str] | None = None,
    on_progress: Callable[[pipeline.BuildProgress], None] | None = None,
    stop: cancel.Scope | None = None,
) -> list[pipeline.BuiltModel]:
    """Build models and **pick up what came out** — the state both buttons leave.

    `build` is the engine call; this is the app's memory of it, and Run and Build
    share it for the same reason they share `build_project`: they are one
    mechanism at two scopes. Build used to throw its results away, so pressing it
    compiled the whole project and still left *Write outputs* and *Save report*
    greyed out — the project had been run and the window had no idea.

    ``results`` is the **open spec's** steps, because the report half and both
    save buttons are about the spec you are looking at; ``built`` is everything
    that ran, so the header can name the models it also had to build. A build that
    didn't touch the open spec (or a window with no spec open) leaves ``results``
    empty rather than borrowing another model's — the saves stay disabled, which
    is the honest state.

    A failure lands in ``run_error`` rather than a toast: the run report pane shows
    it until the next run, and a stack trace that vanishes after four seconds is
    not something you can read.

    **A stop is not a failure and does not land there.** `Cancelled` is the answer
    to a button the human pressed, so it leaves `run_error` clear and comes back
    with nothing; the caller says so in a notification instead. Reporting it in
    the pane would leave the app apologising, in red, for having done as it was
    told — and would sit there until the next run.
    """
    app.run_error = None
    app.run_problem = None
    app.built = []
    try:
        built = await build(app, only=only, on_progress=on_progress, stop=stop)
    except cancel.Cancelled:
        app.results = None
        return []
    except Exception as exc:  # noqa: BLE001 — shown to the operator, not swallowed
        app.results = None
        app.run_error = f"{type(exc).__name__}: {exc}"
        app.run_problem = feedback.remember(exc, "run")
        return []
    app.built = built
    open_model = app.spec_path.stem if app.spec_path else None
    target = next((m for m in built if m.name == open_model), None)
    app.results = target.results if target else None
    if app.results:
        # Off the loop, before the pane that asks for them is redrawn
        # (`warm_shapes`). A stop or a failure has no results to warm.
        await asyncio.to_thread(warm_shapes, app.results)
    return built


async def run_scope(
    app: App,
    *,
    scope: Collection[str] | None = None,
    on_progress: Callable[[pipeline.BuildProgress], None] | None = None,
    stop: cancel.Scope | None = None,
) -> list[pipeline.BuiltModel]:
    """Run the tables in ``scope`` **and everything they read**, then write their ``.sql``.

    **One action at whatever scope the canvas is showing** *(2026-08-15)*. This
    was two buttons — Run for the open spec, Build for the project — and they were
    always one mechanism at two scopes (`build_project`'s ``only``). Once the
    canvas can be narrowed to a set of tables, the scope is already on screen and
    picking it twice is what made them look like different verbs: an empty
    ``scope`` is the whole project, which is what Build was.

    Both halves of "and everything they read" are decisions, not conveniences. A
    spec that references another spec's table cannot run until that table is
    built, so "run this" has always meant running its upstreams — `run_spec` did
    it implicitly. Doing it through `build_project(only=...)` is the same work,
    named, so the app can say which models it touched.

    And it **writes the SQL for what it ran**, so a run can never leave the
    deliverable describing an older version of the spec. That makes the staleness
    warning mean something narrower and more useful: it can now only fire on a
    spec edited outside the app.
    """
    app.run_error = None
    app.built = []
    # ``None`` is *nothing chosen*, which is the whole project — the same
    # tri-state the canvas draws from (`graph._visible`), so Run and what you can
    # see cannot disagree about scope.
    return await execute(
        app, only=sorted(scope) if scope else None, on_progress=on_progress, stop=stop
    )


def runs_in(app: App) -> list[Path]:
    """Saved run reports, newest first — from the run folders, and the old place.

    A run's account lives in ``out/<stamp>/_run/`` now. The project-root ``runs/``
    is read and never written: a project saved by an earlier version still has
    reports there, and a list that quietly dropped them would be the app deciding
    your history started when you upgraded. Newest first by path, which is date
    order because the folder is stamped.
    """
    found = sorted((app.root / OUT_DIR).glob(f"*/{pipeline.RUN_META_DIR}/*.md"), reverse=True)
    legacy = app.root / RUNS_DIR
    if legacy.is_dir():
        found += sorted(legacy.glob("*.md"), reverse=True)
    return found


async def write_report(app: App) -> Path | None:
    """Save the open run as markdown. Explicit, like every other write here."""
    if not app.results:
        return None
    return await asyncio.to_thread(
        spec_module.write_report,
        app.results,
        app.root / RUNS_DIR,
        spec_path=app.spec_path,
    )


def read_text(path: Path) -> str:
    """A saved report or a compiled model, off disk.

    Not threaded, and that is the point: the pane that shows it draws in one pass
    (`workflow.pane`), because an `await` mid-render is what put a blank frame on
    screen between deleting the old content and creating the new. These are local
    files of a few kilobytes; the work that genuinely blocks is still threaded
    below.
    """
    return path.read_text(encoding="utf-8")


# --- the two logged histories (docs/CONVERSATION.md §3) ----------------------


def logs_in(app: App) -> list[Path]:
    """Every chat and indexing job, newest first (`portia/runlog.py`).

    One list, both kinds and the pre-rename folder folded in: the right pane
    draws them together and tells them apart by glyph (`docs/CHAT_SESSIONS.md`
    §3.2). Not the same thing as `runs_in`, which lists saved *spec run*
    reports. A chat is how the recipe was decided; a run is what the recipe
    did; an indexing is neither, it is a job the app ran on your behalf.
    """
    return runlog.logs_in(app.catalog_dir)


def log_path(app: App, name: str) -> Path | None:
    """Where a named log lives, or ``None`` if it is not there any more.

    Resolved here so no panel has to know that a chat sits in `.portia/chats/`,
    an indexing in `.portia/indexing/`, and anything written before the rename in
    `.portia/runs/` — which is why this searches rather than joining a path.
    """
    return next((p for p in logs_in(app) if p.name == name), None)


def log_listing(app: App, path: Path) -> dict:
    """What one list row says: the top of the file and the name it was given."""
    return runlog.read_listing(path, app.catalog_dir)


def rename_log(app: App, path: Path, title: str) -> None:
    runlog.set_title(path, title, app.catalog_dir)


def delete_log(app: App, path: Path) -> None:
    runlog.delete(path, app.catalog_dir)


def read_log(path: Path) -> runlog.Transcript:
    """One logged transcript, off disk. Read in the render pass — see `read_text`."""
    return runlog.read(path)


def log_summary(transcript: runlog.Transcript) -> dict:
    """Its own counts. Computed by the engine, never by a panel — the app must
    not arrive at a different number than `cli.history` does."""
    return runlog.summary(transcript)


async def write_outputs(app: App) -> list[Path]:
    """Save the tables the last Run or Build produced, under ``out/``.

    **Every model that ran, not only the open one.** Run is already scoped to the
    open spec *and everything it reads*, and it writes the ``.sql`` for all of
    them; Build is the same mechanism at project scope. The tables follow that
    scope, so pressing this after a build leaves one file per model.

    Before, it wrote the open spec's table alone, which meant ``out/`` never held
    more than one file: selecting another spec clears the run, so the only table
    you could ever write was the one currently open, over the top of the last
    one. Naming each file for its model (`pipeline.write_outputs`) is what keeps
    "overwrite" honest — rebuilding a table replaces its own file, nothing else.

    **A table is saved with its report** *(2026-08-15)*, which is why this is one
    press and not two. They were two buttons, and the second one was the quiet
    half nobody pressed: a CSV in ``out/`` with no account of the run that made it
    is a number with its provenance left in a window that has since been closed —
    the exact thing the durable artifacts exist to stop. One report per table, so
    a saved run answers *which table* rather than *which press*.
    """
    if not app.built:
        return []
    # **The folder is resolved once**, here, and handed to both writers — the
    # tables and the account of them belong to one run, so neither may work out
    # where that is on its own.
    when = datetime.now()
    folder = pipeline.new_run_dir(app.root / OUT_DIR, when=when)
    written = await asyncio.to_thread(partial(pipeline.write_outputs, app.built, folder))
    app.outputs = written
    app.reports = await asyncio.to_thread(partial(_write_reports, app, folder, when))
    app.run_folder = folder
    return written


def _write_reports(app: App, folder: Path, when: datetime) -> list[Path]:
    """The account of the run, inside the run's own folder.

    ``out/<stamp>/_run/`` — one markdown per table plus an index of what ran.
    **Not a separate `runs/` at the project root**, which is where these used to
    go: the tables and the account of how they were made are two halves of one
    result, and keeping them in two trees meant a CSV you were holding had no way
    to say which run produced it.
    """
    meta = Path(folder) / pipeline.RUN_META_DIR
    written = [
        spec_module.write_report(
            model.results, meta, spec_path=model.spec_path, name=model.name, when=when
        )
        for model in app.built
        if model.results
    ]
    meta.mkdir(parents=True, exist_ok=True)
    (meta / RUN_INDEX).write_text(_run_index(app, when), encoding="utf-8")
    return written


def _run_index(app: App, when: datetime) -> str:
    """What ran, in build order, with where each table landed.

    The one file that answers "what is this folder" without opening a table. It
    counts and it never scores (`runlog.py`'s rule, applied to a save): which
    models ran, in which layer, and any flags they carried — no duration ranking,
    nothing sorted by size.
    """
    lines = [
        f"# run {when.strftime(pipeline.RUN_STAMP)}",
        "",
        f"{len(app.built)} model(s), in build order.",
        "",
    ]
    for model in app.built:
        where = f"{model.layer}/{model.name}.csv" if model.layer else f"{model.name}.csv"
        lines.append(f"- **{model.name}** → `{where}` · {len(model.results)} step(s)")
        if model.blocking:
            lines.append(f"  - blocking: {', '.join(model.blocking)}")
    return "\n".join(lines) + "\n"


def read_table(path: Path):
    """A produced table, read lazily — through the one loader, like everything else.

    Nothing is read here. The pane that shows it asks for a count and fifteen
    rows; before this it loaded the whole file to show those fifteen, which was a
    straight bug the moment an output got large. That count is what actually
    costs something, and it is measured off the loop and cached (`shape_of`).
    """
    return load_table(path, connect())


# --- the shape under a preview ----------------------------------------------
#
# **A row count is a scan, and it was running on the event loop** *(2026-09-07)*.
# `components.table_preview` asked a lazy `Table` for ``count(*)`` plus fifteen
# rows in the render pass, so every click that redrew the middle pane paid a
# full parse of a CSV per table on it — 423 ms measured on the demo project's
# ten-step report, twice per step (the label and the body each asked), and
# every one of those milliseconds was the window not answering clicks. A
# warehouse project paid it on the meter. The shape is now measured on a thread,
# once per key, and the pane draws *counting…* until it lands.
#
# The key is what the count is derived **from**, `column_lineage`'s rule: a
# file's path, size and mtime; a step result's identity; a remote table's name on
# its connection. A file rewritten on disk misses and is counted again; a result
# object is held by the cache so its id cannot be reused under it.

#: How many shapes are kept. A few dozen tables is a whole project; a cache
#: without a bound would hold every result of every run for the life of the
#: window.
SHAPE_CACHE = 64

_SHAPES: dict[tuple, tuple] = {}
_SHAPE_OWNERS: dict[tuple, Any] = {}
_MEASURING: set[tuple] = set()


def table_shape(data: Any, limit: int = State.PREVIEW_ROWS) -> tuple:
    """``(total rows, the first `limit` of them as pandas)`` — Table or DataFrame.

    One place both panes ask, so the number under a table and the number in the
    label above it can never come from two different counts. It is the engine
    call behind every preview, which is why it is here and not in `components`.
    """
    if isinstance(data, Table):
        return data.preview(limit)
    return len(data), data.head(limit)


def file_shape_key(path: Path) -> tuple:
    """A file's identity for the shape cache: where it is, and whether it moved."""
    return ("file", *_stamp(path))


def result_shape_key(result: Any) -> tuple:
    """A step result's identity. The object is held (`shape_of`), so ``id`` is safe."""
    return ("result", id(result))


def remote_shape_key(ref: str, app: App) -> tuple:
    """A warehouse table's identity: its name on this connection."""
    return ("remote", app.connection or "", ref)


def shape_of(key: tuple) -> tuple | None:
    """``(total rows, the first rows)`` if measured, else ``None``. Never computes."""
    return _SHAPES.get(key)


def measure_shape(key: tuple, table: Any, *, owner: Any = None) -> tuple:
    """Count and head ``table`` and remember the answer. **Call on a thread.**

    `Table.preview` takes its own cursor, so a table built on one thread can be
    measured on another. ``owner`` is kept beside the shape for as long as the
    shape is, so a key made from an object's id stays about that object.
    """
    shape = _SHAPES.get(key)
    if shape is not None:
        return shape
    shape = table_shape(table)
    while len(_SHAPES) >= SHAPE_CACHE:
        oldest = next(iter(_SHAPES))
        _SHAPES.pop(oldest, None)
        _SHAPE_OWNERS.pop(oldest, None)
    _SHAPES[key] = shape
    if owner is not None:
        _SHAPE_OWNERS[key] = owner
    return shape


async def measure_shape_later(key: tuple, table: Any, on_done: Callable[[], Any]) -> None:
    """Measure off the loop, then tell the pane. One measurement per key at a time.

    The second render of a pane whose count is still in flight must not start
    a second scan: the first one's answer is the answer.
    """
    if key in _MEASURING or key in _SHAPES:
        return
    _MEASURING.add(key)
    try:
        await asyncio.to_thread(measure_shape, key, table)
    except Exception:  # noqa: BLE001 — a preview that cannot be counted is drawn as such
        _SHAPES[key] = (None, None)
    finally:
        _MEASURING.discard(key)
    on_done()


def warm_shapes(results: list) -> None:
    """Measure every step result's shape. **Call on a thread**, after a run.

    The run just finished on this thread and is holding every step's relation,
    so the counts the report region is about to ask for cost a scan each *now*,
    off the loop, rather than on the first click that redraws it.
    """
    for result in results:
        table = getattr(result, "table", None)
        if table is not None:
            try:
                measure_shape(result_shape_key(result), table, owner=result)
            except Exception:  # noqa: BLE001 — one uncountable step is that step's problem
                continue


# --- reloading the spec after the copilot has written to it -----------------


#: What portia knows about one source, as three states rather than two. The
#: middle one is the one that matters and the one the app kept losing: a source
#: can be **profiled and never read**, which looks identical to a read one in any
#: list that only knows indexed/not-indexed — and it is the state a project sits
#: in when the copilot ran out of turn, or was never asked.
UNINDEXED, UNREAD, INTERPRETED = "unindexed", "unread", "interpreted"


@dataclass(frozen=True)
class SourceState:
    """One table in the project and what portia has done with it so far.

    A data file, a scoped warehouse table, or — since 2026-09-07 — a table the
    project built: the catalog holds an entry for each (`catalog.index_model`),
    and what a built table is carries as much context for the next piece of
    work as what a source is. ``kind`` says which, so a row can show it.
    """

    name: str
    rel: str
    state: str
    stale: bool = False
    kind: str = State.SOURCE
    #: Whether measured facts stand behind the entry (`catalog.is_profiled`).
    #: A file always; a warehouse table only once somebody paid for the scan.
    #: **Orthogonal to ``state``**: the copilot reads a scoped table off its
    #: metadata (`CONNECTOR.md` §2.6), so a table can be read and never
    #: profiled, and this is the axis the Indexing tab could not see
    #: (2026-09-07). Its Index button took files only, so a metadata-only
    #: table sat at *not read* with nothing on the tab able to profile it.
    profiled: bool = True

    @property
    def indexed(self) -> bool:
        return self.state != UNINDEXED

    @property
    def needs_profile(self) -> bool:
        """Whether Index has a scan to run on this table."""
        return self.indexed and not self.profiled


def source_states(app: App) -> list[SourceState]:
    """Every data file in the project's scope, with what portia knows about it.

    **Both halves in one list, deliberately.** The un-indexed files come from the
    data folder and the rest from the catalog, and a screen that showed only one
    of them made "what is left to do here" a question you answered by comparing
    two places. Sorted by name, never by state: which sources need attention is a
    judgment, and ordering by it would be the screen making it (`DESIGN.md`).
    """
    entries = catalog.load_catalog(app.portia_dir).get("sources") or {}
    known = {entry.get("source") for entry in entries.values()}
    states = [
        SourceState(
            name=name,
            rel=entry.get("source", ""),
            state=INTERPRETED if catalog.is_interpreted(entry) else UNREAD,
            stale=catalog.is_stale(entry, portia_dir=app.portia_dir),
            profiled=catalog.is_profiled(entry),
        )
        for name, entry in entries.items()
    ]
    # **A warehouse project lists no file** *(2026-09-21)*. Its data is the scope
    # (`project_tree` already draws it that way), and a project folder on a
    # work machine is a repository: with no `data_dir` the walk is the whole
    # root, and thirty scoped tables were listed among seventy stray CSVs and
    # Parquet files, by stem, so *forecast_runs* ten times, each one
    # tickable for an Index that would have profiled it into a project that
    # cannot join it to anything (`CONNECTOR.md` §2.2, no mixing).
    if not app.connection:
        states += [
            SourceState(name=path.stem, rel=rel, state=UNINDEXED)
            for path in data_files_in(app, app.data_dir)
            if (rel := path.relative_to(app.root).as_posix()) not in known
        ]
    # Built tables, by the same two states a source has: read, or not yet. A
    # model is never *unindexed* — building it is what indexes it — so the Index
    # button skips them and Interpret takes them, name for name.
    stale = set(stale_models(app))
    states += [
        SourceState(
            name=name,
            rel=str(entry.get("table") or f"models/{name}.sql"),
            state=INTERPRETED if entry.get("summary") else UNREAD,
            stale=name in stale,
            kind=State.MODEL,
            profiled=catalog.is_profiled(entry),
        )
        for name, entry in catalog.load_models(app.portia_dir).items()
    ]
    return sorted(states, key=lambda s: s.name)


def to_index(states: list[SourceState]) -> tuple[list[SourceState], list[SourceState]]:
    """What Index would do for these rows: files to profile, and tables to scan.

    Two lists because they cost different things. A file is profiled on this
    machine for nothing; a warehouse table, scoped or built there, is scanned
    on the meter, and the button's caption has to say so before it is pressed.
    """
    files = [s for s in states if not s.indexed]
    remote = [s for s in states if s.needs_profile]
    return files, remote


def sync_knowledge(app: App) -> str:
    """Put the project's structural half in the graph — **best effort, always**.

    The window indexes through `catalog.index_source` and used to stop there,
    so a source added here was in the catalog and absent from the graph until
    something else happened to refresh it. `cli.index` had done this since the
    graph shipped; the two edges disagreeing about what portia knows is the seam
    `docs/VISION.md` says must never break.

    Threaded, because it opens a Neo4j connection. Never fatal: an index that
    fails because a container is stopped is `KNOWLEDGE_GRAPH.md` §6.6's leak, and
    the catalog is written either way.
    """
    from portia import knowledge
    from portia.knowledge import store

    try:
        knowledge.sync(app.root, portia_dir=app.portia_dir)
    except store.GraphUnavailable as exc:
        return str(exc)
    return ""


#: The last column walk, and the project state it was taken on. One entry: the
#: canvas asks about the model it has open, and a second one would only be read
#: if two projects were open in one process, which is not a thing the app does.
_LINEAGE: tuple[tuple, dict] = ((), {})


def column_lineage(app: App, model: str):
    """Which of ``model``'s columns came from which input table.

    **This is the column-level half of what an arrow on the canvas means**, and
    it comes off `knowledge.build`, which reads the catalog and the specs into an
    in-memory graph and *opens no connection*. So the canvas can say
    ``rooms_booked came from bookings.rooms_sold`` whether or not Neo4j is
    running — the database is where that graph is **stored**, never where it is
    worked out. A panel that went blank when a container was stopped would be the
    app making a storage decision visible as a missing fact.

    Cached against the files it is derived from, because the walk parses every
    `sql` step with sqlglot — 130 ms on the demo project — and this is asked on
    every render of an open card. The key is what the answer is derived *from*, so
    a spec the copilot just appended to invalidates it without anything having to
    remember to.
    """
    from portia.knowledge import build as knowledge_build

    global _LINEAGE
    key = _lineage_key(app)
    cached_key, answers = _LINEAGE
    if key != cached_key:
        try:
            result = knowledge_build.build_graph(app.root, portia_dir=app.catalog_dir)
        except Exception:  # noqa: BLE001 — a lineage walk may never blank the canvas
            # A malformed spec is the spec pane's problem to report. The canvas
            # keeps drawing, one section short. **Any** exception, since
            # 2026-09-04: a `{table: …}` source reached the walk as a dict and
            # raised a `TypeError` the narrower clause let through, and the
            # whole middle pane went white on the first card click of a
            # warehouse project. The walk is a by-product; the card is not.
            return knowledge_build.Inputs()
        answers = {"graph": result.graph}
        _LINEAGE = (key, answers)
    if model not in answers:
        answers[model] = knowledge_build.inputs_of(answers["graph"], model)
    return answers[model]


def _lineage_key(app: App) -> tuple:
    """What a column walk is derived from: the spec files, and the catalog files.

    **Stat calls only** — size and mtime, the same two facts `catalog.is_stale`
    compares, and nothing is opened or parsed. The obvious alternative was
    `pipeline.fingerprint`, which digests the parts of a spec that decide its SQL
    and so would survive a reworded rationale; it was measured and dropped,
    because computing it means loading and re-serialising **every** spec in the
    project on **every** render of this pane. That spends the parse the cache
    exists to avoid, in order to occasionally avoid it — and the cost of a
    spurious miss is one rebuild, while the cost of the finer key is paid every
    time the mouse moves.
    """
    files = [app.root / path for path in spec_module.discover_specs(app.root).values()]
    # Every YAML under `.portia/` *is* the catalog — the project file and one per
    # source. Globbing rather than naming them keeps this from being a second
    # place that knows the catalog's file layout.
    files += [p for p in app.catalog_dir.rglob("*.yaml") if p.is_file()]
    return (str(app.root), tuple(sorted(_stamp(p) for p in files)))


def _stamp(path: Path) -> tuple:
    try:
        facts = path.stat()
    except OSError:
        return (str(path), None, None)
    return (str(path), facts.st_size, facts.st_mtime)


def knowledge_subgraph(app: App, *, columns: bool = False) -> dict:
    """The knowledge graph, as nodes and edges for the explorer to draw.

    Synchronous, like every other read a pane draws in one pass: this is a
    handful of Cypher over a few hundred nodes, not a scan of anyone's data.

    A stopped container comes back as ``{"unavailable": ...}`` rather than
    raising. The window has to behave sensibly when the database is down
    (`KNOWLEDGE_GRAPH.md` §3.5), and a pane is the surface where "behave
    sensibly" means *say so and draw nothing else*.

    It takes the app for one reason: the project. One Neo4j server holds every
    project on the machine, so a picture that named none of them drew all of
    them at once (`knowledge/schema.py`'s `PROJECT`).
    """
    from portia.knowledge import query, schema, store

    try:
        with store.session() as session:
            return query.subgraph(session, project=schema.project_id(app.root), columns=columns)
    except store.GraphUnavailable as exc:
        return {"nodes": [], "edges": [], "unavailable": str(exc)}


def reload_spec(app: App) -> None:
    """Re-read the open spec from disk; the copilot may have appended a step.

    Called after a turn ends and after each approved write, so the graph fills in
    as steps are recorded rather than after a manual refresh.
    """
    if app.spec_path and app.spec_path.exists():
        app.spec = spec_module.load_spec(app.spec_path)
    elif app.spec_path:
        app.spec = None


# --- keeping a chart (`docs/VISUALIZATION.md` §6) ----------------------------


def save_figure(app: App, chart: State.Chart, *, notes: str = "", folder: str = "") -> Path:
    """Keep a figure: the picture, the query that made it, and the note.

    **This replaced writing a finding** *(2026-09-03, the user's call)*. The
    journal keeps a sentence and a SQL string, which is the right record of a
    *conclusion* and is not a picture — reopening one meant re-running the query,
    and only while the chat that drew it was still on screen. A figure file
    carries its own rows, so it opens with nothing running.

    The journal did not go anywhere. `record_finding` is still the agent's tool
    and `findings.QUERY_TOOLS` still numbers `plot_data` calls, so a chart that
    changed a decision can still become a finding — by the copilot, in prose,
    where a conclusion belongs. What changed is that *keeping the picture* no
    longer has to be that.

    Raises `ValueError`, never `SystemExit`, as `cli/import_data` does.
    """
    return figures.save(
        {
            "tab": chart.name,
            "question": chart.question,
            "sql": chart.sql,
            "inputs": list(chart.inputs),
            "vega": dict(chart.vega),
            "columns": list(chart.columns),
            "n_rows": len(chart.rows),
            "rows": list(chart.rows),
        },
        notes=notes,
        folder=folder,
        root=app.root,
    )


def take_drawn(app: App) -> list[State.Chart]:
    """Stashed charts this window has not taken yet, oldest first.

    `figures.stash` is where a chart goes when the process that drew it has no
    window (`cli/serve.py`). Taken once per ``(name, mtime)``: the same name at a
    newer mtime is the chart drawn again, and it comes back to replace the tab.
    """
    taken = []
    for doc in figures.stashed(app.catalog_dir):
        name = str(doc.get("name") or "")
        if not name or app.drawn_seen.get(name) == doc["mtime_ns"]:
            continue
        app.drawn_seen[name] = doc["mtime_ns"]
        taken.append(
            State.Chart(
                name=name,
                question=str(doc.get("question") or ""),
                vega=dict(doc.get("vega") or {}),
                rows=list(doc.get("rows") or []),
                columns=list(doc.get("columns") or []),
                sql=str(doc.get("sql") or ""),
                inputs=list(doc.get("inputs") or []),
                stashed=True,
            )
        )
    return taken


def unstash(app: App, chart: State.Chart) -> None:
    """A stashed chart was kept or discarded, so its file goes (`figures.unstash`)."""
    if chart.stashed:
        figures.unstash(chart.name, app.catalog_dir)
        app.drawn_seen.pop(chart.name, None)
        chart.stashed = False


def stash_failure(app: App, chart: State.Chart) -> None:
    """Leave a stashed chart's render failure where the process that drew it reads."""
    if chart.stashed:
        figures.stash_failure(chart.name, chart.error or "", app.catalog_dir)


def load_figure(app: App, path: str) -> dict:
    """One saved figure, read off disk.

    Here rather than in `ui/charts.py` because this is the module that calls the
    engine — a pane reaching for `portia.figures` itself is the seam that keeps
    the rest of `ui/` free of the product's internals. `charts.open_figure` turns
    what comes back into a tab.
    """
    return figures.load(app.root / path)


def saved_figures(app: App) -> list[dict]:
    """Every figure in the gallery, each with its repo-relative ``path``."""
    return figures.load_all(app.root)


def figure_folders(app: App) -> list[str]:
    """Every folder in the gallery, the root first."""
    return figures.folders(app.root)


def folder_contents(app: App, folder: str) -> list[str]:
    """The figures a folder holds, at any depth — what deleting it would take."""
    return figures.contents(folder, root=app.root)


def remove_folder(app: App, folder: str) -> list[str]:
    """Delete a gallery folder with everything in it. Returns the figures removed.

    The window has asked by the time this is called (`artifacts._folder_confirm`);
    the engine's own refusal for a non-empty folder is the default and is
    overridden here on purpose, once, in the one caller that asked a person.
    """
    return figures.remove_folder(folder, root=app.root, with_contents=True)


def chart_queries(app: App, chart: State.Chart) -> list[dict]:
    """The chart's own `plot_data` call in the chat log, as `review` numbered it.

    **Looked up rather than remembered.** `handlers.plot_data` runs inside the
    MCP server and has no idea what position the log will give its call — the log
    is teed at the edges, after the fact — so the chart cannot carry one. The tab
    name is the handle, which it already is everywhere else (§3.3), and the
    *last* match wins because redrawing under a name supersedes.

    Empty when the chart came from somewhere that is not a chat (a test harness,
    a replay). A finding with no query under it is a sentence with nothing
    beneath it, so `keep_chart`'s caller is what refuses that — here, an empty
    list is a fact about the log rather than an error.
    """
    reviewed = findings.review(app.portia_dir, root=app.root)
    mine = [q for q in reviewed if q.get("tab") == chart.name]
    return mine[-1:]

"""One home per warehouse (`docs/CONNECTORS.md` §7).

A project that names a ``connection:`` in its ``project.yaml`` runs on that
warehouse; one that does not runs on DuckDB in memory. :func:`backend_for`
turns the first into a `core.backend.Backend` and :func:`activate` installs it,
which is the one thing every edge does before it opens a project — the app in
`ui/engine.open_project`, a CLI at the top of ``main``.

**Which warehouse is the connection's ``kind``**, one of `registry.PROVIDERS`,
and the module that knows how to open it is the one named after it in this
package (:func:`module_for`). Nothing outside this package imports a driver,
on the same argument `knowledge/store.py` makes about Neo4j: a missing extra
must fail as one sentence at the edge, not as an import error inside a check.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from types import ModuleType

from portia import catalog
from portia.connectors import pool, registry
from portia.connectors.pool import SecretRequired
from portia.core import backend
from portia.core.backend import Backend


class ConnectorUnavailable(RuntimeError):
    """The project names a connection this process cannot open — and says why."""


def module_for(kind: str) -> ModuleType:
    """The adapter for a provider: ``portia.connectors.<kind>``.

    Each supplies ``backend(connection, *, agent_writes)`` and a ``Pool``
    (`CONNECTORS.md` §3). Looked up by name so that adding a provider is one
    entry in the registry and one module beside it, and nothing here lists
    them twice.
    """
    if kind not in registry.PROVIDERS:
        raise ConnectorUnavailable(
            f"no connector for {kind!r}; portia knows {', '.join(registry.PROVIDERS)}"
        )
    return importlib.import_module(f"portia.connectors.{kind}")


def open_pool(connection: registry.Connection) -> pool.Pool:
    """A pool for one connection, not yet connected — for the CLI's own verbs."""
    return module_for(connection.kind).Pool(connection)


def backend_for(portia_dir: str | Path = catalog.DEFAULT_DIR) -> Backend:
    """The backend a project runs on, **without connecting**.

    ``LOCAL`` when ``project.yaml`` names no connection. Otherwise the named
    entry from the registry, as its provider's backend, whose ``open`` hands
    out handles on one shared session (`connectors/pool.py`) — a session per
    operation would be a browser popup per operation.
    """
    settings = catalog.project_settings(portia_dir)
    name = settings.get("connection")
    if not name:
        return backend.LOCAL
    try:
        connection = registry.get(name)
    except KeyError:
        known = ", ".join(registry.names()) or "(none)"
        raise ConnectorUnavailable(
            f"this project names the connection {name!r}, which is not in "
            f"{registry.CONNECTIONS}. Known: {known}. Add it in Settings, or with "
            f"`python -m portia.cli.connect add {name} …`."
        ) from None
    module = module_for(connection.kind)
    return module.backend(connection, agent_writes=bool(settings.get("agent_writes")))


def activate(
    portia_dir: str | Path = catalog.DEFAULT_DIR,
    *,
    connect: bool = True,
    secret: str | None = None,
) -> Backend:
    """Install the project's backend for the process, connecting if asked.

    ``connect=True`` opens the session now, which on browser SSO may open a
    browser. The app calls this off the event loop for that reason; a CLI
    calls it inline and the popup is the terminal's to wait on. ``secret`` is
    the password or token a connection that needs one was given this session;
    with none, such a connection raises `SecretRequired` before the driver is
    asked, and **the backend is still installed** so the project opens and the
    window can ask.
    """
    chosen = backend_for(portia_dir)
    if connect and chosen.remote:
        backend.use(chosen)
        pool.pool_of(chosen).connect(secret)
    backend.use(chosen)
    return chosen


def needs_secret(built: Backend) -> bool:
    """Whether connecting the active remote backend needs something typed first."""
    if not built.remote:
        return False
    return pool.pool_of(built).needs_secret


def set_agent_writes(on: bool) -> Backend:
    """Flip the hand-off on the active remote backend, keeping its session open.

    A `Backend` is a frozen value and the flag rides on it, so a new value is a
    new backend — but the session behind it was opened by a browser login and
    must not be dropped for a settings edit. No-op on a local backend.
    """
    active = backend.active()
    if not active.remote:
        return active
    backend.use(pool.rewrite(active, agent_writes=on))
    return backend.active()


def deactivate() -> None:
    """Back to DuckDB, closing whatever session the previous backend held."""
    previous = backend.use(backend.LOCAL)
    if previous.remote:
        pool.pool_of(previous).close()


__all__ = [
    "ConnectorUnavailable",
    "SecretRequired",
    "activate",
    "backend_for",
    "deactivate",
    "module_for",
    "needs_secret",
    "open_pool",
    "registry",
    "set_agent_writes",
]

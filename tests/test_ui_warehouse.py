"""The window's half of the connector, without a browser (`docs/CONNECTOR.md` §2.13).

What the pinned section draws is a tree built from the catalog by a function
that imports no NiceGUI; what opening a project does with a connection it
cannot reach is a state on `App`, not an exception. Both are pinned here. The
pixels are checked by driving the app (§10 of `VISUALIZATION.md`).
"""

from __future__ import annotations

import asyncio

import pytest
import yaml

from portia import catalog
from portia.core import backend
from portia.ui import state, tree
from portia.ui.state import App

pytest.importorskip("nicegui", reason="the app needs the `ui` extra")

from portia.ui import engine  # noqa: E402


def _remote(db: str, schema: str, table: str, *, profiled=None) -> dict:
    return {
        "source": f"{db}.{schema}.{table}",
        "remote": {"database": db, "schema": schema, "table": table, "kind": "table"},
        "indexed": {"size": 10, "mtime": "x", "rows": 3},
        "profiled": profiled,
        "columns": [{"name": "ID", "role": None, "dtype": "NUMBER(38,0)"}],
    }


def test_the_scope_is_drawn_as_database_schema_table():
    sources = {
        "ORDERS": _remote("SALES", "PUBLIC", "ORDERS"),
        "CUSTOMERS": _remote("SALES", "PUBLIC", "CUSTOMERS"),
        "EVENTS": _remote("AQN", "RAW", "EVENTS"),
        "local": {"source": "data/local.csv", "columns": []},
    }
    nodes = tree.warehouse(sources)
    assert [n.name for n in nodes] == ["AQN", "SALES"]
    assert all(n.kind == tree.DATABASE for n in nodes)
    sales = nodes[1]
    assert [s.name for s in sales.children] == ["PUBLIC"] and sales.children[0].kind == tree.SCHEMA
    tables = sales.children[0].children
    assert [(t.name, t.ident, t.rel) for t in tables] == [
        ("CUSTOMERS", "CUSTOMERS", "SALES.PUBLIC.CUSTOMERS"),
        ("ORDERS", "ORDERS", "SALES.PUBLIC.ORDERS"),
    ]
    assert tables[0].kind == "source"


def test_a_table_portia_built_is_drawn_under_its_schema_as_a_model():
    """`CONNECTOR.md` §2.7.2: what the project wrote sits beside what it reads,
    with the model glyph saying which is which. A model with no written table
    is not in the warehouse and is not drawn there."""
    sources = {"ORDERS": _remote("SALES", "PUBLIC", "ORDERS")}
    models = {
        "stg_orders": {"model": "stg_orders", "table": "SALES.STAGING.STG_ORDERS"},
        "local_only": {"model": "local_only"},
    }
    nodes = tree.warehouse(sources, models, table_kind="source", model_kind="model")
    sales = nodes[0]
    assert [s.name for s in sales.children] == ["PUBLIC", "STAGING"]
    (built,) = sales.children[1].children
    assert (built.name, built.ident, built.kind, built.rel) == (
        "STG_ORDERS",
        "stg_orders",
        "model",
        "SALES.STAGING.STG_ORDERS",
    )


def test_a_local_project_has_no_warehouse_tree():
    assert tree.warehouse({"a": {"source": "a.csv", "columns": []}}) == ()


def test_a_warehouse_table_is_not_a_file_in_the_tree(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    app = App()
    app.root, app.portia_dir = tmp_path, ".portia"
    app.catalog = {"sources": {"ORDERS": _remote("SALES", "PUBLIC", "ORDERS")}}
    assert engine.known_files(app) == {}
    assert [n.name for n in engine.warehouse_tree(app)] == ["SALES"]


def test_opening_a_project_that_names_an_unknown_connection_opens_anyway(tmp_path, monkeypatch):
    """The window opens on DuckDB and the pane says what is wrong; nothing raises."""
    from portia.connectors import registry

    monkeypatch.setattr(registry, "CONNECTIONS", tmp_path / "none.yaml")
    d = tmp_path / ".portia"
    catalog.init_project("x", portia_dir=d)
    catalog.set_connection("ghost", portia_dir=d)

    app = App()
    engine.open_project(tmp_path, app)

    assert app.connection == "ghost"
    assert app.connection_status.startswith("failed") and "ghost" in app.connection_status
    assert backend.active() is backend.LOCAL
    assert not engine.needs_connection(app)


def test_opening_a_local_project_installs_duckdb_and_no_status(tmp_path):
    d = tmp_path / ".portia"
    catalog.init_project("x", portia_dir=d)
    app = App()
    engine.open_project(tmp_path, app)
    assert app.connection is None and app.connection_status == ""
    assert backend.active() is backend.LOCAL


def test_saving_a_connection_refuses_a_missing_field_before_writing_anything(tmp_path, monkeypatch):
    from portia.connectors import registry

    monkeypatch.setattr(registry, "CONNECTIONS", tmp_path / "c.yaml")
    d = tmp_path / ".portia"
    catalog.init_project("x", portia_dir=d)
    app = App()
    engine.open_project(tmp_path, app)

    with pytest.raises(ValueError, match="warehouse"):
        engine.save_connection(
            {"name": "prod", "account": "a", "user": "u", "warehouse": ""}, app=app
        )
    assert app.connection is None and not (tmp_path / "c.yaml").exists()

    name = engine.save_connection(
        {"name": "prod", "account": "a", "user": "u", "warehouse": "W"}, app=app
    )
    assert name == "prod" and app.connection == "prod"
    assert app.connection_status == state.NOT_CONNECTED and engine.needs_connection(app)
    assert backend.active().remote and not backend.active().agent_writes
    proj = yaml.safe_load((d / "project.yaml").read_text())
    assert proj["connection"] == "prod" and "target" not in proj
    engine.clear_connection(app)
    assert app.connection is None and backend.active() is backend.LOCAL


def test_the_hand_off_rides_the_held_backend_without_dropping_the_session(tmp_path, monkeypatch):
    """`CONNECTOR.md` §2.7.1: the switch is a project setting and rides the
    process's backend; flipping it keeps the session a browser login opened."""
    from portia.connectors import registry, snowflake

    monkeypatch.setattr(registry, "CONNECTIONS", tmp_path / "c.yaml")
    d = tmp_path / ".portia"
    catalog.init_project("x", portia_dir=d)
    app = App()
    engine.open_project(tmp_path, app)
    fields = {"name": "prod", "account": "a", "user": "u", "warehouse": "W"}
    engine.save_connection(fields, agent_writes=True, app=app)
    assert app.agent_writes is True and backend.active().agent_writes is True
    pool_before = snowflake.pool_of(backend.active())
    engine.set_agent_writes(False, app)
    assert app.agent_writes is False and backend.active().agent_writes is False
    assert snowflake.pool_of(backend.active()) is pool_before
    engine.set_agent_writes(True, app)
    assert yaml.safe_load((d / "project.yaml").read_text())["agent_writes"] is True


def test_connecting_reports_a_failure_as_a_state_not_an_exception(tmp_path, monkeypatch):
    from portia import connectors
    from portia.connectors import registry

    monkeypatch.setattr(registry, "CONNECTIONS", tmp_path / "c.yaml")
    d = tmp_path / ".portia"
    catalog.init_project("x", portia_dir=d)
    app = App()
    engine.open_project(tmp_path, app)
    engine.save_connection({"name": "prod", "account": "a", "user": "u", "warehouse": "W"}, app=app)

    def refuse(*_a, **_k):
        raise RuntimeError("no browser here")

    monkeypatch.setattr(connectors, "activate", refuse)
    assert asyncio.run(engine.connect_project(app)) is False
    assert app.connection_status == "failed: no browser here"


def test_a_preview_never_opens_a_session(tmp_path):
    app = App()
    app.root = tmp_path
    app.connection_status = state.NOT_CONNECTED
    assert engine.read_source(_remote("SALES", "PUBLIC", "ORDERS"), app) is None


def test_ticking_scope_and_folding_nodes_are_plain_state():
    app = App()
    app.tick_scope("A.B.C", True)
    app.tick_scope("A.B.D", True)
    app.tick_scope("A.B.C", False)
    assert app.scope_ticks == frozenset({"A.B.D"})
    app.toggle_warehouse_node("A.B")
    assert "A.B" in app.warehouse_closed
    app.toggle_warehouse_node("A.B")
    assert app.warehouse_closed == frozenset()


# --- the first-run choice, and what a failure says ---------------------------------


def test_a_fresh_project_asks_where_the_data_is_and_a_told_one_does_not(tmp_path):
    d = tmp_path / ".portia"
    catalog.init_project("x", portia_dir=d)
    app = App()
    engine.open_project(tmp_path, app)
    assert app.data_mode == "" and app.needs_data_choice

    engine.choose_data(state.WAREHOUSE_DATA, app)
    assert not app.needs_data_choice and app.on_add_data

    catalog.set_data_dir("data", portia_dir=d)
    engine.open_project(tmp_path, app)
    assert app.data_mode == state.LOCAL_DATA and not app.needs_data_choice
    with pytest.raises(ValueError):
        engine.choose_data("cloudy", app)


def test_a_project_naming_a_connection_opens_in_warehouse_mode(tmp_path, monkeypatch):
    from portia.connectors import registry

    monkeypatch.setattr(registry, "CONNECTIONS", tmp_path / "c.yaml")
    registry.save(registry.Connection(name="prod", account="a", user="u", warehouse="W"))
    d = tmp_path / ".portia"
    catalog.init_project("x", portia_dir=d)
    catalog.set_connection("prod", portia_dir=d)
    app = App()
    engine.open_project(tmp_path, app)
    assert app.data_mode == state.WAREHOUSE_DATA and not engine.needs_secret(app)


def test_a_password_connection_is_not_attempted_until_something_is_typed(tmp_path, monkeypatch):
    from portia.connectors import registry

    monkeypatch.setattr(registry, "CONNECTIONS", tmp_path / "c.yaml")
    d = tmp_path / ".portia"
    catalog.init_project("x", portia_dir=d)
    app = App()
    engine.open_project(tmp_path, app)
    engine.save_connection(
        {"name": "dev", "account": "a", "user": "u", "warehouse": "W", "auth": "password"},
        app=app,
    )
    assert engine.needs_secret(app) and app.connection_status == state.NOT_CONNECTED
    assert asyncio.run(engine.connect_project(app)) is False
    assert "password" in app.connection_status and "390190" not in app.connection_status
    # The process-wide backend is what this test installed; left as it is, the
    # next indexing test asks a password connection for a session and fails.
    engine.clear_connection(app)


def test_the_saml_refusal_is_explained_in_plain_words():
    exc = RuntimeError(
        "390190 (08001): Failed to connect to DB: x.snowflakecomputing.com:443, There was an "
        "error related to the SAML Identity Provider account parameter. Contact Snowflake support."
    )
    plain = engine._plain(exc)
    assert "identity provider" in plain and "password or an access token" in plain
    assert engine._plain(RuntimeError("250001 (08001): Failed to connect")) == "Failed to connect"


def test_the_connect_dialog_opens_on_the_saved_list_with_the_projects_connection_picked():
    """The form only when nothing is saved; else the list, with the named one picked."""
    from portia.ui import screens

    assert screens._initial_view([], None) == (True, "")
    assert screens._initial_view([], "prod") == (True, "")
    assert screens._initial_view(["prod", "dev"], None) == (False, "")
    assert screens._initial_view(["prod", "dev"], "dev") == (False, "dev")
    assert screens._initial_view(["prod"], "ghost") == (False, "")


def test_the_form_carries_the_provider_and_the_dialog_starts_a_new_one_on_the_list(
    tmp_path, monkeypatch
):
    """`CONNECTORS.md` §2.13: a saved connection's row knows its provider, and a
    new connection is drawn from a provider list first, then that provider's
    fields and sign-in methods."""
    from portia.connectors import registry
    from portia.ui import screens

    monkeypatch.setattr(registry, "CONNECTIONS", tmp_path / "c.yaml")
    d = tmp_path / ".portia"
    catalog.init_project("x", portia_dir=d)
    app = App()
    engine.open_project(tmp_path, app)
    engine.save_connection(
        {"name": "prod", "kind": "snowflake", "account": "a", "user": "u", "warehouse": "W"},
        app=app,
    )
    # By name: the list also carries whatever vendor files this machine holds
    # (a `gcloud` config suggests a BigQuery connection), read and never written.
    form = next(f for f in engine.connection_suggestions() if f["name"] == "prod")
    assert form["kind"] == "snowflake" and form["auth"] == "browser"
    assert form["summary"] == "u@a" and form["role"] == ""
    assert screens._auth_label(form) == "Browser sign-in"
    assert screens._secret_label(form) is None
    assert screens._secret_label({**form, "auth": "token"}) == "Access token"

    assert screens._blank_form() == {} and screens._provider_of({}) is None
    assert screens._provider_of({"kind": "snowflake"}) is registry.SNOWFLAKE
    with pytest.raises(ValueError, match="no connector for 'oracle'"):
        engine.save_connection({"name": "x", "kind": "oracle"}, app=app)
    engine.clear_connection(app)


def test_switching_connection_forgets_what_the_picker_listed_for_the_last_one(
    tmp_path, monkeypatch
):
    """The user's report, 2026-09-20: connect to one database, then to another from
    the same screen, and the tree still drew the first one's schemas. A browse is
    kept on the app so a redraw costs no query, and nothing dropped it when the
    connection under it changed, so the databases were never asked for again."""
    from portia.connectors import registry

    monkeypatch.setattr(registry, "CONNECTIONS", tmp_path / "c.yaml")
    catalog.init_project("x", portia_dir=tmp_path / ".portia")
    app = App()
    engine.open_project(tmp_path, app)
    engine.save_connection(
        {"name": "first", "account": "a", "user": "u", "warehouse": "W"}, app=app
    )
    app.scope_listing = {"": ["ANALYTICS"], "ANALYTICS": ["RAW"]}
    app.scope_open = frozenset({"ANALYTICS"})
    app.scope_ticks = frozenset({"ANALYTICS.RAW.ORDERS"})
    app.scope_filter = "ord"

    engine.save_connection(
        {
            "name": "second",
            "kind": "postgres",
            "host": "localhost",
            "database": "shop",
            "user": "u",
        },
        app=app,
    )
    assert app.connection == "second" and backend.active().kind == "postgres"
    assert (app.scope_listing, app.scope_open, app.scope_ticks) == ({}, frozenset(), frozenset())
    assert app.scope_filter == ""

    app.scope_listing = {"": ["shop"]}
    engine.clear_connection(app)
    assert app.scope_listing == {}, "and going back to files drops it too"


def test_a_connected_project_offers_to_switch_not_to_use_an_existing_one():
    from portia.ui import screens

    assert screens.SWITCH_CONNECTION == "Switch connection"
    assert screens.USE_EXISTING != screens.SWITCH_CONNECTION

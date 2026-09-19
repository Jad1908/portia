"""The connector package, without a warehouse (`docs/CONNECTOR.md` §6).

What is decided in code — the registry, the suggestions, which backend a
project resolves to, how a described type is spelled, the cancel throttle — is
pinned here. What Snowflake answers is `test_snowflake_live.py`, which skips.
"""

from __future__ import annotations

import time

import pytest
import yaml

from portia import catalog
from portia.connectors import ConnectorUnavailable, backend_for, registry
from portia.core import backend

pytest.importorskip("snowflake.connector", reason="the snowflake extra is not installed")

from portia.connectors import pool as pools  # noqa: E402
from portia.connectors import snowflake  # noqa: E402


def _conn(name="prod", **over) -> registry.Connection:
    fields = {"account": "acme-eu", "user": "jad@acme.com", "warehouse": "WH_S"}
    fields.update(over)
    return registry.Connection(name=name, **fields)


# --- the registry --------------------------------------------------------------


def test_the_registry_round_trips_and_holds_no_secret(tmp_path):
    path = tmp_path / "connections.yaml"
    registry.save(_conn(role="ANALYST"), path)
    registry.save(_conn("dev", database="DEV"), path)

    loaded = registry.load(path)
    assert set(loaded) == {"prod", "dev"}
    assert loaded["prod"].role == "ANALYST" and loaded["prod"].database is None
    assert registry.names(path) == ["dev", "prod"]
    text = path.read_text(encoding="utf-8")
    assert "password" not in text and "token" not in text


def test_saving_replaces_by_name_and_remove_says_whether_it_did(tmp_path):
    path = tmp_path / "c.yaml"
    registry.save(_conn(warehouse="WH_S"), path)
    registry.save(_conn(warehouse="WH_L"), path)
    assert registry.get("prod", path).warehouse == "WH_L"
    assert registry.remove("prod", path) is True
    assert registry.remove("prod", path) is False
    assert registry.load(path) == {}


def test_a_connection_missing_a_required_field_is_refused(tmp_path):
    with pytest.raises(ValueError, match="Missing: user"):
        registry.save(_conn(user=""), tmp_path / "c.yaml")


def test_an_absent_file_is_no_connections(tmp_path):
    assert registry.load(tmp_path / "nope.yaml") == {}


def test_suggestions_read_snowflakes_file_and_drop_its_secrets(tmp_path):
    toml = tmp_path / "connections.toml"
    toml.write_text(
        '[work]\naccount = "acme-eu"\nuser = "jad"\nwarehouse = "WH"\npassword = "hunter2"\n'
        '[bare]\naccount = "x"\n',
        encoding="utf-8",
    )
    found = {c.name: c for c in registry.snowflake_suggestions(toml)}
    assert found["work"].user == "jad" and found["work"].warehouse == "WH"
    assert "hunter2" not in repr(found["work"])
    # Offered even when incomplete: the form says what is left.
    assert not found["bare"].warehouse


def test_suggestions_survive_a_missing_or_broken_file(tmp_path):
    assert registry.snowflake_suggestions(tmp_path / "none.toml") == []
    broken = tmp_path / "broken.toml"
    broken.write_text("[oops\n", encoding="utf-8")
    assert registry.snowflake_suggestions(broken) == []


def test_snowflakes_file_location_honours_snowflake_home(monkeypatch, tmp_path):
    monkeypatch.setenv("SNOWFLAKE_HOME", str(tmp_path))
    assert registry.snowflake_connections_file() == tmp_path / "connections.toml"


# --- which backend a project resolves to ----------------------------------------


def test_a_project_naming_no_connection_is_local(tmp_path):
    d = tmp_path / ".portia"
    catalog.init_project("x", portia_dir=d)
    assert backend_for(d) is backend.LOCAL
    assert catalog.project_settings(d) == {
        "data_dir": "",
        "connection": None,
        "scope": [],
        "agent_writes": False,
    }


def test_a_project_naming_an_unknown_connection_says_what_is_known(tmp_path, monkeypatch):
    d = tmp_path / ".portia"
    catalog.init_project("x", portia_dir=d)
    proj = d / "project.yaml"
    data = yaml.safe_load(proj.read_text(encoding="utf-8"))
    data["connection"] = "ghost"
    proj.write_text(yaml.safe_dump(data), encoding="utf-8")
    monkeypatch.setattr(registry, "CONNECTIONS", tmp_path / "c.yaml")
    registry.save(_conn("prod"), tmp_path / "c.yaml")
    with pytest.raises(ConnectorUnavailable, match="ghost.*Known: prod"):
        backend_for(d)


def test_a_project_naming_a_known_connection_resolves_without_connecting(tmp_path, monkeypatch):
    d = tmp_path / ".portia"
    catalog.init_project("x", portia_dir=d)
    proj = d / "project.yaml"
    data = yaml.safe_load(proj.read_text(encoding="utf-8"))
    data["connection"] = "prod"
    proj.write_text(yaml.safe_dump(data), encoding="utf-8")
    monkeypatch.setattr(registry, "CONNECTIONS", tmp_path / "c.yaml")
    registry.save(_conn("prod", database="ANALYTICS"), tmp_path / "c.yaml")

    built = backend_for(d)
    assert built.remote and built.kind == "snowflake" and built.opens_on == "ANALYTICS"
    assert built.label == "prod" and built.agent_writes is False
    assert not snowflake.pool_of(built).connected, "resolving must not open a browser"
    assert catalog.load_catalog(d)["connection"] == "prod"


# --- the adapter's own decisions -------------------------------------------------


class _Meta:
    def __init__(self, type_code, precision=None, scale=None):
        self.type_code, self.precision, self.scale = type_code, precision, scale


def test_a_described_type_is_spelled_as_the_warehouse_would():
    assert snowflake.type_name(_Meta(0, 38, 0)) == "NUMBER(38,0)"
    assert snowflake.type_name(_Meta(0, 10, 2)) == "NUMBER(10,2)"
    assert snowflake.type_name(_Meta(1)) == "FLOAT"
    assert snowflake.type_name(_Meta(2)) == "TEXT"
    assert snowflake.type_name(_Meta(8)) == "TIMESTAMP_NTZ"
    assert snowflake.type_name(_Meta(13)) == "BOOLEAN"


def test_split_wants_three_parts():
    assert snowflake.split("DB.S.T") == ("DB", "S", "T")
    with pytest.raises(ValueError, match="DATABASE.SCHEMA.TABLE"):
        snowflake.split("S.T")


def test_scope_is_sorted_qualified_names_without_repeats():
    picks = [("DB", "S", "B"), ("DB", "S", "A"), ("DB", "S", "A")]
    assert snowflake.scope_from(picks) == ["DB.S.A", "DB.S.B"]


class _FakeRaw:
    """Enough of the connector's connection to drive the throttle. Records, decides nothing."""

    session_id = 42

    def __init__(self):
        self.executed: list[str] = []

    def cursor(self):
        return self

    def execute(self, sql):
        self.executed.append(sql)

    def is_closed(self):
        return False

    def close(self):
        pass


def test_interrupt_only_fires_while_a_statement_is_in_flight():
    raw = _FakeRaw()
    session = snowflake.Session(raw, owner=True)
    session.interrupt()
    assert raw.executed == [], "nothing running, nothing to cancel"

    with session._shared.running():
        session.interrupt()
        session.interrupt()
    cancels = [s for s in raw.executed if "CANCEL_ALL_QUERIES(42)" in s]
    assert len(cancels) == 1, "the scope asks every 50 ms; one round trip a second is enough"


def test_interrupt_fires_again_after_the_throttle_window(monkeypatch):
    raw = _FakeRaw()
    session = snowflake.Session(raw, owner=True)
    monkeypatch.setattr(pools, "CANCEL_EVERY", 0.01)
    with session._shared.running():
        session.interrupt()
        time.sleep(0.02)
        session.interrupt()
    assert sum("CANCEL_ALL_QUERIES" in s for s in raw.executed) == 2


def test_a_sibling_shares_the_session_and_does_not_close_it():
    raw = _FakeRaw()
    closed = []
    raw.close = lambda: closed.append(1)
    session = snowflake.Session(raw, owner=True)
    sibling = session.cursor()
    assert (
        sibling._shared is session._shared
        and sibling.remote
        and sibling.dialect.name == "snowflake"
    )
    sibling.close()
    assert closed == []
    session.close()
    assert closed == [1]


def test_a_frame_cannot_be_registered_on_a_warehouse_session():
    session = snowflake.Session(_FakeRaw(), owner=True)
    with pytest.raises(NotImplementedError, match="fixtures are local"):
        session.register("x", None)


def test_write_table_refuses_a_remote_table(tmp_path):
    from portia.core.io import write_table
    from portia.core.table import Table

    t = Table(name="m", query="SELECT 1", con=snowflake.Session(_FakeRaw(), owner=True))
    with pytest.raises(ValueError, match="CONNECTOR.md"):
        write_table(t, tmp_path / "m.csv")


# --- how a session is signed in (`CONNECTOR.md` §2.4, revised 2026-09-04) ---------


def test_a_connection_defaults_to_browser_sign_in_and_records_only_the_others(tmp_path):
    path = tmp_path / "c.yaml"
    registry.save(_conn("sso"), path)
    registry.save(_conn("pw", auth=registry.PASSWORD), path)
    loaded = registry.load(path)
    assert loaded["sso"].auth == registry.BROWSER and not loaded["sso"].needs_secret
    assert loaded["pw"].auth == registry.PASSWORD and loaded["pw"].needs_secret
    text = path.read_text(encoding="utf-8")
    assert "auth: password" in text and "auth: browser" not in text
    with pytest.raises(ValueError, match="signs in with one of"):
        registry.save(_conn("bad", auth="magic"), path)


def test_login_fields_per_method_and_the_secret_goes_nowhere_else():
    sso = snowflake.login_fields(_conn())
    assert sso["authenticator"] == "externalbrowser" and "password" not in sso
    pw = snowflake.login_fields(_conn(auth=registry.PASSWORD), "hunter2")
    assert pw["password"] == "hunter2" and "authenticator" not in pw
    tok = snowflake.login_fields(_conn(auth=registry.TOKEN), "pat-123")
    assert tok["authenticator"] == "PROGRAMMATIC_ACCESS_TOKEN" and tok["token"] == "pat-123"
    assert all(f["application"] == "portia" for f in (sso, pw, tok))


def test_a_password_connection_refuses_to_connect_without_one_before_asking_the_driver():
    pool = snowflake.Pool(_conn(auth=registry.PASSWORD))
    assert pool.needs_secret
    with pytest.raises(snowflake.SecretRequired, match="password"):
        pool.connect()
    assert not pool.connected


def test_the_hand_off_rides_the_backend_and_a_stale_target_key_is_dropped(tmp_path, monkeypatch):
    """`agent_writes` is a project setting (`CONNECTOR.md` §2.7.1): absent means off,
    `set_connection(agent_writes=None)` leaves it alone, and the backend carries it
    so `record_step` reads it off the process, not a file. A `target` key left by
    the two days a project-wide one existed is removed: nothing reads it."""
    from portia import connectors

    d = tmp_path / ".portia"
    catalog.init_project("x", portia_dir=d)
    proj = d / "project.yaml"
    proj.write_text(
        yaml.safe_dump({**yaml.safe_load(proj.read_text(encoding="utf-8")), "target": "A.B"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(registry, "CONNECTIONS", tmp_path / "c.yaml")
    registry.save(_conn("prod"), tmp_path / "c.yaml")
    catalog.set_connection("prod", agent_writes=True, portia_dir=d)
    assert catalog.project_settings(d)["agent_writes"] is True
    assert "target" not in yaml.safe_load(proj.read_text(encoding="utf-8"))
    catalog.set_connection("prod", portia_dir=d)
    assert catalog.project_settings(d)["agent_writes"] is True, "a plain re-save keeps the hand-off"
    catalog.set_connection("prod", agent_writes=False, portia_dir=d)
    assert "agent_writes" not in yaml.safe_load(proj.read_text(encoding="utf-8"))

    catalog.set_connection("prod", agent_writes=True, portia_dir=d)
    built = backend_for(d)
    assert built.agent_writes is True and built.opens_on is None
    with backend.using(built):
        off = connectors.set_agent_writes(False)
        assert off.agent_writes is False
        assert snowflake.pool_of(off) is snowflake.pool_of(built)


# --- a connection is its provider's shape (`CONNECTORS.md` §2.4, §2.13) ------------


def test_a_connection_reads_its_providers_fields_as_attributes_and_refuses_a_strangers():
    c = _conn(role="ANALYST")
    assert c.kind == "snowflake" and c.provider is registry.SNOWFLAKE
    assert c.account == "acme-eu" and c.role == "ANALYST" and c.database is None
    assert c.fields == {
        "account": "acme-eu",
        "user": "jad@acme.com",
        "warehouse": "WH_S",
        "role": "ANALYST",
    }
    with pytest.raises(AttributeError):
        c.keyfile  # noqa: B018 — a BigQuery field is not a Snowflake attribute


def test_a_record_names_its_kind_only_when_it_is_not_the_default(tmp_path):
    path = tmp_path / "c.yaml"
    registry.save(_conn(), path)
    text = path.read_text(encoding="utf-8")
    assert "kind:" not in text, "every file written before there was a second provider is Snowflake"
    assert registry.load(path)["prod"].kind == "snowflake"


def test_an_unknown_kind_is_refused_by_name_before_anything_is_written(tmp_path):
    with pytest.raises(ValueError, match="no connector for 'oracle'"):
        registry.save(
            registry.Connection(name="x", kind="oracle", account="a"), tmp_path / "c.yaml"
        )
    assert not (tmp_path / "c.yaml").exists()


def test_the_provider_says_what_each_sign_in_needs_typed():
    p = registry.SNOWFLAKE
    assert p.default_auth == registry.BROWSER
    assert p.auth_of(registry.BROWSER).secret is None
    assert p.auth_of(registry.PASSWORD).secret == "Password"
    assert p.auth_of(registry.TOKEN).secret == "Access token"
    assert p.required == ("account", "user", "warehouse")
    assert p.summary(_conn()) == "jad@acme.com@acme-eu"
    with pytest.raises(ValueError, match="signs in with one of"):
        p.auth_of("magic")


def test_the_module_for_a_kind_is_looked_up_by_name_and_a_stranger_is_one_sentence():
    from portia import connectors

    assert connectors.module_for("snowflake") is snowflake
    with pytest.raises(connectors.ConnectorUnavailable, match="no connector for 'oracle'"):
        connectors.module_for("oracle")
    pool = connectors.open_pool(_conn())
    assert isinstance(pool, snowflake.Pool) and not pool.connected


def test_the_secret_lives_on_the_shared_pool_and_a_backend_finds_its_pool():
    """`connectors/pool.py`: what every provider shares, held once."""
    pool = snowflake.Pool(_conn(auth=registry.PASSWORD))
    built = snowflake.backend(_conn(), agent_writes=True)
    assert pools.pool_of(built) is not pool and pools.pool_of(built).connection.name == "prod"
    rebuilt = pools.rewrite(built, agent_writes=False)
    assert pools.pool_of(rebuilt) is pools.pool_of(built) and rebuilt.agent_writes is False
    assert built.opens_on is None
    assert snowflake.backend(_conn(database="A", schema="B")).opens_on == "A.B"


# --- BigQuery (`CONNECTORS.md` §8): what the adapter decides, without Google ------

bigquery = pytest.importorskip(
    "portia.connectors.bigquery", reason="the bigquery extra is not installed"
)


def _bq(name="gcp", **over) -> registry.Connection:
    fields = {"project": "acme-analytics"}
    fields.update(over)
    return registry.Connection(name=name, kind="bigquery", **fields)


def test_a_bigquery_connection_needs_a_project_and_a_key_file_only_for_a_service_account(tmp_path):
    path = tmp_path / "c.yaml"
    with pytest.raises(ValueError, match="Missing: project"):
        registry.save(_bq(project=""), path)
    with pytest.raises(ValueError, match="key file. Missing: keyfile"):
        registry.save(_bq(auth=registry.SERVICE_ACCOUNT), path)
    registry.save(_bq(auth=registry.SERVICE_ACCOUNT, keyfile="~/k.json"), path)
    registry.save(_bq("tok", auth=registry.ACCESS_TOKEN, location="EU"), path)
    loaded = registry.load(path)
    assert loaded["gcp"].kind == "bigquery" and loaded["gcp"].keyfile == "~/k.json"
    assert not loaded["gcp"].needs_secret, "a key file is a path, not something typed"
    assert loaded["tok"].needs_secret and loaded["tok"].secret_label == "Access token"
    text = path.read_text(encoding="utf-8")
    assert "kind: bigquery" in text and "auth: adc" not in text, "the default auth is left out"


def test_the_bigquery_provider_maps_its_levels_onto_portias_three():
    p = registry.BIGQUERY
    assert p.levels == ("project", "dataset") and p.default_auth == registry.ADC
    assert p.summary(_bq(location="EU")) == "acme-analytics · EU"
    built = bigquery.backend(_bq(dataset="mart"))
    assert built.kind == "bigquery" and built.remote and built.dialect.name == "bigquery"
    assert built.opens_on == "acme-analytics.mart", "the brief offers project.dataset"
    assert bigquery.pool_of(built).connection.name == "gcp"


def test_gcloud_default_project_is_read_for_a_suggestion_and_never_written(tmp_path):
    (tmp_path / "configurations").mkdir()
    (tmp_path / "active_config").write_text("work\n", encoding="utf-8")
    (tmp_path / "configurations" / "config_work").write_text(
        "[core]\naccount = jad@acme.com\nproject = acme-analytics\n[compute]\nregion = eu\n",
        encoding="utf-8",
    )
    assert registry.gcloud_default_project(tmp_path) == "acme-analytics"
    (found,) = registry.bigquery_suggestions(tmp_path)
    assert found.name == "gcloud" and found.kind == "bigquery" and found.project == "acme-analytics"
    assert registry.gcloud_default_project(tmp_path / "none") is None
    assert registry.bigquery_suggestions(tmp_path / "none") == []


def test_gcloud_config_dir_honours_the_sdks_override(monkeypatch, tmp_path):
    monkeypatch.setenv("CLOUDSDK_CONFIG", str(tmp_path))
    assert registry.gcloud_config_dir() == tmp_path


class _Field:
    def __init__(self, name, field_type, mode="NULLABLE"):
        self.name, self.field_type, self.mode = name, field_type, mode


def test_a_described_type_is_spelled_as_the_console_would():
    assert bigquery.type_name(_Field("n", "INTEGER")) == "INT64"
    assert bigquery.type_name(_Field("x", "FLOAT")) == "FLOAT64"
    assert bigquery.type_name(_Field("b", "BOOLEAN")) == "BOOL"
    assert bigquery.type_name(_Field("s", "STRING")) == "STRING"
    assert bigquery.type_name(_Field("r", "RECORD")) == "STRUCT"
    assert bigquery.type_name(_Field("t", "TIMESTAMP")) == "TIMESTAMP"
    assert bigquery.type_name(_Field("a", "INTEGER", "REPEATED")) == "ARRAY<INT64>"


def test_the_login_plan_per_method_and_the_secret_goes_nowhere_durable():
    adc = bigquery.login_plan(_bq())
    assert adc["auth"] == "adc" and adc["project"] == "acme-analytics" and adc["keyfile"] is None
    sa = bigquery.login_plan(_bq(auth=registry.SERVICE_ACCOUNT, keyfile="~/k.json", location="EU"))
    assert sa["keyfile"].endswith("/k.json") and "~" not in sa["keyfile"] and sa["location"] == "EU"
    tok = bigquery.login_plan(_bq(auth=registry.ACCESS_TOKEN), "ya29.x")
    assert tok["has_secret"] and "ya29.x" not in str(tok)


class _Job:
    def __init__(self, job_id):
        self.job_id, self.location = job_id, "EU"


class _FakeClient:
    """Enough of the driver's client to drive the throttle. Records, decides nothing."""

    project = "acme-analytics"

    def __init__(self):
        self.cancelled: list[str] = []
        self.closed = False

    def cancel_job(self, job_id, location=None):
        self.cancelled.append(job_id)

    def close(self):
        self.closed = True

    def list_datasets(self, project):
        class D:
            def __init__(self, i):
                self.dataset_id = i

        return [D("raw"), D("analytics")]

    def list_tables(self, ref):
        class T:
            def __init__(self, i, k):
                self.table_id, self.table_type = i, k

        return [T("orders", "TABLE"), T("v_orders", "VIEW"), T("m", "MATERIALIZED_VIEW")]


def test_interrupt_cancels_the_running_job_and_only_while_one_runs():
    client = _FakeClient()
    session = bigquery.Session(client, _bq(), owner=True)
    session.interrupt()
    assert client.cancelled == [], "nothing running, nothing to cancel"
    with session._shared.running(_Job("job-1")):
        session.interrupt()
        session.interrupt()
    assert client.cancelled == ["job-1"], "one round trip a second, by job id"


def test_a_sibling_shares_the_client_and_does_not_close_it():
    client = _FakeClient()
    session = bigquery.Session(client, _bq(), owner=True)
    sibling = session.cursor()
    assert sibling._shared is session._shared and sibling.remote
    assert sibling.dialect.name == "bigquery"
    sibling.close()
    assert not client.closed
    session.close()
    assert client.closed and session.closed


def test_browsing_is_project_dataset_table_and_says_which_are_views():
    session = bigquery.Session(
        _FakeClient(), _bq(projects="bigquery-public-data, acme-analytics"), owner=True
    )
    assert session.databases() == ["acme-analytics", "bigquery-public-data"]
    assert session.schemas("acme-analytics") == ["analytics", "raw"]
    assert session.tables("acme-analytics", "raw") == [
        ("m", "view"),
        ("orders", "table"),
        ("v_orders", "view"),
    ]
    with pytest.raises(NotImplementedError, match="fixtures are local"):
        session.register("x", None)


def test_the_credentials_refusal_is_explained_in_plain_words():
    pytest.importorskip("nicegui", reason="the app needs the `ui` extra")
    from portia.ui import engine

    plain = engine._plain(
        RuntimeError(
            "Your default credentials were not found. To set up Application Default "
            "Credentials, see https://cloud.google.com/docs/authentication/external/set-up-adc."
        )
    )
    assert "gcloud auth application-default login" in plain and "service account" in plain


def test_an_entry_from_a_newer_build_is_skipped_and_kept(tmp_path):
    """`~/.config/portia/connections.yaml` is shared by every checkout on the
    machine. A connection of a kind this build has no provider for (a BigQuery
    entry, on the day BigQuery landed on one branch and not the other) used to
    raise from `load` and open the window on a 500. It is left out of what this
    build offers, and a save from this build must not drop it from the file."""
    import yaml

    from portia.connectors import registry

    path = tmp_path / "connections.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "connections": {
                    "mine": {"account": "a", "user": "u", "warehouse": "w"},
                    "theirs": {"kind": "redshift", "cluster": "c-1"},
                }
            }
        ),
        encoding="utf-8",
    )

    assert registry.names(path) == ["mine"]

    registry.save(registry.Connection(name="second", account="b", user="u", warehouse="w"), path)
    written = yaml.safe_load(path.read_text(encoding="utf-8"))["connections"]
    assert written["theirs"] == {"kind": "redshift", "cluster": "c-1"}, "kept as written"
    assert set(written) == {"mine", "second", "theirs"}

    assert registry.remove("mine", path) is True
    assert set(yaml.safe_load(path.read_text(encoding="utf-8"))["connections"]) == {
        "second",
        "theirs",
    }


# --- signing in the way Snowflake's own file says (`docs/HEADLESS.md` §7) ----------


def _vendor_file(tmp_path, monkeypatch, body):
    monkeypatch.setenv("SNOWFLAKE_HOME", str(tmp_path))
    (tmp_path / "connections.toml").write_text(body, encoding="utf-8")


VENDOR = """
[demo]
account = "acme-eu"
user = "jad"
password = "hunter2"
warehouse = "WH_S"
role = "ANALYST"
"""


def test_a_file_connection_hands_the_connector_a_name_and_nothing_else():
    """The connector merges what it is passed *over* the entry, so a field portia
    recorded would overrule a file its owner has since edited. And no secret is
    in what portia sends, because portia never had one."""
    sent = snowflake.login_fields(_conn("demo", auth=registry.FILE, role="STALE_ROLE"))
    assert sent == {"connection_name": "demo", "application": snowflake.APPLICATION}


def test_the_entry_may_be_called_something_else_than_the_connection():
    sent = snowflake.login_fields(_conn("work", auth=registry.FILE, profile="demo"))
    assert sent["connection_name"] == "demo"


def test_a_file_connection_needs_nothing_typed():
    c = _conn("demo", auth=registry.FILE)
    assert not c.needs_secret and c.secret_label is None
    assert not snowflake.Pool(c).needs_secret


def test_a_file_connection_fills_what_nobody_typed_from_the_entry(tmp_path, monkeypatch):
    _vendor_file(tmp_path, monkeypatch, VENDOR)
    bare = registry.Connection(name="demo", auth=registry.FILE)
    filled = registry.filled_from_vendor(bare)
    filled.check()
    assert (filled.account, filled.user, filled.warehouse, filled.role) == (
        "acme-eu",
        "jad",
        "WH_S",
        "ANALYST",
    )
    assert "hunter2" not in repr(filled) and "password" not in filled.fields


def test_what_was_typed_wins_over_the_entry_and_other_methods_are_left_alone(tmp_path, monkeypatch):
    _vendor_file(tmp_path, monkeypatch, VENDOR)
    typed = registry.Connection(name="demo", auth=registry.FILE, role="LOADER")
    assert registry.filled_from_vendor(typed).role == "LOADER"
    browser = registry.Connection(name="demo", auth=registry.BROWSER)
    assert registry.filled_from_vendor(browser) is browser


def test_an_entry_that_is_not_in_the_file_is_refused_by_the_check_it_always_was(
    tmp_path, monkeypatch
):
    _vendor_file(tmp_path, monkeypatch, VENDOR)
    absent = registry.filled_from_vendor(registry.Connection(name="nope", auth=registry.FILE))
    with pytest.raises(ValueError, match="Missing: account"):
        absent.check()


def test_a_file_connection_round_trips_through_the_registry(tmp_path):
    path = tmp_path / "c.yaml"
    registry.save(_conn("work", auth=registry.FILE, profile="demo"), path)
    got = registry.get("work", path)
    assert got.auth == registry.FILE and got.profile == "demo"


def test_the_connect_command_refuses_a_typed_secret_where_nobody_can_type(monkeypatch):
    """A host's shell has no terminal. `getpass` would wait on a prompt nothing answers."""
    import io
    import sys

    from portia.cli import connect as connect_cli

    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    with pytest.raises(SystemExit, match="no terminal here"):
        connect_cli._secret_for(_conn(auth=registry.PASSWORD))
    assert connect_cli._secret_for(_conn(auth=registry.FILE)) is None


def test_a_file_written_in_config_tomls_form_is_said_in_words(tmp_path, monkeypatch):
    """The first real drive: `[connections.demo]` reads as a connection called
    *connections*, offered with every field missing and unopenable by name."""
    _vendor_file(tmp_path, monkeypatch, VENDOR.replace("[demo]", "[connections.demo]"))
    (tmp_path / "connections.toml").chmod(0o600)
    (problem,) = registry.snowflake_file_problems()
    assert "[connections.demo]" in problem and "it is [demo]" in problem
    assert "hunter2" not in problem


def test_a_file_other_users_can_read_is_said_with_the_command_that_fixes_it(tmp_path, monkeypatch):
    _vendor_file(tmp_path, monkeypatch, VENDOR)
    (tmp_path / "connections.toml").chmod(0o644)
    (problem,) = registry.snowflake_file_problems()
    assert "chmod 0600" in problem
    (tmp_path / "connections.toml").chmod(0o600)
    assert registry.snowflake_file_problems() == []


def test_no_file_is_no_problem(tmp_path, monkeypatch):
    monkeypatch.setenv("SNOWFLAKE_HOME", str(tmp_path))
    assert registry.snowflake_file_problems() == []

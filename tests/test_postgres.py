"""What the PostgreSQL connector decides, without a driver or a server (`docs/CONNECTORS.md` §9).

The adapter imports its driver only when a session is opened, so everything
here runs with no extra installed. Whether its questions are the ones a server
answers is `test_postgres_live.py`.
"""

from __future__ import annotations

import pytest

from portia import catalog
from portia.connectors import module_for, postgres, registry
from portia.core import dialect


def _pg(name="shop", **over) -> registry.Connection:
    fields = {"host": "localhost", "database": "shop", "user": "jane"} | over
    return registry.Connection(name=name, kind="postgres", **fields)


def test_a_postgres_connection_needs_a_host_a_database_and_a_user(tmp_path):
    path = tmp_path / "c.yaml"
    with pytest.raises(ValueError, match="Missing: host"):
        registry.save(_pg(host=""), path)
    registry.save(_pg(port="5433", sslmode="require"), path)
    registry.save(_pg("quiet", auth=registry.PGPASS), path)
    loaded = registry.load(path)
    assert loaded["shop"].kind == "postgres" and loaded["shop"].port == "5433"
    assert loaded["shop"].needs_secret and loaded["shop"].secret_label == "Password"
    assert not loaded["quiet"].needs_secret, "libpq finds the password where psql does"
    text = path.read_text()
    assert "kind: postgres" in text and "auth: password" not in text, "the default auth is left out"
    assert "password:" not in text


def test_the_provider_is_listed_and_its_adapter_is_the_module_named_after_it():
    p = registry.PROVIDERS["postgres"]
    assert p.label == "PostgreSQL" and p.levels == ("database", "schema")
    assert p.summary(_pg()) == "jane@localhost/shop"
    assert module_for("postgres") is postgres
    built = postgres.backend(_pg(schema="raw"))
    assert built.kind == "postgres" and built.remote and built.dialect.name == "postgres"
    assert built.opens_on == "shop.raw"


def test_what_is_sent_at_sign_in_and_that_a_password_file_sends_no_password():
    sent = postgres.login_fields(_pg(port="5433", sslmode="require"), "s3cret")
    assert sent == {
        "host": "localhost",
        "port": 5433,
        "dbname": "shop",
        "user": "jane",
        "application_name": "portia",
        "connect_timeout": postgres.CONNECT_TIMEOUT,
        "password": "s3cret",
        "sslmode": "require",
    }
    quiet = postgres.login_fields(_pg(auth=registry.PGPASS), "ignored")
    assert "password" not in quiet and quiet["port"] == 5432 and "sslmode" not in quiet


def test_the_dialect_is_picked_by_the_servers_version():
    assert postgres.dialect_for(160004) is dialect.POSTGRES
    assert postgres.dialect_for(180000) is dialect.POSTGRES
    assert postgres.dialect_for(150008) is dialect.POSTGRES_BEFORE_16
    assert postgres.OLDEST < postgres.EXACT_CAST_FROM


def test_libpqs_five_line_failure_is_one_sentence():
    wrong_password = (
        'connection failed: connection to server at "::1", port 5432 failed: FATAL:  '
        'password authentication failed for user "jane"\n'
        "Multiple connection attempts failed. All failures were:\n- host: 'localhost' …"
    )
    assert postgres.plain_failure(wrong_password, "localhost", 5432) == (
        'password authentication failed for user "jane"'
    )
    nobody_home = (
        'connection failed: connection to server at "::1", port 5432 failed: '
        "could not receive data from server: Connection refused\nMultiple connection …"
    )
    assert postgres.plain_failure(nobody_home, "localhost", 5432) == (
        "nothing is listening at localhost:5432. Is the PostgreSQL server running?"
    )


def test_libpqs_service_file_is_read_for_suggestions_and_its_password_is_dropped(tmp_path):
    services = tmp_path / "pg_service.conf"
    services.write_text(
        "[warehouse]\nhost=db.acme.com\nport=5433\ndbname=analytics\nuser=jane\n"
        "password=hunter2\nsslmode=require\n"
    )
    (found,) = registry.postgres_suggestions(services)
    assert found.name == "warehouse" and found.kind == "postgres"
    assert found.fields == {
        "host": "db.acme.com",
        "port": "5433",
        "database": "analytics",
        "user": "jane",
        "sslmode": "require",
    }
    assert registry.postgres_suggestions(tmp_path / "none") == []


def test_a_missing_driver_is_one_sentence_at_the_edge(monkeypatch):
    import builtins

    real = builtins.__import__

    def without(name, *args, **kwargs):
        if name == "psycopg":
            raise ImportError(name)
        return real(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without)
    with pytest.raises(postgres.ConnectorMissing, match="uv sync --extra postgres"):
        postgres.open_raw(_pg(), "x")


def test_an_estimated_row_count_says_so_wherever_it_is_shown():
    estimated = {"rows": 5000, "approximate": ["rows"]}
    assert catalog.rows_estimated(estimated) and not catalog.rows_estimated({"rows": 5000})
    assert not catalog.rows_estimated(None)
    assert catalog._metadata_summary(estimated, 5).startswith("about 5000 rows, 5 columns.")
    assert catalog._metadata_summary({"rows": 3}, 2).startswith("3 rows, 2 columns.")


def test_every_provider_has_its_mark_so_the_window_never_draws_a_blank():
    """The card and the dialog draw a mark per provider, off a file and a CSS class named for it."""
    from pathlib import Path

    import portia.ui

    assets = Path(portia.ui.__file__).parent / "assets"
    css = (assets / "portia.css").read_text()
    for kind in registry.PROVIDERS:
        assert (assets / "connectors" / f"{kind}.svg").is_file(), kind
        assert f".connector-glyph-{kind} " in css, kind

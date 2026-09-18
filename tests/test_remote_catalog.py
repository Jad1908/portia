"""A warehouse table in the catalog (`docs/CONNECTOR.md` §2.5, §2.6, §2.11, §2.12).

Driven on DuckDB wearing a remote badge: `_Warehouse` answers the two metadata
questions the catalog asks a session — columns and table facts — off DuckDB's
own information schema, so nothing here decides anything a real session would
decide differently, and a real query still runs behind every profile.
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd
import pytest
import yaml

from portia import catalog, pipeline, spec
from portia.agent import handlers
from portia.core import backend, dialect
from portia.core.table import Table


class _Warehouse:
    """DuckDB, claiming to be remote, answering the catalog's metadata questions."""

    remote = True
    dialect = dialect.DUCKDB

    def __init__(self, inner):
        self._inner = inner
        self.scans = 0

    def execute(self, sql):
        if "count(" in sql.lower() and "information_schema" not in sql.lower():
            self.scans += 1
        return self._inner.execute(sql)

    def sql(self, query):
        return self._inner.sql(query)

    def cursor(self):
        twin = _Warehouse(self._inner.cursor())
        return twin

    def interrupt(self):
        self._inner.interrupt()

    def close(self):
        pass

    def columns(self, qualified):
        _, schema, table = qualified.split(".")
        rows = self._inner.execute(
            "SELECT column_name, data_type FROM information_schema.columns "
            f"WHERE table_schema = '{schema}' AND table_name = '{table}' ORDER BY ordinal_position"
        ).fetchall()
        return [(str(n), str(t)) for n, t in rows]

    def table_facts(self, qualified):
        _, schema, table = qualified.split(".")
        (rows,) = self._inner.execute(f"SELECT count(*) FROM {schema}.{table}").fetchone()
        return {
            "size": rows * 10,
            "mtime": "2026-09-04T10:00:00",
            "rows": rows,
            "kind": "table",
            "at": datetime.now().astimezone().isoformat(timespec="seconds"),
        }


@pytest.fixture
def warehouse(con):
    con.execute("CREATE SCHEMA sales")
    Table.from_frame(
        pd.DataFrame({"id": [1, 2, 3], "country": ["FR", "DE", "FR"], "amount": [1.5, 2.5, 3.5]}),
        "orders",
        con,
    )
    con.execute("CREATE TABLE sales.orders AS SELECT * FROM orders")
    con.execute("CREATE TABLE sales.customers AS SELECT 1 AS id, 'a' AS name")
    return _Warehouse(con)


@pytest.fixture
def project(tmp_path):
    d = tmp_path / ".portia"
    catalog.init_project("a warehouse project", portia_dir=d)
    catalog.set_connection("prod", portia_dir=d)
    return d


# --- scoping is metadata, profiling is opt-in ----------------------------------


def test_scoping_a_table_records_metadata_and_scans_nothing(project, warehouse):
    path = catalog.scope_table("memory.sales.orders", warehouse, portia_dir=project)
    entry = yaml.safe_load(path.read_text())

    assert entry["source"] == "memory.sales.orders"
    assert entry["remote"] == {
        "database": "memory",
        "schema": "sales",
        "table": "orders",
        "kind": "table",
    }
    assert entry["indexed"]["rows"] == 3 and set(catalog.STALENESS_FACTS) <= set(entry["indexed"])
    assert entry["profiled"] is None and not catalog.is_profiled(entry)
    assert [c["name"] for c in entry["columns"]] == ["id", "country", "amount"]
    assert "inferred" not in entry["columns"][0] and entry["columns"][0]["dtype"]
    assert warehouse.scans == 0, "scoping must not scan the table"
    assert "Not profiled" in entry["summary"] and not catalog.is_interpreted(entry)

    proj = yaml.safe_load((project / "project.yaml").read_text())
    assert proj["scope"] == ["memory.sales.orders"]
    assert proj["sources"]["orders"] == "sources/orders.yaml"


def test_profiling_a_scoped_table_writes_the_facts_back_and_keeps_judgment(project, warehouse):
    catalog.scope_table("memory.sales.orders", warehouse, portia_dir=project)
    catalog.set_interpretation(
        "orders", summary="Orders, one row each.", roles={"id": "key"}, portia_dir=project
    )

    profile = catalog.profile_remote("orders", warehouse, portia_dir=project)

    assert profile["n_rows"] == 3 and profile["source"] == "memory.sales.orders"
    entry = yaml.safe_load((project / "sources" / "orders.yaml").read_text())
    assert entry["profiled"]["at"] and catalog.is_profiled(entry)
    assert entry["remote"]["table"] == "orders"
    assert entry["summary"] == "Orders, one row each."
    by_name = {c["name"]: c for c in entry["columns"]}
    assert by_name["id"]["role"] == "key" and by_name["id"]["n_distinct"] == 3
    assert by_name["country"]["top"] == "FR" and by_name["amount"]["dtype"]
    assert list(entry) == ["source", "remote", "indexed", "profiled", "summary", "columns"]


def test_re_scoping_keeps_measured_facts_and_refreshes_metadata(project, warehouse):
    catalog.scope_table("memory.sales.orders", warehouse, portia_dir=project)
    catalog.profile_remote("orders", warehouse, portia_dir=project)
    catalog.scope_table("memory.sales.orders", warehouse, portia_dir=project)
    entry = yaml.safe_load((project / "sources" / "orders.yaml").read_text())
    assert entry["profiled"] and {c["name"]: c for c in entry["columns"]}["id"]["n_distinct"] == 3


def test_profile_remote_refuses_a_file_source(project, tmp_path):
    csv = tmp_path / "f.csv"
    csv.write_text("a\n1\n")
    catalog.index_source(csv, portia_dir=project)
    with pytest.raises(ValueError, match="file source"):
        catalog.profile_remote("f", None, portia_dir=project)


# --- the agent's rungs on a scoped table ------------------------------------------


def test_describe_source_says_not_profiled_as_a_field(project, warehouse, monkeypatch):
    catalog.scope_table("memory.sales.orders", warehouse, portia_dir=project)
    monkeypatch.chdir(project.parent)
    described = handlers.describe_source("orders", portia_dir=str(project))
    assert described["profiled"] is False and described["n_rows"] == 3
    assert described["columns"][0]["dtype"] and "inferred" not in described["columns"][0]


def test_profile_source_on_a_scoped_table_scans_once_and_remembers(project, warehouse, monkeypatch):
    catalog.scope_table("memory.sales.orders", warehouse, portia_dir=project)
    monkeypatch.chdir(project.parent)
    fake = backend.Backend(kind="test", dialect=dialect.DUCKDB, open=lambda: warehouse, remote=True)
    with backend.using(fake):
        answer = handlers.profile_source("orders", portia_dir=str(project))
    assert answer["n_rows"] == 3 and answer["columns"][1]["top"] == "FR"
    described = handlers.describe_source("orders", portia_dir=str(project))
    assert "profiled" not in described, "once profiled, the field is not repeated"


def test_a_spec_records_a_scoped_source_as_a_table_ref(project, warehouse):
    catalog.scope_table("memory.sales.orders", warehouse, portia_dir=project)
    entry = catalog.load_catalog(project)["sources"]["orders"]
    assert catalog.source_ref_of(entry) == {"table": "memory.sales.orders"}


# --- staleness keeps its keys (§2.11) -------------------------------------------


def test_a_remote_entry_is_never_stale_on_a_render_and_changed_on_request(project, warehouse):
    catalog.scope_table("memory.sales.orders", warehouse, portia_dir=project)
    entry = catalog.load_catalog(project)["sources"]["orders"]
    assert catalog.is_stale(entry, portia_dir=project) is False
    assert catalog.remote_changed(entry, warehouse) is False
    warehouse._inner.execute("INSERT INTO sales.orders VALUES (4, 'IT', 9.0)")
    assert catalog.remote_changed(entry, warehouse) is True


# --- names collide (§2.12) --------------------------------------------------------


def test_two_tables_with_one_name_get_their_schema_prepended(project, warehouse):
    warehouse._inner.execute("CREATE SCHEMA crm")
    warehouse._inner.execute("CREATE TABLE crm.orders AS SELECT 7 AS id")
    catalog.scope_table("memory.sales.orders", warehouse, portia_dir=project)
    catalog.scope_table("memory.crm.orders", warehouse, portia_dir=project)
    names = set(catalog.load_catalog(project)["sources"])
    assert names == {"orders", "crm__orders"}
    # Scoping the first again keeps its short name.
    assert catalog.source_name("memory.sales.orders", portia_dir=project) == "orders"


def test_two_files_with_one_stem_get_their_folder_prepended(tmp_path):
    d = tmp_path / ".portia"
    for folder in ("raw", "clean"):
        (tmp_path / folder).mkdir()
        (tmp_path / folder / "orders.csv").write_text("a\n1\n")
    catalog.index_source(tmp_path / "raw" / "orders.csv", portia_dir=d)
    catalog.index_source(tmp_path / "clean" / "orders.csv", portia_dir=d)
    assert set(catalog.load_catalog(d)["sources"]) == {"orders", "clean__orders"}
    catalog.index_source(tmp_path / "raw" / "orders.csv", portia_dir=d)
    assert set(catalog.load_catalog(d)["sources"]) == {"orders", "clean__orders"}


def test_removing_a_scoped_table_leaves_the_scope(project, warehouse):
    catalog.scope_table("memory.sales.orders", warehouse, portia_dir=project)
    catalog.scope_table("memory.sales.customers", warehouse, portia_dir=project)
    catalog.remove_source("orders", portia_dir=project)
    assert catalog.project_settings(project)["scope"] == ["memory.sales.customers"]


# --- Build writes where each spec says (§2.7, §2.7.1) ------------------------------------------


def _spec_dir(project, warehouse):
    root = project.parent
    catalog.scope_table("memory.sales.orders", warehouse, portia_dir=project)
    doc = {
        "version": 1,
        "target": "memory.sales",
        "sources": {"orders": {"table": "memory.sales.orders"}},
        "steps": [
            {
                "id": "fr",
                "op": "sql",
                "inputs": ["orders"],
                "sql": "SELECT * FROM orders WHERE country = 'FR'",
            }
        ],
    }
    (root / "specs").mkdir()
    spec.save_spec(doc, root / "specs" / "fr_orders.yaml")
    return root


def test_a_remote_build_creates_the_table_in_the_target(project, warehouse):
    root = _spec_dir(project, warehouse)
    fake = backend.Backend(kind="test", dialect=dialect.DUCKDB, open=lambda: warehouse, remote=True)
    with backend.using(fake):
        built = pipeline.build_project(root)
    assert built[0].written_to == "memory.sales.fr_orders"
    assert warehouse._inner.execute("SELECT count(*) FROM sales.fr_orders").fetchone() == (2,)
    # The compiled file is untouched by where the table went.
    assert 'CREATE TABLE "fr_orders" AS' in built[0].sql_path.read_text()
    # And the model was indexed as shape only — no scan on the meter.
    model = yaml.safe_load((project / "models" / "fr_orders.yaml").read_text())
    assert model["profiled"] is None and model["columns"][0]["dtype"]


def test_each_spec_names_where_its_table_goes_and_the_build_creates_the_schema(project, warehouse):
    """`CONNECTOR.md` §2.7.1, the user's call: different tables go different
    places. The target is the spec's own; a schema it names that does not exist
    is created on the way; no project setting is consulted."""
    root = _spec_dir(project, warehouse)
    doc = spec.load_spec(root / "specs" / "fr_orders.yaml")
    (root / "specs" / "fr_orders.yaml").unlink()
    placed = spec.spec_path("fr_orders", layer="staging", root=root)
    placed.parent.mkdir(parents=True)
    spec.save_spec({**doc, "layer": "staging", "target": "memory.staging"}, placed)
    fake = backend.Backend(kind="test", dialect=dialect.DUCKDB, open=lambda: warehouse, remote=True)
    with backend.using(fake):
        built = pipeline.build_project(root)
    assert built[0].written_to == "memory.staging.fr_orders"
    assert warehouse._inner.execute("SELECT count(*) FROM staging.fr_orders").fetchone() == (2,)


def test_the_table_s_name_is_spelled_as_the_engine_folds_it():
    """On Snowflake a table created quoted lower case must be quoted forever; the
    parts are folded first, so ``stg_chicago`` is the ``STG_CHICAGO`` the team
    reaches unquoted, and a reserved word as a model name still works (quoted)."""
    assert pipeline.table_home("portia_test.staging", dialect.SNOWFLAKE) == [
        "PORTIA_TEST",
        "STAGING",
    ]
    assert pipeline.table_home("memory.sales", dialect.DUCKDB) == ["memory", "sales"]
    with pytest.raises(ValueError, match="DATABASE.SCHEMA"):
        pipeline.table_home("just_a_database", dialect.DUCKDB)


def _hand_off(warehouse, *, on: bool):
    return backend.Backend(
        kind="test", dialect=dialect.DUCKDB, open=lambda: warehouse, remote=True, agent_writes=on
    )


def test_on_the_hand_off_recording_a_step_creates_the_table_where_the_spec_says(project, warehouse):
    """§2.7.1: with `agent_writes`, `record_step` writes the model's table the
    moment the step is measured — after the gate, after the spec is saved — in the
    target the agent chose for that spec. Off, it creates nothing, as ever."""
    root = _spec_dir(project, warehouse)
    (root / "specs" / "fr_orders.yaml").unlink()
    step = {
        "id": "fr",
        "op": "sql",
        "inputs": ["orders"],
        "sql": "SELECT * FROM orders WHERE country = 'FR'",
    }
    with backend.using(_hand_off(warehouse, on=False)):
        out = handlers.record_step(
            "specs/fr_orders.yaml", step, target="memory.staging", portia_dir=str(project)
        )
    assert out["written_to"] is None and out["target"] == "memory.staging"
    assert spec.load_spec(root / "specs" / "fr_orders.yaml")["target"] == "memory.staging"
    assert not warehouse._inner.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name = 'fr_orders'"
    ).fetchone()[0]

    with backend.using(_hand_off(warehouse, on=True)):
        out = handlers.record_step(
            "specs/fr_orders.yaml",
            {**step, "sql": "SELECT * FROM orders WHERE country = 'DE'"},
            supersedes="fr",
            portia_dir=str(project),
        )
    assert out["written_to"] == "memory.staging.fr_orders"
    assert warehouse._inner.execute("SELECT count(*) FROM staging.fr_orders").fetchone() == (1,)


def test_a_written_table_is_on_the_model_entry_and_a_read_of_the_model_goes_there(
    project, warehouse
):
    """`CONNECTOR.md` §2.7.2: the entry says where the table is and which spec
    built it; profiling the model then scans that table rather than re-running
    the spec. Decisive check: with the source table gone, the profile still
    answers, because it never touched the source."""
    root = _spec_dir(project, warehouse)
    (root / "specs" / "fr_orders.yaml").unlink()
    step = {
        "id": "fr",
        "op": "sql",
        "inputs": ["orders"],
        "sql": "SELECT * FROM orders WHERE country = 'FR'",
    }
    with backend.using(_hand_off(warehouse, on=True)):
        handlers.record_step(
            "specs/fr_orders.yaml", step, target="memory.staging", portia_dir=str(project)
        )
        entry = catalog.load_models(project)["fr_orders"]
        assert entry["table"] == "memory.staging.fr_orders"
        assert entry["fingerprint"] == pipeline.fingerprint(
            spec.load_spec(root / "specs" / "fr_orders.yaml")
        )
        warehouse._inner.execute("DROP TABLE sales.orders")
        profile = handlers.profile_source("fr_orders", str(project))
        assert profile["n_rows"] == 2
        # And typed as the warehouse shows it, the name still finds the model.
        described = handlers.describe_source("FR_ORDERS", str(project))
        assert [c["name"] for c in described["columns"]] == ["id", "country", "amount"]


def test_a_build_that_wrote_nothing_leaves_no_table_on_the_entry(project, warehouse):
    root = _spec_dir(project, warehouse)
    (root / "specs" / "fr_orders.yaml").unlink()
    step = {"id": "fr", "op": "sql", "inputs": ["orders"], "sql": "SELECT * FROM orders"}
    with backend.using(_hand_off(warehouse, on=True)):
        handlers.record_step(
            "specs/fr_orders.yaml", step, target="memory.staging", portia_dir=str(project)
        )
    assert "table" in catalog.load_models(project)["fr_orders"]
    with backend.using(_hand_off(warehouse, on=False)):
        handlers.record_step(
            "specs/fr_orders.yaml",
            {**step, "sql": "SELECT * FROM orders WHERE country = 'DE'"},
            supersedes="fr",
            portia_dir=str(project),
        )
    entry = catalog.load_models(project)["fr_orders"]
    assert "table" not in entry and "fingerprint" not in entry


def test_a_spec_keeps_one_target_and_a_different_one_is_refused(project, warehouse):
    root = _spec_dir(project, warehouse)
    step = {
        "id": "de",
        "op": "sql",
        "inputs": ["orders"],
        "sql": "SELECT * FROM orders WHERE country = 'DE'",
    }
    with backend.using(_hand_off(warehouse, on=False)):
        handlers.record_step(
            "specs/fr_orders.yaml", step, target="memory.sales", portia_dir=str(project)
        )
        with pytest.raises(ValueError, match="already writes to 'memory.sales'"):
            handlers.record_step(
                "specs/fr_orders.yaml",
                {**step, "id": "it"},
                target="memory.other",
                portia_dir=str(project),
            )
        with pytest.raises(ValueError, match="DATABASE.SCHEMA"):
            handlers.record_step(
                "specs/fr_orders.yaml",
                {**step, "id": "it"},
                target="justadb",
                portia_dir=str(project),
            )
    assert root


def test_on_the_hand_off_a_step_a_zero_refuses_never_reaches_the_warehouse(project, warehouse):
    root = _spec_dir(project, warehouse)
    (root / "specs" / "fr_orders.yaml").unlink()
    step = {
        "id": "none",
        "op": "sql",
        "inputs": ["orders"],
        "sql": "SELECT * FROM orders WHERE 1 = 0",
    }
    with (
        backend.using(_hand_off(warehouse, on=True)),
        pytest.raises(ValueError, match="empty_output"),
    ):
        handlers.record_step(
            "specs/nothing.yaml", step, target="memory.sales", portia_dir=str(project)
        )
    assert not warehouse._inner.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name = 'nothing'"
    ).fetchone()[0]


def test_on_the_hand_off_a_spec_without_a_target_is_refused_naming_the_field(project, warehouse):
    root = _spec_dir(project, warehouse)
    (root / "specs" / "fr_orders.yaml").unlink()
    step = {"id": "fr", "op": "sql", "inputs": ["orders"], "sql": "SELECT * FROM orders"}
    with (
        backend.using(_hand_off(warehouse, on=True)),
        pytest.raises(ValueError, match="record_step\\(target="),
    ):
        handlers.record_step("specs/fr_orders.yaml", step, portia_dir=str(project))
    assert root


def test_a_remote_build_refuses_a_spec_that_says_nowhere_to_write_before_building(
    project, warehouse
):
    root = _spec_dir(project, warehouse)
    path = root / "specs" / "fr_orders.yaml"
    doc = spec.load_spec(path)
    doc.pop("target")
    spec.save_spec(doc, path)
    fake = backend.Backend(kind="test", dialect=dialect.DUCKDB, open=lambda: warehouse, remote=True)
    with (
        backend.using(fake),
        pytest.raises(ValueError, match="Nothing was built: fr_orders.*nowhere"),
    ):
        pipeline.build_project(root)
    assert not (root / "models").exists()


def test_a_local_build_writes_nowhere_remote(project, warehouse):
    """The same table ref on a local backend: measured, compiled, and no table created."""
    root = _spec_dir(project, warehouse)
    local = backend.Backend(kind="duckdb", dialect=dialect.DUCKDB, open=lambda: warehouse._inner)
    with backend.using(local):
        built = pipeline.build_project(root)
    assert built[0].written_to is None
    assert not warehouse._inner.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name = 'fr_orders'"
    ).fetchone()[0]


def test_a_name_that_differs_only_by_case_still_collides(project, warehouse, tmp_path):
    """macOS: `ORDERS.yaml` is `orders.yaml`. Found driving the window."""
    csv = tmp_path / "orders.csv"
    csv.write_text("a\n1\n")
    catalog.index_source(csv, portia_dir=project)
    warehouse._inner.execute("CREATE SCHEMA up")
    warehouse._inner.execute('CREATE TABLE up."ORDERS" AS SELECT 1 AS id')
    assert catalog.source_name("memory.up.ORDERS", portia_dir=project) == "up__ORDERS"


# --- the graph walk on a warehouse spec (the white middle pane, 2026-09-04) --------


def test_the_lineage_walk_reads_through_a_table_source(project, warehouse):
    """A `{table: …}` source reached `Ref` as a dict and raised a TypeError that
    blanked the app's middle pane on the first card click of a warehouse project."""
    from portia import knowledge
    from portia.knowledge import schema

    root = _spec_dir(project, warehouse)
    result = knowledge.build_graph(root, portia_dir=project)
    reads = result.graph.edges_of(schema.READS)
    assert any(e.end.key == "memory.sales.orders" for e in reads)
    assert "fr_orders" not in result.unresolved


def test_a_note_survives_a_rescope_and_a_profile(project, warehouse):
    """Judgment is copied over by name when an entry is rebuilt; a note is judgment."""
    catalog.scope_table("memory.sales.orders", warehouse, portia_dir=project)
    catalog.set_interpretation("orders", note="amount is net of tax", portia_dir=project)

    catalog.scope_table("memory.sales.orders", warehouse, portia_dir=project)
    entry = yaml.safe_load((project / "sources" / "orders.yaml").read_text())
    assert [n["text"] for n in entry["notes"]] == ["amount is net of tax"]

    catalog.profile_remote("orders", warehouse, portia_dir=project)
    entry = yaml.safe_load((project / "sources" / "orders.yaml").read_text())
    assert [n["text"] for n in entry["notes"]] == ["amount is net of tax"]
    assert list(entry)[-1] == "notes", "the margin stays after the facts"


# --- a built table is a source for every read (2026-09-07) ---------------------------


def _built_fr_orders(project, warehouse):
    root = _spec_dir(project, warehouse)
    (root / "specs" / "fr_orders.yaml").unlink()
    step = {
        "id": "fr",
        "op": "sql",
        "inputs": ["orders"],
        "sql": "SELECT * FROM orders WHERE country = 'FR'",
    }
    with backend.using(_hand_off(warehouse, on=True)):
        handlers.record_step(
            "specs/fr_orders.yaml", step, target="memory.staging", portia_dir=str(project)
        )
    return root


def test_a_warehouse_build_indexes_a_model_as_metadata_that_says_so(project, warehouse):
    """The seven BigQuery models that read as twelve columns of nothing: a
    metadata-only model entry now says `profiled: False` to the agent, and
    carries the warehouse's free facts, as a scoped source does."""
    _built_fr_orders(project, warehouse)
    entry = catalog.load_models(project)["fr_orders"]
    assert entry["profiled"] is None and not catalog.is_profiled(entry)
    assert entry["indexed"]["rows"] == 2 and set(catalog.STALENESS_FACTS) <= set(entry["indexed"])
    assert list(entry)[:2] == ["model", "built"] and entry["table"] == "memory.staging.fr_orders"
    with backend.using(_hand_off(warehouse, on=True)):
        described = handlers.describe_source("fr_orders", str(project))
    assert described["profiled"] is False and described["n_rows"] == 2
    assert described["columns"][0]["dtype"] and "inferred" not in described["columns"][0]


def test_profiling_a_built_table_writes_the_facts_back_and_keeps_everything_else(
    project, warehouse
):
    """`profile_remote` takes a model's name: the scan runs on the written table,
    the facts land on the entry, and the read, the notes, the table and the
    fingerprint survive it. Decisive check: the source table is gone."""
    _built_fr_orders(project, warehouse)
    catalog.set_interpretation(
        "fr_orders",
        summary="French orders.",
        roles={"id": "key"},
        note="two rows",
        portia_dir=project,
    )
    before = catalog.load_models(project)["fr_orders"]
    warehouse._inner.execute("DROP TABLE sales.orders")

    profile = catalog.profile_remote("fr_orders", warehouse, portia_dir=project)

    assert profile["n_rows"] == 2 and profile["source"] == "memory.staging.fr_orders"
    entry = catalog.load_models(project)["fr_orders"]
    assert entry["profiled"]["at"] and catalog.is_profiled(entry)
    assert entry["summary"] == "French orders." and entry["notes"][0]["text"] == "two rows"
    assert entry["table"] == before["table"] and entry["fingerprint"] == before["fingerprint"]
    assert entry["built"] == before["built"]
    by_name = {c["name"]: c for c in entry["columns"]}
    assert by_name["id"]["role"] == "key" and by_name["id"]["n_distinct"] == 2
    assert by_name["country"]["top"] == "FR"


def test_the_agents_profile_of_a_built_table_lands_in_the_catalog(project, warehouse):
    """`profile_source` on a model used to scan the written table and throw the
    facts away, so every audit paid again and the window never saw a number."""
    _built_fr_orders(project, warehouse)
    with backend.using(_hand_off(warehouse, on=True)):
        scans = warehouse.scans
        profile = handlers.profile_source("fr_orders", str(project))
        assert profile["n_rows"] == 2 and warehouse.scans > scans
        entry = catalog.load_models(project)["fr_orders"]
        assert catalog.is_profiled(entry)
        described = handlers.describe_source("fr_orders", str(project))
    assert "profiled" not in described and described["columns"][0]["inferred"]


def test_a_table_the_project_built_is_known_by_its_model_and_never_scoped_twice(project, warehouse):
    _built_fr_orders(project, warehouse)
    assert catalog.written_tables(project) == {"memory.staging.fr_orders": "fr_orders"}
    with pytest.raises(ValueError, match="builds"):
        catalog.scope_table("memory.staging.fr_orders", warehouse, portia_dir=project)
    # And another table called the same thing elsewhere does not take the model's name.
    warehouse._inner.execute("CREATE SCHEMA other")
    warehouse._inner.execute("CREATE TABLE other.fr_orders AS SELECT 1 AS id")
    assert catalog.source_name("memory.other.fr_orders", portia_dir=project) == "other__fr_orders"


def test_profile_remote_says_when_a_model_was_never_written(project, warehouse):
    root = _spec_dir(project, warehouse)
    (root / "specs" / "fr_orders.yaml").unlink()
    with backend.using(_hand_off(warehouse, on=False)):
        handlers.record_step(
            "specs/fr_orders.yaml",
            {"id": "fr", "op": "sql", "inputs": ["orders"], "sql": "SELECT * FROM orders"},
            target="memory.staging",
            portia_dir=str(project),
        )
    with pytest.raises(ValueError, match="not written"):
        catalog.profile_remote("fr_orders", warehouse, portia_dir=project)
    with pytest.raises(ValueError, match="no indexed source or built model"):
        catalog.profile_remote("nothing", warehouse, portia_dir=project)

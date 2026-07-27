"""The catalog indexes sources and preserves human judgment across re-index."""

import time

import pytest
import yaml

from portia.catalog import (
    index_source,
    init_project,
    is_stale,
    load_catalog,
    remove_source,
    set_group,
    set_interpretation,
)
from portia.core import store
from portia.fixtures import messy_customers


def _write_source(tmp_path):
    csv = tmp_path / "customers.csv"
    messy_customers().to_csv(csv, index=False)
    return csv


def test_init_project_stores_context(tmp_path):
    d = tmp_path / ".portia"
    init_project("we run EU events and reconcile vendor data", portia_dir=d)
    proj = yaml.safe_load((d / "project.yaml").read_text())
    assert proj["project"].startswith("we run EU events")
    assert proj["groups"] == [] and proj["sources"] == {}


def test_index_source_builds_two_layer_entry(tmp_path):
    csv = _write_source(tmp_path)
    d = tmp_path / ".portia"
    src_file = index_source(csv, portia_dir=d)
    entry = yaml.safe_load(src_file.read_text())

    # Layer 1: a prose summary (auto-drafted, mentions the key facts).
    assert "40 rows" in entry["summary"]
    assert "customer_id" in entry["summary"]  # candidate key surfaced
    # Layer 2: per-column detail with a role slot + facts.
    col = next(c for c in entry["columns"] if c["name"] == "signup_amount")
    assert col["role"] is None
    assert "numeric_stored_as_text" in col["flags"]
    # registered in the project file
    proj = yaml.safe_load((d / "project.yaml").read_text())
    assert proj["sources"]["customers"] == "sources/customers.yaml"


def test_reindex_preserves_judgment_refreshes_facts(tmp_path):
    csv = _write_source(tmp_path)
    d = tmp_path / ".portia"
    src_file = index_source(csv, portia_dir=d)

    # simulate user edits: a semantic summary + a column role
    data = yaml.safe_load(src_file.read_text())
    data["summary"] = "MY READ: the master EU customer list"
    next(c for c in data["columns"] if c["name"] == "customer_id")["role"] = "identifier"
    src_file.write_text(yaml.safe_dump(data, sort_keys=False))

    # re-index the same file
    index_source(csv, portia_dir=d)
    after = yaml.safe_load(src_file.read_text())

    assert after["summary"] == "MY READ: the master EU customer list"  # prose preserved
    cid = next(c for c in after["columns"] if c["name"] == "customer_id")
    assert cid["role"] == "identifier"  # role preserved
    assert "possible_key" in cid["flags"]  # facts still present/refreshed


def test_set_interpretation_writes_judgment_and_leaves_facts_alone(tmp_path):
    csv = _write_source(tmp_path)
    d = tmp_path / ".portia"
    src_file = index_source(csv, portia_dir=d)
    before = yaml.safe_load(src_file.read_text())

    set_interpretation(
        "customers",
        summary="The master EU customer list, one row per signup.",
        roles={"customer_id": "identifier", "signup_amount": "measure"},
        portia_dir=d,
    )
    after = yaml.safe_load(src_file.read_text())

    assert after["summary"] == "The master EU customer list, one row per signup."
    roles = {c["name"]: c["role"] for c in after["columns"]}
    assert roles["customer_id"] == "identifier"
    assert roles["signup_amount"] == "measure"

    # every fact is byte-identical — only `role` moved
    assert after["candidate_keys"] == before["candidate_keys"]
    for old, new in zip(before["columns"], after["columns"], strict=True):
        assert {k: v for k, v in old.items() if k != "role"} == {
            k: v for k, v in new.items() if k != "role"
        }


def test_set_interpretation_leaves_omitted_fields_untouched(tmp_path):
    csv = _write_source(tmp_path)
    d = tmp_path / ".portia"
    src_file = index_source(csv, portia_dir=d)

    set_interpretation("customers", summary="A first read.", portia_dir=d)
    set_interpretation("customers", roles={"customer_id": "identifier"}, portia_dir=d)
    after = yaml.safe_load(src_file.read_text())

    assert after["summary"] == "A first read."  # not clobbered by the roles-only call
    assert next(c for c in after["columns"] if c["name"] == "customer_id")["role"] == "identifier"


def test_set_interpretation_rejects_unknown_source_and_column(tmp_path):
    csv = _write_source(tmp_path)
    d = tmp_path / ".portia"
    index_source(csv, portia_dir=d)

    with pytest.raises(ValueError, match="no catalog entry"):
        set_interpretation("nope", summary="x", portia_dir=d)
    with pytest.raises(ValueError, match="no such column"):
        set_interpretation("customers", roles={"nope": "identifier"}, portia_dir=d)


def test_load_catalog_bundles_project_and_sources(tmp_path):
    csv = _write_source(tmp_path)
    d = tmp_path / ".portia"
    init_project("reconciliation project", portia_dir=d)
    index_source(csv, portia_dir=d)

    catalog = load_catalog(d)
    assert catalog["project"] == "reconciliation project"
    assert "customers" in catalog["sources"]
    assert catalog["sources"]["customers"]["candidate_keys"]


# --- forgetting a source -----------------------------------------------------


def test_removing_a_source_drops_its_entry_and_its_registration(tmp_path):
    frame = messy_customers()
    frame.to_csv(tmp_path / "customers.csv", index=False)
    d = tmp_path / ".portia"
    index_source(tmp_path / "customers.csv", portia_dir=d)

    remove_source("customers", portia_dir=d)

    assert not (d / "sources" / "customers.yaml").exists()
    assert load_catalog(d)["sources"] == {}


def test_removing_a_source_leaves_the_data_file_alone(tmp_path):
    """Un-indexing says "stop knowing about this", not "delete my CSV"."""
    csv = tmp_path / "customers.csv"
    messy_customers().to_csv(csv, index=False)
    d = tmp_path / ".portia"
    index_source(csv, portia_dir=d)

    remove_source("customers", portia_dir=d)

    assert csv.exists()


def test_removing_a_source_takes_it_out_of_its_groups(tmp_path):
    """A group listing a source that no longer exists is a broken reference."""
    for name in ("a", "b"):
        messy_customers().to_csv(tmp_path / f"{name}.csv", index=False)
        index_source(tmp_path / f"{name}.csv", portia_dir=tmp_path / ".portia")
    set_group("pair", sources=["a", "b"], portia_dir=tmp_path / ".portia")

    remove_source("a", portia_dir=tmp_path / ".portia")

    assert load_catalog(tmp_path / ".portia")["groups"][0]["sources"] == ["b"]


def test_removing_something_that_was_never_indexed_is_not_an_error(tmp_path):
    init_project("x", portia_dir=tmp_path / ".portia")
    assert remove_source("ghost", portia_dir=tmp_path / ".portia") is None


# --- the store: indexing ingests, un-indexing forgets ----------------------


def test_indexing_ingests_the_data_into_the_store(tmp_path):
    """Indexing is the moment the copy is made — eagerly, per §3 of the migration."""
    csv = _write_source(tmp_path)
    d = tmp_path / ".portia"
    index_source(csv, portia_dir=d)

    assert store.store_path(d).exists()
    con = store.connect(d)
    try:
        assert store.table(con, "customers").count() == 40
    finally:
        con.close()


def test_the_entry_records_what_was_ingested_and_when(tmp_path):
    """So a file that changed on disk afterwards is detectable, not silently stale."""
    csv = _write_source(tmp_path)
    entry = yaml.safe_load(index_source(csv, portia_dir=tmp_path / ".portia").read_text())

    assert entry["ingestion"]["size"] == csv.stat().st_size
    assert entry["ingestion"]["ingested_at"]
    assert not is_stale(entry)


def test_a_source_whose_file_changed_reads_as_stale(tmp_path):
    csv = _write_source(tmp_path)
    d = tmp_path / ".portia"
    index_source(csv, portia_dir=d)
    time.sleep(0.01)
    messy_customers(n=30).to_csv(csv, index=False)

    assert is_stale(yaml.safe_load((d / "sources" / "customers.yaml").read_text()))


def test_reindexing_refreshes_the_store_and_the_ingestion_record(tmp_path):
    """The update rule, extended: facts refresh, judgment survives."""
    csv = _write_source(tmp_path)
    d = tmp_path / ".portia"
    index_source(csv, portia_dir=d)
    set_interpretation("customers", summary="our CRM export", portia_dir=d)

    messy_customers(n=30).to_csv(csv, index=False)
    entry = yaml.safe_load(index_source(csv, portia_dir=d).read_text())

    assert entry["summary"] == "our CRM export"  # judgment preserved
    assert not is_stale(entry)  # fact refreshed
    con = store.connect(d)
    try:
        assert store.table(con, "customers").count() == 30
    finally:
        con.close()


def test_forgetting_a_source_drops_its_data_too(tmp_path):
    """The catalog stops knowing about it, so the copy it caused should go."""
    csv = _write_source(tmp_path)
    d = tmp_path / ".portia"
    index_source(csv, portia_dir=d)
    remove_source("customers", portia_dir=d)

    con = store.connect(d)
    try:
        assert not store.has(con, "customers")
    finally:
        con.close()
    assert csv.exists()  # the file itself is still not ours to delete

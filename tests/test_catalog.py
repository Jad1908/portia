"""The catalog indexes sources and preserves human judgment across re-index."""

import yaml

from portia.catalog import index_source, init_project, load_catalog
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


def test_load_catalog_bundles_project_and_sources(tmp_path):
    csv = _write_source(tmp_path)
    d = tmp_path / ".portia"
    init_project("reconciliation project", portia_dir=d)
    index_source(csv, portia_dir=d)

    catalog = load_catalog(d)
    assert catalog["project"] == "reconciliation project"
    assert "customers" in catalog["sources"]
    assert catalog["sources"]["customers"]["candidate_keys"]

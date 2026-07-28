"""The project store: ingest once, query many times, and know when it's stale."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pandas as pd
import pytest

from portia.core import store

MOCK = Path(__file__).resolve().parents[1] / "data" / "mock"


def test_ingest_makes_the_data_queryable(con):
    store.ingest(con, MOCK / "otb.csv")
    assert store.table_names(con) == ["otb"]
    assert store.table(con, "otb").count() == 14


def test_ingest_records_what_it_read(con):
    facts = store.ingest(con, MOCK / "hotels.csv")
    assert facts["size"] == (MOCK / "hotels.csv").stat().st_size
    assert facts["ingested_at"]


def test_ingest_replaces_a_previous_copy(con, tmp_path):
    path = tmp_path / "s.csv"
    pd.DataFrame({"a": [1, 2]}).to_csv(path, index=False)
    store.ingest(con, path)
    pd.DataFrame({"a": [1, 2, 3]}).to_csv(path, index=False)
    store.ingest(con, path)
    assert store.table(con, "s").count() == 3


def test_a_source_can_be_named_independently_of_its_file(con):
    store.ingest(con, MOCK / "otb.csv", name="bookings")
    assert store.table_names(con) == ["bookings"]


def test_asking_for_something_never_ingested_says_what_is_there(con):
    store.ingest(con, MOCK / "otb.csv")
    with pytest.raises(ValueError, match="not in the store"):
        store.table(con, "nope")


def test_forget_drops_the_data(con):
    store.ingest(con, MOCK / "otb.csv")
    store.forget(con, "otb")
    assert store.table_names(con) == []
    assert not store.has(con, "otb")


def test_forgetting_something_absent_is_not_an_error(con):
    store.forget(con, "never_here")


def test_the_store_lives_inside_the_catalog_directory(tmp_path):
    con = store.connect(tmp_path / ".portia")
    try:
        store.ingest(con, MOCK / "hotels.csv")
    finally:
        con.close()
    assert (tmp_path / ".portia" / "store.duckdb").exists()
    assert store.store_path(tmp_path / ".portia").name == "store.duckdb"


def test_a_path_with_a_quote_in_it_still_ingests(con, tmp_path):
    path = tmp_path / "jad's data.csv"
    pd.DataFrame({"a": [1]}).to_csv(path, index=False)
    store.ingest(con, path, name="quoted")
    assert store.table(con, "quoted").count() == 1


# --- staleness --------------------------------------------------------------


def test_a_freshly_ingested_source_is_not_stale(con, tmp_path):
    path = tmp_path / "s.csv"
    pd.DataFrame({"a": [1]}).to_csv(path, index=False)
    assert not store.is_stale(store.ingest(con, path), path)


def test_a_changed_file_is_stale(con, tmp_path):
    path = tmp_path / "s.csv"
    pd.DataFrame({"a": [1]}).to_csv(path, index=False)
    facts = store.ingest(con, path)
    time.sleep(0.01)
    pd.DataFrame({"a": [1, 2]}).to_csv(path, index=False)
    assert store.is_stale(facts, path)


def test_a_rewrite_of_the_same_length_is_still_stale(con, tmp_path):
    """Size alone would miss this, which is why mtime is recorded to the microsecond."""
    path = tmp_path / "s.csv"
    path.write_text("a\n1\n")
    facts = store.ingest(con, path)
    time.sleep(0.01)
    path.write_text("a\n2\n")
    os.utime(path, (facts["mtime"] + 1, facts["mtime"] + 1))
    assert store.is_stale(facts, path)


def test_a_deleted_file_is_stale(con, tmp_path):
    path = tmp_path / "s.csv"
    pd.DataFrame({"a": [1]}).to_csv(path, index=False)
    facts = store.ingest(con, path)
    path.unlink()
    assert store.is_stale(facts, path)


def test_nothing_recorded_makes_no_claim(con, tmp_path):
    assert not store.is_stale(None, tmp_path / "s.csv")
    assert not store.is_stale({"size": 1, "mtime": 1.0}, None)


# --- §6.3: typed ingest must not erase a flag -------------------------------


def test_typed_ingest_keeps_the_dirty_columns_as_text(con):
    """The decision behind typed ingest, pinned rather than trusted.

    Typed is smaller and faster, but a sniffer that typed a dirty column as
    BIGINT would erase a flag portia exists to raise. These are the columns whose
    text-ness *is* the finding: `signup_amount` holds numbers with stray
    whitespace and two non-numeric values, and `mixed_ref` holds both.
    """
    store.ingest(con, MOCK / "messy_customers.csv")
    dtypes = store.table(con, "messy_customers").dtypes
    assert dtypes["signup_amount"] == "VARCHAR"
    assert dtypes["mixed_ref"] == "VARCHAR"
    assert dtypes["customer_id"] == "BIGINT"  # a genuinely numeric key still types

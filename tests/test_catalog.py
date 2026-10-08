"""The catalog indexes sources and preserves human judgment across re-index."""

import os
import time
from pathlib import Path

import pytest
import yaml

from portia.catalog import (
    index_source,
    init_project,
    is_stale,
    load_catalog,
    remove_source,
    set_data_dir,
    set_group,
    set_interpretation,
)
from portia.fixtures import messy_customers


def _write_source(tmp_path):
    csv = tmp_path / "customers.csv"
    messy_customers().to_csv(csv, index=False)
    return csv


def test_init_project_stores_context(tmp_path):
    d = tmp_path / ".portia"
    init_project("we run EU events and reconcile vendor data", portia_dir=d)
    proj = yaml.safe_load((d / "project.yaml").read_text(encoding="utf-8"))
    assert proj["project"].startswith("we run EU events")
    assert proj["groups"] == [] and proj["sources"] == {}


def test_index_source_builds_two_layer_entry(tmp_path):
    csv = _write_source(tmp_path)
    d = tmp_path / ".portia"
    src_file = index_source(csv, portia_dir=d)
    entry = yaml.safe_load(src_file.read_text(encoding="utf-8"))

    # Layer 1: a prose summary (auto-drafted, mentions the key facts).
    assert "40 rows" in entry["summary"]
    assert "8 columns" in entry["summary"]  # shape, not a nominated key
    # Layer 2: per-column detail with a role slot + facts.
    col = next(c for c in entry["columns"] if c["name"] == "signup_amount")
    assert col["role"] is None
    assert "numeric_stored_as_text" in col["flags"]
    # registered in the project file
    proj = yaml.safe_load((d / "project.yaml").read_text(encoding="utf-8"))
    assert proj["sources"]["customers"] == "sources/customers.yaml"


def test_reindex_preserves_judgment_refreshes_facts(tmp_path):
    csv = _write_source(tmp_path)
    d = tmp_path / ".portia"
    src_file = index_source(csv, portia_dir=d)

    # simulate user edits: a semantic summary + a column role
    data = yaml.safe_load(src_file.read_text(encoding="utf-8"))
    data["summary"] = "MY READ: the master EU customer list"
    next(c for c in data["columns"] if c["name"] == "customer_id")["role"] = "identifier"
    src_file.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    # re-index the same file
    index_source(csv, portia_dir=d)
    after = yaml.safe_load(src_file.read_text(encoding="utf-8"))

    assert after["summary"] == "MY READ: the master EU customer list"  # prose preserved
    cid = next(c for c in after["columns"] if c["name"] == "customer_id")
    assert cid["role"] == "identifier"  # role preserved
    assert cid["n_distinct"] and cid["null_rate"] == 0.0  # facts still refreshed


def test_set_interpretation_writes_judgment_and_leaves_facts_alone(tmp_path):
    csv = _write_source(tmp_path)
    d = tmp_path / ".portia"
    src_file = index_source(csv, portia_dir=d)
    before = yaml.safe_load(src_file.read_text(encoding="utf-8"))

    set_interpretation(
        "customers",
        summary="The master EU customer list, one row per signup.",
        roles={"customer_id": "identifier", "signup_amount": "measure"},
        portia_dir=d,
    )
    after = yaml.safe_load(src_file.read_text(encoding="utf-8"))

    assert after["summary"] == "The master EU customer list, one row per signup."
    roles = {c["name"]: c["role"] for c in after["columns"]}
    assert roles["customer_id"] == "identifier"
    assert roles["signup_amount"] == "measure"

    # every fact is byte-identical — only `role` moved
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
    after = yaml.safe_load(src_file.read_text(encoding="utf-8"))

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
    assert catalog["sources"]["customers"]["columns"]


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


# --- no copy: indexing reads the file where it is --------------------------


def test_indexing_copies_nothing(tmp_path):
    """`docs/PIPELINE.md` §2.7 — portia keeps no second copy of the user's data.

    This used to ingest into `.portia/store.duckdb` eagerly. Two things retired
    it: the hot paths went to the file anyway, and portia now sources only from
    inside the repo, where a hidden duplicate is a worse trade than a re-parse.
    """
    csv = _write_source(tmp_path)
    d = tmp_path / ".portia"
    index_source(csv, portia_dir=d)

    assert not (d / "store.duckdb").exists()
    assert [p.name for p in d.iterdir()] != []  # the catalog itself is still written


def test_the_entry_records_the_path_relative_to_the_project(tmp_path):
    """An absolute path pins a project to one laptop; the spec has to travel."""
    csv = _write_source(tmp_path)
    entry = yaml.safe_load(
        index_source(csv, portia_dir=tmp_path / ".portia").read_text(encoding="utf-8")
    )

    assert entry["source"] == "customers.csv"
    assert not Path(entry["source"]).is_absolute()


def test_indexing_refuses_a_file_outside_the_project(tmp_path):
    """Not a warning — an outside path is not an option (§2.7)."""
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    stray = outside / "customers.csv"
    messy_customers().to_csv(stray, index=False)
    project = tmp_path / "project"
    (project / ".portia").mkdir(parents=True)

    with pytest.raises(ValueError, match="outside this project"):
        index_source(stray, portia_dir=project / ".portia")


def test_the_entry_records_what_the_file_looked_like_when_indexed(tmp_path):
    """So a file that changed on disk afterwards is detectable, not silently stale."""
    csv = _write_source(tmp_path)
    entry = yaml.safe_load(
        index_source(csv, portia_dir=tmp_path / ".portia").read_text(encoding="utf-8")
    )

    assert entry["indexed"]["size"] == csv.stat().st_size
    assert entry["indexed"]["at"]
    assert not is_stale(entry, portia_dir=tmp_path / ".portia")


def test_a_source_whose_file_changed_reads_as_stale(tmp_path):
    csv = _write_source(tmp_path)
    d = tmp_path / ".portia"
    index_source(csv, portia_dir=d)
    time.sleep(0.01)
    messy_customers(n=30).to_csv(csv, index=False)

    entry = yaml.safe_load((d / "sources" / "customers.yaml").read_text(encoding="utf-8"))
    assert is_stale(entry, portia_dir=d)


def test_reindexing_refreshes_the_facts_and_keeps_the_judgment(tmp_path):
    """The update rule: facts refresh, judgment survives."""
    csv = _write_source(tmp_path)
    d = tmp_path / ".portia"
    index_source(csv, portia_dir=d)
    set_interpretation("customers", summary="our CRM export", portia_dir=d)

    messy_customers(n=30).to_csv(csv, index=False)
    entry = yaml.safe_load(index_source(csv, portia_dir=d).read_text(encoding="utf-8"))

    assert entry["summary"] == "our CRM export"  # judgment preserved
    assert not is_stale(entry, portia_dir=d)  # fact refreshed


def test_forgetting_a_source_leaves_the_file_alone(tmp_path):
    """Un-indexing is a statement about the catalog, never about someone's disk."""
    csv = _write_source(tmp_path)
    d = tmp_path / ".portia"
    index_source(csv, portia_dir=d)
    remove_source("customers", portia_dir=d)

    assert not (d / "sources" / "customers.yaml").exists()
    assert csv.exists()


# --- bringing outside data in ----------------------------------------------


def test_import_plans_the_copy_before_making_it(tmp_path):
    """The confirmation shows the real thing, and a clash is found before any bytes move."""
    from portia.cli.import_data import plan

    root = tmp_path / "project"
    (root / "data").mkdir(parents=True)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    stray = outside / "customers.csv"
    messy_customers().to_csv(stray, index=False)

    pairs = plan([stray], root / "data", root)
    assert pairs == [(stray, root / "data" / "customers.csv")]
    assert stray.exists()  # planning copies nothing

    (root / "data" / "customers.csv").write_text("already here", encoding="utf-8")
    with pytest.raises(ValueError, match="refusing to overwrite"):
        plan([stray], root / "data", root)


def test_import_refuses_a_destination_outside_the_project(tmp_path):
    from portia.cli.import_data import plan

    root = tmp_path / "project"
    root.mkdir()
    with pytest.raises(ValueError, match="must be inside the project"):
        plan([], tmp_path / "somewhere-else", root)


# --- the project's data folder ----------------------------------------------


def test_the_data_folder_is_recorded_beside_the_brief(tmp_path):
    """Which part of the repo is this project's data is a durable project fact,
    so it lives in `project.yaml` where it is read in a diff beside the brief."""
    d = tmp_path / ".portia"
    init_project("a project", portia_dir=d)

    set_data_dir("warehouse/raw", portia_dir=d)

    assert load_catalog(d)["data_dir"] == "warehouse/raw"
    assert load_catalog(d)["project"] == "a project", "the brief is untouched"


def test_an_unset_data_folder_reads_as_empty_not_missing(tmp_path):
    """Every project written before the field has none, and the honest answer for
    one nobody has told is "" — which the tree reads as the whole repo."""
    d = tmp_path / ".portia"
    init_project("a project", portia_dir=d)

    assert load_catalog(d)["data_dir"] == ""


def test_the_data_folder_is_stored_relative_and_stripped(tmp_path):
    """Relative like every other path portia writes, so the setting survives the
    project being cloned somewhere else."""
    d = tmp_path / ".portia"
    init_project("a project", portia_dir=d)

    set_data_dir("  data/raw/  ", portia_dir=d)

    assert load_catalog(d)["data_dir"] == "data/raw"


def test_setting_the_data_folder_does_not_disturb_the_indexed_sources(tmp_path, monkeypatch):
    """It is a scope, not a re-home: nothing moves and no entry is rewritten."""
    d = tmp_path / ".portia"
    monkeypatch.chdir(tmp_path)
    init_project("a project", portia_dir=d)
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "orders.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    index_source(tmp_path / "data" / "orders.csv", portia_dir=d)

    set_data_dir("data", portia_dir=d)

    entry = load_catalog(d)["sources"]["orders"]
    assert entry["source"] == "data/orders.csv"
    assert (tmp_path / "data" / "orders.csv").exists()


def test_an_untouched_file_does_not_go_stale_on_the_clock(tmp_path, monkeypatch):
    """`indexed` records *when we looked* beside the file's own facts, and for a
    while `is_stale` compared that too — so a source went stale one second after
    it was indexed, with an identical size and an identical mtime.

    Nothing user-facing read it yet, which is why it survived; the tests hid it
    because each finished inside the same wall-clock second as its own index.
    """
    monkeypatch.chdir(tmp_path)
    csv = _write_source(tmp_path)
    d = tmp_path / ".portia"
    entry = yaml.safe_load(index_source(csv, portia_dir=d).read_text(encoding="utf-8"))

    # The one thing that moves without the file moving.
    entry["indexed"]["at"] = "1999-01-01T00:00:00+00:00"

    assert not is_stale(entry, portia_dir=d)


# --- notes: what a chat learned, appended and never rewritten ----------------
#
# `docs/COPILOT.md` §2. The summary is one paragraph replaced whole; a note is
# one dated sentence kept in order, so what one chat learned about a table is
# in front of the next one before it builds on it.


def test_a_note_is_appended_dated_and_leaves_the_read_alone(tmp_path):
    csv = _write_source(tmp_path)
    d = tmp_path / ".portia"
    src_file = index_source(csv, portia_dir=d)
    set_interpretation("customers", summary="A first read.", portia_dir=d)

    set_interpretation("customers", note="signup_amount is in cents, not euros.", portia_dir=d)
    set_interpretation("customers", note="  customer_id repeats across years.  ", portia_dir=d)
    after = yaml.safe_load(src_file.read_text(encoding="utf-8"))

    assert after["summary"] == "A first read."
    assert [n["text"] for n in after["notes"]] == [
        "signup_amount is in cents, not euros.",
        "customer_id repeats across years.",
    ]
    assert all(n["at"] for n in after["notes"])
    assert list(after).index("notes") > list(after).index("columns"), "the margin, after the facts"


def test_a_note_survives_a_reindex_and_an_empty_one_is_refused(tmp_path):
    csv = _write_source(tmp_path)
    d = tmp_path / ".portia"
    src_file = index_source(csv, portia_dir=d)
    set_interpretation("customers", note="the id is a legacy code", portia_dir=d)

    index_source(csv, portia_dir=d)
    after = yaml.safe_load(src_file.read_text(encoding="utf-8"))
    assert [n["text"] for n in after["notes"]] == ["the id is a legacy code"]

    with pytest.raises(ValueError, match="say something"):
        set_interpretation("customers", note="   ", portia_dir=d)
    untouched = yaml.safe_load(src_file.read_text(encoding="utf-8"))
    assert "notes" not in untouched or len(untouched["notes"]) == 1


def test_an_entry_without_notes_has_no_notes_key(tmp_path):
    """Absent, not empty: a silence on every source of every project costs nothing."""
    csv = _write_source(tmp_path)
    d = tmp_path / ".portia"
    src_file = index_source(csv, portia_dir=d)
    set_interpretation("customers", summary="A read.", portia_dir=d)
    assert "notes" not in yaml.safe_load(src_file.read_text(encoding="utf-8"))


# --- reading fast, writing the same bytes (2026-10-08) -------------------------


def _realistic_entry(tmp_path) -> Path:
    """An entry the way a project ends up with one: profiled, read, noted.

    Values chosen for what a YAML emitter can disagree about: text past U+FFFF,
    a long value holding a tab (which forces double quotes and a fold), prose
    with typographic punctuation, a date, a decimal, nulls, a column name with
    a space in it.
    """
    import pandas as pd

    n = 30
    pd.DataFrame(
        {
            "booking id": range(n),
            "city": ["Zürich", "São Paulo", "東京"] * 10,
            "amount_eur": [round(i * 12.345, 3) for i in range(n)],
            "stayed_on": pd.date_range("2026-01-01", periods=n).strftime("%Y-%m-%d"),
            "comment": [
                f"Guest {i} wrote:\tlate check-in ≈ 23:40 — “fine”, would book again 🙂 "
                + "and the rest of a long free-text field " * 3
                if i % 3
                else None
                for i in range(n)
            ],
        }
    ).to_csv(tmp_path / "bookings.csv", index=False)
    d = tmp_path / ".portia"
    entry_file = index_source(tmp_path / "bookings.csv", portia_dir=d)
    set_interpretation(
        "bookings",
        summary=(
            "One row per booking from the reservations feed — amounts in euros, "
            "dates as ISO strings, and a free-text comment that guests write 😀 "
            "including tabs and quotes ('like this'). Roughly a month of stays."
        ),
        roles={"booking id": "key", "amount_eur": "measure"},
        note="amount_eur excludes the city tax; see the finance note of 2026-09-30.",
        portia_dir=d,
    )
    return entry_file


def test_the_catalog_is_read_by_libyaml_and_reads_the_same_values(tmp_path):
    """The window reads every entry again each time the catalog moves, on its
    event loop; on two 1,579-column tables that was 1.3 s of pure-Python
    scanning for eighteen files. libyaml's parser is used where PyYAML has it,
    and it has to read back exactly what the pure-Python one does."""
    from portia import catalog

    entry_file = _realistic_entry(tmp_path)
    text = entry_file.read_text(encoding="utf-8")

    assert catalog._read(entry_file) == yaml.load(text, Loader=yaml.SafeLoader)
    if yaml.__with_libyaml__:
        assert catalog._LOADER is yaml.CSafeLoader
    else:
        assert catalog._LOADER is yaml.SafeLoader


def test_the_catalog_is_written_by_the_pure_python_emitter_byte_for_byte(tmp_path):
    """The catalog is read as a diff, so writing it must not move a byte that
    did not change. libyaml's emitter would: it escapes a character past U+FFFF
    (the 😀 above becomes ``\\U0001F600``) and folds a long double-quoted string
    at other places. So the writer stays pure Python, and a round trip through
    `_read` and `_write` is the file it started from."""
    from portia import catalog

    entry_file = _realistic_entry(tmp_path)
    written = entry_file.read_bytes()
    entry = catalog._read(entry_file)

    assert written.decode("utf-8") == yaml.safe_dump(
        entry, sort_keys=False, default_flow_style=False, allow_unicode=True
    )
    assert "😀" in written.decode("utf-8") and "\\U0001F600" not in written.decode("utf-8")
    catalog._write(entry_file, entry)
    assert entry_file.read_bytes() == written


# --- reading only what moved, writing whole (2026-10-08) -----------------------


def _three_sources(tmp_path):
    import pandas as pd

    d = tmp_path / ".portia"
    init_project("Bookings and the hotels they are for.", portia_dir=d)
    for name in ("bookings", "hotels", "rates"):
        pd.DataFrame({"id": [1, 2, 3], name: ["a", "b", "c"]}).to_csv(
            tmp_path / f"{name}.csv", index=False
        )
        index_source(tmp_path / f"{name}.csv", portia_dir=d)
    return d


def _entries_read(monkeypatch) -> list[str]:
    from portia import catalog

    reads: list[str] = []
    real = catalog._read

    def read(path):
        if Path(path).parent.name == "sources":
            reads.append(Path(path).stem)
        return real(path)

    monkeypatch.setattr(catalog, "_read", read)
    return reads


def test_a_reload_reads_only_the_entries_that_moved_and_agrees_with_a_whole_read(
    tmp_path, monkeypatch
):
    """The window holds the catalog and reloads it whenever an entry moves; it
    read every entry each time, which on two 1,579-column tables was half a
    second per tool result of a reading job that wrote eighteen. An entry whose
    file looks as it did is the dict already held, and the answer is the one a
    whole read gives, whatever moved: an entry, the register, the groups."""
    from portia import catalog

    d = _three_sources(tmp_path)
    loaded, seen = catalog.reload_catalog({}, {}, d)
    assert loaded == load_catalog(d)
    reads = _entries_read(monkeypatch)

    again, seen = catalog.reload_catalog(loaded, seen, d)
    assert reads == [] and again == loaded
    assert all(again["sources"][n] is loaded["sources"][n] for n in loaded["sources"])

    set_interpretation("hotels", summary="One row per hotel.", portia_dir=d)
    set_group("stays", sources=["bookings", "hotels"], context="One feed.", portia_dir=d)
    reads.clear()  # the write read its own entry
    moved, seen = catalog.reload_catalog(again, seen, d)
    assert reads == ["hotels"]
    assert (
        moved == load_catalog(d) and moved["sources"]["hotels"]["summary"] == "One row per hotel."
    )
    assert moved["sources"]["bookings"] is again["sources"]["bookings"]

    remove_source("rates", portia_dir=d)
    (tmp_path / "rooms.csv").write_text("id,beds\n1,2\n", encoding="utf-8")
    index_source(tmp_path / "rooms.csv", portia_dir=d)
    reads.clear()
    last, _ = catalog.reload_catalog(moved, seen, d)
    assert reads == ["rooms"]
    assert last == load_catalog(d) and set(last["sources"]) == {"bookings", "hotels", "rooms"}


def test_an_entry_is_written_whole_or_not_at_all(tmp_path, monkeypatch):
    """The window reads the entries that moved off its loop now, while a tool
    may be writing the next one, and a write that truncated the file and dumped
    into it was a quarter of a second of half an entry: the first replay of a
    reading job read one and failed on a YAML error. A write that fails leaves
    the entry as it was, a reader holding the old file reads it whole, and
    nothing is left beside it."""
    from portia import catalog

    d = _three_sources(tmp_path)
    entry_file = d / "sources" / "hotels.yaml"
    before = entry_file.read_bytes()
    entry = catalog._read(entry_file)

    def breaks(*a, **k):
        raise RuntimeError("the emitter fell over halfway")

    monkeypatch.setattr(catalog.yaml, "safe_dump", breaks)
    with pytest.raises(RuntimeError):
        catalog._write(entry_file, {**entry, "summary": "Never written."})
    assert entry_file.read_bytes() == before
    monkeypatch.undo()

    with open(entry_file, encoding="utf-8") as reading:
        catalog._write(entry_file, {**entry, "summary": "Written whole."})
        if os.name != "nt":  # Windows refuses to replace an open file; `_replace` waits
            assert reading.read() == before.decode("utf-8")
    assert catalog._read(entry_file)["summary"] == "Written whole."
    assert sorted(p.name for p in entry_file.parent.iterdir()) == [
        "bookings.yaml",
        "hotels.yaml",
        "rates.yaml",
    ]


def test_naming_a_source_reads_only_the_entry_that_holds_the_name(tmp_path, monkeypatch):
    """Whether a name is taken is the register's answer; whose it is, one
    entry's. Naming a source read the whole catalog, every entry, for each
    file indexed: by the end of a run on two 1,579-column tables, half a
    second of parsing per file on the worker, holding the interpreter's lock
    the window's loop needed to answer a click."""
    from portia import catalog

    d = _three_sources(tmp_path)
    (tmp_path / "raw").mkdir()
    (tmp_path / "raw" / "hotels.csv").write_text("id\n1\n", encoding="utf-8")
    (tmp_path / "rooms.csv").write_text("id\n1\n", encoding="utf-8")
    reads = _entries_read(monkeypatch)

    assert catalog.source_name("rooms.csv", portia_dir=d) == "rooms"
    assert reads == []
    assert catalog.source_name("hotels.csv", portia_dir=d) == "hotels"  # its own name
    assert catalog.source_name("raw/hotels.csv", portia_dir=d) == "raw__hotels"
    assert reads == ["hotels", "hotels"]

"""The canonical loader dispatches by format and refuses the unknown."""

from pathlib import Path

import pandas as pd
import pytest

from portia.checks.profiling import profile_path
from portia.core.io import (
    NA_TOKENS,
    connect,
    load_frame,
    load_table,
    supported_suffixes,
    write_table,
)

MOCK = Path(__file__).resolve().parents[1] / "data" / "mock"


def test_loads_csv(tmp_path):
    p = tmp_path / "t.csv"
    pd.DataFrame({"a": [1, 2], "b": ["x", "y"]}).to_csv(p, index=False)
    df = load_frame(p)
    assert list(df.columns) == ["a", "b"]
    assert len(df) == 2


def test_unsupported_format_raises_clearly(tmp_path):
    p = tmp_path / "t.xlsx"
    p.write_bytes(b"not really excel")
    with pytest.raises(ValueError, match="unsupported data format"):
        load_frame(p)


def test_csv_is_supported():
    assert ".csv" in supported_suffixes()


def test_profile_path_round_trips(tmp_path):
    p = tmp_path / "nums.csv"
    pd.DataFrame({"x": [1.0, 2.0, 3.0, 4.0]}).to_csv(p, index=False)
    prof = profile_path(p)
    assert prof["source"] == str(p)
    assert prof["columns"][0]["mean"] == 2.5


def test_load_table_is_lazy_and_reads_the_same_rows(tmp_path):
    p = tmp_path / "t.csv"
    pd.DataFrame({"a": [1, 2, 3]}).to_csv(p, index=False)
    con = connect()
    try:
        assert load_table(p, con).count() == 3
    finally:
        con.close()


def test_the_two_tiers_agree_on_what_missing_looks_like(tmp_path):
    """`NA_TOKENS` is a copy of pandas' default set, and copies drift.

    Both directions matter. If pandas starts nulling something we don't list,
    DuckDB reads a value where pandas reads a gap; if we list something pandas
    keeps, DuckDB invents a gap. Either way a null rate would depend on which
    reader ran, and null rates are what the copilot decides on.
    """
    con = connect()
    try:
        for token in NA_TOKENS:
            p = tmp_path / "na.csv"
            p.write_text(f"k,v\n1,x\n2,{token}\n", encoding="utf-8")
            assert bool(load_frame(p)["v"].isna().iloc[1]), f"pandas keeps {token!r}"
            assert load_table(p, con).scalar("count(v)") == 1, f"duckdb keeps {token!r}"

        for token in ("-", "?", "NIL", "na", "Null", "missing"):
            p = tmp_path / "kept.csv"
            p.write_text(f"k,v\n1,x\n2,{token}\n", encoding="utf-8")
            assert not bool(load_frame(p)["v"].isna().iloc[1]), f"pandas nulls {token!r}"
            assert load_table(p, con).scalar("count(v)") == 2, f"duckdb nulls {token!r}"
    finally:
        con.close()


# --- parquet -----------------------------------------------------------------


def test_parquet_is_supported_on_both_halves(tmp_path):
    """A format you can load and not save is a trap you find at the end of a run."""
    assert ".parquet" in supported_suffixes()
    con = connect()
    try:
        source = load_table(MOCK / "messy_customers.csv", con)
        out = write_table(source, tmp_path / "messy.parquet")
        assert out.exists()
        assert load_table(out, con).count() == source.count()
        assert len(load_frame(out)) == 40  # the pandas half works too
    finally:
        con.close()


def test_a_round_trip_through_parquet_keeps_the_schema(tmp_path):
    """The point of converting: the CSV reader stops guessing.

    `signup_amount` is numbers-with-whitespace plus a `pending`, and its
    text-ness *is* the finding. Parquet carries that rather than re-sniffing it.
    """
    con = connect()
    try:
        before = load_table(MOCK / "messy_customers.csv", con)
        after = load_table(write_table(before, tmp_path / "m.parquet"), con)
        assert after.dtypes == before.dtypes
        assert after.dtypes["signup_amount"] == "VARCHAR"
    finally:
        con.close()


def test_the_same_evidence_comes_out_of_either_format(tmp_path):
    """Converting must not change what the copilot reads."""
    from portia.checks.profiling import profile

    con = connect()
    try:
        csv = load_table(MOCK / "messy_customers.csv", con)
        parquet = load_table(write_table(csv, tmp_path / "m.parquet"), con)
        assert profile(parquet) == profile(csv)
    finally:
        con.close()


def test_writing_an_unsupported_format_says_so(tmp_path):
    con = connect()
    try:
        with pytest.raises(ValueError, match="unsupported data format"):
            write_table(load_table(MOCK / "hotels.csv", con), tmp_path / "out.xlsx")
    finally:
        con.close()


def test_a_parquet_source_loads_like_any_other(tmp_path):
    """The reader dispatches on the extension, so a project can hold either."""
    con = connect()
    try:
        write_table(load_table(MOCK / "reservations.csv", con), tmp_path / "reservations.parquet")
        assert load_table(tmp_path / "reservations.parquet", con, name="reservations").count() == 14
    finally:
        con.close()


# --- a CSV's guess: made once, replayed (2026-10-09) ---------------------------
#
# DuckDB guesses a CSV's dialect and every column's type before each statement
# that names it, 7 s on a 1,579-column file (`DUCKDB_MIGRATION.md` §17). A local
# connection asks once and sends the guess with every later statement. Two things
# have to hold: a replayed read is the read DuckDB would have made, and a guess is
# never sent with a file that is not the one it was made from.


@pytest.fixture
def sniffs(monkeypatch):
    """Every guess made, by path, starting from none kept."""
    from portia.core import io

    monkeypatch.setattr(io, "_GUESSES", {})
    monkeypatch.setattr(io, "_GUESSING", {})
    made: list[Path] = []
    real = io._sniff

    def counted(path, fmt):
        made.append(path)
        return real(path, fmt)

    monkeypatch.setattr(io, "_sniff", counted)
    return made


@pytest.fixture
def settled(monkeypatch):
    """Keep a guess about a file written a moment ago.

    `SETTLE_SECONDS` is there for file systems that stamp a write to the second,
    and a test's files are always that young. These tests are about the key.
    """
    from portia.core import io

    monkeypatch.setattr(io, "SETTLE_SECONDS", 0)


def _read(con, relation: str) -> tuple:
    rel = con.sql(f"SELECT * FROM {relation}")
    return list(rel.columns), [str(t) for t in rel.types], rel.fetchall()


def test_a_csv_is_guessed_once_and_every_later_statement_replays_it(tmp_path, sniffs, settled):
    """A profile, a count, a schema, a cursor on another thread: one guess between them."""
    import datetime

    from portia.core import io

    p = tmp_path / "t.csv"
    p.write_text("id,day\n1,2024-01-02\n2,2024-02-03\n", encoding="utf-8")
    con = connect()
    try:
        table = load_table(p, con)
        assert sniffs == [], "building a table reads nothing"
        assert table.count() == 2
        assert table.dtypes == {"id": "BIGINT", "day": "DATE"}
        assert table.using(con.cursor()).rows(1) == [(1, datetime.date(2024, 1, 2))]
    finally:
        con.close()
    assert profile_path(p)["n_rows"] == 2
    assert sniffs == [p.resolve()]
    plain = io.read_relation(p)
    assert "auto_detect" not in plain, "the query a table holds is the plain reader"
    assert "auto_detect=false" in io._replayed(f"SELECT * FROM {plain}")


#: Files a guess has to survive: what the user listed, and what broke the
#: replay DuckDB itself writes (its path and names are not quoted).
GUESSED = {
    "dates_and_times": "id,day,at\n1,2024-01-02,2024-01-02 10:00:00\n2,2024-02-03,2024-02-03 11:30:00.5\n",
    "day_first": "d\n31/12/2024\n01/02/2024\n",
    "quoted_fields": 'k,note\n1,"a, b"\n2,"two\nlines"\n3,"say ""hi"""\n',
    "a_column_all_null": "k,empty,tokens\n1,,NA\n2,,null\n3,,N/A\n",
    "upper_case_names": "ID,Station_Name,VALUE\n1,North,1.5\n2,South,2.5\n",
    "an_apostrophe_in_a_name": "id,o'clock\n1,9\n2,10\n",
    "semicolons_and_crlf": "a;b\r\n1,5;x\r\n2,5;y\r\n",
    "no_header": "1,2\n3,4\n5,6\n",
    "duplicate_names": "a,a,b\n1,2,3\n",
    "header_only": "a,b\n",
    "booleans_and_leading_zeros": "flag,code\ntrue,007\nfalse,010\n",
}


@pytest.mark.parametrize("name", sorted(GUESSED))
def test_a_replayed_read_is_the_read_duckdb_would_have_guessed(tmp_path, sniffs, name):
    """Same names, same types, same values, from a folder whose name has an apostrophe:
    a bare DuckDB connection guesses at bind, a local one replays."""
    import duckdb

    from portia.core import io

    folder = tmp_path / "o'brien"
    folder.mkdir()
    p = folder / f"{name}.csv"
    p.write_text(GUESSED[name], encoding="utf-8")
    plain = io.read_relation(p)
    assert "auto_detect=false" in io._replayed(plain)
    bare, local = duckdb.connect(), connect()
    try:
        assert _read(local, plain) == _read(bare, plain)
    finally:
        bare.close()
        local.close()


def test_a_profile_is_the_same_whether_the_guess_is_replayed_or_made_at_bind(
    tmp_path, sniffs, monkeypatch
):
    from portia.core import io

    p = tmp_path / "mixed.csv"
    p.write_text(
        "ID,day,note,empty,o'clock\n"
        '1,2024-01-02,"a, b",,9\n'
        '2,2024-02-03,"two\nlines",NA,10\n'
        "3,2024-03-04,plain,,11\n",
        encoding="utf-8",
    )
    replayed = profile_path(p)
    assert len(sniffs) == 1
    monkeypatch.setattr(io, "_sniff", lambda path, fmt: None)  # DuckDB guesses at bind
    assert profile_path(p) == replayed


def test_a_table_built_before_its_file_changed_reads_the_file_as_it_is_now(
    tmp_path, sniffs, settled
):
    """The window keeps a run's step results and writes them later. The guess is
    checked when a statement is sent, not when the table's query was written."""
    import datetime

    p = tmp_path / "t.csv"
    p.write_text("a,b\n1,2\n", encoding="utf-8")
    con = connect()
    try:
        held = load_table(p, con)
        assert held.dtypes == {"a": "BIGINT", "b": "BIGINT"}
        p.write_text("a,b,c\nx,2024-01-02,1.5\n", encoding="utf-8")
        assert held.dtypes == {"a": "VARCHAR", "b": "DATE", "c": "DOUBLE"}
        assert held.rows() == [("x", datetime.date(2024, 1, 2), 1.5)]
        out = write_table(held, tmp_path / "out.csv")
        assert out.read_text(encoding="utf-8") == "a,b,c\nx,2024-01-02,1.5\n"
    finally:
        con.close()
    assert len(sniffs) == 2


def test_a_rewrite_of_the_same_size_is_noticed(tmp_path, sniffs, settled):
    """Same path, same byte count, other names and types. The stamp is moved a
    whole second, so this does not depend on how finely the file system stamps."""
    import os

    p = tmp_path / "t.csv"
    p.write_text("a,b\n1,2\n", encoding="utf-8")
    con = connect()
    try:
        held = load_table(p, con)
        assert held.columns == ["a", "b"]
        before = p.stat()
        p.write_text("x,y\nu,v\n", encoding="utf-8")
        os.utime(p, ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000_000))
        assert p.stat().st_size == before.st_size
        assert held.dtypes == {"x": "VARCHAR", "y": "VARCHAR"}
    finally:
        con.close()
    assert len(sniffs) == 2


def test_another_file_moved_into_place_with_the_same_size_and_stamp_is_noticed(
    tmp_path, sniffs, settled
):
    """An editor that saves by writing a new file and renaming it over the old one
    makes a new inode; with the old modification time put back, only that and the
    change time say it is a different file."""
    import os

    p = tmp_path / "t.csv"
    p.write_text("a,b\n1,2\n", encoding="utf-8")
    con = connect()
    try:
        held = load_table(p, con)
        assert held.columns == ["a", "b"]
        before = p.stat()
        fresh = tmp_path / "t.csv.tmp"
        fresh.write_text("x,y\nu,v\n", encoding="utf-8")
        os.utime(fresh, ns=(before.st_atime_ns, before.st_mtime_ns))
        os.replace(fresh, p)
        after = p.stat()
        assert (after.st_size, after.st_mtime_ns) == (before.st_size, before.st_mtime_ns)
        assert held.columns == ["x", "y"]
    finally:
        con.close()
    assert len(sniffs) == 2


def test_a_rewrite_that_puts_the_old_stamp_back_is_noticed(tmp_path, sniffs, settled):
    """Same inode, size and modification time: only the change time moved, and only
    the kernel sets that."""
    import os

    p = tmp_path / "t.csv"
    p.write_text("a,b\n1,2\n", encoding="utf-8")
    con = connect()
    try:
        held = load_table(p, con)
        assert held.columns == ["a", "b"]
        before = p.stat()
        p.write_text("x,y\nu,v\n", encoding="utf-8")
        os.utime(p, ns=(before.st_atime_ns, before.st_mtime_ns))
        after = p.stat()
        assert (after.st_ino, after.st_size, after.st_mtime_ns) == (
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        )
        if after.st_ctime_ns == before.st_ctime_ns:
            pytest.skip("this file system stamps too coarsely; SETTLE_SECONDS covers it")
        assert held.columns == ["x", "y"]
    finally:
        con.close()
    assert len(sniffs) == 2


def test_a_guess_about_a_file_written_a_moment_ago_is_not_kept(tmp_path, sniffs):
    """Inside `SETTLE_SECONDS` of the last write, a rewrite could land on the same
    stamp on a coarse file system, so the guess serves one statement only."""
    p = tmp_path / "t.csv"
    p.write_text("a,b\n1,2\n", encoding="utf-8")
    con = connect()
    try:
        table = load_table(p, con)
        assert table.count() == table.count() == 1
    finally:
        con.close()
    assert len(sniffs) == 2


def test_a_file_that_changes_while_being_guessed_is_read_the_plain_way(
    tmp_path, sniffs, settled, monkeypatch
):
    """The guess then describes neither version, so it is neither sent nor kept,
    and DuckDB guesses the file as it now is."""
    from portia.core import io

    p = tmp_path / "t.csv"
    p.write_text("a,b\n1,2\n", encoding="utf-8")
    real = io._sniff

    def rewritten_meanwhile(path, fmt):
        reader = real(path, fmt)
        p.write_text("x,y,z\nu,v,w\n", encoding="utf-8")
        return reader

    monkeypatch.setattr(io, "_sniff", rewritten_meanwhile)
    con = connect()
    try:
        assert load_table(p, con).columns == ["x", "y", "z"]
    finally:
        con.close()
    assert io._GUESSES == {}


def test_threads_reading_a_new_file_at_once_make_one_guess(tmp_path, sniffs, settled, monkeypatch):
    """Tool calls and the window's work run on threads, each on its own cursor. The
    first readers of a file wait for one guess rather than each making their own."""
    import threading
    import time
    from concurrent.futures import ThreadPoolExecutor

    from portia.core import io

    p = tmp_path / "t.csv"
    p.write_text("id,v\n1,a\n2,b\n", encoding="utf-8")
    counted = io._sniff

    def slow(path, fmt):
        time.sleep(0.2)
        return counted(path, fmt)

    monkeypatch.setattr(io, "_sniff", slow)
    readers = 8
    start = threading.Barrier(readers)
    con = connect()
    table = load_table(p, con)

    def read(_):
        own = table.using(con.cursor())
        start.wait()
        return own.dtypes, own.count()

    try:
        with ThreadPoolExecutor(readers) as pool:
            answers = list(pool.map(read, range(readers)))
    finally:
        con.close()
    assert answers == [({"id": "BIGINT", "v": "VARCHAR"}, 2)] * readers
    assert sniffs == [p.resolve()]


def test_nothing_about_the_guess_is_written_anywhere(tmp_path, sniffs, settled, monkeypatch):
    """Memory only: a restart, or a new DuckDB, guesses again."""
    monkeypatch.chdir(tmp_path)
    p = tmp_path / "data" / "t.csv"
    p.parent.mkdir()
    p.write_text("a,b\n1,2\n", encoding="utf-8")
    before = sorted(tmp_path.rglob("*"))
    con = connect()
    try:
        load_table(p, con).count()
        load_table(p, con).count()
    finally:
        con.close()
    profile_path(p)
    assert len(sniffs) == 1
    assert sorted(tmp_path.rglob("*")) == before


def test_parquet_warehouses_and_written_sql_are_never_guessed(tmp_path, sniffs, monkeypatch):
    """Parquet carries its schema; a warehouse session never reads a file and is
    handed back as it was opened; SQL written to a file reads the file as it is on
    the day it runs."""
    import dataclasses

    from portia.core import backend
    from portia.core.io import read_query

    monkeypatch.chdir(tmp_path)
    csv = tmp_path / "data" / "t.csv"
    csv.parent.mkdir()
    csv.write_text("a,b\n1,2\n", encoding="utf-8")
    con = connect()
    try:
        out = write_table(load_table(csv, con), tmp_path / "t.parquet")
        sniffs.clear()
        assert load_table(out, con).count() == 1
        written = read_query("data/t.csv", absolute=False)
        assert con.execute(f"SELECT count(*) FROM ({written})").fetchone() == (1,)
    finally:
        con.close()
    assert sniffs == []

    class Session:
        remote = True

        def interrupt(self):
            pass

    remote = dataclasses.replace(backend.LOCAL, kind="warehouse", remote=True, open=Session)
    with backend.using(remote):
        assert type(connect()) is Session


def test_a_window_preview_builds_its_table_without_reading(tmp_path, sniffs, settled):
    """`ui/engine.read_table` runs in a render pass. Building the table is free;
    the guess is made on the thread that measures it."""
    engine = pytest.importorskip("portia.ui.engine")

    p = tmp_path / "t.csv"
    p.write_text("a,b\n1,2\n", encoding="utf-8")
    table = engine.read_table(p)
    assert sniffs == []
    assert table.preview()[0] == 1
    assert len(sniffs) == 1


def test_a_file_duckdb_cannot_guess_fails_as_it_always_did(tmp_path, sniffs, settled):
    """Not valid UTF-8: no guess, so the plain reader, and DuckDB's own error —
    asked once, not again until the file changes."""
    import duckdb

    p = tmp_path / "t.csv"
    p.write_bytes(b"a,b\n\xff\xfe,1\n")
    con = connect()
    try:
        for _ in range(2):
            with pytest.raises(duckdb.InvalidInputException, match="Invalid unicode"):
                load_table(p, con).count()
    finally:
        con.close()
    assert len(sniffs) == 1


def test_a_missing_file_is_not_guessed_and_fails_where_it_did(tmp_path, sniffs):
    import duckdb

    con = connect()
    try:
        table = load_table(tmp_path / "gone.csv", con)
        with pytest.raises(duckdb.IOException):
            table.count()
    finally:
        con.close()
    assert sniffs == []


def test_a_stopped_guess_is_raised_and_not_remembered(tmp_path, sniffs, settled, monkeypatch):
    """Stop interrupts the guess's connection. That is not DuckDB failing to guess
    the file, so nothing is kept and the next statement guesses."""
    import dataclasses

    import duckdb

    from portia.core import io

    class Interrupted:
        def execute(self, sql):
            raise duckdb.InterruptException("INTERRUPT Error: Interrupted!")

        def interrupt(self):
            pass

        def close(self):
            pass

    p = tmp_path / "t.csv"
    p.write_text("a,b\n1,2\n", encoding="utf-8")
    con = connect()
    try:
        table = load_table(p, con)
        real = io.backend.LOCAL
        monkeypatch.setattr(io.backend, "LOCAL", dataclasses.replace(real, open=Interrupted))
        with pytest.raises(duckdb.InterruptException):
            table.count()
        assert io._GUESSES == {}
        monkeypatch.setattr(io.backend, "LOCAL", real)
        assert table.count() == 1
    finally:
        con.close()
    assert len(io._GUESSES) == 1

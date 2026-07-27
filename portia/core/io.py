"""Canonical data loading — THE one way to get a table from a path.

Every tool and check loads data through here. Nothing else calls ``pd.read_csv``
or names a DuckDB reader directly. New formats are registered in ``_FORMATS``,
**once**, so support grows in a single place instead of a dozen ad-hoc readers
drifting apart (different NA handling, dtype coercion, encodings).

A format now declares *two* ways to be read, side by side in one entry: the
pandas loader and the DuckDB table function. That pairing is the point — it is
what stops someone adding Parquet support to one tier and not the other.

Two entry points, for two different callers:

- :func:`load_frame` — the whole file, in memory, as pandas. Correct for fixtures,
  tests, and small reads, and **wrong for anything at scale**: pandas needs ~2.4×
  a CSV's size to hold it and ~4.8× to profile it (`docs/DUCKDB_MIGRATION.md` §1).
- :func:`load_table` — a lazy :class:`~portia.core.table.Table` over the file.
  Nothing is read until something asks for a number.

:func:`load_table` reads the file *in place*, which is right for a one-off — the
CLI pointed at a path, with no project around it. A project ingests instead
(`core.store`): querying the same CSV repeatedly re-parses it every time, and the
ingested store is 20× faster on column-scoped reads and 5.6× smaller on disk.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from portia.core.table import Table, quote_literal


@dataclass(frozen=True)
class Format:
    """How one file format is read, on both tiers.

    ``sql_reader`` is a DuckDB table function taking a path — ``read_csv_auto``,
    and ``read_parquet`` the day Parquet lands. Registering a format means
    filling in both fields; a format that only fills in one is a format that
    silently doesn't work at scale.
    """

    read_frame: Callable[..., pd.DataFrame]
    sql_reader: str


def load_frame(path: str | Path, **kwargs: Any) -> pd.DataFrame:
    """Load a whole data file into memory as a DataFrame.

    Raises a clear error for unsupported formats rather than silently guessing.
    """
    return _format(path).read_frame(Path(path), **kwargs)


def load_table(path: str | Path, con: Any, *, name: str | None = None) -> Table:
    """A lazy :class:`Table` reading ``path`` directly, without copying it.

    For a one-off read where there is no project to ingest into. Inside a
    project, prefer ``core.store.ingest`` — see the module docstring.
    """
    path = Path(path)
    return Table(name=name or path.stem, query=read_query(path), con=con)


def read_query(path: str | Path) -> str:
    """The ``SELECT`` that reads ``path`` in DuckDB. The one place a reader is named."""
    path = Path(path)
    return f"SELECT * FROM {_format(path).sql_reader}({quote_literal(str(path.resolve()))})"


def supported_suffixes() -> tuple[str, ...]:
    """Extensions this module can read — useful for CLIs and file panels."""
    return tuple(sorted(_FORMATS))


def find_data_files(target: str | Path) -> list[Path]:
    """Every supported data file at ``target`` — a file, a directory, or a glob.

    Lives here rather than in a CLI because both human edges need it: the
    ``index`` command resolves what to index, and the app's "add by path" field
    resolves the same thing. What counts as a data file is decided by
    :data:`_FORMATS`, so the answer stays one fact rather than two lists that
    drift apart.
    """
    path = Path(target)
    if path.is_file():
        return [path]

    suffixes = supported_suffixes()
    if path.is_dir():
        found = sorted(p for p in path.iterdir() if p.suffix.lower() in suffixes)
    else:
        found = sorted(p for p in _glob(path) if p.suffix.lower() in suffixes)

    if not found:
        raise ValueError(f"no supported data files at {str(target)!r} ({', '.join(suffixes)})")
    return found


def _format(path: str | Path) -> Format:
    suffix = Path(path).suffix.lower()
    fmt = _FORMATS.get(suffix)
    if fmt is None:
        supported = ", ".join(sorted(_FORMATS))
        raise ValueError(
            f"unsupported data format {suffix!r} for {Path(path).name} (supported: {supported})"
        )
    return fmt


def _glob(pattern: Path):
    """Match a glob, absolute or relative.

    ``Path().glob("/data/*.csv")`` raises on an absolute pattern, so an absolute
    one is anchored at the root and matched from there. Someone adding files by
    path types the path they have, and it is usually the absolute one.
    """
    if pattern.is_absolute():
        root = Path(pattern.anchor)
        return root.glob(str(pattern.relative_to(root)))
    return Path().glob(str(pattern))


def _load_csv(path: Path, **kwargs: Any) -> pd.DataFrame:
    # Let pandas infer dtypes: "numeric stored as text" must remain a *reportable*
    # signal, not something we normalize away at the door. DuckDB's sniffer is
    # left to do the same on its side, for the same reason.
    return pd.read_csv(path, **kwargs)


# Register new formats here, once — both halves. e.g.
# ".parquet": Format(read_frame=_load_parquet, sql_reader="read_parquet").
_FORMATS: dict[str, Format] = {
    ".csv": Format(read_frame=_load_csv, sql_reader="read_csv_auto"),
}

"""A demo project at real size — five sources, ~3M rows, problems worth finding.

`portia/fixtures/` is the other mock data in this repo and it is a different
thing: those tables are fourteen rows, kept in git, and shaped so a *test* can
assert on them. This one is shaped so a **person** can drive portia through it on
camera and hit something real at every step. It is too big to track, so the
generator is tracked and the data is not — same split as `sandbox/`, which is why
the default output lands there.

The domain is hotel demand forecasting, where property records, an event feed and
a forward book have to be harmonized before a model can read them. Every name and
every row is invented.

**The sources, and the one problem each is really about:**

======================== ========= =============================================
file                     rows      the problem it exists for
======================== ========= =============================================
``regions.csv``                 35 a lookup that misses five spellings
``properties.csv``          12,240 a dimension whose key is not unique
``events_feed.parquet``    389,400 snapshots: latest-per-id, not distinct
``rates_daily.csv``        900,000 the right key, spelled differently — overlap 0
``bookings.csv``         2,005,000 four currencies and no conversion rate
======================== ========= =============================================

Every builder's docstring lists what is planted in it and what that should cost
downstream; ``DEMO.md``, written beside the data, is the same list arranged as
things to try on camera.

Regenerating is deterministic — each builder seeds its own RNG — so a re-record
after a bad take produces byte-identical files and the numbers you already said
out loud stay true.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path

import duckdb
import pandas as pd

from devtools.demodata.bookings import bookings
from devtools.demodata.events import events
from devtools.demodata.properties import properties
from devtools.demodata.rates import rates
from devtools.demodata.regions import regions
from portia.core.io import write_table
from portia.core.table import Table

#: Repo-root/sandbox/demo — gitignored, like every other throwaway project.
#: parents[2] = repo root from devtools/demodata/__init__.py.
DEFAULT_DIR = Path(__file__).resolve().parents[2] / "sandbox" / "demo"

#: The shooting script, tracked here and copied into the project so it sits
#: beside the data it describes. Tracked rather than generated because it is
#: prose about what to *do* with the dataset, which is the one thing the
#: builders' docstrings cannot say.
DEMO_NOTES = Path(__file__).parent / "DEMO.md"

#: filename -> builder. The single place a demo source is registered. The
#: suffix is part of the name because it is a *decision*: `events_feed` is
#: Parquet so a demo can show what that costs and saves next to `bookings.csv`,
#: which is the same order of magnitude and text.
_SOURCES: dict[str, Callable[[], pd.DataFrame]] = {
    "regions.csv": regions,
    "properties.csv": properties,
    "events_feed.parquet": events,
    "rates_daily.csv": rates,
    "bookings.csv": bookings,
}

__all__ = ["DEFAULT_DIR", "write_demo"]


def write_demo(directory: Path | str = DEFAULT_DIR) -> list[Path]:
    """Build every demo source and write it to ``directory/data``.

    Written through :func:`portia.core.io.write_table`, which is the only way
    data is written anywhere in this project — so the demo's Parquet is
    ZSTD-compressed by the same registration the engine's output uses, and a
    format the engine could not write back is a format this cannot produce.
    """
    data_dir = Path(directory) / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    written = []
    con = duckdb.connect(":memory:")
    try:
        for filename, build in _SOURCES.items():
            frame = build()  # noqa: F841 — read by DuckDB's replacement scan
            con.register("_demo_frame", frame)
            table = Table(name=Path(filename).stem, query="SELECT * FROM _demo_frame", con=con)
            written.append(write_table(table, data_dir / filename))
            con.unregister("_demo_frame")
    finally:
        con.close()

    written.append(Path(shutil.copy(DEMO_NOTES, Path(directory) / DEMO_NOTES.name)))
    return written

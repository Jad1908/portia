"""Canonical data loading — THE one way to get a DataFrame from a path.

Every tool and check loads data through :func:`load_frame`. Nothing else calls
``pd.read_csv`` / ``pd.read_parquet`` directly. New formats are registered here,
once, in ``_LOADERS`` — so support grows in a single place instead of a dozen
ad-hoc readers drifting apart (different NA handling, dtype coercion, encodings).

This is also the seam where the pandas → DuckDB/Snowflake swap happens: the
return type is "a DataFrame-like", and a scale tier can hand back a lazy frame
without any check changing (see docs/TECH_STACK.md, "Compute stays behind a
checks layer").
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import pandas as pd


def load_frame(path: str | Path, **kwargs: Any) -> pd.DataFrame:
    """Load a data file into a DataFrame, dispatching on file extension.

    Raises a clear error for unsupported formats rather than silently guessing.
    """
    path = Path(path)
    loader = _LOADERS.get(path.suffix.lower())
    if loader is None:
        supported = ", ".join(sorted(_LOADERS))
        raise ValueError(
            f"unsupported data format {path.suffix!r} for {path.name} "
            f"(supported: {supported})"
        )
    return loader(path, **kwargs)


def supported_suffixes() -> tuple[str, ...]:
    """Extensions :func:`load_frame` can read — useful for CLIs and file panels."""
    return tuple(sorted(_LOADERS))


def _load_csv(path: Path, **kwargs: Any) -> pd.DataFrame:
    # Let pandas infer dtypes: "numeric stored as text" must remain a *reportable*
    # signal, not something we normalize away at the door.
    return pd.read_csv(path, **kwargs)


# Register new formats here, once. e.g. ".parquet": _load_parquet (needs pyarrow).
_LOADERS: dict[str, Callable[..., pd.DataFrame]] = {
    ".csv": _load_csv,
}

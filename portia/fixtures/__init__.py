"""Kept mock data + the registry that writes it to disk.

Add a new fixture: write its builder in a module here and register it in
``_FIXTURES``. `python -m portia.fixtures` regenerates them all.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import pandas as pd

from portia.fixtures.customers import messy_customers

# Repo-root/data/mock — kept on disk (see .gitignore). parents[2] = repo root
# from portia/fixtures/__init__.py.
DEFAULT_DIR = Path(__file__).resolve().parents[2] / "data" / "mock"

# name -> builder. The single place fixtures are registered.
_FIXTURES: dict[str, Callable[[], pd.DataFrame]] = {
    "messy_customers": messy_customers,
}

__all__ = ["messy_customers", "write_fixtures", "DEFAULT_DIR"]


def write_fixtures(directory: Path | str = DEFAULT_DIR) -> list[Path]:
    """Generate the mock CSVs and keep them on disk. Returns written paths."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    written = []
    for name, builder in _FIXTURES.items():
        path = directory / f"{name}.csv"
        builder().to_csv(path, index=False)
        written.append(path)
    return written

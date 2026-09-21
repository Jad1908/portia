"""A path portia writes down is spelled with forward slashes on every machine.

``str(Path)`` uses the machine's separator, so on Windows a catalog entry said
``data\\orders.csv`` and ``_sources.sql`` read a file by that name. Both are
committed, and a Mac reads that string as one file name. The window also splits
its figure and folder keys on ``/``. Nothing on a Mac can see the difference, so
the rule is held by reading the source, like the encoding one.
"""

from __future__ import annotations

import ast
import pathlib

import pandas as pd
import pytest

from portia import catalog
from portia.core import io

ROOT = pathlib.Path(__file__).resolve().parents[1]


def machine_spelled(source: str) -> list[int]:
    """Lines where a ``relative_to`` result is turned into text with ``str()``."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
            continue
        if node.func.id != "str":
            continue
        inner = (n for arg in node.args for n in ast.walk(arg) if isinstance(n, ast.Attribute))
        if any(attr.attr == "relative_to" for attr in inner):
            found.append(node.lineno)
    return found


#: Text that never leaves the process, so the machine's spelling is harmless.
#: `theme` hashes asset names into a cache stamp; `io._glob` hands a pattern
#: straight back to `Path.glob` on the same machine.
LOCAL_ONLY = {"portia/ui/theme.py", "portia/core/io.py"}


def test_no_relative_path_is_spelled_with_the_machines_separator():
    found = [
        f"{path.relative_to(ROOT).as_posix()}:{line}"
        for path in sorted((ROOT / "portia").rglob("*.py"))
        if path.relative_to(ROOT).as_posix() not in LOCAL_ONLY
        for line in machine_spelled(path.read_text(encoding="utf-8"))
    ]
    assert not found, "use core.io.relative (or .as_posix()) at: " + ", ".join(found)


def test_the_scan_sees_what_it_is_for():
    assert machine_spelled("x = str(p.relative_to(root))") == [1]
    assert machine_spelled("x = str(p.relative_to(root).parent)") == [1]
    assert machine_spelled("x = p.relative_to(root).as_posix()") == []
    assert machine_spelled("x = str(p)") == []


def test_relative_is_forward_slashed_and_refuses_a_path_outside(tmp_path):
    assert io.relative(tmp_path / "data" / "raw" / "orders.csv", tmp_path) == "data/raw/orders.csv"
    with pytest.raises(ValueError):
        io.relative(tmp_path.parent / "elsewhere.csv", tmp_path)


def test_a_query_written_to_a_file_names_its_source_with_forward_slashes():
    written = io.read_query(pathlib.PurePosixPath("data") / "raw" / "orders.csv", absolute=False)
    assert "'data/raw/orders.csv'" in written


def test_a_source_in_a_folder_is_recorded_with_forward_slashes(tmp_path):
    folder = tmp_path / "data" / "raw"
    folder.mkdir(parents=True)
    pd.DataFrame({"ID": [1, 2]}).to_csv(folder / "orders.csv", index=False)
    recorded = catalog.source_ref(folder / "orders.csv", portia_dir=tmp_path / ".portia")
    assert recorded == "data/raw/orders.csv"

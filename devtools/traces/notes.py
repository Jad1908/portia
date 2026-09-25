"""The working set, notes and tags: one JSON file, keyed by log.

A log's key is its path from the repo (`logs.log_key`), so the file reads as
a list of runs and a diff of it says which note changed. It belongs in the
private notes repo, `docs/traces/notes.json`, because the logs it points at
carry real table names: nothing here may land in a tracked file. Without a
`docs/` folder (a fresh public clone) it falls back to `devtools/out/`, which
is gitignored.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

from devtools.traces.logs import REPO

NOTES_DIR = REPO / "docs"
FALLBACK_DIR = REPO / "devtools" / "out"
FILE_NAME = "notes.json"

#: The fields a note carries. Anything else sent is dropped rather than stored.
FIELDS = ("starred", "note", "tags")


def default_path() -> Path:
    if NOTES_DIR.is_dir():
        return NOTES_DIR / "traces" / FILE_NAME
    return FALLBACK_DIR / f"trace-{FILE_NAME}"


class Notes:
    """Read once, written whole on every change. One reader at a time."""

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else default_path()
        self._lock = threading.Lock()
        self._data: dict[str, dict[str, Any]] = self._load()

    def get(self, key: str) -> dict[str, Any]:
        found = self._data.get(key) or {}
        return {
            "starred": bool(found.get("starred")),
            "note": str(found.get("note") or ""),
            "tags": [str(t) for t in found.get("tags") or []],
        }

    def put(self, key: str, change: dict[str, Any], *, title: str | None = None) -> dict[str, Any]:
        with self._lock:
            current = self.get(key)
            for field in FIELDS:
                if field in change:
                    current[field] = change[field]
            current["tags"] = sorted({str(t).strip() for t in current["tags"] if str(t).strip()})
            current["note"] = str(current["note"])
            current["starred"] = bool(current["starred"])
            if current["starred"] or current["note"].strip() or current["tags"]:
                # The title rides along so the file is readable on its own.
                self._data[key] = {**current, **({"title": title} if title else {})}
            else:
                self._data.pop(key, None)
            self._save()
            return self.get(key)

    def _load(self) -> dict[str, dict[str, Any]]:
        if not self.path.is_file():
            return {}
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
        except ValueError:
            return {}
        return loaded if isinstance(loaded, dict) else {}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(self._data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        handle, temp = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            out.write(text)
        os.replace(temp, self.path)

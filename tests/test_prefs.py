"""`portia.ui.prefs` — what the window remembers between launches, and what it
does when the file is missing, broken or from another version.

`conftest._never_the_users_window_files` points every path here at a temp
folder, so none of these can touch the real `~/.config/portia`.
"""

from __future__ import annotations

import json

import pytest

prefs = pytest.importorskip("portia.ui.prefs")


def _write(path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_nothing_remembered_reads_as_empty():
    assert prefs.load() == {}
    assert prefs.machine() == {}
    assert prefs.recents() == []


def test_the_three_old_files_are_read_when_this_one_does_not_exist(tmp_path):
    project = tmp_path / "proj"
    _write(prefs.LEGACY_RECENTS, [{"path": str(project), "opened": "2026-09-01T10:00"}, "junk"])
    _write(prefs.LEGACY_VIEWS, {str(project): ["stg"], "/elsewhere": "not a list"})
    _write(prefs.LEGACY_LAYOUTS, {str(project): {"stg": [4, 5]}})

    assert prefs.recents() == [(project, "2026-09-01T10:00")]
    assert prefs.project(project) == {"view": ["stg"], "layout": {"stg": [4, 5]}}
    assert prefs.project(tmp_path / "elsewhere") == {}


def test_the_old_files_are_carried_into_the_first_write_and_left_in_place(tmp_path):
    project = tmp_path / "proj"
    _write(prefs.LEGACY_VIEWS, {str(project): ["stg"]})
    prefs.update_machine({"theme": "dark"})
    saved = json.loads(prefs.FILE.read_text(encoding="utf-8"))
    assert saved["projects"][str(project)]["view"] == ["stg"]
    assert saved["machine"] == {"theme": "dark"}
    assert prefs.LEGACY_VIEWS.exists(), "an older portia still reads it"
    # Once this file exists, the old ones are never read again.
    _write(prefs.LEGACY_VIEWS, {str(project): ["mart"]})
    assert prefs.project(project)["view"] == ["stg"]


def test_an_unreadable_file_reads_as_empty_and_is_set_aside_before_a_write():
    prefs.FILE.parent.mkdir(parents=True, exist_ok=True)
    prefs.FILE.write_text("{ half a file", encoding="utf-8")
    assert prefs.load() == {}
    prefs.update_machine({"theme": "light"})
    assert prefs.UNREADABLE.read_text(encoding="utf-8") == "{ half a file"
    assert prefs.machine() == {"theme": "light"}


def test_a_file_in_the_wrong_encoding_reads_as_empty():
    prefs.FILE.parent.mkdir(parents=True, exist_ok=True)
    prefs.FILE.write_bytes(b"\xff\xfe\x00junk")
    assert prefs.load() == {}


def test_a_document_that_is_not_an_object_reads_as_empty():
    _write(prefs.FILE, ["a", "list"])
    assert prefs.load() == {}
    assert prefs.machine() == {}


def test_a_half_of_the_wrong_type_reads_as_empty(tmp_path):
    _write(prefs.FILE, {"machine": "dark", "projects": [1, 2], "recents": {"a": 1}})
    assert prefs.machine() == {}
    assert prefs.project(tmp_path) == {}
    assert prefs.recents() == []
    prefs.update_machine({"theme": "dark"})
    assert prefs.machine() == {"theme": "dark"}


def test_none_removes_a_key_and_other_keys_survive_a_merge():
    prefs.update_machine({"theme": "dark", "effort": "low"})
    prefs.update_machine({"theme": None})
    assert prefs.machine() == {"effort": "low"}


def test_keys_a_newer_portia_wrote_are_kept(tmp_path):
    _write(prefs.FILE, {"version": 7, "machine": {"future": 1}, "elsewhere": True})
    prefs.update_machine({"theme": "dark"})
    saved = json.loads(prefs.FILE.read_text(encoding="utf-8"))
    assert saved["version"] == 7, "never written back as an older version"
    assert saved["elsewhere"] is True
    assert saved["machine"] == {"future": 1, "theme": "dark"}


def test_a_write_leaves_no_temporary_file_behind():
    prefs.update_machine({"theme": "dark"})
    assert [p.name for p in prefs.FILE.parent.iterdir()] == ["prefs.json"]


def test_recents_are_newest_first_without_duplicates_and_capped(tmp_path, monkeypatch):
    monkeypatch.setattr(prefs, "RECENTS_KEPT", 3)
    for name in ("a", "b", "c", "d"):
        prefs.remember_opened(tmp_path / name)
    prefs.remember_opened(tmp_path / "c")
    assert [p.name for p, _ in prefs.recents()] == ["c", "d", "b"]


def test_a_project_is_keyed_by_its_resolved_path(tmp_path, monkeypatch):
    (tmp_path / "proj").mkdir()
    monkeypatch.chdir(tmp_path)
    prefs.update_project(tmp_path / "proj" / ".." / "proj", {"draft": "hello"})
    assert prefs.project(tmp_path / "proj")["draft"] == "hello"
    assert list(prefs.load()["projects"]) == [str((tmp_path / "proj").resolve())]


def test_an_entry_left_empty_is_removed(tmp_path):
    prefs.update_project(tmp_path, {"draft": "x"})
    prefs.update_project(tmp_path, {"draft": None})
    assert prefs.project(tmp_path) == {}
    assert prefs.load().get("projects") == {}


def test_the_projects_looked_at_longest_ago_are_dropped_first(tmp_path, monkeypatch):
    monkeypatch.setattr(prefs, "PROJECTS_KEPT", 2)
    stamps = iter(["2026-09-01T00:00:00", "2026-09-02T00:00:00", "2026-09-03T00:00:00"])
    monkeypatch.setattr(prefs, "_now", lambda: next(stamps))
    for name in ("old", "middle", "new"):
        prefs.update_project(tmp_path / name, {"draft": name})
    assert prefs.project(tmp_path / "old") == {}
    assert prefs.project(tmp_path / "middle")["draft"] == "middle"
    assert prefs.project(tmp_path / "new")["draft"] == "new"


def test_a_folder_that_is_gone_is_not_forgotten(tmp_path):
    """A drive that is not mounted today is not a project that was deleted."""
    gone = tmp_path / "not-here"
    prefs.remember_opened(gone)
    prefs.update_project(gone, {"draft": "x"})
    assert prefs.recents()[0][0] == gone
    assert prefs.project(gone)["draft"] == "x"

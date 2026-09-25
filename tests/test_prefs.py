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


# --- the machine's half, on the app -----------------------------------------


def _app(**fields):
    from portia.ui.state import App

    app = App()
    for name, value in fields.items():
        setattr(app, name, value)
    return app


def _only_these_providers(monkeypatch, *kinds):
    from portia.agent import providers

    monkeypatch.setattr(providers, "offered_kinds", lambda path=None: kinds)


def test_what_the_window_held_comes_back_on_the_next_launch(tmp_path, monkeypatch):
    _only_these_providers(monkeypatch, "anthropic", "ollama")
    prefs.restore_machine(_app())  # the first launch: nothing to restore
    project = tmp_path / "proj"
    project.mkdir()
    before = _app(
        provider="ollama",
        model="qwen3:8b",
        effort="high",
        theme=True,
        interpret=False,
        pane_choices={"wide": (True, False)},
        files_width=300,
        transcript_width=500,
        reopen_last=False,
        opened=True,
        root=project,
    )
    prefs.sync(before)

    after = _app()
    last = prefs.restore_machine(after)
    assert (after.provider, after.model, after.effort) == ("ollama", "qwen3:8b", "high")
    assert after.model_unconfirmed == "qwen3:8b", "a server's model is checked on its first list"
    assert after.theme is True
    assert after.interpret is False
    assert after.pane_choices == {"wide": (True, False)}
    assert (after.show_files, after.show_transcript) == (True, False), "the wide band's choice"
    assert (after.files_width, after.transcript_width) == (300, 500)
    assert after.reopen_last is False
    assert last == project.resolve()
    assert after.spend_alert is None


def test_nothing_is_written_before_the_last_launch_was_read():
    """Until `restore_machine` runs the app holds defaults, and writing them would
    overwrite what was remembered with what was not."""
    prefs.sync(_app(theme=True))
    assert not prefs.FILE.exists()


def test_a_window_sitting_still_writes_nothing(monkeypatch):
    app = _app()
    prefs.restore_machine(app)
    writes: list[dict] = []
    monkeypatch.setattr(prefs, "update_machine", writes.append)
    prefs.sync(app)
    prefs.sync(app)
    assert writes == []
    app.theme = False
    prefs.sync(app)
    prefs.sync(app)
    assert len(writes) == 1 and writes[0]["theme"] == "light"


def test_a_window_closed_on_the_picker_opens_on_the_picker(tmp_path):
    app = _app()
    prefs.restore_machine(app)
    app.opened, app.root = True, tmp_path
    prefs.sync(app)
    app.opened = False
    prefs.sync(app)
    assert prefs.restore_machine(_app()) is None


def test_a_provider_switched_off_since_is_not_restored_and_the_composer_says_so(monkeypatch):
    _only_these_providers(monkeypatch, "anthropic")
    prefs.update_machine({"provider": "ollama", "model": "qwen3:8b", "effort": "medium"})
    app = _app()
    prefs.restore_machine(app)
    assert app.provider == "anthropic"
    assert app.model != "qwen3:8b"
    assert app.effort == "medium", "effort is not the provider's"
    reason, remedy = app.spend_alert
    assert "Ollama" in reason and "switched off" in reason
    assert remedy.startswith("Using ")


def test_a_kind_this_build_does_not_have_is_not_restored(monkeypatch):
    prefs.update_machine({"provider": "mistral", "model": "large"})
    app = _app()
    prefs.restore_machine(app)
    assert app.provider == "anthropic"
    assert "mistral" in app.spend_alert[0]


def test_a_retired_model_falls_back_to_the_default_and_says_so(monkeypatch):
    from portia.agent.providers import anthropic

    _only_these_providers(monkeypatch, "anthropic")
    prefs.update_machine({"provider": "anthropic", "model": "claude-2.1"})
    app = _app()
    prefs.restore_machine(app)
    assert app.model == anthropic.DEFAULT_MODEL
    assert app.model_unconfirmed == ""
    assert "claude-2.1" in app.spend_alert[0]
    shown = next(m.shown for m in anthropic.CATALOG if m.name == anthropic.DEFAULT_MODEL)
    assert shown in app.spend_alert[1], "named the way the picker names it"


def test_a_model_in_the_catalog_is_restored_without_a_word(monkeypatch):
    from portia.agent.providers import anthropic

    _only_these_providers(monkeypatch, "anthropic")
    model = anthropic.CATALOG[-1].name
    prefs.update_machine({"provider": "anthropic", "model": model})
    app = _app()
    prefs.restore_machine(app)
    assert app.model == model
    assert app.spend_alert is None


def test_values_of_the_wrong_shape_are_ignored_one_by_one(monkeypatch):
    prefs.update_machine(
        {
            "theme": "purple",
            "effort": "extreme",
            "interpret": "yes",
            "reopen": 1,
            "files_width": True,
            "transcript_width": -40,
            "panes": {"enormous": [True, True], "narrow": [True], "medium": [False, True]},
            "project": 42,
        }
    )
    app = _app()
    assert prefs.restore_machine(app) is None
    fresh = _app()
    assert app.theme is fresh.theme
    assert app.effort == fresh.effort
    assert app.interpret is fresh.interpret
    assert app.reopen_last is fresh.reopen_last
    assert (app.files_width, app.transcript_width) == (None, None)
    assert app.pane_choices == {"medium": (False, True)}


# --- a server's model, checked when its list arrives ------------------------


def _listed(*names):
    from portia.agent.providers import Model

    return [Model(name=n) for n in names]


def test_a_remembered_server_model_that_is_gone_falls_back_on_the_first_list():
    from portia.agent.providers import ollama
    from portia.ui import engine

    app = _app(provider="ollama", model="qwen3:8b", model_unconfirmed="qwen3:8b")
    engine._confirm_restored_model(app, "ollama", _listed("llama3:8b"))
    assert app.model == ollama.PROVIDER.default_model
    assert app.model_unconfirmed == ""
    assert "qwen3:8b" in app.spend_alert[0] and "Ollama" in app.spend_alert[0]


def test_a_remembered_server_model_that_is_listed_stays():
    from portia.ui import engine

    app = _app(provider="ollama", model="qwen3:8b", model_unconfirmed="qwen3:8b")
    engine._confirm_restored_model(app, "ollama", _listed("llama3:8b", "qwen3:8b"))
    assert app.model == "qwen3:8b"
    assert app.model_unconfirmed == ""
    assert app.spend_alert is None


def test_an_empty_list_proves_nothing_and_the_check_waits():
    """A server that is down lists nothing; its models are not gone."""
    from portia.ui import engine

    app = _app(provider="ollama", model="qwen3:8b", model_unconfirmed="qwen3:8b")
    engine._confirm_restored_model(app, "ollama", [])
    assert app.model == "qwen3:8b"
    assert app.model_unconfirmed == "qwen3:8b"


def test_a_model_picked_before_the_list_arrived_is_left_alone():
    from portia.ui import engine

    app = _app(provider="ollama", model="llama3:8b", model_unconfirmed="qwen3:8b")
    engine._confirm_restored_model(app, "ollama", _listed("mistral:7b"))
    assert app.model == "llama3:8b"
    assert app.spend_alert is None


def test_another_providers_list_does_not_check_it():
    from portia.ui import engine

    app = _app(provider="ollama", model="qwen3:8b", model_unconfirmed="qwen3:8b")
    engine._confirm_restored_model(app, "llamacpp", _listed("Qwen3-8B"))
    assert app.model_unconfirmed == "qwen3:8b"


# --- the side panes ---------------------------------------------------------


def test_a_pane_closed_in_one_band_stays_closed_in_that_band_only():
    from portia.ui.state import MEDIUM, NARROW_BAND, WIDE

    app = _app()
    app.resize(WIDE)
    assert app.set_panes(True, False)
    app.resize(MEDIUM - 1)
    assert app.band == NARROW_BAND
    assert (app.show_files, app.show_transcript) == (False, False), "narrow's defaults"
    app.resize(WIDE)
    assert (app.show_files, app.show_transcript) == (True, False), "the choice made here"


def test_a_remembered_width_is_drawn_inside_this_windows_limits():
    from portia.ui import app as app_module

    assert app_module._width(None, 260, (150, 520)) == 260
    assert app_module._width(900, 260, (150, 520)) == 520, "dragged in a wider window"
    assert app_module._width(40, 260, (150, 520)) == 150, "under the floor it would close"
    assert app_module._width(333, 260, (150, 520)) == 333


def test_picking_a_theme_is_what_is_remembered():
    from portia.ui import theme
    from portia.ui.state import APP

    was = APP.theme
    try:
        theme.set_mode(True)
        assert APP.theme is True and theme.mode() is True
    finally:
        APP.theme = was


# --- reopening the last project ---------------------------------------------


@pytest.fixture
def launch(monkeypatch, tmp_path):
    """`app.restore` as a fresh process would run it, on a fresh window."""
    from portia.ui import app as app_module
    from portia.ui.state import App

    window = App()
    monkeypatch.setattr(app_module, "APP", window)
    monkeypatch.setattr(app_module, "_restored", False)
    # `open_project` moves the process into the project; this puts it back.
    monkeypatch.chdir(tmp_path)
    return app_module, window


def _left_open(root, *, reopen: bool = True) -> None:
    prefs.update_machine({"project": prefs.key(root), "reopen": reopen})


def test_the_project_open_at_the_last_close_is_opened_again(tmp_path, launch):
    app_module, window = launch
    project = tmp_path / "proj"
    project.mkdir()
    _left_open(project)
    app_module.restore()
    assert window.opened and window.root == project.resolve()


def test_reopening_happens_once_per_process_and_never_on_a_reload(tmp_path, launch):
    app_module, window = launch
    project = tmp_path / "proj"
    project.mkdir()
    _left_open(project)
    app_module.restore()
    window.opened = False  # back to the picker, on purpose
    app_module.restore()  # the page is reloaded
    assert window.opened is False


def test_a_project_folder_that_is_gone_leaves_the_picker_showing(tmp_path, launch):
    app_module, window = launch
    _left_open(tmp_path / "deleted")
    app_module.restore()
    assert window.opened is False
    assert not (tmp_path / "deleted").exists(), "opening would have created it"


def test_the_switch_off_leaves_the_picker_showing(tmp_path, launch):
    app_module, window = launch
    project = tmp_path / "proj"
    project.mkdir()
    _left_open(project, reopen=False)
    app_module.restore()
    assert window.opened is False


def test_a_project_named_on_the_command_line_wins(tmp_path, launch):
    app_module, window = launch
    named, remembered = tmp_path / "named", tmp_path / "remembered"
    named.mkdir()
    remembered.mkdir()
    _left_open(remembered)
    app_module.open_at_start(named)
    app_module.restore()
    assert window.root == named.resolve()


def test_a_project_that_will_not_open_leaves_the_picker_showing(tmp_path, launch, monkeypatch):
    app_module, window = launch
    project = tmp_path / "proj"
    project.mkdir()
    _left_open(project)

    def refuse(*_a, **_k):
        raise PermissionError("not yours")

    monkeypatch.setattr(app_module, "open_at_start", refuse)
    app_module.restore()
    assert window.opened is False


def test_two_windows_overwrite_each_other_only_on_what_both_changed():
    """Two processes, each holding what it read at its own start: the one that
    writes last must not put back what the other changed since."""
    first, second = _app(), _app()
    prefs.restore_machine(first)
    written_by_first = prefs._written_machine
    prefs.restore_machine(second)
    written_by_second = prefs._written_machine

    prefs._written_machine = written_by_first
    first.theme = True
    prefs.sync(first)

    prefs._written_machine = written_by_second
    second.files_width = 320
    prefs.sync(second)

    saved = prefs.machine()
    assert saved["theme"] == "dark", "the other window's pick survives"
    assert saved["files_width"] == 320

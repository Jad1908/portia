"""The human edge of the agent loop.

Both defects covered here were found by running the copilot by hand, not by
reading the code: a keystroke meant for a write confirmation was consumed as the
answer to a question, and Ctrl-C buried the transcript under an `anyio`
traceback. Neither is engine behaviour, and both corrupt the only measurement
that matters — what the human actually saw and said (docs/EVALUATION.md).
"""

from __future__ import annotations

import pytest

from portia.cli import chat

# --- type-ahead --------------------------------------------------------------


def test_a_keystroke_typed_before_the_prompt_is_not_taken_as_the_answer(monkeypatch):
    """The Run 6 defect: a queued `Y` answered a question it was never meant for."""
    flushed: list[str] = []

    monkeypatch.setattr(chat.sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setattr("termios.tcflush", lambda *_: flushed.append("flushed"))
    monkeypatch.setattr("builtins.input", lambda prompt: "1")

    assert chat._read("pick? ") == "1"
    assert flushed == ["flushed"]


def test_nothing_is_flushed_when_input_is_piped(monkeypatch):
    """A pipe has no type-ahead to discard, and flushing one is an error."""
    monkeypatch.setattr(chat.sys.stdin, "isatty", lambda: False, raising=False)
    monkeypatch.setattr("termios.tcflush", lambda *_: pytest.fail("flushed a pipe"))
    monkeypatch.setattr("builtins.input", lambda prompt: "piped")

    assert chat._read("pick? ") == "piped"


# --- ending a turn -----------------------------------------------------------


def test_ctrl_c_ends_the_turn_instead_of_crashing(monkeypatch, capsys):
    async def interrupted(*_args, **_kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(chat, "run_and_render", interrupted)
    chat.run_turn("anything", model="m", effort=None, cwd=".", portia_dir=".portia")

    assert "[interrupted]" in capsys.readouterr().out


def test_a_real_error_still_surfaces(monkeypatch):
    """Only Ctrl-C is an ordinary exit; a bug must not be swallowed with it."""

    async def broken(*_args, **_kwargs):
        raise RuntimeError("the engine broke")

    monkeypatch.setattr(chat, "run_and_render", broken)
    with pytest.raises(RuntimeError, match="the engine broke"):
        chat.run_turn("anything", model="m", effort=None, cwd=".", portia_dir=".portia")


def test_a_project_can_be_described_with_nothing_to_index(tmp_path, monkeypatch, capsys):
    """A warehouse project has no files. `--init` alone is a whole, successful call."""
    import sys

    from portia import catalog
    from portia.cli import index

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["portia index", "--init", "Orders on a warehouse."])
    index.main()
    assert catalog.load_catalog(".portia")["project"] == "Orders on a warehouse."
    assert "project context set" in capsys.readouterr().out


def test_indexing_nothing_and_describing_nothing_is_refused(tmp_path, monkeypatch):
    import sys

    import pytest

    from portia.cli import index

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["portia index"])
    with pytest.raises(SystemExit) as refused:
        index.main()
    assert refused.value.code == 2

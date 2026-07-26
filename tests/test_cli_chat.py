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

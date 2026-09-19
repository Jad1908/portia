"""`portia <command>` — one name on the PATH, for an install that is not a checkout."""

import sys

import pytest

from portia.cli import __main__ as portia_command


def test_every_edge_is_a_command_and_the_window_is_one_too():
    known = portia_command.commands()
    assert known[0] == "ui"
    for name in ("build", "connect", "index", "journal", "import_data"):
        assert name in known


def test_the_two_a_host_starts_are_not_offered_to_a_person():
    assert not {"serve", "hook"} & set(portia_command.commands())


def test_the_command_is_handed_its_own_arguments(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["portia", "connect", "providers"])
    portia_command.main()
    assert "snowflake" in capsys.readouterr().out


def test_an_unknown_command_is_refused_with_the_ones_there_are(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["portia", "nope"])
    with pytest.raises(SystemExit, match="no command 'nope'"):
        portia_command.main()


def test_every_command_the_skill_and_the_guide_name_exists():
    """A guide an agent follows is only as good as its commands."""
    import re
    from pathlib import Path

    root = Path(__file__).parent.parent
    texts = [root / "INSTALL.md", root / "plugin" / "skills" / "portia" / "SKILL.md"]
    named = set()
    for path in texts:
        text = path.read_text()
        # Commands only: a line inside a fenced block, or a whole inline code span.
        # Prose such as "portia needs a few sentences" is not one.
        fenced = "\n".join(re.findall(r"```[a-z]*\n(.*?)```", text, re.S))
        named |= set(re.findall(r"^(?:uv run )?portia ([a-z_]+)\b", fenced, re.M))
        spans = re.findall(r"`((?:uv run )?portia [^`]+)`", text)
        named |= {span.removeprefix("uv run ").split()[1] for span in spans}
    named = {n for n in named if not n.startswith("-") and n != "..."}
    assert named, "the guide names no command, so this test reads nothing"
    assert named <= set(portia_command.commands()), named - set(portia_command.commands())

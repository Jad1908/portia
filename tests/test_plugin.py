"""The Claude Code plugin: what it names has to exist, and its skill is a rendered copy."""

import json
import tomllib
from pathlib import Path

from devtools import plugin

ROOT = Path(__file__).parent.parent
PLUGIN = ROOT / "plugin"


def _scripts():
    return tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["scripts"]


def test_the_skill_is_what_the_prompts_render_to():
    """Edit `prompts/copilot.md` or `prompts/headless/skill.md`, then run
    `python -m devtools.plugin`. A skill that drifted from the method is two copilots."""
    assert plugin.SKILL.read_text() == plugin.render()


def test_the_method_is_in_the_skill_word_for_word():
    from portia.agent import prompts

    assert prompts.load("copilot") in plugin.SKILL.read_text()


def test_nothing_is_left_unfilled():
    body = plugin.SKILL.read_text()
    assert "{method}" not in body and "{indexing}" not in body and "{names}" not in body


def test_the_server_the_plugin_starts_is_a_command_the_package_installs():
    servers = json.loads((PLUGIN / ".mcp.json").read_text())["mcpServers"]
    assert {s["command"] for s in servers.values()} <= _scripts().keys()


def test_every_hook_is_a_command_the_package_installs_and_an_event_it_knows():
    from portia.cli import hook

    hooks = json.loads((PLUGIN / "hooks" / "hooks.json").read_text())["hooks"]
    commands = [h["command"] for groups in hooks.values() for g in groups for h in g["hooks"]]
    assert commands
    for command in commands:
        script, event = command.split()
        assert script in _scripts()
        assert event in hook._EVENTS


def test_the_guard_is_matched_to_every_tool_it_knows_how_to_refuse():
    from portia.cli import hook

    hooks = json.loads((PLUGIN / "hooks" / "hooks.json").read_text())["hooks"]
    (pre,) = hooks["PreToolUse"]
    assert set(pre["matcher"].split("|")) == set(hook._WRITES) | set(hook._READS)


def test_the_marketplace_points_at_the_plugin():
    market = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text())
    (listed,) = market["plugins"]
    assert (ROOT / listed["source"] / ".claude-plugin" / "plugin.json").is_file()
    manifest = json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text())
    assert listed["name"] == manifest["name"]

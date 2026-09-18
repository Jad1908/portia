"""The two rules a host has to enforce, as Claude Code hooks.

`portia-mcp` puts the tools in a host (`cli/serve.py`). Two things the app held
in its own loop cannot be held by a tool server, because neither is a tool call:

- **Review before you reply.** `agent/curation.py` holds a reply once, through
  the SDK's ``Stop`` hook, until `review_queries` has run. It exists because the
  same rule as prose was followed zero times in 27 queries (`FINDINGS.md` §5.3),
  and a skill is prose. Claude Code has the same hook, as a command.
- **Nothing enters a spec unmeasured, and no number comes off a file.** In the
  app both hold by construction: the copilot has no filesystem. A host's model
  has one, so the two obvious ways round the tools are refused here. An edit to
  a file portia writes is sent to the tool that measures what it writes, and a
  read of an indexed data file is sent to the checks.

  **This is a guard and not a sandbox, and it says so.** A shell command gets
  past it, and that was accepted with the open design. It closes the door a
  model walks through by default, the file tool, so that going round the tools
  takes a decision and leaves a command in the host's transcript.

Each is one short process per event, so everything heavy is imported after the
cheap checks have had the chance to say *not ours*. **A hook that fails must
never block**: every unexpected shape is answered with silence, because a
session stopped by portia's bug in a project that merely contains a `.portia/`
folder is worse than a rule that was not enforced once.

Claude Code's hook contract, as used here: the event arrives as JSON on stdin,
exit 0 with nothing on stdout means *carry on*, and a JSON object on stdout
makes the decision.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

#: The host's file tools, and which input field names the file.
_WRITES = {"Edit": "file_path", "Write": "file_path", "MultiEdit": "file_path"}
_WRITES["NotebookEdit"] = "notebook_path"
_READS = {"Read": "file_path"}

#: `agent/tools.SERVER_NAME`, restated because importing that module imports the
#: Agent SDK, and this process runs before every reply. A test holds the two equal.
_SERVER = "portia"


# --- review before you reply ------------------------------------------------


def stop(event: dict[str, Any]) -> dict[str, Any] | None:
    """Hold the reply once if this exchange asked the data and reviewed nothing.

    The exchange is read off **the host's own transcript** and not off portia's
    log. The transcript is the one file that is certainly this session's: two
    hosts on one project write two portia logs, and a hook cannot tell which is
    its own. It also means the rule holds for a session whose server was
    restarted halfway.

    ``stop_hook_active`` is the host saying this stop is already the
    continuation of a held one, which is `Curation.hold`'s *once, never twice*
    without any state of ours.
    """
    if event.get("stop_hook_active"):
        return None
    from portia.agent import curation, prompts

    curator = curation.Curation()
    for name in _tools_since_the_last_prompt(event.get("transcript_path")):
        curator.saw_tool(name)
    if not curator.hold():
        return None
    return {"decision": "block", "reason": prompts.error("review_before_reply")}


def _tools_since_the_last_prompt(transcript: Any) -> list[str]:
    """portia's tools called since the human last spoke, by their bare names."""
    if not transcript:
        return []
    called: list[str] = []
    try:
        lines = Path(str(transcript)).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in lines:
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if not isinstance(record, dict):
            continue
        content = (record.get("message") or {}).get("content")
        if record.get("type") == "user" and _is_a_prompt(content):
            called = []
        elif record.get("type") == "assistant" and isinstance(content, list):
            called += [
                _label(block.get("name") or "")
                for block in content
                if isinstance(block, dict) and block.get("type") == "tool_use"
            ]
    return [name for name in called if name]


def _is_a_prompt(content: Any) -> bool:
    """A human's message, as opposed to the tool results that share its record type."""
    if isinstance(content, str):
        return True
    if not isinstance(content, list):
        return False
    return not any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content)


def _label(name: str) -> str:
    """``mcp__plugin_portia_portia__query_data`` → ``query_data``; anything else → ``""``.

    The prefix is the host's and depends on how the server was installed (a
    plugin's differs from a project's `.mcp.json`), so the rule is only that the
    server's name is in it.
    """
    head, _, tail = name.rpartition("__")
    return tail if head.startswith("mcp__") and _SERVER in head else ""


# --- the file tools ---------------------------------------------------------


def guard(event: dict[str, Any]) -> dict[str, Any] | None:
    """Refuse a hand edit of what portia writes, or a read of data it has indexed."""
    tool = str(event.get("tool_name") or "")
    field = _WRITES.get(tool) or _READS.get(tool)
    target = (event.get("tool_input") or {}).get(field or "")
    if not field or not target:
        return None
    root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or event.get("cwd") or ".").resolve()
    from portia import catalog

    if not (root / catalog.DEFAULT_DIR).is_dir():
        return None  # not a portia project: none of this is ours to say
    path = Path(str(target)).expanduser()
    path = (path if path.is_absolute() else root / path).resolve()
    if not path.is_relative_to(root):
        return None
    rel = path.relative_to(root)
    from portia.agent import prompts

    if tool in _WRITES:
        if _is_portias(rel):
            return _deny(prompts.error("hand_edit", path=rel.as_posix()))
    elif _is_indexed_data(rel, root):
        return _deny(prompts.error("raw_read", path=rel.as_posix()))
    return None


def _is_portias(rel: Path) -> bool:
    """Whether portia writes this file: the four artifacts, the models and the catalog."""
    from portia import catalog, figures, findings, pipeline, spec

    owned = (
        spec.SPECS_DIR,
        pipeline.MODELS_DIR,
        findings.FINDINGS_DIR,
        figures.FIGURES_DIR,
        catalog.DEFAULT_DIR,
    )
    return bool(rel.parts) and rel.parts[0] in owned


def _is_indexed_data(rel: Path, root: Path) -> bool:
    """Whether this is a file the catalog has measured, or one waiting in the data folder.

    Precise on purpose. A suffix rule would refuse the CSV of test fixtures in
    somebody's repository, which portia has no opinion about; the catalog names
    the files whose numbers have to come from a check.
    """
    from portia import catalog

    portia_dir = root / catalog.DEFAULT_DIR
    try:
        entries = catalog.load_catalog(portia_dir)["sources"].values()
        data_dir = catalog.project_settings(portia_dir)["data_dir"]
    except Exception:  # noqa: BLE001 - an unreadable catalog guards nothing
        return False
    for entry in entries:
        ref = entry.get("source")
        if isinstance(ref, str) and (root / ref).resolve() == (root / rel).resolve():
            return True
    if data_dir and rel.is_relative_to(Path(data_dir)):
        from portia.core.io import supported_suffixes

        return rel.suffix.lower() in supported_suffixes()
    return False


def _deny(reason: str) -> dict[str, Any]:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


# --- the process ------------------------------------------------------------

_EVENTS = {"stop": stop, "guard": guard}


def main() -> None:
    parser = argparse.ArgumentParser(description="portia's Claude Code hooks.")
    parser.add_argument("event", choices=sorted(_EVENTS))
    args = parser.parse_args()
    try:
        event = json.loads(sys.stdin.read() or "{}")
        decision = _EVENTS[args.event](event if isinstance(event, dict) else {})
    except Exception:  # noqa: BLE001 - a hook that fails must never block (see the docstring)
        return
    if decision is not None:
        print(json.dumps(decision))


if __name__ == "__main__":
    main()

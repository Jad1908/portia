"""Write the Claude Code plugin's skill from the prompts it is made of.

`python -m devtools.plugin` writes `plugin/skills/portia/SKILL.md`, and
`tests/test_plugin.py` fails when the committed file is not what this renders.

**A copy, and the test is what makes that safe.** Every string a model reads lives
in `portia/agent/prompts/` (`CLAUDE.md`), and a skill is a file Claude Code reads
from the plugin's own folder, which an installed plugin has and the rest of this
repository it does not. So the skill cannot be a link into `prompts/`. It is
rendered from three prompt files: the head that says what differs inside a host,
`copilot.md` verbatim as the method, and `tasks/index_batch.md` verbatim as the
read after indexing. Editing the method for the app's copilot therefore edits it
for a host's, in the same commit, or the test says so.

Here rather than in `portia/`: nothing the package does at run time needs it, and
the wheel does not ship `plugin/`.
"""

from __future__ import annotations

from pathlib import Path

from portia.agent import prompts

SKILL = Path(__file__).parent.parent / "plugin" / "skills" / "portia" / "SKILL.md"

#: What Claude Code reads to decide when to load the skill. Frontmatter, not
#: instruction: it is matched against the task, never followed.
FRONTMATTER = """---
name: portia
description: >-
  How to work with portia's data tools (get_context, describe_source,
  profile_source, join_findings, query_data, plot_data, record_step,
  record_finding and the rest of the portia MCP server). Load this before the
  first portia tool call in a session, and whenever the user asks to explore,
  profile, index, join, harmonize or build a table or pipeline from the data in
  a project that has a .portia folder.
---
"""


def render() -> str:
    body = prompts.load("headless/skill").format(
        method=prompts.load("copilot"),
        indexing=prompts.task("index_batch", names="the ones the command just listed"),
    )
    return f"{FRONTMATTER}\n{body}\n"


def main() -> None:
    SKILL.parent.mkdir(parents=True, exist_ok=True)
    SKILL.write_text(render(), encoding="utf-8")
    print(f"wrote {SKILL.relative_to(Path.cwd()) if SKILL.is_relative_to(Path.cwd()) else SKILL}")


if __name__ == "__main__":
    main()

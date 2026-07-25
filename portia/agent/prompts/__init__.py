"""Loading the instruction text. See ``README.md`` in this directory for the layout.

Every string the copilot reads comes through here, so there is exactly one place
to look when behaviour needs tuning. Missing text is a hard error rather than an
empty default: a tool whose description silently vanished would still be *offered*
to the model, which is a subtle and expensive failure.
"""

from __future__ import annotations

import re
from pathlib import Path

HERE = Path(__file__).parent

#: An HTML comment at the top of a file documents its placeholders for whoever is
#: editing it. It is for humans; strip it so it never reaches the model.
_EDITOR_NOTE = re.compile(r"\A\s*<!--.*?-->\s*", re.DOTALL)


def load(name: str) -> str:
    """Read one prompt by path-ish name, e.g. ``"copilot"`` or ``"tools/run_spec"``."""
    path = HERE / f"{name}.md"
    if not path.exists():
        raise FileNotFoundError(f"no prompt {name!r} — expected {path}")
    return _EDITOR_NOTE.sub("", path.read_text()).strip()


def tool(name: str) -> str:
    """A tool's description, as the model will read it.

    Markdown is collapsed to a single block: tool descriptions are delivered as a
    plain string in the schema, so hard-wrapping in the source file shouldn't leak
    into what the model sees.
    """
    return " ".join(load(f"tools/{name}").split())


def task(name: str, **fields: object) -> str:
    """A CLI command's opening instruction, with its placeholders filled."""
    return load(f"tasks/{name}").format(**fields)


def names(kind: str) -> set[str]:
    """Every prompt filed under ``kind`` (``"tools"``, ``"tasks"``). For tests."""
    return {p.stem for p in (HERE / kind).glob("*.md")}

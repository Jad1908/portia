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


def tool(name: str, **fields: object) -> str:
    """A tool's description, **verbatim** — the file is what the model reads.

    This used to collapse the markdown to a single line, and the reasoning was
    that *"tool descriptions are delivered as a plain string in the schema, so
    hard-wrapping in the source file shouldn't leak into what the model sees."*
    The premise is wrong. A description is a JSON string, newlines are legal in
    one, and they survive the in-process MCP server's tool listing unchanged
    (measured 2026-08-25; `tests/test_agent_prompts.py` now pins it). Nothing was
    ever forcing the join.

    What the join actually cost is the structure, not the wrapping: it deleted
    every blank line, heading and list start along with the hard wraps.
    ``record_step.md`` is 155 lines with seven headings and arrived as one
    8,577-character string with its ``## 'sql'`` heading run into the body, and
    ``graph_lookup.md``'s four bullets arrived as one sentence joined by
    semicolons. Prompt text is the least stable, most performance-sensitive part
    of this system (CLAUDE.md), so it is worth 263 characters across all ten
    descriptions to have the model read the document we wrote.

    Unwrapping the prose while keeping the structure was the other candidate and
    it was turned down. It needs a markdown parser in the prompt loader, and a
    heuristic that guesses wrong corrupts prompt text with nothing reviewing the
    result. Verbatim has no transformation to review: the file, the string in
    `runlog`'s ``PROMPTS`` record, and what `devtools.context` renders are one
    text. The cost accepted in exchange is that reflowing a paragraph in an
    editor is a prompt change, which is why it shows up in a diff.

    ``fields`` fills placeholders with values the *code* owns — the set of
    transforms an op accepts, the fields it reports. Those belong in the
    description (the model has to know them) but must not be retyped into it,
    or the prose and the code drift apart silently. A description with
    placeholders must therefore be given every one of them; a literal brace in
    such a file has to be doubled.
    """
    text = load(f"tools/{name}")
    return text.format(**fields) if fields else text


def task(name: str, **fields: object) -> str:
    """A CLI command's opening instruction, with its placeholders filled."""
    return load(f"tasks/{name}").format(**fields)


def error(name: str, **fields: object) -> str:
    """The message a refused tool call hands back, with its facts filled in.

    A refusal is read at the exact moment the model is deciding what to do next,
    which makes its wording as load-bearing as any tool description — so it lives
    here as prose rather than in an f-string at the raise site.
    """
    return load(f"errors/{name}").format(**fields)


def names(kind: str) -> set[str]:
    """Every prompt filed under ``kind`` (``"tools"``, ``"tasks"``). For tests."""
    return {p.stem for p in (HERE / kind).glob("*.md")}

"""The bits of HTML both dev pages write the same way.

`page.py` (the run audit) and `contextpage.py` (what the agent is given) are two
different readings of the same loop, and they were always going to need the same
five things: escape a value, inline an asset, clip a preview, render a JSON-ish
value as something a human reads, and fold a long block of text away behind a
summary. This is where those live, for the reason `CLAUDE.md` gives about shared
helpers — the alternative is two `_esc`s that drift in their handling of quotes
and one page that renders a `record_step` input readably while the other does
not.

Nothing here knows what a transcript or a tool is. That belongs to the page.
"""

from __future__ import annotations

import html
from pathlib import Path
from typing import Any

ASSETS = Path(__file__).parent / "assets"


def esc(text: Any) -> str:
    return html.escape(str(text), quote=True)


def asset(name: str) -> str:
    """The CSS and the JS, inlined. A page has to survive being emailed."""
    return (ASSETS / name).read_text(encoding="utf-8")


def clip(text: Any, chars: int) -> str:
    flat = " ".join(str(text).split())
    return flat if len(flat) <= chars else flat[:chars] + "…"


def money(cost: Any) -> str:
    return f"${float(cost):.4f}" if cost else ""


def folded(label: str, text: str, *, cls: str = "fold", open_: bool = False) -> str:
    """A block of text behind a summary, with how much of it there is.

    The size is on the summary rather than inside because the question you ask
    of a collapsed block is whether it is worth opening, and for prompt text
    that question is mostly "how much of my context is this".
    """
    return (
        f'<details class="{cls}"{" open" if open_ else ""}>'
        f'<summary>{esc(label)}<span class="size">{len(text):,} chars</span></summary>'
        f'<pre class="block">{esc(text)}</pre></details>'
    )


def value_html(value: Any) -> str:
    """A tool argument as something readable.

    Pretty-printed JSON is the obvious move and it is wrong for the argument
    that matters most: a `record_step` carries SQL, and inside a JSON string
    every newline is a literal ``\\n``, so the one thing you most need to read is
    the one thing least readable. Multi-line strings get their own block.
    """
    if isinstance(value, dict):
        rows = "".join(
            f'<div class="kv"><span class="k">{esc(str(k))}</span>{value_html(v)}</div>'
            for k, v in value.items()
        )
        return f'<div class="obj">{rows}</div>' if rows else '<span class="v none">{}</span>'
    if isinstance(value, list):
        if not value:
            return '<span class="v none">[]</span>'
        if all(not isinstance(item, dict | list) for item in value):
            return f'<span class="v">{esc(", ".join(str(i) for i in value))}</span>'
        items = "".join(f'<div class="item">{value_html(i)}</div>' for i in value)
        return f'<div class="arr">{items}</div>'
    if value is None:
        return '<span class="v none">null</span>'
    text = str(value)
    if "\n" in text:
        return f'<pre class="block sql">{esc(text)}</pre>'
    return f'<span class="v">{esc(text)}</span>'

"""The copilot — the layer that does the *judging*.

`CLAUDE.md` refuses a deterministic `decide` module on purpose: choosing what
matters, what to ask, and what to recommend is context-dependent judgment that
fails at scale when baked into code. So "decide" is an **agent**, not an
algorithm — a Claude Agent SDK loop reading the checks' evidence through an
in-process MCP server.

Layout:

- ``handlers.py`` — the callable surface, as pure functions. No SDK imports.
- ``tools.py`` — ``@tool`` wrappers + the in-process MCP server. The only
  place the SDK meets the engine.
- ``events.py`` — SDK messages normalized into portia events. The seam the UI
  will sit on (docs/TECH_STACK.md).
- ``ask.py`` — intercepts ``AskUserQuestion`` so a question reaches the human.
- ``session.py`` — the options block and the client lifecycle.
- ``prompts/`` — the system prompt, as editable prose.

Requires the ``agent`` extra: ``uv sync --extra agent``.
"""

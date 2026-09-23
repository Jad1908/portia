"""Everything the copilot is given, gathered from the code that gives it:

    python -m devtools.context sandbox/gui
    python -m devtools.context --no-logs -o just-the-prompts.html

A dev tool for tuning the loop, not part of the product — the sibling of
`devtools/audit.py`, and the other half of the same job. The audit page answers
*what did it do*; this one answers **what was it given to do it with**. Both
render one self-contained HTML file and open it: no server, nothing to keep
running, and a page you can keep beside the one you generate after a prompt
edit.

Three things reach the model and they are in three different places in this
repo, which is why reading them together needs a tool at all:

* **The system prompt** — `prompts/copilot.md` plus a brief composed from the
  project's own `.portia/`. Half of it does not exist until you name a project,
  so this page is always rendered *against* one.
* **The tool list** — descriptions from `prompts/tools/`, schemas from
  `agent/tools.py`, and the two are assembled by the SDK. Taken here from the
  MCP server itself rather than rebuilt, so what you read is the payload the
  model is sent.
* **Everything the engine says back** — refusals from `prompts/errors/`, and a
  larger number of validation messages raised as plain strings in the modules a
  tool call bottoms out in. Those are read at the moment the model is choosing
  what to do next, which makes them prompt text whether or not they live in
  `prompts/`.

What a tool *returns* is not in any of those, and cannot be derived: it depends
on the data. So it is read from real runs — `.portia/chats/` and
`.portia/indexing/`, through `portia.runlog`, the same parser `cli.history` and
the audit page use. A tool nothing has called says so rather than showing an
invented example.
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
import webbrowser
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import portia
from devtools import page
from portia import runlog
from portia.agent import prompts as prompt_files
from portia.catalog import DEFAULT_DIR

#: Where a page lands when you do not say. Gitignored, and stamped rather than
#: overwritten — comparing a run against the one before it is the whole point.
OUT_DIR = Path(__file__).parent / "out"

STAMP = "%Y-%m-%dT%H-%M-%S"

#: The installed portia, whatever checkout this is. Everything scanned below is
#: read out of here, so the page describes the code that is actually loaded.
PACKAGE = Path(portia.__file__).resolve().parent

#: Where a tool sits in the disclosure ladder, in the words `copilot.md` and
#: `CLAUDE.md` use. **A label, not a mechanism** — nothing in the engine ranks
#: tools, and this map does not either; it is the placement the prompts teach,
#: written down so a page can group by it.
#:
#: `graph_lookup` is deliberately not a rung (`KNOWLEDGE_GRAPH.md` §9.1): the
#: rungs are depth on one table and assume you know which one, and the router is
#: what tells you which. Kept here rather than derived because no tool
#: description states its own rung — and pinned by `tests/test_devtools_context.py`,
#: which fails when a tool is added without a placement.
LADDER = {
    "graph_lookup": ("router", "Before L2 — which table, and where a column came from."),
    "get_context": ("L1", "The brief again, to pick up changes made this session."),
    "describe_source": ("L2", "One source's columns and roles. No statistics."),
    "profile_source": ("L3", "One source's full measured facts."),
    "join_findings": ("L4", "What a join would really do, plus example rows."),
    "query_data": (
        "L5",
        "One SELECT over named tables, when the question spans them. Writes nothing.",
    ),
    "plot_data": (
        "beside L5",
        "The same SELECT with the rows drawn on screen instead of returned.",
    ),
    "view_chart": (
        "beside plot_data",
        "A picture of a drawn chart, for judging the drawing. Vision models only.",
    ),
    "measure_overlaps": (
        "measure",
        "Compares column pairs the agent picked, and keeps the answers.",
    ),
    "set_interpretation": ("write", "Records what a table is, and a note learned about it."),
    "set_group": ("write", "Records that some sources belong together."),
    "record_step": ("write", "Runs a decided step, measures it, records it if it holds."),
    "review_queries": ("curate", "This chat's query_data calls, numbered — read at the end."),
    "record_finding": ("write", "Keeps one thing learned, indexed by the tables it concerns."),
    "ask_user": (
        "ask",
        "One to four questions to the human, on the harness with no question of its own.",
    ),
    "read_spec": (
        "read",
        "One spec as recorded: steps verbatim, what it reads and is read by; "
        "its journal and last build on request.",
    ),
    "run_spec": ("verify", "Re-executes a spec and reports drift and outcomes."),
}

#: Modules whose exceptions reach the model. `tools.py` turns any exception out
#: of a handler into a tool result the agent reads, so a `ValueError` raised
#: four calls deep in the engine is prompt text with no author.
#:
#: Ordered agent-first because that is where nearly all of it is deliberate.
#: This list is *where to look*, not a claim that every message here is
#: reachable — which path can raise which error is exactly the thing a page must
#: not guess at, so each one is shown with its file and line for you to judge.
MESSAGE_SOURCES = (
    "agent",
    "spec.py",
    "ops",
    "catalog.py",
    "knowledge",
    "checks",
)

#: The four ways a prompt is loaded. Found by parsing rather than by importing,
#: because the question is *where in the code is this text used* and an import
#: graph cannot answer it — and by parsing rather than by grepping, because the
#: interesting ones are wrapped: `raise ValueError(prompts.error(` puts the name
#: on the following line, and a regex over lines reported four live refusals and
#: the whole `merge` task as dead text.
_LOADERS = ("load", "tool", "task", "error")

#: `{placeholder}` in a template. Doubled braces are a literal brace and are not
#: placeholders — `prompts.tool` says so.
_PLACEHOLDER = re.compile(r"(?<!\{)\{([a-z_][a-z0-9_]*)\}(?!\})", re.IGNORECASE)


@dataclass(frozen=True)
class Site:
    """Where a prompt is loaded, in portia's own source."""

    path: str
    line: int
    call: str


@dataclass(frozen=True)
class PromptFile:
    """One markdown file under `prompts/`, and what is known about its use."""

    name: str
    kind: str
    path: Path
    raw: str
    #: What the model actually receives, when that differs from the file —
    #: `record_step`'s description is a template filled from the ops. ``None``
    #: when this page cannot render it without inventing values.
    rendered: str | None = None
    placeholders: tuple[str, ...] = ()
    sites: tuple[Site, ...] = ()


@dataclass(frozen=True)
class Delivered:
    """One tool exactly as the SDK will describe it to the model."""

    name: str
    qualified: str
    description: str
    schema: dict[str, Any]
    read_only: bool
    auto_approved: bool
    rung: str
    rung_note: str
    prompt_path: Path | None
    raw_prompt: str
    handler_doc: str


@dataclass(frozen=True)
class Observation:
    """One real call, out of a real log — what went in and what came back."""

    log: str
    at: str | None
    seconds: float | None
    input: dict[str, Any]
    result: str
    is_error: bool
    allowed: bool | None


@dataclass(frozen=True)
class Message:
    """Text the engine hands the model that does not live in `prompts/`."""

    path: str
    line: int
    kind: str
    text: str
    #: Set when the string is `prompts.error("x")` rather than prose — the text
    #: is in the inventory above and repeating it here would be two copies of
    #: one sentence, which is the thing `prompts/` exists to prevent.
    prompt: str | None = None


@dataclass
class Injected:
    """The whole of what the agent is given, for one project."""

    project: Path
    portia_dir: str
    system_prompt: str
    l0: str
    l1: str
    #: False when the composed prompt is not simply L0 then L1 — said out loud
    #: rather than papered over, because the split shown below it would then be
    #: a reconstruction rather than the thing itself.
    composed_as_expected: bool
    tools: list[Delivered] = field(default_factory=list)
    prompts: list[PromptFile] = field(default_factory=list)
    observed: dict[str, list[Observation]] = field(default_factory=dict)
    messages: list[Message] = field(default_factory=list)
    logs: list[dict] = field(default_factory=list)
    #: Prompt files nothing loads, and tools with no file. Both are silent
    #: failures — a description that vanished still leaves its tool offered.
    orphans: list[str] = field(default_factory=list)

    @property
    def always_on_chars(self) -> int:
        """Everything pushed on every request: the prompt, and the tool list."""
        return len(self.system_prompt) + sum(len(t.description) for t in self.tools)


# --- gathering ---------------------------------------------------------------


def gather(project: Path, *, with_logs: bool = True) -> Injected:
    """Read the code, the prompts, the project and (optionally) its logs."""
    portia_dir = str(project / DEFAULT_DIR)
    system, l0, l1, composed = _system_prompt(portia_dir)
    tools = delivered_tools()
    sites = call_sites()
    files = prompt_inventory(sites)

    logs = runlog.logs_in(portia_dir) if with_logs and Path(portia_dir).is_dir() else []
    transcripts = [runlog.read(p) for p in logs]

    return Injected(
        project=project,
        portia_dir=portia_dir,
        system_prompt=system,
        l0=l0,
        l1=l1,
        composed_as_expected=composed,
        tools=tools,
        prompts=files,
        observed=observations(transcripts),
        messages=engine_messages(),
        logs=[runlog.summary(t) for t in transcripts],
        orphans=_orphans(tools, files),
    )


def _system_prompt(portia_dir: str) -> tuple[str, str, str, bool]:
    """The composed prompt, and the two halves it is composed from.

    Composed through `runlog.prompts_read`, which is what a chat log records —
    so this page and the log cannot disagree about what was sent. The halves are
    read from their own sources and then *checked* against the whole, rather
    than recovered by splitting on the separator: a separator that also appears
    inside `copilot.md` would silently cut the prompt in the wrong place, and
    the reader would have no way to tell.
    """
    from portia.agent import context, session

    read = runlog.prompts_read(portia_dir)
    system = str(read.get("system") or "")
    l0 = session.PROMPT_PATH.read_text(encoding="utf-8")
    l1 = context.build_brief(portia_dir)
    return system, l0, l1, system.startswith(l0.rstrip()) and system.endswith(l1.rstrip())


def delivered_tools() -> list[Delivered]:
    """Every tool as the MCP server will list it — description *and* schema.

    Read off the running server rather than off `ALL_TOOLS`, because the schema
    the model receives is not the one written in `tools.py`: the SDK expands the
    shorthand (`{"source": str}`) into JSON Schema at server-creation time. A
    page rebuilding that expansion would be a second implementation of it, and
    the day it drifts is the day this page quietly lies about the one thing it
    exists to show.
    """
    from portia.agent import handlers, tools

    listed = {t["name"]: t for t in _list_tools(tools)}
    auto = {t.name for t in tools.READ_TOOLS}
    out = []
    for registered in tools.ALL_TOOLS:
        name = registered.name
        described = listed.get(name, {})
        path = prompt_files.HERE / "tools" / f"{name}.md"
        rung, note = LADDER.get(name, ("", ""))
        out.append(
            Delivered(
                name=name,
                qualified=tools.qualified(name),
                description=str(described.get("description") or registered.description or ""),
                schema=dict(described.get("inputSchema") or {}),
                read_only=read_only(registered.annotations),
                auto_approved=name in auto,
                rung=rung,
                rung_note=note,
                prompt_path=path if path.exists() else None,
                raw_prompt=path.read_text(encoding="utf-8") if path.exists() else "",
                handler_doc=(getattr(handlers, name, None).__doc__ or "").strip(),
            )
        )
    return out


def read_only(annotations: Any) -> bool:
    """Whether a tool's annotations carry the read-only hint, on mcp 1.x or 2.x (the field was renamed)."""
    if annotations is None:
        return False
    for name in ("read_only_hint", "readOnlyHint"):
        value = getattr(annotations, name, None)
        if value is not None:
            return bool(value)
    return False


def _list_tools(tools: Any) -> list[dict[str, Any]]:
    """Ask the in-process MCP server for its tool list, the way the CLI does.

    Falls back to the registered objects if the SDK's internals move. The
    fallback loses the expanded schema and nothing else, so the page degrades to
    "the schema as written" rather than to nothing.
    """
    import asyncio

    try:
        from mcp.types import ListToolsRequest

        server = tools.build_server()["instance"]
        # Public on mcp 1.x, underscored on 2.x; the same table either way.
        handlers = getattr(server, "request_handlers", None) or server._request_handlers
        handler = handlers[ListToolsRequest]
        response = asyncio.run(handler(ListToolsRequest(method="tools/list")))
        return [t for t in response.root.model_dump().get("tools", [])]
    except Exception:  # noqa: BLE001 - a dev page must render without the SDK's internals
        return [
            {"name": t.name, "description": t.description, "inputSchema": t.input_schema}
            for t in tools.ALL_TOOLS
        ]


def call_sites() -> dict[str, list[Site]]:
    """Every `prompts.*("name")` in portia, keyed by the prompt it names.

    This is the honest answer to *when does the model see this file*: not a note
    someone wrote, but the lines that load it. A prompt with no call site is
    dead text; a `prompts.task` in `cli/index.py` says the file opens an
    indexing job, and says it in a way that cannot go stale.
    """
    found: dict[str, list[Site]] = {}
    for path in sorted(PACKAGE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            named = _names_prompt(node)
            if named is None:
                continue
            call = f"prompts.{_callee(node)}({named.split('/')[-1]!r})"
            found.setdefault(named, []).append(Site(_relative(path), node.lineno, call))
    return found


def prompt_inventory(sites: dict[str, list[Site]]) -> list[PromptFile]:
    """Every markdown file the model can be given, with what fills it."""
    from portia.agent import handlers

    vocabulary = handlers.step_vocabulary()
    out = []
    for path in sorted(prompt_files.HERE.rglob("*.md")):
        if path.name == "README.md":
            continue
        name = path.relative_to(prompt_files.HERE).with_suffix("").as_posix()
        kind = name.split("/")[0] if "/" in name else "system"
        raw = path.read_text(encoding="utf-8")
        out.append(
            PromptFile(
                name=name,
                kind=kind,
                path=path,
                raw=raw,
                rendered=_rendered(name, kind, vocabulary),
                placeholders=tuple(dict.fromkeys(_PLACEHOLDER.findall(raw))),
                sites=tuple(sites.get(name, ())),
            )
        )
    return out


def _rendered(name: str, kind: str, vocabulary: dict[str, str]) -> str | None:
    """The file as the model reads it, where that can be known without guessing.

    A tool description is renderable: its placeholders are filled from the ops,
    which is the whole reason it is a template. A task's are filled from what
    the operator typed, and a refusal's from the facts of a failure that has not
    happened — so those are shown as templates, marked as templates. Inventing
    plausible values for them would put text on this page that no model ever
    read, in the tool built to show exactly that.
    """
    if kind != "tools":
        return None
    try:
        return prompt_files.tool(name.split("/", 1)[1], **vocabulary)
    except (KeyError, IndexError, FileNotFoundError):
        return None


def observations(transcripts: list[runlog.Transcript]) -> dict[str, list[Observation]]:
    """What each tool actually returned, out of the logs, newest log first.

    Paired through `page.nodes`, so a call and its result are matched by the
    same rule the audit page uses — the alternative is two answers to "what did
    this call return", which is the disagreement `core/present.py` exists to
    stop, one layer out.
    """
    out: dict[str, list[Observation]] = {}
    for transcript in transcripts:
        for node in page.nodes(transcript):
            if not isinstance(node, page.Call):
                continue
            result = node.result.data if node.result else {}
            out.setdefault(node.name, []).append(
                Observation(
                    log=transcript.name,
                    at=node.at,
                    seconds=node.seconds,
                    input={
                        k: v
                        for k, v in (node.call.data.get("input") or {}).items()
                        if k != "portia_dir"
                    },
                    result=str(result.get("text") or ""),
                    is_error=bool(result.get("is_error")),
                    allowed=node.allowed,
                )
            )
    return out


def engine_messages() -> list[Message]:
    """Strings the engine raises or denies with — prompt text with no author.

    Scanned rather than listed, because the point is the ones nobody remembers
    writing. `tools._failed` turns any exception out of a handler into a tool
    result, so every `raise ValueError(...)` on a reachable path is a sentence
    the model reads at the moment it is choosing what to do next — the same
    moment `prompts/errors/` exists to serve, and the reason the README calls
    that wording as load-bearing as a tool description.

    f-strings are kept with their expressions in braces. A message is worth
    reading as a shape (`no indexed source {source!r} — have: {known}`), and
    resolving those would need a failure to have happened.
    """
    out: list[Message] = []
    for where in MESSAGE_SOURCES:
        target = PACKAGE / where
        files = [target] if target.is_file() else sorted(target.rglob("*.py"))
        for path in files:
            out.extend(_messages_in(path))
    return out


def _messages_in(path: Path) -> list[Message]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call) and node.exc.args:
            arg = node.exc.args[0]
            named = _names_prompt(arg)
            text = named or _literal(arg)
            if text:
                found.append(Message(_relative(path), node.lineno, "raise", text, prompt=named))
        elif isinstance(node, ast.Call) and _callee(node) == "PermissionResultDeny":
            for keyword in node.keywords:
                text = _literal(keyword.value)
                if keyword.arg == "message" and text:
                    found.append(Message(_relative(path), node.lineno, "deny", text))
    return sorted(found, key=lambda m: m.line)


def _names_prompt(node: ast.AST) -> str | None:
    """``prompts.error("blocked_step", …)`` → ``errors/blocked_step``.

    The receiver has to be ``prompts``: `load` and `error` are ordinary enough
    words that matching on the method alone would collect other modules' calls
    and report them as prompt loads.
    """
    if not isinstance(node, ast.Call) or not node.args:
        return None
    func = node.func
    if not isinstance(func, ast.Attribute) or func.attr not in _LOADERS:
        return None
    if not isinstance(func.value, ast.Name) or func.value.id != "prompts":
        return None
    first = node.args[0]
    if not isinstance(first, ast.Constant) or not isinstance(first.value, str):
        return None
    return first.value if func.attr == "load" else f"{func.attr}s/{first.value}"


def _callee(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    return func.id if isinstance(func, ast.Name) else ""


def _literal(node: ast.AST) -> str | None:
    """A string literal or f-string as text; anything else is not a message."""
    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, str) else None
    if isinstance(node, ast.JoinedStr):
        parts = []
        for value in node.values:
            if isinstance(value, ast.Constant):
                parts.append(str(value.value))
            elif isinstance(value, ast.FormattedValue):
                parts.append("{" + ast.unparse(value.value) + "}")
        return "".join(parts)
    return None


def _orphans(tools: list[Delivered], files: list[PromptFile]) -> list[str]:
    """Text nothing reads, and tools with no text. Both fail silently.

    `tests/test_agent_prompts.py` already fails on a tool without a file, which
    is the half that matters most. This catches the other direction too, and it
    is here rather than as a test because a dev page that only shows what is
    wired up would be reassuring in exactly the case you opened it for.
    """
    out = [f"{t.name} — tool with no description file" for t in tools if t.prompt_path is None]
    known = {t.name for t in tools}
    for prompt in files:
        if prompt.kind == "tools" and prompt.name.split("/", 1)[1] not in known:
            out.append(f"{prompt.name}.md — a description for no tool")
        elif prompt.kind in ("tasks", "errors") and not prompt.sites:
            out.append(f"{prompt.name}.md — nothing loads it")
    return out


def _relative(path: Path) -> str:
    try:
        return str(path.relative_to(PACKAGE.parent))
    except ValueError:
        return str(path)


# --- the command --------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    from devtools.contextpage import render_context_page

    parser = argparse.ArgumentParser(
        description="Render everything the copilot is given into one HTML page."
    )
    parser.add_argument(
        "project",
        nargs="?",
        default=".",
        help="the project whose brief and logs to read (default: .)",
    )
    parser.add_argument(
        "--no-logs",
        action="store_true",
        help="skip the recorded tool outputs — prompts and schemas only",
    )
    parser.add_argument("-o", "--out", default=None, help="where to write the page")
    parser.add_argument(
        "--no-open", action="store_true", help="write the file without opening a browser"
    )
    args = parser.parse_args(argv)

    project = Path(args.project).expanduser()
    if not project.is_dir():
        print(f"No such project: {project}", file=sys.stderr)
        return 2

    injected = gather(project, with_logs=not args.no_logs)
    if not injected.system_prompt:
        # The one failure that makes the page pointless rather than partial.
        print(
            "Could not compose the system prompt — is the `agent` extra installed?",
            file=sys.stderr,
        )
        return 1

    out = Path(args.out).expanduser() if args.out else _default_out(project)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_context_page(injected), encoding="utf-8")

    calls = sum(len(v) for v in injected.observed.values())
    print(
        f"{out}  ({len(injected.tools)} tools, {len(injected.prompts)} prompts, "
        f"{injected.always_on_chars:,} chars always on, {calls} recorded calls)"
    )
    for orphan in injected.orphans:
        print(f"  ! {orphan}", file=sys.stderr)
    if not args.no_open:
        webbrowser.open(out.resolve().as_uri())
    return 0


def _default_out(project: Path) -> Path:
    return OUT_DIR / f"context-{project.resolve().name}-{datetime.now().strftime(STAMP)}.html"


if __name__ == "__main__":
    raise SystemExit(main())

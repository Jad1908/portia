"""Every log the viewer can show, read into one shape.

Two formats come in and one goes out. portia's run log is read through
`portia.runlog`, the one parser for it. Claude Code's session files are read
here, because nothing in portia reads them: they are what the benchmark's
baseline leaves behind (`docs/BENCHMARK_EVAL.md` §5.5), and what Claude Code
writes when portia runs inside it.

A run comes out as a **summary** (what the list needs) and, on request, its
**steps**: what you said, what the copilot said, each tool call with its result
folded in, each question with its answer, the end of each reply. A step is
drawn by the page as is; nothing here decides what matters in it.

**A portia chat is also a Claude Code session.** The SDK drives the Claude
Code binary, which writes its own session file for every chat portia runs.
Those copies are dropped: by session id, which portia's log records on every
reply, and for logs older than that, by what they are — a file the Python SDK
wrote whose every tool call went to portia (`is_portia_copy`).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from portia import runlog
from portia.agent import events
from portia.agent.providers import anthropic
from portia.catalog import DEFAULT_DIR
from portia.core import columnar
from portia.core.present import duration

#: The repository this checkout is. A log's key is its path from here when it
#: lives here, so a note keeps pointing at the same log on another machine.
REPO = Path(__file__).resolve().parents[2]

#: Where the viewer looks when it is given nowhere.
DEFAULT_ROOTS = (REPO / "sandbox",)

PORTIA = "portia"
CLAUDE_CODE = "Claude Code"
CLAUDE_FORMAT = "claude-code"

#: Folders a walk for `.portia/` never enters. Dot-folders are skipped too,
#: except `.portia` itself, which is found rather than entered.
SKIP_DIRS = frozenset({"node_modules", "__pycache__"})

#: How much of a tool's input the one-line summary shows.
ARG_CHARS = 140

#: The widest table a result is drawn as. A wider list stays in the JSON.
TABLE_COLUMNS = 14

#: Input keys that name what a call is about, in the order they are tried.
ARG_KEYS = (
    "source",
    "question",
    "step_id",
    "id",
    "spec_path",
    "spec",
    "command",
    "file_path",
    "path",
    "pattern",
    "query",
    "skill",
    "description",
)

#: What `system` prompt text is split on to leave the part every project shares
#: (`session.build_system_prompt`: copilot.md, then this separator, then the brief).
BRIEF_SEPARATOR = "\n\n---\n\n"

#: A Claude model id with a release date on the end, as Claude Code logs it.
_DATED = re.compile(r"-\d{8}$")


@dataclass
class Entry:
    """One log on disk, and what was last read from it."""

    id: str
    key: str
    path: Path
    format: str
    mtime: float = 0.0
    summary: dict[str, Any] | None = None
    steps: list[dict[str, Any]] | None = None
    sessions: frozenset[str] = frozenset()
    #: The session this file is, for a Claude Code file.
    session: str | None = None


class Library:
    """The logs under some roots, re-read only when a file changes."""

    def __init__(self, roots: list[Path] | None = None, claude_home: Path | None = None):
        self.roots = [Path(r).expanduser().resolve() for r in (roots or DEFAULT_ROOTS)]
        self.claude_home = claude_home if claude_home is not None else default_claude_home()
        self.entries: dict[str, Entry] = {}

    # --- what the page asks for --------------------------------------------

    def runs(self) -> list[dict[str, Any]]:
        """Every run's summary, newest first, with Claude Code's copies of portia chats dropped."""
        self.scan()
        portia_sessions: set[str] = set()
        for entry in self.entries.values():
            if entry.format == PORTIA:
                portia_sessions |= entry.sessions
        out = []
        for entry in self.entries.values():
            if entry.summary is None:
                continue
            if entry.format == CLAUDE_FORMAT and entry.session in portia_sessions:
                continue
            out.append(entry.summary)
        out.sort(key=lambda s: s.get("date") or "", reverse=True)
        return out

    def steps(self, run_id: str) -> list[dict[str, Any]] | None:
        entry = self.entries.get(run_id)
        if entry is None:
            self.scan()
            entry = self.entries.get(run_id)
        if entry is None:
            return None
        self._refresh(entry)
        return entry.steps

    def key(self, run_id: str) -> str | None:
        entry = self.entries.get(run_id)
        return entry.key if entry else None

    # --- finding and reading -----------------------------------------------

    def scan(self) -> None:
        seen: dict[str, Entry] = {}
        for path, fmt in self._paths():
            key = log_key(path, fmt)
            run_id = short_id(key)
            entry = self.entries.get(run_id) or Entry(run_id, key, path, fmt)
            try:
                self._refresh(entry)
            except (OSError, ValueError):
                continue
            seen[run_id] = entry
        self.entries = seen

    def _paths(self) -> list[tuple[Path, str]]:
        found: list[tuple[Path, str]] = []
        for root in self.roots:
            if root.is_file() and root.suffix == ".jsonl":
                found.append((root, CLAUDE_FORMAT if _is_claude_file(root) else PORTIA))
                continue
            for portia_dir in portia_dirs(root):
                found += [(p, PORTIA) for p in runlog.logs_in(portia_dir)]
            found += [(p, CLAUDE_FORMAT) for p in claude_files(self.claude_home, root)]
        return found

    def _refresh(self, entry: Entry) -> None:
        mtime = entry.path.stat().st_mtime
        if entry.summary is not None and entry.mtime == mtime:
            return
        if entry.format == PORTIA:
            summary, steps, sessions = read_portia(entry.path)
        else:
            summary, steps, sessions = read_claude(entry.path)
            entry.session = next(iter(sessions), None)
        if summary is None:
            entry.summary, entry.steps = None, None
            entry.mtime = mtime
            return
        summary.update(
            id=entry.id,
            key=entry.key,
            project=self._project(entry, summary),
            path=display_path(entry.path),
        )
        entry.summary, entry.steps, entry.sessions, entry.mtime = summary, steps, sessions, mtime

    def _project(self, entry: Entry, summary: dict[str, Any]) -> str:
        """The folder a run was in, from the root that found it."""
        folder = Path(summary.pop("cwd", "") or "")
        if entry.format == PORTIA:
            folder = entry.path.parent.parent.parent
        for root in self.roots:
            try:
                rel = folder.resolve().relative_to(root)
            except (ValueError, OSError):
                continue
            return rel.as_posix() if rel.parts else root.name
        return folder.name or "?"


def default_claude_home() -> Path:
    """Where Claude Code keeps its session files: `CLAUDE_CONFIG_DIR`, else `~/.claude`."""
    base = os.environ.get("CLAUDE_CONFIG_DIR")
    return (Path(base).expanduser() if base else Path.home() / ".claude") / "projects"


def portia_dirs(root: Path) -> list[Path]:
    """Every `.portia/` under ``root``, without walking into data folders' dot-dirs."""
    if not root.is_dir():
        return []
    found = []
    for folder, dirs, _files in os.walk(root):
        if DEFAULT_DIR in dirs:
            found.append(Path(folder) / DEFAULT_DIR)
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d not in SKIP_DIRS)
    return found


def slug(path: Path) -> str:
    """Claude Code's folder name for a working directory: every non-alphanumeric is ``-``."""
    return re.sub(r"[^A-Za-z0-9]", "-", str(path))


def claude_files(home: Path, root: Path) -> list[Path]:
    """Claude Code session files whose working directory is ``root`` or under it."""
    if not home.is_dir():
        return []
    prefix = slug(root)
    found = []
    for folder in home.iterdir():
        if folder.name != prefix and not folder.name.startswith(prefix + "-"):
            continue
        for path in folder.glob("*.jsonl"):
            cwd = _claude_cwd(path)
            if cwd is not None and _under(Path(cwd), root):
                found.append(path)
    return found


def log_key(path: Path, fmt: str) -> str:
    """A name for a log that survives a move to another machine."""
    if fmt == CLAUDE_FORMAT:
        return f"claude-code/{path.stem}"
    return display_path(path)


def display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO).as_posix()
    except ValueError:
        try:
            return "~/" + path.resolve().relative_to(Path.home()).as_posix()
        except ValueError:
            return path.as_posix()


def short_id(key: str) -> str:
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


def model_label(model: str | None) -> str:
    """``claude-haiku-4-5-20251001`` → ``Haiku 4.5``; anything else as it is."""
    if not model:
        return "?"
    bare = _DATED.sub("", model)
    for known in anthropic.CATALOG:
        if known.name == bare:
            return known.label.removeprefix("Claude ")
    return model


# --- portia's log ------------------------------------------------------------


def read_portia(path: Path) -> tuple[dict[str, Any] | None, list[dict[str, Any]], frozenset[str]]:
    transcript = runlog.read(path)
    if not transcript.header and not transcript.events:
        return None, [], frozenset()
    summary = runlog.summary(transcript)
    listing = runlog.read_listing(path, path.parent.parent)
    host = transcript.header.get(runlog.HOSTED)
    provider = summary.get("provider")
    sessions = frozenset(
        str(e.data["session_id"])
        for e in transcript.events
        if e.kind == events.RESULT and e.data.get("session_id")
    )
    wall = summary.get("wall_seconds")
    out = {
        "format": PORTIA,
        "kind": transcript.kind,
        "harness": runlog.host_label(host) if host else PORTIA,
        "provider": provider if provider and provider != "anthropic" else None,
        "title": listing.get("title") or path.stem,
        "model": model_label(summary.get("model")),
        "effort": summary.get("effort"),
        "prompts": prompts_version(transcript.prompts),
        "sha": summary.get("portia_sha"),
        "date": _minute(summary.get("started") or transcript.header.get("started")),
        "took": duration(wall) if wall else None,
        "cost": summary.get("cost_usd"),
        "calls": summary.get("tools") or 0,
        "errors": summary.get("tool_errors") or 0,
        "cwd": transcript.header.get("cwd"),
    }
    return out, portia_steps(transcript), sessions


def prompts_version(prompts: dict[str, Any]) -> str | None:
    """A short fingerprint of what every chat of this portia was given.

    The system prompt minus the project's own brief, plus the tool
    descriptions: two runs with the same fingerprint read the same
    instructions, whatever project they ran on.
    """
    if not prompts:
        return None
    shared = str(prompts.get("system") or "").split(BRIEF_SEPARATOR, 1)[0]
    tools = json.dumps(prompts.get("tools"), sort_keys=True, default=str)
    return hashlib.sha1((shared + tools).encode("utf-8")).hexdigest()[:7]


def portia_steps(transcript: runlog.Transcript) -> list[dict[str, Any]]:
    timed = transcript.timed()
    results = {
        str(e.data.get("id")): (e, at)
        for e, at in timed
        if e.kind == events.TOOL_RESULT and e.data.get("id")
    }
    steps: list[dict[str, Any]] = []
    tools: list[dict[str, Any]] = []
    asked: dict[str, Any] | None = None
    reply_start: str | None = None

    for event, at in timed:
        data = event.data
        if event.kind == events.PROMPT:
            reply_start = at
            steps.append({"t": "you", "text": str(data.get("text") or "")})
        elif event.kind == events.TEXT:
            steps.append({"t": "say", "text": str(data.get("text") or "")})
        elif event.kind == events.THINKING:
            if str(data.get("text") or "").strip():
                steps.append({"t": "think", "text": str(data["text"])})
        elif event.kind == events.TOOL_CALL:
            found, found_at = results.pop(str(data.get("id")), (None, None))
            step = tool_step(
                tool_name(str(data.get("name") or "")),
                data.get("input") or {},
                result=found.data.get("text") if found else None,
                error=bool(found and found.data.get("is_error")),
                seconds=runlog.elapsed(at, found_at),
            )
            step["_name"] = str(data.get("name") or "")
            tools.append(step)
            steps.append(step)
        elif event.kind == events.APPROVAL:
            target = _awaiting(tools, str(data.get("name") or ""), gated=False)
            if target is not None:
                target["_gated"] = True
        elif event.kind == events.APPROVAL_RESULT:
            target = _awaiting(tools, str(data.get("name") or ""), gated=True)
            if target is not None:
                target["write"] = (
                    "auto" if data.get("auto") else "approved" if data.get("allowed") else "refused"
                )
                if data.get("waited") is not None:
                    target["waited"] = duration(float(data["waited"]))
        elif event.kind == events.QUESTION:
            asked = {"t": "ask", "questions": _questions(data.get("questions") or [])}
            steps.append(asked)
        elif event.kind == events.ANSWER:
            if asked is not None:
                answers = data.get("answers") or {}
                for q in asked["questions"]:
                    q["answer"] = _text(answers.get(q["q"]))
                if data.get("waited") is not None:
                    asked["waited"] = duration(float(data["waited"]))
                asked = None
        elif event.kind == events.RESULT:
            steps.append(
                {
                    "t": "end",
                    "secs": _took(reply_start, at),
                    "cost": data.get("cost_usd"),
                    "how": data.get("subtype") if data.get("subtype") != "success" else None,
                }
            )
        elif event.kind == events.ERROR:
            steps.append({"t": "error", "text": _text(data.get("message") or data)})
    for step in tools:
        step.pop("_name", None)
        step.pop("_gated", None)
    return steps


def _awaiting(tools: list[dict[str, Any]], name: str, *, gated: bool) -> dict[str, Any] | None:
    """The latest call of this name still waiting for its gate (`can_use_tool` fires between call and result)."""
    for step in reversed(tools):
        if step.get("_name") != name:
            continue
        if gated and step.get("_gated") and "write" not in step:
            return step
        if not gated and not step.get("_gated"):
            return step
    return None


def _questions(raw: list[Any]) -> list[dict[str, Any]]:
    out = []
    for q in raw:
        if not isinstance(q, dict):
            continue
        options = [
            _text(o.get("label") if isinstance(o, dict) else o) for o in q.get("options") or []
        ]
        out.append({"q": _text(q.get("question")), "options": options, "answer": None})
    return out


# --- Claude Code's session file -------------------------------------------------


def read_claude(path: Path) -> tuple[dict[str, Any] | None, list[dict[str, Any]], frozenset[str]]:
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict) and not record.get("isSidechain"):
            records.append(record)

    title = next((r.get("aiTitle") for r in records if r.get("type") == "ai-title"), None)
    stamps = [r["timestamp"] for r in records if isinstance(r.get("timestamp"), str)]
    models = [
        r["message"].get("model")
        for r in records
        if r.get("type") == "assistant" and isinstance(r.get("message"), dict)
    ]
    steps = claude_steps(records)
    prompts = [s for s in steps if s["t"] == "you"]
    if not prompts or is_portia_copy(records):
        return None, [], frozenset()
    session = next((r.get("sessionId") for r in records if r.get("sessionId")), path.stem)
    tools = [s for s in steps if s["t"] == "tool"]
    first, last = (_local(stamps[0]), _local(stamps[-1])) if stamps else (None, None)
    out = {
        "format": CLAUDE_FORMAT,
        "kind": runlog.CHAT,
        "harness": CLAUDE_CODE,
        "provider": None,
        "title": title or runlog.first_line(prompts[0]["text"]) or path.stem,
        "model": model_label(next((m for m in models if m and m != "<synthetic>"), None)),
        "effort": None,
        "prompts": None,
        "sha": None,
        "date": _minute(first.isoformat()) if first else None,
        "took": duration((last - first).total_seconds()) if first and last else None,
        "cost": None,
        "calls": len(tools),
        "errors": sum(1 for s in tools if s.get("error")),
        "cwd": next((r.get("cwd") for r in records if r.get("cwd")), None),
    }
    return out, steps, frozenset({str(session)})


#: How the Python Agent SDK signs the records of a session it drives.
SDK_PY = "sdk-py"


def is_portia_copy(records: list[dict[str, Any]]) -> bool:
    """Whether a Claude Code file is the binary's own copy of a chat portia ran.

    The Python SDK wrote it and every tool it called was portia's. A baseline
    run is driven by the same SDK but calls Claude Code's own tools, so it is
    kept; one that called no tool at all would be taken for a copy.
    """
    if not any(r.get("entrypoint") == SDK_PY for r in records):
        return False
    names = [
        str(block.get("name") or "")
        for r in records
        if r.get("type") == "assistant" and isinstance(r.get("message"), dict)
        for block in r["message"].get("content") or []
        if isinstance(block, dict) and block.get("type") == "tool_use"
    ]
    return all(n.startswith(events.TOOL_PREFIX) or n == "AskUserQuestion" for n in names)


def claude_steps(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results: dict[str, tuple[dict[str, Any], str | None, Any]] = {}
    for record in records:
        message = record.get("message")
        if record.get("type") != "user" or not isinstance(message, dict):
            continue
        for block in message.get("content") if isinstance(message.get("content"), list) else []:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                results[str(block.get("tool_use_id"))] = (
                    block,
                    record.get("timestamp"),
                    record.get("toolUseResult"),
                )

    steps: list[dict[str, Any]] = []
    for record in records:
        kind, message, at = record.get("type"), record.get("message"), record.get("timestamp")
        if kind == "system" and record.get("hookErrors"):
            steps.append({"t": "hook", "text": "\n\n".join(map(str, record["hookErrors"]))})
            continue
        if not isinstance(message, dict) or record.get("isMeta"):
            continue
        content = message.get("content")
        if kind == "user":
            if isinstance(content, str):
                if not content.lstrip().startswith("<"):
                    steps.append({"t": "you", "text": content})
                continue
            texts = [
                b.get("text", "")
                for b in content or []
                if isinstance(b, dict) and b.get("type") == "text"
            ]
            if texts and not any(
                isinstance(b, dict) and b.get("type") == "tool_result" for b in content
            ):
                steps.append({"t": "context", "text": "\n\n".join(texts)})
            continue
        if kind != "assistant":
            continue
        for block in content or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text" and str(block.get("text") or "").strip():
                steps.append({"t": "say", "text": block["text"]})
            elif block.get("type") == "thinking" and str(block.get("thinking") or "").strip():
                steps.append({"t": "think", "text": block["thinking"]})
            elif block.get("type") == "tool_use":
                found, found_at, extra = results.get(str(block.get("id")), (None, None, None))
                name = tool_name(str(block.get("name") or ""))
                if name == "AskUserQuestion":
                    steps.append(_claude_question(block.get("input") or {}, extra))
                    continue
                steps.append(
                    tool_step(
                        name,
                        block.get("input") or {},
                        result=_claude_result(found) if found else None,
                        error=bool(found and found.get("is_error")),
                        seconds=runlog.elapsed(at, found_at),
                    )
                )
    return steps


def _claude_result(block: dict[str, Any]) -> str:
    content = block.get("content")
    if isinstance(content, list):
        return "\n\n".join(
            str(b.get("text") or "")
            for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return _text(content)


def _claude_question(tool_input: dict[str, Any], extra: Any) -> dict[str, Any]:
    questions = _questions(tool_input.get("questions") or [])
    answers = extra.get("answers") if isinstance(extra, dict) else None
    for q in questions:
        if isinstance(answers, dict):
            q["answer"] = _text(answers.get(q["q"])) or None
    return {"t": "ask", "questions": questions}


def _is_claude_file(path: Path) -> bool:
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                record = json.loads(line)
            except ValueError:
                continue
            return isinstance(record, dict) and "kind" not in record
    return False


def _claude_cwd(path: Path) -> str | None:
    """The working directory a session file records, read from the top only."""
    try:
        with path.open(encoding="utf-8") as handle:
            for number, line in enumerate(handle):
                if '"cwd"' in line:
                    try:
                        cwd = json.loads(line).get("cwd")
                    except ValueError:
                        continue
                    if cwd:
                        return str(cwd)
                if number > 50:
                    return None
    except OSError:
        return None
    return None


# --- one tool call, whatever wrote it ------------------------------------------------


def tool_step(
    name: str,
    tool_input: dict[str, Any],
    *,
    result: Any,
    error: bool,
    seconds: float | None,
) -> dict[str, Any]:
    """A call and what came back, with any list of records in the result pulled out as a table."""
    shown_input = {k: v for k, v in tool_input.items() if k != "portia_dir"}
    step: dict[str, Any] = {
        "t": "tool",
        "name": name,
        "arg": summarize_input(shown_input),
        "input": shown_input,
        "secs": round(seconds, 1) if seconds is not None else None,
        "error": error,
    }
    if result is None:
        step["result"] = None
        return step
    parsed = _json(result) if isinstance(result, str) and not error else None
    if isinstance(parsed, dict):
        tables, rest = split_tables(parsed)
        step["result"] = rest
        step["tables"] = tables
        return step
    decoded = decode_columnar(result) if isinstance(result, str) and not error else None
    if decoded is not None:
        head, blocks = decoded
        tables, rest = split_tables(head)
        step["result"] = rest
        step["tables"] = tables + blocks
        return step
    step["result"] = _text(result)
    return step


#: A block heading in `core.columnar.render`'s output: ``## 48 of 191 columns``.
_BLOCK = re.compile(r"\n## (\d+ of \d+ \S+)\n")
_UNESCAPE = re.compile(r"\\([\\tnr])")
_UNESCAPED = {"\\": "\\", "t": "\t", "n": "\n", "r": "\r"}


def decode_columnar(text: str) -> tuple[dict[str, Any], list[dict[str, Any]]] | None:
    """Read `core.columnar.render`'s output back: its JSON head, then one table per block.

    That is the shape `describe_source` and `profile_source` hand the model. It
    is not JSON as a whole, so without this a profile reads as one long string.
    """
    try:
        head, end = json.JSONDecoder().raw_decode(text)
    except ValueError:
        return None
    if not isinstance(head, dict):
        return None
    parts = _BLOCK.split(text[end:])
    if len(parts) < 3 or parts[0].strip():
        return None
    tables = []
    for heading, body in zip(parts[1::2], parts[2::2], strict=True):
        lines = body.rstrip("\n").split("\n")
        columns = lines[0].split(columnar.DELIMITER)
        rows = [[_columnar_cell(c) for c in line.split(columnar.DELIMITER)] for line in lines[1:]]
        tables.append({"key": heading, "columns": columns, "rows": rows})
    return head, tables


def _columnar_cell(cell: str) -> Any:
    if cell == columnar.NULL:
        return None
    text = _UNESCAPE.sub(lambda m: _UNESCAPED[m.group(1)], cell)
    for kind in (int, float):
        try:
            return kind(text)
        except ValueError:
            continue
    return text


def tool_name(qualified: str) -> str:
    """``mcp__plugin_portia_portia__describe_source`` → ``describe_source``: the server is noise here."""
    if qualified.startswith("mcp__"):
        return qualified.split("__", 2)[-1]
    return qualified


def summarize_input(tool_input: dict[str, Any]) -> str:
    """One line saying what a call was about: its most telling argument."""
    if isinstance(tool_input.get("step"), dict) and tool_input["step"].get("id"):
        return _clip(str(tool_input["step"]["id"]))
    if "left" in tool_input and "right" in tool_input:
        return _clip(f"{_brief(tool_input['left'])} ⋈ {_brief(tool_input['right'])}")
    for key in ARG_KEYS:
        value = tool_input.get(key)
        if isinstance(value, str) and value.strip():
            return _clip(value)
    for value in tool_input.values():
        if isinstance(value, str) and value.strip():
            return _clip(value)
    return _clip(", ".join(f"{k}={_brief(v)}" for k, v in tool_input.items()))


def split_tables(result: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Top-level lists of records become tables; everything else stays JSON."""
    tables, rest = [], {}
    for key, value in result.items():
        if _is_records(value):
            columns: list[str] = []
            for row in value:
                columns += [c for c in row if c not in columns]
            if len(columns) <= TABLE_COLUMNS:
                tables.append(
                    {
                        "key": key,
                        "columns": columns,
                        "rows": [[_cell(row.get(c)) for c in columns] for row in value],
                    }
                )
                rest[key] = f"[{len(value)} rows, shown as a table]"
                continue
        rest[key] = value
    return tables, rest


def _is_records(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(v, dict) for v in value)


def _cell(value: Any) -> Any:
    if value is None or isinstance(value, bool | int | float | str):
        return value
    return json.dumps(value, default=str)


def _brief(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("source") or value.get("table") or json.dumps(value, default=str))
    if isinstance(value, list):
        return ", ".join(_brief(v) for v in value)
    return str(value)


def _clip(text: str) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= ARG_CHARS else flat[:ARG_CHARS] + "…"


def _json(text: str) -> Any:
    try:
        return json.loads(text)
    except ValueError:
        return None


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, default=str)


def _took(start: str | None, end: str | None) -> str | None:
    seconds = runlog.elapsed(start, end)
    return duration(seconds) if seconds is not None else None


def _minute(stamp: str | None) -> str | None:
    return stamp[:16] if stamp else None


def _local(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone().replace(tzinfo=None)


def _under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root)
    except (ValueError, OSError):
        return False
    return True

"""Every instruction the model reads comes from `agent/prompts/`, and none is missing.

The refactor these guard: `record_step`'s description once omitted a sentence
saying that steps chain, and the copilot concluded portia couldn't express a
two-hop join and told the user to go use dbt. Prompt text is load-bearing, so a
missing or unused file should fail loudly rather than degrade into an empty
string the model still gets offered.
"""

import ast
import pathlib
import re

import pytest

from portia.agent import prompts, tools
from portia.checks.outcome import BLOCKING_FLAGS

#: Longer than any legitimate code string here, shorter than any real prompt.
#: The scan is clean at this threshold today; if a change trips it, the answer is
#: almost always "that text belongs in portia/agent/prompts/".
MAX_INLINE_STRING = 200

PACKAGE = pathlib.Path(__file__).resolve().parents[1] / "portia"


def test_every_tool_has_a_description_file():
    declared = {t.name for t in tools.ALL_TOOLS}
    assert declared == prompts.names("tools"), (
        "a tool without a prompt file would ship an empty description; "
        "a prompt file without a tool is dead text"
    )


def test_every_tool_description_is_non_trivial():
    for t in tools.ALL_TOOLS:
        assert len(t.description) > 80, f"{t.name} has a suspiciously thin description"


#: A line that opens a markdown structure rather than continuing a sentence.
STRUCTURE = re.compile(r"^\s*(#{1,6} |[-*+] |\d+\. |> |```)")


def _structural_lines(text: str) -> set[str]:
    return {line.strip() for line in text.splitlines() if STRUCTURE.match(line)}


def test_tool_descriptions_keep_the_structure_of_their_file():
    """The file is what the model reads, headings and bullets included.

    This assertion is the inverse of the one it replaces, which required
    ``"\\n" not in t.description`` because *"they're delivered as a plain schema
    string — source wrapping must not leak"*. Newlines are legal in a JSON string
    and survive the MCP tool listing unchanged, so that rule was enforcing an
    assumption; what it actually deleted was every heading and list start, which
    is what `prompts.tool` now explains. Pinned in this direction so nobody
    reintroduces the join for tidiness.
    """
    for t in tools.ALL_TOOLS:
        source = prompts.load(f"tools/{t.name}")
        missing = _structural_lines(source) - _structural_lines(t.description)
        assert not missing, f"{t.name} lost markdown structure on the way to the model: {missing}"


def test_at_least_one_tool_description_carries_multiple_lines():
    """Belt to the braces above, which passes trivially if every file is one line.

    ``record_step.md`` is 155 lines with seven headings; a description that
    reaches the model as a single line means the flattening came back.
    """
    assert any("\n" in t.description for t in tools.ALL_TOOLS)


#: `{expect_join}` — a placeholder, not a brace. Descriptions legitimately show
#: JSON, so matching bare `{` would flag every worked example.
UNFILLED = re.compile(r"\{[a-z_][a-z0-9_]*\}")


def test_no_tool_description_reaches_the_model_with_an_unfilled_placeholder():
    """A literal `{expect_join}` in the tool list is worse than saying nothing."""
    for t in tools.ALL_TOOLS:
        assert not UNFILLED.search(t.description), f"{t.name} has an unfilled placeholder"


def test_the_step_vocabulary_in_the_prompt_is_generated_from_the_ops():
    """The lists the model reads and the lists the validator enforces are one thing.

    They used to be two: the description said "base `expect` on what the check
    measured" and named nothing, and `_EXPECTABLE` revealed the real vocabulary
    only by rejecting a step. A run burned two round-trips guessing at it. Adding
    a provenance field must now update the instruction, not silently outdate it.
    """
    from portia.agent import handlers
    from portia.ops import join as join_op
    from portia.ops import normalize as normalize_op
    from portia.ops import sql as sql_op

    description = next(t for t in tools.ALL_TOOLS if t.name == "record_step").description

    for field in join_op.PROVENANCE_KEYS | normalize_op.PROVENANCE_KEYS | sql_op.PROVENANCE_KEYS:
        assert field in description, f"`expect` may name {field!r}, but nothing says so"
    for how in join_op.HOWS:
        assert how in description, f"'{how}' is a valid join, but nothing says so"
    for op in normalize_op.TRANSFORM_OPS:
        assert op in description, f"{op!r} is a valid transform, but nothing says so"
    for flag in BLOCKING_FLAGS:
        assert flag in description, f"{flag!r} refuses a write, but nothing says so"

    # and the generated block is what's actually rendered, not a lookalike
    assert handlers.step_vocabulary()["expect_join"] in description


def test_a_missing_prompt_is_a_loud_error():
    with pytest.raises(FileNotFoundError, match="no prompt"):
        prompts.load("tools/does_not_exist")


def test_editor_notes_never_reach_the_model():
    """Files document their own placeholders in an HTML comment. It's for humans."""
    for kind in ("tools", "tasks"):
        for name in prompts.names(kind):
            assert "<!--" not in prompts.load(f"{kind}/{name}"), f"{kind}/{name}"


@pytest.mark.parametrize(
    ("name", "fields"),
    [
        ("interpret", {"source": "customers"}),
        ("merge", {"left": "a", "right": "b", "spec": "specs/a.yaml"}),
        ("index_batch", {"names": "'a', 'b'"}),
        ("reinterpret", {"source": "customers", "note": "the id is a legacy code"}),
    ],
)
def test_task_templates_fill_their_placeholders(name, fields):
    filled = prompts.task(name, **fields)
    assert "{" not in filled, "an unfilled placeholder would reach the model literally"
    for value in fields.values():
        assert value.strip("'") in filled


def test_task_templates_are_all_covered_by_the_test_above():
    """A new task prompt must be added to the parametrize list, not silently skipped."""
    covered = {"interpret", "merge", "index_batch", "reinterpret"}
    unformatted = {"ask_for_context"}  # plain prose, no placeholders
    assert prompts.names("tasks") == covered | unformatted


# --- the boundary: no instruction text inside code ---------------------------


#: Markers that identify a literal as query text rather than prose. Two or more
#: must appear, in upper case, which is how SQL is written in this codebase and
#: is not how English is.
_SQL_MARKERS = ("SELECT ", "FROM ", "WHERE ", "JOIN ", "GROUP BY", "ORDER BY", "COPY ", "CREATE ")


def _is_query_text(literal: str) -> bool:
    """A query is not a prompt.

    The rule below exists because text the **model** reads has to be reviewable
    as prose in a file — `record_step`'s description lost a sentence while buried
    in a decorator argument and the copilot started telling users to go use dbt.
    A parameterised `SELECT` in `checks/` or `ops/` is neither read by the model
    nor improved by being torn away from the code that builds it; the join check's
    overlap query *is* the check, and splitting it to satisfy a character count
    would make the one thing worth reading harder to read.

    Deliberately narrow, and it matches fragments as well as whole statements —
    an f-string's literal chunks are separate AST nodes, so the piece between two
    placeholders has to be recognisable on its own.
    """
    return sum(marker in literal for marker in _SQL_MARKERS) >= 2


def _long_string_literals(path: pathlib.Path):
    """Every non-docstring string literal over the threshold, with its line."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = {
        d
        for node in ast.walk(tree)
        if isinstance(node, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
        for d in [ast.get_docstring(node, clean=False)]
        if d
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if (
                node.value not in docstrings
                and len(node.value) > MAX_INLINE_STRING
                and not _is_query_text(node.value)
            ):
                yield node.lineno, len(node.value), node.value
        elif isinstance(node, ast.JoinedStr):  # an f-string prompt would hide here
            literal = "".join(
                v.value
                for v in node.values
                if isinstance(v, ast.Constant) and isinstance(v.value, str)
            )
            if len(literal) > MAX_INLINE_STRING and not _is_query_text(literal):
                yield node.lineno, len(literal), literal


def test_no_instruction_text_is_written_inline_in_code():
    """Prompt text lives in `portia/agent/prompts/` — never in a Python string.

    Not style: `record_step`'s description once lost a sentence about steps
    chaining while buried in a decorator argument, and the copilot concluded
    portia couldn't express a two-hop join and told the user to go use dbt. Text
    the model acts on has to be diffable and reviewable as prose, which means it
    lives in a file. Docstrings are exempt — they are written for us, not the model.
    """
    offenders = [
        f"{path.relative_to(PACKAGE.parent)}:{line} ({size} chars) {text[:60]!r}…"
        for path in sorted(PACKAGE.rglob("*.py"))
        for line, size, text in _long_string_literals(path)
    ]
    assert not offenders, (
        "long string literals found in code — if this is text the model reads, "
        "move it to portia/agent/prompts/ and load it with prompts.load/tool/task:\n  "
        + "\n  ".join(offenders)
    )


def test_every_tool_description_comes_from_a_file():
    """Belt to the braces above: no @tool may inline its description."""
    source = (PACKAGE / "agent" / "tools.py").read_text(encoding="utf-8")
    decorators = [
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "tool"
    ]
    assert len(decorators) == len(tools.ALL_TOOLS)
    for call in decorators:
        description = call.args[1]
        assert isinstance(description, ast.Call), (
            f'tool at line {call.lineno} inlines its description; use prompts.tool("<name>")'
        )
        assert ast.unparse(description).startswith("prompts.tool(")


def test_the_query_exemption_cannot_swallow_prose():
    """The escape hatch in the rule above, kept narrow enough to be safe."""
    assert _is_query_text("SELECT a, count(*) FROM t GROUP BY a")
    assert _is_query_text("), count(*) FILTER (WHERE l.ln IS NOT NULL) FROM l JOIN r ON x")
    # Instruction text, including text that happens to talk about SQL.
    assert not _is_query_text(
        "Write a single SELECT over the tables you declare in inputs. Explain your reasoning "
        "to the user before recording the step, and never assert a number you did not measure."
    )
    assert not _is_query_text("You are portia, a data harmonization copilot. " * 5)


# --- every prompt file is read by something ------------------------------------------

#: The `prompts.*` calls that load a file, and the folder each one reads from.
_LOADERS = ("load", "tool", "task", "error")


def _loaded_prompt(node: ast.AST) -> str | None:
    """``prompts.error("blocked_step", …)`` → ``errors/blocked_step``.

    The receiver has to be ``prompts``: `load` and `error` are ordinary enough
    words that matching on the method alone would collect other modules' calls.
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


def _call_sites() -> dict[str, list[str]]:
    """Every `prompts.*("name")` in portia, by the file it loads.

    Read off the syntax tree rather than line by line: a line-wise scan once
    reported four live refusals and the whole `merge` task as dead text, because
    their loaders are wrapped over two lines.
    """
    found: dict[str, list[str]] = {}
    for path in sorted(PACKAGE.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            named = _loaded_prompt(node)
            if named is not None:
                found.setdefault(named, []).append(
                    f"{path.relative_to(PACKAGE.parent)}:{node.lineno}"
                )
    return found


def test_a_loader_wrapped_over_two_lines_is_still_found():
    sites = _call_sites()

    assert sites["errors/blocked_step"], "wrapped over two lines in handlers.py"
    assert sites["tasks/merge"], "wrapped over two lines in cli/chat.py"


def test_no_prompt_file_is_dead_text():
    """A task or refusal nothing loads still gets edited, and a tool file with no tool is never sent."""
    sites = _call_sites()
    names = {t.name for t in tools.ALL_TOOLS}
    orphans = []
    for path in sorted(prompts.HERE.rglob("*.md")):
        name = path.relative_to(prompts.HERE).with_suffix("").as_posix()
        kind = name.split("/")[0] if "/" in name else ""
        if kind == "tools" and name.split("/", 1)[1] not in names:
            orphans.append(f"{name}.md: a description for no tool")
        elif kind in ("tasks", "errors") and not sites.get(name):
            orphans.append(f"{name}.md: nothing loads it")
    assert not orphans, "\n".join(orphans)


# --- every tool has a place on the ladder --------------------------------------------

#: Where each tool sits in the disclosure ladder, in the words `copilot.md` uses.
#: **A label, not a mechanism**: nothing in the engine ranks tools. It is written
#: down here so that adding a tool means deciding where it goes, and the test
#: below fails until someone has. `graph_lookup` is deliberately not a rung
#: (`KNOWLEDGE_GRAPH.md` §9.1): the rungs are depth on one table, and the router
#: is what tells you which table.
LADDER = {
    "graph_lookup": "router",
    "get_context": "L1",
    "describe_source": "L2",
    "profile_source": "L3",
    "join_findings": "L4",
    "query_data": "L5",
    "plot_data": "beside L5",
    "view_chart": "beside plot_data",
    "measure_overlaps": "measure",
    "set_interpretation": "write",
    "set_group": "write",
    "record_step": "write",
    "review_queries": "curate",
    "record_finding": "write",
    "ask_user": "ask",
    "read_spec": "read",
    "run_spec": "verify",
}


def test_every_tool_has_a_place_on_the_ladder():
    names = {t.name for t in tools.ALL_TOOLS}
    missing = sorted(names - set(LADDER))
    assert not missing, f"place these in LADDER: {', '.join(missing)}"
    stale = sorted(set(LADDER) - names)
    assert not stale, f"LADDER names tools that no longer exist: {', '.join(stale)}"

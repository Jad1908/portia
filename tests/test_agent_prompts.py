"""Every instruction the model reads comes from `agent/prompts/`, and none is missing.

The refactor these guard: `record_step`'s description once omitted a sentence
saying that steps chain, and the copilot concluded portia couldn't express a
two-hop join and told the user to go use dbt. Prompt text is load-bearing, so a
missing or unused file should fail loudly rather than degrade into an empty
string the model still gets offered.
"""

import ast
import pathlib

import pytest

from portia.agent import prompts, tools

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


def test_tool_descriptions_are_a_single_block():
    """They're delivered as a plain schema string — source wrapping must not leak."""
    for t in tools.ALL_TOOLS:
        assert "\n" not in t.description, f"{t.name} kept its line breaks"


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
    ],
)
def test_task_templates_fill_their_placeholders(name, fields):
    filled = prompts.task(name, **fields)
    assert "{" not in filled, "an unfilled placeholder would reach the model literally"
    for value in fields.values():
        assert value.strip("'") in filled


def test_task_templates_are_all_covered_by_the_test_above():
    """A new task prompt must be added to the parametrize list, not silently skipped."""
    covered = {"interpret", "merge", "index_batch"}
    unformatted = {"ask_for_context"}  # plain prose, no placeholders
    assert prompts.names("tasks") == covered | unformatted


# --- the boundary: no instruction text inside code ---------------------------


def _long_string_literals(path: pathlib.Path):
    """Every non-docstring string literal over the threshold, with its line."""
    tree = ast.parse(path.read_text())
    docstrings = {
        d
        for node in ast.walk(tree)
        if isinstance(node, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
        for d in [ast.get_docstring(node, clean=False)]
        if d
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value not in docstrings and len(node.value) > MAX_INLINE_STRING:
                yield node.lineno, len(node.value), node.value
        elif isinstance(node, ast.JoinedStr):  # an f-string prompt would hide here
            literal = "".join(
                v.value
                for v in node.values
                if isinstance(v, ast.Constant) and isinstance(v.value, str)
            )
            if len(literal) > MAX_INLINE_STRING:
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
    source = (PACKAGE / "agent" / "tools.py").read_text()
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

"""Every instruction the model reads comes from `agent/prompts/`, and none is missing.

The refactor these guard: `record_step`'s description once omitted a sentence
saying that steps chain, and the copilot concluded portia couldn't express a
two-hop join and told the user to go use dbt. Prompt text is load-bearing, so a
missing or unused file should fail loudly rather than degrade into an empty
string the model still gets offered.
"""

import pytest

from portia.agent import prompts, tools


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

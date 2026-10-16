"""The three schema changes the 2026-10 prompt drives asked for (`docs/FINDINGS.md` §5,
`docs/BACKLOG_NEXT.md` §4 Tool surface leftovers)."""

import pytest

from portia.agent import handlers, prompts, tools


def test_a_findings_answer_is_the_last_field_the_model_is_shown():
    props = list(tools.record_finding.input_schema["properties"])
    assert props[-2:] == ["answer", "portia_dir"]
    assert "so" in tools.record_finding.input_schema["required"]


@pytest.mark.parametrize("tag", handlers.MARKUP_TAGS)
def test_markup_inside_a_findings_prose_is_refused_by_name(tag):
    with pytest.raises(ValueError) as exc:
        handlers._finding_prose({"question": "q", "answer": f"found it.{tag}", "so": "s"})
    assert tag in str(exc.value)
    assert "answer" in str(exc.value)
    assert prompts.error("finding_markup", field="answer", tag=tag).splitlines()[0] in str(
        exc.value
    )


def test_no_offered_tool_shows_the_catalog_argument():
    server = tools.build_server()
    for name in ("get_context", "record_step", "query_data"):
        assert (
            tools.CATALOG_ARG
            in next(t for t in tools.ALL_TOOLS if t.name == name).input_schema["properties"]
        )
    # and what the model is offered has it taken out
    schema = tools.without_catalog_argument(tools.get_context.input_schema)
    assert tools.CATALOG_ARG not in schema["properties"]
    assert server is not None


def test_the_step_schema_names_the_ops_and_their_fields():
    step = tools.record_step.input_schema["properties"]["step"]
    assert step["properties"]["op"]["enum"] == list(handlers.STEP_OPS)
    assert step["required"] == ["id", "op"]
    for field in (
        "left",
        "right",
        "keys",
        "input",
        "transforms",
        "inputs",
        "sql",
        "expect",
        "grain",
        "rationale",
    ):
        assert field in step["properties"]


def test_expect_never_names_a_field_the_op_only_echoes():
    for op, fields in handlers._EXPECTABLE.items():
        assert not fields & handlers._EXPECT_ECHOES, op
    assert "result_rows" in handlers._EXPECTABLE["sql"]


def test_query_inputs_description_names_kept_results_and_the_portia_tables():
    text = tools.query_data.input_schema["properties"]["inputs"]["description"]
    assert "portia_columns" in text and "kept" in text

"""The context map — that it shows what is actually sent, and invents nothing.

`devtools/` is not part of the product, so this covers only the parts that are
real logic rather than markup, and each one is a way the page could be
*confidently wrong*, which is worse than a page that does not exist:

* it could show a tool description with its placeholders unfilled, i.e. a
  sentence no model has ever read;
* it could show the schema as written in `tools.py` rather than the one the SDK
  expands and sends;
* it could report a live prompt as dead text, because its loader is wrapped over
  two lines (it did — see `test_a_wrapped_loader_is_still_a_call_site`);
* it could fill a "what this returns" panel with a plausible example instead of
  a recorded one.

The placement test is here for a different reason: `LADDER` is the one thing on
the page written down rather than read out of the code, so this is what stops it
going stale when a tool is added.
"""

from __future__ import annotations

from pathlib import Path

from devtools import context, contextpage
from portia import runlog
from portia.agent import events, tools


def _transcript(*evts: events.Event) -> runlog.Transcript:
    return runlog.Transcript(
        path=Path("2026-08-08T22-10-52.jsonl"),
        header={"started": "2026-08-08T22:10:52", "kind": runlog.CHAT},
        events=list(evts),
    )


def _injected(**overrides) -> context.Injected:
    """A minimal `Injected`, for the renderer tests. No project, no logs."""
    base = {
        "project": Path("sandbox/x"),
        "portia_dir": "sandbox/x/.portia",
        "system_prompt": "L0\n\n---\n\nL1",
        "l0": "L0",
        "l1": "L1",
        "composed_as_expected": True,
    }
    return context.Injected(**{**base, **overrides})


def _tool(name: str, **overrides) -> context.Delivered:
    base = {
        "name": name,
        "qualified": f"mcp__portia__{name}",
        "description": "what it does",
        "schema": {"type": "object", "properties": {}},
        "read_only": True,
        "auto_approved": True,
        "rung": "L2",
        "rung_note": "a rung",
        "prompt_path": Path(f"tools/{name}.md"),
        "raw_prompt": "what it does",
        "handler_doc": "",
    }
    return context.Delivered(**{**base, **overrides})


# --- what the model is actually sent ------------------------------------------


def test_every_tool_has_a_placement():
    """`LADDER` restates `copilot.md`; a tool added without one drops off the map."""
    missing = sorted({t.name for t in tools.ALL_TOOLS} - set(context.LADDER))
    assert not missing, f"add these to devtools.context.LADDER: {', '.join(missing)}"
    stale = sorted(set(context.LADDER) - {t.name for t in tools.ALL_TOOLS})
    assert not stale, f"LADDER names tools that no longer exist: {', '.join(stale)}"


def test_the_description_shown_is_the_filled_one():
    """`record_step`'s file is a template; the model never sees the braces."""
    delivered = {t.name: t for t in context.delivered_tools()}
    step = delivered["record_step"]

    assert "{expect_sql}" in step.raw_prompt, "the file should still be a template"
    assert "{expect_sql}" not in step.description
    assert "result_rows" in step.description, "filled from the ops' own PROVENANCE_KEYS"


def test_the_schema_shown_is_the_one_the_sdk_expands():
    """`tools.py` writes `{"source": str}`; the model is sent JSON Schema."""
    delivered = {t.name: t for t in context.delivered_tools()}
    schema = delivered["describe_source"].schema

    assert schema.get("type") == "object"
    assert "source" in (schema.get("properties") or {})
    assert schema["properties"]["source"]["type"] == "string"


def test_the_two_halves_add_up_to_the_composed_prompt(tmp_path):
    injected = context.gather(tmp_path, with_logs=False)

    assert injected.composed_as_expected
    assert injected.l0 and injected.l1
    assert injected.system_prompt.endswith(injected.l1.rstrip())


def test_an_undescribed_project_still_renders(tmp_path):
    """The first thing anyone points this at is a project with nothing in it."""
    page = contextpage.render_context_page(context.gather(tmp_path, with_logs=False))

    assert "has not described this project yet" in page
    assert "No data sources have been indexed yet" in page


# --- where the text is used ----------------------------------------------------


def test_a_wrapped_loader_is_still_a_call_site():
    """`raise ValueError(prompts.error(` puts the name on the next line.

    A line-wise regex reported four live refusals and the whole `merge` task as
    text nothing loads — the exact failure this page exists to surface, produced
    by the page itself.
    """
    sites = context.call_sites()

    assert sites["errors/blocked_step"], "wrapped over two lines in handlers.py"
    assert sites["tasks/merge"], "wrapped over two lines in cli/chat.py"
    assert all(s.path.startswith("portia/") for s in sites["errors/blocked_step"])


def test_nothing_in_the_repo_is_orphaned():
    """A tool with no description is still offered; a dead prompt still gets edited."""
    injected = context.gather(context.PACKAGE.parent, with_logs=False)

    assert injected.orphans == []


def test_a_prompt_nothing_loads_is_reported(tmp_path):
    injected = _injected(
        tools=[_tool("describe_source")],
        prompts=[
            context.PromptFile(
                name="tasks/ghost", kind="tasks", path=tmp_path / "ghost.md", raw="text"
            )
        ],
    )
    assert context._orphans(injected.tools, injected.prompts) == [
        "tasks/ghost.md — nothing loads it"
    ]


# --- text that is not under prompts/ --------------------------------------------


def test_the_refusal_in_ask_py_is_found():
    """Under the 200-char test's threshold, and still read by the model."""
    found = [m for m in context.engine_messages() if m.kind == "deny"]

    assert found, "the PermissionResultDeny message is prompt text with no author"
    assert "declined this write" in found[0].text


def test_a_raise_that_loads_a_prompt_points_at_the_file_rather_than_repeating_it():
    named = [m for m in context.engine_messages() if m.prompt == "errors/blocked_step"]

    assert named, "handlers.py raises the blocked-step refusal"
    assert named[0].text == "errors/blocked_step"


def test_an_f_string_message_keeps_its_shape():
    """A message is worth reading as a shape; resolving it needs a real failure."""
    texts = [m.text for m in context.engine_messages() if m.prompt is None]

    assert any("{" in t and "no indexed source" in t for t in texts)


# --- what a tool returned --------------------------------------------------------


def test_recorded_outputs_are_paired_with_the_call_that_asked():
    run = _transcript(
        events.Event(
            events.TOOL_CALL,
            {"name": "mcp__portia__describe_source", "id": "a", "input": {"source": "orders"}},
        ),
        events.Event(events.TOOL_RESULT, {"id": "a", "text": "orders facts"}),
    )
    seen = context.observations([run])

    assert list(seen) == ["describe_source"]
    assert seen["describe_source"][0].input == {"source": "orders"}
    assert seen["describe_source"][0].result == "orders facts"


def test_portia_dir_is_dropped_from_a_recorded_input():
    """It is on every call and is the same value every time."""
    run = _transcript(
        events.Event(
            events.TOOL_CALL,
            {
                "name": "mcp__portia__profile_source",
                "id": "a",
                "input": {"source": "orders", "portia_dir": ".portia"},
            },
        ),
    )
    assert context.observations([run])["profile_source"][0].input == {"source": "orders"}


def test_a_tool_nothing_called_says_so_rather_than_showing_an_example():
    page = contextpage.render_context_page(_injected(tools=[_tool("run_spec")]))

    assert "Never called in the logs read for this page" in page


def test_the_page_escapes_what_a_tool_returned():
    """A recorded result is arbitrary text out of someone's data, not markup."""
    seen = context.Observation(
        log="2026-08-08T22-10-52",
        at=None,
        seconds=None,
        input={"source": "<b>x</b>"},
        result="<script>alert(1)</script>",
        is_error=False,
        allowed=None,
    )
    page = contextpage.render_context_page(
        _injected(tools=[_tool("describe_source")], observed={"describe_source": [seen]})
    )

    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page


def test_a_prompt_that_is_not_composed_as_expected_says_so():
    page = contextpage.render_context_page(_injected(composed_as_expected=False))

    assert "Composed differently than this page assumed" in page

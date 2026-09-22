"""`portia-mcp` — the tools served to a host that is not portia.

Driven through a real MCP client over an in-memory pair, not by calling the
wrapped handlers: what a host receives is the listing and the results as the
protocol carries them, and the first drive of anything in this repo has found
bugs in exactly that seam. What is pinned is what the app did around the tools
and a host does not: the log, which chat a review reads, and Stop.
"""

import asyncio
import json
import time

import pytest

pytest.importorskip("claude_agent_sdk")

from mcp.shared.memory import create_connected_server_and_client_session as connect  # noqa: E402

from portia import findings, runlog  # noqa: E402
from portia.agent import events, tools  # noqa: E402
from portia.catalog import index_source, init_project  # noqa: E402
from portia.cli import serve  # noqa: E402
from portia.fixtures import sales_customers, sales_orders  # noqa: E402


@pytest.fixture
def sales(tmp_path, monkeypatch):
    """Two indexed sources, one column upper case, in a project at ``tmp_path``."""
    monkeypatch.chdir(tmp_path)
    sales_orders().rename(columns={"order_id": "ORDER_ID"}).to_csv("orders.csv", index=False)
    sales_customers().to_csv("customers.csv", index=False)
    d = tmp_path / ".portia"
    init_project("order reconciliation", portia_dir=d)
    index_source("orders.csv", portia_dir=d)
    index_source("customers.csv", portia_dir=d)
    return str(d)


def drive(portia_dir, script):
    """Run ``script(client, session)`` against a served project."""

    async def go():
        session = serve.Session(portia_dir)
        async with connect(serve.build(session)) as client:
            return await script(client, session)

    return asyncio.run(go())


def count(client, question="how many orders are there?"):
    return client.call_tool(
        "query_data",
        {"sql": "SELECT count(*) AS n FROM orders", "inputs": ["orders"], "question": question},
    )


def test_a_host_is_offered_the_tools_the_app_is(sales):
    """Same names, same descriptions, same schemas: one server, two transports.

    One exception, on purpose: the app's `get_context` says the brief is already
    in the system prompt, and for a host that is false.
    """

    async def both(client, session):
        served = (await client.list_tools()).tools
        # The app's list for a model that takes no image: `view_chart` needs the
        # window's memory, which is another process here (`serve._offered`).
        async with connect(tools.build_server(sees_images=False)["instance"]) as in_app:
            return served, (await in_app.list_tools()).tools

    served, in_app = drive(sales, both)
    assert [t.name for t in served] == [t.name for t in in_app]
    for ours, theirs in zip(served, in_app, strict=True):
        # The one argument a host is not asked for (`_without_the_catalog_argument`).
        theirs.inputSchema.get("properties", {}).pop("portia_dir", None)
        assert ours.inputSchema == theirs.inputSchema, ours.name
        assert ours.annotations == theirs.annotations, ours.name
        if ours.name != serve.BRIEF_TOOL:
            assert ours.description == theirs.description, ours.name
    (brief,) = (t for t in served if t.name == serve.BRIEF_TOOL)
    assert "CALL THIS FIRST" in brief.description
    assert "ALREADY HAVE" not in brief.description


def test_a_host_is_never_asked_where_the_catalog_is(sales):
    async def listing(client, session):
        return (await client.list_tools()).tools

    for tool in drive(sales, listing):
        assert "portia_dir" not in tool.inputSchema.get("properties", {}), tool.name


def test_a_host_that_says_where_the_catalog_is_anyway_is_overruled(sales):
    """Haiku passed `"portia_dir": "."` on every call and was told nothing was indexed."""

    async def ask(client, session):
        brief = await client.call_tool(serve.BRIEF_TOOL, {"portia_dir": "."})
        described = await client.call_tool(
            "describe_source", {"source": "orders", "portia_dir": "."}
        )
        return brief.content[0].text, described.isError

    brief, failed = drive(sales, ask)
    assert "orders" in brief and not failed


def test_the_brief_a_host_pulls_is_the_brief_the_app_pushes(sales):
    from portia.agent import context

    async def pull(client, session):
        return (await client.call_tool(serve.BRIEF_TOOL, {})).content[0].text

    assert drive(sales, pull) == context.build_brief(sales)


def test_a_host_is_not_offered_the_tool_that_reads_the_windows_memory(sales):
    async def names(client, session):
        return {t.name for t in (await client.list_tools()).tools}

    assert drive(sales, names) == {t.name for t in tools.ALL_TOOLS} - {"view_chart"}


def test_the_instructions_arrive_with_the_connection(sales):
    async def instructions(client, session):
        return (await client.initialize()).instructions

    assert "get_context" in drive(sales, instructions)


def test_a_session_that_calls_nothing_leaves_no_log(sales):
    async def listing_only(client, session):
        await client.list_tools()

    drive(sales, listing_only)
    assert runlog.logs_in(sales, kind=runlog.CHAT) == []


def test_a_call_and_its_result_are_in_the_log_as_the_app_writes_them(sales):
    async def one(client, session):
        result = await count(client)
        return result.content[0].text, session.log.path

    text, path = drive(sales, one)
    logged = runlog.read(path)
    call, result = (e for e in logged.events if e.kind in (events.TOOL_CALL, events.TOOL_RESULT))
    assert events.tool_label(call.data["name"]) == "query_data"
    assert call.data["input"]["question"] == "how many orders are there?"
    assert result.data == {"id": call.data["id"], "text": text, "is_error": False}
    assert json.loads(text)["rows"][0]["n"] == len(sales_orders())


def test_the_header_names_the_host_and_the_listing_stops_there(sales):
    async def one(client, session):
        await count(client)
        return session.log.path

    path = drive(sales, one)
    assert runlog.read_header(path)[runlog.HOSTED] == serve.CLAUDE_CODE
    listing = runlog.read_listing(path, sales)
    assert listing["host"] == serve.CLAUDE_CODE
    assert listing["title"] == "Claude Code session"


def test_what_the_log_says_was_read_is_what_this_host_was_given(sales):
    async def one(client, session):
        await count(client)
        return session.log.path

    read = runlog.read(drive(sales, one)).prompts
    assert "get_context" in read["system"]
    assert "You have no filesystem" not in read["system"]  # the app's prompt, not sent here
    assert read["tools"].keys() == tools.descriptions(sees_images=False).keys()
    assert "CALL THIS FIRST" in read["tools"][serve.BRIEF_TOOL]


def test_a_review_reads_this_sessions_log_and_not_the_newest(sales, tmp_path):
    """The window open beside a host: the newest chat is somebody else's."""

    async def ask_then_review(client, session):
        await count(client)
        # A chat the window started afterwards, holding a question of its own.
        other = runlog.start(sales, cwd=str(tmp_path))
        other.event(
            events.Event(
                events.TOOL_CALL,
                {
                    "name": tools.qualified("query_data"),
                    "id": "x",
                    "input": {"sql": "SELECT 1", "question": "not ours"},
                },
            )
        )
        assert findings.review(sales, root=tmp_path)[0]["question"] == "not ours"
        reviewed = await client.call_tool("review_queries", {})
        return json.loads(reviewed.content[0].text), session.log.path

    reviewed, path = drive(sales, ask_then_review)
    assert [q["question"] for q in reviewed["queries"]] == ["how many orders are there?"]
    assert reviewed["chat"] == path.stem


def test_a_finding_rests_on_a_query_out_of_the_hosted_log(sales, tmp_path):
    """The whole point: the journal works with no app around it."""

    async def keep(client, session):
        await count(client)
        kept = await client.call_tool(
            "record_finding",
            {
                "question": "how many orders are there?",
                "answer": "every order is one row",
                "so": "orders is the grain to build on",
                "about": ["orders"],
                "from": [1],
            },
        )
        return kept.isError, kept.content[0].text

    failed, text = drive(sales, keep)
    assert not failed, text
    (finding,) = findings.load_all(tmp_path)
    assert finding["queries"][0]["sql"] == "SELECT count(*) AS n FROM orders"
    assert json.loads(finding["queries"][0]["result"])["rows"][0]["n"] == len(sales_orders())


CHART = {
    "question": "how many orders per customer?",
    "tab": "orders per customer",
    "sql": "SELECT customer_id, count(*) AS n FROM orders GROUP BY 1",
    "inputs": ["orders"],
    "vega": {"mark": "bar", "encoding": {"x": {"field": "customer_id"}, "y": {"field": "n"}}},
}


@pytest.fixture
def no_listeners():
    from portia.agent import drawn

    drawn.reset()
    yield drawn
    drawn.reset()


def test_a_chart_goes_to_disk_and_the_receipt_says_nobody_is_looking(sales, no_listeners):
    from portia import figures

    async def draw(client, session):
        return json.loads((await client.call_tool("plot_data", CHART)).content[0].text)

    receipt = drive(sales, draw)
    assert receipt["shown"] == {"window": "closed", "open_with": no_listeners.OPEN_WITH}
    (doc,) = figures.stashed(sales)
    assert doc["name"] == CHART["tab"] and doc["rows"], "the rows are what the window draws"


def test_the_receipt_says_a_window_is_open_when_one_is(sales, no_listeners):
    no_listeners.announce(sales, "http://127.0.0.1:8080")

    async def draw(client, session):
        return json.loads((await client.call_tool("plot_data", CHART)).content[0].text)

    assert drive(sales, draw)["shown"] == {"window": "open", "url": "http://127.0.0.1:8080"}


def test_a_window_that_died_without_saying_so_is_not_open(sales, no_listeners, tmp_path):
    (tmp_path / ".portia" / no_listeners.WINDOW_FILE).write_text(
        '{"pid": 999999999, "url": ""}', encoding="utf-8"
    )
    assert no_listeners.watching(sales)["window"] == "closed"


def test_a_render_the_window_refused_comes_back_on_the_next_receipt(sales, no_listeners):
    from portia import figures

    async def draw_twice(client, session):
        await client.call_tool("plot_data", CHART)
        figures.stash_failure(CHART["tab"], "Unrecognized mark", sales)  # the window's half
        other = {**CHART, "tab": "a second chart"}
        return json.loads((await client.call_tool("plot_data", other)).content[0].text)

    assert drive(sales, draw_twice)["render_failures"] == {CHART["tab"]: "Unrecognized mark"}


def test_the_apps_receipt_is_unchanged(sales, no_listeners):
    """`shown` is a fact about another process. In the app the window drew it."""
    receipt = tools._draw({**CHART, "portia_dir": sales})
    assert "shown" not in receipt


def test_a_refused_call_is_logged_as_refused(sales):
    async def bad(client, session):
        result = await client.call_tool(
            "query_data", {"sql": "DROP TABLE orders", "inputs": ["orders"], "question": "?"}
        )
        return result.isError, session.log.path

    failed, path = drive(sales, bad)
    assert failed
    (result,) = (e for e in runlog.read(path).events if e.kind == events.TOOL_RESULT)
    assert result.data["is_error"]


def test_a_cancelled_request_stops_the_query_and_not_only_the_wait(sales):
    """Stop has to reach the database. A cancelled `await` leaves its thread running."""
    slow = {
        "sql": "SELECT sum(a.range * b.range) AS n FROM range(10000000) a, range(1000000) b",
        "inputs": ["orders"],
        "question": "how long can this take?",
    }
    ended = {}

    async def timed(args):
        try:
            return await tools.query_data.handler(args)
        finally:
            ended["at"] = time.monotonic()

    async def press():
        session = serve.Session(sales)
        task = asyncio.ensure_future(session.wrap("query_data", timed)(slow))
        await asyncio.sleep(0.5)
        pressed = time.monotonic()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        while "at" not in ended:
            assert time.monotonic() - pressed < 10, "the query outlived the cancel"
            await asyncio.sleep(0.05)
        return session.log.path

    path = asyncio.run(press())
    (result,) = (e for e in runlog.read(path).events if e.kind == events.TOOL_RESULT)
    assert result.data["is_error"]
    assert "Stop" in result.data["text"]


# --- a warehouse, with no window to sign in through (`docs/HEADLESS.md` §7) --------


@pytest.fixture
def warehouse(sales, tmp_path, monkeypatch):
    """``name(auth)``: the served project, pointed at a Snowflake connection signing in that way."""
    from portia import catalog, connectors
    from portia.connectors import registry

    monkeypatch.setattr(registry, "CONNECTIONS", tmp_path / "connections.yaml")

    def name(auth, connection="demo"):
        registry.save(
            registry.Connection(
                name=connection, auth=auth, account="acme-eu", user="jad", warehouse="WH_S"
            )
        )
        catalog.set_connection(connection, portia_dir=sales)
        return sales

    yield name
    connectors.deactivate()
    serve._unusable.clear()


def _brief(portia_dir):
    async def pull(client, session):
        return (await client.call_tool(serve.BRIEF_TOOL, {})).content[0].text

    return drive(portia_dir, pull)


def test_on_files_the_brief_says_nothing_about_signing_in(sales):
    assert "Signing in" not in _brief(sales)


def test_the_brief_warns_that_a_browser_will_open_before_it_does(warehouse):
    from portia.connectors import registry

    brief = _brief(warehouse(registry.BROWSER))
    assert "Signing in to `demo`" in brief and "browser window" in brief
    assert "never given a credential" in brief


def test_a_file_connection_says_nothing_is_typed_and_the_file_is_not_to_be_opened(warehouse):
    from portia.connectors import registry

    brief = _brief(warehouse(registry.FILE))
    assert "connections.toml" in brief and "connect suggest" in brief
    assert "browser window" not in brief


def test_a_typed_password_is_said_to_be_unusable_before_the_first_refusal(warehouse):
    from portia.connectors import registry

    brief = _brief(warehouse(registry.PASSWORD))
    assert "cannot be opened from here" in brief and "password" in brief
    assert "Do not ask the user for it" in brief
    assert "--auth file" in brief


def test_the_refusal_a_host_reads_never_says_enter_it(warehouse):
    """`SecretRequired` says *enter it to connect*. Here there is nowhere to."""
    from portia.connectors import registry

    async def ask(client, session):
        result = await count(client)
        return result.isError, result.content[0].text, session.log.path

    failed, text, path = drive(warehouse(registry.PASSWORD), ask)
    assert failed
    assert "enter it" not in text and "SecretRequired" not in text
    assert "Do not ask the user for it" in text and "`demo`" in text
    (logged,) = (e for e in runlog.read(path).events if e.kind == events.TOOL_RESULT)
    assert logged.data["text"] == text, "the log holds what the model read"


def test_the_server_follows_a_connection_chosen_halfway_through_a_session(warehouse, sales):
    """`connect use` runs in another process, after this one started on files."""
    from portia.connectors import registry
    from portia.core import backend

    async def halfway(client, session):
        before = (await client.call_tool(serve.BRIEF_TOOL, {})).content[0].text
        warehouse(registry.FILE)  # the other process: `connect add`, `connect use`
        after = (await client.call_tool(serve.BRIEF_TOOL, {})).content[0].text
        return before, after, backend.active()

    before, after, active = drive(sales, halfway)
    assert "Signing in" not in before
    assert "Signing in to `demo`" in after
    assert active.remote and active.label == "demo"


def test_a_connection_this_machine_does_not_have_is_said_and_the_server_still_serves(
    sales, tmp_path, monkeypatch
):
    from portia import catalog, connectors
    from portia.connectors import registry

    monkeypatch.setattr(registry, "CONNECTIONS", tmp_path / "connections.yaml")
    catalog.set_connection("somebody-elses", portia_dir=sales)
    try:
        brief = _brief(sales)
    finally:
        connectors.deactivate()
        serve._unusable.clear()
    assert "`somebody-elses` is not usable on this machine" in brief
    assert "orders" in brief, "the catalog still reads"

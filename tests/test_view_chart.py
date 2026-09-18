"""`view_chart`: the copilot looks at a chart the window painted.

`docs/VISUALIZATION.md` §12. Everything here runs without a browser: a surface
is a subscriber and a picture is a string, which is what `agent/drawn` was shaped
for. What only a browser can show (the canvas being shrunk, given a background,
and sent) is pinned as text against `chart.js` and driven by hand.
"""

import asyncio
import json

import pytest

from portia.agent import drawn, handlers, providers, session, tools

PIXELS = "aGVsbG8="  # any base64; nothing here decodes it


def chart(tab="Orders", rows=None):
    rows = rows if rows is not None else [{"City": "Lyon", "n": 3}, {"City": "Nice", "n": 5}]
    return {
        "tab": tab,
        "question": "orders per city?",
        "vega": {"mark": "bar", "encoding": {"x": {"field": "City"}, "y": {"field": "n"}}},
        "rows": rows,
        "columns": ["City", "n"],
        "n_rows": len(rows),
    }


def painted(tab="Orders", **kw):
    drawn.report_picture(tab, drawn.Picture(image=PIXELS, width=900, height=500, **kw))


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    drawn.reset()
    # No test should spend three seconds learning that nothing was painted.
    monkeypatch.setattr(drawn, "PICTURE_WAIT", 0.05)
    yield
    drawn.reset()


def _call(**kw):
    return asyncio.run(tools.view_chart.handler(kw))


# --- the picture --------------------------------------------------------------


def test_the_model_gets_an_image_block_and_the_facts_beside_it():
    drawn.subscribe(lambda _: None)
    painted(chart=chart())

    result = _call(tab="Orders")

    image, text = result["content"]
    assert image == {"type": "image", "data": PIXELS, "mimeType": "image/png"}
    facts = json.loads(text["text"])
    assert facts["viewed"] == "Orders"
    assert facts["picture"] == {"width": 900, "height": 500}
    # The measured rows ride beside the pixels, paired, in the receipt's shape:
    # the right numbers have to be the nearest ones.
    assert facts["plotted"] == [{"City": "Lyon", "n": 3}, {"City": "Nice", "n": 5}]


def test_the_picture_never_becomes_text():
    """Only text blocks reach a chat log, and nothing saves a picture."""
    from portia.agent import events

    drawn.subscribe(lambda _: None)
    painted(chart=chart())
    result = _call(tab="Orders")
    assert PIXELS not in events.tool_result_text(result["content"])


def test_a_big_chart_says_unpaired_beside_its_picture():
    drawn.subscribe(lambda _: None)
    rows = [{"City": f"c{i}", "n": i} for i in range(tools.RECEIPT_ROWS + 1)]
    painted(chart=chart(rows=rows))
    facts = json.loads(_call(tab="Orders")["content"][1]["text"])
    assert facts[tools.UNPAIRED] is True
    assert "plotted" not in facts


def test_the_facts_are_the_receipts_own():
    """One function, so the numbers beside the pixels are the ones it drew from."""
    receipt = tools._receipt(chart())
    assert {k: v for k, v in receipt.items() if k != "drawn"} == tools._facts(chart())


def test_a_picture_with_no_rows_behind_it_still_comes_back():
    drawn.subscribe(lambda _: None)
    painted()
    facts = json.loads(_call(tab="Orders")["content"][1]["text"])
    assert facts == {"viewed": "Orders", "picture": {"width": 900, "height": 500}}


# --- staleness ----------------------------------------------------------------


def test_redrawing_a_tab_forgets_its_picture():
    """A correction must never be judged by the render it corrected."""
    drawn.subscribe(lambda _: None)
    painted(chart=chart())
    drawn.publish(chart())
    assert drawn.picture("Orders", wait=0) is None


def test_it_waits_for_a_paint_that_is_on_its_way(monkeypatch):
    import threading

    monkeypatch.setattr(drawn, "PICTURE_WAIT", 2.0)
    drawn.subscribe(lambda _: None)
    threading.Timer(0.1, painted).start()
    assert handlers.view_chart("Orders")["image"] == PIXELS


# --- the three refusals -------------------------------------------------------


def test_a_failed_render_is_the_renderers_message():
    drawn.subscribe(lambda _: None)
    drawn.report_failure("Orders", "Cannot read properties of undefined")
    result = _call(tab="Orders")
    assert result["is_error"]
    assert "Cannot read properties of undefined" in result["content"][0]["text"]
    # Read, not taken: asked twice, it says the same thing twice.
    assert _call(tab="Orders")["is_error"]


def test_a_paint_clears_a_failure():
    drawn.report_failure("Orders", "boom")
    painted()
    assert drawn.broken("Orders") is None


def test_with_no_window_it_refuses_at_once(monkeypatch):
    monkeypatch.setattr(drawn, "PICTURE_WAIT", 60.0)  # would hang if it waited
    with pytest.raises(ValueError, match="no window"):
        handlers.view_chart("Orders")


def test_a_tab_nobody_painted_is_refused():
    drawn.subscribe(lambda _: None)
    with pytest.raises(ValueError, match="has not painted"):
        handlers.view_chart("Orders")


def test_a_tab_is_required():
    with pytest.raises(ValueError, match="needs a `tab`"):
        handlers.view_chart("  ")


# --- only a model that can see is offered it ------------------------------------


def test_a_model_that_cannot_see_is_not_offered_it():
    assert "view_chart" in tools.descriptions(sees_images=True)
    assert "view_chart" not in tools.descriptions(sees_images=False)
    blind = {t.name for t in tools.offered(sees_images=False)}
    assert blind == {t.name for t in tools.ALL_TOOLS} - {"view_chart"}


def test_which_providers_see():
    assert providers.get("anthropic").sees_images is True
    assert providers.get("ollama").sees_images is False
    assert providers.get("llamacpp").sees_images is False


def test_the_prompt_is_measured_without_a_tool_that_was_not_sent():
    longer = session.prompt_chars("nowhere/.portia", "anthropic")
    shorter = session.prompt_chars("nowhere/.portia", "ollama")
    assert longer - shorter == len(tools.descriptions()["view_chart"])


# --- the window's half ----------------------------------------------------------


def test_the_window_files_a_picture_under_the_name_the_agent_used(monkeypatch):
    from portia.ui import charts
    from portia.ui.state import App, Chart

    app = App()
    monkeypatch.setattr(charts, "APP", app)
    app.show_chart(Chart(name="Orders", question="q", rows=[{"City": "Lyon"}], columns=["City"]))
    # Kept by the user since: its key is its path now, and the agent still says "Orders".
    app.chart("Orders").path = "figures/orders.yaml"

    charts.pictured("figures/orders.yaml", "data:image/png;base64," + PIXELS, 900, 500)

    found = drawn.picture("Orders", wait=0)
    assert found is not None and found.image == PIXELS
    assert found.chart["rows"] == [{"City": "Lyon"}]


@pytest.mark.parametrize(
    "image",
    ["data:image/jpeg;base64,abc", "not a data url", "data:image/png;base64," + "A" * 900_001],
)
def test_the_window_drops_what_is_not_a_picture_it_could_have_sent(monkeypatch, image):
    from portia.ui import charts
    from portia.ui.state import App, Chart

    app = App()
    monkeypatch.setattr(charts, "APP", app)
    app.show_chart(Chart(name="Orders", rows=[{"x": 1}]))
    charts.pictured("Orders", image, 900, 500)
    assert drawn.picture("Orders", wait=0) is None


def test_the_browser_shrinks_the_picture_and_paints_behind_it():
    """The canvas is transparent and retina-sized. Sent raw, a dark-mode chart is
    light text on nothing and a wide one passes socket.io's million-byte limit,
    which drops the message without a word. Both were only ever visible in a
    browser; this pins that the two lines answering them are still there."""
    from pathlib import Path

    from portia.ui import charts

    js = (Path(charts.__file__).parent / "assets" / "chart.js").read_text()
    assert "pen.fillRect(0, 0, out.width, out.height);" in js
    assert "edge / Math.max(canvas.width, canvas.height)" in js
    assert "const PICTURE_CHARS = 800000;" in js
    assert charts.PICTURE_LIMIT > 800_000, "the server must accept what the client may send"

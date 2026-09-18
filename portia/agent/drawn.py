"""Where a chart's rows go when they are not going to the model.

**Named `drawn` and not `figures`**, because `portia/figures.py` is the durable
store — a folder of saved figures on disk — and this is the transport that gets a
freshly drawn one from the MCP server to whatever surface is listening. Two
modules called `figures` in one codebase is one import away from a reader
believing publishing a chart saves it.

`docs/VISUALIZATION.md` §2.3. `plot_data` runs a ``SELECT`` and its result has two
readers with opposite needs: the **user** wants every row, drawn; the **model**
wants to know a chart exists and nothing else. A tool result is pasted into the
conversation as text and `tools.RESULT_BUDGET` refuses it over 30,000 characters,
so sending the rows back through the tool return means a 5,000-point scatter is
refused outright — and if it fit, it would be the agent reading coordinates one at
a time for a picture it will not look at.

So `tools.plot_data` splits the handler's answer: the receipt is returned to the
model, and the rows are published here.

**A subscription rather than a parameter, and that is the same call `core/cancel`
made.** A tool body runs inside the in-process MCP server, driven by the SDK, with
no handle on whatever surface a human is sitting at. Threading a sink through the
tool protocol to reach it would put a rendering argument on the signature of code
that has nothing to do with rendering. `ask.py` already injects its collaborators
for the same reason: the CLI, a test and the app all drive one loop.

**A surface that is not listening is not an error.** `cli/chat.py` has no tab strip
and no browser; a chart drawn from the terminal publishes to nobody, the agent still
gets its receipt, and the SQL is in the chat log either way. Same shape as the
best-effort graph writes in `cli/index` — a missing surface is not a failed step.
"""

from __future__ import annotations

from collections.abc import Callable

#: Given one published chart, do something with it. Returns nothing.
Sink = Callable[[dict], None]

_sinks: list[Sink] = []


def subscribe(sink: Sink) -> Callable[[], None]:
    """Start receiving charts. Returns the way to stop.

    A list rather than a single slot because two browser tabs on one project is
    the intended case (`ui/state.py`: there is one ``APP`` per process, not per
    tab), and a second subscriber silently displacing the first is the bug that
    shape invites.
    """
    _sinks.append(sink)

    def unsubscribe() -> None:
        if sink in _sinks:
            _sinks.remove(sink)

    return unsubscribe


def publish(chart: dict) -> None:
    """Hand a drawn chart to every listening surface.

    **A sink that raises must not fail the tool call.** The chart ran, the rows
    are real, and the agent's receipt is true whatever a renderer did with them;
    turning a UI fault into a tool error would tell the model its query failed
    when it did not. So each sink is called on its own and an exception stops
    that sink rather than the publication.
    """
    for sink in list(_sinks):
        try:
            sink(chart)
        except Exception:  # noqa: BLE001 - a broken surface is not a broken query
            continue


#: Charts a surface reported failing to render, waiting to be told to the model.
#: One entry per tab, newest wins — a tab redrawn under the same name is one
#: chart (`VISUALIZATION.md` §3.3), so two failures for it are one fact.
_failures: dict[str, str] = {}


def report_failure(tab: str, message: str) -> None:
    """A surface could not draw a chart portia published (`VISUALIZATION.md` §11).

    **The reverse direction of this module, and it exists because the forward one
    is fire-and-forget.** :func:`publish` swallows a sink's exception on the
    argument that a broken surface is not a broken query, and `assets/chart.js`
    catches Vega-Lite's refusal and writes it into the figure. Both are still
    right about what they are each protecting. What neither could do is tell the
    agent, which by then holds a receipt saying the chart was drawn — so it
    narrates a picture that is not there, over a chart it cannot see anyway.

    Held rather than pushed. The failure arrives after the tool returned, and
    there is no supported way to amend a result already in the conversation;
    inventing one would be portia writing into the SDK's transcript. So it waits
    for the next receipt, which is where :func:`take_failures` reads it.
    """
    named = (tab or "").strip()
    if named:
        _failures[named] = message


def take_failures() -> dict[str, str]:
    """Every render failure since the last call, and clears them.

    **Taken rather than read**, so one failure is reported once. A receipt that
    kept restating a chart the agent already fixed would be a second wrong thing
    to reason from, and the tab is on screen with the message on it regardless.
    """
    taken = dict(_failures)
    _failures.clear()
    return taken


def reset() -> None:
    """Forget every subscriber. For tests, and for closing a project."""
    _sinks.clear()
    _failures.clear()

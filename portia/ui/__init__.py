"""portia's window — V0 of the three-pane app (docs/VISION.md, docs/DESIGN.md).

    python -m portia.ui [--project DIR] [--port 8080]

Requires the ui extra:  uv sync --extra ui --extra agent

**The bar this has to clear is a full test run with no terminal.** Create the
project, write its context, add the data, index it, run a turn, answer its
questions, approve its writes, execute the spec, write the outputs, and read
every artifact — all in the window. Any step that sends the operator back to a
shell is a bug, because the thing being fixed is that tuning the loop across two
surfaces is miserable.

**It drives the copilot; it is not a viewer.** `agent/ask.py` injects ``answer``
and ``confirm`` as callables so something other than stdin can supply them, and
this is that something. The decision points are what is being tuned, and
answering a question with the evidence and the spec-so-far visible beside it is
the product.

Layout, per module:

- `app.py`      — the window: toolbar, three panes, which screen is showing
- `screens.py`  — before the panes: open a project · the brief · add data
- `artifacts.py`— left: what portia knows about
- `workflow.py` — middle: the spec as a graph, over the run report
- `transcript.py` — right: the turn, the question form, the write confirmation
- `turn.py`     — driving `session.run` with the app's own answer/confirm
- `engine.py`   — the only module that calls the engine
- `graph.py`    — DAG geometry (no NiceGUI import)
- `state.py`    — what is being looked at (no NiceGUI import)
- `theme.py` + `assets/portia.css` — the look, as tokens

Two rules hold the whole thing together, both from `DESIGN.md`:
**no computation in the UI**, and **colour and prominence communicate kind,
never rank** — the screen must not smuggle in the prioritization the checks layer
refuses to make.
"""

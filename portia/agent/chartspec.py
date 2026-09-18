"""What the agent may write into a chart, and the one thing it may not.

`docs/VISUALIZATION.md` §2.4 and §2.9. The agent authors a **Vega-Lite spec** —
layers, palettes, scales, axis and legend configuration, formatting, all of it —
and this module is the guard that makes that safe. It is `ops/sql.check_sql`'s
shape and it is here for `check_sql`'s reason: the escape hatch is what makes the
tool useful, and a hatch with nothing at the door is a different product.

**The line is `CLAUDE.md`'s, restated for pixels: the agent may author a
transform, and never a number.** Every chart grammar is partly a transform
language. Vega-Lite will take ``aggregate: "mean"`` in a channel, ``bin: true``,
a ``transform`` block with a ``regression`` in it, or an ``expr`` anywhere, and
compute the result in the browser. A number that arrives that way came from no
``SELECT``, is in no chat log, and `findings.review` cannot read it back — so a
finding resting on it is prose with nothing underneath.

So: everything passes except the compute surface, which is refused by name with a
message saying to put it in the SQL. That is not a hedge between the two options
§2.8 recorded. It is the whole of the first one — the vocabulary is Vega-Lite's,
not a list portia maintains and keeps having to widen — with the second one's
single rule kept.

**Two things that compute and are allowed, on purpose.** A `boxplot` works out a
median and `stack` sums bars. Both are renderings of rows that are all measured
and all in the log, neither asserts a datum nobody selected, and Vega-Lite stacks
a bar chart by default anyway — refusing the explicit ``stack`` while the
implicit one ran would be a rule that only catches the person who wrote it down.
The line is *a number presented as a measurement*, not *arithmetic happened*.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

#: Keys that compute a value the query did not return. Refused anywhere in the
#: spec, at any depth, in a channel or a transform block or a config.
#:
#: ``expr`` and ``signal`` are here because a Vega expression is a general
#: escape: ``{"value": {"expr": "datum.n * 2"}}`` is an aggregate with better
#: manners. ``datum`` is not a key anyone writes deliberately outside one.
COMPUTES = frozenset(
    {
        "aggregate",
        "bin",
        "calculate",
        "density",
        "expr",
        "fold",
        "impute",
        "joinaggregate",
        "loess",
        "lookup",
        "op",
        "pivot",
        "quantile",
        "regression",
        "sequence",
        "signal",
        "timeUnit",
        "transform",
        "window",
    }
)

#: Keys portia supplies and an agent may not. Not a safety rule — a clarity one:
#: two sources for the rows is a chart that can disagree with its own query.
#: ``url`` would also reach the network from a sandbox that has none.
OURS = frozenset({"$schema", "data", "datasets", "url"})

#: Every mark Vega-Lite draws from rows it was handed.
#:
#: **The list is the library's, not portia's.** It used to be four names, then
#: twelve, and each widening was a release the user waited for — which is the
#: argument for not keeping a list at all. What is missing is what needs
#: something portia does not send: ``geoshape`` wants geometry and ``image``
#: wants a URL, and the sandbox has no network.
MARKS = frozenset(
    {
        "arc",
        "area",
        "bar",
        "boxplot",
        "circle",
        "errorband",
        "errorbar",
        "line",
        "point",
        "rect",
        "rule",
        "square",
        "text",
        "tick",
        "trail",
    }
)


def check(spec: Any) -> None:
    """Refuse a spec that computes, or that supplies its own data.

    Raises `ValueError` naming the key and where it was, which is what the agent
    reads. Silence is not an option here for `handlers._requested`'s reason: a
    chart drawn with a channel quietly dropped looks exactly as finished as the
    one that was asked for.
    """
    from portia.agent import prompts

    if not isinstance(spec, dict) or not spec:
        raise ValueError(prompts.error("chart_needs_a_spec"))
    for key, path in _keys(spec):
        if key in COMPUTES:
            raise ValueError(prompts.error("chart_computes", key=key, path=path))
        if key in OURS:
            raise ValueError(prompts.error("chart_supplies_data", key=key, path=path))
    marks = list(_marks(spec))
    if not marks:
        raise ValueError(prompts.error("chart_needs_a_mark", marks=_named(MARKS)))
    for mark in marks:
        if mark not in MARKS:
            raise ValueError(prompts.error("chart_unknown_mark", mark=mark, marks=_named(MARKS)))


def fields(spec: Any) -> list[str]:
    """Every column the spec names, in the order it names them.

    What `handlers.plot_data` checks against the columns the ``SELECT`` actually
    returned. A misspelled field draws an empty axis in Vega-Lite rather than an
    error, which is the failure this whole module is shaped against.
    """
    found: list[str] = []
    for value in _walk(spec):
        if isinstance(value, dict):
            field = value.get("field")
            if isinstance(field, str) and field and field not in found:
                found.append(field)
    return found


def respell(spec: Any, resolved: Iterable[str]) -> Any:
    """The spec with each ``field`` respelled, in :func:`fields` order.

    ``resolved`` is what `dialect.resolve_columns` made of :func:`fields`, so
    the two line up by position, and a name it left alone maps to itself. A
    copy: the agent's spec is what lands in the log and the figure, and this
    spelling is for the rows the browser will actually be handed.
    """
    mapping = dict(zip(fields(spec), resolved, strict=True))
    if all(k == v for k, v in mapping.items()):
        return spec
    return _respell(spec, mapping)


def _respell(node: Any, mapping: dict[str, str]) -> Any:
    if isinstance(node, dict):
        out = {}
        for key, value in node.items():
            if key == "field" and isinstance(value, str):
                out[key] = mapping.get(value, value)
            else:
                out[key] = _respell(value, mapping)
        return out
    if isinstance(node, list):
        return [_respell(v, mapping) for v in node]
    return node


def _named(names: frozenset[str]) -> str:
    return ", ".join(sorted(names))


def _keys(node: Any, path: str = "") -> list[tuple[str, str]]:
    """Every key in the spec, with a readable path to it."""
    out: list[tuple[str, str]] = []
    if isinstance(node, dict):
        for key, value in node.items():
            here = f"{path}.{key}" if path else key
            out.append((key, here))
            out.extend(_keys(value, here))
    elif isinstance(node, list):
        for i, value in enumerate(node):
            out.extend(_keys(value, f"{path}[{i}]"))
    return out


def _walk(node: Any) -> list[Any]:
    out: list[Any] = []
    if isinstance(node, dict):
        out.append(node)
        for value in node.values():
            out.extend(_walk(value))
    elif isinstance(node, list):
        for value in node:
            out.extend(_walk(value))
    return out


def _marks(spec: Any) -> list[str]:
    """The mark of every view in the spec — one for a plain chart, one per layer.

    A mark is ``"bar"`` or ``{"type": "bar", ...}``; both are Vega-Lite's own
    forms and the second is how a chart gets its colour without a palette
    vocabulary portia has to invent.
    """
    found: list[str] = []
    for node in _walk(spec):
        if isinstance(node, dict) and "mark" in node:
            mark = node["mark"]
            if isinstance(mark, str):
                found.append(mark)
            elif isinstance(mark, dict):
                found.append(str(mark.get("type", "")))
            else:
                found.append(str(mark))
    return found

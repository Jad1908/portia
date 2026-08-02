"""The component vocabulary from DESIGN.md, once each.

Every pane builds from these, so a rule lives in one place: a `flag-badge` is the
same size whatever number produced it, an `acknowledged-banner` cannot collapse,
and `table-preview` says how many rows it is showing. Reuse before you add.

Two rules are enforced here rather than trusted to each caller:

- **No ranking.** Nothing sorts by severity, sizes a badge by its number, or
  rolls findings up into a score. Colour says *kind*: `error` is a blocking zero,
  `warning` is drift, the accent is an acknowledged override, and everything else
  is uncoloured.
- **No computing.** These render values the engine produced. The only arithmetic
  is `len(frame)` for the preview's honest `showing N of M`.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pandas as pd
from nicegui import ui

from portia.checks.outcome import BLOCKING_FLAGS
from portia.core.present import as_yaml, count, inline
from portia.core.table import Table
from portia.ui.state import PREVIEW_ROWS

#: `flag-badge` variants. Three, and no others (DESIGN.md).
BLOCKING = "blocking"
DRIFT = "drift"
ACKNOWLEDGED = "ack"

#: Step fields that are decisions in their own right and get their own block in a
#: laid-out payload, rather than a one-line value.
BLOCK_FIELDS = ("sql", "expect", "transforms", "rationale", "acknowledge")

#: Fields a human wrote as English, not as data. The test from DESIGN.md: if a
#: human typed it as data or an identifier it is mono; if they wrote it as
#: English it is not. A source summary in monospace reads like a machine said it.
PROSE_FIELDS = ("rationale", "summary", "context")

#: Never shown: it is plumbing the operator did not choose.
HIDDEN_FIELDS = ("portia_dir",)

_NULL = "·"


# --- text -------------------------------------------------------------------


def text(
    value: str, *, style: str = "t-body", color: str = "c-body", wrap: bool = True
) -> ui.label:
    label = ui.label(value).classes(f"{style} {color}")
    return label.classes("pre-wrap") if wrap else label


def mono(value: str, *, color: str = "c-body", small: bool = False) -> ui.label:
    return text(value, style="t-mono-sm" if small else "t-mono", color=color)


def caption(value: str, *, color: str = "c-mute") -> ui.label:
    return text(value, style="t-caption", color=color)


def empty_note(value: str) -> ui.label:
    """What an empty section says. It states the fact rather than disappearing."""
    return ui.label(value).classes("empty-note pre-wrap")


def section_header(value: str) -> ui.label:
    return ui.label(value).classes("p-section-header")


def pane_title(value: str) -> ui.label:
    return ui.label(value).classes("t-heading-lg")


def rule(strong: bool = False) -> ui.element:
    return ui.element("div").classes("p-rule-strong" if strong else "p-rule")


def scroll_area(key: str, *, classes: str = "") -> ui.element:
    """A scrolling region whose position survives the pane being rebuilt.

    NiceGUI replaces a refreshable's elements rather than patching them, and a
    replaced element starts at the top — so every click threw the file list and
    the run report back to row one. The offset is **client state**, exactly like
    the canvas's pan and zoom: the server states a key here and
    `assets/scroll.js` puts the position back on whatever element now carries it.
    Nothing measures it, nothing persists it, and no render asks for it.

    ``key`` names *what is being scrolled*, not which pane it is in — two saved
    runs shown in the same pane are two things to keep a place in, and sharing a
    key would drop you into the second one at the first one's offset.
    """
    element = ui.element("div").classes(f"p-scroll {classes}".strip())
    return element.props(f'data-scroll-key="{key}"')


# --- controls ---------------------------------------------------------------


def button(
    label: str,
    on_click: Callable[..., Any] | None = None,
    *,
    kind: str = "tertiary",
    icon: str | None = None,
    micro: bool = False,
    enabled: bool = True,
) -> ui.button:
    """One button. ``kind`` is primary | secondary | tertiary.

    The teal fill is scarce on purpose — at most one per view, and never on
    approving a write (DESIGN.md → `write-confirm`).

    **An icon with no label is an icon button**, and gets square padding rather
    than a text button's asymmetric one. That is a statement of fact about the
    arguments rather than a flag to remember: a caller that passes both gets a
    labelled button, and one that passes only an icon owes it a tooltip.
    """
    classes = f"btn btn-{kind}" + (" btn-micro" if micro else "")
    if icon and not label:
        classes += " btn-icon"
    # color=None so Quasar doesn't add `bg-primary`: which fill a button gets is
    # a portia decision (there is at most one accent fill per view), not a
    # framework default.
    b = ui.button(label, on_click=on_click, icon=icon, color=None).props("unelevated no-caps dense")
    b.classes(classes)
    b.set_enabled(enabled)
    return b


def segmented(options, current, on_pick: Callable[[str], Any]) -> None:
    """A `segmented-control`, built from our own buttons.

    Quasar's toggle paints its active segment with a solid brand fill, which
    would be a second accent fill in a view that is allowed exactly one. The
    selected segment here is the soft accent wash DESIGN.md specifies instead.
    """
    with ui.element("div").classes("row-gap-xs"):
        for option in options:
            picked = option == current
            b = button(str(option), lambda o=option: on_pick(o), micro=True)
            if picked:
                b.classes("seg-active")


def model_effort(app, on_effort: Callable[[str], Any]) -> None:
    """What a turn will spend: the model, and the reasoning effort.

    Picked in three places — the goal box, the add-data screen, and Settings —
    and it is **one setting in all three**, bound to the same two fields. Three
    hand-rolled copies of the pair is how they stop agreeing: an option added to
    one list and not the others, or a select that writes a field the turn never
    reads. The app is passed in rather than imported so this stays a control
    rather than a thing that knows about the open project.
    """
    from portia.agent.session import DEFAULT_MODEL, EFFORTS, MODELS

    app.model = app.model or DEFAULT_MODEL
    with ui.element("div").classes("row-gap-sm"):
        ui.select(list(MODELS), value=app.model).props(
            "borderless dense options-dense new-value-mode=add-unique use-input"
        ).classes("p-field p-field-mono").bind_value(app, "model")
    segmented(EFFORTS, app.effort, on_effort)


def chip(value: str) -> ui.label:
    """`type-chip` — a source's or step's kind (`csv`, `join`, `normalize`, `sql`)."""
    return ui.label(value).classes("type-chip")


def fact(icon: str, value: Any, label: str) -> ui.element:
    """One measured value, as a small icon and the number itself.

    For places where the same handful of facts repeats down a long list and a
    labelled line each would bury the values in their own labels. The icon is
    shorthand, never the whole story — ``label`` names the fact in a tooltip, so
    nothing on screen is a number whose meaning you have to guess.
    """
    with ui.element("div").classes("fact") as row:
        ui.icon(icon).classes("fact-icon")
        ui.label("—" if value is None else str(value)).classes("fact-value")
    row.tooltip(label)
    return row


def flag_badge(name: str, variant: str = "") -> ui.label:
    """One flag, named exactly as the engine names it.

    Uniform size regardless of the number behind it. A non-blocking flag is
    uncoloured — visible, not ranked.
    """
    suffix = f" flag-badge--{variant}" if variant else ""
    return ui.label(name).classes(f"flag-badge{suffix}")


#: How prose is rendered wherever it appears. ``code-friendly`` is not cosmetic:
#: it stops `_` from starting emphasis, and without it `customer_id and name`
#: came out as "customer" followed by italics with the underscores eaten. Column
#: names are the identifiers this whole product is about; a renderer that
#: silently rewrites them is worse than one that shows raw asterisks.
MARKDOWN_EXTRAS = ["fenced-code-blocks", "tables", "code-friendly"]


def markdown(value: str) -> ui.markdown:
    """Prose as its author wrote it — the copilot's, or a saved report's."""
    return ui.markdown(value, extras=MARKDOWN_EXTRAS).classes("p-markdown t-body c-body")


def code_block(value: str) -> ui.element:
    with ui.element("div").classes("code-block") as block:
        ui.html(_escape(value))
    return block


def collapsed(summary: str, body: Callable[[], Any]) -> ui.expansion:
    """A row that opens to show its evidence. Collapsed by default."""
    with ui.expansion(summary).classes("p-expansion w-full").props("dense dense-toggle") as exp:
        body()
    return exp


def kv_list() -> ui.element:
    """A container whose `kv` rows share one label column, so values line up.

    Ragged labels put every number at a different indent, which is most of what
    made the first run report unreadable.
    """
    return ui.element("div").classes("kv-list")


def kv(key: str, value: Any = None, *, body: Callable[[], Any] | None = None) -> None:
    """One row of a `kv_list`: the engine's field name, then what it measured.

    The key is the engine's own spelling (`left_dropped`, not "rows dropped from
    the left"), because those are the names an `expect` block has to use — the
    report is where you learn the vocabulary.
    """
    ui.label(key).classes("kv-key")
    if body is not None:
        with ui.element("div").classes("kv-value-slot"):
            body()
    else:
        ui.label(inline(value)).classes("kv-value")


# --- artifact rows ----------------------------------------------------------


def artifact_row(
    *,
    name: str,
    icon: str,
    meta: str = "",
    note: str = "",
    selected: bool = False,
    depth: int = 0,
    caret: str = "",
    on_click: Callable[..., Any] | None = None,
) -> ui.element:
    """One file portia knows about. Selected is one of the accent's three jobs.

    ``depth`` indents it inside the left tree and ``caret`` gives it a disclosure
    triangle, so a folder and a file are **one row type at two settings** rather
    than two components that have to be kept looking alike. The indent is handed
    to CSS as a custom property rather than computed into a padding here: how far
    a level steps in is a look, and looks live in ``assets/portia.css``.
    """
    classes = "artifact-row" + (" artifact-row--selected" if selected else "")
    with ui.element("div").classes(classes).style(f"--depth:{depth}") as row:
        if caret:
            ui.icon(caret).classes("artifact-caret")
        ui.icon(icon).classes("artifact-icon")
        # Own class rather than utility classes: this wrapper's job is to be the
        # thing that shrinks, and a long path is exactly what it holds.
        with ui.element("div").classes("artifact-body"):
            ui.label(name).classes("artifact-name")
            if note:
                ui.label(note).classes("artifact-note").tooltip(note)
        if meta:
            ui.label(meta).classes("artifact-meta")
    if on_click is not None:
        row.on("click", on_click)
    return row


# --- the acknowledged banner ------------------------------------------------


def acknowledged_banner(
    flags: list[str],
    *,
    rationale: str | None = None,
    measured: dict | None = None,
) -> ui.element:
    """A blocking flag a human waved through. It never collapses.

    A spec once shipped a table with 3.85% too much revenue because an override
    was a fifteen-character fragment buried mid-dict in a terminal prompt
    (docs/EVALUATION.md, Run 5). On screen it is a banner, at the top of its
    step, and if a step is both acknowledged and clean-looking the banner wins.

    ``measured`` is the outcome report, when there is one. Before a write there
    isn't — the step has not run — so the banner names the flags and says what
    they mean without inventing a number the engine never produced.
    """
    with ui.element("div").classes("ack-banner") as banner:
        ui.label("acknowledged override").classes("t-caption c-accent uppercase")
        with ui.element("div").classes("row-gap-xs"):
            for flag in flags:
                flag_badge(flag, ACKNOWLEDGED)
        for flag in flags:
            caption(flag_meaning(flag), color="c-body")
        if measured is not None:
            _measured_facts(measured, flags)
        if rationale:
            ui.label(rationale).classes("t-body c-body pre-wrap")
    return banner


def _measured_facts(outcome: dict, flags: list[str]) -> None:
    """What the engine measured about the waived flags — its numbers, not ours."""
    grain = outcome.get("grain")
    with kv_list():
        if grain and grain.get("measurable") and not grain.get("unique"):
            kv("grain", grain["keys"])
            kv("duplicated keys", grain.get("n_duplicated_keys"))
            kv("max multiplicity", grain.get("max_multiplicity"))
            for example in grain.get("examples") or []:
                kv("example", example)
        if grain and not grain.get("measurable"):
            kv("missing columns", grain.get("missing_columns"))
        if outcome.get("newly_all_null_columns"):
            kv("became all-null", outcome["newly_all_null_columns"])
        for name, contribution in (outcome.get("contribution") or {}).items():
            if contribution.get("contributed") is False:
                kv("contributed nothing", name)
        if "empty_output" in flags:
            kv("rows", outcome.get("n_rows"))


#: What each blocking flag means, in one line. Every one is a zero-condition —
#: that is the whole reason it can block (`checks.outcome.BLOCKING_FLAGS`).
_FLAG_MEANING = {
    "empty_output": "The step produced no rows at all.",
    "all_null_column": "A column arrived with data and came out entirely null.",
    "source_did_not_contribute": "An input put no values into the result.",
    "grain_not_unique": "The declared grain is not one row per key.",
    "grain_columns_missing": "The declared grain names columns the result doesn't have.",
}
_UNKNOWN_FLAG = "A zero-condition the engine refuses to write past unacknowledged."


def flag_meaning(flag: str) -> str:
    """One plain line for a blocking flag. Covered for every flag, by test."""
    return _FLAG_MEANING.get(flag, _UNKNOWN_FLAG)


def flag_variant(flag: str, acknowledged: list[str]) -> str:
    """Which of the three badge treatments a flag gets. Kind, never rank."""
    if flag in acknowledged:
        return ACKNOWLEDGED
    return BLOCKING if flag in BLOCKING_FLAGS else ""


# --- payloads ---------------------------------------------------------------


def payload_view(tool_input: dict, *, skip: tuple[str, ...] = HIDDEN_FIELDS) -> None:
    """A write's payload laid out, not dumped.

    A step is a decision being made on the record, so it reads as a form: one
    labelled line per field, with `sql`, `expect`, `transforms` and `rationale`
    given their own blocks. Never a `dict` repr.
    """
    step = tool_input.get("step")
    for key, value in tool_input.items():
        if key not in skip and key != "step":
            _field(key, value)
    # A `record_step` payload nests the decision one level down. It is unwrapped
    # rather than shown as an object, because the step *is* what is being agreed
    # to — `skip` applies at both levels so nothing is stated twice.
    for key, value in (step or {}).items() if isinstance(step, dict) else ():
        if key not in skip:
            _field(key, value)


def _field(key: str, value: Any) -> None:
    if value is None or value == [] or value == {}:
        return
    if key in PROSE_FIELDS and isinstance(value, str):
        with ui.element("div").classes("report-group"):
            ui.label(key).classes("report-group-label")
            ui.label(value).classes("t-body c-body pre-wrap")
    elif key in BLOCK_FIELDS or isinstance(value, (dict, list)):
        with ui.element("div").classes("report-group"):
            ui.label(key).classes("report-group-label")
            code_block(value if isinstance(value, str) else as_yaml(value))
    else:
        with kv_list():
            kv(key, value)


# --- table preview ----------------------------------------------------------


def table_preview(data, *, limit: int = PREVIEW_ROWS) -> None:
    """The produced table. Nulls are visible, never blank and never zero.

    Takes a `core.table.Table` or a DataFrame. Given a Table it reads `limit`
    rows and one count — so previewing a step that produced 80 million rows costs
    what previewing ten costs, and `workflow._table` no longer has to load a
    whole output to put a shape in a label.
    """
    if data is None:
        empty_note("no table")
        return
    total, head = table_shape(data, limit)
    if total == 0:
        empty_note(f"0 rows × {count(head.shape[1], 'column')}")
        return

    frame = head
    numeric = {c for c in head.columns if pd.api.types.is_numeric_dtype(head[c])}
    with ui.element("div").classes("table-preview"):
        ui.html(_table_html(head, numeric))
    caption(f"showing {len(head)} of {count(total, 'row')} · {count(frame.shape[1], 'column')}")


def table_shape(data, limit: int = PREVIEW_ROWS):
    """``(total rows, the first `limit` of them as pandas)`` — Table or DataFrame.

    One place both panes ask, so the number under a table and the number in the
    label above it can never come from two different counts.
    """
    if isinstance(data, Table):
        return data.preview(limit)
    return len(data), data.head(limit)


def _table_html(head: pd.DataFrame, numeric: set) -> str:
    columns = "".join(
        f"<th><span class='col-name'>{_escape(str(c))}</span>"
        f"<span class='col-dtype'>{_escape(str(head[c].dtype))}</span></th>"
        for c in head.columns
    )
    rows = "".join(
        "<tr>"
        + "".join(
            f"<td class='{'numeric' if c in numeric else ''}'>{_cell(value)}</td>"
            for c, value in zip(head.columns, row, strict=True)
        )
        + "</tr>"
        for row in head.itertuples(index=False, name=None)
    )
    return f"<table><thead><tr>{columns}</tr></thead><tbody>{rows}</tbody></table>"


def _cell(value: Any) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)) or value is pd.NaT:
        return f"<span class='null'>{_NULL}</span>"
    return _escape(str(value))


# --- helpers ----------------------------------------------------------------


def _escape(value: str) -> str:
    return (
        value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )

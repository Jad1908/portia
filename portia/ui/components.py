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
import yaml
from nicegui import ui

from portia.checks.outcome import BLOCKING_FLAGS
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


def count(n: int, word: str) -> str:
    """`1 step` / `2 steps`. A count is a measured number; it should read like one."""
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


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
    """
    classes = f"btn btn-{kind}" + (" btn-micro" if micro else "")
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


def inline(value: Any) -> str:
    """A measured value on one line, in words rather than punctuation.

    ``{"left": 8, "right": 6}`` becomes ``left 8 · right 6`` and
    ``{"left": ["customer_id"], "right": ["customer_id"]}`` becomes
    ``left customer_id · right customer_id``. Both were being dumped as inline
    YAML, braces and all, which is most of what made the report unreadable.
    Anything genuinely nested still falls back to YAML rather than being flattened
    into something that reads clearer than it is.
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return _scalar(value)
    if isinstance(value, dict) and all(not isinstance(v, dict) for v in value.values()):
        return " · ".join(f"{k} {inline(v)}" for k, v in value.items()) or "—"
    if isinstance(value, list) and all(not isinstance(v, (dict, list)) for v in value):
        return ", ".join(_scalar(v) for v in value) or "—"
    return _as_yaml(value, flow=True)


def _scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return "—" if value is None else str(value)


# --- artifact rows ----------------------------------------------------------


def artifact_row(
    *,
    name: str,
    icon: str,
    meta: str = "",
    note: str = "",
    selected: bool = False,
    on_click: Callable[..., Any] | None = None,
) -> ui.element:
    """One file portia knows about. Selected is one of the accent's three jobs."""
    classes = "artifact-row" + (" artifact-row--selected" if selected else "")
    with ui.element("div").classes(classes) as row:
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
            code_block(value if isinstance(value, str) else _as_yaml(value))
    else:
        with kv_list():
            kv(key, value)


# --- table preview ----------------------------------------------------------


def table_preview(frame: pd.DataFrame, *, limit: int = PREVIEW_ROWS) -> None:
    """The produced frame. Nulls are visible, never blank and never zero."""
    if frame is None:
        empty_note("no table")
        return
    if frame.empty:
        empty_note(f"0 rows × {count(frame.shape[1], 'column')}")
        return

    head = frame.head(limit)
    numeric = {c for c in frame.columns if pd.api.types.is_numeric_dtype(frame[c])}
    with ui.element("div").classes("table-preview"):
        ui.html(_table_html(head, numeric))
    caption(
        f"showing {len(head)} of {count(len(frame), 'row')} · {count(frame.shape[1], 'column')}"
    )


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


class _NoAliases(yaml.SafeDumper):
    """Never emit `&id001` / `*id001`.

    A join step names the same key list on both sides, and PyYAML's anchors
    turned that into `keys: &id001 [customer_id] … right: *id001` on screen.
    Correct YAML, unreadable evidence — and this pane exists to be read.
    """

    def ignore_aliases(self, data: Any) -> bool:
        return True


def _as_yaml(value: Any, *, flow: bool = False) -> str:
    """A value the way the artifacts are written, or plainly if it is a scalar.

    A scalar goes through as itself: ``yaml.safe_dump(8)`` is ``"8\\n...\\n"``, and
    a row count rendering as ``8 ...`` reads like a truncation of the number the
    whole product exists to be trusted about.
    """
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"  # as the YAML artifacts spell it
    if value is None or isinstance(value, (int, float)):
        return str(value)
    dumped = yaml.dump(
        value,
        Dumper=_NoAliases,
        sort_keys=False,
        default_flow_style=flow,
        allow_unicode=True,
        width=10_000 if flow else 80,
    )
    return dumped.strip()


def _escape(value: str) -> str:
    return (
        value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )

"""One way to show a measured value — shared by the terminal, the app, and the
saved report, so the day two of them disagree about a null rate is a day nobody
has to spend working out which one to believe."""

from __future__ import annotations

from portia.core.present import as_yaml, count, format_rate, inline

# --- rates -------------------------------------------------------------------


def test_a_rate_reads_as_whole_percent():
    assert format_rate(0.65) == "65%"
    assert format_rate(0.0) == "0%"
    assert format_rate(1.0) == "100%"


def test_a_tiny_rate_never_renders_as_zero():
    """ "0%" for a column that does have nulls is the quiet lie this prevents."""
    assert format_rate(0.004) == "<1%"
    assert format_rate(0.0001) == "<1%"


def test_an_absent_rate_says_so():
    assert format_rate(None) == "—"


# --- values on one line ------------------------------------------------------


def test_a_flat_mapping_reads_as_words():
    assert inline({"left": 8, "right": 6}) == "left 8 · right 6"


def test_a_mapping_of_lists_reads_as_words_too():
    """A join names the same key on both sides; `{left: [k], right: [k]}` was
    being dumped with braces, which is most of what made the report unreadable."""
    keys = {"left": ["customer_id"], "right": ["customer_id"]}
    assert inline(keys) == "left customer_id · right customer_id"


def test_a_list_is_comma_separated():
    assert inline(["a", "b"]) == "a, b"


def test_booleans_are_spelled_the_way_the_yaml_spells_them():
    assert inline(True) == "true"
    assert inline(False) == "false"


def test_nothing_is_a_dash_not_the_word_none():
    assert inline(None) == "—"
    assert inline([]) == "—"
    assert inline({}) == "—"


def test_a_number_survives_intact():
    """`yaml.safe_dump(8)` is `8\\n...\\n`, and `8 ...` reads like a truncation."""
    assert inline(8) == "8"
    assert as_yaml(8) == "8"


def test_genuinely_nested_values_fall_back_rather_than_being_flattened():
    """Better honest YAML than a line that reads clearer than the data is."""
    assert inline({"a": {"b": 1}}) == "{a: {b: 1}}"


def test_yaml_never_emits_anchors():
    """PyYAML aliased a shared list into `&id001` / `*id001` on screen."""
    shared = ["customer_id"]
    assert "&id" not in as_yaml({"left": shared, "right": shared})


# --- counts ------------------------------------------------------------------


def test_one_is_singular_and_everything_else_is_not():
    assert count(1, "step") == "1 step"
    assert count(0, "step") == "0 steps"
    assert count(2, "step") == "2 steps"

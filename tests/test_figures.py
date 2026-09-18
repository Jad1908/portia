"""The gallery on disk — the two ways out of a folder (`docs/VISUALIZATION.md` §6.5).

`figures.remove` has always taken an empty folder and refused a full one; that
stays the default. `remove_folder` is the override the window uses once a person
has said yes, and what it returns is what the window needs next: the tabs to
retire.
"""

from __future__ import annotations

import pytest

from portia import figures


def _chart(tab: str) -> dict:
    return {"tab": tab, "question": "why?", "sql": "select 1", "rows": [{"x": 1}], "columns": ["x"]}


def test_an_empty_folder_is_removed_without_a_question(tmp_path):
    figures.make_folder("drafts", root=tmp_path)
    assert figures.remove_folder("drafts", root=tmp_path) == []
    assert not (tmp_path / "figures" / "drafts").exists()


def test_a_folder_holding_figures_is_refused_by_default(tmp_path):
    figures.save(_chart("Cities"), folder="drafts", root=tmp_path)
    with pytest.raises(ValueError, match="holds 1 figure"):
        figures.remove_folder("drafts", root=tmp_path)
    assert (tmp_path / "figures" / "drafts" / "cities.json").exists()


def test_contents_counts_every_figure_underneath_at_any_depth(tmp_path):
    figures.save(_chart("Cities"), folder="drafts", root=tmp_path)
    figures.save(_chart("Volumes"), folder="drafts/old", root=tmp_path)
    assert figures.contents("drafts", root=tmp_path) == [
        "figures/drafts/cities.json",
        "figures/drafts/old/volumes.json",
    ]
    assert figures.contents("nowhere", root=tmp_path) == []


def test_removing_with_contents_returns_what_went_so_tabs_can_follow(tmp_path):
    figures.save(_chart("Cities"), folder="drafts", root=tmp_path)
    figures.save(_chart("Volumes"), folder="drafts/old", root=tmp_path)
    gone = figures.remove_folder("drafts", root=tmp_path, with_contents=True)
    assert gone == ["figures/drafts/cities.json", "figures/drafts/old/volumes.json"]
    assert not (tmp_path / "figures" / "drafts").exists()


def test_the_top_level_cannot_be_deleted(tmp_path):
    """`_safe_folder` turns an empty name into the gallery root; refuse it by name."""
    figures.save(_chart("Cities"), root=tmp_path)
    with pytest.raises(ValueError, match="top level"):
        figures.remove_folder("", root=tmp_path, with_contents=True)
    # A climbing name is stripped to a name inside the gallery and found missing.
    with pytest.raises(ValueError):
        figures.remove_folder("../figures", root=tmp_path, with_contents=True)
    assert (tmp_path / "figures" / "cities.json").exists()


def test_a_folder_that_does_not_exist_is_an_error_not_a_silent_success(tmp_path):
    with pytest.raises(ValueError, match="not a folder"):
        figures.remove_folder("ghost", root=tmp_path)


# --- the stash: charts drawn by a process with no window ---------------------


def _drawn(tab="rates", rows=None):
    return {
        "tab": tab,
        "question": "how do rates differ?",
        "sql": "SELECT 1",
        "inputs": ["t"],
        "vega": {"mark": "bar"},
        "columns": ["RISK", "rate"],
        "n_rows": len(rows or [{"RISK": "a", "rate": 1}]),
        "rows": rows or [{"RISK": "a", "rate": 1}],
    }


def test_a_stashed_chart_carries_what_a_figure_carries(tmp_path):
    path = figures.stash(_drawn(), tmp_path)
    assert path.parent.name == figures.DRAWN_DIR
    (doc,) = figures.stashed(tmp_path)
    assert doc["name"] == "rates" and doc["rows"] == [{"RISK": "a", "rate": 1}]
    assert doc["path"] == str(path)


def test_stashing_under_the_same_name_replaces(tmp_path):
    """The tab name is the chart. A second file would be the old picture surviving."""
    figures.stash(_drawn(rows=[{"RISK": "a", "rate": 1}]), tmp_path)
    figures.stash(_drawn(rows=[{"RISK": "a", "rate": 2}]), tmp_path)
    (doc,) = figures.stashed(tmp_path)
    assert doc["rows"] == [{"RISK": "a", "rate": 2}]


def test_a_stash_is_never_in_the_gallery(tmp_path):
    figures.stash(_drawn(), tmp_path / ".portia")
    assert figures.load_all(tmp_path) == []


def test_unstashing_takes_the_chart_and_its_failure(tmp_path):
    figures.stash(_drawn(), tmp_path)
    figures.stash_failure("rates", "no such mark", tmp_path)
    figures.unstash("rates", tmp_path)
    assert figures.stashed(tmp_path) == []
    assert figures.take_stash_failures(tmp_path) == {}


def test_a_failure_is_taken_once_and_a_redraw_clears_it(tmp_path):
    figures.stash(_drawn(), tmp_path)
    figures.stash_failure("rates", "no such mark", tmp_path)
    assert figures.take_stash_failures(tmp_path) == {"rates": "no such mark"}
    assert figures.take_stash_failures(tmp_path) == {}
    figures.stash_failure("rates", "no such mark", tmp_path)
    figures.stash(_drawn(), tmp_path)  # drawn again: the old failure is about the old spec
    assert figures.take_stash_failures(tmp_path) == {}

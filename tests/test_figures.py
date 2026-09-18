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

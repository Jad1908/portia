"""The notes checker: that a citation which no longer resolves is caught, and the
facts it states about a note are the right ones.

The notes are private, so these build a small notes folder and a small repo of
their own rather than reading `docs/`.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from devtools import notes


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    d = tmp_path / "notes"
    d.mkdir()
    (d / "PIPELINE.md").write_text(
        "# Pipeline\n\n**Status 2026-10-14.** Built.\n\n## 2. Decisions\n\n### 2.7 Only data in the repo\n\n"
        "## 9. `read_spec`\n\nThe card.\n",
        encoding="utf-8",
    )
    (d / "PLAN.md").write_text(
        "# Plan\n\n## Auth posture\n\n~~old~~ and `feat/old-branch`.\n\n"
        "**Status 2026-09-01.** A second one.\n",
        encoding="utf-8",
    )
    return d


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    (r / "a.py").write_text(
        "# PIPELINE.md §2.7 holds. PIPELINE.md §6 is gone.\n"
        "# (docs/PLAN.md → Auth posture). (PLAN.md → Where we were).\n"
        "# CONNECTOR.md was merged away. README.md is a file.\n",
        encoding="utf-8",
    )
    (r / "README.md").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(r)], check=True)
    subprocess.run(["git", "-C", str(r), "add", "."], check=True)
    return r


def test_headings_give_their_numbers(folder: Path) -> None:
    loaded = notes.load_notes(folder)
    assert loaded["PIPELINE"].numbers == {"2", "2.7", "9"}


def test_only_what_does_not_resolve_is_reported(folder: Path, repo: Path) -> None:
    bad = notes.cites(repo, notes.load_notes(folder))
    assert len(bad) == 3
    assert any("PIPELINE.md §6" in b for b in bad)
    assert any("Where we were" in b for b in bad)
    assert any("CONNECTOR.md: no such note" in b for b in bad)
    assert not any("README" in b or "§2.7" in b or "Auth posture" in b for b in bad)


def test_health_states_budget_status_and_history(folder: Path, monkeypatch) -> None:
    monkeypatch.setattr(notes, "WORD_BUDGET", 10)
    rows = {r.name: r for r in notes.health(notes.load_notes(folder))}
    assert rows["PIPELINE.md"].status == "ok"
    assert rows["PLAN.md"].status == "missing"
    assert rows["PLAN.md"].traces == "1 struck-through, 1 branch or worktree names"
    assert rows["PIPELINE.md"].over


def test_a_public_clone_has_no_notes_and_passes(tmp_path: Path, capsys) -> None:
    assert notes.main(["--notes", str(tmp_path / "docs")]) == 0
    assert "No notes" in capsys.readouterr().out


def test_the_checker_does_not_read_its_own_examples(folder: Path, repo: Path) -> None:
    """Its docstring and this file quote broken citations on purpose."""
    for rel in notes.SELF:
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# CONNECTOR.md is gone; PIPELINE.md §6 too.\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    assert not any(b.startswith(notes.SELF) for b in notes.cites(repo, notes.load_notes(folder)))

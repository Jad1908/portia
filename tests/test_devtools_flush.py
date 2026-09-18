"""The flush tool — that it removes what it planned and nothing else.

`devtools/` is not part of the product, so this covers only what is real logic
rather than printing: what lands in the plan, that the histories stay out of the
default, and the guard that matters. **Everything here deletes files**, which is
the whole reason it is tested: the tool exists to be pointed at a project by
hand, and the two ways it could go wrong are taking the data with it and taking
something git tracks.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from devtools import flush


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A project with one of everything, and a data file that must survive.

    It names a warehouse connection too, with one table in scope, so the reset
    is tested against the remote half of `project.yaml` as well as the local.
    """
    root = tmp_path / "proj"
    for d in (
        ".portia/sources",
        ".portia/chats",
        ".portia/indexing",
        "specs/staging",
        "models",
        "out/2026-01-01/_run",
        "findings",
        "figures/sub",
        "data",
    ):
        (root / d).mkdir(parents=True)
    (root / ".portia/project.yaml").write_text(
        yaml.safe_dump(
            {
                "project": "my brief",
                "groups": [{"name": "g", "sources": ["t"]}],
                "sources": {"t": "sources/t.yaml"},
                "data_dir": "data",
                "connection": "work",
                "scope": ["DB.RAW.T"],
                "agent_writes": True,
            }
        )
    )
    for f in (
        ".portia/sources/t.yaml",
        ".portia/chats/c.jsonl",
        ".portia/indexing/i.jsonl",
        "specs/s.yaml",
        "specs/staging/s2.yaml",
        "models/m.sql",
        "out/2026-01-01/_run/index.md",
        "findings/f.yaml",
        "figures/a.json",
        "figures/sub/b.json",
        "data/t.csv",
    ):
        (root / f).write_text("x\n")
    return root


def _run(root: Path, categories: tuple[str, ...]) -> None:
    flush.flush(flush.plan(root, categories), categories)


def test_the_default_leaves_the_data_and_the_histories(project):
    _run(project, flush.DEFAULT)

    survivors = sorted(p.relative_to(project).as_posix() for p in project.rglob("*") if p.is_file())
    assert survivors == [
        ".portia/chats/c.jsonl",
        ".portia/indexing/i.jsonl",
        ".portia/project.yaml",
        "data/t.csv",
    ]


SETUP = {
    "project": "my brief",
    "data_dir": "data",
    "connection": "work",
    "agent_writes": True,
}


def test_the_brief_and_setup_survive_a_catalog_flush(project):
    """`data_dir`, `connection` and `agent_writes` are setup, not output. Losing one
    makes the next index read the wrong place, which is a confusing failure
    rather than a clean slate."""
    _run(project, ("catalog",))

    doc = yaml.safe_load((project / ".portia/project.yaml").read_text())
    assert doc == SETUP


def test_the_scope_goes_with_the_catalog(project):
    """`scope` is the remote source index: `catalog.scope_table` appends to it as
    it writes the entry. Left behind after the entries are gone, it named tables
    nothing described, and the picker drew every one as *in scope* with its tick
    disabled — so a flushed warehouse project could not be re-indexed until
    `.portia` was deleted by hand (2026-09-06)."""
    _run(project, ("specs",))
    doc = yaml.safe_load((project / ".portia/project.yaml").read_text())
    assert doc["scope"] == ["DB.RAW.T"], "only the catalog clears it"

    _run(project, ("catalog",))
    doc = yaml.safe_load((project / ".portia/project.yaml").read_text())
    assert "scope" not in doc
    assert doc["connection"] == "work"


def test_the_brief_goes_only_when_asked(project):
    _run(project, ("catalog", "brief"))

    doc = yaml.safe_load((project / ".portia/project.yaml").read_text())
    assert doc == {k: v for k, v in SETUP.items() if k != "project"}


def test_figures_are_flushed_by_default_wherever_they_were_dragged(project):
    """Nothing reads a figure back to the agent, but a saved chart is a gallery
    row and a tab one click away, so a reset that kept them would open on the
    last attempt's pictures. `figures.load_all` walks subfolders; so does this."""
    p = flush.plan(project, flush.DEFAULT)
    assert sorted(x.name for x in p.paths["figures"]) == ["a.json", "b.json"]


def test_history_is_never_in_the_default(project):
    assert "history" not in flush.DEFAULT
    assert not flush.plan(project, flush.DEFAULT).paths.get("history")

    p = flush.plan(project, ("history",))
    assert [x.name for x in p.paths["history"]] == ["chats", "indexing"]
    assert not p.paths.get("catalog"), "naming a category selects only it"


def test_a_git_tracked_file_is_skipped_and_reported(project):
    """The repo root is itself a portia project and `specs/sales_join.yaml` is
    hand-authored and committed, so a mistyped path is a real way to lose real
    work. Tracked files are skipped rather than failing the run — the untracked
    rest of a sandbox project is still what you wanted gone."""
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)
    subprocess.run(["git", "add", "specs/s.yaml"], cwd=project, check=True)

    p = flush.plan(project, ("specs",))

    assert [x.name for x in p.tracked] == ["s.yaml"]
    assert [x.name for x in p.paths["specs"]] == ["s2.yaml"], "the untracked one still goes"

    flush.flush(p, ("specs",))
    assert (project / "specs/s.yaml").exists()
    assert not (project / "specs/staging/s2.yaml").exists()


def test_a_directory_is_not_flushed_when_it_holds_something_tracked(project):
    """The guard is about the tree, not the name: `out/` goes as a whole
    directory, so anything tracked underneath has to hold the whole thing back."""
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)
    subprocess.run(["git", "add", "out/2026-01-01/_run/index.md"], cwd=project, check=True)

    p = flush.plan(project, ("outputs",))

    assert not p.paths["outputs"], "out/ holds a tracked file, so it stays"
    assert [x.name for x in p.tracked] == ["index.md"]


def test_it_refuses_something_that_is_not_a_project(tmp_path):
    (tmp_path / "nope").mkdir()
    with pytest.raises(ValueError, match="no .portia"):
        flush.plan(tmp_path / "nope", flush.DEFAULT)

    with pytest.raises(ValueError, match="no such project"):
        flush.plan(tmp_path / "missing", flush.DEFAULT)


def test_an_unknown_category_names_the_ones_there_are(project):
    with pytest.raises(ValueError, match="catalog"):
        flush.plan(project, ("cataloge",))


def test_findings_are_flushed_by_default_and_history_is_not(project):
    """They look alike and they are not.

    A finding's prose is judgment nothing regenerates, which is the argument that
    keeps chat logs out of the default. But a finding is **read back by the
    agent** through `describe_source`, so one left over from the previous attempt
    at the same task means the next run starts holding its own prior conclusions.
    That is not a reset — and the queries underneath are in the chat log either
    way, which is what makes losing the sentence recoverable at all.
    """
    plan = flush.plan(project, flush.DEFAULT)
    assert [p.name for p in plan.paths.get("findings", [])] == ["f.yaml"]
    assert not plan.paths.get("history")

"""Remove a spec and everything it produced: ``python -m portia.cli.remove_spec``.

A spec is the only artifact in portia that *makes* others. Deleting the YAML by
hand leaves the compiled ``.sql`` on disk, the model's measured entry in
``.portia/models/``, and a Model node in the graph — three descriptions of a
table nothing builds any more, all of which read as live.

**The dependency guard is the whole of the design.** A table with something
downstream cannot go on its own, because the model reading it would compile
against a name that no longer exists. There are exactly two ways through, and
they are the two the user asked for: name the whole family at once, or start at
the top of the tree and work down. Either way the set being removed has to be
closed under "is read by", and that is one condition rather than two special
cases.

Same shape as `cli/import_data`, and for the same reasons: :func:`plan` computes
what would go **before** anything is deleted, so a confirmation shows the real
thing rather than a description of it, and it raises ``ValueError`` rather than
``SystemExit`` because a refusal is something a window has to be able to draw.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path

from portia import catalog, knowledge, pipeline, spec


@dataclass
class Removal:
    """What removing these specs would delete, computed before anything does."""

    names: list[str]
    files: list[Path] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.files)


def blocked_by(
    names: list[str], models: dict[str, Path], *, base_dir: str | Path = "."
) -> dict[str, set[str]]:
    """Models outside ``names`` that read a model inside it, by what they read.

    Empty means the set is closed and safe to remove. Anything in it is a spec
    that would be left compiling against a table nobody builds.
    """
    deps = spec.dependencies(models, base_dir=base_dir)
    going = set(names)
    blockers: dict[str, set[str]] = {}
    for model, reads in deps.items():
        if model in going:
            continue
        lost = reads & going
        if lost:
            blockers[model] = lost
    return blockers


def plan(names: list[str], root: str | Path = ".") -> Removal:
    """Everything on disk that exists because these specs do.

    The spec itself, its compiled ``.sql``, and what the last build measured
    about the table it produced (`catalog.index_model`). Not the data: a spec
    reads files it did not create, and deleting those is a different act
    entirely.
    """
    root = Path(root)
    models = spec.discover_specs(root)
    unknown = [n for n in names if n not in models]
    if unknown:
        known = ", ".join(sorted(models)) or "(none)"
        raise ValueError(f"no spec named {unknown[0]!r}. In this project: {known}")

    blockers = blocked_by(names, models, base_dir=root)
    if blockers:
        raise ValueError(_still_read(blockers))

    portia_dir = root / catalog.DEFAULT_DIR
    files: list[Path] = []
    for name in names:
        spec_file = root / models[name]
        doc = spec.load_spec(spec_file)
        for candidate in (
            spec_file,
            pipeline.model_path(models[name], layer=doc.get("layer"), root=root),
            portia_dir / catalog.MODELS_DIR / f"{name}.yaml",
        ):
            if candidate.exists():
                files.append(candidate)
    return Removal(sorted(names), files)


def _still_read(blockers: dict[str, set[str]]) -> str:
    """The refusal, naming both ends and both ways out."""
    lines = [
        f"  {model} reads {', '.join(sorted(reads))}" for model, reads in sorted(blockers.items())
    ]
    return (
        "cannot remove — these models still read what you asked to delete:\n"
        + "\n".join(lines)
        + "\n\nRemove the whole family in one go by naming them all, or start at the "
        "top of the tree and work down."
    )


def remove(p: Removal, root: str | Path = ".") -> list[Path]:
    """Delete what :func:`plan` found, then tell the graph."""
    for path in p.files:
        path.unlink()
    _sync(root)
    return p.files


def _sync(root: str | Path) -> None:
    """Rebuild the structural graph so the Model nodes go with their specs.

    **Best-effort, exactly like `handlers.record_step`'s sync** (§6.6): the files
    are already gone and a stopped container must not turn that into a failure.

    Nothing here deletes a measurement. A rebuild prunes structural edges and
    then any node left with no relationships at all, so a Model whose spec is
    gone goes with it — and a Column of that model carrying an `OVERLAPS` stays,
    because `OVERLAPS` is never pruned (§4.4, §5.2). That leaves a measured
    column with no table, which is a real hole and is `BACKLOG.md`'s to close;
    it is dormant today because nothing has ever measured a model.
    """
    try:
        knowledge.sync(root)
    except Exception as exc:  # noqa: BLE001 - the files are already gone
        print(f"note: the knowledge graph was not updated: {exc}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Remove specs and everything they produced.")
    parser.add_argument("names", nargs="+", help="model name(s) — the whole family, or top-down")
    parser.add_argument("--root", default=".", help="project root (default: .)")
    parser.add_argument("--yes", action="store_true", help="skip the confirmation")
    args = parser.parse_args()

    try:
        p = plan(args.names, args.root)
    except ValueError as exc:
        raise SystemExit(str(exc)) from None

    if not p:
        raise SystemExit(f"nothing on disk for {', '.join(p.names)}")

    print(f"removing {', '.join(p.names)} — this deletes:")
    for path in p.files:
        print(f"  {path}")
    if not args.yes and input("go ahead? [y/N] ").strip().lower() not in ("y", "yes"):
        raise SystemExit("nothing removed")

    for path in remove(p, args.root):
        print(f"removed {path}")


if __name__ == "__main__":
    main()

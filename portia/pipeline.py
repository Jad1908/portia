"""Compiling a spec to SQL — the pipeline you can hand to a data team.

`docs/PIPELINE.md` is the design; this is the half of it that turns a spec into a
file. **One spec produces one table**, so one spec compiles to one ``.sql``: its
steps become named CTEs and the last one is what the table is.

**Nothing here is new SQL.** ``run_spec`` already composes every step's ``SELECT``
into the next one and throws the result away when the run ends; each op hands back
that same SELECT expressed against its inputs' *names* (`ops.base.OpResult.compiled`,
built by the same function that built the executed query). This module stacks those
into a file. That is the whole distance between what the engine did and the artifact.

**Why it is a build output.** The spec carries the ``rationale``, the ``expect``
block and the ``grain`` claim; plain SQL can hold none of them. An editable ``.sql``
would mean the decision record describes something other than what runs, which is
the one thing this product cannot afford. So the header says not to edit it, the
fingerprint makes an edit *visible*, and :func:`is_stale` makes a file that has
drifted from its spec something the run reports rather than something you discover
later. It is committed anyway — the pipeline is the deliverable and someone has to
read it in a PR (`PIPELINE.md` §2.3).

**Sources are named, not inlined.** A model says ``FROM "orders"``, exactly as a dbt
model does, and :func:`compile_sources` writes the companion that creates those names
as views over the repo's files. So the models drop into a dbt project unchanged, and
the pipeline still runs on its own by executing the sources file first.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from pathlib import Path

import yaml

from portia.core.io import read_query
from portia.core.table import quote_ident
from portia.spec import StepResult

#: Where compiled models land, relative to the project root. Their own directory,
#: separate from ``specs/``: one is the decision record, the other the build output.
MODELS_DIR = "models"

#: The companion that creates the source names the models read. Leading underscore
#: so it sorts to the top of the directory and reads as not-a-model.
SOURCES_FILE = "_sources.sql"

#: How much of the spec digest goes in the header. Seven, as git does it — long
#: enough to not collide in a project, short enough to read.
FINGERPRINT_CHARS = 7

_HEADER_FINGERPRINT = re.compile(r"^--\s*spec fingerprint\s+([0-9a-f]+)\s*$", re.MULTILINE)


def fingerprint(doc: dict) -> str:
    """A stable digest of the parts of a spec that decide its SQL.

    Sources and steps only. ``rationale`` is in there because it is inside a step
    and cheap to leave, but the point is the shape: change what the pipeline *does*
    and the fingerprint moves, so a stale file is detectable without re-running
    anything.
    """
    material = {"sources": doc.get("sources") or {}, "steps": doc.get("steps") or []}
    canonical = yaml.safe_dump(material, sort_keys=True, default_flow_style=False)
    return hashlib.sha256(canonical.encode()).hexdigest()[:FINGERPRINT_CHARS]


def model_name(spec_path: str | Path) -> str:
    """The table a spec builds: its filename. One spec, one table, one name."""
    return Path(spec_path).stem


def model_path(spec_path: str | Path, *, layer: str | None = None, root: str | Path = ".") -> Path:
    """Where a spec's compiled ``.sql`` belongs.

    A ``layer`` becomes a subdirectory; **no layer means no subdirectory**, which
    is the whole of how a flat project is handled — the simple case is the absence
    of a field, never a second mode (`PIPELINE.md` §2.5).
    """
    base = Path(root) / MODELS_DIR
    return (base / layer if layer else base) / f"{model_name(spec_path)}.sql"


def compile_spec(
    results: list[StepResult],
    *,
    name: str,
    spec_path: str | Path | None = None,
    spec_fingerprint: str = "",
    when: datetime | None = None,
) -> str:
    """One spec's steps as one ``CREATE TABLE … AS WITH …`` statement.

    Every step becomes a CTE — including the last, with a trailing
    ``SELECT * FROM <last>``. Uniform on purpose: inlining the final step instead
    would make the file's shape depend on how many steps there are, and a diff
    between two versions of a pipeline is easier to read when only the changed
    block moves.
    """
    if not results:
        raise ValueError(f"nothing to compile: {name!r} has no steps")

    blocks = []
    for r in results:
        if not r.compiled:
            raise ValueError(f"step {r.id!r} ({r.op}) produced no compiled SQL")
        blocks.append(f"{quote_ident(r.id)} AS (\n{_indent(r.compiled)}\n)")

    return (
        _header(name, spec_path=spec_path, spec_fingerprint=spec_fingerprint, when=when)
        + f"CREATE TABLE {quote_ident(name)} AS\n"
        + "WITH "
        + ",\n".join(blocks)
        + f"\nSELECT * FROM {quote_ident(results[-1].id)};\n"
    )


def compile_sources(sources: dict[str, str], *, when: datetime | None = None) -> str:
    """The companion file: every source name, as a view over the repo's file.

    Paths stay **as the spec records them** — relative to the project root — so the
    generated pipeline runs on a machine other than the one that wrote it. The
    reader and its options come from `core.io`, the one place a reader is named, so
    this file and the engine cannot disagree about which tokens mean null.
    """
    lines = [
        "-- Generated by portia. Do not edit.",
        "-- The sources the models read, as views over this repo's files.",
        f"-- {(when or datetime.now()).isoformat(timespec='seconds')}",
        "",
    ]
    for name, path in sorted(sources.items()):
        lines.append(
            f"CREATE OR REPLACE VIEW {quote_ident(name)} AS\n{read_query(path, absolute=False)};\n"
        )
    return "\n".join(lines)


def write_model(
    results: list[StepResult],
    spec_path: str | Path,
    *,
    layer: str | None = None,
    root: str | Path = ".",
    spec_fingerprint: str = "",
    when: datetime | None = None,
) -> Path:
    """Compile one spec and write it to its place under ``models/``."""
    path = model_path(spec_path, layer=layer, root=root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        compile_spec(
            results,
            name=model_name(spec_path),
            spec_path=spec_path,
            spec_fingerprint=spec_fingerprint,
            when=when,
        )
    )
    return path


def write_sources(sources: dict[str, str], *, root: str | Path = ".", when=None) -> Path:
    """Write the companion sources file. One per project, not one per spec."""
    path = Path(root) / MODELS_DIR / SOURCES_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(compile_sources(sources, when=when))
    return path


def file_fingerprint(path: str | Path) -> str | None:
    """The spec fingerprint a generated file claims, or None if it has no header."""
    p = Path(path)
    if not p.exists():
        return None
    match = _HEADER_FINGERPRINT.search(p.read_text())
    return match.group(1) if match else None


def is_stale(spec_path: str | Path, doc: dict, *, layer: str | None = None, root=".") -> bool:
    """Whether the ``.sql`` on disk no longer matches what the spec would produce.

    A missing file is **not** stale — it was never generated, which is a different
    thing from having drifted, and reporting it as drift would make the warning
    fire on every project that has not compiled yet.
    """
    on_disk = file_fingerprint(model_path(spec_path, layer=layer, root=root))
    return on_disk is not None and on_disk != fingerprint(doc)


def _header(
    name: str,
    *,
    spec_path: str | Path | None,
    spec_fingerprint: str,
    when: datetime | None,
) -> str:
    """What a reader of the file needs, and what makes an edit detectable.

    The "do not edit" is not decoration — it names *why*, because a rule whose
    reason is invisible gets worked around by the next person in a hurry.
    """
    stamp = (when or datetime.now()).isoformat(timespec="seconds")
    lines = [f"-- {name} — generated by portia. Do not edit."]
    if spec_path:
        lines.append(f"-- Change {spec_path} and regenerate; that file holds the")
        lines.append("-- rationale, the expectations and the grain claim this one cannot.")
    lines.append(f"-- generated {stamp}")
    if spec_fingerprint:
        lines.append(f"-- spec fingerprint {spec_fingerprint}")
    return "\n".join(lines) + "\n\n"


def _indent(sql: str, spaces: int = 4) -> str:
    pad = " " * spaces
    return "\n".join(pad + line if line.strip() else line for line in sql.splitlines())

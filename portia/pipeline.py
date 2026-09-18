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
from collections.abc import Callable, Collection
from dataclasses import dataclass
from datetime import datetime
from itertools import count
from pathlib import Path
from typing import Any

import yaml

from portia import catalog, spec
from portia.core import backend, cancel
from portia.core import dialect as dialects
from portia.core.io import connect, source_query
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
    dialect: dialects.Dialect | None = None,
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
    # The deliverable runs where it was written for (`CONNECTOR.md` §2.9), so
    # its names are quoted the way that engine reads them. The process's
    # backend, because a compile holds no connection.
    q = (dialect or backend.active().dialect).quote

    blocks = []
    for r in results:
        if not r.compiled:
            raise ValueError(f"step {r.id!r} ({r.op}) produced no compiled SQL")
        blocks.append(f"{q(r.id)} AS (\n{_indent(r.compiled)}\n)")

    return (
        _header(name, spec_path=spec_path, spec_fingerprint=spec_fingerprint, when=when)
        + f"CREATE TABLE {q(name)} AS\n"
        + "WITH "
        + ",\n".join(blocks)
        + f"\nSELECT * FROM {q(results[-1].id)};\n"
    )


def compile_sources(
    sources: dict[str, Any],
    *,
    when: datetime | None = None,
    dialect: dialects.Dialect | None = None,
) -> str:
    """The companion file: every source name, as a view over what the spec names.

    Paths stay **as the spec records them** — relative to the project root — so the
    generated pipeline runs on a machine other than the one that wrote it. The
    reader and its options come from `core.io`, the one place a reader is named, so
    this file and the engine cannot disagree about which tokens mean null. A source
    that names a **table** (`core/io.TABLE_REF`) becomes a view over that table,
    which is the same line a dbt ``source()`` would resolve to.
    """
    lines = [
        "-- Generated by portia. Do not edit.",
        "-- The sources the models read, as views over what each spec names.",
        f"-- {(when or datetime.now()).isoformat(timespec='seconds')}",
        "",
    ]
    d = dialect or backend.active().dialect
    for name, ref in sorted(sources.items()):
        view = source_query(ref, absolute=False, dialect=d)
        lines.append(f"CREATE OR REPLACE VIEW {d.quote(name)} AS\n{view};\n")
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


@dataclass
class BuiltModel:
    """One model, built: what ran, what came out, and where the SQL landed."""

    name: str
    spec_path: Path
    layer: str | None
    results: list[StepResult]
    sql_path: Path
    #: The qualified table a remote build created (`docs/CONNECTOR.md` §2.7).
    #: ``None`` on a local build, whose deliverable is the ``.sql`` alone.
    written_to: str | None = None

    @property
    def blocking(self) -> list[str]:
        """Zero-conditions any step hit and did not acknowledge."""
        return sorted({flag for r in self.results for flag in r.blocking})

    @property
    def drift(self) -> dict[str, dict]:
        """Every step that diverged from its prediction, by step id."""
        return {r.id: r.drift for r in self.results if r.drift}


@dataclass(frozen=True)
class BuildProgress:
    """Where a build has got to, as it gets there.

    Two levels, because a build has two: the project's models in dependency
    order, and one model's steps. A surface that reported only the outer one
    would say ``1 of 1`` for the whole of a single-spec project, which is the
    common case and the one where the wait is longest.

    **Counts, a name, and nothing derived from them.** No percentage, no
    estimate, no bar: step costs differ by two orders of magnitude within one
    spec — 0.6s and 47.6s sit next to each other in the demo project — so
    anything that implied uniform progress would be a measurement portia did not
    make. `DESIGN.md` → `in-flight` reaches the same rule from the other side:
    the mark is a kind, not a rank, and nothing about it grows.
    """

    #: The model being built, and its place in the run order.
    model: str
    models_done: int
    models_total: int
    #: The step being executed inside it. Empty between models, when the build
    #: has named its next model but not yet entered a step.
    step: str = ""
    steps_done: int = 0
    steps_total: int = 0


def build_project(
    root: str | Path = ".",
    *,
    when: datetime | None = None,
    only: str | Collection[str] | None = None,
    on_progress: Callable[[BuildProgress], None] | None = None,
    stop: cancel.Scope | None = None,
) -> list[BuiltModel]:
    """Run specs in dependency order and write their ``.sql``.

    The project's DAG comes from what the specs say they read — nothing declares
    an order (`spec.run_order`). Models are built in that order, so an upstream is
    always real before a downstream reads it.

    ``only`` narrows it to one model — or several — **and everything they read**,
    which is what "run this spec" means once specs reference each other: a table
    is not built until its inputs are. It is a scope, not a shortcut — every model
    in that set is executed and recompiled exactly as a full build would. Several
    names are one closure and one build order (`spec.upstream_of`), never one run
    per name.

    **The sources file is always written in full**, whatever the scope. It creates
    the names *every* model reads, so writing only the scoped subset would leave
    the rest of the pipeline unable to run — a narrower build must not break a
    file it wasn't asked to touch.

    **Nothing is suppressed.** A model whose step hit a blocking zero is still
    compiled and still returned, carrying its flags; deciding whether to ship a
    pipeline with a known zero in it is the human's call, and a builder that
    silently dropped the file would be making that call in code.

    ``on_progress`` is called with a :class:`BuildProgress` before each model and
    before each of its steps, on whatever thread is building. The app builds on a
    worker, so **marshalling back to the event loop is the caller's job** —
    `ui/engine.build` does it, and this stays a plain synchronous callback so the
    CLI could use one too without an event loop existing.

    ``stop`` makes the build interruptible: it is installed as the ambient scope
    for the whole of it, so every connection opened underneath — including the
    sandbox `ops/sql` opens for the agent's own SQL — can be interrupted by a
    press, and :exc:`cancel.Cancelled` comes out. **A half-built project is left
    exactly as far as it got**, which is the honest state: the models written
    before the press are real, compiled from the specs on disk, and the ones
    after it were never started. Nothing is rolled back because nothing was in
    flight that a rollback would undo.
    """
    root = Path(root)
    with cancel.scope(stop):
        return _build_in_order(root, when=when, only=only, on_progress=on_progress)


def _build_in_order(
    root: Path,
    *,
    when: datetime | None,
    only: str | Collection[str] | None,
    on_progress: Callable[[BuildProgress], None] | None,
) -> list[BuiltModel]:
    """The build itself, with the cancel scope already installed around it."""
    models = spec.discover_specs(root)
    if not models:
        return []

    docs = {name: spec.load_spec(root / path) for name, path in models.items()}
    order = (
        spec.upstream_of(only, models, base_dir=root)
        if only is not None
        else spec.run_order(models, base_dir=root)
    )

    active = backend.active()
    # Every spec's target checked before the first model runs: a build that
    # stops at model three for want of a schema name has spent two models' worth
    # of someone's meter finding out.
    targets = {name: _spec_target(active, name, docs[name], models[name]) for name in order}
    con = connect()
    built: list[BuiltModel] = []

    for done, name in enumerate(order):
        cancel.check()
        doc = docs[name]
        layer = doc.get("layer")
        spec.validate_layer(layer)
        target = targets[name]

        on_step = None
        if on_progress is not None:
            on_progress(BuildProgress(name, done, len(order)))

            def on_step(  # noqa: B023 - called before the next iteration rebinds it
                step_done: int, step_total: int, step_id: str, _model=name, _done=done
            ) -> None:
                on_progress(
                    BuildProgress(_model, _done, len(order), step_id, step_done, step_total)
                )

        results = spec.run_spec(doc, base_dir=root, con=con, models=models, on_step=on_step)
        sql_path = write_model(
            results,
            models[name],
            layer=layer,
            root=root,
            spec_fingerprint=fingerprint(doc),
            when=when,
        )
        written_to = None
        if target is not None and results and results[-1].table is not None:
            cancel.check()
            written_to = _write_into_warehouse(name, results, con, target)
        built.append(BuiltModel(name, models[name], layer, results, sql_path, written_to))
        _index(name, results, root, written_to=written_to, fingerprint=fingerprint(doc))

    sources: dict[str, str] = {}
    for doc in docs.values():
        sources |= doc.get("sources") or {}
    write_sources(sources, root=root, when=when)
    return built


def _index(
    name: str,
    results: list[StepResult],
    root: Path,
    *,
    written_to: str | None = None,
    fingerprint: str | None = None,
) -> None:
    """Record what the table portia just built actually looks like.

    **Building is the only moment this is free.** A model has no file to
    profile, so reaching its table means running its spec — and the run that
    just finished is holding it. Doing it on demand later would re-execute the
    whole spec to measure the same rows (48x the compiled SQL on the AQN
    project), which is why `catalog.index_model` is not offered as a command.

    **Best-effort, and it may not fail a build.** The `.sql` is already written
    and the table is already correct; a profile is a description of it. This is
    the same call `knowledge.sync` makes for the same reason (§6.6) — a
    by-product that cannot be allowed to take the thing it is a by-product of
    down with it.
    """
    table = results[-1].table if results else None
    if table is None:
        return
    try:
        catalog.index_model(
            name,
            table,
            portia_dir=root / catalog.DEFAULT_DIR,
            # Shape only on a warehouse (`docs/CONNECTOR.md` §2.6): "free" here
            # meant no file to re-read, and a scan on a meter is not that.
            metadata_only=backend.is_remote(table.con),
            written_to=written_to,
            fingerprint=fingerprint,
        )
    except Exception as exc:  # noqa: BLE001 - a description must not fail a build
        print(f"note: could not record what {name} looks like: {exc}")


def table_home(target: str, dialect: dialects.Dialect) -> list[str]:
    """``[database, schema]`` a model is created in, spelled as the engine folds them.

    The target is the spec's own (`docs/CONNECTOR.md` §2.7.1): the copilot chose
    it when it recorded the spec's first step, and it may differ from the spec
    next to it. Each part is spelled as the engine folds an unquoted identifier
    and then quoted, so the object is the one the team reaches unquoted and a
    reserved word still works.
    """
    spec.validate_target(target)
    database, schema = target.split(".")
    return [dialect.fold(database), dialect.fold(schema)]


def write_into_warehouse(name: str, results: list[StepResult], *, target: str | None) -> str:
    """Create a model's table where its spec says, for a caller holding a
    finished run — `handlers.record_step` on the hand-off (§2.7.1).

    The connection is the one the results were computed on: the run that just
    measured the table is holding it, and a second session would be a second
    sign-in. Refuses, naming the field, when the spec has no target.
    """
    table = results[-1].table if results else None
    if table is None:
        raise ValueError("nothing to write: the run produced no table")
    if not target:
        raise ValueError(
            f"{name!r} says nowhere to write. A spec on a warehouse names its "
            f"target (DATABASE.SCHEMA): record_step(target=…) sets it on the spec."
        )
    return _write_into_warehouse(name, results, table.con, target)


def _write_into_warehouse(name: str, results: list[StepResult], con: Any, target: str) -> str:
    """``CREATE OR REPLACE TABLE <target>.<name> AS …`` — the remote deliverable.

    `docs/CONNECTOR.md` §2.7, on the user's call: a mart is checked by querying
    the staging and intermediate tables it reads, where they are, so a build has
    to leave them there. The compiled ``.sql`` is untouched by this — it names the
    table bare, as a dbt model does — and the target is the spec's rather than
    something baked into the artifact. The schema is created when it is missing
    (§2.7.1): `CREATE SCHEMA IF NOT EXISTS` is what the role either allows or
    refuses, in its own words.
    """
    table = results[-1].table
    assert table is not None
    dialect = dialects.of(con)
    home = table_home(target, dialect)
    con.execute(f"CREATE SCHEMA IF NOT EXISTS {'.'.join(dialect.quote(p) for p in home)}")
    parts = [*home, dialect.fold(name)]
    qualified = ".".join(dialect.quote(p) for p in parts)
    con.execute(f"CREATE OR REPLACE TABLE {qualified} AS {table.query}")
    return ".".join(parts)


def _spec_target(active: backend.Backend, name: str, doc: dict, path: Path) -> str | None:
    """Where this spec's table goes on a remote build, refused up front when unsaid."""
    if not active.remote:
        return None
    target = doc.get("target")
    spec.validate_target(target)
    if not target:
        raise ValueError(
            f"Nothing was built: {name} ({path}) says nowhere to write. A spec on a "
            f"warehouse names its target (DATABASE.SCHEMA); record_step(target=…) "
            f"sets it, or add `target:` to the file."
        )
    return str(target)


#: The folder inside a saved run that holds the account of it rather than the
#: data. Leading underscore so it sorts above the layer directories and reads as
#: not-a-table — the same convention as `SOURCES_FILE`.
RUN_META_DIR = "_run"

#: How a saved run's folder is named. A timestamp, because what you want from a
#: directory of these is *which run was this*; colons are not portable in
#: filenames. Same format as `spec.REPORT_STAMP`, and deliberately the same
#: string — one run writes both.
RUN_STAMP = "%Y-%m-%dT%H-%M-%S"


def new_run_dir(out_dir: str | Path, *, when: datetime | None = None) -> Path:
    """Pick the folder one save will land in: ``out/<timestamp>/``, never a used one.

    **A run is a folder, not a scattering of files** *(2026-08-16)*. Outputs used
    to overwrite ``out/<model>.csv`` while the account of the run went to a
    separate ``runs/`` at the project root, so the two halves of one result aged
    apart and the CSV you were holding had no way to say which run made it. One
    folder per save keeps them together, and keeps the previous save intact
    instead of replacing it.

    **A taken name gets a suffix rather than being reused.** The stamp is
    second-resolution, so two saves inside one second would otherwise land in one
    folder and the first would be silently overwritten — which is the exact thing
    a folder per save exists to prevent. Rare in a human's hands and not rare at
    all in a test, and "rare" is not a reason to lose someone's output.

    It does not create the directory: the caller resolves the folder **once** and
    hands the same path to everything that writes into it, so the tables and the
    account of them cannot disagree about which run they belong to.
    """
    base = Path(out_dir) / (when or datetime.now()).strftime(RUN_STAMP)
    if not base.exists():
        return base
    for n in count(2):
        candidate = base.with_name(f"{base.name}-{n}")
        if not candidate.exists():
            return candidate
    raise AssertionError("unreachable")  # pragma: no cover


def write_outputs(built: list[BuiltModel], run_folder: str | Path) -> list[Path]:
    """Save every built model's table into one run folder, by layer.

    A build is a set of tables, not one table. ``build_project`` runs a whole
    scope — the project, or one model and everything it reads — and writes the
    ``.sql`` for all of it, so the data it produced belongs to the same scope.

    **The tree mirrors ``models/``**: ``<run folder>/<layer>/<model>.csv``, with no
    subdirectory for a model that declares no layer, exactly as `model_path`
    places the compiled SQL. Two trees describing one project should have one
    shape.

    ``run_folder`` comes from `new_run_dir` and is passed in rather than derived,
    because the run's reports go into the same folder and one of the two working
    it out separately is how they end up in different ones.
    """
    target = Path(run_folder)
    written: list[Path] = []
    for model in built:
        into = target / model.layer if model.layer else target
        written += spec.write_outputs(model.results, into, name=model.name)
    return written


def stale_models(root: str | Path = ".") -> list[str]:
    """Models whose ``.sql`` on disk no longer matches their spec.

    Cheap — it reads a header, it does not run anything — so a surface can ask on
    every render. See :func:`is_stale` for why a missing file is not stale.
    """
    root = Path(root)
    stale = []
    for name, path in spec.discover_specs(root).items():
        doc = spec.load_spec(root / path)
        if is_stale(path, doc, layer=doc.get("layer"), root=root):
            stale.append(name)
    return sorted(stale)


def unread_steps(root: str | Path = ".") -> dict[str, list[str]]:
    """Per spec, the steps its table does not read. **An invariant now, not a fact.**

    `spec.unreachable_steps` computed this all along and the app drew it as an
    `unused` chip, because a step nothing reads arrived honestly: `record_step`
    was append-only and the only way to ask the data a question, so a spec
    collected the questions somebody asked on the way to it. Seven of eleven
    recorded steps in the 2026-08-17 AQN run were unreachable and five were pure
    counting queries.

    Both causes are gone (`docs/FINDINGS.md` §7). Questions go to `query_data`
    and their answers to `findings/`; a superseded attempt is *replaced* rather
    than left behind. So an unread step is no longer something to explain to a
    reader — it is a bug, and `cli/build --check` is where a bug of that shape
    belongs. It also costs something real: an unread step is still compiled into
    the ``.sql`` and still executed on every run, because `run_spec` runs each
    step in order to measure it.

    Cheap: it reads YAML and runs nothing, so `--check` stays a CI-speed command.
    """
    root = Path(root)
    found = {}
    for name, path in spec.discover_specs(root).items():
        unread = spec.unreachable_steps(spec.load_spec(root / path))
        if unread:
            found[name] = unread
    return dict(sorted(found.items()))


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

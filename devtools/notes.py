"""Check the private notes against the code and against their own rules:
    python -m devtools.notes [check|cites|health] [--notes DIR]

**A tool for working on portia, not part of it.** The notes live in `docs/`, a
private repository nested in this folder, so a public clone has none and this
tool says so and stops. It exists because the notes grew to 240,000 words of
history in two months and nothing noticed: a section the code cited was
renumbered, a status said "not merged" for weeks, banners stacked on banners.
`docs/NOTES.md` is the guide it holds them to.

**`cites`** lists every citation of a note in tracked files that does not
resolve: `NOTE.md §2.7` where the note has no heading numbered 2.7, `NOTE.md →
Some heading` where the note never says those words, and any mention of a note
that no longer exists. Code points into the notes about 650 times, and a
pointer to a section that moved is a comment that now lies.

**`health`** states facts about each note: its size against its budget, whether
it opens on a dated Status line, and how many traces of history it still carries
(struck-through text, a second Status, branch and worktree names). It reports
and the person decides; only a broken citation or a note over its budget fails
the run.

`check`, the default, is both.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: Words a note may hold before it is pruned rather than added to. The default
#: fits one subject's live decisions. Each exception says why it is longer, and
#: raising one is a decision made in `docs/NOTES.md`, not here.
WORD_BUDGET = 5_000
WORD_BUDGETS = {
    "DESIGN.md": 9_000,  # the component reference for the whole window
    "BACKLOG_NEXT.md": 6_000,  # every open item, each with its evidence
    "BENCHMARK_EVAL.md": 7_000,  # the method and the bench and trace-viewer reference
    "CONNECTORS.md": 6_500,  # the rules for every warehouse, then one section per warehouse
}
#: `CLAUDE.md` is read at the start of every session, so it is held by lines.
CLAUDE_LINES = 300

#: Files that are not topic notes and carry no Status line: the rulebook, its
#: reasons, the shipped list, and the founding brief, frozen on purpose.
NO_STATUS = {"CLAUDE.md", "CONVENTIONS.md", "CHANGELOG.md", "brief.md"}
#: How far down the Status line may sit (a title and a blank line come first).
STATUS_WITHIN = 6
STATUS = re.compile(r"\*\*Status \d{4}-\d{2}-\d{2}")

#: Traces of history a note should no longer carry. Counted, never judged.
STRUCK = re.compile(r"~~[^~]+~~")
BRANCH = re.compile(r"`(?:feat|fix|test|try|backlog)/[\w./-]+`")

SECTION_REF = r"(?:\.md)?`?\)?\s*(?:,\s*)?§\s*(\d+(?:\.\d+)*[a-z]?)"
ARROW_REF = r"\.md`?\s*(?:→|->)\s*`?([^`\n;:)]{3,60})"
#: The checker and its test quote broken citations on purpose, as examples.
SELF = ("devtools/notes.py", "tests/test_devtools_notes.py")
#: File kinds worth reading for citations.
SUFFIXES = (".py", ".md", ".js", ".css", ".yaml", ".yml", ".toml", ".json", ".txt")


@dataclass
class Note:
    name: str
    text: str
    numbers: set[str] = field(default_factory=set)

    @property
    def lower(self) -> str:
        return self.text.lower()


def load_notes(notes: Path) -> dict[str, Note]:
    """Every note by stem, with the section numbers its headings carry."""
    out = {}
    for path in sorted(notes.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        note = Note(path.stem, text)
        for line in text.splitlines():
            m = re.match(r"#+\s*(?:§\s*)?(\d+(?:\.\d+)*[a-z]?)[.\s]", line)
            if m:
                note.numbers.add(m.group(1))
        out[note.name] = note
    return out


def tracked(root: Path) -> list[str]:
    """The files git tracks under ``root``: the public repo, never `docs/`."""
    out = subprocess.run(
        ["git", "-C", str(root), "ls-files"], capture_output=True, text=True, check=True
    ).stdout.splitlines()
    return [
        f
        for f in out
        if f.endswith(SUFFIXES) and not f.startswith(("docs/", "sandbox/")) and f not in SELF
    ]


def cites(root: Path, notes: dict[str, Note]) -> list[str]:
    """One line per citation in ``root``'s tracked files that does not resolve."""
    names = sorted(notes, key=len, reverse=True)
    known = "|".join(re.escape(n) for n in names)
    section = re.compile(rf"\b({known}){SECTION_REF}")
    arrow = re.compile(rf"\b({known}){ARROW_REF}")
    # A `NAME.md` in capitals that is neither a note nor a file in the repo:
    # a note deleted, merged or misspelt. `AGENTS.md` is Codex's own file.
    mention = re.compile(r"\b([A-Z][A-Z_]{3,})\.md\b")
    files = tracked(root)
    elsewhere = {Path(f).stem for f in files if f.endswith(".md")} | {"AGENTS"}
    bad = []
    for rel in files:
        try:
            lines = (root / rel).read_text(encoding="utf-8").splitlines()
        except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError):
            continue
        for i, line in enumerate(lines, 1):
            where = f"{rel}:{i}"
            for m in section.finditer(line):
                note, num = m.group(1), m.group(2)
                if num not in notes[note].numbers:
                    bad.append(f"{where}: {note}.md §{num}: no heading with that number")
            for m in arrow.finditer(line):
                note = m.group(1)
                head = re.split(r"[,.(]", m.group(2).strip().strip('`*"'))[0]
                probe = " ".join(head.lower().split()[:3])
                if probe and probe not in notes[note].lower:
                    bad.append(f"{where}: {note}.md → {head.strip()}: not in the note")
            for m in mention.finditer(line):
                name = m.group(1)
                if name not in notes and name not in elsewhere:
                    bad.append(f"{where}: {name}.md: no such note")
    return bad


@dataclass
class Health:
    name: str
    words: int
    lines: int
    budget: str
    over: bool
    status: str
    traces: str


def health(notes: dict[str, Note]) -> list[Health]:
    """Facts about each note against `docs/NOTES.md`'s rules."""
    out = []
    for note in notes.values():
        file = f"{note.name}.md"
        words, lines = len(note.text.split()), note.text.count("\n")
        if file == "CLAUDE.md":
            budget, over = f"{CLAUDE_LINES} lines", lines > CLAUDE_LINES
        else:
            limit = WORD_BUDGETS.get(file, WORD_BUDGET)
            budget, over = f"{limit:,} words", words > limit
        if file in NO_STATUS:
            status = "n/a"
        else:
            head = "\n".join(note.text.splitlines()[:STATUS_WITHIN])
            status = "ok" if STATUS.search(head) else "missing"
        n_status = len(STATUS.findall(note.text))
        parts = []
        if n_status > 1:
            parts.append(f"{n_status} Status lines")
        if n := len(STRUCK.findall(note.text)):
            parts.append(f"{n} struck-through")
        if n := len(BRANCH.findall(note.text)):
            parts.append(f"{n} branch or worktree names")
        out.append(Health(file, words, lines, budget, over, status, ", ".join(parts)))
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m devtools.notes", description=__doc__.split("\n\n")[0]
    )
    parser.add_argument("what", nargs="?", default="check", choices=("check", "cites", "health"))
    parser.add_argument(
        "--notes",
        type=Path,
        default=ROOT / "docs",
        help="the notes folder (default: docs/ beside the code)",
    )
    args = parser.parse_args(argv)
    if not args.notes.is_dir() or not any(args.notes.glob("*.md")):
        print(
            f"No notes at {args.notes}: the notes are a private repository a public "
            "clone does not have."
        )
        return 0
    notes = load_notes(args.notes)
    failed = False
    if args.what in ("check", "health"):
        rows = health(notes)
        width = max(len(r.name) for r in rows)
        print(f"{'note':{width}}  {'words':>6}  {'budget':>13}  status   history left")
        for r in sorted(rows, key=lambda r: -r.words):
            mark = "  OVER" if r.over else ""
            print(
                f"{r.name:{width}}  {r.words:>6,}  {r.budget:>13}  {r.status:7}  "
                f"{r.traces or '-'}{mark}"
            )
        failed |= any(r.over for r in rows)
    if args.what in ("check", "cites"):
        bad = cites(ROOT, notes)
        print(f"\n{len(bad)} citation(s) in the code that do not resolve" + (":" if bad else "."))
        for line in bad:
            print(f"  {line}")
        failed |= bool(bad)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

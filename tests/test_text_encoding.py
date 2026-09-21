"""Every text file portia reads or writes names its encoding.

Python picks the machine's default when none is named: UTF-8 on a Mac, cp1252
on Windows. The first Windows tester got a 500 on every page, because the
stylesheet holds one `←` and cp1252 has no character for its third byte. The
loud half is the small half: most bytes do decode under cp1252, into the wrong
characters, so the prompts reached the model garbled and said nothing, and a
compiled `.sql` written there is one a Mac cannot read back.

Nothing on a Mac can notice a missing encoding, so this reads the source.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
#: Shipped code, the tools that write files a project keeps, and the tests:
#: a test that reads back what portia wrote has to read it the way portia wrote
#: it, or the suite fails on the one machine where the rule matters.
CHECKED = ("portia", "devtools", "tests")


def _mode(call: ast.Call, position: int) -> str | None:
    """The `mode` a call to open passes, if it is written as a literal."""
    given = [k.value for k in call.keywords if k.arg == "mode"]
    if not given and len(call.args) > position:
        given = [call.args[position]]
    if given and isinstance(given[0], ast.Constant) and isinstance(given[0].value, str):
        return given[0].value
    return None


def _names_no_encoding(call: ast.Call) -> bool:
    """Whether this call opens text and leaves the encoding to the machine.

    `read_text` and `write_text` take the encoding as their next positional, so
    a call passing it that way counts as naming it. That also lets through
    `engine.read_text(path)`, portia's own function, which takes a path there.
    A method called `open` is only a file's when it is handed a mode: a dialog
    has one too, and it takes nothing.
    """
    if any(k.arg == "encoding" for k in call.keywords):
        return False
    func = call.func
    if isinstance(func, ast.Attribute):
        if func.attr == "read_text":
            return not call.args
        if func.attr == "write_text":
            return len(call.args) < 2
        if func.attr == "open":
            mode = _mode(call, 0)
            return mode is not None and "b" not in mode
        return False
    if isinstance(func, ast.Name) and func.id == "open":
        return "b" not in (_mode(call, 1) or "r")
    return False


def unnamed(source: str) -> list[int]:
    """Line numbers of the text reads and writes in `source` naming no encoding."""
    calls = (node for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Call))
    return sorted(call.lineno for call in calls if _names_no_encoding(call))


def test_every_text_read_and_write_names_its_encoding():
    found = [
        f"{path.relative_to(ROOT)}:{line}"
        for folder in CHECKED
        for path in sorted((ROOT / folder).rglob("*.py"))
        for line in unnamed(path.read_text(encoding="utf-8"))
    ]
    assert not found, 'add encoding="utf-8" to: ' + ", ".join(found)


@pytest.mark.parametrize(
    "source",
    [
        "p.read_text()",
        'p.read_text(errors="replace")',
        "p.write_text(body)",
        "open(p)",
        'open(p, "w")',
        'open(p, mode="a")',
        'p.open("w")',
    ],
)
def test_the_check_catches(source):
    assert unnamed(source) == [1]


@pytest.mark.parametrize(
    "source",
    [
        'p.read_text(encoding="utf-8")',
        'p.read_text("utf-8")',
        'p.write_text(body, encoding="utf-8")',
        'open(p, "w", encoding="utf-8")',
        'open(p, "rb")',
        'p.open("ab")',
        "dialog.open()",
        "engine.read_text(path)",
    ],
)
def test_the_check_lets_through(source):
    assert unnamed(source) == []


#: `é` as cp1252 writes it: one byte, and not a whole character in UTF-8.
NOT_UTF8 = "host=café.example\n".encode("cp1252")


def test_a_vendors_file_portia_cannot_decode_is_no_suggestion_and_never_a_crash(tmp_path):
    from portia.connectors import registry

    toml = tmp_path / "connections.toml"
    toml.write_bytes(b"[prod]\n" + NOT_UTF8)
    assert registry.snowflake_suggestions(toml) == []

    services = tmp_path / "pg_service.conf"
    services.write_bytes(b"[warehouse]\n" + NOT_UTF8)
    assert registry.postgres_suggestions(services) == []

    (tmp_path / "active_config").write_bytes(NOT_UTF8)
    assert registry.gcloud_default_project(tmp_path) is None


def test_a_compiled_model_is_utf8_on_disk_whatever_the_machine(tmp_path):
    from portia import pipeline

    path = pipeline.write_sources({"région": "data/région.csv"}, root=tmp_path)
    assert "région" in path.read_bytes().decode("utf-8")

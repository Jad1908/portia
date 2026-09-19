"""`portia <command>` — one name on the PATH for every command-line edge.

Every edge here is `python -m portia.cli.<name>`, which is right inside a
checkout and useless outside one: somebody who installed portia as a tool, to use
it from Claude Code in their own data repository (`docs/HEADLESS.md`), has
`portia-mcp` and `portia-hook` on their PATH and no interpreter that can import
`portia`. The skill told their model to run `uv run python -m portia.ui`, which
fails there. So there is one more script, and it is only a dispatcher:

    portia ui --project .          the window
    portia connect suggest         any module under `portia/cli/`
    portia index data/ --no-interpret

Nothing is decided here. The name after `portia` is a module, its own `main`
parses the rest, and `python -m portia.cli.<name>` stays exactly what it was.
"""

from __future__ import annotations

import importlib
import pkgutil
import sys

#: The one command that is not a module of this package.
WINDOW = "ui"

#: Edges a person never types: the host starts them (`plugin/`).
_HOSTED = ("serve", "hook")


def commands() -> list[str]:
    """Every command, the window first and the rest as the package lists them."""
    import portia.cli as package

    found = sorted(m.name for m in pkgutil.iter_modules(package.__path__))
    return [WINDOW, *(n for n in found if not n.startswith("_") and n not in _HOSTED)]


def main() -> None:
    known = commands()
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print("usage: portia <command> [options]\n\ncommands: " + ", ".join(known))
        print("\n`portia <command> --help` says what each one takes.")
        raise SystemExit(0 if len(sys.argv) > 1 else 2)
    name = sys.argv[1]
    if name not in known:
        raise SystemExit(f"portia: no command {name!r}. One of: {', '.join(known)}")
    module = "portia.ui.__main__" if name == WINDOW else f"portia.cli.{name}"
    # The command's own parser reads `sys.argv`, and its usage line should name
    # what was typed.
    sys.argv = [f"portia {name}", *sys.argv[2:]]
    importlib.import_module(module).main()


if __name__ == "__main__":
    main()

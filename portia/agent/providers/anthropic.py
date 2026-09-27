"""Anthropic: the provider the loop was built on, and the one that sets nothing.

The SDK's binary resolves the account itself, from whatever is in the user's
environment, and portia neither reads nor sets an auth variable
(`docs/PLAN.md` → Auth posture). So :meth:`env` is empty on purpose, and stays
empty: this module is the statement that the default path is unchanged by the
providers seam existing.

The model list is a convenience for a picker and never a validation set
(`session.MODELS`'s old comment, kept), and a preflight passes with no facts
because the one thing that could be checked, whether the account is good, is
the binary's to find out when the chat starts. **The status reads what the
binary says about itself** *(2026-09-22, for the providers dashboard,
`docs/PROVIDERS.md` §9.5)*: ``claude --version`` and ``claude auth status``,
which is the binary's own report of whether it is signed in and how. Reading
it is not setting anything, so the auth posture is unchanged: portia still
writes no variable and holds no credential.
"""

from __future__ import annotations

import importlib.util
import json
import platform
import subprocess
from collections.abc import Callable
from pathlib import Path

from portia.agent.providers import (
    Model,
    Preflight,
    Program,
    Provider,
    ProviderUnavailable,
    Status,
    choose_program,
    machine_binary,
    remembered_version,
)

#: The model is a config knob, never a hard dependency (`docs/PLAN.md`).
#: Sonnet 5, a current model, since 2026-09-23 (the user's call): Haiku 4.5 is
#: on Claude Code's legacy list now, and a default inside the fold opened the
#: picker on its legacy models every time. Developing on a small model is
#: still a choice in the picker, one press away.
DEFAULT_MODEL = "claude-sonnet-5"

#: Every model Claude Code's own ``/model`` picker offers, in its order and
#: split as it splits them: four current, then the legacy ones it folds away
#: (`docs/PROVIDERS.md` §5.1). Read off Claude Code 2.1.280, the binary
#: ``claude-agent-sdk`` 0.2.158 bundles; a model newer than the lock is one a
#: picker can still be typed into, because ``--model`` takes anything the SDK
#: accepts and this list is a convenience, never a validation set.
CATALOG = (
    Model("claude-opus-5-5", label="Claude Opus 5.5"),
    Model("claude-fable-5-1", label="Claude Fable 5.1"),
    Model("claude-opus-5", label="Claude Opus 5"),
    Model("claude-sonnet-5", label="Claude Sonnet 5"),
    Model("claude-fable-5", label="Claude Fable 5", legacy=True),
    Model("claude-opus-4-8", label="Claude Opus 4.8", legacy=True),
    Model("claude-opus-4-7", label="Claude Opus 4.7", legacy=True),
    Model("claude-opus-4-6", label="Claude Opus 4.6", legacy=True),
    Model("claude-opus-4-5", label="Claude Opus 4.5", legacy=True),
    Model("claude-sonnet-4-6", label="Claude Sonnet 4.6", legacy=True),
    Model("claude-haiku-4-5", label="Claude Haiku 4.5", legacy=True),
)
MODELS = tuple(m.name for m in CATALOG)


QUICK_TIMEOUT = 10.0
INSTALL_REMEDY = (
    "Install Claude Code, or `pip install 'portia[agent]'`, which bundles a copy of it."
)
SIGN_IN_REMEDY = "Sign in with `claude` in a terminal; the copilot uses that account."


def bundled_binary() -> str | None:
    """The Claude Code binary the SDK ships, found without importing the SDK."""
    spec = importlib.util.find_spec("claude_agent_sdk")
    if spec is None or not spec.submodule_search_locations:
        return None
    name = "claude.exe" if platform.system() == "Windows" else "claude"
    for root in spec.submodule_search_locations:
        candidate = Path(root) / "_bundled" / name
        if candidate.is_file():
            return str(candidate)
    return None


def _run_at(program: str, *args: str) -> tuple[int, str]:
    """One command of ``program``; the code and what it printed."""
    try:
        done = subprocess.run(
            [program, *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=QUICK_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProviderUnavailable(f"`claude {' '.join(args)}` did not answer: {exc}") from exc
    return done.returncode, (done.stdout or done.stderr or "").strip()


def _printed_version(program: str) -> str:
    """The first line ``program --version`` prints."""
    _, out = _run_at(program, "--version")
    return out.splitlines()[0] if out else ""


def program() -> Program | None:
    """The Claude Code that runs, by `providers.choose_program`'s rule (`docs/PROVIDERS.md` §4.10).

    The settings' path, else the machine's own when it is at least as new as
    the bundled one, else the bundled one. The one program for every provider
    on this harness: Ollama's and llama.cpp's chats run it too, and the path
    is configured here, on the provider that *is* the harness.
    """
    return choose_program(
        configured=PROVIDER.settings().binary,
        machine=machine_binary("claude"),
        bundled=bundled_binary(),
        printed=lambda path: remembered_version(path, _printed_version),
    )


def binary() -> str | None:
    """The path of the ``claude`` that runs (:func:`program`), ``None`` where there is none."""
    found = program()
    return found.path if found else None


def _run(*args: str) -> tuple[int, str]:
    found = binary()
    if found is None:
        raise ProviderUnavailable("Claude Code is not installed.")
    return _run_at(found, *args)


def version() -> str:
    """What ``claude --version`` prints, e.g. ``2.1.280 (Claude Code)``."""
    found = program()
    if found is None:
        raise ProviderUnavailable("Claude Code is not installed.")
    return found.version or _printed_version(found.path)


def signed_in() -> tuple[bool | None, str]:
    """The binary's own sign-in report: whether, and how, with no email in it.

    ``claude auth status`` prints JSON with the method and the plan; an older
    binary without the command reports *not measured* rather than *signed out*.
    """
    code, out = _run("auth", "status")
    try:
        report = json.loads(out)
    except ValueError:
        return None, ""
    if not isinstance(report, dict):
        return None, ""
    logged = report.get("loggedIn")
    if not logged:
        return False, "not signed in"
    parts = [str(report.get("authMethod") or ""), str(report.get("subscriptionType") or "")]
    return True, "signed in" + (" · " + " ".join(p for p in parts if p) if any(parts) else "")


class Anthropic(Provider):
    kind = "anthropic"
    label = "Anthropic"
    default_model = DEFAULT_MODEL
    honours_effort = True
    #: Every model in `MODELS` takes images.
    sees_images = True
    static_models = CATALOG
    runtime_fields = ("binary",)
    start_remedy = SIGN_IN_REMEDY

    def env(self) -> dict[str, str]:
        return {}

    def status(self) -> Status:
        found = program()
        if found is None:
            return Status(
                reachable=False, detail="Claude Code is not installed.", remedy=INSTALL_REMEDY
            )
        try:
            build = version()
            ok, words = signed_in()
        except ProviderUnavailable as exc:
            return Status(reachable=False, detail=str(exc), remedy=INSTALL_REMEDY)
        if ok is None:
            return Status(
                reachable=None, detail="sign-in not measured", version=build, program=found
            )
        if not ok:
            return Status(
                reachable=False, detail=words, version=build, remedy=SIGN_IN_REMEDY, program=found
            )
        return Status(reachable=True, detail=words, version=build, account=words, program=found)

    def models(self) -> list[Model]:
        return list(self.static_models)

    def preflight(self, model: str, *, prompt_chars: Callable[[], int]) -> Preflight:
        return Preflight(ok=True)


PROVIDER = Anthropic()

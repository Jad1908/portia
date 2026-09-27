"""Codex: OpenAI's harness beside the Claude one (`docs/PROVIDERS.md` §9).

Every other provider is an environment for the Claude Agent SDK's binary. This
one is a second **harness**: Codex's ``app-server``, driven by `agent/codex.py`
over the ``openai-codex`` SDK, with portia's tools served to it over a loopback
MCP connection. What this module decides is the same three things the others
do, where the binary is, which models it offers and what can be measured before
a message goes, plus the one thing a second harness adds: the configuration it
is started with, which is where the copilot's non-negotiables go on this route
(:func:`overrides`).

**The home is portia's, and the sign-in is the user's.** Codex reads
``$CODEX_HOME/config.toml`` and ``$CODEX_HOME/AGENTS.md`` on every thread, and
a dotted ``--config`` override merges with the file rather than replacing it:
measured on 2026-09-22, ``mcp_servers={}`` left a server named in the user's
own file in the copilot's tool list, and the global ``AGENTS.md`` reached the
model whatever ``project_doc_max_bytes`` said. That is the 2026-09-04 Google
Drive leak by another door. So the binary is started with :data:`HOME`, a
directory portia owns and keeps empty of instructions, and the one file the
user's own home holds that portia needs, ``auth.json``, is linked into it
(:func:`home`). Signing in stays ``codex login`` in a terminal, shown and never
run, the way a model is pulled.

**A local model goes through the same harness.** With ``OPENAI_BASE_URL`` in
this provider's variables, Codex is pointed at that server as a custom model
provider and no sign-in is needed. Ollama's Responses endpoint keeps the
namespaced tool list Codex sends (measured with ``qwen3:0.6b``); llama.cpp's
drops it with a warning, so through Codex the local server is Ollama, and
`docs/PROVIDERS.md` §9.3 records why. Fit is still measured by loading: when the
base URL is Ollama's, the preflight is `ollama.Ollama.preflight`.

Nothing here imports the SDK at module level, like every provider, so the
picker lists and checks without the ``codex`` extra installed.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from portia.agent.providers import (
    CODEX,
    Model,
    Preflight,
    Program,
    Provider,
    ProviderUnavailable,
    Status,
    choose_program,
    machine_binary,
    remembered_version,
    rough_tokens,
)

#: Where ``codex login`` writes the sign-in, and the one file read out of it.
LOGIN_HOME = Path.home() / ".codex"
AUTH_FILE = "auth.json"
#: The ``CODEX_HOME`` the binary is started with: portia's, so nothing the
#: user configured for their own Codex reaches the copilot (module docstring).
HOME = Path.home() / ".config" / "portia" / "codex"

#: The two variables this provider reads out of its settings. Both are the
#: names OpenAI's own tools use, so a person who has them set elsewhere types
#: nothing new. The base URL is read by portia and turned into a custom model
#: provider (:func:`overrides`); the key is handed to the binary as it is.
BASE_URL_VAR = "OPENAI_BASE_URL"
API_KEY_VAR = "OPENAI_API_KEY"
#: The name of that custom provider in Codex's configuration.
LOCAL_PROVIDER = "portia"
#: The name of portia's MCP server as Codex sees it, and the namespace prefix
#: the model reads. `events.TOOL_PREFIX` is the same string with the SDK's
#: double underscore, so `events.tool_label` strips both.
SERVER_NAME = "portia"

#: A stand-in for the model name while nothing has been listed on an account:
#: Codex picks its own default when the thread is started with no model.
ACCOUNT_DEFAULT = "account default"

#: Every model Codex's own picker lists, in its order, split the way T3 Code's
#: splits them: the current family first, the rest folded under *legacy*
#: (`docs/PROVIDERS.md` §5.1). Read off the model list compiled into
#: ``codex-cli`` 0.156.1, the binary ``openai-codex`` 0.156.1 pins; the entries
#: that binary hides (the Daybreak pair, GPT-5.4, its own review model) are not
#: here, as they are not in its picker. On an account the list is the
#: account's (``model/list``) and this only says which of those are legacy.
CATALOG = (
    Model("gpt-6-astra", label="GPT-6 Astra"),
    Model("gpt-6-sol", label="GPT-6 Sol"),
    Model("gpt-6-luna", label="GPT-6 Luna"),
    Model("gpt-5.6-sol", label="GPT-5.6 Sol", legacy=True),
    Model("gpt-5.6-terra", label="GPT-5.6 Terra", legacy=True),
    Model("gpt-5.6-luna", label="GPT-5.6 Luna", legacy=True),
    Model("gpt-5.5", label="GPT-5.5", legacy=True),
)
LEGACY = frozenset(m.name for m in CATALOG if m.legacy)
_LABELS = {m.name: m.label for m in CATALOG}

#: How long Codex waits on one tool call. A tool that asks the human a
#: question waits as long as they take; 70 s passed at the server's default on
#: 2026-09-22, and this makes the ceiling a day rather than a guess.
TOOL_TIMEOUT_SEC = 24 * 3600
QUICK_TIMEOUT = 10.0

#: What the binary's own default model is called in its strings, drawn as a
#: hint beside the placeholder and never sent: the account decides.
INSTALL_REMEDY = "Install it with `pip install 'portia[codex]'`, or `npm install -g @openai/codex`."
SIGN_IN_REMEDY = (
    "Sign in with `codex login` in a terminal, or set OPENAI_BASE_URL to a local server."
)

#: The model names last listed, newest listing wins (`llamacpp._served`'s shape),
#: with the account's default first.
_listed: list[str] = []


def bundled_binary() -> str | None:
    """The binary the ``openai-codex`` package pins, without importing it."""
    spec = importlib.util.find_spec("codex_cli_bin")
    if spec is None or not spec.submodule_search_locations:
        return None
    for root in spec.submodule_search_locations:
        for name in ("codex", "codex.exe"):
            candidate = Path(root) / "bin" / name
            if candidate.is_file():
                return str(candidate)
    return None


def _run_at(
    program: str, *args: str, timeout: float = QUICK_TIMEOUT, env: dict[str, str] | None = None
) -> tuple[int, str]:
    """One command of ``program``; the code and what it printed."""
    try:
        done = subprocess.run(
            [program, *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
            env=env,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProviderUnavailable(f"`codex {' '.join(args)}` did not answer: {exc}") from exc
    return done.returncode, (done.stdout or done.stderr or "").strip()


def _printed_version(program: str) -> str:
    """The first line ``program --version`` prints, under no home: a version needs none."""
    _, out = _run_at(program, "--version")
    return out.splitlines()[0] if out else ""


def program() -> Program | None:
    """The ``codex`` that runs, by `providers.choose_program`'s rule (`docs/PROVIDERS.md` §4.10).

    The settings' path, else the machine's own when it is at least as new as
    the bundled one, else the bundled one. Which *program* runs is the user's;
    the home it runs under stays portia's (:func:`home`), for the module
    docstring's reason.
    """
    return choose_program(
        configured=PROVIDER.settings().binary,
        machine=machine_binary("codex"),
        bundled=bundled_binary(),
        printed=lambda path: remembered_version(path, _printed_version),
    )


def binary() -> str | None:
    """The path of the ``codex`` that runs (:func:`program`), ``None`` where there is none."""
    found = program()
    return found.path if found else None


def login_home() -> Path:
    """Where the sign-in lives: the settings' ``home``, else Codex's own."""
    configured = PROVIDER.settings().home.strip()
    return Path(os.path.expanduser(configured)) if configured else LOGIN_HOME


def home() -> Path:
    """Portia's ``CODEX_HOME``, created on first use with the user's sign-in linked in.

    A link rather than a copy, so a sign-in made or refreshed in a terminal is
    read here without anybody copying anything. A real file already there is
    left alone: it means somebody signed this home in directly.
    """
    HOME.mkdir(parents=True, exist_ok=True)
    link = HOME / AUTH_FILE
    target = login_home() / AUTH_FILE
    if link.is_symlink() and os.readlink(link) != str(target):
        link.unlink()
    if not link.is_symlink() and not link.exists():
        try:
            link.symlink_to(target)
        except OSError:
            pass
    return HOME


def base_url() -> str:
    """A local OpenAI-compatible server to route through, or ``""`` for the account."""
    return PROVIDER.settings().env.get(BASE_URL_VAR, "").strip().rstrip("/")


def process_env() -> dict[str, str]:
    """The variables the binary is started with, on top of the process's own."""
    env = {k: v for k, v in PROVIDER.settings().env.items() if k != BASE_URL_VAR}
    env["CODEX_HOME"] = str(home())
    return env


def _run(*args: str, timeout: float = QUICK_TIMEOUT) -> tuple[int, str]:
    """One ``codex`` command, under portia's home; the code and what it printed."""
    found = binary()
    if found is None:
        raise ProviderUnavailable("Codex is not installed.")
    return _run_at(found, *args, timeout=timeout, env={**os.environ, **process_env()})


def version() -> str:
    """What ``codex --version`` prints, e.g. ``codex-cli 0.156.0``."""
    found = program()
    if found is None:
        raise ProviderUnavailable("Codex is not installed.")
    return found.version or _printed_version(found.path)


def signed_in() -> tuple[bool, str]:
    """Whether portia's home holds a sign-in, in ``codex login status``'s own words."""
    code, out = _run("login", "status")
    return code == 0, out.splitlines()[0] if out else ""


def is_ollama(url: str) -> bool:
    """Whether ``url`` is this machine's Ollama, so its preflight can be reused."""
    from portia.agent.providers import ollama

    return url.rstrip("/").removesuffix("/v1") == ollama.host()


def overrides(mcp_url: str, *, model_context_window: int | None = None) -> tuple[str, ...]:
    """The ``--config`` lines the copilot's non-negotiables become on this harness.

    Each one was measured on 2026-09-22 against ``codex-cli 0.156.0`` by
    pointing the binary at a server that logs what it receives
    (`docs/PROVIDERS.md` §9.2). In order: portia's tools and nothing else the
    binary would offer (the shell, patching, images, the web, sub-agents,
    goals, skills, and ``request_user_input``, which is listed and then refused
    outside plan mode, so a model that reached for it stopped); reads run
    freely and writes stop, which is Codex's own approval flow keyed off the
    ``readOnlyHint`` every read tool already carries; and the sandbox stays
    read-only under a copilot that has no file tool to use it with anyway.
    """
    lines = [
        f'mcp_servers.{SERVER_NAME}.url="{mcp_url}"',
        f"mcp_servers.{SERVER_NAME}.tool_timeout_sec={TOOL_TIMEOUT_SEC}",
        'approval_policy="on-request"',
        'sandbox_mode="read-only"',
        "features.shell_tool=false",
        "features.view_image=false",
        "features.image_generation=false",
        "features.multi_agent=false",
        "features.goals=false",
        "features.apps=false",
        "features.skill_search=false",
        "features.memories=false",
        'web_search="disabled"',
        "tools.experimental_request_user_input.enabled=false",
        "skills.include_instructions=false",
        "project_doc_max_bytes=0",
        "include_environment_context=false",
        "include_permissions_instructions=false",
        "include_apps_instructions=false",
    ]
    url = base_url()
    if url:
        lines += [
            f'model_providers.{LOCAL_PROVIDER}.name="{LOCAL_PROVIDER}"',
            f'model_providers.{LOCAL_PROVIDER}.base_url="{url}"',
            f'model_providers.{LOCAL_PROVIDER}.wire_api="responses"',
            f'model_provider="{LOCAL_PROVIDER}"',
        ]
        if API_KEY_VAR in PROVIDER.settings().env:
            lines.append(f'model_providers.{LOCAL_PROVIDER}.env_key="{API_KEY_VAR}"')
    if model_context_window:
        lines.append(f"model_context_window={int(model_context_window)}")
    return tuple(lines)


def _local_models(url: str) -> list[Model]:
    """``GET <base_url>/models``, the list every OpenAI-compatible server serves."""
    req = urllib.request.Request(f"{url}/models", headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=QUICK_TIMEOUT) as response:
            payload = json.loads(response.read() or b"{}")
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        raise ProviderUnavailable(f"nothing answers at {url} ({exc}).") from exc
    entries = payload.get("data") if isinstance(payload, dict) else None
    return sorted(
        (Model(str(e.get("id"))) for e in entries or [] if isinstance(e, dict) and e.get("id")),
        key=lambda m: m.name,
    )


def _account_models() -> list[Model]:
    """The account's list, off ``model/list``; the default first."""
    try:
        from openai_codex import Codex, CodexConfig
    except ImportError as exc:
        raise ProviderUnavailable(
            "the `codex` extra is not installed: pip install 'portia[codex]'"
        ) from exc
    config = CodexConfig(codex_bin=binary(), env=process_env())
    try:
        with Codex(config) as codex:
            listed = codex.models().data
    except Exception as exc:  # noqa: BLE001 - the SDK's own words are the reason
        raise ProviderUnavailable(str(exc).splitlines()[0] if str(exc) else repr(exc)) from exc
    models = [
        Model(
            m.model,
            detail=m.description or "",
            label=_LABELS.get(m.model) or m.display_name or "",
            legacy=m.model in LEGACY,
        )
        for m in listed
        if not m.hidden
    ]
    defaults = [m.model for m in listed if m.is_default]
    models.sort(key=lambda m: (m.name not in defaults, m.name))
    return models


class Codex(Provider):
    kind = "codex"
    label = "Codex"
    harness = CODEX
    honours_effort = True
    #: Whether an image inside an MCP tool result reaches the model through
    #: Codex is unmeasured, so `view_chart` is not offered (the local
    #: providers' reason, `docs/VISUALIZATION.md` §12.6).
    sees_images = False
    #: Codex reports tokens and never a price; a ChatGPT plan has none per
    #: token, and a number priced as if it were Claude's would be about nothing.
    metered = False
    runtime_fields = ("binary", "home")
    start_remedy = SIGN_IN_REMEDY

    @property
    def static_models(self) -> tuple[Model, ...]:  # type: ignore[override]
        """The catalog on an account, nothing on a local server, whose list is its own."""
        return () if base_url() else CATALOG

    @property
    def default_model(self) -> str:  # type: ignore[override]
        if _listed:
            return _listed[0]
        return ACCOUNT_DEFAULT

    def env(self) -> dict[str, str]:
        return process_env()

    def env_notes(self) -> dict[str, str]:
        return {
            BASE_URL_VAR: "An OpenAI-compatible server to run on instead of the account, "
            "e.g. Ollama's http://127.0.0.1:11434/v1. No sign-in is needed with it set.",
            API_KEY_VAR: "An API key, for an account signed in that way rather than through "
            "`codex login`. Stored in the clear in providers.yaml.",
        }

    def status(self) -> Status:
        found = program()
        if found is None:
            return Status(reachable=False, detail="Codex is not installed.", remedy=INSTALL_REMEDY)
        try:
            build = version()
        except ProviderUnavailable as exc:
            return Status(reachable=False, detail=str(exc), remedy=INSTALL_REMEDY)
        url = base_url()
        if url:
            return Status(
                reachable=True,
                detail=f"routed to {url}",
                version=build,
                account="local server",
                program=found,
            )
        try:
            ok, words = signed_in()
        except ProviderUnavailable as exc:
            return Status(reachable=False, detail=str(exc), version=build, program=found)
        if not ok:
            return Status(
                reachable=False,
                detail=words or "Not signed in.",
                version=build,
                remedy=SIGN_IN_REMEDY,
                program=found,
            )
        return Status(reachable=True, detail=words, version=build, account=words, program=found)

    def models(self) -> list[Model]:
        url = base_url()
        models = _local_models(url) if url else _account_models()
        _listed[:] = [m.name for m in models]
        return models

    def preflight(self, model: str, *, prompt_chars: Callable[[], int]) -> Preflight:
        """What can be measured before a message goes: the binary, the route, and a local model's fit."""
        found = program()
        chars = int(prompt_chars())
        facts: dict[str, Any] = {
            "binary": found.path if found else None,
            "prompt_chars": chars,
            "prompt_tokens_about": rough_tokens(chars),
        }
        if found is None:
            return Preflight(False, facts, reason="Codex is not installed.", remedy=INSTALL_REMEDY)
        facts["origin"] = found.origin
        url = base_url()
        if url:
            facts["base_url"] = url
            if is_ollama(url):
                # Ollama loads the model and says what it loaded it with; the
                # refusal and its remedy are that provider's, measured there.
                from portia.agent.providers import ollama

                inner = ollama.PROVIDER.preflight(model, prompt_chars=lambda: chars)
                return Preflight(inner.ok, {**facts, **inner.facts}, inner.reason, inner.remedy)
            return Preflight(True, facts)
        try:
            ok, words = signed_in()
        except ProviderUnavailable as exc:
            return Preflight(False, facts, reason=str(exc), remedy=INSTALL_REMEDY)
        facts["signed_in"] = ok
        if not ok:
            return Preflight(False, facts, reason=words or "Not signed in.", remedy=SIGN_IN_REMEDY)
        return Preflight(True, facts)


PROVIDER = Codex()

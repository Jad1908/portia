"""Ollama: a model served from this machine, behind the same loop.

Ollama 0.14 and later speak the Anthropic Messages API, and the SDK's binary
honours ``ANTHROPIC_BASE_URL``. So the whole of the switch is two variables
in the binary's environment (:meth:`env`), and everything portia relies on in
the loop is untouched: the permission callback, the question tool, the Stop
hook, interrupt and resume all live in the binary, not in the API it calls.
`docs/PROVIDERS.md` §2 is the argument for taking that route first, and §3 is
what it does not give: the effort knob is accepted and ignored, there is no
prompt cache, and the binary's own system prompt rides on top of portia's.

**What this module measures, and why it has to.** A remote API either answers
or refuses. A local server does two things a remote one never does, and both
are silent from the client's side:

- it **refuses to load** a model that does not fit beside what is already in
  memory, with a message the binary turns into a generic failure three
  retries later;
- it serves a model with **a context window the server chose**, 4,096 tokens
  by default on a machine with under 24 GB, and truncates the front of any
  prompt that does not fit. The front of every portia prompt is `copilot.md`.

:meth:`preflight` asks the server to load the model (an empty ``generate``
does exactly that and nothing else), then reads back from ``/api/ps`` what it
loaded with, and refuses the send with the server's own words when the model
did not load or the instructions will not fit. The facts it read are returned
either way, so a picker can draw them beside the name.

**Portia never manages weights.** A model is added with ``ollama pull``, in a
terminal, and :meth:`add_command` says so. A pull is a multi-gigabyte download
with its own progress, its own cancellation and its own failures, Ollama's own
tool already does all three, and the library it pulls from has no API to
browse. Same argument as never converting a CSV to Parquet: a tool that manages
somebody's model files is not a data-harmonization concern (§4).

Every network call goes through :func:`_request`, which a test replaces.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

from portia.agent.providers import (
    BASE_URL_VAR,
    TOKEN_VAR,
    Model,
    Preflight,
    Provider,
    ProviderUnavailable,
    Status,
    machine_memory,
    rough_tokens,
)

#: Ollama's own variable for where its server is, read the way its CLI reads
#: it. The default is the server's default, so a stock install needs nothing.
HOST_VAR = "OLLAMA_HOST"
DEFAULT_HOST = "http://127.0.0.1:11434"

#: The token the SDK's binary requires and Ollama ignores, which is what
#: Ollama's own documentation says to set. It is a fixed string with no secret
#: in it (`docs/PLAN.md` → Auth posture, revised 2026-09-08). The two variable
#: names are the package's (`providers.BASE_URL_VAR`, `providers.TOKEN_VAR`),
#: shared with every local provider.
TOKEN = "ollama"

#: How long a loaded model stays in memory after a preflight, so the first
#: message finds it there. Ollama's own default is five minutes; a little more
#: covers a person reading the composer before pressing Send.
KEEP_ALIVE = "10m"

#: Loading a model reads gigabytes off disk; listing them does not.
QUICK_TIMEOUT = 3.0
LOAD_TIMEOUT = 180.0

#: The first model a stock Ollama can run the loop on. Small enough for a
#: 16 GB machine beside DuckDB, and it calls tools.
DEFAULT_MODEL = "qwen3:8b"


def host() -> str:
    """The server's URL, from `HOST_VAR` the way Ollama's own CLI reads it."""
    raw = os.environ.get(HOST_VAR, "").strip() or DEFAULT_HOST
    if "://" not in raw:
        raw = f"http://{raw}"
    return raw.rstrip("/")


def _request(path: str, body: dict | None = None, *, timeout: float = QUICK_TIMEOUT) -> Any:
    """One HTTP call to the server. GET when ``body`` is ``None``, else a JSON POST.

    The one function a test replaces. Raises `ProviderUnavailable` when the
    server cannot be reached, and `OllamaError` with the server's own message
    when it answered with one, so a caller can tell *not running* from
    *refused* without parsing anything.
    """
    url = f"{host()}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        raise OllamaError(_error_text(exc), status=exc.code) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ProviderUnavailable(
            f"Ollama is not answering at {host()} ({exc.reason if hasattr(exc, 'reason') else exc})."
        ) from exc
    return json.loads(raw) if raw else {}


class OllamaError(RuntimeError):
    """The server answered with an error, in its own words."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def _error_text(exc: urllib.error.HTTPError) -> str:
    """Ollama puts its reason in ``{"error": "..."}``; keep that and nothing else."""
    try:
        payload = json.loads(exc.read())
        if isinstance(payload, dict) and payload.get("error"):
            return str(payload["error"])
    except (ValueError, OSError):
        pass
    return f"HTTP {exc.code}"


class Ollama(Provider):
    kind = "ollama"
    label = "Ollama"
    default_model = DEFAULT_MODEL
    honours_effort = False
    start_remedy = "Open the Ollama app, or run `ollama serve`."
    warms = True
    metered = False

    def env(self) -> dict[str, str]:
        return {BASE_URL_VAR: host(), TOKEN_VAR: TOKEN}

    def status(self) -> Status:
        try:
            version = _request("/api/version").get("version") or "?"
        except ProviderUnavailable as exc:
            return Status(reachable=False, detail=str(exc))
        return Status(reachable=True, detail=f"Ollama {version} at {host()}")

    def models(self) -> list[Model]:
        listed = _request("/api/tags").get("models") or []
        return sorted((_model(entry) for entry in listed), key=lambda m: m.name)

    def add_command(self, model: str) -> str:
        return f"ollama pull {model}"

    def preflight(self, model: str, *, prompt_chars: Callable[[], int]) -> Preflight:
        """Load the model, read what it loaded with, refuse with the server's reason.

        Three refusals, each with the one thing to do about it: the server is
        not running; the model is not installed; the model did not load, in
        Ollama's words (*model requires more system memory (13.2 GiB) than is
        available (9.1 GiB)*). And one portia measures itself: the context the
        model was loaded with is smaller than the instructions it is about to
        be sent, which Ollama would answer by dropping the front of them.
        """
        chars = int(prompt_chars())
        facts: dict[str, Any] = {
            "prompt_chars": chars,
            "prompt_tokens_about": rough_tokens(chars),
            "machine_memory": machine_memory(),
        }
        try:
            installed = {m.name: m for m in self.models()}
        except ProviderUnavailable as exc:
            return Preflight(False, facts, reason=str(exc), remedy=START_REMEDY)
        if model not in installed:
            return Preflight(
                False,
                facts,
                reason=f"{model} is not installed.",
                remedy=self.add_command(model),
            )
        facts["size"] = installed[model].size
        try:
            _request(
                "/api/generate", {"model": model, "keep_alive": KEEP_ALIVE}, timeout=LOAD_TIMEOUT
            )
        except OllamaError as exc:
            return Preflight(
                False, facts, reason=f"Ollama did not load {model}: {exc}", remedy=FIT_REMEDY
            )
        except ProviderUnavailable as exc:
            return Preflight(False, facts, reason=str(exc), remedy=START_REMEDY)
        loaded = _loaded(model)
        facts["context_length"] = loaded.get("context_length")
        facts["size_loaded"] = loaded.get("size")
        context = facts["context_length"]
        if context is not None and context < facts["prompt_tokens_about"]:
            return Preflight(
                False,
                facts,
                reason=(
                    f"{model} is loaded with a {context:,}-token context, and portia's "
                    f"instructions alone are about {facts['prompt_tokens_about']:,} tokens. "
                    "Ollama would drop the front of them."
                ),
                remedy=CONTEXT_REMEDY,
            )
        return Preflight(True, facts)


def _model(entry: dict) -> Model:
    details = entry.get("details") or {}
    detail = " ".join(
        str(details[k]) for k in ("parameter_size", "quantization_level") if details.get(k)
    )
    size = entry.get("size")
    return Model(str(entry.get("name") or entry.get("model")), int(size) if size else None, detail)


def _loaded(model: str) -> dict:
    """The ``/api/ps`` entry for ``model``, or ``{}`` when it is not resident."""
    try:
        running = _request("/api/ps").get("models") or []
    except (ProviderUnavailable, OllamaError):
        return {}
    for entry in running:
        if entry.get("name") == model or entry.get("model") == model:
            return entry
    return {}


#: The one thing to do about each refusal. Sentences, because they are read by
#: a person at the composer; the command form goes beside them where there is
#: one (`add_command`).
START_REMEDY = Ollama.start_remedy
FIT_REMEDY = "Pick a smaller model or a smaller tag of this one, or unload what is resident."
CONTEXT_REMEDY = (
    "Raise the context length in Ollama's settings, or start it with "
    "OLLAMA_CONTEXT_LENGTH=32768. Ollama recommends at least 32K for the loop."
)

PROVIDER = Ollama()

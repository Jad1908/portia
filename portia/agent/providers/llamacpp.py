"""llama.cpp: a model served by ``llama-server`` on this machine, behind the same loop.

``llama-server`` speaks the Anthropic Messages API (``/v1/messages``), so this
is the second provider of the shape `ollama.py` has: two variables in the
binary's environment (:meth:`env`) and nothing in the loop changed. It exists
for what the server lets you set that Ollama's does not (`docs/PROVIDERS.md`
§6.2): the context is a flag rather than a menu-bar slider, the prompt cache
is per slot and survives a changed middle (``--cache-reuse``), and the same
weights measured 1.7× Ollama's prefill on the 2026-09-14 benchmark.

**What this module measures, and why it is less than Ollama's.** Ollama loads
a model on request, so `ollama.Ollama.preflight` has to ask for the load and
read what it loaded with. ``llama-server`` loads its model when it starts and
serves that one: there is nothing to load, and the two facts that decide
whether a message can go are already on the server. ``/props`` says the
context each slot was given, and ``/v1/models`` says what is being served and
how big it is. :meth:`preflight` reads both and refuses the send when the
instructions will not fit the slot or the name picked is not the one being
served, with the command that starts the server the right way as the remedy.

**The context is per slot, and ``-c`` is split across slots.** A server
started with ``-c 32768 -np 2`` gives each slot 16,384 tokens (measured, §6.2),
which is under portia's instructions plus one turn of tool results. The start
command this module builds says ``-np 1`` for that reason; with the binary's
title request off (`session.BINARY_ENV`) nothing else runs beside the chat.

**And this is the one provider portia can start** (§4.9, the user's call,
2026-09-14). Ollama is a daemon with its own app and its own lifetime;
``llama-server`` is one model on one port with flags portia already knows, so
the window can run the command it would otherwise only show. What it needs
is in :data:`CONFIG`, **one file for the machine and never per project**: a
model is downloaded once and read by every project, the way Ollama keeps
``~/.ollama/models``. A model named as a Hugging Face repository is fetched by
``llama-server -hf`` into its own cache (``~/Library/Caches/llama.cpp`` on
macOS, ``~/.cache/llama.cpp`` on Linux, or ``LLAMA_CACHE``), also once. The
process is a child of the window (:func:`start`), its output goes to
:data:`LOG`, and closing the window stops it (:func:`stop`), so ten gigabytes
never outlive the app that loaded them. Portia still never *pulls*: §4.4 was
about a multi-gigabyte download with its own progress and failures, and this
runs a file already on disk, or hands the fetch to the server's own flag.

**The model name is the server's, and there is no name until it has been
asked.** ``llama-server`` serves the file it was started with and ignores the
``model`` field in single-model mode, so :attr:`default_model` is whatever the
last listing said, else the configured model, else a placeholder. A name the
server does not list is refused rather than sent, because a server started as
a router (``--models-dir``) does read the field.

Every network call goes through :func:`_request`, which a test replaces.
"""

from __future__ import annotations

import atexit
import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

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

#: The server's own variables for where it listens, read the way it reads
#: them, so a server started with them is found without portia learning a
#: third spelling. The defaults are the server's defaults.
HOST_VAR = "LLAMA_ARG_HOST"
PORT_VAR = "LLAMA_ARG_PORT"
DEFAULT_HOST = "127.0.0.1"
#: Not the server's own 8080: that is the port portia's window listens on
#: (`ui/__main__`), and on 2026-09-14 the picker sat spinning because the
#: window itself answered the health check with a 404. A server started by
#: hand on 8080 is still found through `LLAMA_ARG_PORT` or the panel's field.
DEFAULT_PORT = 8081

#: The server's own variable for the key it was started with (``--api-key``).
#: Read and passed through as the binary's token when set; otherwise the
#: binary gets a fixed string the server ignores, as on Ollama.
KEY_VAR = "LLAMA_API_KEY"
TOKEN = "llama.cpp"

#: The model name before the server has been asked what it serves and nothing
#: is configured. The server ignores the name in single-model mode; the picker
#: and ``models list`` replace it with the server's own name on the first
#: listing.
SERVED = "no server"

#: Nothing here loads anything, so every call is quick. Starting does load,
#: and a first load reads gigabytes off disk.
QUICK_TIMEOUT = 3.0
LOAD_TIMEOUT = 300.0
READY_POLL = 0.5

#: The machine's one llama-server configuration, beside the connections file
#: (`connectors/registry.CONNECTIONS`): a model is the machine's, not a
#: project's. The server's output goes beside it.
CONFIG = Path.home() / ".config" / "portia" / "llamacpp.yaml"
LOG = CONFIG.with_name("llama-server.log")
BINARY = "llama-server"

#: **The registry**: the one folder on this machine portia looks in for model
#: files (the user's call, 2026-09-14: "having the models fly around the
#: laptop is crazy"). Created the first time llama.cpp is used, listed by the
#: start panel by file name, and what Ollama's ``~/.ollama/models`` is to
#: Ollama. Data rather than config, so it is the XDG data directory. A model
#: elsewhere is still allowed, typed as a path, because a 5 GB file is not
#: something portia copies.
REGISTRY_DIR = Path.home() / ".local" / "share" / "portia" / "models"
MODEL_SUFFIX = ".gguf"

#: What the loop needs of the server. ``-c`` is the context per slot only when
#: there is one slot, and portia's instructions alone are about 14,700 tokens
#: (§6). ``--cache-reuse`` lets a turn whose middle changed (tool results)
#: keep the prefix behind it.
DEFAULT_CONTEXT = 32768
SLOTS = 1
CACHE_REUSE = 256
SERVE_FLAGS = f"-c {DEFAULT_CONTEXT} -np {SLOTS} --cache-reuse {CACHE_REUSE}"


@dataclass(frozen=True)
class ServerConfig:
    """How this machine's llama-server is started: the model, the context, the port."""

    #: A ``.gguf`` path, or a Hugging Face repository (``org/name:quant``) the
    #: server fetches once into its own cache.
    model: str = ""
    context: int = DEFAULT_CONTEXT
    port: int = DEFAULT_PORT

    @property
    def name(self) -> str:
        """What the model is called on screen and on the server: the file's stem,
        or the repository as typed. The server is started with this as its
        alias, so ``/v1/models`` says *Qwen3-8B-Q4_K_M* and not a path."""
        return display_name(self.model)

    @property
    def in_registry(self) -> bool:
        return _is_file(self.model) and Path(self.model).expanduser().parent == REGISTRY_DIR

    def command(self) -> list[str]:
        """The exact argv, the same one every remedy shows as a sentence."""
        flag = "-m" if _is_file(self.model) else "-hf"
        return [
            BINARY,
            flag,
            self.model,
            "-a",
            self.name,
            "-c",
            str(self.context),
            "-np",
            str(SLOTS),
            "--cache-reuse",
            str(CACHE_REUSE),
            "--host",
            DEFAULT_HOST,
            "--port",
            str(self.port),
        ]

    @classmethod
    def from_form(cls, form: dict[str, str]) -> ServerConfig:
        """Typed fields to a config, refusing a number that is not one."""
        model = (form.get("model") or "").strip()
        if not model:
            raise ValueError("Name the model: a .gguf file, or a Hugging Face repository.")
        return cls(
            model, _number(form, "context", DEFAULT_CONTEXT), _number(form, "port", DEFAULT_PORT)
        )

    def as_form(self) -> dict[str, str]:
        return {"model": self.model, "context": str(self.context), "port": str(self.port)}


def _number(form: dict[str, str], key: str, default: int) -> int:
    raw = (form.get(key) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(f"{key} must be a whole number, not {raw!r}.") from None
    if value <= 0:
        raise ValueError(f"{key} must be above zero.")
    return value


def _is_file(model: str) -> bool:
    return model.endswith(MODEL_SUFFIX) or model.startswith(("/", "~", "."))


def display_name(model: str) -> str:
    """A file's stem, or a repository name as typed; empty for no model."""
    if not model:
        return ""
    if _is_file(model):
        return Path(model).name.removesuffix(MODEL_SUFFIX)
    return model


def registry_models() -> list[Path]:
    """Every model file in `REGISTRY_DIR`, by name, creating the folder on first use."""
    REGISTRY_DIR.mkdir(parents=True, exist_ok=True)
    return sorted(p for p in REGISTRY_DIR.iterdir() if p.suffix == MODEL_SUFFIX and p.is_file())


def load_config(path: Path | None = None) -> ServerConfig:
    """The saved configuration, or the defaults with no model when none was saved."""
    target = path or CONFIG
    if not target.exists():
        return ServerConfig()
    raw = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        return ServerConfig()
    return ServerConfig(
        str(raw.get("model") or ""),
        int(raw.get("context") or DEFAULT_CONTEXT),
        int(raw.get("port") or DEFAULT_PORT),
    )


def save_config(config: ServerConfig, path: Path | None = None) -> Path:
    target = path or CONFIG
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump(config.__dict__, sort_keys=True), encoding="utf-8")
    return target


def host() -> str:
    """The server's URL: the server's own variables first, else the configured port."""
    raw_host = os.environ.get(HOST_VAR, "").strip() or DEFAULT_HOST
    if "://" in raw_host:
        return raw_host.rstrip("/")
    port = os.environ.get(PORT_VAR, "").strip() or str(load_config().port)
    return f"http://{raw_host}:{port}"


def _request(path: str, *, timeout: float = QUICK_TIMEOUT) -> Any:
    """One GET to the server. The one function a test replaces.

    Raises `ProviderUnavailable` when nothing answers, and `LlamaError` with
    the server's own message when it answered with one (a 503 while the model
    is still loading is the common case).
    """
    req = urllib.request.Request(f"{host()}{path}", headers=_headers())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        raise LlamaError(_error_text(exc), status=exc.code) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ProviderUnavailable(
            f"llama-server is not answering at {host()} "
            f"({exc.reason if hasattr(exc, 'reason') else exc})."
        ) from exc
    return json.loads(raw) if raw else {}


def _headers() -> dict[str, str]:
    key = os.environ.get(KEY_VAR, "").strip()
    return {"Authorization": f"Bearer {key}"} if key else {}


class LlamaError(RuntimeError):
    """The server answered with an error, in its own words."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def _error_text(exc: urllib.error.HTTPError) -> str:
    """``llama-server`` puts its reason in ``{"error": {"message": "..."}}``."""
    try:
        payload = json.loads(exc.read())
        error = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])
        if isinstance(error, str) and error:
            return error
    except (ValueError, OSError):
        pass
    return f"HTTP {exc.code}"


# --- the process portia started, if any -------------------------------------------

_process: subprocess.Popen[bytes] | None = None


def running() -> int | None:
    """The pid of the server this process started and has not stopped, else ``None``."""
    if _process is not None and _process.poll() is None:
        return _process.pid
    return None


def start(config: ServerConfig) -> int:
    """Start ``llama-server`` with ``config``, output to `LOG`, and return its pid.

    Refused when this process already started one (stop it first), when the
    binary is not installed, or when something is already answering on the
    port, because a second server on the same port would fail after loading
    the model and the refusal would arrive minutes late.
    """
    if running() is not None:
        raise ProviderUnavailable("llama-server is already running from this window.")
    if shutil.which(BINARY) is None:
        raise ProviderUnavailable(f"{BINARY} is not installed. {INSTALL_REMEDY}")
    if _answering(config.port):
        raise ProviderUnavailable(
            f"Something is already answering on port {config.port}. Stop it, or pick another port."
        )
    LOG.parent.mkdir(parents=True, exist_ok=True)
    log = LOG.open("ab")
    global _process
    _process = subprocess.Popen(
        config.command(), stdout=log, stderr=subprocess.STDOUT, start_new_session=True
    )
    return _process.pid


def _answering(port: int) -> bool:
    try:
        urllib.request.urlopen(f"http://{DEFAULT_HOST}:{port}/health", timeout=1.0)
    except urllib.error.HTTPError:
        return True
    except (urllib.error.URLError, TimeoutError, OSError):
        return False
    return True


def wait_ready(*, timeout: float = LOAD_TIMEOUT) -> None:
    """Block until the started server answers ``/health`` with *ok*.

    Raises `ProviderUnavailable` with the log's last lines when the process
    exits first (a model that does not fit, a file that is not there) or when
    ``timeout`` passes, which on a first ``-hf`` fetch means the download is
    still going and the server is not the problem.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if running() is None:
            raise ProviderUnavailable(f"llama-server exited before it was ready. {_log_tail()}")
        try:
            if _request("/health").get("status") == "ok":
                return
        except (ProviderUnavailable, LlamaError):
            pass
        time.sleep(READY_POLL)
    raise ProviderUnavailable(
        f"llama-server is not ready after {int(timeout)} s; it is still loading, or the "
        f"model is still downloading. {_log_tail()}"
    )


def _log_tail(lines: int = 5) -> str:
    try:
        tail = LOG.read_text(errors="replace", encoding="utf-8").splitlines()[-lines:]
    except OSError:
        return ""
    return (
        ("The log ends with: " + " | ".join(t.strip() for t in tail if t.strip())) if tail else ""
    )


def stop(*, wait: float = 10.0) -> None:
    """Stop the server this process started, if any. Nothing to do otherwise."""
    global _process
    if _process is None:
        return
    if _process.poll() is None:
        _process.terminate()
        try:
            _process.wait(wait)
        except subprocess.TimeoutExpired:
            _process.kill()
            _process.wait()
    _process = None
    _served.clear()


#: The window stops the server from NiceGUI's shutdown hook (`ui/app.py`);
#: this is for a plain interpreter exit, a CLI that started one. It does
#: **not** cover a SIGTERM to the window: uvicorn re-raises the signal with
#: the default handler after its own shutdown, and that exit skips ``atexit``
#: (measured 2026-09-14, a 10 GB server left behind), which is why the hook
#: is the one that counts. A no-op when nothing was started.
atexit.register(stop)

#: The names the last listing returned, first the one a fresh chat starts on.
#: Module state rather than a network call in `default_model`, because that
#: attribute is read while a pane is being drawn.
_served: list[str] = []


class LlamaCpp(Provider):
    kind = "llamacpp"
    label = "llama.cpp"
    honours_effort = False
    start_remedy = "Start it from the picker, or run the command the panel shows."
    # Nothing loads, but the flag also puts the list on screen at start and the
    # refresh control beside it, which a server you start by hand needs more
    # than Ollama does.
    warms = True
    metered = False
    starts = True

    @property
    def default_model(self) -> str:  # type: ignore[override]
        if _served:
            return _served[0]
        return load_config().name or SERVED

    def started(self) -> bool:
        return running() is not None

    def env(self) -> dict[str, str]:
        return {BASE_URL_VAR: host(), TOKEN_VAR: os.environ.get(KEY_VAR, "").strip() or TOKEN}

    def status(self) -> Status:
        try:
            health = _request("/health")
        except ProviderUnavailable as exc:
            return Status(reachable=False, detail=str(exc))
        except LlamaError as exc:
            # 503 is the server loading its model; anything else answering
            # at this address is not llama-server (the window on 8080 was).
            if exc.status == 503:
                return Status(reachable=True, detail=f"llama-server at {host()} is loading: {exc}")
            return Status(
                reachable=False,
                detail=f"{host()} answers, but not as llama-server ({exc}). {OTHER_PORT_REMEDY}",
            )
        build = ""
        try:
            build = str(_request("/props").get("build_info") or "")
        except (ProviderUnavailable, LlamaError):
            pass
        state = health.get("status") or "?"
        return Status(
            reachable=True,
            detail=f"llama-server {build} at {host()} ({state})".strip(),
            version=f"llama-server {build}".strip(),
        )

    def models(self) -> list[Model]:
        listed = _request("/v1/models").get("data") or []
        models = [_model(entry) for entry in listed]
        _served[:] = [m.name for m in models]
        return models

    def add_command(self, model: str) -> str:
        """The command that serves ``model``: a file by path, anything else off Hugging Face."""
        if model == SERVED:
            return f"llama-server -m {REGISTRY_DIR}/<model>.gguf {SERVE_FLAGS}"
        flag = "-m" if _is_file(model) else "-hf"
        return f"llama-server {flag} {model} {SERVE_FLAGS}"

    def preflight(self, model: str, *, prompt_chars: Callable[[], int]) -> Preflight:
        """Read what the server serves and the context it gave each slot; refuse on either.

        Three refusals, each with the command that starts the server the right
        way: nothing is answering; the name picked is not the one being served;
        the slot's context is smaller than the instructions about to be sent.
        Nothing is loaded here, because the server loaded its model when it
        started, and the facts it read are returned either way.
        """
        chars = int(prompt_chars())
        facts: dict[str, Any] = {
            "prompt_chars": chars,
            "prompt_tokens_about": rough_tokens(chars),
            "machine_memory": machine_memory(),
        }
        try:
            listed = self.models()
        except ProviderUnavailable as exc:
            return Preflight(False, facts, reason=str(exc), remedy=self.start_remedy)
        except LlamaError as exc:
            return Preflight(
                False, facts, reason=f"llama-server is not ready: {exc}", remedy=WAIT_REMEDY
            )
        if not listed:
            return Preflight(
                False, facts, reason="llama-server is serving no model.", remedy=self.start_remedy
            )
        names = [m.name for m in listed]
        if model not in names and model not in (SERVED, load_config().name):
            return Preflight(
                False,
                facts,
                reason=f"llama-server is serving {names[0]}, not {model}.",
                remedy=self.add_command(model),
            )
        served = listed[names.index(model)] if model in names else listed[0]
        facts["model"] = served.name
        facts["size"] = served.size
        try:
            props = _request("/props")
        except (ProviderUnavailable, LlamaError) as exc:
            return Preflight(False, facts, reason=str(exc), remedy=self.start_remedy)
        settings = props.get("default_generation_settings") or {}
        facts["context_length"] = settings.get("n_ctx")
        facts["slots"] = props.get("total_slots")
        facts["build"] = props.get("build_info")
        context = facts["context_length"]
        if context is not None and context < facts["prompt_tokens_about"]:
            return Preflight(
                False,
                facts,
                reason=(
                    f"each llama-server slot has a {context:,}-token context, and portia's "
                    f"instructions alone are about {facts['prompt_tokens_about']:,} tokens."
                ),
                remedy=CONTEXT_REMEDY,
            )
        return Preflight(True, facts)


def _model(entry: dict) -> Model:
    meta = entry.get("meta") or {}
    parts = []
    params = meta.get("n_params")
    if params:
        parts.append(f"{int(params) / 1e9:.1f}B")
    if meta.get("ftype"):
        parts.append(str(meta["ftype"]))
    size = meta.get("size")
    return Model(str(entry.get("id")), int(size) if size else None, " ".join(parts))


#: The one thing to do about each refusal, read by a person at the composer.
WAIT_REMEDY = "Wait for the model to finish loading, then send again."
INSTALL_REMEDY = "Install it with `brew install llama.cpp`."
OTHER_PORT_REMEDY = "Start it on another port, or point LLAMA_ARG_PORT at where it runs."
CONTEXT_REMEDY = (
    f"Restart llama-server with `{SERVE_FLAGS}`: -c is the context per slot only with -np 1, "
    "and is split across slots otherwise."
)

PROVIDER = LlamaCpp()

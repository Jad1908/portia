"""One home per model provider (`docs/PROVIDERS.md`).

A **provider** is where the model the copilot runs on comes from: Anthropic's
API, or an Ollama server on this machine. The loop is the same either way. The
Claude Agent SDK drives it through a Claude Code binary, the machine's own or
the SDK's bundled copy (:func:`choose_program`), and every mechanism portia
leans on (the permission callback, `AskUserQuestion`, the Stop hook, interrupt,
resume) lives in that binary rather than in the API it talks to. So a provider decides
three things and nothing else: **which environment the binary is started with**,
**which models it can offer**, and **what can be measured about one before a
message is sent to it**.

That last one is the part a remote API never needed. A local model is a file
that has to fit in this machine's memory beside DuckDB, and it is served with a
context window the server chose, not the model. Ollama drops the front of a
prompt that does not fit, silently, and the front of every portia prompt is the
instruction that the copilot never authors a number. `Provider.preflight` asks
the server to load the model and reads back what it loaded with, so a message is
refused with the server's own reason instead of answered by a model that never
saw its instructions.

**The shape mirrors `connectors/`**: one module per kind named for it, a
`PROVIDER` at the top of each, and :func:`get` importing by name so adding a
provider is one module and one entry in :data:`KINDS`. Nothing in here imports
the SDK, so the picker can list models and check a fit without the ``agent``
extra loaded, and every call that touches a network is a plain function a test
can replace.

**Facts, never a verdict.** A preflight reports what it measured: the model's
size, the context it was loaded with, the machine's memory, and what the server
said when it refused. Whether a 13 GB model is a good idea on a 16 GB machine
is the human's call, made with those numbers in front of them.
"""

from __future__ import annotations

import importlib
import os
import platform
import re
import shutil
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

#: Every provider, in the order a picker offers them. The first is the default.
KINDS = ("anthropic", "codex", "ollama", "llamacpp")
DEFAULT_KIND = KINDS[0]

#: What drives the loop for a provider (`docs/PROVIDERS.md` §9). Route A is the
#: Claude Agent SDK's binary, and every provider but one is an environment for
#: it. Route B is a second harness, Codex's app-server, which speaks neither
#: Anthropic's API nor sits behind a bridge to it; `agent/codex.py` drives it
#: and `session.conversation` picks by this field. A harness is a kind, not a
#: rank: the same tools, the same log, the same window either way.
CLAUDE = "claude"
CODEX = "codex"
HARNESSES = (CLAUDE, CODEX)

#: One file for the machine, beside the connections and the llama.cpp
#: configuration: which providers the picker offers, where a binary is, and
#: the variables a provider's process is started with (`docs/PROVIDERS.md`
#: §9.5). Never per project, for the reason `llamacpp.CONFIG` is not: an
#: account or a binary is the machine's, and every project reads the same one.
SETTINGS = Path.home() / ".config" / "portia" / "providers.yaml"

#: What the SDK's binary needs to talk to a server that is not Anthropic's:
#: where it is, and a token the binary requires and a local server ignores.
#: Shared by every local provider, so the two names are written once.
BASE_URL_VAR = "ANTHROPIC_BASE_URL"
TOKEN_VAR = "ANTHROPIC_AUTH_TOKEN"

#: Where a harness's program came from (`Program.origin`, `docs/PROVIDERS.md`
#: §4.10). `CONFIGURED` is the path in the machine's settings; `MACHINE` is the
#: user's own install, found on ``PATH``; `BUNDLED` is the copy the harness's
#: Python package carries. A kind, never a rank: a dashboard draws each by name.
CONFIGURED = "configured"
MACHINE = "machine"
BUNDLED = "bundled"
ORIGINS = (CONFIGURED, MACHINE, BUNDLED)
ORIGIN_WORDS = {
    CONFIGURED: "the configured path",
    MACHINE: "the machine's own",
    BUNDLED: "bundled with the SDK",
}


class ProviderUnavailable(RuntimeError):
    """The provider could not be reached, and this is what it said."""


@dataclass(frozen=True)
class Model:
    """One model a provider offers, with what the provider states about it."""

    name: str
    #: Bytes on disk, for a local model. ``None`` where the provider does not
    #: say, which is every remote one; never zero.
    size: int | None = None
    #: The vendor's one line about it: parameter count and quantization for a
    #: local model, nothing for a remote one.
    detail: str = ""
    #: What the vendor's own picker calls it (``Claude Opus 5.5``), empty where
    #: the name is all there is, which is every local model.
    label: str = ""
    #: Whether the vendor's own picker files it under *legacy*: still served,
    #: no longer what it offers first. The vendor's word, never portia's
    #: judgment of the model, and a picker folds these away rather than ranks
    #: them (`docs/PROVIDERS.md` §5.1).
    legacy: bool = False

    @property
    def shown(self) -> str:
        """The name a picker draws: the vendor's label, else the model's name."""
        return self.label or self.name


@dataclass(frozen=True)
class Program:
    """The one program a harness runs, and why it is that one (`docs/PROVIDERS.md` §4.10).

    ``version`` is the line the program printed for ``--version``
    (``2.1.283 (Claude Code)``), empty where it printed none. ``passed_over``
    is one line about the candidate not taken, empty where there was none:
    the machine's copy that was older than the bundled one, or a launcher
    script that cannot be started as the program.
    """

    path: str
    origin: str
    version: str = ""
    passed_over: str = ""

    @property
    def origin_words(self) -> str:
        """What a dashboard calls the origin."""
        return ORIGIN_WORDS[self.origin]

    def as_dict(self) -> dict[str, str]:
        """The record a log keeps: where it was, where it came from, what it printed."""
        out = {"path": self.path, "origin": self.origin, "version": self.version}
        if self.passed_over:
            out["passed_over"] = self.passed_over
        return out


_NUMBER = re.compile(r"(\d+(?:\.\d+)+)")


def build_number(printed: str) -> tuple[int, ...]:
    """The first dotted number in what a program printed for ``--version``; empty where there is none.

    ``2.1.283 (Claude Code)`` and ``codex-cli 0.156.1`` both read; a tuple, so
    ``2.1.283`` sorts after ``2.1.99``, which a string would not.
    """
    match = _NUMBER.search(printed)
    return tuple(int(part) for part in match.group(1).split(".")) if match else ()


#: What each program printed for ``--version``, by path and the file's stamp.
#: A probe is a subprocess, 0.35 s for the bundled Claude Code on the machine
#: this was built on, and the dashboard, the options and the log all ask; so
#: each copy is asked once per process, and again when the file changes,
#: which is what an update does.
_printed: dict[tuple[str, int, int], str] = {}


def remembered_version(path: str, probe: Callable[[str], str]) -> str:
    """What ``path`` prints for ``--version``, asked once per copy (:data:`_printed`)."""
    try:
        stamp = os.stat(path)
    except OSError:
        return ""
    key = (path, stamp.st_mtime_ns, stamp.st_size)
    if key not in _printed:
        try:
            _printed[key] = probe(path)
        except ProviderUnavailable:
            _printed[key] = ""
    return _printed[key]


def machine_binary(name: str) -> str | None:
    """The user's own ``name`` on ``PATH``; on Windows the real program before any launcher script."""
    if platform.system() == "Windows":
        return shutil.which(f"{name}.exe") or shutil.which(name)
    return shutil.which(name)


def choose_program(
    *,
    configured: str,
    machine: str | None,
    bundled: str | None,
    printed: Callable[[str], str],
) -> Program | None:
    """Which of a harness's programs runs (`docs/PROVIDERS.md` §4.10).

    The configured path when there is one, as it is. Else the machine's own
    install when it is at least as new as the bundled copy, because the copy
    the user updates knows today's models, and a copy older than the one
    portia was built against may not know the SDK's flags. Else the bundled
    copy. Else whichever exists, and ``None`` when neither does. ``printed``
    is what a candidate says for ``--version``, asked only when the two have
    to be compared; a candidate that prints no number cannot be compared and
    is passed over for one that does. On Windows a launcher script on
    ``PATH`` (npm's ``claude.cmd``) is passed over too: the SDK refuses to
    start one.
    """
    if configured.strip():
        expanded = os.path.expanduser(configured.strip())
        path = expanded if Path(expanded).is_file() else shutil.which(expanded)
        return Program(path, CONFIGURED, printed(path)) if path else None
    over = ""
    if machine and platform.system() == "Windows" and not machine.lower().endswith(".exe"):
        over = f"{machine} is a launcher script, which cannot be started as the program"
        machine = None
    if machine and bundled:
        mine, theirs = printed(machine), printed(bundled)
        if build_number(mine) and (
            not build_number(theirs) or build_number(mine) >= build_number(theirs)
        ):
            return Program(machine, MACHINE, mine)
        over = (
            f"{machine} prints no version"
            if not build_number(mine)
            else f"{machine} is {mine}, older than the bundled {theirs}"
        )
        return Program(bundled, BUNDLED, theirs, over)
    if machine:
        return Program(machine, MACHINE, printed(machine))
    if bundled:
        return Program(bundled, BUNDLED, printed(bundled), over)
    return None


@dataclass(frozen=True)
class Status:
    """Whether the provider can be reached right now, and what it said.

    ``reachable`` is ``None`` when nothing was measured. ``detail`` is one line
    for a human, in the provider's own words where it has them. ``version`` and
    ``account`` are the two facts a dashboard draws on their own lines when a
    provider reports them: the binary or server that answered, and who it is
    signed in as (`docs/PROVIDERS.md` §9.5). Both empty where not measured,
    never guessed. ``remedy`` is the one thing to do when it is not reachable.
    """

    reachable: bool | None
    detail: str = ""
    version: str = ""
    account: str = ""
    remedy: str = ""
    #: Which program answered and why it is that one, for the two harnesses
    #: (§4.10); ``None`` for a server, and where nothing was found.
    program: Program | None = None


@dataclass(frozen=True)
class Settings:
    """What the machine says about one provider (:data:`SETTINGS`).

    ``enabled`` is whether the picker offers it; the default is yes for every
    kind, and the dashboard is where a kind nobody has is switched off. Off
    means *not offered*: a chat that already ran on it still opens and says so.
    ``binary`` is a path for a provider that runs one, empty for
    `choose_program`'s rule (the machine's own when it is at least as new as
    the bundled copy); ``home`` is that binary's own configuration directory,
    empty for its default. ``env`` is the variables the provider's process is
    started with, on top of the machine's own: an API key, a base URL. A secret
    written here is written in the clear, in the user's home, which is where
    the vendors' own files keep it too; the dashboard says so beside the field.
    """

    enabled: bool = True
    binary: str = ""
    home: str = ""
    env: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"enabled": self.enabled}
        if self.binary:
            out["binary"] = self.binary
        if self.home:
            out["home"] = self.home
        if self.env:
            out["env"] = dict(self.env)
        return out


def load_settings(path: Path | None = None) -> dict[str, Settings]:
    """Every kind's settings, the defaults for a kind the file does not name."""
    target = path or SETTINGS
    raw: Any = {}
    if target.exists():
        raw = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raw = {}
    out: dict[str, Settings] = {}
    for kind in KINDS:
        found = raw.get(kind)
        entry: dict[str, Any] = dict(found) if isinstance(found, dict) else {}
        raw_env = entry.get("env")
        env: dict[str, Any] = dict(raw_env) if isinstance(raw_env, dict) else {}
        out[kind] = Settings(
            enabled=bool(entry.get("enabled", True)),
            binary=str(entry.get("binary") or ""),
            home=str(entry.get("home") or ""),
            env={str(k): str(v) for k, v in env.items()},
        )
    return out


def save_settings(settings: dict[str, Settings], path: Path | None = None) -> Path:
    """Write every kind's settings. A kind on its defaults is written as such, so the file reads whole."""
    target = path or SETTINGS
    target.parent.mkdir(parents=True, exist_ok=True)
    data = {kind: settings[kind].as_dict() for kind in KINDS if kind in settings}
    target.write_text(yaml.safe_dump(data, sort_keys=True), encoding="utf-8")
    return target


def settings_for(kind: str, path: Path | None = None) -> Settings:
    return load_settings(path).get(kind, Settings())


def offered_kinds(path: Path | None = None) -> tuple[str, ...]:
    """The kinds a picker draws: every enabled one, in `KINDS` order, never empty.

    With everything switched off the default is offered anyway: a picker with
    no options is a composer that cannot send, and the dashboard is where to
    see why.
    """
    settings = load_settings(path)
    kinds = tuple(kind for kind in KINDS if settings[kind].enabled)
    return kinds or (DEFAULT_KIND,)


@dataclass(frozen=True)
class Preflight:
    """What was measured about a model before sending to it, and whether the send can go.

    ``facts`` are measured values, keyed by name and never rounded here.
    ``reason`` is why not, in the server's own words where it has them, and
    ``remedy`` is the one thing to do about it: a command, or a setting to
    change. Both are empty when ``ok``.
    """

    ok: bool
    facts: dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    remedy: str = ""


class Provider(ABC):
    """Where the copilot's model comes from. One subclass per kind."""

    #: The name in `KINDS`, and the name a log, a flag and a picker use.
    kind: str
    #: What a human calls it.
    label: str
    #: What drives the loop for this provider (:data:`HARNESSES`). `CLAUDE` for
    #: every environment of the SDK's binary; `CODEX` for the one provider
    #: that is a second harness (`agent/codex.py`).
    harness: str = CLAUDE
    #: The settings the dashboard draws a field for, beyond `enabled` and the
    #: variables every provider has: ``binary`` and ``home`` for a provider
    #: that runs a program of its own, nothing for a server reached over HTTP.
    runtime_fields: tuple[str, ...] = ()
    #: The model a fresh chat starts on when nothing was picked.
    default_model: str
    #: Whether the reasoning-effort knob reaches the model. It is an Anthropic
    #: API parameter; Ollama accepts it and ignores it, and a control that is
    #: offered and quietly ignored is the failure `DESIGN.md` names. A surface
    #: reads this and stops drawing the knob.
    honours_effort: bool
    #: Whether a model from here can be handed a picture. It decides whether a
    #: chat is offered `view_chart` (`tools.VISION_TOOLS`), because a text-only
    #: model given an image block errors or describes a chart it never saw.
    #: **False unless a provider says otherwise, and the local two do not yet**:
    #: there it is a fact about one model and not about the server, and whether
    #: a local server's Anthropic-shaped endpoint takes an image inside a tool
    #: result is unmeasured (`docs/VISUALIZATION.md` §12.6).
    sees_images: bool = False
    #: What to do when the provider cannot be reached, for the one kind that
    #: can be started: a sentence, or empty where nothing on this machine
    #: answers for it.
    start_remedy: str = ""
    #: Whether `preflight` loads something a person should see the window
    #: waiting on. A local server reads gigabytes off disk the first time;
    #: a remote API's preflight is nothing, and a status line for nothing
    #: would flash on every send.
    warms: bool = False
    #: Whether a message costs money the SDK can price. The binary reports a
    #: dollar figure on every result, priced as if the model were Claude's;
    #: on a local server that number is about nothing, and a surface drops it
    #: rather than draw a bill for a model that ran on the user's own machine.
    metered: bool = True
    #: Whether portia can start this provider's server itself (`docs/PROVIDERS.md`
    #: §4.9). True for a server that is one model on one port with flags portia
    #: knows (`llamacpp`); false for a daemon with its own app and lifetime
    #: (Ollama) and for a remote API. A surface reads this and offers the start
    #: panel where the remedy would otherwise be a command to type.
    starts: bool = False
    #: The models this provider can name without asking anyone: a list written
    #: in its own module. Empty where the list is the server's, which is every
    #: local provider. A picker may draw these in a render, where `models` is
    #: a network call and may not be (`docs/PROVIDERS.md` §4.3). Written in the
    #: order the vendor's own picker offers them, current first, and read off
    #: the binary portia runs (§5.1).
    static_models: tuple[Model, ...] = ()

    @abstractmethod
    def env(self) -> dict[str, str]:
        """Variables the SDK's binary is started with, on top of the process's own."""

    @abstractmethod
    def status(self) -> Status:
        """Whether the provider can be reached now. Cheap, and safe to call often."""

    @abstractmethod
    def models(self) -> list[Model]:
        """Every model this provider can offer, or `ProviderUnavailable`."""

    @abstractmethod
    def preflight(self, model: str, *, prompt_chars: Callable[[], int]) -> Preflight:
        """Measure what can be measured about ``model`` before a message goes to it.

        ``prompt_chars`` says how long everything portia composes for the model
        is (`session.prompt_chars`), so a provider that knows its context
        window can say whether the instructions fit. A callable rather than a
        number because composing the prompt reads the catalog, and a provider
        that measures nothing should not cost that on every send.
        """

    def add_command(self, model: str) -> str | None:
        """How ``model`` is installed, outside portia. ``None`` where nothing is."""
        return None

    def started(self) -> bool:
        """Whether this process started the provider's server and has not stopped it.

        Only a provider with `starts` can say yes; a surface draws *Stop*
        where it drew *Start*. Never a network call: it is read in a render.
        """
        return False

    def settings(self) -> Settings:
        """This provider's machine settings (:data:`SETTINGS`). One small file read."""
        return settings_for(self.kind)

    def env_notes(self) -> dict[str, str]:
        """The variables this provider reads out of `Settings.env`, each with one line saying what it does.

        Drawn beside the variables editor so a person knows which names mean
        something to portia. Empty where a provider reads none of its own.
        """
        return {}


def get(kind: str) -> Provider:
    """The provider for ``kind``: ``portia.agent.providers.<kind>``'s ``PROVIDER``.

    Looked up by name, like `connectors.module_for`, so a new provider is one
    module and one entry in `KINDS` rather than a branch in every caller.
    """
    if kind not in KINDS:
        raise ValueError(f"no model provider {kind!r}; portia knows {', '.join(KINDS)}")
    return importlib.import_module(f"portia.agent.providers.{kind}").PROVIDER


def machine_memory() -> int | None:
    """Physical memory on this machine, in bytes. ``None`` where the OS does not say.

    A fact drawn beside a local model's size, so the person choosing one has
    both numbers. Nothing here divides one by the other: whether a model fits
    is measured by asking the server to load it (`Provider.preflight`), not
    estimated from a ratio.
    """
    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        page = os.sysconf("SC_PAGE_SIZE")
    except (ValueError, OSError, AttributeError):
        return None
    if pages <= 0 or page <= 0:
        return None
    return int(pages) * int(page)


#: Characters per token, for turning a prompt's length into the *rough* number
#: of tokens it costs. An estimate and stated as one wherever it is drawn: the
#: tokenizer is the model's, and it is only used to tell a 4,096-token window
#: from a 30,000-character prompt, which no tokenizer would call a fit.
CHARS_PER_TOKEN = 4


def rough_tokens(chars: int) -> int:
    """About how many tokens ``chars`` characters of prompt cost. Rounded up."""
    return -(-int(chars) // CHARS_PER_TOKEN)


#: The module that chooses each harness's program (`program_for`).
HARNESS_MODULE = {CLAUDE: "anthropic", CODEX: "codex"}


def program_for(harness: str) -> Program | None:
    """The program ``harness`` runs now, chosen by that harness's own module (§4.10)."""
    if harness not in HARNESS_MODULE:
        raise ValueError(f"unknown harness {harness!r} — expected one of {', '.join(HARNESSES)}")
    module = importlib.import_module(f"portia.agent.providers.{HARNESS_MODULE[harness]}")
    return module.program()

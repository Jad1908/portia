"""One home per model provider (`docs/PROVIDERS.md`).

A **provider** is where the model the copilot runs on comes from: Anthropic's
API, or an Ollama server on this machine. The loop is the same either way. The
Claude Agent SDK's bundled binary drives it, and every mechanism portia leans on
(the permission callback, `AskUserQuestion`, the Stop hook, interrupt, resume)
lives in that binary rather than in the API it talks to. So a provider decides
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
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

#: Every provider, in the order a picker offers them. The first is the default.
KINDS = ("anthropic", "ollama", "llamacpp")
DEFAULT_KIND = KINDS[0]

#: What the SDK's binary needs to talk to a server that is not Anthropic's:
#: where it is, and a token the binary requires and a local server ignores.
#: Shared by every local provider, so the two names are written once.
BASE_URL_VAR = "ANTHROPIC_BASE_URL"
TOKEN_VAR = "ANTHROPIC_AUTH_TOKEN"


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


@dataclass(frozen=True)
class Status:
    """Whether the provider can be reached right now, and what it said.

    ``reachable`` is ``None`` when nothing was measured. The Anthropic provider
    measures nothing: the account resolves inside the SDK's binary when a chat
    starts, and portia writes no auth code (`docs/PLAN.md` → Auth posture).
    """

    reachable: bool | None
    detail: str = ""


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
    #: The model a fresh chat starts on when nothing was picked.
    default_model: str
    #: Whether the reasoning-effort knob reaches the model. It is an Anthropic
    #: API parameter; Ollama accepts it and ignores it, and a control that is
    #: offered and quietly ignored is the failure `DESIGN.md` names. A surface
    #: reads this and stops drawing the knob.
    honours_effort: bool
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

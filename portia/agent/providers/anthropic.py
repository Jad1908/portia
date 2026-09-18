"""Anthropic: the provider the loop was built on, and the one that sets nothing.

The SDK's binary resolves the account itself, from whatever is in the user's
environment, and portia neither reads nor sets an auth variable
(`docs/PLAN.md` → Auth posture). So :meth:`env` is empty on purpose, and stays
empty: this module is the statement that the default path is unchanged by the
providers seam existing.

Nothing here is measured. The model list is a convenience for a picker and
never a validation set (`session.MODELS`'s old comment, kept), the status is
*not measured*, and a preflight passes with no facts because the one thing that
could be checked, whether the account is good, is the binary's to find out when
the chat starts.
"""

from __future__ import annotations

from collections.abc import Callable

from portia.agent.providers import Model, Preflight, Provider, Status

#: The model is a config knob, never a hard dependency (`docs/PLAN.md`). We
#: develop on a small one on purpose: if the loop works here, the *engine* is
#: good.
DEFAULT_MODEL = "claude-haiku-4-5"

#: Models worth offering in a picker, cheapest first, which is also the order
#: `PLAN.md` says to develop in. ``--model`` takes anything the SDK accepts.
MODELS = ("claude-haiku-4-5", "claude-sonnet-5", "claude-opus-5")


class Anthropic(Provider):
    kind = "anthropic"
    label = "Anthropic"
    default_model = DEFAULT_MODEL
    honours_effort = True
    #: Every model in `MODELS` takes images.
    sees_images = True
    static_models = tuple(Model(name) for name in MODELS)

    def env(self) -> dict[str, str]:
        return {}

    def status(self) -> Status:
        return Status(reachable=None)

    def models(self) -> list[Model]:
        return list(self.static_models)

    def preflight(self, model: str, *, prompt_chars: Callable[[], int]) -> Preflight:
        return Preflight(ok=True)


PROVIDER = Anthropic()

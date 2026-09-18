"""The terminal edge of the model providers: ``python -m portia.cli.models <verb> …``.

    list [--provider KIND]        every provider, whether it can be reached, and its models
    check MODEL --provider KIND   what a preflight measures about one model, and whether
                                  a message could go to it now

Same rules as the window's picker (`docs/PROVIDERS.md` §5): the list is what
the provider says it serves, the sizes are the provider's own numbers, and the
check is `Provider.preflight`, so the composer and this command cannot disagree
about whether a model will load. Nothing here manages weights; when a model
is missing, the line printed is the command that installs it.
"""

from __future__ import annotations

import argparse

from portia import catalog
from portia.agent import providers
from portia.core import present


def _list(args: argparse.Namespace) -> None:
    kinds = (args.provider,) if args.provider else providers.KINDS
    memory = providers.machine_memory()
    if memory is not None:
        print(f"this machine: {present.size(memory)} of memory\n")
    for kind in kinds:
        provider = providers.get(kind)
        status = provider.status()
        state = {True: "reachable", False: "not reachable", None: "not measured"}[status.reachable]
        print(
            f"{provider.kind:10s} {provider.label}  [{state}{': ' + status.detail if status.detail else ''}]"
        )
        if status.reachable is False:
            print(f"    {provider.start_remedy or 'start it and list again'}")
            continue
        try:
            models = provider.models()
        except providers.ProviderUnavailable as exc:
            print(f"    {exc}")
            continue
        if not models:
            print("    no models")
        for m in models:
            mark = "  (default)" if m.name == provider.default_model else ""
            facts = " · ".join(
                part for part in (present.size(m.size) if m.size else "", m.detail) if part
            )
            print(f"    {m.name:32s} {facts}{mark}")
        command = provider.add_command("<name>")
        if command:
            print(f"    add one:  {command}")
        print()


def _check(args: argparse.Namespace) -> None:
    from portia.agent import session

    provider = providers.get(args.provider)
    result = provider.preflight(args.model, prompt_chars=lambda: session.prompt_chars(args.dir))
    for key, value in result.facts.items():
        shown = present.size(value) if key in SIZE_FACTS and value is not None else value
        print(f"  {key:20s} {shown if shown is not None else '—'}")
    if result.ok:
        print(f"\nok: {args.model} on {provider.label} can take a message now")
        return
    print(f"\nrefused: {result.reason}")
    if result.remedy:
        print(f"  {result.remedy}")
    raise SystemExit(1)


#: Facts a preflight reports in bytes, drawn as sizes.
SIZE_FACTS = ("size", "size_loaded", "machine_memory")


def main() -> None:
    parser = argparse.ArgumentParser(description="Where the copilot's model comes from.")
    parser.add_argument("--dir", default=catalog.DEFAULT_DIR, help="catalog directory")
    verbs = parser.add_subparsers(dest="verb", required=True)

    listing = verbs.add_parser("list", help="every provider and its models")
    listing.add_argument("--provider", choices=providers.KINDS, default=None)
    listing.set_defaults(run=_list)

    check = verbs.add_parser("check", help="measure whether a model can take a message now")
    check.add_argument("model")
    check.add_argument("--provider", choices=providers.KINDS, required=True)
    check.set_defaults(run=_check)

    args = parser.parse_args()
    args.run(args)


if __name__ == "__main__":
    main()

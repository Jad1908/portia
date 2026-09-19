"""The terminal edge of the connectors: ``python -m portia.cli.connect <verb> …``.

    add <name> [--kind snowflake] --<field> value …  [--auth <method>]
                               save a connection; each provider's fields are its own flags
    providers                  every warehouse portia can connect to, with its fields and sign-ins
    list                       every connection in ~/.config/portia/connections.yaml
    suggest                    what the vendors' own configuration would fill in
    test <name>                open a session (a browser may open) and say who you are
    use <name> [--agent-writes | --no-agent-writes]
                                       make this project run on that connection
    browse <name> [DB[.SCHEMA]]        databases, schemas, or the tables of one
    scope DB.SCHEMA.TABLE …    bring tables into the project's scope, as metadata

Same rules as the window (`docs/CONNECTORS.md`): a connection is the user's and
holds no secret, the project names it, scoping a table costs no scan, and the
two edges call the same functions so they cannot disagree about any of it.
"""

from __future__ import annotations

import argparse

from portia import catalog
from portia.connectors import activate, open_pool, registry
from portia.core.io import connect


def _add(args: argparse.Namespace) -> None:
    provider = registry.PROVIDERS[args.kind]
    fields = {key: getattr(args, key) for key in provider.keys}
    connection = registry.filled_from_vendor(
        registry.Connection(
            name=args.name, kind=args.kind, auth=args.auth or provider.default_auth, **fields
        )
    )
    path = registry.save(connection)
    print(f"saved {args.name!r} ({provider.label}) → {path}")


def _providers(args: argparse.Namespace) -> None:
    for provider in registry.PROVIDERS.values():
        print(f"{provider.kind:12s} {provider.label}")
        for f in provider.fields:
            print(f"    --{f.key:12s} {'required' if f.required else 'optional'}")
        ways = ", ".join(
            f"{a.key} ({a.secret.lower()} typed per session)" if a.secret else a.key
            for a in provider.auth
        )
        print(f"    --auth        {ways}")


def _list(args: argparse.Namespace) -> None:
    found = registry.load()
    if not found:
        print(f"no connections in {registry.CONNECTIONS}")
        return
    for name, c in sorted(found.items()):
        provider = c.provider
        extras = ", ".join(f"{k}={v}" for k, v in c.fields.items())
        print(f"{name:20s} {provider.label:10s} {c.auth:10s} {extras}")


def _suggest(args: argparse.Namespace) -> None:
    found = registry.suggestions()
    if not found:
        print(f"nothing to suggest — no {registry.snowflake_connections_file()}")
        return
    for c in found:
        missing = [f for f in c.provider.required if not c.fields.get(f)]
        note = f"  (missing {', '.join(missing)})" if missing else ""
        print(f"{c.name:20s} {c.provider.label:10s} {c.provider.summary(c)}{note}")
    if any(c.kind == registry.SNOWFLAKE.kind for c in found):
        print(
            f"\nadd one as it is, signing in the way the file says:  "
            f"connect add <name> --auth {registry.FILE}"
        )


def _test(args: argparse.Namespace) -> None:
    connection = registry.get(args.name)
    session = open_pool(connection).connect(_secret_for(connection))
    try:
        print(session.whoami())
    finally:
        session.close()


def _use(args: argparse.Namespace) -> None:
    registry.get(args.name)  # refuse a name that is not there, with KeyError
    path = catalog.set_connection(args.name, agent_writes=args.agent_writes, portia_dir=args.dir)
    said = [f"connection: {args.name}"]
    if args.agent_writes is not None:
        said.append("the copilot creates tables" if args.agent_writes else "only Build writes")
    print(f"{path} → {', '.join(said)}")


def _browse(args: argparse.Namespace) -> None:
    connection = registry.get(args.name)
    session = open_pool(connection).connect(_secret_for(connection))
    try:
        parts = (args.where or "").split(".") if args.where else []
        if not parts:
            for db in session.databases():
                print(db)
        elif len(parts) == 1:
            for schema in session.schemas(parts[0]):
                print(f"{parts[0]}.{schema}")
        else:
            for table, kind in session.tables(parts[0], parts[1]):
                print(f"{parts[0]}.{parts[1]}.{table}  ({kind})")
    finally:
        session.close()


def _secret_for(connection: registry.Connection) -> str | None:
    """Ask for the password or token on the terminal, never from an argument or a file."""
    if not connection.needs_secret:
        return None
    import getpass
    import sys

    if not sys.stdin.isatty():
        # A host's shell, a CI job: nobody is there to type, and `getpass` would
        # wait on a prompt nothing can answer (`docs/HEADLESS.md` §7).
        raise SystemExit(
            f"{connection.name} signs in with a {(connection.secret_label or 'secret').lower()}, "
            "and there is no terminal here to type it into. Use a connection that signs in "
            "with `browser` or `file` (`connect providers` lists them)."
        )

    return getpass.getpass(f"{connection.name} {(connection.secret_label or '').lower()}: ")


def _scope(args: argparse.Namespace) -> None:
    settings = catalog.project_settings(args.dir)
    secret = (
        _secret_for(registry.get(settings["connection"])) if settings.get("connection") else None
    )
    active = activate(args.dir, secret=secret)
    if not active.remote:
        raise SystemExit("this project names no connection — `connect use <name>` first")
    con = connect()
    for qualified in args.tables:
        written = catalog.scope_table(qualified, con, portia_dir=args.dir)
        print(f"in scope → {written}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Connections to a warehouse, and a project's scope."
    )
    parser.add_argument("--dir", default=catalog.DEFAULT_DIR, help="catalog directory")
    verbs = parser.add_subparsers(dest="verb", required=True)

    add = verbs.add_parser("add", help="save a connection (no secret is stored)")
    add.add_argument("name")
    add.add_argument(
        "--kind",
        choices=list(registry.PROVIDERS),
        default=registry.DEFAULT_KIND,
        help="which warehouse; `providers` lists each one's fields",
    )
    # Every provider's fields, all optional here: which are required is the
    # provider's to say, and `Connection.check` says it with the field's name.
    for provider in registry.PROVIDERS.values():
        for f in provider.fields:
            if f"--{f.key}" not in add._option_string_actions:
                add.add_argument(f"--{f.key}", help=f"{provider.label}: {f.label.lower()}")
    add.add_argument("--auth", help="how to sign in; `providers` lists each one's methods")
    add.set_defaults(run=_add)

    verbs.add_parser("providers", help="the warehouses portia can connect to").set_defaults(
        run=_providers
    )
    verbs.add_parser("list", help="the saved connections").set_defaults(run=_list)
    verbs.add_parser("suggest", help="what the vendors' own files offer").set_defaults(run=_suggest)

    test = verbs.add_parser("test", help="open a session and say who you are")
    test.add_argument("name")
    test.set_defaults(run=_test)

    use = verbs.add_parser("use", help="make this project run on a connection")
    use.add_argument("name")
    use.add_argument(
        "--agent-writes",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="let the copilot create each model's table as it records the step (CONNECTOR.md §2.7.1)",
    )
    use.set_defaults(run=_use)

    browse = verbs.add_parser("browse", help="databases, schemas, or a schema's tables")
    browse.add_argument("name")
    browse.add_argument("where", nargs="?", help="DB or DB.SCHEMA")
    browse.set_defaults(run=_browse)

    scope = verbs.add_parser("scope", help="bring tables into the project, as metadata")
    scope.add_argument("tables", nargs="+", metavar="DB.SCHEMA.TABLE")
    scope.set_defaults(run=_scope)

    args = parser.parse_args()
    try:
        args.run(args)
    except KeyError as exc:
        known = ", ".join(registry.names()) or "(none)"
        raise SystemExit(f"no connection named {exc.args[0]!r}. Known: {known}") from None
    except ValueError as exc:
        raise SystemExit(str(exc)) from None


if __name__ == "__main__":
    main()

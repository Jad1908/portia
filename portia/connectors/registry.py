"""The user's connections — global, outside any project, holding no secret — and
the providers they are connections *to*.

`docs/CONNECTORS.md` §2.3 and §2.4. A project's ``project.yaml`` names a
connection; this file is where the name resolves, for the person whose machine
this is. It sits beside the app's ``recents.json`` because it is about the
user, not about any one project's data — the same argument `ui/engine.VIEWS`
makes.

**Nothing in here is a credential.** A password or token is typed per session
and held on the pool in memory; a key file stays where the vendor put it and
at most its path is recorded. So this file can be read aloud, and a stolen
copy buys an account name and a warehouse name.

**A provider is the shape of a connection to one kind of warehouse**: the
fields it records, which are required, how it can sign in and what each way
needs typed, and where the vendor's own tools keep the same facts. The dialog
draws a provider's form from this and nothing else, which is how the list of
providers in the window and the list of connectors in the package cannot
disagree (`CONNECTORS.md` §2.13). Adding a warehouse adds one `Provider` here
and one module beside this file; nothing in here imports a driver.

**A vendor's own configuration is read for suggestions and never written.**
The Snowflake CLI, dbt and the Python connector all parse ``connections.toml``,
and a data-harmonization tool rewriting a file three other tools depend on is
how a colleague's ``snowsql`` stops working. The user's call was *empty fields
by default, and offer what exists*; :func:`suggestions` is the second half.
"""

from __future__ import annotations

import configparser
import os
import sys
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import yaml

#: Where portia keeps the user's connections. YAML rather than TOML because the
#: standard library reads TOML and does not write it, and one more dependency
#: for one small file is not worth its weight.
CONNECTIONS = Path.home() / ".config" / "portia" / "connections.yaml"


# --- what a provider is ----------------------------------------------------------


@dataclass(frozen=True)
class Field:
    """One thing a connection to this provider records, as the form asks it."""

    key: str
    label: str
    required: bool = False
    #: The example the empty box shows; the one explanation a field gets
    #: (`CONNECTOR.md` §2.12a, revised: no line under the label).
    placeholder: str = ""
    #: Drawn in the mono face: an identifier rather than a name.
    mono: bool = True


@dataclass(frozen=True)
class Auth:
    """One way a provider signs in, and what that way needs typed per session."""

    key: str
    label: str
    #: The label of the one box drawn under the picked connection, or ``None``
    #: when the method needs nothing typed (browser SSO, application default
    #: credentials). Whatever is typed is held on the pool and written nowhere.
    secret: str | None = None
    #: Fields that are required only under this method — a key file's path
    #: for a service account, which a browser sign-in has no use for.
    needs: tuple[str, ...] = ()


@dataclass(frozen=True)
class Provider:
    """The shape of a connection to one kind of warehouse."""

    kind: str
    label: str
    #: The glyph the dialog draws beside it. Kind, never rank.
    icon: str
    fields: tuple[Field, ...]
    #: In the order offered; the first is the default.
    auth: tuple[Auth, ...]
    #: One line saying who into what, for a saved connection's row.
    summary: Callable[[Connection], str]
    #: Connections the vendor's own tools already describe, offered to fill from.
    suggest: Callable[[], list[Connection]]
    #: The field keys holding the first two of portia's three levels — what
    #: the brief offers as the place to start building (`pool.opens_on`).
    #: ``database.schema`` on Snowflake, ``project.dataset`` on BigQuery.
    levels: tuple[str, str] = ("database", "schema")

    @property
    def keys(self) -> tuple[str, ...]:
        return tuple(f.key for f in self.fields)

    @property
    def required(self) -> tuple[str, ...]:
        return tuple(f.key for f in self.fields if f.required)

    @property
    def default_auth(self) -> str:
        return self.auth[0].key

    def auth_of(self, key: str) -> Auth:
        for method in self.auth:
            if method.key == key:
                return method
        raise ValueError(
            f"{self.label} signs in with one of {', '.join(a.key for a in self.auth)}, not {key!r}"
        )


# --- a connection ----------------------------------------------------------------


@dataclass(frozen=True, init=False)
class Connection:
    """One named way into one warehouse: a kind, a sign-in method, and the provider's fields.

    The fields are reachable as attributes (``connection.account``) and an
    absent one is ``None``, so an adapter reads its own provider's fields the
    way it always did; a key no provider knows is an error rather than a quiet
    ``None``.
    """

    name: str
    kind: str
    auth: str
    fields: dict[str, str]

    def __init__(self, name: str, *, kind: str = "snowflake", auth: str | None = None, **fields):
        provider = PROVIDERS.get(kind)
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "auth", auth or (provider.default_auth if provider else ""))
        object.__setattr__(
            self,
            "fields",
            {
                k: str(v).strip()
                for k, v in fields.items()
                if v not in (None, "") and str(v).strip()
            },
        )

    def __getattr__(self, item: str):
        fields = self.__dict__.get("fields") or {}
        if item in fields:
            return fields[item]
        provider = PROVIDERS.get(self.__dict__.get("kind", ""))
        if provider is not None and item in provider.keys:
            return None
        raise AttributeError(item)

    @property
    def provider(self) -> Provider:
        try:
            return PROVIDERS[self.kind]
        except KeyError:
            raise ValueError(
                f"no connector for {self.kind!r}; portia knows {', '.join(PROVIDERS)}"
            ) from None

    def check(self) -> None:
        """Refuse a connection missing a required field, naming it."""
        provider = self.provider
        missing = [f for f in provider.required if not self.fields.get(f)]
        if missing:
            wanted = ", ".join(f.label.lower() for f in provider.fields if f.required)
            raise ValueError(
                f"A {provider.label} connection needs a name, {wanted}. "
                f"Missing: {', '.join(missing)}."
            )
        method = provider.auth_of(self.auth)
        for key in method.needs:
            if not self.fields.get(key):
                label = next(f.label.lower() for f in provider.fields if f.key == key)
                raise ValueError(f"{method.label} needs a {label}. Missing: {key}.")

    @property
    def needs_secret(self) -> bool:
        """Whether opening a session needs something typed: a password or a token."""
        return self.provider.auth_of(self.auth).secret is not None

    @property
    def secret_label(self) -> str | None:
        """What the thing typed is called, or ``None`` when nothing is."""
        return self.provider.auth_of(self.auth).secret

    def as_record(self) -> dict:
        """The fields, with the default kind and the default auth left out."""
        record: dict = dict(self.fields)
        if self.kind != DEFAULT_KIND:
            record["kind"] = self.kind
        provider = PROVIDERS.get(self.kind)
        if provider is None or self.auth != provider.default_auth:
            record["auth"] = self.auth
        return record


# --- the file --------------------------------------------------------------------


def load(path: Path | None = None) -> dict[str, Connection]:
    """Every connection this build can read, by name. An absent file is none.

    ``path`` defaults to :data:`CONNECTIONS` **at call time**, so a test can
    point the module at a temporary file by patching the constant.

    **An entry of a kind this build has no provider for is left out, not raised
    on** *(2026-09-07)*. The file is the user's and is shared between every
    build of portia on the machine: a BigQuery connection written by a checkout
    with that connector opened a checkout without it on a `TypeError` from
    here, before any screen was drawn. What it cannot read it also must not
    lose — `save` and `remove` rewrite the file from `_entries`, the raw
    records, so the entry survives the next write.
    """
    return {
        name: connection
        for name, record in _entries(path).items()
        if (connection := _connection(name, record)) is not None
    }


def _connection(name: str, record: object) -> Connection | None:
    """The entry as a `Connection`, or ``None`` when this build cannot read it."""
    if not isinstance(record, dict) or record.get("kind", DEFAULT_KIND) not in PROVIDERS:
        return None
    try:
        return Connection(name=name, **record)
    except (TypeError, ValueError):
        return None


def _entries(path: Path | None = None) -> dict[str, dict]:
    """The file's records as written, readable by this build or not."""
    try:
        raw = yaml.safe_load((path or CONNECTIONS).read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        return {}
    entries = raw.get("connections") or {}
    return dict(entries) if isinstance(entries, dict) else {}


def names(path: Path | None = None) -> list[str]:
    return sorted(load(path))


def get(name: str, path: Path | None = None) -> Connection:
    """The named connection, or ``KeyError`` — the caller says what is known."""
    return load(path)[name]


def save(connection: Connection, path: Path | None = None) -> Path:
    """Add or replace one connection. Refuses one with a required field missing."""
    connection.check()
    target = path or CONNECTIONS
    entries = _entries(target)
    entries[connection.name] = connection.as_record()
    _write(entries, target)
    return target


def remove(name: str, path: Path | None = None) -> bool:
    """Forget a connection. Returns whether there was one to forget."""
    target = path or CONNECTIONS
    entries = _entries(target)
    if name not in entries:
        return False
    del entries[name]
    _write(entries, target)
    return True


def _write(entries: dict[str, dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"connections": entries}, sort_keys=True), encoding="utf-8")


def suggestions() -> list[Connection]:
    """What every provider's own tools already know, as portia's shape, for the form to offer."""
    out: list[Connection] = []
    for provider in PROVIDERS.values():
        out += provider.suggest()
    return out


# --- Snowflake (`docs/CONNECTOR.md`) ---------------------------------------------

#: How a Snowflake session is opened. ``browser`` is SSO through the account's
#: identity provider, and it is the default because it stores nothing anywhere.
#: **An account with no identity provider cannot use it**: a fresh dev or trial
#: account answers ``390190 … SAML Identity Provider account parameter`` to an
#: ``externalbrowser`` login (found on the first real attempt, 2026-09-04). The
#: other two take a secret **typed per session and never written**: the
#: account password (the connector runs the account's MFA on top of it), or a
#: programmatic access token.
BROWSER, PASSWORD, TOKEN = "browser", "password", "token"
AUTH_METHODS = (BROWSER, PASSWORD, TOKEN)

#: What a Snowflake connection must say (`CONNECTOR.md` §2.4). ``user`` is
#: required because browser SSO still wants the login name — the connector
#: refuses an empty one unless the authenticator is OAuth, a PAT or workload
#: identity, and ``externalbrowser`` is none of those.
REQUIRED = ("name", "account", "user", "warehouse")
OPTIONAL = ("role", "database", "schema")


def snowflake_connections_file() -> Path:
    """Where the Snowflake connector reads its ``connections.toml``.

    The connector's own rule, restated rather than imported so this module
    works without the extra: ``$SNOWFLAKE_HOME`` if set, else ``~/.snowflake``
    when it exists, else the platform's application-support directory.
    """
    home = os.environ.get("SNOWFLAKE_HOME")
    if home:
        return Path(home).expanduser() / "connections.toml"
    dot = Path.home() / ".snowflake"
    if dot.is_dir():
        return dot / "connections.toml"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "snowflake" / "connections.toml"
    return Path.home() / ".config" / "snowflake" / "connections.toml"


def snowflake_suggestions(path: Path | None = None) -> list[Connection]:
    """Connections Snowflake's own file describes, as portia's shape, for the form to offer.

    Fields the file spells differently are mapped (``username`` is ``user``);
    a password or key in it is **dropped**, because portia stores no secret.
    An entry missing a required field is still offered — it fills what it can,
    and the form says what is left.
    """
    target = path or snowflake_connections_file()
    try:
        raw = tomllib.loads(target.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError, UnicodeDecodeError):
        return []
    out = []
    for name, fields in raw.items():
        if not isinstance(fields, dict):
            continue
        out.append(
            Connection(
                name=str(name),
                kind=SNOWFLAKE.kind,
                account=str(fields.get("account") or ""),
                user=str(fields.get("user") or fields.get("username") or ""),
                warehouse=str(fields.get("warehouse") or ""),
                role=_opt(fields.get("role")),
                database=_opt(fields.get("database")),
                schema=_opt(fields.get("schema")),
            )
        )
    return out


def _opt(value: object) -> str | None:
    return str(value) if value not in (None, "") else None


SNOWFLAKE = Provider(
    kind="snowflake",
    label="Snowflake",
    icon="ac_unit",
    fields=(
        Field("account", "Account", True, "myorg-myaccount"),
        Field("user", "User", True, "jane.doe"),
        Field("warehouse", "Warehouse", True, "COMPUTE_WH"),
        Field("role", "Role"),
        Field("database", "Database"),
        Field("schema", "Schema"),
    ),
    auth=(
        Auth(BROWSER, "Browser sign-in"),
        Auth(PASSWORD, "Password", secret="Password"),
        Auth(TOKEN, "Access token", secret="Access token"),
    ),
    summary=lambda c: f"{c.user or ''}@{c.account or ''}",
    suggest=snowflake_suggestions,
)

# --- BigQuery (`docs/CONNECTORS.md` §8) -------------------------------------------

#: How a BigQuery client is signed in. ``adc`` is Application Default
#: Credentials, what ``gcloud auth application-default login`` leaves on the
#: machine, and it is the default because it stores nothing in portia and is
#: what every other Google tool on the laptop already uses. ``service_account``
#: reads a key file whose **path** is recorded and whose contents never are.
#: ``token`` is an OAuth access token typed per session, which is what
#: ``gcloud auth print-access-token`` hands out and what a CI job holds.
ADC, SERVICE_ACCOUNT, ACCESS_TOKEN = "adc", "service_account", "token"


def gcloud_config_dir() -> Path:
    """Where the Google Cloud SDK keeps its configurations: ``$CLOUDSDK_CONFIG`` or ``~/.config/gcloud``."""
    override = os.environ.get("CLOUDSDK_CONFIG")
    return Path(override).expanduser() if override else Path.home() / ".config" / "gcloud"


def gcloud_default_project(config_dir: Path | None = None) -> str | None:
    """The project the active gcloud configuration names, or nothing.

    Read for a suggestion and never written, the same rule as Snowflake's
    file: ``active_config`` names the configuration and
    ``configurations/config_<name>`` holds ``[core] project = …``.
    """
    root = config_dir or gcloud_config_dir()
    try:
        active = (root / "active_config").read_text(encoding="utf-8").strip() or "default"
    except (OSError, UnicodeDecodeError):
        active = "default"
    try:
        lines = (
            (root / "configurations" / f"config_{active}").read_text(encoding="utf-8").splitlines()
        )
    except (OSError, UnicodeDecodeError):
        return None
    section = ""
    for line in lines:
        line = line.strip()
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
        elif section == "core" and line.startswith("project"):
            _, _, value = line.partition("=")
            return value.strip() or None
    return None


def bigquery_suggestions(config_dir: Path | None = None) -> list[Connection]:
    """One connection, ``gcloud``, on the SDK's active project — when there is one."""
    project = gcloud_default_project(config_dir)
    if not project:
        return []
    return [Connection(name="gcloud", kind=BIGQUERY.kind, project=project)]


BIGQUERY = Provider(
    kind="bigquery",
    label="BigQuery",
    icon="cloud_queue",
    fields=(
        Field("project", "Project", True, "my-gcp-project"),
        Field("dataset", "Dataset", False, "analytics"),
        Field("location", "Location", False, "EU"),
        Field("keyfile", "Key file", False, "~/keys/portia-sa.json"),
        Field("projects", "Also browse", False, "bigquery-public-data, another-project"),
    ),
    auth=(
        Auth(ADC, "Google Cloud SDK sign-in"),
        Auth(SERVICE_ACCOUNT, "Service account key file", needs=("keyfile",)),
        Auth(ACCESS_TOKEN, "Access token", secret="Access token"),
    ),
    summary=lambda c: f"{c.project or ''}" + (f" · {c.location}" if c.location else ""),
    suggest=bigquery_suggestions,
    levels=("project", "dataset"),
)

# --- PostgreSQL (`docs/CONNECTORS.md` §9) -----------------------------------------

#: How a PostgreSQL connection signs in. ``password`` is typed per session and
#: held on the pool. ``pgpass`` sends none and lets libpq look where ``psql``
#: looks: ``~/.pgpass``, ``PGPASSWORD``, or nothing on a server that trusts the
#: socket. It is the method with nothing to type, which is what a session with
#: no window needs.
PGPASS = "pgpass"


def pg_service_file() -> Path:
    """Where libpq reads named services: ``$PGSERVICEFILE`` or ``~/.pg_service.conf``."""
    override = os.environ.get("PGSERVICEFILE")
    return Path(override).expanduser() if override else Path.home() / ".pg_service.conf"


def postgres_suggestions(path: Path | None = None) -> list[Connection]:
    """The services libpq's own file describes, as portia's shape, for the form to offer.

    Read and never written, the rule the other two follow. A ``password`` line
    in it is dropped.
    """
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read_string((path or pg_service_file()).read_text(encoding="utf-8"))
    except (OSError, configparser.Error, UnicodeDecodeError):
        return []
    return [
        Connection(
            name=name,
            kind=POSTGRES.kind,
            host=_opt(service.get("host")),
            port=_opt(service.get("port")),
            database=_opt(service.get("dbname")),
            user=_opt(service.get("user")),
            sslmode=_opt(service.get("sslmode")),
        )
        for name, service in parser.items()
        if name != parser.default_section
    ]


POSTGRES = Provider(
    kind="postgres",
    label="PostgreSQL",
    icon="storage",
    fields=(
        Field("host", "Host", True, "localhost"),
        Field("port", "Port", False, "5432"),
        Field("database", "Database", True, "shop"),
        Field("user", "User", True, "jane"),
        Field("schema", "Schema", False, "public"),
        Field("sslmode", "SSL mode", False, "require"),
    ),
    auth=(
        Auth(PASSWORD, "Password", secret="Password"),
        Auth(PGPASS, "Password file"),
    ),
    summary=lambda c: f"{c.user or ''}@{c.host or ''}/{c.database or ''}",
    suggest=postgres_suggestions,
)

#: Every warehouse portia can connect to, by kind. The dialog's provider list
#: and `connectors.module_for` both read this, in this order.
PROVIDERS: dict[str, Provider] = {
    SNOWFLAKE.kind: SNOWFLAKE,
    BIGQUERY.kind: BIGQUERY,
    POSTGRES.kind: POSTGRES,
}

#: What an entry with no ``kind`` is: every file written before there was a
#: second provider names a Snowflake connection.
DEFAULT_KIND = SNOWFLAKE.kind

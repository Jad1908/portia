<!-- Appended to the brief a host pulls, on a warehouse project (`cli/serve.py`). {label} is the connection's name. -->
## Signing in to `{label}`

This connection signs in the way the user's own Snowflake `connections.toml` says. The
first tool call that reaches the warehouse opens the session by itself. Nothing is typed
into this chat and you are never given a credential. You have no reason to open that
file: `uv run python -m portia.cli.connect suggest` lists what it offers, without secrets.

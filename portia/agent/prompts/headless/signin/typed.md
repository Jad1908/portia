<!-- Appended to the brief a host pulls, on a warehouse project (`cli/serve.py`). {label} is the connection's name, {what} is "password" or "access token". -->
## Signing in to `{label}`

**This connection cannot be opened from here.** It signs in with a {what} typed per
session, and this session has nowhere to type one. Do not ask the user for it and do not
accept one pasted into the chat: a secret in a conversation is in its transcript.

Every tool that reaches the warehouse will be refused until the project names a connection
that signs in another way. Tell the user now, and offer the two that work:

- `browser`: their company sign-in, in a browser window.
- `file`: the entry they already keep in Snowflake's `connections.toml`, by name.
  `portia connect suggest` lists those entries, then
  `connect add <name> --auth file` and `connect use <name>`.

What the catalog already holds still reads: `get_context`, `describe_source`, `read_spec`.

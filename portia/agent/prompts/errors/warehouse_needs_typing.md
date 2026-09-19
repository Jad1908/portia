<!-- What a host's model reads in place of `SecretRequired` (`cli/serve.py`). {label} is the connection, {what} is "password" or "access token". -->
`{label}` signs in with a {what} typed per session, and this session has nowhere to type
one. Nothing was run.

Do not ask the user for it and do not accept one pasted into the chat. Tell them the
project needs a connection that signs in another way, and offer both:

- `browser`: their company sign-in, in a browser window.
- `file`: the entry they already keep in Snowflake's `connections.toml`, by name.
  `portia connect suggest` lists those entries, then
  `connect add <name> --auth file` and `connect use <name>`.

Do not call this tool again until they say the connection has changed.

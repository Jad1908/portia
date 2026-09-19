<!-- Appended to the brief a host pulls when the project names a connection this machine cannot open (`cli/serve.py`). {label} is the name, {why} is what portia said. -->
## The warehouse connection `{label}` is not usable on this machine

{why}

Until that is fixed the tools run on local files, and every source this project scoped
from the warehouse will be refused. Tell the user now. `uv run python -m
portia.cli.connect list` shows the connections this machine has, and `connect suggest`
shows what their own tools already describe.

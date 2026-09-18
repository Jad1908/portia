<!--
`get_context` as a host that is not portia is offered it (`cli/serve.py`). The app's
description says the brief is already in the system prompt, which is true there and
false here: nothing pushes it, so this tool is how it arrives.
-->
The project brief: what this data is for, the groups, one line per source, every
spec in build order with its layer and what it reads, and which engine the data
lives on with what a question costs there.

CALL THIS FIRST, before any other portia tool, in every session. Nothing else gives
you the list of sources and specs, and without it you will ask the user for names
portia already holds.

Call it again after a source is indexed or interpreted, a group is recorded, or a
spec gains a step. It is cheap: it reads the catalog and runs nothing.

It carries no per-source detail on purpose. Climb to `describe_source` for that.

<!--
What a host's model reads when `portia-hook guard` refuses `record_step` or `run_spec` in
the reply that indexed data (`cli/hook.py`, `docs/HEADLESS.md` §4.8). {command} is the
shell command that indexed, as it was run.
-->
This reply ran `{command}`, so it is a reading job, and a reading job does not build.
`record_step` and `run_spec` are refused until the user's next message.

In portia's window, indexing is a job of its own that has no way to build. Here it is the
rest of the reply that indexed, and the same rule holds for the same reason: a step
written while reading is a fix nobody asked for, made before the person has seen what
was read.

Finish the read. A problem you found in a source goes in its `note`, through
`set_interpretation`. Then tell the user what you read and what you would build first.
The build starts when they answer.

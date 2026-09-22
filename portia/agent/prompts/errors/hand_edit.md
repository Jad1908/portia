<!--
What a host's model reads when `portia-hook guard` refuses an edit (`cli/hook.py`).
{path} is the file it tried to write, relative to the project.
-->
`{path}` is a file portia writes, so it is not edited by hand.

A step in a spec is there because `record_step` ran it and measured what came out. A hand
edit puts a step in the record that nothing measured, and the compiled SQL, the model's
catalog entry and the graph then describe a table that was never built.

Use the tool that owns the file:

- a spec or its compiled `.sql`: `record_step`, with `supersedes` to replace a step
- a catalog entry, a summary or a note: `set_interpretation`
- a group: `set_group`
- a finding: `record_finding`

If the user asked for this edit themselves, tell them what it would skip and let them make
it. Their own editor is not guarded, and `acknowledge` in a spec is theirs to write.

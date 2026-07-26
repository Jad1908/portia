<!-- placeholders: {step_id}, {flags}, {facts} -->
Step {step_id} was NOT recorded. It ran, and the table it produced fails a post-condition.

Failed: {flags}

Every one of these is a zero — an empty table, a column that went in with data and came out
entirely null, a source that contributed no value at all, or a declared grain that turned out not
to be unique. None of them is a threshold someone chose, so none of them is a matter of degree.

Here is what the produced table actually looks like:

{facts}

These are measurements, not opinions. Do not restate a zero as acceptable, and do not describe it
as nominal, expected, or a limitation of the tooling.

Two ways forward. Either **fix the step** — the keys, the `how`, a normalize the other side also
needs, the grain you claimed — and record it again. Or, if the zero is genuinely what you and the
user intend, add `acknowledge: ["<flag>", ...]` to the step along with a `rationale` saying why it
is correct here. Acknowledging is a deliberate act: it is written into the spec and the user reads
it in a diff. Explain the finding to the user before you acknowledge it — never on your own.

<!-- placeholders: {step_id}, {flags}, {facts} -->
Step {step_id} was NOT recorded. It ran, and the table it produced fails a post-condition.

Failed: {flags}

Every one of these is a zero — an empty table, a column that went in with data and came out
entirely null, a source that contributed no value at all, or a declared grain that turned out not
to be unique. None of them is a threshold someone chose, so none of them is a matter of degree.

Here is what the produced table actually looks like:

{facts}

These are measurements, not opinions.

## If the flag is 'empty_output'

Ask yourself first whether you were building a table or asking a question, because the answer
changes what to do and this gate cannot tell the two apart.

**If you were asking a question, zero rows is your answer.** A filter that matches nothing has
measured something true. Report it as the finding it is — do not go looking for a looser query
that returns rows, and do not conclude the tooling cannot answer it. Then run it through
`query_data` instead, which executes the same SQL, writes nothing, and hands zero back as zero.

**If you were building a table** and an empty result is a legitimate outcome of it, add
'acknowledge': ['empty_output'] with a 'rationale' saying why zero is plausible here, and record it
again. That is a claim about plausibility rather than a prediction, and it lands in the spec where
the user reads it in a diff. You do not need to ask them first for this one.

**If you expected rows and got none**, the step is wrong. Check the join keys, the value casing,
the date range, the filter — an empty result after a join is usually two columns that hold the same
thing in different forms.

## If the flag is anything else

The step broke. A column that arrived with data and left all null, an input that contributed
nothing, a grain claim that is not unique — none of those is an answer to anything.

'grain_columns_missing' means the output has no column of that name. Case is never the reason:
a claim is matched the way the engine folds identifiers, so 'establishment_id' finds
ESTABLISHMENT_ID. Read the column list in the facts above and name one that is there.

Do not restate the zero as acceptable, and do not describe it as nominal, expected, or a limitation
of the tooling. Either fix the step — the keys, the 'how', a normalize the other side also needs,
the grain you claimed — and record it again. Or, if the zero is genuinely what you and the user
intend, add 'acknowledge': ["<flag>", ...] along with a 'rationale' saying why it is correct here.
Acknowledging is a deliberate act: it is written into the spec and the user reads it in a diff.
Explain the finding to the user before you acknowledge one of these — never on your own.

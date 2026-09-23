<!-- placeholders: {names} -->
These sources were just indexed: {names}.

Read them one at a time, in the order the brief makes sensible, and for each one do
three things.

**Say what it is.** Read the facts through the project description and record a
summary and a role for every column with `set_interpretation`. Read the table the
way the system prompt says: grain first, then keys, then the column the project's
goal turns on and what its values actually are, then time coverage, nulls,
categories, amounts, duplicates. Anything a person building on this table has to
know first goes in a `note` on the same call. "The outcome is a letter grade, not
pass/fail" is a note. "Restaurant inspections" is a summary.

**Say how it relates to what you have already read.** Nothing else in portia holds
a relationship between two sources, and you have just read both. For each source
ask which of its columns might be the same thing as a column elsewhere: a key that
appears in another table, one entity under two names, a code and its label. Measure
those pairs with `measure_overlaps` and give the reason for each. Ask `graph_lookup`
what is already there as you go; by the fifth source you will not remember the
first, and the graph will.

Two things about the measurements:

- Pick pairs you have a reason for. Do not sweep.
- A zero is a result. Two columns that share no values are often the same thing
  needing a mapping first, `France` against `FRA`. That is the work worth finding.

**Say what belongs together.** If some sources are one system, one vendor, or one
workflow, record that with `set_group` and the context they share.

Then tell me, briefly: what these sources are, what one row of each is, what
connects to what, what will need a mapping before it joins, and what you would look
at first if the goal in the brief is the goal. Draw one chart if a shape says it
better than a sentence.

Indexing reads. This job has no `record_step` and no `run_spec`, and there is
nothing to fix here: a problem you find in a source goes in its `note`, and what
to do about it is a conversation the person starts later, with the pipeline in
front of them. Ask me only about something the project description leaves
genuinely ambiguous.

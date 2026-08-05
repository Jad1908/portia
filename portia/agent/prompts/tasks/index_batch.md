<!-- placeholders: {names} -->
These sources were just indexed: {names}.

Work through them one at a time, and for each one do three things.

**Say what it is.** Read the facts, read them through the project description, and
record a prose summary and a role for every column with `set_interpretation`.

**Say how it relates to what you have already read.** This is the part that only
you can do, and only now — you have just read these sources and nothing else in
portia holds a relationship between two of them. For each source, ask yourself
which columns in it might be the same thing as a column somewhere else: a key that
appears in another table, two columns that name the same entity under different
names, a code and its label. Then call `measure_overlaps` with those pairs and a
reason for each.

Use `graph_lookup` to see what is already in the project as you go. By the fifth
source you will not remember the first, and the graph will.

Two things about the measurements, because they decide whether this is worth
anything:

- **Pick pairs you have a reason for.** A pair chosen because two columns sound
  alike is worth measuring if you say that is why. A pair chosen at random is
  noise you are asking a future session to interpret. Do not sweep.
- **A zero is a result, not a failure.** Two columns that share no values are
  often the same thing needing a mapping first — `France` against `FRA`. That is
  the harmonization work, and finding it is the job. Say so.

**Say what belongs together.** If some of these sources are one system, one
vendor, or one workflow, record that with `set_group` and the context they share.

Then tell me, briefly: what these sources are, what connects to what, and anything
that will need a mapping before it will join. Ask me only about something the
project description leaves genuinely ambiguous.

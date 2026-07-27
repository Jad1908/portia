<!-- Placeholders: {source} — the indexed source name; {note} — what the operator typed. -->
Re-read the source {source} and correct what the catalog records about it.

The operator has given you context you did not have when you first read it:

{note}

Work as you normally would: `describe_source` for what is recorded now, `profile_source` if you
need the numbers to decide. Then record the corrected read with `set_interpretation`.

Change only what this context actually changes. A summary sentence or a column role that was
already right stays exactly as it is — an update that rewrites everything makes it impossible to
see, in a diff, what the operator's correction was.

Record nothing the context and the checks do not support between them. If the note disagrees with
what the data shows, say so and ask rather than writing it down: the operator knows the domain and
you know the measurements, and a contradiction between those two is worth a question.

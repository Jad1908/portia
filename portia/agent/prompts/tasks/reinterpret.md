<!-- placeholders: {source}, {note} -->
Re-read the source {source} and correct what the catalog records about it.

The operator has given you context you did not have when you first read it:

{note}

`describe_source` for what is recorded now, `profile_source` if you need the numbers, then `set_interpretation` with the corrected read. Change only what this context changes, so the diff shows the correction. If the context is a fact about the table rather than a change to its read, add it as a note and keep the summary. If it disagrees with what the data shows, say so and ask: they know the domain and you have the measurements.

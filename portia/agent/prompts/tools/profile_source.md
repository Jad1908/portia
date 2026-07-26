One source's full measured facts: per-column dtype, null rate, distinct count,
min/max, quartiles, sample values and quality flags.

Expensive — the detailed rung. Call it when you need the actual numbers:
interpreting a source, judging whether a key is usable, quantifying a data-quality
problem you are about to raise. Not for browsing.

'source' is an indexed source, or a table an earlier step produced, written
'<spec path>#<step id>'. Use the step form to see what a table looks like AFTER
your transforms ran — did the column you normalized actually change, is the key
you are about to join on still what you think it is. A step's output has no
catalog entry, so that form returns measurements only: no summary, no roles.

These facts are unranked. Deciding which of them matter is your job.

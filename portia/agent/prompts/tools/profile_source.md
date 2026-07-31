One source's full measured facts: per-column dtype, null rate, distinct count,
min/max, quartiles, sample values and quality flags.

Expensive — the detailed rung. Call it when you need the actual numbers:
interpreting a source, judging whether a key is usable, quantifying a data-quality
problem you are about to raise. Not for browsing.

'source' is any of three things: an indexed source, ANOTHER MODEL in this project
(just its name — the spec that builds it is found for you), or a table an earlier
step in the spec you are writing produced, written '<spec path>#<step id>'.

Use the last two to see what a table looks like AFTER transforms ran — did the
column you normalized actually change, is the key you are about to join on still
what you think it is. Neither has a catalog entry, so they return measurements
only: no summary, no roles.

These facts are unranked. Deciding which of them matter is your job.

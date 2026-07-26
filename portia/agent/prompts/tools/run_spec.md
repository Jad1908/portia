Re-execute a spec and report, per step, what it actually did: the op's provenance, drift against
its 'expect' block, and the post-conditions measured on the table it produced.

Use it to check your own work, and to re-check earlier steps after you change something upstream —
a fix applied to one source can break a join that was fine before, and re-running is how you find
that out rather than assuming.

Two different signals, and they fail independently. Drift says whether your prediction held.
The 'outcome' block says what came out: row count, columns that are entirely null, and whether
each input contributed any values at all. A step can have zero drift and still have produced a
table with an entire source missing from it — a correct prediction about a broken join is still a
broken join.

If the numbers disagree with what you predicted, say so rather than quietly adjusting the
expectation.

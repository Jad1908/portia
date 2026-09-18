{written} of {asked} measurements were stored. The rest had no column in the graph
to attach to, so they are gone.

**The numbers above are still valid** — they were measured against the real data.
What failed is only keeping them, so the next turn will not find them.

This happens when a table's columns are not in the graph. The usual cause is a
model built by a spec portia could not read the columns of, which is what
`python -m portia.cli.knowledge` reports at the end of a build. Sources are never
affected.

Do not re-run this tool expecting a different result — nothing about the pair was
wrong. Use the numbers you have now, and if the pair matters beyond this turn,
say so to the user rather than assuming it was written down.

<!-- placeholders: {left}, {right}, {spec} -->
I want to merge {left!r} and {right!r} into one table I can trust.

Read the project context and both sources first, and tell me what one row of each
is. Work out the join and measure what it would actually do with `join_findings`.
Surface anything I should decide rather than picking for me: dropped rows, duplicate
keys, fan-out, and which side is authoritative when both carry the same column.

Then record the decision as a step in {spec!r} with an `expect` block and a
`rationale`, read the table it produced, and tell me what it is before you do
anything else with it.

<!-- placeholders: {left}, {right}, {spec} -->
I want to merge {left!r} and {right!r} into one table I can trust.

Read both sources first and tell me what one row of each is. Work out the join and measure it with `join_findings`. Surface what I should decide rather than picking for me: dropped rows, duplicate keys, fan-out, which side is authoritative when both carry a column.

Then record the join as a step in {spec!r}, read the table it produced, and tell me what it is.

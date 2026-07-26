Execute a decided step, measure the table it produces, and append it to the spec — the durable,
re-runnable record of what was done to the data and why.

RECORDING RUNS IT. This is not a save: the step is executed before anything is written, and the
result tells you what the produced table actually looks like — row count, any column that came out
entirely null, and whether each input source actually put values into the output. Read that block.
A join can match nothing, leave every column from one side null, and still report exactly the row
count you predicted; the numbers agreeing is not the same as the table being right.

The step is a dict: 'id', 'op' ('join' or 'normalize'), the op's fields, an 'expect' block of
provenance values you predict, and a 'rationale' explaining the decision.

STEPS CHAIN: a step's output is stored under its 'id', and a later step may name that id as its
'left', 'right' or 'input' to receive the resulting table. That is how multi-hop work is built —
join A to B, then join THAT result to C. You do not need an external tool for this.

join fields: 'left', 'right', 'keys' (or 'left_on'/'right_on' when the key columns are named
differently), 'how'. Note 'keys', not 'on' — 'on' is a reserved boolean in YAML.

normalize fields: 'input', and 'transforms' as a list of
{'column': <name>, 'op': 'strip'|'lower'|'to_numeric'|'to_string'} — the key is 'op', not
'transform'.

DECLARE THE GRAIN. Optionally add 'grain': a list of output columns naming what one row of the
result is meant to be, e.g. one row per customer per day. You state the claim; the engine measures
whether the table is actually unique on it and shows you the duplicated keys if not. This is the
only thing that catches a join quietly multiplying rows — a fan-out that inflates a total by a few
percent produces a table that looks entirely plausible. Declare it whenever the step is meant to
produce a table at a particular grain, which for a training table is always.

WHAT REFUSES TO BE WRITTEN. A step whose output hits a zero is not recorded: an empty table, a
column that went in with data and came out entirely null, a source that contributed no value at
all, or a declared grain that is not unique. None of these is a threshold — they are zeros, so
there is nothing to weigh. Fix the step and record it again. If a zero is genuinely intended, add
'acknowledge': ['<flag>'] with a 'rationale' saying why, and tell the user before you do — the
acknowledgement is written into the spec and they will read it in the diff.

STEPS ARE APPEND-ONLY. A recorded step cannot be revised and its id cannot be reused. If a
prediction turns out wrong, that is a finding to report, not an id to bump.

Base 'expect' on what the check measured; run_spec will hold you to it.

Append a decided step to the spec — the durable, re-runnable record of what was
done to the data and why.

The step is a dict: 'id', 'op' ('join' or 'normalize'), the op's fields, an
'expect' block of provenance values you predict, and a 'rationale' explaining the
decision.

STEPS CHAIN: a step's output is stored under its 'id', and a later step may name
that id as its 'left', 'right' or 'input' to receive the resulting table. That is
how multi-hop work is built — join A to B, then join THAT result to C. You do not
need an external tool for this.

join fields: 'left', 'right', 'keys' (or 'left_on'/'right_on' when the key columns
are named differently), 'how'. Note 'keys', not 'on' — 'on' is a reserved boolean
in YAML.

normalize fields: 'input', and 'transforms' as a list of
{'column': <name>, 'op': 'strip'|'lower'|'to_numeric'|'to_string'} — the key is
'op', not 'transform'.

Base 'expect' on what the check measured; run_spec will hold you to it.

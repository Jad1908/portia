<!-- placeholders: {op}, {names}, {notes}, {expectable} — filled from handlers._validate_step.
     {notes} is a block of one line per field, or empty when nothing needs explaining. -->
Step not recorded: 'expect' may only name fields the op itself reports, and {op} does not report
{names}. A prediction about a field nothing measures is compared against nothing on every run, so
the spec would report drift forever — and drift that always fires is drift everyone learns to
ignore.

{notes}
Recording a step brings back two reports, and they answer different questions. The op's report says
what the operation did, and that is the one 'expect' is checked against. The 'outcome' block says
what the produced table turned out to be — it is measured fresh on every run rather than predicted,
which is why reading a number there does not make it something you can name in 'expect'.

{op} reports: {expectable}

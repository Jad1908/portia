<!-- placeholders: {op}, {missing}, {stray} — filled from handlers._validate_step. {stray} is a
     whole sentence or empty; it names a field the step DID send that belongs to a different op. -->
Step not recorded: a '{op}' step needs {missing}.

{stray}The three ops name the tables they read differently, and the names are not interchangeable: 'join'
reads 'left' and 'right', 'normalize' reads one table as 'input', and 'sql' reads a list of them as
'inputs', because one query may name several. Nothing else about the step needs to change — send it
again with the field this op uses.

<!-- placeholders: {size}, {budget}, {step} — filled from agent/tools._step_receipt. -->
The step WAS recorded: it is in the spec and its table is indexed. Do not record it again.

Its report was {size} characters even shortened, over the {budget} a tool will return, so this is
only the receipt: where the step was written, and the shape and flags of the table it produced.
To read the table it produced, call `profile_source` on '{step}' with 'columns' naming the ones
you need, or `describe_source` on the model.

<!-- placeholders: {field}, {tag} -->
`{field}` holds `{tag}`, which is tool-call markup, not prose. A parameter is closed with
`</parameter>`; a different closing tag runs the text on into the next parameter, which then
arrives empty or not at all.

Send the call again with `question`, `so` and `answer` each as its own parameter, holding a
sentence and no tags.

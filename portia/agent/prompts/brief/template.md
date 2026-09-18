<!-- placeholders: {project}, {groups}, {models}, {sources} -->
# This project

{project}
{groups}{models}
## Indexed sources

{sources}

That is the whole index — a name and a sentence each, and nothing about their shape. It is here so you know what exists and can tell which one a question is about. It is not enough to decide anything with.

For **which** source you want, and how it relates to the others — what connects to what, and where a built table's columns came from — ask `graph_lookup`. For one source's columns and roles call `describe_source`; for its measured facts call `profile_source`. A table under **Pipeline** is one portia builds from a spec: `read_spec` opens that spec without running it, and the table's name works in `profile_source`, `join_findings` and `query_data` exactly as a source's does.

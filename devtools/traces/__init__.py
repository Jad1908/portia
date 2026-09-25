"""A trace viewer: every chat and indexing job, read one step at a time.

    python -m devtools.traces                 # every log under sandbox/
    python -m devtools.traces ~/work/project  # or wherever you point it

Built for one job: open a run, see what the copilot did, and find where it
went wrong. It reads portia's own logs (`.portia/chats/`, `.portia/indexing/`)
and Claude Code's session files (`~/.claude/projects/`), because the benchmark
compares the two and both have to read the same way.

Three modules and a page. `logs` finds and reads the logs into one shape,
`notes` keeps the working set, notes and tags in one JSON file, and `server`
hands both to the page in `assets/` over a local HTTP server. **The viewer
scores nothing**: it shows what a log says, and a note is the reader's.
"""

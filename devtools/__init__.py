"""Developer tools for working *on* portia. Not part of the product.

Nothing under here ships: `pyproject.toml`'s wheel target packages `portia`
only, and no module in `portia/` may import this package. The dependency runs
one way — devtools reads portia's public seams (`portia.runlog`,
`portia.agent.events`) and portia never learns it is being read.

It is a tracked top-level directory rather than something under `sandbox/`
because `sandbox/` is gitignored whole: a tool you use to tune the prompts is
worth keeping across machines, while the throwaway projects it reads are not.
"""

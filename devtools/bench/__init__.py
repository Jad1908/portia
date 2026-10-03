"""The benchmark and the agent eval's tooling (`docs/BENCHMARK_EVAL.md`).

Reads what a run left behind and never judges it. Three pieces, one module each:

- `numbers` and `invariants`: the floor. A report over any log listing what
  broke a rule that holds on every route (§6.3): a number in a reply that
  appears in nothing the model saw, a zero acknowledged with nobody asked
  outside autopilot, a step recorded with no measurement under it. Counts
  and a list, with the sentence around each flag, so a person can look.
- `case`, `user` and `run`: a case file, the scripted user that holds its
  facts, and the runner that drives a case through portia with nobody at the
  keyboard (§7, §8).
- `baseline`: the same case through plain Claude Code (§5.5).
- `points`: one prepared project, one message, N runs, the outcomes counted (§6.4).

Everything here reads portia's public seams (`portia.runlog`, `portia.agent`)
and portia never learns it is being read (`devtools/__init__.py`). Every
number it prints is a count, k of N, or a fact off a log: no composite, no
rank (`CLAUDE.md` → facts versus judgment; `DESIGN.md` → kind, never rank).
"""

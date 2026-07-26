# portia — DESIGN.md

The single source of truth for every visual decision in the portia app. `VISION.md` owns
*structure and flows*, `PLAN.md` owns *direction*; this file owns *appearance*. Where they
overlap, this file wins on looks, `VISION.md` wins on layout and behavior.

## Overview

The look descends from a Raycast-style command-palette aesthetic — a quiet developer surface where
hairline borders and a surface-color ladder do the work shadows usually do, and color is held back
almost everywhere. It is shared with its sibling project, deliberately: the same restraint, the
same token vocabulary, the same 8px/Inter/hairline discipline.

Three things are specific to portia:

1. **It runs in a browser, not a window.** NiceGUI (Vue/Quasar) renders it, so tokens are CSS
   custom properties, icons come from an icon font rather than SF Symbols, and width rules are
   real breakpoints. Light and dark are equal first-class modes resolved from
   `prefers-color-scheme`, with a manual override.
2. **Monospace is load-bearing, not an accent.** dossier used mono in two places. portia is a data
   tool: column names, SQL, YAML, row counts, null rates, provenance numbers and table previews are
   all mono. Proportional type is for prose — summaries, rationales, the copilot talking.
3. **The visual system carries a correctness rule.** portia's whole premise is that deterministic
   code owns facts and the agent owns judgment (`CLAUDE.md`). That has a design consequence, stated
   once here and enforced throughout: **color and prominence communicate *kind*, never *rank*.**

What carries over unchanged: Inter with the `ss03` stylistic set, the 8px spacing base, the 4–16px
radius vocabulary, no drop shadows on persistent chrome, tight in-surface padding (12–24px, never
32px+), and a single scarce accent for the one primary action per view.

This is an **app**, not a marketing page. No hero, no footer, no pricing grid. The load-bearing
surface is a three-pane window: **files & artifacts** (left), **workflow** (middle — the spec as a
graph over its run report), and **transcript** (right).

**Key characteristics:**
- Mode-aware tokens; full light + dark. Dark elevation = surface ladder; light elevation = hairline borders.
- One teal primary action (`{colors.accent-primary}`), scarce — at most one per view.
- Hairline 1px borders (`{colors.hairline}`) carry every surface edge; no shadows on persistent chrome.
- Cool, faintly blue-tinted dark surfaces; cool off-white light surfaces.
- Inter + `font-feature-settings: "calt", "kern", "liga", "ss03"` site-wide; `{typography.mono}` for all data, code and measured numbers.
- Saturated color appears only as *status* — never as decorative chrome, and never as a ranking.
- The accent earns a soft tint in exactly three places: the **selected step card**, the **selected artifact row**, and a **focused input**. Nowhere else.

### The rule that is specific to this product

**Status color says what kind of thing happened. It never says how bad it is.**

The checks layer surfaces evidence generously and is forbidden from ranking, scoring or
prioritizing it — that judgment belongs to the agent and the human, who have the goal and the
context. A UI can undo that rule without writing a line of engine code: sort findings by severity,
paint a heat scale, size a badge by impact, and the screen has made the ranking the code refused
to make.

So:
- `{colors.error}` means **a blocking zero** — a fact with no threshold behind it.
- `{colors.warning}` means **drift** — a prediction that didn't hold.
- `{colors.accent-text}` means **an acknowledged override** — a human decision on the record.
- Nothing else is colored, nothing is sorted by severity, and no badge grows with a number.

Read `CLAUDE.md` → "facts vs judgment" before adding any visual that implies one finding matters
more than another.

## Colors

All tokens are **mode-aware**: each resolves to its Light or Dark value from `prefers-color-scheme`,
overridable by a manual toggle. Component specs reference the token name only; never a raw hex.

### Brand & Accent

| Token | Light | Dark | Use |
|---|---|---|---|
| `{colors.accent-primary}` | `#0C6B61` | `#0D9488` | Primary action fill (Run, primary confirm). Deep teal. |
| `{colors.accent-primary-pressed}` | `#0A564E` | `#0B7D72` | Pressed state — one notch deeper. |
| `{colors.on-accent}` | `#FFFFFF` | `#FFFFFF` | Label/icon on the accent fill. |
| `{colors.accent-text}` | `#0C6B61` | `#2DD4BF` | Accent as *foreground*: links, selected-row label, an acknowledged flag. |
| `{colors.accent-soft}` | `rgba(12,107,97,0.10)` | `rgba(13,148,136,0.18)` | Selected row fill, focused-input wash, accent badge background. |

> The hue lives in one token. Re-hueing the whole app away from its sibling project is a
> single-token change — deliberately, so it stays an easy decision to defer.

### Surface — Dark (lighter = closer)

| Token | Dark value | Use |
|---|---|---|
| `{colors.canvas}` | `#08090C` | Window background, left pane base. The dominant surface. |
| `{colors.surface}` | `#0D0F14` | Workflow pane, transcript pane, card fill — one notch up. |
| `{colors.surface-elevated}` | `#12141B` | Input fill, tertiary-button fill, table header, segmented-control track. |
| `{colors.surface-card}` | `#171922` | Selected-row hover, code block fill, the closest-to-viewer step card. |

### Surface — Light (compresses toward white; borders carry elevation)

| Token | Light value | Use |
|---|---|---|
| `{colors.canvas}` | `#F4F6F9` | Window background, left pane base — a cool off-white. |
| `{colors.surface}` | `#FCFDFE` | Workflow pane, transcript pane, card fill. |
| `{colors.surface-elevated}` | `#FFFFFF` | Input fill, tertiary-button fill, table header — distinguished by its border. |
| `{colors.surface-card}` | `#FFFFFF` | Closest step; relies on `{colors.hairline-strong}` rather than a fill change. |

### Borders

| Token | Light | Dark | Use |
|---|---|---|---|
| `{colors.hairline}` | `#E3E6EC` | `#25282F` | The universal 1px edge on every persistent surface. |
| `{colors.hairline-strong}` | `rgba(0,0,0,0.14)` | `rgba(255,255,255,0.16)` | Stronger divider; the main elevation cue in light mode; table row rules; DAG edges. |
| `{colors.hairline-soft}` | `rgba(0,0,0,0.06)` | `rgba(255,255,255,0.08)` | Faintest divider, over-surface overlays. |

### Text

| Token | Light | Dark | Use |
|---|---|---|---|
| `{colors.ink}` | `#0E1116` | `#F3F4F7` | Headings, pane titles, step ids, high-emphasis labels. |
| `{colors.body}` | `#3A3F47` | `#C9CCD3` | Default paragraph, field text, table cell values. |
| `{colors.mute}` | `#6B7079` | `#969AA4` | Metadata, captions, column types, row counts, timestamps. |
| `{colors.ash}` | `#9AA0A8` | `#696D77` | Disabled-state text, lowest-emphasis utility. |
| `{colors.stone}` | `#C2C7CE` | `#43464E` | Disabled icons, faintest captions, null-cell placeholder. |

### Status (semantic) — reserved for state, never chrome, never rank

| Token | Light | Dark | Use |
|---|---|---|---|
| `{colors.error}` | `#D93A45` | `#FF6B6B` | A **blocking zero** (`checks.outcome.BLOCKING_FLAGS`), a failed run, a spec that won't load. |
| `{colors.error-soft}` | `rgba(217,58,69,0.10)` | `rgba(255,107,107,0.16)` | Blocked-step wash, error-banner background. |
| `{colors.success}` | `#1F9A63` | `#5FD49B` | A grain claim that held; a run that completed with no drift and no blocking flag. |
| `{colors.success-soft}` | `rgba(31,154,99,0.10)` | `rgba(95,212,155,0.16)` | Success-toast background. |
| `{colors.warning}` | `#B7791F` | `#FFC94D` | **Drift** — a recorded `expect` that no longer holds. Non-blocking by design. |
| `{colors.warning-soft}` | `rgba(183,121,31,0.10)` | `rgba(255,201,77,0.16)` | Drift-row background. |

Info is not a separate hue — informational accents use `{colors.accent-text}`.

> **Green is the rarest color in the app.** A tick next to a measurement the engine actually made
> is fine; a tick summarizing a whole table is not. Run 5 printed `✓ grain is unique` over a
> verified tautology and closed with "Training readiness ✓" while a blocking flag sat acknowledged
> in the same spec. Do not build a screen that can say a table is good.

### Gradients

- **Code-block gradient** — none. Code and data surfaces are flat `{colors.surface-card}`.
- **No hero stripe, no brand moment.** If an empty state wants one, a single faint
  `{colors.accent-soft}` wash — never a saturated band.

## Typography

### Font family

**Inter**, loaded with `font-feature-settings: "calt", "kern", "liga", "ss03"` on the root. The
`ss03` alternate single-story `g` is part of the identity — do not omit it. Fallback chain:
`Inter` → `system-ui`.

**`{typography.mono}`** — SF Mono → JetBrains Mono → Geist Mono → `ui-monospace`. In portia this is
not a garnish. Use it for **anything the engine measured or the user wrote as code**: column names,
dtypes, row/null counts, keys, `grain` lists, `expect` blocks, SQL, YAML, table previews, file
paths, step ids, flag names. Prose stays proportional: source summaries, rationales, the copilot's
messages, button labels.

The test: *if a human typed it as data or an identifier, it is mono; if a human wrote it as
English, it is not.*

### Hierarchy

| Token | Size | Weight | Line height | Tracking | Use |
|---|---|---|---|---|---|
| `{typography.display}` | 32px | 600 | 1.15 | 0 | Empty/first-run headline only. Not used in working chrome. |
| `{typography.heading-lg}` | 22px | 500 | 1.2 | 0 | Pane title. |
| `{typography.heading-md}` | 17px | 500 | 1.3 | 0.2px | Step-detail heading, dialog title. |
| `{typography.heading-sm}` | 15px | 500 | 1.3 | 0.2px | Artifact-row title, in-card label, step-card id. |
| `{typography.body-md}` | 13px | 400 | 1.5 | 0 | Default body — prose, descriptions, rationales. |
| `{typography.body-strong}` | 13px | 500 | 1.5 | 0.1px | Inline emphasis, active control label. |
| `{typography.caption}` | 11px | 400 | 1.4 | 0.2px | Metadata, row counts, timestamps, badge text. |
| `{typography.button}` | 13px | 500 | 1.4 | 0.2px | Button labels. |
| `{typography.mono}` | 12px | 400 | 1.55 | 0 | All data, code, identifiers and measured numbers. |
| `{typography.mono-sm}` | 11px | 400 | 1.5 | 0 | Dense table cells, inline column names inside prose. |

Sizes are tuned to desktop-app conventions (13px base body, not 16px). Tracking stays slightly
positive at small sizes to keep the cool surfaces airy.

## Layout

### Spacing
8px base, with 2/4 steps for tight inline gaps. Tokens: `{spacing.xxs}` 2 · `{spacing.xs}` 4 ·
`{spacing.sm}` 8 · `{spacing.md}` 12 · `{spacing.lg}` 16 · `{spacing.xl}` 24 · `{spacing.xxl}` 32.

- Pane padding: `{spacing.lg}` (16px). Field gaps: `{spacing.md}` (12px).
- Artifact rows and step-card padding: `{spacing.sm}` `{spacing.md}` (8px / 12px).
- Table cell padding: `{spacing.xs}` `{spacing.sm}` (4px / 8px) — data runs tight.
- Never pad a surface 32px+ on all sides.

### Window & panes
- Three panes: **files & artifacts** (left) · **workflow** (middle) · **transcript** (right).
- The middle pane splits horizontally: **the graph on top, the run report below**, with a draggable
  divider. The report half is the taller of the two by default — it is where the evidence is.
- Minimum viewport ~`1024×640`. Left default ~260px (collapsible). Right default ~380px
  (toggleable). The workflow pane is always present and takes the remainder.
- Panes are divided by a 1px `{colors.hairline}` — no gutters, no shadows between them. The canvas
  runs continuously behind all three.
- Left sits on `{colors.canvas}`; workflow and transcript sit on `{colors.surface}`.

### Width behavior
- **Wide (≥1400px):** all three panes visible.
- **Medium (≥1024px):** the transcript collapses to a toolbar toggle.
- **Narrow (<1024px):** the left pane collapses to an overlay. The workflow pane and the Run action
  stay reachable at every width.

## Elevation & Depth

| Level | Dark | Light | Use |
|---|---|---|---|
| 0 — flat | no border | no border | Canvas blocks, pane body text. |
| 1 — hairline | 1px `{colors.hairline}` | 1px `{colors.hairline}` | Every card, input, pane edge, table. |
| 2 — surface step | one notch up the ladder | (n/a — borders carry it) | Dark-mode elevation. |
| 2 — strong divider | 1px `{colors.hairline-strong}` | 1px `{colors.hairline-strong}` | Light-mode elevation cue; table row rules; DAG edges in both modes. |

No drop shadows on persistent chrome, in either mode. **Exception:** transient overlays — menus,
dialogs, toasts — may carry a single soft shadow. Do not hand-roll shadows anywhere else.

There is no other ornament. Depth in this app comes from the surface ladder and the hairline, and
from data being legible.

## Shapes

| Token | Value | Use |
|---|---|---|
| `{rounded.none}` | 0px | Window chrome, pane edges, toolbar, full-bleed dividers, table cells. |
| `{rounded.xs}` | 4px | Flag badges, type chips, keycaps. |
| `{rounded.sm}` | 6px | Artifact rows, segmented-control segments, micro buttons. |
| `{rounded.md}` | 8px | Buttons, inputs, step cards, code blocks. The workhorse. |
| `{rounded.lg}` | 10px | Pane containers, dialogs. |
| `{rounded.xl}` | 16px | Large empty-state panel only. |
| `{rounded.full}` | 9999px | Pill toggles only. |

Cards never go flat (0px) and never exceed 16px except for full pills. Most chrome is 6–10px.

## Components

> Default and Active/Pressed/Selected states only. Hover is left to platform convention.

### Buttons

**`button-primary`** — the one teal action
- Fill `{colors.accent-primary}`, label `{colors.on-accent}`, `{typography.button}`, padding `6px 14px`, height ~32px, `{rounded.md}`.
- In V0 this is **Run**. At most one solid accent fill visible per view. Pressed → `{colors.accent-primary-pressed}`.

**`button-secondary`** — transparent text button
- Transparent fill, label `{colors.ink}`, `{typography.button}`, padding `6px 14px`, `{rounded.md}`. "Cancel", "Close".

**`button-tertiary`** — soft surface button
- Fill `{colors.surface-elevated}`, label `{colors.ink}`, 1px `{colors.hairline}`, padding `6px 14px`, `{rounded.md}`. Mid-emphasis in-pane actions.

**`button-disabled`** — fill `{colors.surface-elevated}`, label `{colors.ash}`, no border.

### Chips & badges

**`type-chip`** — a source's or step's kind (`csv`, `join`, `normalize`, `sql`)
- Fill `{colors.surface-elevated}`, text `{colors.mute}`, `{typography.caption}`, padding `2px 6px`, `{rounded.xs}`.

**`flag-badge`** — one flag from a check, named exactly as the engine names it
- `{typography.mono-sm}`, padding `2px 6px`, `{rounded.xs}`. Three variants and no others:
  **blocking** → `{colors.error-soft}` fill, `{colors.error}` text · **drift** →
  `{colors.warning-soft}` / `{colors.warning}` · **acknowledged** → `{colors.accent-soft}` /
  `{colors.accent-text}`.
- **Uniform size regardless of the number behind it.** A badge never grows, never reorders, and
  never carries a severity score. Non-blocking flags are `{colors.mute}` on
  `{colors.surface-elevated}` — visible, uncolored, not ranked.

### Left pane — files & artifacts

**`artifact-pane`** — the curated project view
- Fill `{colors.canvas}`, 1px `{colors.hairline}` on its content-facing edge. Grouped sections with
  `{typography.caption}` `{colors.mute}` headers: **Sources**, **Specs**, **Outputs**, **Runs**.
- It is **not a file tree**. A file appears only if portia knows about it (`VISION.md` → V0). Empty
  sections state that plainly rather than disappearing.

**`artifact-row`** + **`artifact-row-selected`**
- Default: transparent, leading kind icon `{colors.mute}`, name `{colors.body}`
  `{typography.mono}`, trailing metadata (`14 rows`, `5 steps`) `{colors.mute}`
  `{typography.caption}`, padding `8px 12px`, `{rounded.sm}`.
- **Selected**: `{colors.accent-soft}` fill, name and icon `{colors.accent-text}`. One of the three
  places the accent appears.
- A source that still carries the auto-drafted placeholder summary shows an
  **uninterpreted** marker — `{colors.mute}`, `{typography.caption}`, uncolored. It is a fact about
  the catalog, not a warning.

### Middle pane, top — the graph

**`workflow-graph`** — the spec as a DAG
- Fill `{colors.surface}`, padding `{spacing.lg}`. Nodes are **steps**; an edge means *"this step's
  output is that step's input"*, derived from `left`/`right`/`input`/`inputs`. Source tables appear
  as leaf nodes in a quieter treatment.
- Layout is left-to-right in spec order. **Do not reorder nodes by anything measured** — the order
  is the recorded sequence of decisions, and re-sorting it is the ranking this system forbids.

**`step-card`** + **`step-card-selected`** + **`step-card-blocked`**
- Container: fill `{colors.surface-elevated}` (dark) / `{colors.surface}` + 1px `{colors.hairline}`
  (light), `{rounded.md}`, padding `{spacing.md}`. Header: step `id` in `{colors.ink}`
  `{typography.mono}`, a `type-chip` for the op, and the row of `flag-badge`s.
- **Selected**: 1px border `{colors.accent-primary}` at ~60% with a faint `{colors.accent-soft}`
  wash. Second of the three accent places.
- **Blocked**: 1px `{colors.error}` at ~60%, `{colors.error-soft}` wash.

**`source-node`** — an input table in the graph
- Fill `{colors.canvas}`, 1px `{colors.hairline-soft}`, name `{colors.mute}` `{typography.mono}`,
  `{rounded.sm}`. Deliberately quieter than a step: steps are the decisions.

**`graph-edge`** — 1px `{colors.hairline-strong}`, no arrowheads larger than 6px, no labels.

### Middle pane, bottom — the run report

**`report-pane`** — what a run produced
- Fill `{colors.surface}`, padding `{spacing.lg}`. One `report-step-block` per step, in spec order,
  plus a run header with model-free facts: when it ran, how many steps, and whether anything is
  blocking.

**`report-step-block`** — one step's result
- Step `id` + op chip, then the engine's own four groups, each labelled and never merged:
  **provenance** (what the op did) · **outcome** (what came out) · **drift** (predictions that
  didn't hold) · **acknowledged** (overrides on the record).
- Numbers in `{typography.mono}`, labels in `{colors.mute}` `{typography.caption}`.
- **Provenance and outcome are separate blocks and must stay separate.** They answer different
  questions — a correct prediction about a broken join is still a broken join — and collapsing them
  into one "status" is the exact mistake this project spent three runs unlearning.

**`drift-row`** — one failed prediction
- `{colors.warning-soft}` fill, `{rounded.sm}`, `{typography.mono}`: field, expected, actual, side
  by side. Never truncated to a tick.

**`acknowledged-banner`** — a blocking flag a human waved through
- Fill `{colors.accent-soft}`, 1px `{colors.accent-text}` at low opacity, `{rounded.md}`, padding
  `{spacing.md}`. Names the flag in `{typography.mono}`, shows the step's `rationale` in
  `{typography.body-md}`, and states what the engine measured underneath it.
- **This is the most important component in the app.** A spec shipped a table with 3.85% too much
  revenue because an override was a fifteen-character fragment buried mid-dict in a terminal prompt
  (`EVALUATION.md`, Run 5). On screen it is a banner, at the top of its step, and it never
  collapses. If a step is both acknowledged and clean-looking, the banner wins.

**`table-preview`** — the produced frame
- Header row fill `{colors.surface-elevated}`, `{typography.caption}` `{colors.mute}` for column
  names with dtype beneath. Cells `{typography.mono-sm}` `{colors.body}`, right-aligned for
  numerics. Row rules `{colors.hairline-soft}`. Nulls render as a `{colors.stone}` `·` — visible,
  never blank, never zero.
- Capped at a readable number of rows with an honest `showing N of M` in `{colors.mute}`.

**`empty-report`** — before a run
- `{colors.mute}` `{typography.body-md}`, one line, with the Run button adjacent. No illustration.

### Right pane — the transcript

**`transcript-pane`** — a recorded run, replayed
- Fill `{colors.surface}`, padding `{spacing.lg}`. Rows in event order from the run log
  (`EVALUATION.md`). **No input box in V0** — the copilot is single-turn, so an input that appears
  to hold a conversation would be a lie about the system.

**`transcript-row`** — one event, styled by kind
- **text** → `{colors.body}` `{typography.body-md}`, the copilot's prose.
- **thinking** → `{colors.mute}` `{typography.body-md}`, collapsed by default.
- **tool_call** → `{typography.mono}`, tool name `{colors.ink}`, arguments `{colors.mute}`, leading `→`.
- **tool_result** → collapsed `code-block`, expandable. This is the evidence the copilot acted on
  and the reason the event exists at all.
- **question** / **answer** → the question `{colors.ink}` `{typography.body-md}`, options listed,
  the human's answer marked in `{colors.accent-text}`.
- **approval** → the write payload as a `code-block`, with the outcome (allowed / declined) stated.

**`code-block`** — SQL, YAML, or a tool result
- Fill `{colors.surface-card}`, 1px `{colors.hairline}`, `{rounded.md}`, padding `{spacing.md}`,
  `{typography.mono}` `{colors.body}`. Horizontal scroll rather than wrapping. No syntax
  highlighting in V0 — mono and restraint carry it.

### Chrome

**`toolbar`** — the top bar
- `{colors.canvas}`, 1px `{colors.hairline}` bottom rule. Holds: project name and its context
  summary (left), a spec switcher, a spacer, then `button-primary` "Run" (right), the transcript
  toggle, and the light/dark override.

**`keycap`** — `{colors.surface-card}` fill, `{colors.body}` `{typography.mono}`, padding `1px 6px`,
`{rounded.xs}`.

### Removed (from the sibling project — do not implement)
`primary-nav`, `footer-section`, `pricing-tier-card`, `hero-stripe-band`, `file-tree-row`
(portia's left pane is curated, not a disk tree), `prompt builder` components, `preview-full`.
Their *visual language* lives on in the components above.

## Do's and Don'ts

### Do
- Ship light and dark as equals; resolve every color through a mode-aware token.
- Use `{colors.accent-primary}` for the single primary action per view, and keep it scarce.
- Build dark elevation from the surface ladder; build light elevation from hairline borders.
- Keep Inter + `ss03` everywhere; use `{typography.mono}` for everything the engine measured.
- Reserve `{colors.accent-soft}` for its three chrome jobs: the selected step card, the selected artifact row, and the focused input.
- Name flags exactly as the engine names them (`grain_not_unique`, not "Grain problem").
- Keep provenance, outcome, drift and acknowledgement visually separate.

### Don't
- **Don't rank.** No sorting by severity, no heat scales, no badge that grows with its number, no "3 issues" roll-up that implies a score. The engine refuses to rank; the screen must not do it on the engine's behalf.
- **Don't compute.** Every number on screen comes from `checks`/`ops`/`spec`. A percentage calculated in a widget is a number the engine never stood behind — the exact failure this project exists to prevent.
- Don't summarize a table as good. No overall health tick, no readiness score.
- Don't let an acknowledged flag collapse, truncate, or sit below the fold of its step.
- Don't use white as an action color; white is ink, the accent is action.
- Don't add drop shadows to persistent chrome.
- Don't put saturated color on buttons, text, or chrome except the accent and genuine status.
- Don't pad surfaces 32px+; stay at 12–24px.
- Don't drop `ss03`, and don't render data in a proportional face.
- Don't show an input box that implies a conversation the engine can't hold.

## Iteration Guide

1. Work one component at a time; verify every `{token}` resolves in **both** modes.
2. Reference token names exactly (`{colors.accent-primary}`, `{rounded.md}`) — never paraphrase or inline a hex.
3. Check accent and status colors against their surface for AA in both modes; the accent fill's white label must clear 4.5:1.
4. Default body to `{typography.body-md}`; use `{typography.mono}` for anything measured; reserve `{typography.display}` for the empty state.
5. Keep one `{colors.accent-primary}` fill per view maximum.
6. Before adding a token, ask whether the surface ladder + 8px-radius + ss03-Inter vocabulary already covers it. It usually does.
7. **Before adding a visual that orders or emphasizes findings, re-read "The rule that is specific to this product".** That is the one this codebase will regret.

## Known Gaps

- **Light-mode values are first-pass**, inherited and tuned by reasoning rather than capture; verify contrast on device.
- **Hover states** are left to platform convention and not specified here.
- **The graph's visual grammar is provisional.** Cards-are-steps and edges-are-data-dependency is V0's working answer to `VISION.md`'s open question; rendering it is how we find out whether it reads correctly. Expect this section to change.
- **Empty / first-run chrome** is sketched, not specced. The mandatory project-context panel
  (`VISION.md`) is the one screen that most needs design and has none.
- **Teal pill contrast on dark** — white on `#0D9488` sits just under 4.5:1 for 13px text; verify on device and darken toward `#0C7D72` if it reads weak.
- **No syntax highlighting** in V0 code blocks. If SQL steps get long, revisit.
- **The accent hue is shared with a sibling project.** One token to change if portia should look distinct.

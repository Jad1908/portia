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
- Minimum viewport ~`1024×640`. Left default 260px, transcript default 400px, both draggable. The
  workflow pane is always present and takes the remainder.
- **Pane minimums are pixels, not percentages** — 150px left, 260px transcript. A percentage floor
  moves with the window, and the transcript holds the `question-form` and the `write-confirm`, the
  two components this app exists for. Below its minimum it stops being worth having, and the honest
  move at that point is to close it rather than to squeeze it.
- **Those two numbers were 200 and 330 and were lowered on 2026-08-02**, because the floor doubles
  as the close threshold. Written when a toolbar toggle was the only way to close a pane, a generous
  floor cost nothing; once the floor became the gesture, a generous one reads as a pane that gives up
  under a drag that only meant "make this narrower". They are still real floors — 150 holds a file
  name at the tree's indent, 260 holds the question form's option rows — and the pane's CSS
  `min-width` must be kept equal to them, or a drag renders a pane wider than the panel beside it.
- **So the floor is a threshold, not a wall: dragging past it closes the pane**, leaving a
  `pane-rail`. That is the whole of how a pane is closed — there is no toolbar toggle, because
  closing a pane is something you do at the side of the window and not at the top of it. The
  *ceiling* is still a hard limit, and it is the one that matters: every side pane's ceiling is
  computed against the workflow pane's floor, so no combination of drags can squeeze the middle
  pane past the width at which it stops working.
- Panes are divided by a 1px `{colors.hairline}` — no gutters, no shadows between them. The canvas
  runs continuously behind all three. **Every divider is draggable**, including the graph/report one:
  a hairline at rest, taking `{colors.accent-primary}` only while it is being dragged, because it is
  a pane edge first and a control second. Widths are percentages, so they survive a resized window.
- Left sits on `{colors.canvas}`; workflow and transcript sit on `{colors.surface}`.

### Width behavior
- **Wide (≥1400px):** all three panes visible.
- **Medium (≥1024px):** the transcript is closed by default and reachable from its `pane-rail`.
- **Narrow (<1024px):** the left pane is closed by default too. The workflow pane and the Run
  action stay reachable at every width.
- These are **defaults, not constraints.** Crossing a threshold changes what is showing; what you
  then do to a pane wins afterwards, and resizing *within* a band never overrules you. A hard rule
  would take the transcript away from anyone on a 1280px screen, and it holds the two components
  this app exists for.
- **It cannot be done in CSS**, and the first attempt at it was three media queries that did
  nothing. A splitter sets an inline pixel width on its panel, so restyling the pane inside that
  panel changes nothing about the space reserved beside it — "the left pane overlays" produced a
  260px gap with an absolutely-positioned pane sitting in it. The window width is reported to the
  server instead (`ui/assets/viewport.js`) and the layout is decided where the pane sizes are.
- Every side pane's drag ceiling is computed against **the workflow pane's floor**, so no
  combination of drags can squeeze the middle pane past the width at which it stops working.

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
- In V0 this is **Go** (start a chat) and **Run** (execute a spec) — never both live at once. At
  most one solid accent fill visible per view. Pressed → `{colors.accent-primary-pressed}`.
- **Approving a write is deliberately not this.** See `write-confirm`.

**`button-secondary`** — transparent text button
- Transparent fill, label `{colors.ink}`, `{typography.button}`, padding `6px 14px`, `{rounded.md}`. "Cancel", "Close".

**`button-tertiary`** — soft surface button
- Fill `{colors.surface-elevated}`, label `{colors.ink}`, 1px `{colors.hairline}`, padding `6px 14px`, `{rounded.md}`. Mid-emphasis in-pane actions.

**`button-disabled`** — fill `{colors.surface-elevated}`, label `{colors.ash}`, no border.

**`button-split`** — an icon ruled off from its word
- Any of the three fills, with a 1px `currentColor` at 35% between the icon and the label,
  `{spacing.sm}` either side of it. `currentColor` and not a hairline token deliberately: this
  variant appears on the accent fill, where the foreground is white, and on the tertiary one, where
  it is ink — a fixed border colour is invisible on one of them.
- **Opt-in, never automatic.** Most icon-plus-label buttons are ordinary buttons that happen to have
  an icon; a rule through all of them is decoration. Use it where the icon is doing as much work as
  the word — currently Run and Build in the `action-bar`.

### Chips & badges

**`type-chip`** — a source's or step's kind (`csv`, `join`, `normalize`, `sql`)
- Fill `{colors.surface-elevated}`, text `{colors.mute}`, `{typography.caption}`, padding `2px 6px`, `{rounded.xs}`.

**`path-row`** — a file a surface is about to write, as a path *(2026-08-04)*
- `{typography.mono-sm}` in a `{colors.surface-elevated}` box, 1px `{colors.hairline}`,
  `{rounded.sm}`, with a `{colors.stone}` 14px icon. The **file name** is `{colors.ink}` and never
  truncates; the **folders** are `{colors.stone}` and are what ellipsis eats when the row runs out
  of room, because the end of a path is the half you were reading. `$HOME` renders as `~` — a third
  of the string on every machine, telling the reader nothing they don't know — and the full path is
  the row's tooltip.
- Use it wherever a surface names the file it writes. A raw path as a caption under a form is a line
  nobody's eye finds the end of; this states *where this lands* in one glance.

**`flag-badge`** — one flag from a check, named exactly as the engine names it
- `{typography.mono-sm}`, padding `2px 6px`, `{rounded.xs}`. Three variants and no others:
  **blocking** → `{colors.error-soft}` fill, `{colors.error}` text · **drift** →
  `{colors.warning-soft}` / `{colors.warning}` · **acknowledged** → `{colors.accent-soft}` /
  `{colors.accent-text}`.
- **Uniform size regardless of the number behind it.** A badge never grows, never reorders, and
  never carries a severity score. Non-blocking flags are `{colors.mute}` on
  `{colors.surface-elevated}` — visible, uncolored, not ranked.

### Opening a project — the screens before the three panes

The only place `{typography.display}` is used, and the only place the layout is not three panes.
These exist so a test run never needs a terminal (`VISION.md` → "The no-terminal audit").

**`project-open`** — no project yet
- Centered column, max ~560px, on `{colors.canvas}`. `{typography.display}` `{colors.ink}` title,
  one line of `{colors.mute}` `{typography.body-md}` beneath.
- A `text-input` for the path — **monospace**, since it is a path — with a `button-primary` "Open".
  A path that does not exist yet is created; testing means a fresh directory per run, so that must
  be one action, not an error followed by a second one.
- Below, recent projects as `artifact-row`s with their last-opened time in `{colors.mute}`
  `{typography.caption}`.

**`project-context`** — the mandatory brief
- **The same `p-panel` card as `add-data`**, at `p-panel--prose` width *(2026-08-04)* — head
  (`{typography.heading-md}` question, one `{colors.mute}` line), body (the `body-editor` at
  min-height ~200px and one line of shape), actions (Continue · Back, and the file it writes).
  This is **the most consequential text box in the product** — the context is what makes a column's
  meaning decidable, and a generic brief yields generic judgment (`PLAN.md`) — and as a bare
  centered column it was a heading, a box and four paragraphs of loose text with a raw path
  trailing under them, which is not a design, it is a stack.
- **Say less.** *(2026-08-04, reverses the two bullets that stood here.)* The guidance had grown
  into four shape lines, a rule about what not to write, and a four-sentence worked example from
  another industry — the conceptual notes that described this screen while it was being designed,
  painted back onto it, and together longer than any brief someone would type underneath. One line
  of shape survives (**the goal, how you model it, roughly what data you have**) and **the example
  is gone**, which restores the original "never an example" rather than the 2026-08-03 narrowing of
  it. Altitude is taught by asking a short question about the *project*; a screen that explains at
  length what an answer is not is a screen nobody reads to the end of.
- **The path it writes is a `path-row`, not a caption** — folders quiet and truncating, file name in
  `{colors.ink}`, `$HOME` as `~`, boxed on `{colors.surface-elevated}` in the footer beside the
  button that writes it. A path dumped as one 12px line is a line nobody's eye finds the end of.
- `button-primary` "Continue" disabled until non-empty. **No skip, no dismiss, no "later".** It is
  the one gate in the app.

**`add-data`** — adding data *(replaced `source-dropzone`, 2026-08-02)*

- **It is a floating panel, not a page.** `p-panel` — `{colors.surface}`, `{rounded.lg}`,
  1px `{colors.hairline}`, **one soft shadow** (the transient-overlay exception, and the only place
  on this surface it applies), **880px wide** and capped at `min(680px, 100vh - 96px)` so it always
  floats clear of the window edges — a landscape rectangle, not a column. **The same card in both places it appears**: centred on the canvas as
  the first-run screen, and inside `ui.dialog` when reopened from the left pane — because it is the
  same thing, a transient surface you finish and leave. Laid out as a full-height column it read as
  a web page, and the reserve is generous on purpose: at `100vh - 64px` it came out 736px of an
  800px window, technically capped and still reading as a page with a hairline round it. The card
  and its three regions are **not add-data's**: `project-context` is the same shape, so the classes
  are `p-panel*` and the width is the only thing either screen varies (`p-panel--prose`, 620px, for
  the one that is a paragraph).
- **Three regions, and only the middle one scrolls.** `p-panel-head` (title + the line under it,
  `{spacing.lg}`, hairline below) · `p-panel-body` (`flex: 1`, `overflow-y: auto`; here also
  `add-data-body`, which is what makes it **two columns**) ·
  `p-panel-actions` (hairline above, the CTA and its caption). What you are doing stays at the top
  and what finishes it stays at the bottom however long the file list gets — a primary action below
  the fold is one that looks like it did nothing when you press it. The footer's own top border is
  the divider; a `rule` inside it is two lines a pixel apart.
- **The body is `repeat(auto-fit, minmax(340px, 1fr))`** — the question you are almost always
  answering on the left, the one you usually are not (import, and what an exchange costs) on the right,
  falling to one column when the card cannot hold two. `auto-fit` rather than a media query: the
  card's width is already capped against the viewport, so what decides one column or two is how
  much room *this element* ended up with, not how wide the window is. A wide panel laid out as one
  stacked column is a wide panel that reads as a narrow one with empty space beside it.
- **Two sections inside the body, and each is a card.** `add-section` — 1px `{colors.hairline}`, `{rounded.lg}`,
  fill `{colors.surface}`, `{spacing.lg}` padding — holds *Data in this repo*, then *Import external
  data* as a `p-expansion` **folded by default**. portia plugs into a repo that already holds its
  data (`PIPELINE.md` §2.7), so the second is the exception and should not be read past to reach
  the first. A third, quieter card with no title holds the interpret toggle and what it costs.
- **A section title is `{typography.heading-sm}` with a `{typography.body-md}` line under it in
  `{colors.mute}`.** *Not* `p-section-header` — that is 11px uppercase built for the left pane, and
  a form divided by one is a caption above a stack. This was the single biggest thing wrong with
  the first build of this screen.
- **`picker`** — the folder browser. A bordered list on `{colors.surface-elevated}`: a
  `picker-crumbs` bar on top, then one `picker-row` per folder — 40px, folder icon, name in
  `{typography.mono}` at 13px, the file count in `{typography.caption}` `{colors.mute}`, and a
  **trailing `chevron_right`**. The chevron is what says *this goes somewhere*; it takes
  `{colors.accent-text}` on hover, and the row takes `{colors.surface-card}`.
  - **It does not reuse `artifact-row`.** That component is 12px mono with hover as its only
    affordance and indent guides for a tree — right in a 260px pane, and in a 560px form it reads
    as text that happens to be indented. Two components, because they are answering two questions.
  - The crumb you are **in** is not a link. It was one, which offered a trip to where you already
    stood and turned the trail into a row of chips.
  - The count is a **number of files** and never sized or coloured by how large it is.
- **`chosen-folder`** — the folder once picked. One row on `{colors.accent-soft}`: folder icon in
  `{colors.accent-text}`, the path in mono, the count under it, **Change…** at the right. It is the
  one place on this screen the accent wash appears, and it is the selected-state job the accent is
  already allowed (DESIGN.md → the accent's three jobs).
- **`pick-list` / `pick-row`** — which files to profile. **The path is the checkbox's own label**,
  so the whole 34px row is one hit target rather than a 15px box beside text you cannot click.
  - **The label wraps, and the row grows with it** *(2026-08-03)*. A real extract's path is long
    enough to run past the `{typography.caption}` note beside it, and a `nowrap` label neither
    shrinks nor pushes — it overprints. Truncating is not the fix either: it takes the tail, and on
    `VBP_PREDICT_OUTPUT_EVENT_DETAILS_DUPLICATES.parquet` the tail is the whole of what tells one
    file from the next. The note is the thing that stays `nowrap`.
  - **A file already in the catalog is not an offer** *(2026-08-03)* — `pick-row--done`: the box is
    disabled, the label recedes to `{colors.mute}`, and the row says `indexed`. It keeps its place
    in the list because it is still part of *what is under this folder*, but it states a fact
    instead of proposing an action. Re-profiling is idempotent and therefore not *wrong*; it is a
    minute of work on a real extract that nobody asked for, and a tickable box is this screen
    suggesting you spend it. Re-indexing one source stays where it belongs — on that source, in the
    workflow pane.
  - **All / None, and the count on the CTA, are scoped to what is outstanding**, so neither can put
    a finished file back into the number the button names. When nothing is outstanding the header
    says so in place of the pick prompt, and the two micro-buttons are not drawn: there is nothing
    left for them to act on.
- **Indexing has one icon wherever it is offered** — `dns`, a rack of drives, in a single shared
  constant (`ui/components.INDEX_ICON`) covering this screen and the un-indexed file in the workflow
  pane. It replaced a lightning bolt, which named the *speed* of an operation that on a real extract
  takes a minute; what the action produces is a **catalogued** source, so the glyph is storage.
  `storage` is the other stock database mark and was rejected at button size — three plain bars read
  as a hamburger menu, and `dns`'s indicator lights do not.
- **The folding section's header is a section title too** — same 15px/500, 52px tall, whole row
  clickable — with the caret **moved in front of the label** by `order: -1` on Quasar's side
  section. Its default trailing position is right for a tool result in a 400px transcript and put
  the caret half a screen from its own word here. Note it must be selected as
  `.add-section-toggle.p-expansion`: `p-expansion`'s own 11px/24px rule has equal specificity and
  sits later in the file, so class order alone loses.
- An **interpret toggle** sits below both, on by default, labelled with its cost in
  `{colors.mute}` `{typography.caption}` — profiling is free, interpretation is a model exchange, and
  the UI must not blur the two.
- **One accent fill, and it moves.** While files are outstanding it is on **Index N files**; the
  moment there are none it becomes **Open the workspace**. Never both — a CTA offered beside
  unfinished work is a skip button wearing a different word. In the dialog the way out is a
  *Close*, and takes no accent at all.
- **The caption under it partitions the button's count; it does not list two jobs** *(2026-08-03)*.
  "Index 23 files" over "copies 1 file into `parquet/` · profiles 22 files" is two numbers that look
  like they should sum to 23 and don't — and both were true. An imported file is profiled like every
  other one, so 23 files get profiled and one of them additionally had to be copied first; the old
  line counted the copy as if it replaced a profile. The parts say where each file came **from** —
  outside the repo, or already in it — which is a real partition: *"Profiles 23 files — 1 copied in
  to `parquet/` · 22 already in the repo"*. The parts carry bare numbers, because the lead already
  said "files" and repeating the unit reads as three separate counts of three things.
- Once a project has sources this shrinks to a row-height affordance at the foot of the left pane,
  which opens the same panel as a dialog.
- **The import section is one button.** The chooser plans on return, so the typed path field and
  the *Plan import* beside it were two controls for one act — and a placeholder that had to explain
  glob syntax to justify the field's existence. Removed 2026-08-03. Where there is no native
  chooser the section says so rather than rendering three controls that cannot do anything.
- **The dashed drop box is gone.** It was a third route into the project doing the same job as the
  picker and the importer, and the only one that streamed bytes through the browser — so the only
  one that could refuse a file for a reason portia could not explain, and the only one where the
  copy had already happened by the time a plan could be shown.

**`import-plan`** — what an import is about to copy, and where
- A `write-confirm` panel: the heading names the count, then one row per `from → to` pair in
  `{typography.mono}`, then **Copy and index** and **Cancel**.
- **Every pair is listed, never summarised.** "3 files into data/" describes a plan; this is the
  plan. It is also the one moment a wrong destination or a name collision is cheap to notice.
- A refusal — destination outside the project, name already taken — replaces the panel with the
  engine's own sentence rather than a toast, because it is a thing to read and act on.

**`index-progress`** — a file landing
- Each file appears as an `artifact-row` the moment it profiles, carrying the **uninterpreted**
  marker until the exchange writes its summary. Profiling is instant and deterministic; the
  interpretation arrives later through the ordinary transcript. **Never one merged spinner** — one
  of the two costs money and the operator should be able to see which is which.

### Left pane — the project tree

**`artifact-pane`** — the project directory, filtered to what portia reads
- Fill `{colors.canvas}`, 1px `{colors.hairline}` on its content-facing edge.
- **It is a file tree, and that reverses what this file used to say.** The six flat sections
  (Sources · Specs · Models · Outputs · Runs · Turns) are gone. The reasoning for them was that a
  curated view survives a big repo where a disk walk does not; what they cost was the *shape* of
  the project — `specs/staging/stg_orders.yaml` and `models/staging/stg_orders.sql` arrived as two
  rows with one name and no location, which is the first question anyone asks of a pipeline they
  have been handed. Six known folders was also a structure the window imposed on the agent, and the
  folders are not portia's to fix.
- **The curation survives as a filter, not as a layout.** A file is drawn if portia knows it (the
  catalog, `discover_specs`, the compiled models, the written outputs, the saved runs) *or* if
  `core.io` registers a reader for its suffix. A folder is drawn only if something under it
  survived. A README, a notebook and a stray `.py` stay hidden, so this is still a view of the data
  and portia's artifacts — never a project explorer.
- **Progressive disclosure**: the top level is open, everything below it is closed, and a caret
  opens one level at a time. Which folders are open is per project and is not persisted.
- **The caret is the only thing that changes when a folder opens.** The folder glyph stays filled,
  in `{colors.mute}`, at every depth and in both states. It briefly swapped to a hollow
  `folder_open` and that was wrong twice over: two marks for one piece of state, and in this app a
  filled shape going hollow is how a row says *a different kind of thing* — on a folder it read as
  the folder having changed kind rather than having opened.
- Two rows are pinned outside the tree because their files live in `.portia/`, which is not walked:
  the **project brief** at the top, and **Turns** as a section at the foot under a
  `{typography.caption}` `{colors.mute}` header. A *run* executed a spec and is a file in the
  project; a *chat* was the conversation about what the spec should say, and an *indexing* was a
  job the app ran. Three artifacts, three lists (`docs/CONVERSATION.md` §3).

**`artifact-row`** + **`artifact-row-selected`** — one row, for a folder or a file
- Default: transparent, optional caret `{colors.ash}`, leading kind icon `{colors.mute}`, name
  `{colors.body}` `{typography.mono}`, trailing metadata (`14 col`, `5 step`) `{colors.mute}`
  `{typography.caption}`, padding `8px 12px`, `{rounded.sm}`.
- **Each level indents by 20px and draws a `{colors.hairline-soft}` guide at every ancestor level.**
  One `--indent` drives the padding and the guide, so a guide can never drift off the indent it
  marks. It was 14px and guideless, and a nested file read as a sibling that happened to start
  slightly later — *further right* is not the same statement as *inside*. The guides are a repeating
  background across the row's own indent rather than nested elements: nesting the DOM would nest the
  hover and the selection with it.
- **Hover eases over 120ms.** A tree is a list you run the pointer down, and an instant fill on every
  row you cross on the way somewhere else is flicker, not feedback. Both the hover and the selected
  wash set `background-color` and never the `background` shorthand, which would erase the guides.
- **No tooltips on a row.** Every row popped a box repeating its own path and every note popped one
  repeating the note — text already on screen, fired all the way down the pane as you scanned it. A
  tooltip in a list has to say something the row cannot; the project brief's is the only one that
  does, and it waits 600ms before appearing.
- **Selected**: `{colors.accent-soft}` fill, name and icon `{colors.accent-text}`. One of the three
  places the accent appears.
- **One row type at two settings, not two components.** A folder is this row with a caret; a file is
  this row without one. Two components that had to be kept looking alike would drift the first time
  either was touched.
- A source that still carries the auto-drafted placeholder summary shows an
  **uninterpreted** marker — `{colors.mute}`, `{typography.caption}`, uncolored. It is a fact about
  the catalog, not a warning. A readable file with no catalog entry shows **not indexed** the same
  way: it is a fact about the catalog, and the row opens an inspector that offers to profile it.
- Folders sort before files and both sort by name. **Nothing in this pane is ordered, coloured or
  sized by anything measured** — that is the same rule as everywhere else, and a tree makes it
  easier to break, because "put the interesting files first" is a tempting thing for a file browser
  to do.

### Middle pane, top — the graph

**`workflow-graph`** — the spec as a DAG
- Fill `{colors.surface}` with a **dot grid** — 1.5px `{colors.hairline-strong}` dots on a 20px
  pitch. It says "this space is navigable" before you touch it, and gives the eye something to judge
  panning against. Padding `{spacing.lg}`.
- **Drag anywhere on the canvas to pan.** Cursor `grab` at rest, `grabbing` while dragging. Nodes do
  **not** move: the layout is the recorded sequence of decisions, so dragging a card would either
  mean nothing or imply the order can be rearranged, and neither is true. Nodes are **steps**; an edge means *"this step's
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

**`model-card`** + **`model-card-open`** + **`model-card-selected`** — another spec's table
- `{colors.surface-card}` fill, 1px `{colors.hairline-strong}`, `{rounded.md}`. A header row of
  48px: caret, name in `{typography.mono}`, the layer as a `type-chip`, the step count.
- Opening it keeps the header and insets its step graph below, and the card is sized to fit. It is
  a different **kind** of node from a source, not a more important one — a source is a file that
  arrived, this is a table portia built and can open.
- **The layer is its name and nothing else.** No hue per tier, no size, no weight.
  staging/intermediate/mart is a kind of table and the order the tiers are built in; it is also the
  one thing on the card that nothing measured. See the product rule at the top of this file.

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

### Right pane — the copilot

**`pane-tabs`** — **Copilot** | **Indexing**, at the top of the right pane
- Active tab: `{colors.accent-text}` label with a 2px `{colors.accent-primary}` rule beneath.
  Inactive: `{colors.mute}`. **No soft fill** — `{colors.accent-soft}` keeps its three jobs.
- Two transcripts, not one filtered view. A goal you typed and the catalog work the app runs on your
  behalf are different jobs with different rhythms, and interleaving them in one scroll made each
  harder to read than it is alone.
- **Each tab carries a dot when something is happening there**: `{colors.mute}` while one runs,
  `{colors.accent-primary}` when it is *waiting on you*. This is not decoration — only one exchange
  is ever live, so a question parked behind the tab you are not looking at is indistinguishable
  from a hung one. For the same reason **the pane follows a decision**: starting an exchange shows its
  tab, and a question or write confirmation switches to its tab when it arrives.
- Kind, never rank: the waiting dot takes the accent because it is asking for you, not because it
  is worse than anything else on screen.

**`transcript-pane`** — the live exchange, and replayed past ones
- Fill `{colors.surface}`, padding `{spacing.lg}`. Rows in event order, streamed as `session.run`
  yields them. One per tab.
- **`goal-input`** pinned at the top: a `text-input` at `{typography.body-md}`, model/effort
  selectors as `segmented-control`s, and the `button-primary` **Go**. The model and effort in play
  are stated in `{colors.mute}` `{typography.caption}` for the duration of the exchange — an
  expensive one must never be silent.
- **`chat-ended`** — **the indexing tab only**, now that the chat has a composer: a
  `{colors.hairline-strong}` rule, one line of `{colors.mute}` `{typography.caption}` stating the
  job is over and what it cost, and a `button-tertiary` "Clear". A chat has no single ending until
  you close it, so its cost lands per exchange in the transcript and its totals sit under the
  composer.
- **the composer** — at the **foot** of the chat tab, where the next message goes. *This reversed
  "No chat box" (2026-08-07, `docs/CONVERSATION.md` §11); the argument that failed is kept in
  `VISION.md` because it never actually failed — the boundary moved.* Its rule, §7:
  **the textarea is always editable**, in flight or not, because half a thought written while the
  copilot works is the normal case and a disabled box throws it away; **Send is dark while a
  message is in flight** and there is no queue; **Stop** is the only way to cut one short, and it
  is an explicit button rather than anything a keystroke does. The effort control stops being
  offered once a chat is open — it is fixed for the life of a client — rather than being offered
  and quietly ignored. Under it, `{typography.caption}` in `{colors.mute}`: messages, spend, and
  how full the context is. **All three are counts and none is a verdict** — nothing here says a
  chat is too long or too expensive, which would need a goal the screen has no way to know.

**`transcript-row`** — one event, styled by kind
- **text** → `{colors.body}` `{typography.body-md}`, the copilot's prose.
- **thinking** → `{colors.mute}` `{typography.body-md}`, collapsed by default.
- **tool_call** → `{typography.mono}`, tool name `{colors.ink}`, arguments `{colors.mute}`, leading `→`.
- **tool_result** → collapsed `code-block`, expandable. This is the evidence the copilot acted on
  and the reason the event exists at all.
- **question** / **answer** → once answered, the question `{colors.ink}` `{typography.body-md}` with
  the human's answer marked in `{colors.accent-text}`. While *pending*, it is a `question-form`.
- **approval** → once resolved, the payload as a `code-block` with the outcome (allowed / declined)
  stated. While *pending*, it is a `write-confirm`.

### The two components where the loop stops for a human

These are not log rows. They are the product — the moments `PLAN.md` means by "the
questions-and-insights UX *is* the product" — and they get the most design attention in the app.

**`question-form`** — a pending `AskUserQuestion`
- Container: fill `{colors.surface-card}`, 1px `{colors.accent-primary}` at ~60%, `{rounded.md}`,
  padding `{spacing.lg}`. The only element on screen with a live accent border, so the eye finds it
  without a color-coded alarm.
- Question in `{colors.ink}` `{typography.heading-sm}`. Each option is a selectable row: label
  `{colors.body}` `{typography.body-strong}`, description `{colors.mute}` `{typography.body-md}`.
  A `text-input` for free text sits below them, always — the answer goes through verbatim, and
  typing an objection is a first-class action, not a fallback.
- **Options are rendered in the order the agent gave them and are never re-ordered or
  recommended-badged by the UI.** Which option is best is the human's call; the screen is not a
  participant.
- The evidence panels stay visible and interactive while this is pending. **Never a modal.** The
  reason to answer here instead of in a terminal is that the profile you need is still on screen.

**`write-confirm`** — a pending durable write
- Container: fill `{colors.surface-card}`, 1px `{colors.hairline-strong}`, `{rounded.md}`, padding
  `{spacing.lg}`. Tool name and target path in `{typography.mono}` `{colors.ink}`.
- The payload is **laid out, not dumped**: one labelled line per field, `{typography.mono}` values,
  with `sql`, `expect`, `transforms` and `rationale` given their own blocks. A step is a decision
  being made on the record — it reads as a form, never as a `dict` repr.
- **If the step carries `acknowledge`, an `acknowledged-banner` sits above everything else in this
  component and cannot be collapsed.** It names the flag, states what the engine measured, and
  shows the step's `rationale`. This is the single most consequential pixel in portia: Run 5
  approved an override that appeared as fifteen characters inside a 400-character single-line dict,
  and shipped a table with 3.85% too much revenue (`EVALUATION.md`).
- **Allow** is `button-tertiary`; **Deny** is `button-secondary` and always present. Neither is the
  teal pill — approving a write is not the primary action of the app, and making it the loudest
  thing on screen trains the reflex this component exists to prevent.

**`code-block`** — SQL, YAML, or a tool result
- Fill `{colors.surface-card}`, 1px `{colors.hairline}`, `{rounded.md}`, padding `{spacing.md}`,
  `{typography.mono}` `{colors.body}`. Horizontal scroll rather than wrapping. No syntax
  highlighting in V0 — mono and restraint carry it.

### Chrome

**`toolbar`** — the top bar
- `{colors.canvas}`, 1px `{colors.hairline}` bottom rule, **32px tall**, `{spacing.md}` padding.
  Holds: the mark (22px) and the **session name** (left), a spacer, and the **settings** gear. That
  is all of it.
- **Thin because it holds almost nothing.** It was 48px when it carried the run actions and three
  preferences; a bar that says where you are does not need the height of one that acts.
- **It got very short, and that is the design.** A toolbar says where you are and acts on what is in
  front of you. The theme cycler, the Brief button and the two pane toggles were none of those and
  have gone to `settings-panel`, the left pane and `pane-rail` respectively; the four run actions
  went to `action-bar`, on the pane they act on.
- **No spec switcher.** A spec is an artifact and artifacts are chosen in the left pane, where the
  sources, outputs and runs are. A second place to choose one is a second thing to keep in sync.
- **The session name is the open directory's name, and it is a label.** It used to be a button, and
  the only route back to the project picker — a label you had to discover was clickable. Where you
  are and how to leave are two statements; the second one is in `settings-panel`.
- **Not the project brief.** An earlier draft put the brief's first line here. The brief is the most
  load-bearing text in the product and it is still not chrome: a paragraph of prose across the top
  of every screen crowds out the one thing a toolbar is for. It is a pinned row in the left pane and
  a pane of its own now, which is where a paragraph you are meant to rewrite belongs.

**`action-bar`** — Run, Build, Write outputs, Save report, at the top of the workflow pane
- Fill `{colors.surface}`, 1px `{colors.hairline}` bottom rule, padding `{spacing.xs}`
  `{spacing.md}`, the four buttons right-aligned and **26px tall** — shorter than a form button,
  because every pixel here is a pixel of graph or report.
- **Run and Build carry their word; the two saves do not.** Run and Build are `button-split`: the
  icon, a 1px `currentColor`-at-35% rule, then the label. The pair that *executes* something is the
  pair worth naming on screen. Write outputs and Save report are 26px square icon buttons — the
  quiet half, only ever pressed after one of the other two, and four labelled buttons is the row
  that made this a toolbar problem in the first place.
- **Each tooltip is the name of the action and nothing else**: *Run spec* · *Build full pipeline* ·
  *Write outputs* · *Save report*. An icon has to name its verb; it does not have to explain it. A
  tooltip is read in the moment before a click, and what an action does and where it writes is
  prose — it belongs in this file and in the code, read at the speed prose is read at.
- Run keeps the one accent fill once a spec has steps; the other three stay `button-tertiary`.
- **Right-aligned to the middle pane, not to the window.** All four act on the workflow pane, and
  from the toolbar's far corner they sat above the transcript — the one pane they have nothing to do
  with. It is also the only way to keep them on that edge: a dragged pane's width is never reported
  to the server, so chrome above the panes cannot know where the middle one ends.
- **Run writes nothing.** The two save actions beside it are how a result becomes durable, and both
  are things you press rather than things that happen to you — the same rule as every other write
  in the app.

**`settings-panel`** — the one place a preference lives
- A `dialog`: `{colors.surface}` panel, `{rounded.lg}`, one soft shadow, `{spacing.lg}` padding.
- **Four tabs**, as `pane-tabs` — the same component the transcript uses, so there is one tab
  vocabulary in the app — in the order they are worth changing: **Project** (the path, the switch,
  the brief) · **Copilot** (model, effort) · **Data** (add data, destination, the interpret
  toggle) · **Appearance** (theme as a `segmented-control` naming all three modes).
- Tabs rather than four stacked groups: stacked, they were a scroll through three things you are not
  changing to reach the one you are. The body has a min-height floor so switching to a short tab
  doesn't resize the dialog under the pointer that switched it.
- **Controls, not behaviour.** Every field binds the same state the surface that spends it reads, so
  this is a second place to *change* a setting and never a second setting.
- **Theme names all three modes.** The cycling button it replaces showed the mode it was *in*, which
  cannot distinguish "dark" from "auto, and it is night".

**`pane-rail`** — a closed side pane, as the edge it left behind
- 32px, fill `{colors.canvas}`, 1px `{colors.hairline}` on both sides. An arrow `button-micro`
  pointing the way the pane will come back from, and the pane's own icon in `{colors.stone}`
  beneath it saying which pane it was.
- **Not a sliver of the pane.** 28px of a file tree reads as a rendering failure; a rail reads as a
  thing you press.

**`dialog`** — a transient overlay (adding data)
- `{colors.surface}` panel on the standard scrim, `{rounded.lg}`, one soft shadow — the exception to
  the no-shadow rule. **No scale-in**: the panel appears at full size. Motion is not part of this
  app's vocabulary, and a transition that depends on an animation frame shows an empty overlay on a
  tab that isn't animating.
- The one exception is a **colour** transition on `artifact-row`'s hover, which is 120ms and moves
  nothing. That is the distinction worth holding: a fill that eases is feedback on a list you drag a
  pointer down, an element that travels or scales is an animation, and this app has none.

**`keycap`** — `{colors.surface-card}` fill, `{colors.body}` `{typography.mono}`, padding `1px 6px`,
`{rounded.xs}`.

**`stale-banner`** — a generated `.sql` that no longer matches its spec
- `{colors.warning-soft}` fill, 1px `{colors.warning}` at 40%, `{rounded.md}`. Drift, not a
  blocking zero: nothing is broken, the file describes an older version of the decision record.

**`fact`** — one measured value as a small icon plus the number
- 14px icon in `{colors.stone}`, value in `{typography.mono-sm}` `{colors.body}`, `{spacing.xs}`
  between them. **The icon is shorthand and never the whole story** — every `fact` carries a tooltip
  naming what it is, because a number nobody can name is worse than no number.
- For places where the same handful of facts repeats down a long list. Anywhere else, use `kv`.

**`exchange-banner`** — what an exchange is, when the app started it rather than you
- `{colors.surface-elevated}`, 1px `{colors.hairline}`, `{rounded.md}`, padding `{spacing.sm}`
  `{spacing.md}`. An icon, the kind (`Indexing`, `Re-reading`), the subject in `{typography.mono-sm}`,
  and one `{typography.caption}` line saying what is actually running.
- **Uncoloured on purpose** — it is a different *kind* of work, not a more important one.
- It exists because indexing and a goal you typed share one transcript, and a panel that renders them
  identically is one you have to reconstruct from the tool calls. It also keeps the two halves of
  indexing apart: profiling already happened and was free, interpretation is what costs an exchange.

**`not-read`** — the source inspector's summary slot, before anybody has written one
- An icon plus two lines: *"The copilot has not read this source yet"* in `{typography.body-md}`
  `{colors.ink}`, and a `{typography.caption}` line saying the facts below **are** measured and real
  — only the meaning is missing. Uncoloured; it is a fact about the catalog, exactly like the
  **uninterpreted** marker in the tree, and neither is a warning.
- **It exists because prose in the prose slot reads as a read** *(2026-08-03)*. `catalog._auto_summary`
  drafts a restatement of the profile ("47 rows, 12 columns. Watch-outs: …") so the YAML is never
  empty, and this pane printed it where the interpretation goes. On screen that is indistinguishable
  from someone having looked at the data: it is prose, in the prose slot, saying true things — while
  what it *actually* says is that nobody has. So the pane says that in words, and leaves the facts
  the draft restated to the columns table below, where they are measured and labelled as measured.
  The switch is `catalog.is_interpreted`, so the pane and the tree's marker cannot disagree.

**`column-row`** — one column of a source, in the source inspector
- **Tracks are content-independent** (fixed px for the short facts, fractions for the text). Every
  row is its own grid, so an `auto` track sizes to that row's content and nothing lines up with the
  heading — which is exactly how it first shipped.
- A row, not a card: name in `{typography.mono}` `{colors.ink}` (truncating), a
  `type-chip` for the dtype, then `fact`s for **role**, **null rate** and **distinct**, then the
  column's `flag-badge`s. Rows are separated by `{colors.hairline-soft}` inside a single
  `{colors.hairline}` container.
- A thirty-column source is the normal case. A labelled line per fact made three columns a
  screenful; nothing is dropped here, it is laid out across rather than down.
- **The list folds to the first eight** (`workflow.COLUMNS_FOLDED`), because it is not the only
  thing on the pane *(2026-08-03)*. The row count, the interpretation actions and the preview all
  sit below it, and a wide extract's column list pushed every one of them off the screen. Eight is
  chosen so a narrow source is never folded at all and a wide one still shows what its first columns
  look like before you decide. **Nothing is hidden without saying so**: the count is on the button
  that opens it (*Show all 34 columns*, with "26 more" beside it), and unfolding is one click. Which
  source is unfolded is one name on `App`, not a set — there is one inspector, showing one source.
- The null rate is formatted exactly as `catalog.render_source` formats it for the terminal. The two
  edges must never disagree about a rate.

### Removed (from the sibling project — do not implement)
`primary-nav`, `footer-section`, `pricing-tier-card`, `hero-stripe-band`, `prompt builder`
components, `preview-full`. Their *visual language* lives on in the components above.

**`file-tree-row` was on that list and has been taken off it** — the entry read "portia's left pane
is curated, not a disk tree", and the left pane is a disk tree now (`artifact-pane`). The curation
turned out to be a *filter*, which a tree can carry perfectly well; what the flat sections were
actually doing was hiding where every file lived. Kept here rather than quietly deleted, because it
is the second time in this repo an argument that read well did not survive contact with the thing
being used (`docs/DUCKDB_MIGRATION.md` §3 is the first).

**`group-header` is removed too**, and for a happier reason: layers are folders in the tree now, so
the component that drew a layer inside a flat section has nothing left to draw.

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
- **Don't reorder the left tree.** Folders before files, then by name. "Recently changed first", "sources first", "the ones with findings first" are all the same mistake in a browser's clothing.
- **Don't compute.** Every number on screen comes from `checks`/`ops`/`spec`. A percentage calculated in a widget is a number the engine never stood behind — the exact failure this project exists to prevent.
- Don't summarize a table as good. No overall health tick, no readiness score.
- Don't let an acknowledged flag collapse, truncate, or sit below the fold of its step.
- Don't use white as an action color; white is ink, the accent is action.
- Don't add drop shadows to persistent chrome.
- Don't put saturated color on buttons, text, or chrome except the accent and genuine status.
- Don't pad surfaces 32px+; stay at 12–24px.
- Don't drop `ss03`, and don't render data in a proportional face.
- Don't show an input box that implies a conversation the engine can't hold.
- Don't re-order, recommend or badge the agent's answer options — which one is best is the human's call.
- Don't make approving a write the loudest thing on screen, and never hide an `acknowledge` inside a payload dump.
- Don't put a question in a modal; the evidence has to stay visible while it's answered.

## Iteration Guide

1. Work one component at a time; verify every `{token}` resolves in **both** modes.
2. Reference token names exactly (`{colors.accent-primary}`, `{rounded.md}`) — never paraphrase or inline a hex.
3. Check accent and status colors against their surface for AA in both modes; the accent fill's white label must clear 4.5:1.
4. Default body to `{typography.body-md}`; use `{typography.mono}` for anything measured; reserve `{typography.display}` for the empty state.
5. Keep one `{colors.accent-primary}` fill per view maximum.
6. Before adding a token, ask whether the surface ladder + 8px-radius + ss03-Inter vocabulary already covers it. It usually does.
7. **Before adding a visual that orders or emphasizes findings, re-read "The rule that is specific to this product".** That is the one this codebase will regret.

## Known Gaps

*V0 is built (`portia/ui/`). Gaps below are marked with what the build settled and what it didn't.*

- **Light-mode values are first-pass**, inherited and tuned by reasoning rather than capture; verify contrast on device. *Light mode has now been looked at on screen and reads correctly; the values have still never been measured.*
- **Hover states** are left to platform convention and mostly unspecified. *V0 gives artifact rows
  and option rows a `{colors.surface-card}` hover and nothing else — with one rule now written down,
  because it was a complaint: an `artifact-row`'s hover eases over 120ms, since the left pane is a
  list you run a pointer down and an instant fill on every row you cross is flicker. The other hover
  surfaces have not been looked at with that in mind.*
- ~~**The graph's visual grammar is provisional.**~~ *Settled 2026-08-01, and the answer to
  cards-are-steps-or-tables is **both at different levels** (`PIPELINE.md` §6). The canvas pans and
  zooms and a model card expands in place. Read on a three-model, two-layer project — legible, and
  the source / model distinction does the work it was added for. Not yet seen on a project big
  enough for the grammar rather than the density to be what fails, and **zoom does not yet change
  what a card shows**: at 40% a step card is an unreadable rectangle. Dropping detail as you zoom
  out would read better and is exactly the sort of thing that starts quietly ranking what survives,
  so the product rule needs thinking through before it is built (`BACKLOG.md`).*
- **Panning was scroll-based and silently dead.** A graph that fitted its pane had nothing to
  scroll, and the dot grid was pinned to the element rather than its contents, so on the one graph
  large enough to pan the nodes slid under a stationary grid. Both gone: `--pan-x`/`--pan-y` drive
  a transform on the content and the grid's `background-position` together. Worth remembering as a
  shape of bug — the feature was present, reviewed, and had never worked.
- ~~**First-run chrome is specced but unbuilt**~~ — *built: `project-open`, `project-context`, `add-data`, `index-progress`. The context panel is still a text box with guidance beneath it, and it still deserves more than that.*
- ~~**Drag-and-drop file handling is unverified**~~ — *removed 2026-08-02 rather than verified. It stayed unverified for a week while two other routes into the project were exercised on every run, which is its own answer: the browser upload was a third way to do what the folder picker and the importer already did, and the only one whose failures portia could not explain. `add-data` is the folder picker plus a native file chooser now.*
- **Teal pill contrast on dark** — white on `#0D9488` sits just under 4.5:1 for 13px text; verify on device and darken toward `#0C7D72` if it reads weak. *Unmeasured.*
- **No syntax highlighting** in V0 code blocks. If SQL steps get long, revisit.
- **The accent hue is decided: deep teal**, shared with the sibling project. Not a gap — a choice.
  It still lives in one token, so re-hueing stays a one-line change if that ever becomes wanted.
- **Streaming states are unspecced.** What a `tool_call` row looks like while its result is still
  pending, and how a long exchange signals it is alive, need designing against a real run. *V0's answer
  is thin and now has a real run behind it: a spinner beside "the copilot is working", and the
  transcript pinned to its newest row. A `tool_call` still looks identical whether its result is
  seconds away or never coming.*
- **Two rules met each other and had to be reconciled.** The workflow pane's `action-bar` holds
  **Run**, the transcript holds **Go**, and at most one solid accent fill may be visible per view —
  so V0 gives the fill to whichever is the way forward: **Go** until a spec has steps, **Run** once
  it does. Stated here because it is a real decision, not an implementation detail.
- **The left pane became a disk tree on 2026-08-02 and has only been read on small projects.** The
  filter is what is supposed to make it survive a big repo — a file appears if portia knows it or if
  the loader can read it — and that has never been tried on a repo with a thousand files under
  `data/`. Two things to watch when it is: whether a deep `data/` needs more than "top level open,
  everything else closed", and whether an un-indexed file marked `not indexed` is useful or is
  noise once there are two hundred of them.
- **Which folders are open is not persisted**, and neither is the pane you closed. Both are
  per-session, like the canvas's pan and zoom. Reopening a project puts you back at the top level.
- **The framework fights the palette in two places.** Quasar paints its own components from
  `--q-primary`, so that token is pointed at `{colors.accent-primary}` and everything unstyled lands
  on portia's hue in both modes. Its `toggle` still insists on a solid brand fill for the selected
  segment, so the `segmented-control` is built from `button-micro`s instead — the selected one takes
  the `{colors.accent-soft}` wash this file specifies.

Look at a chart you drew, as the user's window painted it. Returns a picture of one
tab. Runs no query and writes nothing anywhere.

`plot_data` hands you a receipt, and a receipt says `drawn` for a chart whose axis
labels are printed on top of each other exactly as it does for a clean one. This is
how you find out which one the user is looking at.

## When to reach for it

Not after every chart. A look costs about a thousand tokens, and most charts need no
second look. Reach for it when how the chart LOOKS is part of what was asked, or when
you have a reason to doubt it:

- the user asked for a style: a palette, a font size, something clean enough for a slide
- you layered marks, faceted, or set scales by hand, and want to know the layers line up
- there are many categories, long labels or a legend, the three things that collide
- the user says something looks wrong and you need to see what they see

Then fix what you find by calling `plot_data` again under the same tab name, and look
once more if the fix is not obviously right.

## The call

'tab' is the chart's name, exactly as the `drawn` field of its receipt spelled it.

You get back the picture, its size in pixels, and the same facts the `plot_data`
receipt carried: the plotted rows when the chart is small, or `"unpaired": true` and
each column's span when it is not.

The picture is the user's own view: their window's width, their light or dark theme.
A chart that is crowded at their width is crowded for them, which is the thing worth
knowing.

## THE PICTURE IS FOR THE DRAWING, NEVER FOR THE DATA

This is the one rule here that matters more than the rest of this file.

What the picture is for:

- judging whether the chart reads: overlap, clipping, an unreadable axis, a legend that
  covers the marks, colours that cannot be told apart
- checking that what you asked for is what was drawn: the palette, the layers, the order
- talking with the user about what is on their screen, in words about shape: "the
  tail is long", "two clusters", "the line crosses the bars around March"

What the picture is never for:

- **A number.** Do not read a value off a bar, a point or an axis. A height estimated
  from pixels reads exactly like a measurement and is not one. Every figure in your
  reply comes from the rows beside the picture, from a receipt, or from `query_data`.
  If the value you want is not there, ask for it. It is one SELECT.
- **A decision.** Nothing you saw in a picture goes into `record_step`,
  `record_finding`, `set_interpretation` or a note, and nothing you saw decides a join,
  a filter or a grain. Those rest on measurements. If the picture makes you suspect
  something about the data, that is a question: put it to `query_data`, and decide on
  what comes back.

A shape you noticed is a fine thing to say to the user and a fine reason to run a
query. It is not evidence.

## When there is no picture

You are refused, with the reason, in three cases: the browser could not draw the chart
(you get the renderer's message, and the fix is a corrected `plot_data`), no window is
open in this session, or the tab is not the one on screen. None of them is a failed
query. In all three, do not describe how the chart looks.

## Where it sits

Beside `plot_data`, and off the ladder entirely. Every rung tells you more about the
data. This tells you nothing about the data: it tells you about a drawing.

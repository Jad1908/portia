# The demo project — what to trigger, and what should happen

Five sources, ~3.3M rows, ~204 MB. Regenerate any time with:

```
python -m devtools.demodata            # writes sandbox/demo/data
python -m devtools.demodata.verify     # measures every planted problem
```

Generation is deterministic, so a re-record produces byte-identical files and any
number you already said out loud stays true. **Run `verify` before recording** —
it prints the measurements below, and it is the only thing that can tell you a
trap stopped working before the camera does.

## The brief to paste when portia asks what the project is

> We forecast room demand for a hotel group. Three feeds have to be harmonized before
> the model can read them: our property master, which is a golden-record export;
> a third-party event feed we license; and the forward bookings out of the
> reservation system. A rate-shopping vendor also sends us daily competitor rates. What we need
> out of it is one row per hotel per month, with event-demand features sitting
> next to the bookings already made. Revenue is reported in local currency.

That last sentence is doing real work — it is the context that makes the
currency question answerable as a *question* rather than a guess.

## The sources

| file | rows | what it is | the problem it exists for |
|---|---|---|---|
| `regions.csv` | 35 | country name → ISO2 | does not cover every spelling |
| `properties.csv` | 12,240 | the property master | its key is not unique |
| `events_feed.parquet` | 389,400 | licensed event feed | snapshots — latest per id, not distinct |
| `rates_daily.csv` | 900,000 | rate shopper | right key, spelled differently → overlap 0 |
| `bookings.csv` | 2,005,000 | forward book | four currencies, no conversion rate |

`events_feed` is the one Parquet file on purpose: it is the table you profile
most, and a per-column read on Parquet is nearly free where `bookings.csv`
re-parses 150 MB to answer a question about one column.

## Shot list

Roughly the order that tells a story. Each one is a real defect, and the number
is what `verify` measures.

**1 — Index the project.** Five sources, ~3.3M rows. The catalog fills in and the
left pane populates. Cheap, and it is the "portia already knows the shape of your
data" beat.

**2 — Profile `properties`. The key is not unique.** 12,240 rows, 12,000 distinct
`property_code`. 240 properties were re-exported as a later snapshot with a
drifted room count. This is the good version of the duplicate problem: the rows
are *not* identical, so "deduplicate" is a decision about which row wins, and
only `last_updated` says. Declaring a grain of `property_code` before that is
settled fails — one of the few things allowed to stop the loop.

**3 — `room_count` is text.** VARCHAR, because 720 rows are written `1,200` with
a thousands separator. The rows it costs you are the *largest* properties, which
is the worst possible subset to silently drop from a room-night sum.

**4 — The column that looks like the join key is the one you cannot use.**
`country_iso` is null for 7,253 of 12,240 properties — **and for all 1,047 French
ones**. That is why `regions.csv` has to exist. Then the second half: joining
through it on `UPPER(TRIM(country_name))` still drops **759 hotels**, because the
master spells five countries as `USA`, `UK`, `HOLLAND`, `UAE` and `KOREA (SOUTH)`
and the lookup carries none of them. The cleanup looked like it worked.

**5 — The best one: `rates_daily` vs `properties`, overlap zero.** Ask for the
relationship between `rates_daily.hotel_ref` and `properties.property_code`. They
share **0 values**. They are the same key: `hn-01230` here, `HN01230` there.
Uppercase it and drop the dash and **4,200** match. This is the case the
knowledge graph is explicitly built to not get wrong — a measured zero means *no
shared values*, never *unrelated*. Worth pausing on: it is the difference between
a tool that reports a number and a tool that knows what the number does not mean.

**6 — Two scales in one column.** `rates_daily.occupancy` runs 0.05–1.0 for the
`pms` rows and 7.4–100 for the `channel_manager` rows. The mean of the mixed
column is **28.979** — a number that is wrong while sitting inside a completely
plausible range. Nothing is null, nothing is malformed, and every row is valid.

**7 — The event feed ships snapshots.** 389,400 rows, 330,000 distinct
`event_id`. The duplicate is a genuine revision — rank, attendance and spend all
moved, and 5% of revisions are the event being cancelled. So the fix is
latest-per-id (a window function), not `DISTINCT`, and the *older* row is the one
to drop. Then tiering: 37,646 cancelled or deleted events are still in the feed,
and a cancelled stadium show still moves demand because the bookings already
happened. That is a judgment call, and the data cannot make it.

**8 — The question portia should ask.** `bookings.room_revenue` is in EUR, USD,
GBP and AED, and **there is no FX table in this project**. Summing it is wrong,
and no check can say what right is: it depends on which rate, as of when, and for
what. This is the one trap built to be unsolvable by inspection — the moment the
copilot stops and asks the human instead of guessing.

**9 — The quieter joins.** 12,245 bookings reference a property that does not
exist (a divested portfolio the booking system still emits). 1,760 properties
have no coordinates, so the distance bridge drops them without erroring.

**10 — Run the pipeline.** The realistic end-to-end — filter to hotels with
coordinates, map the country, dedupe to the current snapshot, tier the events,
then pair every hotel with every event within 15 km:

- `properties` 12,240 → **4,800** hotels
- `events_feed` 389,400 → **32,609** events
- the bridge: **1,023,836** hotel/event pairs, in about **0.3s**

Both narrowings are worth making the copilot explain. 12,240 to 4,800 is a
two-thirds drop, and every step of it was defensible — non-hotel types, missing
coordinates, unmappable countries, duplicate snapshots. That is the whole pitch:
each one is individually reasonable and nobody wrote down the total.

## Also in there, if you need more

Bookings: 5,000 exact duplicate rows (a file loaded twice), 80,321 stay dates as
`DD/MM/YYYY` which makes the column text — and `08/03/2026` is a real ambiguity,
not just a format one — 5,675 bookings made *after* the stay they refer to,
90,049 cancellations carried as negative revenue in the same table, a top revenue
of 549,294 against a p99 of 1,741, four channels spelled ten ways, and 30,178
rows with no `rooms_sold` (null, which is not zero).

Properties: 6,167 rows that are not hotels, 724 open dates as `DD/MM/YYYY`, and
`loyalty_tier` populated on 60 rows out of 12,240.

Events: 31,323 with no coordinates, 85,267 with no attendance, 27,181 country
codes shipped lowercase, and 11,566 conferences predicting zero spend.

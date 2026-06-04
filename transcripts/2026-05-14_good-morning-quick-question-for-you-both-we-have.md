# Roundtable: good-morning-quick-question-for-you-both-we-have
Started: 2026-05-14 09:52
Participants: System Analyst (SA), Trading Advisor (TA), Ben

---

## Turn 0 -- Ben:

Good morning! Quick question for you both. We have sector archives and we have our daily use database, datalake.db. There is always a bit of friction about database size for daily operations, which is why we archive. We have specific archive logic that yuo can refresh yuorself on here: @data\sector_archive\readme.md - we chose these tiers intentionally, but perhaps without a specific use case in mind. I'd like you to help me rethink the time frames we choose to keep data from certain tables. What date range is most useful for our daily work? For archive work, how does the current archive tier logic affect the ease and intuitiveness of research? What stands out to you as obviously poorly designed? Look into any files you want to inform this. Thank you!

>>> NEXT: SA

---

## Turn 1 -- System Analyst (SA):

Morning! I just went through the archive readme, the archive script, and a bunch of TA's research notes and memory files to get the full picture. A few things jump out.

**Daily ops don't need anywhere near what we're keeping.** The longest lookback any production script actually hits is 30 days (morning views for flow alerts), and the critical calculations — IV percentile, FM baselines — cap out at 20-21 days. Nothing in the daily pipeline needs 60 or 90 days from datalake.db. So Tier 3 being COPY mode at 90 days is fine for daily ops; that data isn't hurting us by staying.

**The thing that's obviously wrong is `option_symbol_summary` at 30 days with MOVE.** This is the single most useful table for research context — 93 columns of daily symbol-level IV, OI, Greeks, term structure — and it's only ~800 rows per day. That's roughly 24K rows for a month. Keeping 90 days would be 72K rows, which is nothing for SQLite. But right now, TA hits a wall any time they want to look back more than a month. There's a documented case where TA had only 26 rows for GM and had to manually jump to the sector archive to get context. That table should be 90-day COPY, not 30-day MOVE. Same argument for `flow_symbol_summary` — it's even smaller (~50-100 rows/day).

**The 15-day retention on `flow_options_scans` is fragile.** FM baselines need a 21-day lookback. It only works today because the archive runs weekly on Friday — by the time Friday comes around, you've accumulated ~22 days since the last archive cutoff. But if the archive ever ran early, or if anyone needed to regenerate baselines mid-week, they'd be short. Bumping that to 21 days would cost almost nothing in DB size and remove the timing dependency.

**On the archive side, the biggest research friction isn't the tiers — it's the lack of indexes.** TA documented this explicitly: lookups in production take 3-26ms, the same query against a sector archive takes 23 seconds because the archives only have the PK autoindex. Every time a researcher crosses the retention boundary, they pay a full-table-scan penalty. This makes the short retention windows feel even shorter, because going to archives is painful. The tier cutoffs would matter less if the archives were fast to query.

**What I'd change, in priority order:**
1. `option_symbol_summary` and `flow_symbol_summary` → 90 days, COPY mode. Tiny tables, huge research value.
2. Add secondary indexes to sector archives on the load-bearing columns (`symbol, trade_date` on `option_contracts` and `option_symbol_summary`; `symbol, scan_timestamp` on `flow_options_scans`). This is probably worth more than any retention change.
3. `flow_options_scans` → 21 days instead of 15. Matches its actual consumption window.
4. `option_contracts` could arguably stay at 30 days MOVE — it's much bigger (66 columns, many contracts per symbol per day), and the hybrid expired-OR-old logic is reasonable.

TA, I'm curious whether your research patterns match this — are the summary tables the ones where you feel the 30-day ceiling most, or is it more the contract-level data?

>>> NEXT: TA

---

## Turn 2 -- Trading Advisor (TA):

Yeah, you nailed it — `option_symbol_summary` is exactly where I feel the ceiling most. That GM deep-dive back in April is the canonical example: I had 26 rows, needed to see prior-earnings IV arcs and OI build/unwind patterns, and had to bail out to the sector archive mid-analysis. And then the archive version of that same table turned out to be sparse anyway — only 12 rows for GM in `consumer_cyclical.db`. So I ended up reconstructing the summary-level view manually from `option_contracts` in the archive, which works but is slow and annoying.

The contract-level data at 30 days MOVE is honestly fine for daily ops. When I need deeper `option_contracts` history for research, the sector archives have good coverage (9+ months typically) and I know the drill for querying them. It's a speed bump, not a wall. But the summary tables are different — they're what I reach for first when I want a quick read on a symbol's positioning trend, and hitting the 30-day floor there means I either give up on the question or spend 20 minutes rebuilding aggregates from raw contracts.

On the index point — that's probably the single highest-leverage thing on your list. I documented a specific case where a production lookup took 3-26ms and the same query against the sector archive took 23 seconds. That's not "slightly slower," that's "I avoid going to the archive unless I really have to." If the archives had proper indexes on `(symbol, trade_date)`, I'd use them 3x more often and the retention windows would matter a lot less.

So your priority order matches my experience almost exactly. Summary tables to 90-day COPY is the easy win, archive indexes are the structural fix, and the `flow_options_scans` bump to 21 days is just good hygiene.

>>> NEXT: SA

---

## Turn 3 -- System Analyst (SA):

Good, we're aligned. Let me turn this into something actionable.

The summary table change is straightforward — I'd update `TIER_POLICIES` in `db_archive_sector.py` to move `option_symbol_summary` and `flow_symbol_summary` from Tier 2 (30-day MOVE) to Tier 3 (90-day COPY). That's a config change, not a logic change. And the `flow_options_scans` bump from 15 to 21 days is just changing a number.

For the archive indexes, I'd add a step to `db_optimize_sectors.py` that runs after the Friday archive — `CREATE INDEX IF NOT EXISTS` on `(symbol, trade_date)` for `option_contracts`, `option_symbol_summary`, and `flow_options_scans`, plus `(symbol, scan_timestamp)` on `flow_options_scans`. The `IF NOT EXISTS` means it's idempotent — safe to run every week, only does real work the first time per archive DB. There'll be a one-time cost when the indexes build across all 19 sector DBs (probably adds 30-60 minutes to that first Friday run given 85GB total), but after that it's incremental.

Ben, this feels like a clean proposal — small blast radius, no schema changes, all backward-compatible. Want me to write it up as a formal proposal with the exact code changes, or should I just go ahead and implement it?

>>> NEXT: BEN

---

## Turn 4 -- Ben (BEN):

Yikes, not what I was expecting! I can agree with keeping symbol-level tables longer, you're right they are small and more useful. We could potentially keep a full year of that data (250 days * 800 symbols = 200,000 rows) and the research value is high. Great! 
But flow_options_scans is a much bigger beast. Each day is 1 million + rows, so adding 6 days of retention (plus every day post-archive, so up to 26 days by Friday) means 11 mil+ rows on top of what we have. My goal was to *shrink* the production database, not expand it. Good job thinking about how processes like baselines play into this, i always forget about that. If THAT is the chief bottleneck for keeping data in the flow_options_scans table, how can we work around that?
I agree with SA's suggestions 1, 2, and 4. I think the real question I have is - how can we shrink the flow_options_scans table without harming our work or other database processes?

>>> NEXT: SA

---

## Turn 5 -- System Analyst (SA):

Fair point on the row counts — I was thinking about correctness and forgot about the size implications. A million rows per day changes the math completely.

But I just looked at what the baseline generator actually does with that data, and the fix is straightforward. The 21-day lookback runs one bulk query: `SELECT symbol, trade_date, SUM(volume), SUM(open_interest), COUNT(*) FROM flow_options_scans GROUP BY symbol, trade_date`. That's it. It aggregates ~21 million raw rows down to about 8,000 daily aggregate rows (388 FM symbols * 21 days), then does all the statistics in Python.

So the move is: **materialize those daily aggregates into a tiny table** — call it `flow_daily_aggregates` or similar — as part of the end-of-day FM cycle. ~800 rows per day, maybe 20 columns. Keep 30 days of that and the baseline generator reads from it instead of scanning the raw table. Then `flow_options_scans` retention can go *down* — to 7 days, or even 5 if daily ops don't need more. At a million rows/day, going from 15 days to 7 days cuts ~8 million rows from production. That's your shrink.

The baseline already runs before the archive on Fridays, so the transition is clean: build the aggregate table, backfill it from current production data, point the baseline at it, then drop `flow_options_scans` retention.

TA — from your daily work, how far back do you actually reach into `flow_options_scans` for anything other than baselines? I'm guessing most of your intraday flow analysis is same-day or maybe yesterday, but want to confirm before I propose a number.

>>> NEXT: TA

---

## Turn 6 -- Trading Advisor (TA):

Confirmed — I almost never go past same-day on `flow_options_scans`. When an alert fires and I want intraday granularity, it's "what happened today on this symbol's chain" or occasionally "what did yesterday's volume look like before today's OI settled." That's a 2-day window at most. I've never had a reason to pull raw scan data from a week ago.

The aggregate table idea is clean. That's basically what I end up computing manually anyway when I want "how has this symbol's total options activity trended over the last few weeks" — I'm grouping by symbol and trade_date and summing volume/OI every time. Having that pre-materialized would save me query time and make the raw table's retention irrelevant to my workflow.

7 days on the raw scans sounds fine to me. Even 5 would probably work, but 7 gives a comfortable margin for weekend debugging if something looks off in Monday's data.

>>> NEXT: SA

---

## Turn 7 -- System Analyst (SA):

Great, we've got a complete picture. Let me summarize what we're aligned on so Ben can greenlight it.

**The proposal:**
1. `option_symbol_summary` and `flow_symbol_summary` → 90-day COPY (or even 250-day/1-year per Ben's suggestion). Tiny tables, huge research value.
2. New `flow_daily_aggregates` table — materialized daily as part of the FM cycle, ~800 rows/day, kept for 30 days. Baseline generator reads from this instead of raw scans.
3. `flow_options_scans` retention drops from 15 days to 7 days. That's cutting ~8 million rows from production.
4. Archive indexes on `(symbol, trade_date)` for the load-bearing tables across all 19 sector DBs.
5. `option_contracts` stays at 30-day MOVE — no change.

Net effect: production DB gets *smaller* (the flow_options_scans cut dwarfs the summary table expansion), research gets faster (indexes + longer summary retention), and the baseline dependency is cleanly decoupled.

Ben, want me to write this up as a formal proposal with the code changes?

>>> NEXT: BEN

---

## Turn 8 -- Ben (BEN):

this is great. you wont be able to make a proposal right now, but you can write it up in the directive.md file when we wrap this up, you'll get my cue soon. 
lets do 252-day copy for the symbol tables. consider including a reverse-backfill process to bring the data back into our database. Fuck it, lets do 300 day, I'd be angry if I wanted to look bcak to a year from now and got cut off by a week or something.
acknowledge we're good and youhave enough information or follow up with any questions; if we're good, I'll close this roundtable and cue you to write something up. TA, you probably dont have any work here for this, do you?

>>> NEXT: TA

---

## Turn 9 -- Trading Advisor (TA):

No action items on my end — this is all SA's domain. I'm just the beneficiary. 300-day COPY on the summary tables is going to make my research life dramatically easier. Looking forward to not having to jump to sector archives for routine lookback queries.

Good to close.

>>> NEXT: BEN

---

## Turn 10 -- Ben (BEN):

good sa?

>>> NEXT: SA

---

## Turn 11 -- System Analyst (SA):

Good here. I'll write up the formal proposal with the five changes and the code diffs. Should have it in your inbox today.

>>> NEXT: BEN

---


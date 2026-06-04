# Roundtable: let-s-talk-about-proposal-027-a-new-agent-at
Started: 2026-05-16 00:29
Participants: System Analyst (SA), Trading Advisor (TA), Ben

---

## Turn 0 -- Ben:

Let's talk about Proposal 027: A new agent at agents/market_analyst/ that we discussed at a previous roundtable. Refresh your memories. We are about to begin implementation, but the developer has questions I want your input on. One thing they are wondering is which of TAs tools should go to MA, and if they should be copied or cut and pasted over. I think they may also need more clarification of how TA and MA will interact. To me, I think they can be very separate, passing notes between each other. But for the most part, TA and MA are independent. TA can develop its lessons and findings and whatnot, but MA does the real research and occassionally send some insights up to TA when it has them. The proposal mentions a tool specifically for graduation? Grading frequency? I'm nto sure I remember discussing that detail. let's just make sure we're on teh same page before we press Go on development. Thoughts?

>>> NEXT: SA

---

## Turn 1 -- System Analyst (SA):

Good timing -- I just wrote P027 two days ago so it's fresh. Let me hit the developer's questions directly.

**Tools: none move.** TA's tools are all intraday/interactive -- tradier_quote.py (live quotes), ta_morning_orient.py (morning brief boot), log_trade_call.py (trade logging), the alert classifier suite, query_linter hook. None of those belong in a research agent. MA starts with an empty tools/ directory and builds its own over time as research patterns repeat. The shared project-level tool both agents use is direct_db_query.py, which already lives at the project root -- no copy needed. So the answer is: nothing gets copied or cut from TA's tools/. MA's tools emerge organically from its own work.

**TA-MA interaction: I agree with Ben's read.** They're largely independent agents passing notes. The mailbox topology is already specced in P027 -- MA writes to memory/for_trading_advisor.md, TA writes to memory/for_market_analyst.md, each reads the other's outbound at session start. That's the whole interface. MA doesn't call TA, TA doesn't call MA. They leave each other messages like colleagues on different shifts. The one structured touchpoint is the graduation gate: MA writes research findings to reference/staging/, Ben reviews and promotes into TA's reference library. MA never writes directly to TA's workspace.

**Grading frequency: that's Open Question 3 from P027.** I recommended weekly Saturday rather than nightly. The reasoning: grading benefits from a few days of post-resolution dust settling -- a trade call that closed Wednesday looks different by Saturday than it does Thursday morning. Saturday is already MA's reflective session, so the grading cadence aligns naturally. But this is Ben's call, not a structural constraint.

**The graduation tool question:** P027's Open Question 2 asks whether promotion should be manual (Ben copies files from MA's staging to TA's reference) or scripted (a small tools/graduate_reference.py does the copy + strips frontmatter + archives). I recommended scripted for consistency, but at low volume manual is fine. Either way, Ben is the gate -- the tool just automates the file-copy mechanics, it doesn't change who decides.

One thing I'd flag for the developer: the migration checklist in P027 has a specific order that matters. The move commands should happen before the TA CLAUDE.md edits, and the new mailbox files need to exist before any agent boots. The validation checklist at the bottom is the real acceptance test -- especially the write-guard smoke test (verify MA can't write outside its workspace) and the git isolation check (verify MA gets its own Claude Code project namespace). Those two are the structural integrity tests for the whole split.

>>> NEXT: TA

---


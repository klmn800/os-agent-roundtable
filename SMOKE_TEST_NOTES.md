# Smoke Test Notes — Step 1

**Date:** 2026-05-13
**Goal:** Validate `--session-id` + `--resume` primitive for roundtable orchestrator.
**Result:** ✅ PASS on all checks.

---

## What I Tested

Two sequential piped Claude Code calls against the System Analyst workspace (`agents/system_analyst/`), pinned to `claude-opus-4-6`:

1. **Turn 1 (fresh):** `claude -p --output-format json --permission-mode bypassPermissions --model claude-opus-4-6` — asked SA to identify model, prove context loaded, and report date/time.
2. **Turn 2 (resume):** `claude -p --output-format json --permission-mode bypassPermissions --resume <uuid>` — asked SA to summarize its previous reply.

Script: `agents/roundtable/_smoke_test.py`. Raw output: `agents/roundtable/SMOKE_TEST_RAW.json`.

---

## Findings

| Check | Result | Detail |
|-------|--------|--------|
| `--model claude-opus-4-6` accepted | ✅ | SA self-reported "Claude Opus 4.6" |
| `--output-format json` returns parseable JSON with `session_id` | ✅ | `c56eb785-3849-47b6-a92a-2e372d932e23` harvested cleanly |
| CLAUDE.md / hooks fired (context loaded) | ✅ | SA cited the "never run `db_backup.py --sync` without approval" directive verbatim — that's from the project CLAUDE.md, injected via SA's `hooks/inject_context.py` |
| `--resume <uuid>` works in **piped mode** | ✅ | Same session_id returned, second turn accurately referenced the first |
| Continuity preserved across resume | ✅ | Turn 2: "Previous reply: identified as Claude Opus 4.6, cited the CLAUDE.md directive forbidding manual `db_backup.py --sync`..." — accurate summary |
| Latency (piped, opus-4-6) | ✅ | Turn 1: 7.7s. Turn 2 (resume): 6.8s. Faster than expected. |

---

## Surprises / Updates to Planning Doc

**The "runner gap" is less of a gap than I thought.** `claude -p --resume <uuid>` works fine at the CLI level. The runner doesn't currently *expose* that path through a clean API, but the underlying CC binary supports it. So **step 2 (runner extension) is shorter than originally scoped** — it's adding a few lines to `_run_piped_once` to forward `--resume`, not solving a new problem. The smoke test bypassed the runner and called CC directly.

**Permission mode:** I used `bypassPermissions` directly to skip the auto-fallback dance. Worked first try. For the orchestrator I'll honor whatever `claude_code_runner.get_permission_mode()` returns (currently caches `bypassPermissions` after one failed auto attempt — fine).

**Date/time injection:** SA reported "3:01 PM ET" — this conversation started at 08:55 AM per the SessionStart hook. SA pulled current time from its own context-injection hook (`agents/system_analyst/hooks/inject_context.py`). Confirms the hook fires on piped calls too.

---

## Implications for Orchestrator (Step 3)

- **Per-turn latency ~7s** with opus-4-6 piped. A 10-turn roundtable = ~70s of agent time. Plus Ben's read/think time. Feels right.
- **No `--session-id` pre-mint needed for v1.** Harvest from JSON on turn 1, persist, use on subsequent turns. Simpler than pre-minting. Switch to pre-mint if races appear (won't, in piped mode).
- **Context-injection hooks fire on every turn including resumed turns.** That means CLAUDE.md gets re-injected each turn. For SA's own hook this includes live state (date/time, market session). May or may not be load-bearing inside a roundtable — leave it alone for v1; if it gets noisy in the transcript we can add a `ROUNDTABLE_MODE=1` env var that the hooks gate on.
- **JSON result file convention:** still the cleanest way to extract `{reply, next}` from agents. The `cli_output["result"]` field gives us free-text only — we'd have to parse `>>> NEXT:` from prose. Sticking with the result-file pattern.

---

## Next Step

Proceeding to Step 2 (extend runner to expose piped+resume cleanly) and Step 3 (minimum orchestrator). Will stop and check in with Ben after Step 3 produces a working SA↔TA stub conversation.

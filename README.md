# Roundtable

A turn-taking chat between two existing Claude Code agents — currently System Analyst (SA) and Trading Advisor (TA) — with Ben observing and pausing in when an agent tags him. Each agent's session persists across turns via `claude --resume`, so context stays warm and the conversation has continuity. At the end, each agent writes its commitments back to its own `directive.md` so the next regular session inherits the outcome.

The phone-to-phone-with-mic-against-speaker analogy: two CC sessions, a shared transcript as the wire between them, and an orchestrator pumping replies back and forth.

---

## Quick start

```powershell
# Real conversation seeded from one of SA's open questions
python agents\roundtable\orchestrator.py --seed-question Q4

# Custom inline prompt
python agents\roundtable\orchestrator.py --seed "SA and TA, I want you to brainstorm merger arbitrage signal design."

# Custom prompt from a file (good for long/multi-paragraph kickoffs)
python agents\roundtable\orchestrator.py --seed-file ideas/merger_arb_brainstorm.md

# Make TA respond first instead of SA (default is SA)
python agents\roundtable\orchestrator.py --seed "TA, what's your read on..." --first-speaker TA

# Override the auto-derived topic slug (the filename-safe label used for state + transcript)
python agents\roundtable\orchestrator.py --seed "..." --topic-slug merger-arb-brainstorm

# Stub handshake (smoke test — agents just introduce themselves)
python agents\roundtable\orchestrator.py
```

`--seed-question`, `--seed`, and `--seed-file` are mutually exclusive — pick one. With `--seed` or `--seed-file`, the orchestrator auto-derives a topic slug from the first meaningful line; use `--topic-slug` to override.

**Who responds first?** SA by default. Add `--first-speaker TA` to flip it. This only matters at roundtable start — once the conversation is rolling, agents tag each other (or `BEN`) via the `next` field in their replies. `--first-speaker` is ignored when resuming an existing roundtable (the next speaker is already in state).

Must be run in your own terminal (the orchestrator reads stdin when you're tagged).

### What's a "topic slug"?

A filename-safe label for the roundtable (lowercase, dashes instead of spaces, no punctuation). Used for the state file (`state/<slug>.json`), the per-roundtable result subdir (`state/<slug>/`), and the transcript filename (`transcripts/YYYY-MM-DD_<slug>.md`). Most of the time you don't think about it — the auto-derived one is fine. Override with `--topic-slug` when you want a specific name (e.g. for ease of resuming the same conversation later).

## What happens

1. **Orchestrator writes a seed** as Ben's turn 0 in a new transcript. For `--seed-question Q4`, it pulls the Q4 block from `agents/system_analyst/questions_for_ben.md` and wraps it in Ben's framing ("Yo SA, we've got TA here on the line...").
2. **First speaker (SA by default) runs.** Fresh CC session in the agent's workspace (~25s — full context load with hooks, CLAUDE.md, memory). Agent writes JSON `{"reply": "...", "next": "SA|TA|BEN"}` to a per-turn result file. Orchestrator appends to transcript.
3. **Other agent's first turn.** Same shape.
4. **Subsequent turns** for each agent are resumed sessions (`claude --resume <uuid>`, ~7s) — much faster, KV cache hot, agent's own session memory carries continuity.
5. **When an agent tags `BEN`,** orchestrator pauses, prints the last reply, and reads multi-line stdin:
   ```
   > _
   ```
   - Type your reply, empty line on its own to send.
   - `/skip` to bounce straight back to the speaker who tagged you (no Ben message).
   - `/quit` to exit. State preserved.
6. **After sending,** pick who replies next: `SA` or `TA`. Loop continues.
7. **Ctrl+C anytime** — state preserved, no closing summary.
8. **On clean exit (`/quit`),** orchestrator prompts: *"Ask SA and TA to write closing-commitment summaries into their directive.md files? [Y/n]"*. Answer `Y` and each agent runs once more (fresh session, ~30-80s) to extract ITS commitments from the transcript and produce a directive section. The orchestrator appends each to the corresponding agent's `directive.md`.

## Watching live (optional, two-window setup)

The orchestrator already prints each turn as it lands in the window where you started it. But that window also has status messages, timing info, your input prompts, etc. — useful but cluttered. If you want a clean second window showing **just the transcript** as it grows, here's how.

**Easiest path (works from cmd OR PowerShell):**

When the orchestrator starts, it prints a `Follow live:` line — exactly the command to run. Just copy-paste it into a second terminal window. Looks like:

```
Follow live: powershell -Command "Get-Content -Wait 'E:\options_scanner\agents\roundtable\transcripts\2026-05-13_q4-...md'"
```

Paste, hit Enter, leave the window open. Turns will stream in as they land.

**If you're already in PowerShell** (prompt looks like `PS E:\options_scanner>`):

```powershell
Get-Content -Wait E:\options_scanner\agents\roundtable\transcripts\<the filename>.md
```

> If you see `'Get-Content' is not recognized as an internal or external command`, you're in `cmd.exe`, not PowerShell. Type `powershell` and hit Enter to switch — or just use the copy-paste line above, which works from either shell.

## File layout

```
agents/roundtable/
├── README.md                 (this file)
├── PLANNING.md               (design doc — decisions, patterns, future ideas)
├── orchestrator.py           (the loop)
├── _smoke_test.py            (one-off CLI primitive test; not load-bearing)
├── SMOKE_TEST_NOTES.md       (results from the primitive smoke test)
├── transcripts/
│   └── YYYY-MM-DD_<slug>.md  (one per roundtable — live-appended)
├── state/
│   ├── <slug>.json           (per-roundtable state: session UUIDs, history, last_message)
│   └── <slug>/               (per-roundtable result files + closing summaries)
│       ├── turn_NN_X_result.json
│       └── closing_X.json
└── logs/
    ├── prompt_turn_NN_X.txt  (exact prompt sent to agent X on turn NN)
    └── session_turn_NN_X.md  (agent X's final reply text on turn NN)
```

## Resuming an existing roundtable

Re-run with the same `--seed-question` (or `--topic-slug`). The orchestrator detects the existing state file, prints "Resuming existing roundtable...", and picks up where it left off (correct next speaker, intact session UUIDs).

To start fresh on the same topic: delete the state file + transcript first.

```powershell
Remove-Item agents\roundtable\state\q4-*.json
Remove-Item agents\roundtable\state\q4-* -Recurse
Remove-Item agents\roundtable\transcripts\*q4-*.md
```

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Agent refuses to write the result file | Cultural workspace-boundary instinct overriding the explicit "this write is pre-approved" prompt | Per-turn prompt already says the write is pre-approved. If it still happens, the agent's reply text is in `logs/session_turn_NN_X.md` — extract manually, write to the expected result file, restart orchestrator (resumes cleanly). |
| Permission-mode auto fails on first call | Tier mismatch or CLI version | Runner auto-falls-back to `bypassPermissions` and prints a one-line notice. No action needed. |
| Same JSON keeps appearing across roundtables | Result-file collision (pre-2026-05-13 bug) | Should not recur — result files are now namespaced under `state/<slug>/`. If it does, check that `state/<slug>/` exists. |
| Conversation looped without Ben | Either agents kept tagging each other, or `MAX_TURNS=20` was hit | `MAX_TURNS` is a soft safety cap — adjust in `orchestrator.py` if 20 is too low. Or Ctrl+C and inject. |
| Closing summary duplicate in directive.md | Agent self-edited directive.md instead of (or in addition to) writing only the JSON | Closing prompt forbids self-writes; if it still happens, dedupe by hand. |

## Design and rationale

See `PLANNING.md` in this directory for:
- Architecture overview and data flow
- Key design decisions (and the alternatives considered)
- Bug history and what each one taught us
- Future iteration ideas (agent-initiated roundtables, richer UI, N-party support)

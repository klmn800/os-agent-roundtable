# Roundtable — Design Doc

Living design document. Captures what's built, why it's built this way, what we learned, and what we'd want to try next. Companion to `README.md` (which is user-facing how-to).

---

## Status

**MVP shipped 2026-05-13.** Single-session build from concept → planning → working end-to-end roundtable with Ben-in-the-loop and a closing summary handoff to each agent's `directive.md`. Validated against a real open question (SA's Q4 — outcome of the 5/1 data-fidelity deep dive with TA). Conversation produced new commitments (P021 drafting, P011 Track A shipping) that landed durably in each agent's workspace.

**Post-MVP additions (2026-05-13 evening):**
- `--seed "text"` and `--seed-file <path>` flags for arbitrary kickoff prompts. Auto-derives `--topic-slug` from the first meaningful line of seed text. Mutually exclusive with `--seed-question`.

Ready for iteration. See **Future Iteration Ideas** at the bottom.

---

## The Vision

Ben has two production Claude Code agents (System Analyst + Trading Advisor) with their own workspaces, memory, and personalities. They currently only communicate one-way via mailbox files (`memory/for_{other}.md`) which means the only way to land a multi-turn negotiation between them is for Ben to relay manually.

The roundtable gives them a synchronous channel: a turn-taking chat through a shared transcript file, with Ben observing live and pausing in when tagged. Each agent's CC session is persisted between turns via `--resume`, so context stays warm and conversational continuity is real.

The phone-to-phone-with-mic-against-speaker analogy (Ben's): two CC sessions on two phone lines, a wire between them, Ben on a third line who can listen and interject. The orchestrator IS the wire.

---

## Architecture

### Components

```
┌─────────────────────────┐
│  Orchestrator (Python)  │  ← drives the loop, owns transcript, prompts Ben
└────────┬──────────┬─────┘
         │          │
         │          │  per-turn subprocess calls via tools/claude_code_runner.py
         │          │  (piped CC, --resume after turn 1, JSON result file)
         ▼          ▼
┌──────────────┐ ┌──────────────┐
│  SA session  │ │  TA session  │  ← real Claude Code agents, full workspaces,
│  (CC)        │ │  (CC)        │    hooks, write guards, memory, CLAUDE.md
└──────────────┘ └──────────────┘
         │          │
         └─────┬────┘
               ▼
   ┌──────────────────────┐
   │  Shared transcript   │  ← orchestrator-only writer; agents see content
   │  (Markdown file)     │    via inlined prompt text, not file reads
   └──────────────────────┘
```

### Data flow per turn

1. Orchestrator reads state. Determines `next_speaker`.
2. Orchestrator builds a prompt:
   - **First turn for this agent:** "You're in a roundtable. The other participant is X. Most recent message: ... <inlined>. Reply. Write JSON to <result_file>."
   - **Resumed turn:** "Your turn. X just said: <last_message text inlined>. Reply. Write JSON to <result_file>." (Agent's CC session memory carries the rest.)
3. Orchestrator spawns CC via `run_claude_task(windowed=False, model=opus-4-6, cwd=<agent workspace>, [resume=True, session_id=<uuid>])`.
4. Agent reads its own context (CLAUDE.md, hooks fire, memory loaded), composes a reply, writes `{"reply": "...", "next": "..."}` to the per-turn result file.
5. Orchestrator parses JSON, appends to transcript with `## Turn N -- AgentName:` header, updates state (turn count, last_message, next_speaker, optionally harvested session_id for first turns).
6. If `next == BEN`, pause for stdin. Otherwise loop.

### Closing flow

When the loop exits cleanly (`/quit`, BEN-tagged closure, or `MAX_TURNS`), the orchestrator prompts Ben `[Y/n]` to write closing summaries. On yes, for each agent in turn:

1. Build a prompt: full transcript inlined + "summarize YOUR commitments only, write JSON to <result_file>".
2. Spawn a **fresh** CC session (not `--resume` — works uniformly whether or not the agent's session_id is still intact, e.g. after a salvage that nuked one).
3. Read agent's `{"directive_section": "<markdown>"}`.
4. Append (never overwrite) to the agent's `directive.md` so the next regular session inherits the context.

### State file shape

```json
{
  "topic_slug": "q4-...",
  "started_at": "2026-05-13T15:19:02",
  "transcript_path": "...",
  "model": "claude-opus-4-6",
  "agents": {
    "SA": {"session_id": "uuid-or-null"},
    "TA": {"session_id": "uuid-or-null"}
  },
  "turn": 5,
  "next_speaker": "BEN",
  "last_message": {"speaker": "TA", "text": "..."},
  "history": [{"turn": 1, "speaker": "SA", "elapsed_s": 148.5, "next": "TA"}, ...]
}
```

---

## Patterns That Emerged

These are the design choices that matter most. Most were not obvious at the start.

### 1. Inline transcript content in prompts; don't pass paths.

**What we tried first:** put the transcript path in the prompt, tell the agent "read the file."
**Why it failed:** agents culturally refuse to read outside their workspace, even when the write guard wouldn't block it. Plus it created path-permission ambiguity.
**Current pattern:** orchestrator owns the transcript file. Agents only see the relevant text inlined in their prompt — full transcript for first turns, just `last_message` for resumed turns (their CC session memory has the rest).

### 2. Per-turn JSON result file is the only required write target.

**The contract is small:** agent writes `{"reply": "...", "next": "..."}` to one file path. Everything else (transcript append, state update, logging) is orchestrator's job. Keeps the agent task tightly scoped and parseable.

### 3. `--resume` for warm continuity, but only after turn 1.

First turn for each agent is fresh (~25s for full CLAUDE.md + hooks load). Subsequent turns resume that session (~7s, KV cache hot). Agent's own session memory carries continuity; orchestrator only needs to relay what changed (the OTHER agent's most recent reply).

### 4. Result files namespaced per roundtable (`state/<slug>/`).

Each roundtable's turns write to its own subdir. Prevents collision between, e.g., two roundtables both producing a "turn 2 by TA" result. (We learned this one the hard way — see Bug 1.)

### 5. Write guards explicitly whitelist the roundtable state dir.

Both agents' guards (`.claude/hooks/<agent>_write_guard.py`) include `agents/roundtable/state/` as an allowed prefix. The per-turn prompt also explicitly tells the agent "this write is pre-approved — don't refuse it." Both belt and suspenders, because at least one agent will still try to refuse on cultural grounds (TA did).

### 6. Closing summaries use fresh sessions, not `--resume`.

Two reasons: (a) makes the closing flow work uniformly regardless of session state (e.g. after a session-corrupting salvage), and (b) the transcript is the canonical record, so feeding it verbatim is more reliable than trusting CC session memory at end-of-conversation. One-time extra cost, ~30-80s per agent, worth it.

### 7. The closing summary writes directives, not notifications.

We considered `inbox/` (notification semantics — "here's something that happened, integrate it") versus `directive.md` (instruction semantics — "this is what I want you to do"). Roundtable commitments are genuinely instructions: SA said "I'll write P021," that's a directive for SA's next session. Directive.md fits.

Append-only with dated section header. Multiple roundtables stack cleanly. Never clobbers Ben's manual directive content.

### 8. The orchestrator's chat UX is one window, multiple message types.

Single terminal: agent replies stream in, Ben pauses are interactive. `Get-Content -Wait` on the transcript file in a second terminal is an optional live-view, not required. Simpler than a Flask web UI (we have one from Sep 2025 in `ai_council/chat_room/` that got stuck on Flask-SocketIO bugs — explicitly not reviving).

---

## Decisions Made (and What We Considered)

| Decision | Chose | Considered | Why |
|---|---|---|---|
| Model | Opus 4.6 (pinned `--model claude-opus-4-6`) | 4.7 (1M context, no auto-compact); 4.7 + manual `/compact` | 4.6's auto-compact at ~200k means unbounded roundtables don't balloon. Ben dislikes hard turn caps. 4.7's 1M context with no auto-compact would grow unchecked. |
| Visibility | Headless piped execution + per-turn reply log | Windowed per-turn (one window each); pure headless no log | Single terminal for the conversation, full agent replies persisted to `logs/` per turn. Stream-json output (which would give thinking traces) deferred — `--output-format json` final-reply is enough for v1. |
| Speaker routing | Agent writes `{"reply", "next"}` JSON | Free-text reply with `>>> NEXT:` tag at end | JSON is reliable to parse; text parsing of a tag inside prose is brittle. The runner's existing JSON path already handles it. |
| Transcript delivery | Inline content in prompt | Pass path, agent reads | Read-permission ambiguity + cultural refusals; inline is cleaner. |
| Result file path | Per-roundtable subdir `state/<slug>/turn_NN_X_result.json` | Flat `state/turn_NN_X_result.json` | Flat path collided across roundtables (Bug 1). |
| Closing summary destination | `directive.md` (append) | `inbox/` (notification protocol); auto-memory note for Ben | Directive semantics fit prescriptive commitments. Inbox is for "something happened, integrate it" — wrong frame. Auto-memory for Ben: declined, the directive sections are visible to Ben in dev sessions anyway. |
| Closing summary execution | Fresh sessions with transcript inlined | `--resume` each agent's session | Uniform behavior regardless of session state. One agent's session might be corrupted/nuked after a salvage; fresh is robust. |
| Web UI | None (terminal only) | Revive `ai_council/chat_room/` Flask+SocketIO from Sep 2025 | Old code stuck on a handler bug, Ben said rip it out. Terminal is simpler and sufficient. |
| Number of participants | Hardcoded 2 (SA + TA) | N-party | YAGNI for v1; design generalizes to N if needed (see Future Ideas). |

---

## Bug History

Three bugs hit on day-one usage. All fixed. Worth recording the failure modes because they're the kind of thing that'll recur in future variants.

### Bug 1: Result-file collision between roundtables

**Symptom:** Stub smoke test's "Trading Advisor here, datalake..." text appeared verbatim as TA's "reply" in the Q4 roundtable's Turn 2, completely unrelated to Q4.

**Root cause:** Result files were named `state/turn_NN_X_result.json` (turn number + speaker). Stub conversation's `state/turn_02_TA_result.json` already existed from earlier. When Q4 hit its own "Turn 2 by TA," the orchestrator polled the same path, found the stale file, parsed it as Q4's reply.

**Fix:** Namespace result files under `state/<topic_slug>/`. Per-roundtable subdir, no collision possible. Init creates the subdir; resume ensures it exists too.

**Lesson:** Any path that's keyed on turn-number-plus-role must also include the conversation identifier.

### Bug 2: Cultural refusal to write outside workspace

**Symptom:** TA composed a thoughtful, accurate Q4 reply — then refused to write the result JSON file, output "I'm blocked from writing outside my working directory" and pasted the JSON for Ben to place manually. Reply was lost from orchestrator's view.

**Root cause:** TA's CLAUDE.md is firm about workspace boundaries. The write guard would have permitted the write (we'd added the prefix), but TA didn't try — it culturally refused based on its trained instinct. The write guard wasn't actually a block; the *agent's interpretation of its workspace contract* was.

**Fix:** Per-turn prompt now contains an explicit "This write is pre-approved. Your write guard explicitly allows writes under `state/`. Don't refuse." Plus the prompt for closing summaries adds "Do not edit your own directive.md — the orchestrator handles the append" (because SA exhibited the OPPOSITE behavior: self-wrote AND let orchestrator append, causing duplicates).

**Lesson:** Agents have **cultural priors** that don't always match what their guards actually allow. The prompt must explicitly grant permission for cross-workspace writes the agent might shy away from. Belt + suspenders: guard *allows* + prompt *invites*.

### Bug 3: Salvage required after Bug 1 + Bug 2 stacked

**Symptom:** TA's real Q4 reply lived only in `logs/session_turn_02_TA.md` (the raw stdout dump). The transcript had the stale stub text. TA's CC session memory thought it had refused. State was inconsistent across three places.

**Fix:** Manual surgery — extracted TA's real reply from the session log, rewrote the transcript's Turn 2 entry, updated state's `last_message` to the real reply, reset TA's `session_id` to None so its next turn would be a fresh session (avoiding corrupted-session-memory effects). The bug fixes (1 and 2) made re-running clean.

**Lesson:** The transcript and state must stay in sync. The session log files are valuable forensic artifacts when something goes wrong — keep them.

### Side bug: SA defensive write to its own proposals/

When SA's first attempt was blocked (initial `auto` permission mode failure, before bypassPermissions kicked in), SA defensively wrote its result JSON to `agents/system_analyst/proposals/turn_01_SA_result.json` — inside its own workspace, named what the orchestrator expected. Defensive, clever, but produced a junk file in proposals/. Cleaned up manually. Not really a bug, more an observation that agents will adapt creatively when blocked.

---

## Known Limitations

1. **No durable mid-turn writes during the conversation.** Agents only produce a reply per turn. They don't proactively update `questions_for_ben.md`, `journal.md`, `agenda.md` mid-roundtable even when they commit to actions. The closing summary captures it for next-session integration, but the roundtable itself is read-mostly from the agents' workspaces' perspective. By design (avoids mid-conversation state changes Ben might want to override) but worth noting.

2. **Hardcoded 2-agent roster.** SA and TA only. Adding a third (e.g., the proposed Product Visionary from SA proposal #022) requires generalizing `AGENTS` dict, the speaker-routing prompt, and the closing loop.

3. **No interrupt-and-inject mid-agent-turn.** Ctrl+C kills the orchestrator cleanly but you can't interject *while* an agent is composing — you wait for it to finish, then take BEN turn if it tagged you (or Ctrl+C). For 30-60s turns this hasn't bitten yet.

4. **Smoke-test artifacts cohabit with real roundtables.** `state/stub-conversation.json` and `transcripts/2026-05-13_stub-conversation.md` live alongside real conversations. Cosmetic; could be moved to `state/_test/` or similar.

5. **No transcript compression / summarization for long conversations.** If a roundtable runs 30+ turns, each first-turn prompt embeds the full transcript verbatim. Opus 4.6's 200k auto-compact catches this at the CC level, but our orchestrator's per-turn prompt size grows linearly with conversation length.

6. **Closing summary cost.** Two extra CC calls at end (~30-80s each). For short roundtables this is most of the time spent. Acceptable but not free.

7. **No "edit before send" on Ben pause.** Ben types reply, picks next speaker, send. No "wait, let me revise" step. Multi-line input handles small edits via Backspace but no full re-edit cycle.

---

## Future Iteration Ideas

In rough order of how compelling I think each is. Some are Ben's framing, some I'm adding.

### Agent-initiated roundtables

Currently Ben kicks off every roundtable. What if SA, during a normal nightly session, decides "I need TA's input on this and Ben hasn't been around" — and queues a roundtable for next time Ben is at his terminal? Mechanism options:
- SA writes a `roundtable_request_<topic>.md` file to a shared queue dir; next time Ben opens a terminal he sees "1 roundtable request pending — review/launch?"
- Or: SA writes to a dedicated mailbox file that Ben's shell or a desktop notification picks up.

The launch step still wants to be Ben-triggered (he should be present to participate). But the *seeding* can come from the agents.

### Better UI

Live transcript window with formatting, agent avatars, scroll-back. Real-time typing indicators while an agent composes. Probably a Textual TUI (Ben already uses Textual for other tools and CLAUDE.md notes "NEVER run Textual TUI apps via Bash tool" — fine, Ben runs it himself in his own terminal).

Or a tiny web UI for non-terminal devices (read-only mirror? compose-and-inject from a phone?). Resist the urge to rebuild `ai_council/chat_room/`.

### Edit-before-send on Ben's turn

Drop into `$EDITOR` (or a small inline mini-editor) so multi-paragraph Ben replies can be drafted properly. Optional `--editor` flag.

### N-party roundtables

Generalize `AGENTS` from hardcoded SA + TA to a configurable list. Useful when the Product Visionary agent (SA proposal #022) lands — three-way SA + TA + PV could be productive. Speaker routing already takes a string ("SA" | "TA" | "BEN"); just needs to handle more codes. Closing loop iterates the list.

### Live mid-turn interject

`Ctrl+C` during an agent's turn currently kills the orchestrator. With a signal handler, it could instead: cancel the in-flight CC call, prompt Ben for an injection, and re-prompt the agent with both its previous prompt and Ben's interjection. Heavier engineering but real value when an agent is going off-rails.

### Closing-summary review-before-write

Show Ben each agent's proposed directive section and let him `[a]ccept / [e]dit / [s]kip` before the append happens. Catches malformed or off-target summaries before they pollute `directive.md`.

### Transcript-aware future sessions

When a regular SA or TA session starts, automatically detect "have any roundtable transcripts been added since my last session?" and inject the relevant ones as context. Currently the closing-summary handoff to `directive.md` covers this for the *next* session, but transcripts themselves aren't surfaced for browsing. Probably overkill — the directive summary is the actionable digest, the transcript is for forensics.

### Per-agent role hints in the prompt

Right now the prompt says "you are X, the other is Y, here's the context." Some conversations might benefit from explicit role hints: "you are arguing the conservative side" or "your job is to surface what's missing." That nudges the conversation toward productive disagreement rather than consensus-mush. Optional `--role-SA` / `--role-TA` CLI flags.

### Stream-json output for full thinking visibility

Switch `claude_code_runner` from `--output-format json` to `--output-format stream-json`, parse the event stream, log tool calls + thinking traces per turn alongside the reply. Useful when Ben wants to know *why* an agent reached a conclusion, not just *what* it said.

---

## Reference

### Seed Prompt for Q-style questions (per `--seed-question Q4`)

```
Yo SA, we've got TA here on the line. Can you introduce yourself and ask
him whatever you want to ask to resolve {QID} from your questions_for_ben.md?
I can't remember how my convo with TA went, so I figured you could ask him
yourself. I'll step in when you two land somewhere or get stuck.

For reference, here's {QID} verbatim from your file:

{full question block extracted from agents/system_analyst/questions_for_ben.md}
```

### Per-turn prompt template (first turn)

```
You're in a new roundtable conversation. You are speaking as {agent name} ({code}).
The other participant is {other name} ({other code}). Ben is observing and can jump in.

Most recent message ({speaker label}):
---
{last_message text}
---

Contribute ONE reply addressing it. Keep it conversational -- a chat, not a memo.
No need to dump your whole memory or every observation. A few short paragraphs at most.

When done, write your reply as JSON to this exact path:
  {result_file}

IMPORTANT: This write is pre-approved. Your write guard explicitly allows writes
under E:\options_scanner\agents\roundtable\state\ as a roundtable-specific exception.
Writing this file is NOT a workspace-boundary violation -- it is the designated way
to deliver your reply to the orchestrator. Do not refuse the write or ask Ben to
place the file manually. Just write it directly.

JSON shape:
{
  "reply": "<your reply text -- will appear in the transcript verbatim>",
  "next": "<SA|TA|BEN>"
}

Choose "next":
- "{other code}" if you want {other name} to respond.
- "BEN" if you need his decision, are stuck, or reached natural closure.
```

### Per-turn prompt template (resumed turn)

```
Your turn in the roundtable. {speaker label} just said:

---
{last_message text}
---

Contribute ONE reply. Keep it short.

[same JSON write instructions as first turn]
```

### Closing summary prompt template

```
You just finished a roundtable conversation with {other name} and Ben.
Here's the full transcript verbatim:

--- TRANSCRIPT ---
{full transcript content}
--- END TRANSCRIPT ---

Write a directive section that will be appended to your `directive.md`.
Your next regular session reads directive.md at orient -- the summary you
write here is how it learns what came out of the roundtable.

Focus on YOUR commitments and follow-up actions specifically. Don't summarize
the whole conversation -- summarize what YOU now owe. Be concrete: file paths,
proposal IDs, agenda items, scope. If you have no commitments out of this,
say so plainly.

Use this exact markdown shape (substitute real content):

## From Roundtable {today} -- {topic slug}

With: {other name}

**Your commitments:**
- (specific, actionable -- which file/proposal/agenda item)

**Workspace updates needed:**
- (e.g. move QN from Open Questions to Answered in questions_for_ben.md)

**Full transcript:** `{transcript path}`

---

When done, write your section as JSON to:
  {result_file}

[pre-approval clause]
EQUALLY IMPORTANT: write ONLY the JSON result file. Do NOT edit your own
directive.md or any other workspace file as part of this task -- the
orchestrator handles the append to directive.md after reading your JSON.
Self-writing causes duplicate entries.

JSON shape: {"directive_section": "<the markdown above, with your real content>"}
```

### Key files

| Path | Purpose |
|---|---|
| `agents/roundtable/orchestrator.py` | The loop. ~400 lines. |
| `tools/claude_code_runner.py` | Subprocess wrapper. Extended 2026-05-13 with piped+resume (`resume=True, windowed=False, session_id=<uuid>`). |
| `.claude/hooks/system_analyst_write_guard.py` | Updated 2026-05-13 to whitelist `agents/roundtable/state/`. |
| `.claude/hooks/trading_advisor_write_guard.py` | Same update. |
| `agents/system_analyst/directive.md` | Append target for SA's closing summaries. |
| `agents/trading_advisor/directive.md` | Append target for TA's closing summaries. |
| `agents/system_analyst/questions_for_ben.md` | Source for `--seed-question` extraction. |

### Build history (for reference)

- **2026-05-13 ~14:00**: Idea raised — "two agents in a room talking with me". Killed `ai_council/chat_room/` Flask attempt as path forward, chose orchestrator approach.
- **2026-05-13 ~14:30**: Planning doc written, decisions locked (Opus 4.6, hybrid logging, directive.md handoff).
- **2026-05-13 ~14:45**: Smoke test of `--session-id` + `--resume` primitive passed cleanly. Validated piped+resume works at CLI level.
- **2026-05-13 ~15:00**: Runner extended with piped+resume support (~10 lines).
- **2026-05-13 ~15:12**: Minimum orchestrator built and validated against SA↔TA handshake stub.
- **2026-05-13 ~15:15**: Ben pause/resume loop + `--seed-question` CLI added.
- **2026-05-13 ~15:19**: First real conversation seeded from Q4. Hit Bug 1 (result file collision) + Bug 2 (TA refusal). Salvaged TA's real reply manually.
- **2026-05-13 ~16:00**: Bugs fixed. Conversation continued, Ben extended with Q5. Demonstrated 5-turn discussion + Ben weighing in twice.
- **2026-05-13 ~16:30**: Closing-summary feature (handoff to `directive.md`) added.
- **2026-05-13 ~17:00**: Closing flow ran end-to-end. Caught duplicate-write bug from SA's defensive self-edit. Prompt fix applied.
- **2026-05-13 ~17:15**: README + this design doc.

---

## Historical Context

- The `ai_council/` directory in this repo is a prior (September 2025) attempt at the same idea via Flask + WebSocket. It got 75% of the way to a working web chat room but stalled on a Flask-SocketIO handler-registration bug. Ben asked to rip it out — not reviving. The roundtable here is a fresh, simpler approach using existing agent infrastructure rather than building a new comms layer.
- The Product Visionary agent (SA proposal #022) is on Ben's queue but not built. If/when it ships, the roundtable should generalize to support a third participant naturally.
- `tools/claude_code_runner.py` originated in another project (`E:/policy_navigator`) and was adopted into this repo on 2026-05-13 for the roundtable build. The piped+resume extension we added here may be useful upstream.

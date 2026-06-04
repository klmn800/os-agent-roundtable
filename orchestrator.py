"""Roundtable orchestrator.

Drives a turn-taking conversation between two Claude Code agents (System
Analyst + Trading Advisor) via piped subprocess calls. Each agent's
session is persisted across turns via --resume. State and transcript
live in agents/roundtable/.

Ben observes the conversation as it builds (printed live) and is paused
into the loop when an agent tags BEN.

Usage:
    # Stub handshake (smoke test):
    python agents/roundtable/orchestrator.py

    # Real conversation seeded from one of SA's open questions:
    python agents/roundtable/orchestrator.py --seed-question Q4

    # Custom topic slug:
    python agents/roundtable/orchestrator.py --topic-slug my-topic
"""
import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.claude_code_runner import run_claude_task

ROUNDTABLE_DIR = Path(__file__).parent
STATE_DIR = ROUNDTABLE_DIR / "state"
TRANSCRIPT_DIR = ROUNDTABLE_DIR / "transcripts"
LOG_DIR = ROUNDTABLE_DIR / "logs"

MODEL = "claude-opus-4-6"
MAX_TURNS = 20  # safety cap; Ben dislikes hard caps but this prevents runaway loops

AGENTS = {
    "SA": {
        "name": "System Analyst",
        "cwd": REPO_ROOT / "agents" / "system_analyst",
    },
    "TA": {
        "name": "Trading Advisor",
        "cwd": REPO_ROOT / "agents" / "trading_advisor",
    },
}

STUB_SEED = (
    "Ben asks: SA and TA, this is a smoke test of a new roundtable feature "
    "where you two can talk directly with me observing. Briefly introduce "
    "yourselves to each other (one line each) and confirm you can hear each "
    "other. Then end the chat by tagging me with next=BEN. Keep it short."
)

SA_QUESTIONS_FILE = REPO_ROOT / "agents" / "system_analyst" / "questions_for_ben.md"


def transcript_path(topic_slug: str) -> Path:
    date = datetime.now().strftime("%Y-%m-%d")
    return TRANSCRIPT_DIR / f"{date}_{topic_slug}.md"


def state_path(topic_slug: str) -> Path:
    return STATE_DIR / f"{topic_slug}.json"


def _slugify(text: str, max_chars: int = 50) -> str:
    """Filename-safe slug: lowercase, dashes for non-alnum, trim at word boundary."""
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    if len(s) > max_chars:
        s = s[:max_chars].rsplit("-", 1)[0]
    return s


def _auto_slug_from_seed(seed_text: str) -> str:
    """Generate a topic_slug from the first meaningful line of seed text.
    Falls back to a timestamp if nothing usable found."""
    for line in seed_text.strip().splitlines():
        line = line.strip().lstrip("#").strip()
        if line:
            slug = _slugify(line)
            if slug:
                return slug
    return "roundtable-" + datetime.now().strftime("%H%M%S")


def load_question_seed(question_id: str) -> tuple[str, str]:
    """Extract a question from SA's questions_for_ben.md and frame Ben's seed.

    Returns (topic_slug, seed_message). Raises if question not found.
    """
    if not SA_QUESTIONS_FILE.exists():
        raise FileNotFoundError(f"SA questions file not found: {SA_QUESTIONS_FILE}")

    text = SA_QUESTIONS_FILE.read_text(encoding="utf-8")

    # Find the heading "### Qn (...)" and capture until the next "### " or "## "
    pattern = rf"###\s+{re.escape(question_id)}\b.*?(?=\n###\s+\w|\n##\s+\w|\Z)"
    m = re.search(pattern, text, re.DOTALL)
    if not m:
        raise ValueError(
            f"Question '{question_id}' not found in {SA_QUESTIONS_FILE}. "
            f"Check the heading style (expected '### {question_id} ...')."
        )
    q_block = m.group(0).strip()

    # Slugify the heading's title for the topic_slug.
    first_line = q_block.splitlines()[0]
    # Strip "### Q4", then any "(...)" date suffix, then a leading dash variant.
    title = re.sub(r"^###\s+\S+\s*", "", first_line).strip()
    title = re.sub(r"\([^)]*\)", "", title).strip()
    title = re.sub(r"^[-—–\s]+", "", title)
    qid_lower = question_id.lower()
    slug = _slugify(title)
    # Dedupe if title already starts with the question id.
    if slug.startswith(qid_lower + "-") or slug == qid_lower:
        topic_slug = slug
    elif slug:
        topic_slug = f"{qid_lower}-{slug}"
    else:
        topic_slug = qid_lower

    seed = (
        f"Yo SA, we've got TA here on the line. Can you introduce yourself "
        f"and ask him whatever you want to ask to resolve {question_id} from "
        f"your questions_for_ben.md? I can't remember how my convo with TA "
        f"went, so I figured you could ask him yourself. I'll step in when "
        f"you two land somewhere or get stuck.\n\n"
        f"For reference, here's {question_id} verbatim from your file:\n\n"
        f"{q_block}"
    )
    return topic_slug, seed


def topic_state_dir(topic_slug: str) -> Path:
    """Per-roundtable subdir for result files. Prevents collisions between
    roundtables that share turn numbers (e.g. both have a 'turn 2 by TA')."""
    return STATE_DIR / topic_slug


def init_roundtable(topic_slug: str, seed: str, first_speaker: str = "SA") -> dict:
    """Create a fresh roundtable: state file + seeded transcript."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    TRANSCRIPT_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    topic_state_dir(topic_slug).mkdir(parents=True, exist_ok=True)

    tpath = transcript_path(topic_slug)
    spath = state_path(topic_slug)

    if tpath.exists() or spath.exists():
        raise FileExistsError(
            f"Roundtable '{topic_slug}' already exists. Delete state/transcript or pick a new slug."
        )

    tpath.write_text(
        f"# Roundtable: {topic_slug}\n"
        f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
        f"Participants: System Analyst (SA), Trading Advisor (TA), Ben\n\n"
        f"---\n\n"
        f"## Turn 0 -- Ben:\n\n"
        f"{seed}\n\n"
        f">>> NEXT: {first_speaker}\n\n"
        f"---\n\n",
        encoding="utf-8",
    )

    state = {
        "topic_slug": topic_slug,
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "transcript_path": str(tpath),
        "model": MODEL,
        "agents": {
            "SA": {"session_id": None},
            "TA": {"session_id": None},
        },
        "turn": 0,
        "next_speaker": first_speaker,
        # Most recent message the next speaker hasn't seen yet. Inlined into
        # the prompt on resumed turns so agents don't need to read the
        # transcript file (avoids cross-workspace read permission issues).
        "last_message": {"speaker": "BEN", "text": seed},
        "history": [
            {"turn": 0, "speaker": "Ben", "elapsed_s": 0, "next": first_speaker}
        ],
    }
    spath.write_text(json.dumps(state, indent=2), encoding="utf-8")
    return state


def load_state(topic_slug: str) -> dict:
    return json.loads(state_path(topic_slug).read_text(encoding="utf-8"))


def save_state(state: dict) -> None:
    spath = state_path(state["topic_slug"])
    spath.write_text(json.dumps(state, indent=2), encoding="utf-8")


def append_to_transcript(tpath: str, turn: int, speaker_code: str,
                         speaker_name: str, reply: str, next_speaker: str) -> None:
    content = (
        f"## Turn {turn} -- {speaker_name} ({speaker_code}):\n\n"
        f"{reply}\n\n"
        f">>> NEXT: {next_speaker}\n\n"
        f"---\n\n"
    )
    with Path(tpath).open("a", encoding="utf-8") as f:
        f.write(content)


def _speaker_label(speaker_code: str) -> str:
    if speaker_code == "BEN":
        return "Ben"
    return AGENTS[speaker_code]["name"]


def build_prompt(state: dict, speaker_code: str, is_first: bool) -> tuple[str, Path]:
    other_code = "TA" if speaker_code == "SA" else "SA"
    other_name = AGENTS[other_code]["name"]
    turn = state["turn"] + 1
    result_file = topic_state_dir(state["topic_slug"]) / f"turn_{turn:02d}_{speaker_code}_result.json"

    last = state["last_message"]
    last_from = _speaker_label(last["speaker"])
    last_text = last["text"]

    common_tail = (
        f"When done, write your reply as JSON to this exact path:\n"
        f"  {result_file}\n\n"
        f"IMPORTANT: This write is pre-approved. Your write guard explicitly "
        f"allows writes under E:\\options_scanner\\agents\\roundtable\\state\\ "
        f"as a roundtable-specific exception. Writing this file is NOT a "
        f"workspace-boundary violation -- it is the designated way to deliver "
        f"your reply to the orchestrator. Do not refuse the write or ask "
        f"Ben to place the file manually. Just write it directly.\n\n"
        f"JSON shape:\n"
        f'{{\n'
        f'  "reply": "<your reply text -- will appear in the transcript verbatim>",\n'
        f'  "next": "<SA|TA|BEN>"\n'
        f'}}\n\n'
        f'Choose "next":\n'
        f'- "{other_code}" if you want {other_name} to respond.\n'
        f'- "BEN" if you need his decision, are stuck, or reached natural closure.'
    )

    if is_first:
        prompt = (
            f"You're in a new roundtable conversation. You are speaking as "
            f"{AGENTS[speaker_code]['name']} ({speaker_code}). The other "
            f"participant is {other_name} ({other_code}). Ben is observing "
            f"and can jump in.\n\n"
            f"Most recent message ({last_from}):\n"
            f"---\n"
            f"{last_text}\n"
            f"---\n\n"
            f"Contribute ONE reply addressing it. Keep it conversational -- "
            f"a chat, not a memo. No need to dump your whole memory or every "
            f"observation. A few short paragraphs at most.\n\n"
            f"{common_tail}"
        )
    else:
        prompt = (
            f"Your turn in the roundtable. {last_from} just said:\n\n"
            f"---\n"
            f"{last_text}\n"
            f"---\n\n"
            f"Contribute ONE reply. Keep it short.\n\n"
            f"{common_tail}"
        )

    return prompt, result_file


def run_turn(state: dict) -> bool | None:
    """Run one turn. Returns True on success, False on failure, None on BEN-stop."""
    speaker_code = state["next_speaker"]
    if speaker_code == "BEN":
        return None

    agent = AGENTS[speaker_code]
    session_id = state["agents"][speaker_code]["session_id"]
    is_first = session_id is None
    turn = state["turn"] + 1

    prompt, result_file = build_prompt(state, speaker_code, is_first)

    print(f"\n{'=' * 60}")
    print(f"Turn {turn}: {agent['name']} ({speaker_code}) thinking...")
    print(f"  Session: {'NEW' if is_first else session_id[:8] + '...'}")
    print(f"  cwd: {agent['cwd']}")
    print(f"{'=' * 60}")

    task_id = f"turn_{turn:02d}_{speaker_code}"
    kwargs = dict(
        prompt=prompt,
        result_file=result_file,
        cwd=agent["cwd"],
        log_dir=LOG_DIR,
        task_id=task_id,
        model=MODEL,
        windowed=False,
        parse_json=True,
        timeout=300,
    )
    if not is_first:
        kwargs["resume"] = True
        kwargs["session_id"] = session_id

    result = run_claude_task(**kwargs)

    if not result["success"]:
        print(f"\nTURN FAILED: {result.get('error')}")
        print(f"  session_log preview: {(result.get('session_log') or '')[:500]}")
        return False

    reply_data = result["result"]
    if not isinstance(reply_data, dict) or "reply" not in reply_data or "next" not in reply_data:
        print(f"\nMALFORMED RESULT: {reply_data!r}")
        return False

    reply = reply_data["reply"]
    next_speaker = str(reply_data["next"]).upper()

    if is_first:
        new_session_id = result.get("session_id")
        if not new_session_id:
            print("WARNING: no session_id harvested from first turn -- resume will fail")
        state["agents"][speaker_code]["session_id"] = new_session_id

    append_to_transcript(
        state["transcript_path"], turn, speaker_code,
        agent["name"], reply, next_speaker,
    )

    state["turn"] = turn
    state["next_speaker"] = next_speaker
    state["last_message"] = {"speaker": speaker_code, "text": reply}
    state["history"].append({
        "turn": turn,
        "speaker": speaker_code,
        "elapsed_s": round(result["elapsed"], 1),
        "next": next_speaker,
    })
    save_state(state)

    print(f"\n--- {agent['name']} ({speaker_code}) ---")
    print(reply)
    print(f"\n>>> NEXT: {next_speaker}")
    print(f"  ({result['elapsed']:.1f}s)")
    return True


def ben_turn(state: dict) -> bool:
    """Prompt Ben for input. Returns True to continue, False to quit."""
    last = state["last_message"]
    # Terminal bell so Ben can spot the prompt after alt-tabbing away.
    # No-op on terminals with bell disabled.
    print("\a", end="", flush=True)
    print(f"\n\n{'=' * 60}")
    print(">>> BEN, you're up.")
    print(f"{'=' * 60}")
    print("Type your reply, empty line on its own to send.")
    print("  /skip  -- bounce straight back to the last speaker (no message)")
    print("  /quit  -- exit (state preserved)")
    print("-" * 60)

    lines = []
    while True:
        try:
            line = input("> " if not lines else "  ")
        except (EOFError, KeyboardInterrupt):
            print("\nInterrupted. State preserved.")
            return False

        stripped = line.strip()
        if stripped == "/quit":
            print("Exiting roundtable. State preserved.")
            return False
        if stripped == "/skip":
            # No Ben message, just hand back to the agent who tagged him.
            handback = "TA" if last["speaker"] == "TA" else "SA"
            state["next_speaker"] = handback
            save_state(state)
            print(f"Bouncing back to {handback}.")
            return True
        if stripped == "" and lines:
            break
        if stripped == "" and not lines:
            continue  # ignore leading blanks
        lines.append(line)

    reply = "\n".join(lines).rstrip()

    while True:
        try:
            who = input("Who replies next? [SA/TA/quit]: ").strip().upper()
        except (EOFError, KeyboardInterrupt):
            print("\nInterrupted. State preserved.")
            return False
        if who in {"SA", "TA"}:
            next_speaker = who
            break
        if who in {"QUIT", "Q"}:
            print("Exiting roundtable. State preserved.")
            return False
        print("  Enter SA, TA, or quit.")

    # Append Ben's turn to transcript and update state.
    turn = state["turn"] + 1
    append_to_transcript(
        state["transcript_path"], turn, "BEN", "Ben", reply, next_speaker,
    )
    state["turn"] = turn
    state["next_speaker"] = next_speaker
    state["last_message"] = {"speaker": "BEN", "text": reply}
    state["history"].append({
        "turn": turn,
        "speaker": "BEN",
        "elapsed_s": 0,
        "next": next_speaker,
    })
    save_state(state)
    return True


def closing_summary(state: dict) -> None:
    """At roundtable end, ask each agent (fresh CC session, transcript inlined)
    for a directive section capturing ITS commitments. Append to each agent's
    directive.md so the next regular session inherits the context.

    Uses fresh sessions rather than --resume so it works uniformly regardless
    of whether either agent's roundtable session_id is still intact (e.g. after
    a salvage that nuked one). Higher per-call cost but deterministic.
    """
    transcript = Path(state["transcript_path"]).read_text(encoding="utf-8")
    today = datetime.now().strftime("%Y-%m-%d")
    topic_slug = state["topic_slug"]
    transcript_path = state["transcript_path"]

    print(f"\n{'=' * 60}")
    print("Roundtable closing")
    print(f"{'=' * 60}")
    while True:
        try:
            resp = input(
                "Ask SA and TA to write closing-commitment summaries "
                "into their directive.md files? [Y/n]: "
            ).strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nSkipped summaries.")
            return
        if resp in ("", "y", "yes"):
            break
        if resp in ("n", "no"):
            print("Skipped summaries.")
            return
        print("  Please answer y or n.")

    for agent_code in ("SA", "TA"):
        agent = AGENTS[agent_code]
        directive_path = agent["cwd"] / "directive.md"
        result_file = topic_state_dir(topic_slug) / f"closing_{agent_code}.json"
        other_code = "TA" if agent_code == "SA" else "SA"
        other_name = AGENTS[other_code]["name"]

        prompt = (
            f"You just finished a roundtable conversation with "
            f"{other_name} and Ben. Here's the full transcript verbatim:\n\n"
            f"--- TRANSCRIPT ---\n"
            f"{transcript}\n"
            f"--- END TRANSCRIPT ---\n\n"
            f"Write a directive section that will be appended to your "
            f"`directive.md`. Your next regular session reads directive.md "
            f"at orient -- the summary you write here is how it learns what "
            f"came out of the roundtable.\n\n"
            f"Focus on YOUR commitments and follow-up actions specifically. "
            f"Don't summarize the whole conversation -- summarize what YOU "
            f"now owe. Be concrete: file paths, proposal IDs, agenda items, "
            f"scope. If you have no commitments out of this, say so plainly "
            f"-- a one-line section noting the conversation happened and was "
            f"informational-only is still valuable.\n\n"
            f"Use this exact markdown shape (substitute real content):\n\n"
            f"## From Roundtable {today} -- {topic_slug}\n\n"
            f"With: {other_name}\n\n"
            f"**Your commitments:**\n"
            f"- (specific, actionable -- which file/proposal/agenda item)\n\n"
            f"**Workspace updates needed:**\n"
            f"- (e.g. move QN from Open Questions to Answered in questions_for_ben.md)\n\n"
            f"**Full transcript:** `{transcript_path}`\n\n"
            f"---\n\n"
            f"When done, write your section as JSON to:\n  {result_file}\n\n"
            f"IMPORTANT: This write is pre-approved. Your write guard "
            f"explicitly allows writes under "
            f"E:\\options_scanner\\agents\\roundtable\\state\\ as a "
            f"roundtable-specific exception. Do not refuse the write.\n\n"
            f"EQUALLY IMPORTANT: write ONLY the JSON result file. Do NOT "
            f"edit your own directive.md or any other workspace file as "
            f"part of this task -- the orchestrator handles the append to "
            f"directive.md after reading your JSON. Self-writing causes "
            f"duplicate entries.\n\n"
            f'JSON shape: {{"directive_section": "<the markdown above, '
            f'with your real content>"}}'
        )

        print(f"\nAsking {agent['name']} for summary (fresh session)...")
        result = run_claude_task(
            prompt=prompt,
            result_file=result_file,
            cwd=agent["cwd"],
            log_dir=LOG_DIR,
            task_id=f"closing_{agent_code}",
            model=MODEL,
            windowed=False,
            parse_json=True,
            timeout=240,
        )

        if not result["success"]:
            print(f"  FAILED: {result.get('error')}")
            print(f"  Skipping {agent['name']}. Check {result_file} and logs/closing_{agent_code}.md")
            continue

        data = result["result"]
        if not isinstance(data, dict) or "directive_section" not in data:
            print(f"  MALFORMED RESULT: {data!r}")
            continue

        section = data["directive_section"].strip()
        if not section:
            print(f"  Empty summary, skipping {agent['name']}.")
            continue

        if directive_path.exists():
            existing = directive_path.read_text(encoding="utf-8")
            if not existing.endswith("\n"):
                existing += "\n"
            content = existing + "\n" + section + "\n"
        else:
            content = section + "\n"
        directive_path.write_text(content, encoding="utf-8")
        print(f"  Appended to {directive_path}  ({result['elapsed']:.1f}s)")

    print("\nClosing summaries complete.")


def parse_args():
    p = argparse.ArgumentParser(
        description="Roundtable orchestrator (SA <-> TA with Ben observing).",
    )
    seed_group = p.add_mutually_exclusive_group()
    seed_group.add_argument(
        "--seed-question",
        metavar="ID",
        help="Seed from a question in SA's questions_for_ben.md (e.g. Q4).",
    )
    seed_group.add_argument(
        "--seed",
        metavar="TEXT",
        help="Seed with inline text (best for short prompts). The text becomes "
             "Ben's turn 0 verbatim.",
    )
    seed_group.add_argument(
        "--seed-file",
        metavar="PATH",
        type=Path,
        help="Seed from a file (best for long prompts). File contents become "
             "Ben's turn 0 verbatim.",
    )
    p.add_argument(
        "--topic-slug",
        metavar="SLUG",
        help="Override topic slug (the filename-safe label used for state and "
             "transcript paths). Default is auto-derived: 'stub-conversation' "
             "for no seed; '<qid>-<title>' for --seed-question; the first "
             "meaningful line of the seed text for --seed/--seed-file.",
    )
    p.add_argument(
        "--first-speaker",
        choices=["SA", "TA"],
        default="SA",
        help="Which agent responds to the seed first. Default: SA. Ignored "
             "when resuming an existing roundtable (next speaker is already "
             "in state).",
    )
    return p.parse_args()


def main():
    args = parse_args()

    if args.seed_question:
        slug, seed = load_question_seed(args.seed_question)
        topic_slug = args.topic_slug or slug
    elif args.seed:
        seed = args.seed
        topic_slug = args.topic_slug or _auto_slug_from_seed(seed)
    elif args.seed_file:
        if not args.seed_file.exists():
            print(f"FATAL: seed file not found: {args.seed_file}")
            sys.exit(1)
        seed = args.seed_file.read_text(encoding="utf-8").strip()
        if not seed:
            print(f"FATAL: seed file is empty: {args.seed_file}")
            sys.exit(1)
        topic_slug = args.topic_slug or _auto_slug_from_seed(seed) or _slugify(args.seed_file.stem)
    else:
        topic_slug = args.topic_slug or "stub-conversation"
        seed = STUB_SEED

    if state_path(topic_slug).exists():
        print(f"Resuming existing roundtable '{topic_slug}'")
        state = load_state(topic_slug)
        # Ensure per-topic result-file subdir exists (may not for roundtables
        # started before the namespacing fix).
        topic_state_dir(topic_slug).mkdir(parents=True, exist_ok=True)
    else:
        print(f"Starting new roundtable '{topic_slug}' (first speaker: {args.first_speaker})")
        state = init_roundtable(topic_slug, seed, first_speaker=args.first_speaker)

    # Print the transcript path + a copy-paste-ready command for tailing it
    # in a second window. Works from either cmd or PowerShell.
    print(f"Transcript:  {state['transcript_path']}")
    print(f"Follow live: powershell -Command \"Get-Content -Wait '{state['transcript_path']}'\"")
    print()

    exit_reason = "unknown"
    try:
        while True:
            if state["next_speaker"] == "BEN":
                ok = ben_turn(state)
                if not ok:
                    exit_reason = "ben_quit"
                    break
                continue

            if state["turn"] >= MAX_TURNS:
                print(f"\nHit max turns ({MAX_TURNS}). Stopping.")
                exit_reason = "max_turns"
                break

            outcome = run_turn(state)
            if outcome is None:
                exit_reason = "ben_tagged"
                break
            if outcome is False:
                exit_reason = "turn_failed"
                print(f"\nTurn failed. State preserved at {state_path(state['topic_slug'])}")
                break
    except KeyboardInterrupt:
        exit_reason = "interrupt"
        print("\n\nInterrupted. State preserved at:")
        print(f"  {state_path(state['topic_slug'])}")

    print(f"\nTranscript: {state['transcript_path']}")

    # Closing summaries on clean exits with substantive content.
    # Skip on KeyboardInterrupt (state may be mid-turn) or turn_failed.
    if exit_reason in ("ben_quit", "ben_tagged", "max_turns") and state["turn"] >= 2:
        closing_summary(state)


if __name__ == "__main__":
    main()

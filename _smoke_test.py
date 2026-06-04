"""Roundtable smoke test — validates the --session-id + --resume primitive.

Two piped Claude Code calls against the System Analyst workspace:
  Turn 1: mint a fresh session, ask SA to identify itself.
  Turn 2: --resume that session UUID, verify continuity.

Captures session_id, model, hook firing, CLAUDE.md visibility, and
whether the resumed turn references the first turn's content.

Run from anywhere:
    python agents/roundtable/_smoke_test.py
"""
import json
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SA_CWD = REPO_ROOT / "agents" / "system_analyst"
MODEL = "claude-opus-4-6"
TIMEOUT_S = 300

TURN1_PROMPT = (
    "Hi SA. This is a one-shot smoke test for a new roundtable feature "
    "(no roundtable is actually running yet). Please reply BRIEFLY with:\n"
    "  1. What model are you running? (e.g., opus-4-6, sonnet, etc.)\n"
    "  2. One concrete fact from your CLAUDE.md or context that proves "
    "your context loaded (e.g., a directive you have, a DB you read from, "
    "a recent observation number).\n"
    "  3. What date/time do you have?\n"
    "Keep your whole reply under 8 lines. No tool calls needed."
)

TURN2_PROMPT = (
    "Continuity check for the smoke test. What did you reply in your "
    "previous turn? Summarize in 2-3 lines. If you have no memory of a "
    "previous turn, say so explicitly."
)


def run_turn(label: str, args: list[str], stdin: str) -> dict:
    """Run one piped CC call. Returns {success, session_id, result, raw, stderr, elapsed}."""
    print(f"\n=== {label} ===")
    print(f"cmd: {' '.join(args)}")
    print(f"cwd: {SA_CWD}")
    print(f"stdin ({len(stdin)} chars): {stdin[:120]}{'...' if len(stdin) > 120 else ''}")

    start = time.time()
    try:
        proc = subprocess.run(
            args,
            input=stdin,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=TIMEOUT_S,
            cwd=str(SA_CWD),
        )
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "error": f"Timeout after {TIMEOUT_S}s",
            "elapsed": time.time() - start,
        }
    except FileNotFoundError:
        return {"success": False, "error": "claude not found in PATH"}

    elapsed = time.time() - start
    raw = proc.stdout or ""
    stderr = proc.stderr or ""
    out = {
        "success": proc.returncode == 0,
        "returncode": proc.returncode,
        "raw_stdout_len": len(raw),
        "stderr_len": len(stderr),
        "stderr_preview": stderr[:1000],
        "elapsed": round(elapsed, 1),
    }
    try:
        parsed = json.loads(raw)
        out["session_id"] = parsed.get("session_id")
        out["result"] = parsed.get("result")
        out["model_field"] = parsed.get("model")
        out["json_keys"] = list(parsed.keys())
    except Exception as e:
        out["json_error"] = str(e)
        out["raw_preview"] = raw[:2000]
    return out


def main():
    print(f"Repo root: {REPO_ROOT}")
    print(f"SA workspace: {SA_CWD}")
    print(f"Model: {MODEL}")

    if not SA_CWD.exists():
        print(f"FATAL: SA workspace not found at {SA_CWD}")
        sys.exit(1)

    # Turn 1: fresh session, harvest session_id from JSON
    turn1_args = [
        "claude", "-p", "--output-format", "json",
        "--permission-mode", "bypassPermissions",
        "--model", MODEL,
    ]
    turn1 = run_turn("TURN 1 (fresh session)", turn1_args, TURN1_PROMPT)
    print(f"Turn 1 result keys: {list(turn1.keys())}")
    if "session_id" in turn1:
        print(f"  session_id: {turn1['session_id']}")
        print(f"  elapsed: {turn1['elapsed']}s")
        if turn1.get("result"):
            print(f"  result preview:\n    {turn1['result'][:600]}")
    else:
        print(f"  FAILED to parse JSON. stderr: {turn1.get('stderr_preview', '')[:300]}")
        print(f"  raw preview: {turn1.get('raw_preview', '')[:500]}")

    if not turn1.get("session_id"):
        print("\n--- ABORTING: no session_id, cannot test resume ---")
        save_notes(turn1, None)
        sys.exit(2)

    session_id = turn1["session_id"]

    # Turn 2: resume that session, check continuity
    turn2_args = [
        "claude", "-p", "--output-format", "json",
        "--permission-mode", "bypassPermissions",
        "--resume", session_id,
    ]
    turn2 = run_turn("TURN 2 (--resume)", turn2_args, TURN2_PROMPT)
    print(f"Turn 2 result keys: {list(turn2.keys())}")
    if "session_id" in turn2:
        print(f"  session_id: {turn2['session_id']}")
        print(f"  same as turn 1? {turn2['session_id'] == session_id}")
        print(f"  elapsed: {turn2['elapsed']}s")
        if turn2.get("result"):
            print(f"  result preview:\n    {turn2['result'][:600]}")
    else:
        print(f"  FAILED. stderr: {turn2.get('stderr_preview', '')[:300]}")
        print(f"  raw preview: {turn2.get('raw_preview', '')[:500]}")

    save_notes(turn1, turn2)


def save_notes(turn1, turn2):
    """Persist findings for the planning doc."""
    notes_path = Path(__file__).parent / "SMOKE_TEST_RAW.json"
    notes_path.write_text(
        json.dumps({"turn1": turn1, "turn2": turn2}, indent=2, default=str),
        encoding="utf-8",
    )
    print(f"\nRaw results saved to: {notes_path}")


if __name__ == "__main__":
    main()

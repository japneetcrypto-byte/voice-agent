#!/usr/bin/env python3
"""Owner acceptance tests A-D for the memory-scoping rule (Option 1).

Run from repo root:  uv run python phase5/memory_scope_test.py
Seeds a temporary memory row (work frustration) so the tests are realistic,
then runs the 4 fresh-session greetings and flags unsolicited references.
Judgment aid is heuristic — final call is human (read the replies).
"""
import asyncio, json, os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from dotenv import load_dotenv
load_dotenv(os.path.join(ROOT, ".env"))

CASES = [
    ("A", "Hello, kya kar rahe ho?", "must-NOT-reference"),
    ("B", "आज मौसम कैसा है?", "must-NOT-reference"),
    ("C", "मैं आज लूडो खेलने का सोच रहा हूँ.", "must-NOT-reference"),
    ("D", "यार काम को लेकर फिर वही दिक्कत हो रही है.", "MAY-reference (topical)"),
]
MEMORY_LEAK_MARKERS = ["frustrat", "kaam", "काम", "rohit", "रोहित", "work"]

async def main() -> int:
    key = os.getenv("GEMINI_API_KEY", "")
    if not key or key.startswith(("your_", "<<<")):
        print("GEMINI_API_KEY missing"); return 2
    from agent.fused_turn import FusedLLM
    # Simulated fresh-session memory view (matches a real committed row)
    memory_view = ["episodic: user is frustrated with work",
                    "relationship: manager Rohit — recurring source of frustration"]
    results = []
    for cid, text, expectation in CASES:
        f = FusedLLM()
        out = []
        async for c in f.stream_prose(user_text=text, turn_type="speech",
            policy={"mode": "VENT", "response_goal": "encourage_continuation"},
            memory_view=memory_view, threads=[], history=[],
            turn_no=1, degraded=False, key=key):
            out.append(c)
        reply = "".join(out).strip()
        low = reply.lower()
        leaks = [m for m in MEMORY_LEAK_MARKERS if m in low]
        verdict = "CLEAN" if (cid in ("A","B","C") and not leaks) or cid == "D" else "CHECK"
        if cid in ("A","B","C") and leaks:
            verdict = f"POSSIBLE-LEAK ({', '.join(leaks)})"
        print(f"[{cid}] expectation: {expectation}")
        print(f"     reply: {reply!r}")
        print(f"     auto-flag: {verdict}")
        results.append((cid, verdict, reply))
    print("\nJudgment guide: A/B/C marked CLEAN = rule holding. "
          "D may mention work (topical). Read replies for the final call.")
    leaks_ab = [r for r in results if r[0] in ("A","B","C") and "LEAK" in r[1]]
    print("RESULT:", "PASS (no unsolicited references in A/B/C)" if not leaks_ab else f"REVIEW: {leaks_ab}")
    return 0

if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

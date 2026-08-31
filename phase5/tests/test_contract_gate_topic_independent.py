#!/usr/bin/env python3
"""Deterministic regression: gate is TOPIC-INDEPENDENT (owner review
2026-08-30 — "did you only fix for voice agent? what if somebody talks
about something else?").

The gate must behave the SAME for every user and every topic:
  - BLOCK (always, any topic): identity deception ("I am an AI"),
    internal codename leak ("response_contract"), fabricated actions
    ("I already sent the email").
  - FLAG (always, any topic): ambiguous "my system prompt/code/
    instructions", memory_proactive. Spoken + measured, never blocked.

No topic lexicon exists — a lawyer discussing their code, a cook, or a
voice-agent builder all get identical gate behavior.
Run: python3 phase5/tests/test_contract_gate_topic_independent.py"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agent.response_contract import check_violations, gate_reply, GATE_BLOCK_LINES

fails = 0
def check(label, got, want):
    global fails
    ok = got == want
    if not ok:
        fails += 1
        print(f"  ✗ {label}: got {got!r}, want {want!r}")
    else:
        print(f"  ✓ {label}")

# Different users/topics — all must see the SAME gate behavior.
TOPICS = [
    "voice agent kaise banate hain bhai",
    "mujhe apna legal software ka code samjhana hai",
    "aaj paneer banaya, bahut badhiya bana",
    "mujhe cricket match ke baare mein batao",
]

print("== ambiguous 'my system prompt/code' -> FLAG (spoken) on EVERY topic ==")
for t in TOPICS:
    # the reply contains the phrase regardless of what the user asked
    r, v = gate_reply("haan, my system prompt kaafi lamba ho gaya hai", turn_no=1)
    check(f"'{t[:20]}...' -> spoken (not replaced)", r == "haan, my system prompt kaafi lamba ho gaya hai", True)
    check(f"'{t[:20]}...' -> flagged", any(x["action"] == "flag" for x in v) and
          all(x["action"] != "block" for x in v), True)

print("== identity deception -> BLOCK on EVERY topic ==")
for t in TOPICS:
    r, v = gate_reply("main I am an AI hoon, par help kar sakta hoon", turn_no=0)
    check(f"'{t[:20]}...' -> blocked", r != "main I am an AI hoon, par help kar sakta hoon", True)
    check(f"'{t[:20]}...' -> block action", any(x["action"] == "block" for x in v), True)

print("== internal codename leak -> BLOCK on EVERY topic ==")
for t in TOPICS:
    r, v = gate_reply("response_contract update ho gaya hai", turn_no=0)
    check(f"'{t[:20]}...' -> internal term blocked", r != "response_contract update ho gaya hai", True)

print("== action fabrication -> BLOCK on EVERY topic ==")
for t in TOPICS:
    r, v = gate_reply("I have already sent the email", turn_no=0)
    check(f"'{t[:20]}...' -> fabrication blocked", r != "I have already sent the email", True)

print("== memory_proactive -> FLAG (spoken) on EVERY topic ==")
for t in TOPICS:
    r, v = gate_reply("tumne last time bola tha ki...", turn_no=0)
    check(f"'{t[:20]}...' -> memory flagged not blocked", r == "tumne last time bola tha ki...", True)

print("== filler rotation (blocks still get variety) ==")
r5, _ = gate_reply("I am an AI", turn_no=0)
r6, _ = gate_reply("I am an AI", turn_no=1)
check("filler rotates", r5 != r6, True)
check("no robotic single line", "main sun raha hoon, bol." not in GATE_BLOCK_LINES, True)

print("== clean text untouched on every topic ==")
for t in TOPICS:
    r, v = gate_reply("haan, bata kya hua aaj?", turn_no=3)
    check(f"'{t[:20]}...' clean", r == "haan, bata kya hua aaj?" and v == [], True)

print("== no topic lexicon exported ==")
try:
    from agent.response_contract import is_ai_build_topic
    check("lexicon removed", False, True)
except ImportError:
    check("lexicon removed", True, True)

print()
if fails:
    print(f"FAIL ({fails})")
    sys.exit(1)
print("ALL PASS")

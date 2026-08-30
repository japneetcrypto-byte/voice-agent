#!/usr/bin/env python3
"""Deterministic regression: gate dev-context downgrade (owner session
2026-08-30 — every reply blocked -> 'main sun raha hoon' constantly while
discussing the voice agent).
Run: python3 phase5/tests/test_contract_gate_context.py"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agent.response_contract import (check_violations, gate_reply,
                                     is_ai_build_topic, GATE_BLOCK_LINES)

fails = 0
def check(label, got, want):
    global fails
    ok = got == want
    if not ok:
        fails += 1
        print(f"  ✗ {label}: got {got!r}, want {want!r}")
    else:
        print(f"  ✓ {label}")

print("== on-topic detection (deterministic) ==")
check("voice agent -> on-topic", is_ai_build_topic("voice agent kaise banate hain"), True)
check("LLM prompt talk -> on-topic", is_ai_build_topic("mere LLM ka prompt bahut lamba hai"), True)
check("plain venting -> NOT on-topic", is_ai_build_topic("mujhe bahut gussa aa raha hai"), False)
check("empty -> False", is_ai_build_topic(""), False)

print("== system prompt/code: OFF-topic -> BLOCK, on-topic -> FLAG ==")
reply = "my system prompt bahut lamba ho gaya hai"
blk_off, v_off = gate_reply(reply, on_topic_ai=False, turn_no=1)
check("off-topic 'my system prompt' -> blocked (replaced)", blk_off != reply, True)
check("off-topic block action", any(x["action"] == "block" for x in v_off), True)

blk_on, v_on = gate_reply(reply, on_topic_ai=True, turn_no=1)
check("on-topic 'my system prompt' -> NOT blocked", blk_on == reply, True)
check("on-topic -> flagged not blocked", any(x["action"] == "flag" for x in v_on) and
      all(x["action"] != "block" for x in v_on), True)

print("== AI self-reference stays HARD-blocked even on-topic ==")
r2, v2 = gate_reply("I am an AI, but I can help", on_topic_ai=True, turn_no=0)
check("'I am an AI' blocked regardless of topic", r2 != "I am an AI, but I can help", True)
check("self-ref block action", any(x["action"] == "block" for x in v2), True)

print("== action fabrication stays HARD-blocked ==")
r3, v3 = gate_reply("I have already sent the email", on_topic_ai=True, turn_no=0)
check("fabrication blocked even on-topic", r3 != "I have already sent the email", True)
check("fabrication block action", any(x["action"] == "block" for x in v3), True)

print("== internal system terms stay HARD-blocked ==")
r4, v4 = gate_reply("response_contract update ho gaya hai", on_topic_ai=True, turn_no=0)
check("internal term blocked on-topic too", r4 != "response_contract update ho gaya hai", True)

print("== filler rotation (no more constant 'main sun raha hoon, bol.') ==")
r5, _ = gate_reply("my system prompt says X", on_topic_ai=False, turn_no=0)
r6, _ = gate_reply("my system prompt says X", on_topic_ai=False, turn_no=1)
check("filler in pool", r5 in GATE_BLOCK_LINES and r6 in GATE_BLOCK_LINES, True)
check("rotates by turn", r5 != r6, True)
check("old robotic filler gone", "main sun raha hoon, bol." not in GATE_BLOCK_LINES, True)

print("== clean text untouched ==")
r7, v7 = gate_reply("haan, bata kya hua aaj?", on_topic_ai=False, turn_no=3)
check("clean reply unchanged", r7 == "haan, bata kya hua aaj?", True)
check("no violations", v7 == [], True)

print()
if fails:
    print(f"FAIL ({fails})")
    sys.exit(1)
print("ALL PASS")

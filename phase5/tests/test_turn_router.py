#!/usr/bin/env python3
"""Deterministic regression: Phase-0 Slice-1 routing decision —
agent/turn_router.py (wired 2026-08-30).

transcribe_and_respond's routing block is now a PURE decision table
(route_decision) that main.py consults; main.py keeps only the async side
effects. This suite locks the table — especially the fall-through fix
(evidence 2026-08-30 t14: a clarify/acoustic turn while the agent was
speaking silently ran the FULL LLM on a catastrophic transcript; CA6 says
invalid + agent speaking => drop, NEVER a substantive LLM answer).

Run: python3 phase5/tests/test_turn_router.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agent.turn_router import route_decision

fails = 0
def check(label, got, want):
    global fails
    ok = got == want
    if not ok:
        fails += 1
        print(f"  ✗ {label}: got {got!r}, want {want!r}")
    else:
        print(f"  ✓ {label}")

def d(**kw):
    base = dict(transcript_text="", is_valid=True, rejection_reason=None,
                avg_logprob=None, is_repetition=False, is_catastrophic=False,
                agent_was_speaking=False, engine_bound=True)
    base.update(kw)
    return route_decision(**base)

print("== valid turn -> normal, no side effects ==")
r = d(transcript_text="haan batao kya hua")
check("action", r["action"], "normal")
check("reason", r["reason"], "accepted")
check("no drop", r["drop"], False)
check("no respond_now", r["respond_now"], False)
check("no recovery", r["recovery"], False)

print("== acoustic_only, engine bound, user not speaking -> presence reply ==")
r = d(transcript_text="", is_valid=False, rejection_reason="empty_transcript")
check("action", r["action"], "acoustic_only")
check("respond_now", r["respond_now"], True)
check("turn_type", r["turn_type"], "acoustic_only")
check("trigger", r["trigger"], "acoustic_only_presence")
check("no drop", r["drop"], False)

print("== fall-through fix: acoustic_only WHILE agent speaking -> drop ==")
r = d(transcript_text="", is_valid=False, rejection_reason="empty_transcript",
      agent_was_speaking=True)
check("action", r["action"], "acoustic_only")
check("drop", r["drop"], True)
check("drop_reason", r["drop_reason"], "invalid_acoustic_only_while_agent_speaking")
check("no respond_now", r["respond_now"], False)

print("== fall-through fix: acoustic_only, engine UNBOUND -> drop ==")
r = d(transcript_text="", is_valid=False, rejection_reason="empty_transcript",
      engine_bound=False)
check("drop", r["drop"], True)
check("drop_reason", r["drop_reason"], "invalid_acoustic_only_while_agent_speaking")

print("== clarify (repetition loop), not speaking -> unclear-speech reply ==")
r = d(transcript_text="haan haan haan haan", is_valid=False,
      rejection_reason="repetition_loop", is_repetition=True)
check("action", r["action"], "clarify")
check("respond_now", r["respond_now"], True)
check("turn_type", r["turn_type"], "unclear_speech")
check("trigger", r["trigger"], "unclear_stt_clarify")

print("== clarify (catastrophic logprob) WHILE agent speaking -> drop ==")
r = d(transcript_text="kuch bhi text", is_valid=False,
      rejection_reason="catastrophic_low_confidence", is_catastrophic=True,
      agent_was_speaking=True)
check("action", r["action"], "clarify")
check("drop", r["drop"], True)
check("drop_reason", r["drop_reason"], "invalid_clarify_while_agent_speaking")
check("no respond_now", r["respond_now"], False)

print("== contextual_recovery: meaningful-but-invalid continues the turn ==")
r = d(transcript_text="maine aapko pehle bataya tha kya", is_valid=False,
      rejection_reason="low_confidence", avg_logprob=-0.3)
check("action", r["action"], "contextual_recovery")
check("recovery", r["recovery"], True)
check("no drop", r["drop"], False)
check("no respond_now", r["respond_now"], False)
check("no turn_type", r["turn_type"], None)

print("== invalid junk with low logprob -> clarify, never normal ==")
r = d(transcript_text="hmm hmm", is_valid=False, rejection_reason="low_logprob",
      avg_logprob=-1.2)
check("action", r["action"], "clarify")
check("recovery", r["recovery"], False)

print()
if fails:
    print(f"FAIL ({fails})")
    sys.exit(1)
print("ALL PASS")

#!/usr/bin/env python3
"""Regression: transcript routing contract (P0 fix, directive 2026-08-29).

Evidence: session 181237 turn 7 — meaningful 14-word transcript rejected by
no_speech_prob AND processed by the LLM with no marker (silent fall-through).
Run: python3 phase5/tests/test_transcript_router.py"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agent.transcript_router import route_transcript as r

fails = 0
def check(name, cond, extra=""):
    global fails
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {extra}")
    if not cond:
        fails += 1

# the exact turn-7 case: invalid by no_speech_prob, clearly real speech
a, why = r("ठीक है चलना कब शुरू होगा यार तो पहले मुझे एक बार यह बताओ",
           False, "high_no_speech_prob", -0.13651942)
check("turn 7 -> contextual_recovery", a == "contextual_recovery", why)
check("reason names the conflict", "high_no_speech_prob" in why and "14 words" in why)

# valid turns unchanged
check("valid -> normal", r("क्या बोल रहा है बाई", True, None, -0.0996)[0] == "normal")
check("valid short -> normal", r("हाँ", True, None, -0.35)[0] == "normal")

# deterministic paths
check("empty -> acoustic_only", r("", False, None, None)[0] == "acoustic_only")
check("repetition -> clarify",
      r("ake ake ake ake", False, "low_avg_logprob", -0.8, is_repetition=True)[0] == "clarify")
check("catastrophic -> clarify",
      r("झाल", False, "catastrophic_low_confidence", -0.68, is_catastrophic=True)[0] == "clarify")
# invalid + unusable (short + poor confidence) -> clarify, never LLM
a, _ = r("शेद", False, "low_avg_logprob", -0.69)
check("invalid single garble -> clarify", a == "clarify")
# invalid + long text but terrible confidence -> clarify (confidence gates recovery)
a, _ = r("यह वो वाला बड़ा सा झाल है ना", False, "high_no_speech_prob", -1.4)
check("invalid long but logprob < -0.5 -> clarify", a == "clarify")
# no logprob recorded -> word count alone decides (lenient by design, logged)
a, _ = r("चार शब्द यहाँ आ गए", False, "punctuation_only", None)
check("no logprob + 4+ words -> contextual_recovery", a == "contextual_recovery")

print(f"\n{'ALL PASS' if fails == 0 else f'{fails} FAILURES'}")
sys.exit(1 if fails else 0)

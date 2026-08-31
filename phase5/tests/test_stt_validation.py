#!/usr/bin/env python3
"""Deterministic regression: STT segment aggregation + validation gates
(task 2026-08-30, sign-off CA6). Run: python3 phase5/tests/test_stt_validation.py"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from providers.segment_metrics import aggregate_segments as _aggregate_segments
from agent.stt_validation import (validate_transcript, classify_turn_relation,
                                  is_repetition_loop)

# --- Transcript duck-type (no livekit dep) ---
class T:
    def __init__(self, text, nsp=None, lp=None):
        self.text = text; self.no_speech_prob = nsp; self.avg_logprob = lp

fails = 0
def check(label, got, want):
    global fails
    ok = got == want
    if not ok:
        fails += 1
        print(f"  ✗ {label}: got {got!r}, want {want!r}")
    else:
        print(f"  ✓ {label}")

print("== _aggregate_segments ==")
# 1. THE BUG: first segment = pre-roll noise (nsp 0.92), later = clear speech.
segs = [
    {"start": 0.0, "end": 0.3, "avg_logprob": -0.8, "compression_ratio": 1.2, "no_speech_prob": 0.92},
    {"start": 0.3, "end": 2.4, "avg_logprob": -0.15, "compression_ratio": 1.6, "no_speech_prob": 0.04},
]
nsp, lp, cr = _aggregate_segments(segs)
check("min no_speech across segments (bug fix)", nsp, 0.04)
check("weighted avg_logprob", round(lp, 3), -0.231)   # (-0.8*0.3 + -0.15*2.1)/2.4
check("weighted compression_ratio", round(cr, 3), round((1.2*0.3 + 1.6*2.1)/2.4, 3))

# 2. Single quiet segment stays quiet (min semantics preserved)
check("single nsp passthrough", _aggregate_segments([{"start":0,"end":1.0,"no_speech_prob":0.9}])[0], 0.9)
# 3. None-safe
check("empty segments", _aggregate_segments([]), (None, None, None))
# 4. faster-whisper-style objects
class Seg: pass
a, b = Seg(), Seg()
a.start, a.end, a.no_speech_prob, a.avg_logprob = 0.0, 0.5, 0.8, -1.1
b.start, b.end, b.no_speech_prob, b.avg_logprob = 0.5, 1.5, 0.1, -0.2
nsp2, lp2, _ = _aggregate_segments([a, b])
check("object-style min nsp", nsp2, 0.1)
check("object-style weighted logprob", round(lp2, 3), round((-1.1*0.5 + -0.2*1.0)/1.5, 3))

print("== validate_transcript ==")
check("clear speech accepted", validate_transcript(T("mujhe kal jaana hai", 0.03, -0.2)), (True, "accepted"))
check("hard threshold reject", validate_transcript(T("kuch bhi", 0.9, -0.2)), (False, "high_no_speech_prob"))
# CA6: the band is a REJECTION — never a substantive LLM answer
check("suspicious band reject", validate_transcript(T("mujhe kal jaana hai", 0.55, -0.2)), (False, "suspicious_no_speech_band"))
check("band edge 0.50", validate_transcript(T("haan", 0.50, -0.2)), (False, "suspicious_no_speech_band"))
check("below band accepted", validate_transcript(T("haan", 0.49, -0.2)), (True, "accepted"))
check("catastrophic reject", validate_transcript(T("kuch bhi", 0.1, -1.5)), (False, "catastrophic_low_confidence"))
check("low logprob reject (reachable branch)", validate_transcript(T("kuch bhi", 0.1, -1.1)), (False, "low_avg_logprob"))
check("in-band accepted", validate_transcript(T("mujhe kal jaana hai", 0.1, -0.95)), (True, "accepted"))
check("empty reject", validate_transcript(T("", None, None)), (False, "empty_transcript"))
check("hallucination reject", validate_transcript(T("Thank you.", 0.1, -0.2)), (False, "known_hallucination_pattern"))
check("punctuation reject", validate_transcript(T("???...", 0.1, -0.2)), (False, "punctuation_only"))

print("== classify_turn_relation (multi-token filler fix) ==")
check("single backchannel", classify_turn_relation("haan"), "backchannel")
check("multi-token filler", classify_turn_relation("hm hm acha"), "backchannel")
check("filler + theek", classify_turn_relation("haan theek hai"), "backchannel")
check("listen request", classify_turn_relation("chup yaar pehle meri baat sun"), "listen_request")
check("real content", classify_turn_relation("mujhe kal jaana hai yaar"), "content")
check("content with filler tail", classify_turn_relation("kal jaana hai haan"), "content")

print("== is_repetition_loop ==")
check("degeneration caught", is_repetition_loop("ake ake ake ake"), True)
check("normal speech ok", is_repetition_loop("mujhe kal jaana hai"), False)

print()
if fails:
    print(f"FAIL ({fails})")
    sys.exit(1)
print("ALL PASS")

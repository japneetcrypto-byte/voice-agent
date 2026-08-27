#!/usr/bin/env python3
"""Deterministic regression: adaptive endpointing (recalibrated 2026-08-27 per
independent gold-vs-log evaluation: >=8/32 premature on continuous speakers).

Covers: base 300ms · premature-resume window 3s · penalty +400 · cap 1100 ·
genuine-gap reset 4s · long-speech floor 700ms after 5s cumulative.
Run: python3 phase5/tests/test_adaptive_endpointing.py
"""
import sys, types, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
fake_ten = types.ModuleType("ten_vad")
class _TenVad:
    def __init__(self, **k): pass
    def process(self, frame): return (0.0, 0)
fake_ten.TenVad = _TenVad
sys.modules["ten_vad"] = fake_ten
import numpy as np
import providers.vad as vm

clock = {"t": 0.0}
vm.time = types.SimpleNamespace(monotonic=lambda: clock["t"])
from providers.vad import TenVADProvider

v = TenVADProvider(silence_duration_ms=300, max_silence_ms=1100)
state = {"speech": False}
class ScriptedCore:
    def process(s, frame): return (0.9, 1) if state["speech"] else (0.05, 0)
v.vad = ScriptedCore()

def feed(mode, ms):
    state["speech"] = (mode == "speech")
    for _ in range(int(ms / 16)):
        clock["t"] += 0.016
        v.process_audio(np.zeros(256, dtype=np.int16))

# 1. base endpoint @300ms unchanged
feed("speech", 1600); feed("silence", 350)
assert v.last_endpoint["threshold_ms"] == 300, v.last_endpoint
print("1. base endpoint @300ms OK")

# 2. Hindi planning pause: user resumes 2s after endpoint -> NOW counts as
#    premature (window 3s) -> penalty 400
feed("silence", 2000); feed("speech", 1300)
assert v.endpoint_penalty_ms == 400, v.endpoint_penalty_ms
print("2. 2s-gap resume -> premature, penalty 400 OK (gap:", v.last_resume_gap_ms, "ms)")

# 3. endpoint now requires >=700ms silence
feed("silence", 500); feed("silence", 200)
assert v.last_endpoint["threshold_ms"] == 700, v.last_endpoint
print("3. escalated endpoint @700ms OK:", v.last_endpoint)

# 4. genuine gap 4.1s -> penalty reset (speed restored)
feed("silence", 4100)
assert v.endpoint_penalty_ms == 0
print("4. genuine gap 4.1s -> penalty reset OK")

# 5. continuous speaker: floor @700ms after 5s cumulative speech
feed("speech", 5200); feed("silence", 650)
assert v.is_speaking, "650ms must NOT endpoint under the 700ms floor"
feed("silence", 100)
assert v.last_endpoint["threshold_ms"] == 700, v.last_endpoint
print("5. long-speech floor endpoint @700ms OK:", v.last_endpoint)

# 6. repeated premature cycles escalate 400 -> 800 (cap 1100 effective)
feed("silence", 750)                       # endpoint @700
feed("silence", 2000); feed("speech", 400) # premature resume (2s gap < 3s window)
p1 = v.endpoint_penalty_ms
feed("silence", 1150)                      # endpoint @1100 (capped)
feed("silence", 2000); feed("speech", 400) # premature again -> capped penalty
p2 = v.endpoint_penalty_ms
assert p1 == 400 and p2 == 800, (p1, p2)
print("6. escalation 400 -> 800 OK (effective threshold capped at 1100)")

print("\nALL RECALIBRATED ENDPOINTING TESTS PASS")

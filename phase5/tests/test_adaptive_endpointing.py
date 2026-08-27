#!/usr/bin/env python3
"""Deterministic regression: adaptive endpointing state machine (aiva timing fix,
owner brief 2026-08-27). Uses a scripted VAD core + fake clock — no ten_vad dep.

Covers: base 300ms unchanged · premature-resume penalty (+250, cap 1100) ·
escalated thresholds enforced · genuine-gap reset · long-speech floor 700ms ·
escalation across repeated premature cycles.
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

feed("speech", 1600); feed("silence", 350)
assert v.last_endpoint["threshold_ms"] == 300, "base threshold changed"
feed("silence", 100); feed("speech", 1300)
assert v.endpoint_penalty_ms == 250, "premature penalty not applied"
feed("silence", 500); feed("silence", 120)
assert v.last_endpoint["threshold_ms"] == 550, "escalated threshold not enforced"
feed("silence", 2600)
assert v.endpoint_penalty_ms == 0, "genuine-gap reset broken"
feed("speech", 8200); feed("silence", 650)
assert v.is_speaking, "long-speech floor violated at 650ms"
feed("silence", 100)
assert v.last_endpoint["threshold_ms"] == 700, "long-speech floor not applied"
feed("silence", 600); feed("silence", 100); feed("speech", 400)
p1 = v.endpoint_penalty_ms
feed("silence", 800); feed("silence", 100); feed("speech", 400)
assert v.endpoint_penalty_ms > p1, "penalty must escalate across cycles"
for _ in range(6):
    feed("silence", 1200); feed("silence", 100); feed("speech", 300)
assert v._effective_silence_ms() == 1100, "cap broken"
print("ALL ADAPTIVE ENDPOINTING TESTS PASS")

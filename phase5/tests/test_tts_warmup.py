#!/usr/bin/env python3
"""Deterministic regression: TTS warm-up policy (CA3-approved add-on,
2026-08-30). Run: python3 phase5/tests/test_tts_warmup.py"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agent.tts_warmup import WarmupPolicy

fails = 0
def check(label, got, want):
    global fails
    ok = got == want
    if not ok:
        fails += 1
        print(f"  ✗ {label}: got {got!r}, want {want!r}")
    else:
        print(f"  ✓ {label}")

p = WarmupPolicy(idle_gap_s=15.0, max_per_session=4, warm_fresh_s=60.0)

print("== no synthesis yet ==")
check("no warm before any reply", p.should_warm(100.0), False)

print("== idle gap rule ==")
p.note_synthesis_end(100.0)
check("no warm during rapid exchange (t+5s)", p.should_warm(105.0), False)
check("warm after idle gap (t+16s)", p.should_warm(116.0), True)

print("== freshness window ==")
p.on_warmup_done(116.0)
check("is_warm right after warmup", p.is_warm(117.0), True)
check("no re-warm while fresh", p.should_warm(150.0), False)     # gap ok, but warm
check("not warm after freshness expires", p.is_warm(200.0), False)  # > 60s
p._last_warm_at = None  # reset for next section
p.note_synthesis_end(200.0)
check("re-warm after expiry + gap", p.should_warm(216.0), True)

print("== per-session quota budget ==")
for i in range(p.max_per_session):
    p.on_warmup_done(220.0 + i)
    p._last_warm_at = None  # simulate expiry between warmups
    p.note_synthesis_end(220.0 + i)
check("budget exhausted -> no more warmups", p.should_warm(300.0), False)

print("== determinism ==")
p2 = WarmupPolicy()
p2.note_synthesis_end(10.0)
check("deterministic decision", p2.should_warm(26.0), True)
p3 = WarmupPolicy()
p3.note_synthesis_end(10.0)
check("identical instance same answer", p3.should_warm(26.0), True)

print()
if fails:
    print(f"FAIL ({fails})")
    sys.exit(1)
print("ALL PASS")

#!/usr/bin/env python3
"""Regression: response playback state + reconciliation (directive fix 2,
2026-08-29) and endpointing hangover (directive fix 1).

Maps the directive's 7 validation scenarios to deterministic unit tests.
Run: python3 phase5/tests/test_response_state.py"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agent.response_state import classify, reconcile_payload, FULLY_PLAYED, PARTIALLY_PLAYED, UNHEARD

fails = 0
def check(name, cond, extra=""):
    global fails
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {extra}")
    if not cond:
        fails += 1

# --- directive scenarios 2/3/4: state classification ---
check("S2 finished -> FULLY_PLAYED",
      classify(False, True, 50) == FULLY_PLAYED)
check("S3 interrupt before first audio -> UNHEARD",
      classify(True, False, 0) == UNHEARD)
check("S3b interrupt with TTFA but zero chars -> UNHEARD",
      classify(True, True, 0) == UNHEARD)
check("S4 interrupt halfway -> PARTIALLY_PLAYED",
      classify(True, True, 33) == PARTIALLY_PLAYED)

# --- reconciliation payload: only for non-fully-played, immediate next turn ---
check("no previous -> None", reconcile_payload(None) is None)
check("FULLY_PLAYED -> no reconciliation",
      reconcile_payload({"status": FULLY_PLAYED, "turn": 5}) is None)
p = reconcile_payload({"status": UNHEARD, "turn": 6,
                       "heard_text": "", "text": "the full secret reply"})
check("UNHEARD payload: status + no heard text, full text withheld",
      p["status"] == UNHEARD and p["turn"] == 6 and "heard_text" not in p
      and "secret" not in json.dumps(p))
p = reconcile_payload({"status": PARTIALLY_PLAYED, "turn": 7,
                       "heard_text": "haan, main soch raha",
                       "text": "haan, main soch raha tha ki..."})
check("PARTIAL payload: heard_text included for continuation",
      p["status"] == PARTIALLY_PLAYED and "soch raha" in p["heard_text"])

# --- directive scenario 7: no duplicate turns in history ---
# UNHEARD: the caller must NOT add the unheard text to history (enforced by
# construction in main.py: history add requires ttfa_logged); the payload
# carries no full text, so the model cannot resurrect it.
check("S7 UNHEARD payload withholds full text (no resurrection path)",
      "secret" not in json.dumps(reconcile_payload(
          {"status": UNHEARD, "turn": 1, "heard_text": "", "text": "the full secret reply"})))

# --- fix 1: endpointing hangover (pure module, numpy-free) ---
from providers.endpointing import HangoverTracker, GRADUAL, HARD_CUT

# natural decay: trailing frames well below the utterance peak
h = HangoverTracker()
for peak in (8000, 9000, 7000, 5000, 3000, 1500, 800, 400):
    h.note_speech_frame(peak)
profile, extra = h.evaluate()
check(f"natural decay -> {profile}, no hangover", profile == GRADUAL and extra == 0)

# hard cut: energy still near peak when it vanishes
h2 = HangoverTracker()
for peak in (8000, 9000, 7000, 8500, 8000):
    h2.note_speech_frame(peak)
profile2, extra2 = h2.evaluate()
check(f"hard cut -> {profile2} + hangover", profile2 == HARD_CUT and extra2 == h2.hangover_ms)

# reset for a new utterance
h2.reset()
check("reset clears state", h2.evaluate() == (GRADUAL, 0))

# degenerate: no speech frames at all
h3 = HangoverTracker()
check("no frames -> gradual", h3.evaluate() == (GRADUAL, 0))

# latency unchanged for natural speech: hangover applies ONLY to hard cut
check("gradual hangover is 0 (latency preserved)", extra == 0)

print(f"\n{'ALL PASS' if fails == 0 else f'{fails} FAILURES'}")
sys.exit(1 if fails else 0)

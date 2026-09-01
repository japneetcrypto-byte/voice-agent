#!/usr/bin/env python3
"""Deterministic regression: semantic ack selection (owner directive
2026-08-30 — "the word being spoken should make sense"). 
Run: python3 phase5/tests/test_ack_selection.py"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agent.ack_bridge import pick_ack_for, ACK_POOL

fails = 0
def check(label, got, want):
    global fails
    ok = got == want
    if not ok:
        fails += 1
        print(f"  ✗ {label}: got {got!r}, want {want!r}")
    else:
        print(f"  ✓ {label}")

print("== no-ack relations (silence is correct) ==")
check("listen request -> no ack", pick_ack_for("chup yaar pehle meri baat sun", "listen_request", 1), (None, "no_ack:listen_request"))
check("backchannel -> no ack", pick_ack_for("haan", "backchannel", 1), (None, "no_ack:backchannel"))
check("empty -> no ack", pick_ack_for("", "content", 1), (None, "no_ack:empty"))

print("== GAP 2026-09-01: no 'batao/bolo' imperative filler in any ack pool ==")
_banned = ("bata", "bolo")
check("no 'batao/bata/bolo' filler in any pool",
      all(not any(b in w.lower() for b in _banned)
          for pool in ACK_POOL.values() for w in pool), True)
check("'achha, batao' removed",
      all("achha, batao" != w for pool in ACK_POOL.values() for w in pool), True)

print("== main.py ack gate must skip first-word greeting turns ==")
_main_py = open(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "agent", "main.py")).read()
_ack_idx = _main_py.index("_rail is None and _greeting is None")
check("ack condition excludes first-word greeting turns",
      "not _first_is_greeting" in _main_py[_ack_idx:_ack_idx + 300], True)
check("_first_is_greeting defined from GREETING_MARKERS",
      "GREETING_MARKERS" in _main_py and "_first_is_greeting" in _main_py, True)

print("== semantic categories ==")
w, r = pick_ack_for("kal kya plan hai bhai?", "content", 0)
check("question category", r, "question")
check("question word in pool", w in ACK_POOL["question"], True)
w2, r2 = pick_ack_for("mujhe bahut gussa aa raha hai", "content", 1)
check("venting category", r2, "venting")
check("venting word in pool", w2 in ACK_POOL["venting"], True)
check("venting never 'theek hai'", w2 != "theek hai", True)
w3, r3 = pick_ack_for("bahut badhiya laga yaar", "content", 2)
check("positive category", r3, "positive")
w4, r4 = pick_ack_for("kal jaana hai", "content", 3)
check("neutral category", r4, "neutral")
check("neutral word in pool", w4 in ACK_POOL["neutral"], True)

print("== determinism + rotation ==")
check("same input -> same word", pick_ack_for("kal kya plan hai bhai?", "content", 5)[0],
      pick_ack_for("kal kya plan hai bhai?", "content", 5)[0])
# rotation: different turn_no on same pool -> picks differ (or at least stay in pool)
words = {pick_ack_for("kal kya plan hai bhai?", "content", i)[0] for i in range(9)}
check("rotation stays in question pool", words <= set(ACK_POOL["question"]), True)
check("rotation produces variety", len(words) > 1, True)

print("== semantic soundness (no out-of-the-blue) ==")
# venting text must never select 'theek hai'-family or neutral-positive chirpy
for i in range(6):
    w5, r5 = pick_ack_for("mera dil toot gaya yaar, bahut dukh hua", "content", i)
    check(f"venting t{i} -> venting", r5, "venting")
# question with negative words -> question takes priority (user asked sth)
_, r6 = pick_ack_for("tum kyun pareshaan ho?", "content", 0)
check("question over negative", r6, "question")

print()
if fails:
    print(f"FAIL ({fails})")
    sys.exit(1)
print("ALL PASS")

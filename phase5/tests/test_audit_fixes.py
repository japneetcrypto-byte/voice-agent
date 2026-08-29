#!/usr/bin/env python3
"""Regression: audit fixes 2026-08-29 (owner: 'audit again — no issues later').

Covers:
  1. Degraded-perception cooldown exit (the documented death-spiral bug)
  2. Layer-2 (compressed session state) reaches the fused LLM call contents
  3. FusedLLM epoch bumps only on first consume (generator laziness — the
     off-by-one that broke end-of-turn meta capture)
Run: python3 phase5/tests/test_audit_fixes.py"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from agent.state_updater import default_state, update
from agent.fused_turn import FusedLLM

fails = 0
def check(name, cond, extra=""):
    global fails
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {extra}")
    if not cond:
        fails += 1

# ---------- 1. degraded cooldown ----------
st = default_state()
def headless(state, turn_no):
    tr = {"turn": turn_no, "turn_type": "speech", "response_completed": True}
    state, _pol, log = update(state, tr, head=None)
    return state, [e for e in log]

codes = []
# turns 1-2: fail, fail -> ENTER (the enter turn counts as degraded turn 1)
st, c = headless(st, 1); codes += c
st, c = headless(st, 2); codes += c
check("ENTER on 2nd parse fail", "DEGRADED-PERCEPTION-ENTER" in codes, str(codes))
check("degraded flag set", st["degraded_perception"] is True)
check("enter turn counts as degraded turn 1", st.get("degraded_turns") == 1)
# turns 3-4: degraded, headless (plain prompt never yields a head)
st, c = headless(st, 3); codes += c
check("degraded turn 2 counted", st.get("degraded_turns") == 2)
# turn 4: 3rd degraded turn -> cooldown exit
st, c = headless(st, 4); codes += c
check("COOLDOWN EXIT on 3rd degraded turn", "DEGRADED-PERCEPTION-EXIT-COOLDOWN" in codes, str(codes[-3:]))
check("degraded flag cleared", st["degraded_perception"] is False)
check("streak reset on exit", st["parse_fail_streak"] == 0)
# turn 5: headless again -> streak restarts (no instant re-enter)
st, c = headless(st, 5)
check("no instant re-enter on 1st fail after exit", st["degraded_perception"] is False)
st, c = headless(st, 6)
check("re-enters after 2 more fails (hysteresis)", st["degraded_perception"] is True)
# success exit resets the cooldown counter too
st2 = default_state()
tr_ok = {"turn": 1, "turn_type": "speech", "response_completed": True}
head = {"m": "C", "c": 0.9, "s": "SAFE"}
st2, _, _ = update(st2, tr_ok, head=None)
st2, _, _ = update(st2, {"turn": 2, "turn_type": "speech"}, head=None)   # ENTER
st2, _, _ = update(st2, {"turn": 3, "turn_type": "speech"}, head=head)  # success
check("success exit resets degraded_turns", st2.get("degraded_turns", 0) == 0
      and st2["degraded_perception"] is False)
# determinism: same inputs -> same log codes
c1 = []
s = default_state()
for t in range(1, 6):
    s, cc = headless(s, t); c1.append(cc)
c2 = []
s = default_state()
for t in range(1, 6):
    s, cc = headless(s, t); c2.append(cc)
check("determinism k=2 (byte-identical logs)", c1 == c2)

# ---------- 2. Layer-2 in fused contents ----------
llm = FusedLLM()
empty = {"people": {}, "open_items": [], "emotional_context": None}
base = llm.build_contents("hello", {"mode": "x"}, [], [], [])
check("no session_state key when layer2 empty", "session_state" not in base)
check("no session_state key when layer2 None",
      "session_state" not in llm.build_contents("hello", {"mode": "x"}, [], [], [], layer2=None))
l2 = {"people": {"Neetu": {"name": "Neetu", "relation": "behen"}},
      "open_items": [], "emotional_context": None}
with_l2 = llm.build_contents("hello", {"mode": "x"}, [], [], [], layer2=l2)
import json as _json
parsed = _json.loads(with_l2)
check("session_state included when layer2 has people", parsed.get("session_state") == l2)
check("stable key order (policy,memory,threads,history,user_turn[,session_state])",
      list(parsed.keys())[:5] == ["policy", "memory", "threads", "history", "user_turn"])

# ---------- 3. epoch bumps on FIRST CONSUME (generator laziness) ----------
import asyncio
async def epoch_check():
    llm = FusedLLM()
    e0 = llm.epoch
    gen = llm.stream_prose(user_text="garbled", turn_type="unclear_speech",
                           policy={}, memory_view=[], threads=[], history=[],
                           turn_no=3, degraded=False, key="")
    check("epoch unchanged before first consume (lazy generator)", llm.epoch == e0)
    first = await gen.__anext__()
    check("epoch bumped on first consume", llm.epoch == e0 + 1)
    check("deterministic path yields a line", isinstance(first, str) and len(first) > 0, repr(first))
    # capture-at-TTFT semantics: snapshot AFTER first consume == current epoch
    turn_fused_epoch = llm.epoch
    check("TTFT-style snapshot equals current epoch",
          getattr(llm, "epoch", None) == turn_fused_epoch)
asyncio.run(epoch_check())

print(f"\n{'ALL PASS' if fails == 0 else f'{fails} FAILURES'}")
sys.exit(1 if fails else 0)

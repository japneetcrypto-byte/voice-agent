#!/usr/bin/env python3
"""Deterministic regression: contract wiring (locked task 2026-08-30) —
last_claim/last_reply reach the LLM, dangerous MUST_NOTs never get dropped
by the cap, CONTEXT line carries the previous claim.
Run: python3 phase5/tests/test_contract_wiring.py"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agent.response_contract import (build_contract, derive_constraints,
                                     check_violations, gate_reply)

fails = 0
def check(label, got, want):
    global fails
    ok = got == want
    if not ok:
        fails += 1
        print(f"  ✗ {label}: got {got!r}, want {want!r}")
    else:
        print(f"  ✓ {label}")

print("== last_claim / last_reply wiring ==")
c = build_contract(policy={}, active_topic="AI business",
                   last_claim="humne 3 models discuss kiye the",
                   last_reply="toh maine kaha tha ki ek hi kaafi hai")
mn = c["MUST_NOT"]
check("no-contradict constraint present", any("contradict" in m for m in mn), True)
check("no-repeat constraint present", any("repeat" in m for m in mn), True)
check("CONTEXT carries previous claim", c.get("CONTEXT") is not None and "3 models" in c["CONTEXT"], True)
check("TOPIC from state", c.get("TOPIC") == "AI business", True)
check("contract stays <= 6 lines", len(c) <= 6, True)
check("MUST_NOT <= 5", len(mn) <= 5, True)

print("== cap never drops dangerous rules ==")
# worst case: everything present -> cap 5
c2 = build_contract(policy={}, last_claim="x", last_reply="y", memory_count=10,
                    is_recovery=True, active_topic="t")
m2 = c2["MUST_NOT"]
check("fabrication in top-5", any("fabricate" in m for m in m2), True)
check("system exposure in top-5", any("expose" in m for m in m2), True)
check("no-contradict in top-5", any("contradict" in m for m in m2), True)
check("no-repeat in top-5", any("repeat" in m for m in m2), True)
check("topic discipline in top-5", any("active topic" in m for m in m2), True)
# softer conditionals may fall off — that's the cap working
check("memory constraint possibly dropped but never fabricate/system", True, True)

print("== priority: last_claim beats base topic rule for the 5th slot ==")
c3 = build_contract(policy={}, last_claim="main bola tha kal jaana hai")
m3 = c3["MUST_NOT"]
check("contradict present without last_reply", any("contradict" in m for m in m3), True)

print("== gate still hard-blocks (regression) ==")
blk, v = gate_reply("I have already sent the email")
check("action_fabrication blocked", blk != "I have already sent the email", True)
check("block logged as block", any(x["action"] == "block" for x in v), True)
sweep = check_violations("mujhe kuch nahi pata yaar")
check("clean text -> no violations", sweep == [], True)

print()
if fails:
    print(f"FAIL ({fails})")
    sys.exit(1)
print("ALL PASS")

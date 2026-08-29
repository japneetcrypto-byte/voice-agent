#!/usr/bin/env python3
"""Regression: Response Contract (directive 2026-08-29 — boundaries in code,
LLM inside them). Run: python3 phase5/tests/test_response_contract.py"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agent.response_contract import build_contract, derive_constraints, check_violations, gate_reply

fails = 0
def check(name, cond, extra=""):
    global fails
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {extra}")
    if not cond:
        fails += 1

# ---- contract shape ----
c = build_contract(policy={"response_goal": "encourage_continuation"},
                   active_topic="voice agent", memory_count=5)
check("has GOAL", "GOAL" in c and c["GOAL"] == "encourage_continuation")
check("has TOPIC", c["TOPIC"] == "voice agent")
check("has MODE", "MODE" in c)
check("has MUST_NOT with >=2 constraints", isinstance(c["MUST_NOT"], list) and len(c["MUST_NOT"]) >= 2)
check("MUST_NOT <=5 (compact)", len(c["MUST_NOT"]) <= 5)
check("serialized <120 tokens (compact)", len(json.dumps(c)) < 500, f"{len(json.dumps(c))}c")

# ---- detail mode changes MODE ----
c_detail = build_contract(policy={}, detail_mode=True)
check("detail mode -> MODE=detail", c_detail["MODE"] == "detail")

# ---- recovery mode ----
c_rec = build_contract(policy={}, route_action="contextual_recovery")
check("recovery -> MODE=recovery", c_rec["MODE"] == "recovery")
check("recovery adds short-response constraint",
      any("long" in mc for mc in c_rec["MUST_NOT"]))

# ---- memory constraint appears when memory exists ----
c_mem = build_contract(policy={}, memory_count=10)
check("memory_count>0 -> no proactive memory constraint",
      any("old-session" in mc for mc in c_mem["MUST_NOT"]))
c_no_mem = build_contract(policy={}, memory_count=0)
check("memory_count=0 -> no memory constraint",
      not any("old-session" in mc for mc in c_no_mem["MUST_NOT"]))

# ---- last_reply adds no-repeat constraint ----
c_rep = build_contract(policy={}, last_reply="haan bol, sun raha hoon")
check("last_reply -> no-repeat constraint",
      any("repeat" in mc or "verbatim" in mc for mc in c_rep["MUST_NOT"]))

# ---- determinism ----
c1 = build_contract(policy={"response_goal": "x"}, memory_count=3)
c2 = build_contract(policy={"response_goal": "x"}, memory_count=3)
check("deterministic", c1 == c2)

# ---- hard violation gate ----
v1 = check_violations("Remember when I told you about the Akharan incident?")
check("memory_proactive detected", any(v["type"] == "memory_proactive" for v in v1))
v2 = check_violations("I am an AI language model and I cannot do that")
check("system_exposure detected", any(v["type"] == "system_exposure" for v in v2))
v3 = check_violations("I've already sent the email to the team")
check("action_fabrication detected", any(v["type"] == "action_fabrication" for v in v3))
v4 = check_violations("haan bol, kya scene hai?")
check("clean reply -> no violations", len(v4) == 0)
check("gate_reply passes through (flag-only for now)",
      gate_reply("haan bol")[0] == "haan bol")

# ---- determinism of gate ----
v_a = check_violations("Remember when I said something?")
v_b = check_violations("Remember when I said something?")
check("gate deterministic", v_a == v_b)

print(f"\n{'ALL PASS' if fails == 0 else f'{fails} FAILURES'}")
sys.exit(1 if fails else 0)

#!/usr/bin/env python3
"""Regression: Call Supervisor (owner brief 2026-08-29 — the "senior jumping
in"). Evidence: session 133659 — user said hello twice into silence.

Run: python3 phase5/tests/test_call_supervisor.py"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agent.call_supervisor import CallSupervisor, ENGAGE_REASONS, build_snapshot
from agent.state_updater import default_state, update

fails = 0
def check(name, cond, extra=""):
    global fails
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {extra}")
    if not cond:
        fails += 1

sup = CallSupervisor()
T = 1000.0  # fixed clock

# 1. the exact incident: hello with no answer -> ENGAGE
d = sup.evaluate({"reason": "reachout_unanswered", "turn": 21, "user_text": "हेलो"}, now=T)
check("hello-no-answer engages", d is not None and d["reason"] == "reachout_unanswered")

# 2. same turn never rescues twice
check("same turn deduped", sup.evaluate({"reason": "reachout_unanswered", "turn": 21}, now=T + 1) is None)

# 3. cooldown: second incident within 15s stands down (one rescue per incident)
d2 = sup.evaluate({"reason": "unanswered", "turn": 22, "user_text": "कहां गए"}, now=T + 5)
check("cooldown blocks rapid re-engage", d2 is None)

# 4. after the cooldown a NEW incident engages and ESCALATES (systemic alarm)
d3 = sup.evaluate({"reason": "skipped", "turn": 25, "user_text": "hello?"}, now=T + 20)
check("post-cooldown engage", d3 is not None)
check("second engagement escalates", d3.get("escalate") is True)

# 5. non-engagement reasons: deliberate WAIT / healthy turns / echo
sup2 = CallSupervisor()
for reason in ("suppressed", "echo", "replied", None):
    check(f"reason {reason!r} ignored", sup2.evaluate({"reason": reason, "turn": 1}, now=T) is None)

# 6. reason catalog is documented (PM-readable) and covers the incident classes
check("catalog covers 4 incident classes",
      {"skipped", "pipeline_error", "unanswered", "reachout_unanswered"} == set(ENGAGE_REASONS))

# 7. snapshot carries the 'what happened there' payload
snap = build_snapshot("reachout_unanswered", {"turn": 21, "user_text": "हेलो"},
                      {"engine_bound": True, "last_engine_path": "fused",
                       "last_tts_provider": "fish", "wait_streak": 0})
check("snapshot has cause + context",
      snap["reason"] == "reachout_unanswered" and snap["last_engine_path"] == "fused"
      and snap["engine_bound"] is True and "user_text" in snap)

# 8. the updater never treats a supervisor turn as PARSE-FAIL
st = default_state()
st, _pol, log = update(st, {"turn": 50, "turn_type": "supervisor_rescue",
                             "response_completed": True}, head=None)
check("supervisor turn != PARSE-FAIL", "TURN-SUPERVISOR-RESCUE" in log and "PARSE-FAIL" not in log, str(log))
st2, _, log2 = update(default_state(), {"turn": 1, "turn_type": "supervisor_rescue"}, head=None)
check("supervisor turn deterministic (k=2 identical logs)", log == log2)

print(f"\n{'ALL PASS' if fails == 0 else f'{fails} FAILURES'}")
sys.exit(1 if fails else 0)

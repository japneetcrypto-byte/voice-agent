#!/usr/bin/env python3
"""ACCEPTANCE 5 — previous-session memory is NEVER used as input to the pass.

Rows committed in a prior session (same owner!) must not appear in the
consolidation prompt. Only this session's validated turns / L2 / captures are
input. (Dedupe uses per-content store lookups, which is not "input" — the
LLM never sees them.)

Locked in docs/SESSION_END_CONSOLIDATION_V1.md §3, §10 test 5.
Run: python3 phase5/tests/test_previous_session_memory_not_input.py
"""
import sys, os, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agent.memory_store import MemoryStore
from agent.session_consolidation import consolidate

fails = 0
def check(label, got, want):
    global fails
    ok = got == want
    if not ok:
        fails += 1
        print(f"  ✗ {label}: got {got!r}, want {want!r}")
    else:
        print(f"  ✓ {label}")

db = os.path.join(tempfile.mkdtemp(), "m.db")
store = MemoryStore(db)
OWNER = "owner-b5"

# prior-session committed memory (deterministic path from an earlier session)
store.commit(OWNER, {"type": "semantic", "content": "user ka naam Rahul hai",
                     "criterion": "explicit"}, immediate=True)

captured = {}
def capt(prompt):
    captured["p"] = prompt
    return '{"bullets": [], "nothing_important_missed": true}'

s = consolidate(owner_id=OWNER, store=store,
                session_turns=[(1, "मुझे चाय पसंद है")], llm_call=capt)
check("pass ran ok", s["status"], "ok")
check("prior-session name absent from prompt", "Rahul" not in captured["p"], True)
check("prior-session content absent from prompt", "ka naam" not in captured["p"], True)
check("this session's turn present", "मुझे चाय पसंद है" in captured["p"], True)

print("\n" + ("ALL PASS" if fails == 0 else f"{fails} FAILURE(S)"))
sys.exit(1 if fails else 0)

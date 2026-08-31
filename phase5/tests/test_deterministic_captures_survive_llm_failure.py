#!/usr/bin/env python3
"""ACCEPTANCE 3 — deterministic captures remain intact even if Gemini fails/timeouts.

The consolidation pass is purely additive: when the LLM call raises, the pass
must fail loudly and change NOTHING in the store. The deterministic path
(explicit commits mid-session + end_session promote of occurrences>=2) is
unaffected and must still work afterwards, and rows must survive a restart
(fresh MemoryStore on the same DB file).

Locked in docs/SESSION_END_CONSOLIDATION_V1.md §7, §10 test 3.
Run: python3 phase5/tests/test_deterministic_captures_survive_llm_failure.py
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
OWNER = "owner-b3"

# deterministic captures already written MID-SESSION (as the live system does)
store.commit(OWNER, {"type": "episodic",
                     "content": "user: मैं उत्तराखंड गया था, देहरादून और नैनीताल देखे",
                     "criterion": "explicit"}, immediate=True)
store.commit(OWNER, {"type": "saved_number",
                     "content": "user's mobile number: 9935411907",
                     "criterion": "explicit"}, immediate=True)
# inferred: seen twice -> occurrences=2 -> promotes at end_session
store.commit(OWNER, {"type": "semantic", "content": "user को चाय पसंद है", "criterion": "salient"})
store.commit(OWNER, {"type": "semantic", "content": "user को चाय पसंद है", "criterion": "salient"})
before = store.db.execute(
    "SELECT content, status, occurrences FROM memory WHERE owner_id=?", (OWNER,)).fetchall()

def boom(prompt):
    raise RuntimeError("gemini down")

s = consolidate(owner_id=OWNER, store=store, session_turns=[(1, "मुझे चाय पसंद है")], llm_call=boom)
check("status failed", s["status"], "failed")
check("reason surfaces the error", "gemini down" in s["reason"], True)
after = store.db.execute(
    "SELECT content, status, occurrences FROM memory WHERE owner_id=?", (OWNER,)).fetchall()
check("failed pass changed nothing", sorted(after), sorted(before))

# clean shutdown: the deterministic end_session path still promotes occurrences>=2
store.promote_pending(OWNER, keep=True)
view = store.view(OWNER)
check("explicit place fact survived", any("उत्तराखंड" in v for v in view), True)
check("saved number survived", any("saved number" in v for v in view), True)
check("occurrences>=2 promoted to committed", any("चाय" in v for v in view), True)

# restart (new worker, same DB)
store2 = MemoryStore(db)
check("survives restart", any("उत्तराखंड" in v for v in store2.view(OWNER)), True)

print("\n" + ("ALL PASS" if fails == 0 else f"{fails} FAILURE(S)"))
sys.exit(1 if fails else 0)

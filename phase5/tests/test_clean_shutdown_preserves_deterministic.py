#!/usr/bin/env python3
"""ACCEPTANCE 9 — clean shutdown does not lose already-safe deterministic captures.

Full lifecycle: session 1 commits explicit place fact + saved number +
occurrences=2 pending, then the consolidation LLM FAILS at shutdown. The
deterministic end_session() must still promote occurrences>=2 and keep the
explicit rows; a restart (fresh MemoryStore + SessionState on the same DB)
must recall them — i.e., the deterministic memory path works even when the
consolidation pass fails.

Locked in docs/SESSION_END_CONSOLIDATION_V1.md §7, §10 test 9.
Run: python3 phase5/tests/test_clean_shutdown_preserves_deterministic.py
"""
import sys, os, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agent.memory_store import MemoryStore
from agent.session_state import SessionState
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
logdir = tempfile.mkdtemp()
OWNER = "owner-b9"

s1 = SessionState(owner_id=OWNER, store=MemoryStore(db), log_dir=logdir)
# deterministic mid-session writes
s1.store.commit(OWNER, {"type": "episodic",
                        "content": "user: मैं उत्तराखंड गया था, देहरादून और नैनीताल देखे",
                        "criterion": "explicit"}, immediate=True)
s1.store.commit(OWNER, {"type": "saved_number",
                        "content": "user's mobile number: 9935411907",
                        "criterion": "explicit"}, immediate=True)
s1.store.commit(OWNER, {"type": "semantic", "content": "user को चाय पसंद है", "criterion": "salient"})
s1.store.commit(OWNER, {"type": "semantic", "content": "user को चाय पसंद है", "criterion": "salient"})

def boom(prompt):
    raise RuntimeError("gemini down")

s = consolidate(owner_id=OWNER, store=s1.store,
                session_turns=[(1, "मुझे चाय पसंद है")], llm_call=boom)
check("consolidation failed", s["status"], "failed")

# clean shutdown — deterministic path, pass failure is irrelevant
s1.end_session()
view = s1.store.view(OWNER)
check("explicit place fact intact after shutdown", any("उत्तराखंड" in v for v in view), True)
check("saved number intact after shutdown", any("saved number" in v for v in view), True)
check("occurrences>=2 pending promoted", any("चाय" in v for v in view), True)

# restart (new worker) — recall
s2 = SessionState(owner_id=OWNER, store=MemoryStore(db), log_dir=logdir)
v2 = s2.memory_view()
check("recall place fact after restart", any("उत्तराखंड" in v for v in v2), True)
check("recall saved number after restart", any("saved number" in v for v in v2), True)

print("\n" + ("ALL PASS" if fails == 0 else f"{fails} FAILURE(S)"))
sys.exit(1 if fails else 0)

#!/usr/bin/env python3
"""ACCEPTANCE 11 (adversarial, owner battle-test) — an LLM cannot confirm its
own proposals.

The same INVENTED fact proposed by the consolidation LLM in Session 1 and
Session 2, with NO deterministic extractor ever seeing it, must stay pending
forever:

- Session 1 -> pending (never committed)
- Session 2 -> DEDUPE-skip (the pass's store.lookup finds the pending row
  BEFORE any commit call) — the LLM re-proposal does NOT confirm it
- occurrences NEVER bumped by LLM proposals (stays 1 across every session)
- promote_pending() at every session end cannot promote it (occurrences < 2)
- view() never shows it; it can never become committed through any
  consolidation pass

Locked in docs/SESSION_END_CONSOLIDATION_V1.md §2 principle 4 ("a LLM
proposal can never confirm a LLM proposal"), §7, §10.
Run: python3 phase5/tests/test_llm_cannot_confirm_llm_proposals.py
"""
import sys, os, tempfile, json
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
OWNER = "owner-b11"
INVENTED = "user को चाय पसंद है"
TURNS = [(1, "मुझे चाय पसंद है")]
RAW = json.dumps({"bullets": [
    {"type": "preference", "content": INVENTED, "turn_ref": 1, "confidence": "high"},
], "nothing_important_missed": True}, ensure_ascii=False)

def status_of(store):
    return store.db.execute(
        "SELECT status, occurrences FROM memory WHERE owner_id=? AND content=?",
        (OWNER, INVENTED)).fetchone()

# ---------------- SESSION 1 ----------------
store1 = MemoryStore(db)
s1 = consolidate(owner_id=OWNER, store=store1, session_turns=TURNS, llm_call=lambda p: RAW)
check("session1: pending=1", s1["pending"], 1)
check("session1: row pending, occurrences=1", status_of(store1), ("pending", 1))
check("session1: invisible to view()", store1.view(OWNER), [])
store1.promote_pending(OWNER, keep=True)          # session-1 end
check("session1 end: still pending (occ 1 < 2, nothing to promote)",
      status_of(store1)[0], "pending")

# ---------------- SESSION 2 (restart: fresh store, same DB) ----------------
store2 = MemoryStore(db)
s2 = consolidate(owner_id=OWNER, store=store2, session_turns=TURNS, llm_call=lambda p: RAW)
check("session2: LLM re-proposal DEDUPED, not confirmed", s2["deduped"], 1)
check("session2: no new row created",
      store2.db.execute("SELECT COUNT(*) FROM memory WHERE owner_id=?", (OWNER,)).fetchone()[0], 1)
check("session2: still pending, occurrences still 1", status_of(store2), ("pending", 1))
store2.promote_pending(OWNER, keep=True)          # session-2 end
check("session2 end: still pending", status_of(store2)[0], "pending")

# ---------------- SESSION 3 ----------------
store3 = MemoryStore(db)
s3 = consolidate(owner_id=OWNER, store=store3, session_turns=TURNS, llm_call=lambda p: RAW)
check("session3: deduped again", s3["deduped"], 1)
check("session3: never committed, occurrences never bumped",
      status_of(store3), ("pending", 1))
store3.promote_pending(OWNER, keep=True)          # session-3 end
check("session3 end: STILL pending — LLM can never self-confirm",
      status_of(store3)[0], "pending")
check("final: view() never shows the invented fact", store3.view(OWNER), [])

print("\n" + ("ALL PASS" if fails == 0 else f"{fails} FAILURE(S)"))
sys.exit(1 if fails else 0)

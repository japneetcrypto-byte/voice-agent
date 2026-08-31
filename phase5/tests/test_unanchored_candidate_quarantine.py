#!/usr/bin/env python3
"""ACCEPTANCE 2 — unanchored candidate -> QUARANTINE, never direct commit.

A bullet whose content has no lexical anchor in its referenced turn, or whose
turn_ref does not exist in the session, is stored as status='quarantined'
(invisible to view(), never auto-promoted). It must NEVER become pending or
committed.

Locked in docs/SESSION_END_CONSOLIDATION_V1.md §5, §10 test 2.
Run: python3 phase5/tests/test_unanchored_candidate_quarantine.py
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
store = MemoryStore(db)
OWNER = "owner-b2"
# turn shares NO content word with bullet 1 ('फुटबॉल'/'पसंद' vs 'दिल्ली'/'रहता')
TURNS = [(1, "मेरा भाई दिल्ली में रहता है")]

# bullet 1: content words not in the referenced turn -> unanchored
# bullet 2: turn_ref 99 does not exist in the session -> unanchored
RAW = json.dumps({"bullets": [
    {"type": "preference", "content": "user को फुटबॉल पसंद है", "turn_ref": 1, "confidence": "high"},
    {"type": "preference", "content": "user को क्रिकेट पसंद है", "turn_ref": 99, "confidence": "med"},
], "nothing_important_missed": True}, ensure_ascii=False)

s = consolidate(owner_id=OWNER, store=store, session_turns=TURNS, llm_call=lambda p: RAW)
check("unanchored_quarantined=2", s["unanchored_quarantined"], 2)
check("pending=0", s["pending"], 0)
check("anchored=0", s["anchored"], 0)
rows = store.db.execute("SELECT status FROM memory WHERE owner_id=?", (OWNER,)).fetchall()
check("both rows quarantined", sorted(r[0] for r in rows), ["quarantined", "quarantined"])
check("quarantined rows invisible to view()", store.view(OWNER), [])

# ---- quarantine safety (owner battle-test): not promotable, not bumpable,
# not committable by later passes, auditable-only ----
store.promote_pending(OWNER, keep=True)   # any session end
check("promote_pending cannot promote quarantined rows",
      store.db.execute("SELECT status FROM memory WHERE owner_id=?", (OWNER,)).fetchall(),
      [("quarantined",), ("quarantined",)])
s2 = consolidate(owner_id=OWNER, store=store, session_turns=TURNS, llm_call=lambda p: RAW)
check("later pass dedupes the same quarantined content (no commit path)",
      s2["deduped"], 2)
rows2 = store.db.execute(
    "SELECT status, occurrences FROM memory WHERE owner_id=?", (OWNER,)).fetchall()
check("quarantined rows unchanged: occ=1, never committed",
      sorted(rows2), [("quarantined", 1), ("quarantined", 1)])
check("still invisible to view() after later pass", store.view(OWNER), [])
# auditable-only: the rows exist with the quarantine status and reason trail
check("auditable: quarantine rows present in the store",
      len(store.db.execute("SELECT * FROM memory WHERE status='quarantined'").fetchall()), 2)

print("\n" + ("ALL PASS" if fails == 0 else f"{fails} FAILURE(S)"))
sys.exit(1 if fails else 0)

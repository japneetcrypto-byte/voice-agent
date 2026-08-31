#!/usr/bin/env python3
"""ACCEPTANCE 7 — duplicate proposals don't create duplicate trusted rows.

Same bullet twice (within a batch or across passes) -> one row max. A bullet
matching an existing committed row -> dedupe-skip. LLM proposals NEVER bump
`occurrences` (recurrence is a deterministic-sighting signal only), even when
the same content is re-proposed in a later pass.

Locked in docs/SESSION_END_CONSOLIDATION_V1.md §5, §7, §10 test 7.
Run: python3 phase5/tests/test_duplicate_proposals_deduped.py
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
OWNER = "owner-b7"
TURNS = [(1, "मुझे क्रिकेट और चाय दोनों पसंद है")]

# already committed (deterministic path earlier)
store.commit(OWNER, {"type": "preference", "content": "user को क्रिकेट पसंद है",
                     "criterion": "explicit"}, immediate=True)

RAW = json.dumps({"bullets": [
    {"type": "preference", "content": "user को क्रिकेट पसंद है", "turn_ref": 1, "confidence": "high"},  # dup of committed
    {"type": "preference", "content": "user को क्रिकेट पसंद है", "turn_ref": 1, "confidence": "high"},  # dup within batch
    {"type": "preference", "content": "user को चाय पसंद है", "turn_ref": 1, "confidence": "high"},      # genuinely new
], "nothing_important_missed": True}, ensure_ascii=False)

s = consolidate(owner_id=OWNER, store=store, session_turns=TURNS, llm_call=lambda p: RAW)
check("deduped=2", s["deduped"], 2)
check("pending=1 (only the new one)", s["pending"], 1)
occ = store.db.execute(
    "SELECT occurrences FROM memory WHERE owner_id=? AND content=?",
    (OWNER, "user को क्रिकेट पसंद है")).fetchone()[0]
check("committed row occurrences NOT bumped by LLM", occ, 1)
check("total rows = 2 (committed + 1 pending)",
      store.db.execute("SELECT COUNT(*) FROM memory WHERE owner_id=?", (OWNER,)).fetchone()[0], 2)

# second pass re-proposes the same new content -> dedupe against pending, no bump
RAW2 = json.dumps({"bullets": [
    {"type": "preference", "content": "user को चाय पसंद है", "turn_ref": 1, "confidence": "high"},
], "nothing_important_missed": True}, ensure_ascii=False)
s2 = consolidate(owner_id=OWNER, store=store, session_turns=TURNS, llm_call=lambda p: RAW2)
check("second pass dedupes against pending", s2["deduped"], 1)
occ2 = store.db.execute(
    "SELECT occurrences FROM memory WHERE owner_id=? AND content=?",
    (OWNER, "user को चाय पसंद है")).fetchone()[0]
check("pending occurrences NOT bumped by re-proposal", occ2, 1)

print("\n" + ("ALL PASS" if fails == 0 else f"{fails} FAILURE(S)"))
sys.exit(1 if fails else 0)

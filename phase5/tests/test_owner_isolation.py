#!/usr/bin/env python3
"""ACCEPTANCE 6 — cross-owner data cannot enter the pass.

The pass runs keyed to the session owner only. Owner A's pass must not see
owner B's rows as duplicates (A gets its own pending row for the same
content), must never write under B, and B's rows stay untouched (no
occurrence bumps, no reads visible to A's view).

Locked in docs/SESSION_END_CONSOLIDATION_V1.md §3, §10 test 6.
Run: python3 phase5/tests/test_owner_isolation.py
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
A, B = "owner-a", "owner-b"

# B committed this fact in B's session
store.commit(B, {"type": "preference", "content": "user को चाय पसंद है",
                 "criterion": "explicit"}, immediate=True)

RAW = json.dumps({"bullets": [
    {"type": "preference", "content": "user को चाय पसंद है", "turn_ref": 1, "confidence": "high"},
], "nothing_important_missed": True}, ensure_ascii=False)

s = consolidate(owner_id=A, store=store, session_turns=[(1, "मुझे चाय पसंद है")], llm_call=lambda p: RAW)
check("pass ran ok", s["status"], "ok")
check("A pending=1 (B's row invisible as dupe)", s["pending"], 1)

rows = store.db.execute(
    "SELECT owner_id, status FROM memory WHERE content=?", ("user को चाय पसंद है",)).fetchall()
check("A pending + B committed, isolated", sorted(rows), [("owner-a", "pending"), ("owner-b", "committed")])
b_occ = store.db.execute(
    "SELECT occurrences FROM memory WHERE owner_id=? AND content=?",
    (B, "user को चाय पसंद है")).fetchone()[0]
check("B occurrences untouched by A's pass", b_occ, 1)
a_rows = store.db.execute("SELECT owner_id FROM memory WHERE owner_id=?", (A,)).fetchall()
check("A's rows all under A", all(r[0] == A for r in a_rows) and len(a_rows) == 1, True)
check("A and B views disjoint (no cross-read)",
      set(store.view(A)) & set(store.view(B)), set())

print("\n" + ("ALL PASS" if fails == 0 else f"{fails} FAILURE(S)"))
sys.exit(1 if fails else 0)

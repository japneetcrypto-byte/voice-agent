#!/usr/bin/env python3
"""Regression: memory promotion guard - the गए bug (session 182736).

Owner called this the critical one: a verb ('गए' = 'went') was extracted as a
person AND committed LIVE mid-session because criterion='explicit'
short-circuits MemoryStore.commit's pending branch, defeating the
pending-until-confirmed guard.

THE INVARIANT under test:
  first sighting of a relationship  -> status='pending', NOT in view()
  second sighting (a real fact)     -> committed, visible in view()
  a one-off garble                  -> stays pending, never pollutes context

Run: python3 phase5/tests/test_memory_promotion_guard.py"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agent.memory_store import MemoryStore

fails = 0
def check(name, cond, extra=""):
    global fails
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {extra}")
    if not cond:
        fails += 1

db = "logs/test_promotion_guard.db"
if os.path.exists(db):
    os.remove(db)
store = MemoryStore(db_path=db)
OWNER = "4da66eb5-test"

# Simulate EXACTLY what _promote_relationship does after the bugfix:
def promote(content, already):
    store.commit(OWNER,
        {"type": "relationship", "content": content,
         "criterion": ("explicit" if already else "salient")},
        immediate=bool(already))

# --- the incident: 'गए — user's bhai' (a garbled verb) ---
promote("गए — user's bhai", already=False)   # first sighting
view = store.view(OWNER)
check("garble first sighting is PENDING (not in live view)",
      not any("गए" in v for v in view), str(view))
row = store.db.execute("SELECT status FROM memory WHERE owner_id=? AND content LIKE '%गए%'",
                       (OWNER,)).fetchone()
check("garble row exists as QUARANTINED (store-level guard)",
      row is not None and row[0] == "quarantined", str(row))

# --- a REAL fact, repeated (the promotion path) ---
promote("Neetu — user's behen", already=False)     # first sighting
check("real fact first sighting also pending",
      not any("Neetu" in v for v in store.view(OWNER)))
promote("Neetu — user's behen", already=True)      # owner repeats -> confirmed
check("real fact repeated -> COMMITTED (in view)",
      any("Neetu" in v for v in store.view(OWNER)))

# --- session-end promotion: quarantined rows are NEVER promoted ---
store.promote_pending(OWNER, keep=True)
check("session-end does NOT promote quarantined rows",
      not any("गए" in v for v in store.view(OWNER)))

# --- the OLD buggy path is now IMPOSSIBLE at store level ---
store2 = MemoryStore(db_path=db + ".old")
store2.commit(OWNER, {"type": "relationship", "content": "गए — user's bhai",
                      "criterion": "explicit"}, immediate=False)
check("OLD BUGGY PATH now quarantined (was instant-commit pre-gate)",
      not any("गए" in v for v in store2.view(OWNER)))
row2 = store2.db.execute("SELECT status FROM memory WHERE content LIKE '%गए%'").fetchone()
check("old-path row lands as quarantined for audit",
      row2 is not None and row2[0] == "quarantined", str(row2))

os.remove(db)
if os.path.exists(db + ".old"):
    os.remove(db + ".old")
print(f"\n{'ALL PASS' if fails == 0 else f'{fails} FAILURES'}")
sys.exit(1 if fails else 0)

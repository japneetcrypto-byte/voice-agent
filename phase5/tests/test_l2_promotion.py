#!/usr/bin/env python3
"""Regression: L2 → L3 PROMOTION (memory continuity slice #1, owner
2026-08-31: "L2 currently dies on clean shutdown, so cross-session
continuity is broken").

Current behavior (the bug): Layer-2 compressed people/facts live only in
LayeredContextManager.layer2 and the crash checkpoint, which is DISCARDED
at clean shutdown — so anything learned by compression (names, relations)
dies with the session and never reaches the SQLite memory store.

Expected behavior: on every successful compression, a deterministic diff
(promotable_people) extracts L2 people that have a relation and are not yet
committed, and routes them through _promote_memory (pending-first; a repeat
sighting confirms -> committed). The rows land in SQLite during the session,
so they survive restart.

Run: python3 phase5/tests/test_l2_promotion.py
"""
import sys, os, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agent.layered_context import promotable_people
from agent.memory_store import MemoryStore

fails = 0
def check(label, got, want):
    global fails
    ok = got == want
    if not ok:
        fails += 1
        print(f"  ✗ {label}: got {got!r}, want {want!r}")
    else:
        print(f"  ✓ {label}")

print("== promotable_people: string-value schema {name: relation} (compression prompt) ==")
st = {"people": {"Neetu": "behen", "Rohan": "dost", "no_relation_guy": ""},
      "active_topic": "family"}
got = promotable_people(st, [])
check("people with relations extracted", got, [("Neetu", "behen"), ("Rohan", "dost")])

print("== promotable_people: dict-value schema {name: {name, relation, source}} (design init) ==")
st2 = {"people": {"neetu": {"name": "Neetu", "relation": "behen", "source": "explicit"},
                  "x": {"name": "X", "relation": None},
                  "y": {"name": "Y"}}}
got = promotable_people(st2, [])
check("dict-value relations extracted", got, [("Neetu", "behen")])

print("== promotable_people: dedupe against already-committed memory ==")
existing = ["relationship: Neetu — user's behen"]
got = promotable_people(st, existing)
check("already-committed person skipped", got, [("Rohan", "dost")])

print("== promotable_people: empty/None state is safe ==")
check("None state", promotable_people(None, []), [])
check("empty state", promotable_people({}, []), [])
check("people missing", promotable_people({"active_topic": "x"}, []), [])

print("== integration: compression-time promotion survives 'shutdown' (repeat confirms) ==")
tmp = tempfile.mkdtemp()
store = MemoryStore(os.path.join(tmp, "m.db"))
owner = "owner-l2"
# First compression produces the person (mimics _compress_layer2 -> _promote_memory)
for name, rel in promotable_people(st, store.view(owner)):
    content = f"{name} — user's {rel}"
    already = any(content in line for line in store.view(owner))
    store.commit(owner, {"type": "relationship", "content": content,
                         "criterion": ("explicit" if already else "salient")},
                 immediate=bool(already))
check("first sighting pending (not yet in view)", any("Neetu" in v for v in store.view(owner)), False)
# Second compression repeats the person -> occurrences bump (still pending)
for name, rel in promotable_people(st, store.view(owner)):
    content = f"{name} — user's {rel}"
    already = any(content in line for line in store.view(owner))
    store.commit(owner, {"type": "relationship", "content": content,
                         "criterion": ("explicit" if already else "salient")},
                 immediate=bool(already))
    break  # simulate one repeat sighting
check("repeat sighting still pending mid-session", any("Neetu" in v for v in store.view(owner)), False)
# SESSION END: occurrences>=2 -> committed (the designed confirm point)
store.promote_pending(owner, keep=True)
check("session-end confirm commits to view", any("Neetu" in v for v in store.view(owner)), True)
check("Rohan (single sighting) stays pending", any("Rohan" in v for v in store.view(owner)), False)

print()
if fails:
    print(f"FAIL ({fails})")
    sys.exit(1)
print("ALL PASS")

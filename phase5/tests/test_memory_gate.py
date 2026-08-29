#!/usr/bin/env python3
"""Regression + fuzz: MemoryGate — the blast-radius containment invariant.

Owner directive: 'proper guardrails; blast radius contained; shall not fail
in extreme situations.'

THE INVARIANT UNDER TEST: no single upstream bug (bad extractor, bad policy,
bad caller) can put garbage into COMMITTED memory in one sighting. Worst case
is a quarantined/pending row that view() never serves.

Run: python3 phase5/tests/test_memory_gate.py"""
import sys, os, random
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agent.memory_store import MemoryStore
from agent.memory_gate import gate_candidate

fails = 0
def check(name, cond, extra=""):
    global fails
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {extra}")
    if not cond:
        fails += 1

db = "logs/test_memory_gate.db"
if os.path.exists(db):
    os.remove(db)
store = MemoryStore(db_path=db)
OWNER = "4da66eb5-gate-test"

# ---- adversarial garbage: verbs, stopwords, junk, edge cases ----
garbage = [
    {"type": "relationship", "content": "गए — user's bhai", "criterion": "explicit"},      # the live incident
    {"type": "relationship", "content": "गया — user's beta", "criterion": "explicit"},
    {"type": "relationship", "content": "कर — user's manager", "criterion": "explicit"},
    {"type": "relationship", "content": "hai — user's dost", "criterion": "salient"},
    {"type": "relationship", "content": "x — user's behen", "criterion": "explicit"},       # 1-char name
    {"type": "relationship", "content": "123 — user's beta", "criterion": "explicit"},      # digits
    {"type": "semantic", "content": "ab", "criterion": "explicit"},                          # degenerate
    {"type": "semantic", "content": "!!", "criterion": "salient"},
    {"type": "episodic", "content": "क", "criterion": "explicit"},
]

committed_leaks = 0
quarantined = rejected = 0
for g in garbage:
    before = store.view(OWNER)
    store.commit(OWNER, dict(g, immediate=True))   # attacker uses max privileges
    after = store.view(OWNER)
    if after != before:
        committed_leaks += 1
    v, _ = gate_candidate(dict(g, immediate=True))
    if v == "quarantine":
        quarantined += 1
    if v == "reject":
        rejected += 1

check(f"no garbage leaked into COMMITTED memory ({committed_leaks} leaks)", committed_leaks == 0)
check(f"garbage quarantined/rejected ({quarantined}Q/{rejected}R of {len(garbage)})",
      quarantined + rejected == len(garbage))
check("view() serves zero garbage rows", len(store.view(OWNER)) == 0)
q = store.db.execute("SELECT COUNT(*) FROM memory WHERE status='quarantined'").fetchone()[0]
check(f"quarantined rows kept for audit ({q})", q >= len(garbage) - rejected)

# ---- the promotion path is ALSO guarded (session-end blanket commit was the
# second half of the गए bug) ----
store2 = MemoryStore(db_path=db + "2")
store2.commit(OWNER, {"type": "relationship", "content": "गए — user's bhai",
                      "criterion": "salient"})   # would have been pending pre-gate; now quarantined
store2.commit(OWNER, {"type": "relationship", "content": "गए — user's bhai",
                      "criterion": "salient"})
store2.promote_pending(OWNER, keep=True)
check("even pre-gate-style repeats can't commit through quarantined rows",
      not any("गए" in v for v in store2.view(OWNER)))

# ---- legitimate flow still works: first pending, repeat commits ----
store.commit(OWNER, {"type": "relationship", "content": "Neetu — user's behen",
                     "criterion": "salient"})
check("valid first sighting -> pending (invisible)",
      not any("Neetu" in v for v in store.view(OWNER)))
store.commit(OWNER, {"type": "relationship", "content": "Neetu — user's behen",
                     "criterion": "explicit"}, immediate=True)
check("valid repeat -> committed (visible)",
      any("Neetu" in v for v in store.view(OWNER)))

# ---- guarded session-end: single-occurrence pending STAYS pending ----
store.commit(OWNER, {"type": "semantic", "content": "user prefers window seat",
                     "criterion": "salient"})
store.promote_pending(OWNER, keep=True)
check("session-end does NOT blanket-commit 1x pending",
      not any("window seat" in v for v in store.view(OWNER)))
row = store.db.execute("SELECT occurrences FROM memory WHERE content='user prefers window seat'").fetchone()
check("1x pending row retained for future confirmation", row is not None and row[0] == 1)

# ---- determinism: same inputs, same verdicts ----
v1 = [gate_candidate(dict(g, immediate=True)) for g in garbage]
v2 = [gate_candidate(dict(g, immediate=True)) for g in garbage]
check("gate deterministic", v1 == v2)

os.remove(db)
if os.path.exists(db + "2"):
    os.remove(db + "2")
print(f"\n{'ALL PASS' if fails == 0 else f'{fails} FAILURES'}")
sys.exit(1 if fails else 0)

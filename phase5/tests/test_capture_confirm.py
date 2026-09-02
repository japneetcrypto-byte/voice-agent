#!/usr/bin/env python3
"""Capture design v2 — topic-blind disclosure capture (owner 2026-09-02).
Design lock: docs/CAPTURE_CONFIRM_DESIGN_LOCK.md.

RED tests — pin the semantics; do NOT implement the chain until the owner
approves the design note. The layer-2 frame detector does not exist yet
(ImportError/AttributeError = RED).

Run: python3 phase5/tests/test_capture_confirm.py
"""
import sys, os, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

fails = 0
def check(label, got, want):
    global fails
    ok = got == want
    if not ok:
        fails += 1
        print(f"  ✗ {label}: got {got!r}, want {want!r}")
    else:
        print(f"  ✓ {label}")

print("== LAYER 2: topic-blind frame detector catches EVERY topic (8/8 probe) ==")
try:
    from agent.entity_extractor import extract_disclosure_frames as edf
except Exception as e:
    print(f"  ✗ extract_disclosure_frames not implemented yet (RED): {e}")
    fails += 1
    edf = None

PROBE = [
 ("मैं कानपुर घुमने जाने वाला हूँ", True),        # travel/plan
 ("अगले हफ्ते मेरा interview है", True),           # event/plan
 ("मुझे कॉफ़ी पसंद है", True),                     # preference
 ("मैं शाकाहारी हूँ", True),                       # diet
 ("मेरी बहन दिल्ली में रहती है", True),            # family/place
 ("मैंने एक नया काम शुरू किया है", True),          # work
 ("मुझे अखबार पढ़ना पसंद है", True),               # hobby
 ("मैं अगले महीने पुणे shift हो रहा हूँ", True),   # move/plan
]
if edf:
    for t, want in PROBE:
        got = len(edf(t)) >= 1
        check(f"frame catches: {t[:40]!r}", got, want)

print("== LAYER 2 negatives: never captured ==")
NEG = [
 ("तू कहाँ जाएगा", "question to agent"),
 ("राहुल कानपुर जाने वाला है", "third person"),
 ("आप बहुत अच्छे हो", "agent-directed"),
 ("बहुत बुरा दिन था, सब गलत हो गया", "venting"),
 ("तुने क्या लिखा हुआ है अभी", "rail turn"),
 ("हाँ", "confirm"),
 ("मैं पागल", "no durable frame"),
]
if edf:
    for t, why in NEG:
        got = len(edf(t)) == 0
        check(f"negative [{why}]: {t[:36]!r}", got, True)

print("== MEMORY: confirmation-gated commit; no silent auto-write ==")
from agent.memory_store import MemoryStore
class Sess:
    owner_id = "capconf_owner"
    def memory_view(self): return []
tmp = tempfile.mkdtemp()
dbp = os.path.join(tmp, "mem.db")
store = MemoryStore(dbp)
sess = Sess()
owner = sess.owner_id
DISC = "मैं कानपुर घुमने जाने वाला हूँ"
cands = edf(DISC) if edf else []
check("LIVE disclosure yields a frame candidate", len(cands) >= 1, True)
if cands:
    # fresh disclosure parks PENDING (never auto-committed)
    store.commit(owner, {"type": "episodic", "content": cands[0]["content"],
                         "criterion": "explicit"}, immediate=False)
    statuses = [r[0] for r in store.db.execute("SELECT status FROM memory WHERE owner_id=?", (owner,))]
    check("fact parked (pending), not auto-committed",
          "pending" in statuses and "committed" not in statuses, True)
    check("pending fact NOT in live memory view", store.view(owner) == [], True)
    # user confirms -> committed -> in view
    store.commit(owner, {"type": "episodic", "content": cands[0]["content"],
                         "criterion": "explicit"}, immediate=True)
    statuses = [r[0] for r in store.db.execute("SELECT status FROM memory WHERE owner_id=?", (owner,))]
    check("confirm commits the fact", "committed" in statuses, True)

print("== BACKSTOP + EXPIRY-ARCHIVE semantics (store-level) ==")
# pending rows must be able to leave the ACTIVE view while the row survives
store.commit(owner, {"type": "episodic", "content": "old pending fact",
                     "criterion": "explicit"}, immediate=False)
store.commit(owner, {"type": "episodic", "content": "old pending fact",
                     "criterion": "explicit"}, immediate=False)
n_pending = store.db.execute("SELECT COUNT(*) FROM memory WHERE owner_id=? AND status='pending'", (owner,)).fetchone()[0]
check("multiple sightings stay ONE pending row (deduped)", n_pending <= 1, True)
cols = [r[1] for r in store.db.execute("PRAGMA table_info(memory)")]
check("memory rows carry created_at/last_seen/sessions (expiry inputs)",
      all(c in cols for c in ("created_at", "last_seen", "sessions")), True)

print()
if fails:
    print(f"FAIL ({fails}) — features not yet implemented (expected RED)")
    sys.exit(1)
print("ALL PASS")

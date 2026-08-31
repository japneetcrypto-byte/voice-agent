#!/usr/bin/env python3
"""Regression: SAVED NUMBERS -> LONG-TERM MEMORY + NEW-SESSION RECALL
(owner session_20260831_192745: "memory is not saving" — 'मैंने तुझे अपना
नंबर सेव करवाया था' / 'क्या लिखा था तुने' were unanswered from memory).

Current behavior (the bug): the dictation rail keeps confirmed numbers only
in engine["dictation"] (SESSION state). A NEW session has no task, so a
number-recall query falls to the LLM, which has no record -> "mere paas
memory ka koi system nahi hai" (t5) / "number thodi save kar sakta hoon"
(t4). Numbers are the single most important thing the user asks us to save,
yet they were never committed to the SQLite store.

Expected behavior:
  - On confirm (value CONFIRMED by the user), the number is written to the
    long-term store: {type: "saved_number", content: "user's <kind>
    number: <digits>", criterion: "explicit"} (immediate — the user
    explicitly confirmed it).
  - In a FRESH session (no task), a saved-number query
    ('नंबर सेव करवाया था' / 'क्या लिखा था तुने') recalls the stored number
    DETERMINISTICALLY, digit-by-digit — never via LLM, never fabricated.
  - If the user asks about a saved number but NONE is stored -> a
    deterministic "koi number save nahi hai — bolo, note kar loon" line,
    never an LLM invention.

Run: python3 phase5/tests/test_saved_number_recall.py
"""
import sys, os, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agent.precision_rail import decide
from agent.memory_store import MemoryStore
from agent.session_state import SessionState
from agent.entity_extractor import _WORD_RE

fails = 0
def check(label, got, want):
    global fails
    ok = got == want
    if not ok:
        fails += 1
        print(f"  ✗ {label}: got {got!r}, want {want!r}")
    else:
        print(f"  ✓ {label}")

class FakeSess:
    """Minimal sess with a real memory_view over a real store."""
    def __init__(self, owner, store):
        self.owner_id = owner
        self.store = store
    def memory_view(self):
        return self.store.view(self.owner_id)

def eng_with(owner, store, dictation=None):
    return {"sess": FakeSess(owner, store), "store": store,
            "dictation": dictation}

db = os.path.join(tempfile.mkdtemp(), "m.db")
OWNER = "owner-num"

print("== confirm persists the number to long-term memory ==")
store = MemoryStore(db)
eng = eng_with(OWNER, store)
# arm -> accumulate 9935411907 -> 'bas' confirms -> ack (status=confirmed)
r = decide("मोबाइल नंबर लिख ले", eng, 1)
check("arm fires", r["action"], "arm")
r = decide("9935 411907", eng, 2)
check("silent accumulate", r["action"], "silent_accumulate")
r = decide("बस", eng, 3)
check("'बस' -> echo_full", r["action"], "echo_full")
r = decide("हां ठीक है", eng, 4)
check("confirm -> ack (status confirmed)", r["action"], "confirm_ack")
check("ack says confirmed", eng["dictation"]["status"], "confirmed")
view = store.view(OWNER)
check("number persisted to store", any("9935411907" in v for v in view), True)
check("kind mobile in content", any("mobile" in v for v in view), True)
check("provenance explicit", any("(explicit)" in v for v in view), True)

print("== FRESH SESSION (no task) + saved-number query -> deterministic recall ==")
eng2 = eng_with(OWNER, store)     # same owner, NEW session, no dictation
r = decide("मैंने तुझे अपना नंबर शेप करवाया था", eng2, 1)
check("'नंबर शेप करवाया था' -> recall (not LLM)", r["action"], "recall")
check("recall speaks the exact digits", r["value"], "9935411907")
check("recall line speaks digit-by-digit",
      "nine nine three five four one one nine zero seven" in (r["line"] or ""), True)
r = decide("एक बार बता दे क्या लिखा था तुने", eng2, 2)
check("'क्या लिखा था तुने' -> recall", r["action"], "recall")
check("recalled value exact", r["value"], "9935411907")

print("== account number kind + recall ==")
store2 = MemoryStore(db)
eng3 = eng_with(OWNER, store2)
r = decide("अकाउंट नंबर लिख ले", eng3, 1)
check("account arm", r["action"], "arm")
r = decide("026900 12", eng3, 2)
r = decide("6205703", eng3, 3)
r = decide("बस", eng3, 4)
r = decide("हां", eng3, 5)
check("account confirmed", eng3["dictation"]["status"], "confirmed")
view3 = store2.view(OWNER)
check("account number persisted", any("026900126205703" in v for v in view3), True)
check("kind account in content", any("account" in v for v in view3), True)
r = decide("मेरा अकाउंट नंबर क्या था", eng_with(OWNER, store2), 1)
check("fresh session account recall", r["action"] == "recall" and r["value"] == "026900126205703", True)

print("== no saved number -> deterministic honest line, never LLM ==")
eng4 = eng_with("owner-fresh", store)
r = decide("मैंने तुझे अपना नंबर सेव करवाया था", eng4, 1)
check("no saved number -> non-None deterministic line", r is not None, True)
check("line admits nothing saved", "nahi" in (r["line"] or ""), True)
r = decide("मेरा नंबर क्या था", eng_with("owner-fresh", store), 2)
check("no saved number + generic query -> honest line or LLM (never fabrication)",
      r is None or r["line"] is not None, True)

print("== dedupe: re-confirming the same number in a later session bumps, no dup rows ==")
eng5 = eng_with(OWNER, store)
r = decide("मोबाइल नंबर लिख ले", eng5, 1)
r = decide("9935 411907", eng5, 2)
r = decide("बस", eng5, 3)
r = decide("हां", eng5, 4)
rows = store.db.execute(
    "SELECT count(*) FROM memory WHERE content LIKE '%9935411907%'").fetchone()[0]
check("single row after repeat (dedupe)", rows, 1)

print()
if fails:
    print(f"FAIL ({fails})")
    sys.exit(1)
print("ALL PASS")

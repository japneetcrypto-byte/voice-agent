#!/usr/bin/env python3
"""CRITICAL ACCEPTANCE — multi-session memory recall (owner 2026-08-31).

Session 1:  "मैं उत्तराखंड गया था, देहरादून और नैनीताल देखे।"
Session 2:  "मैंने कौन सी जगह बताई थी?"
Expected:   agent recalls the stored fact from memory, no fabrication,
            provenance clear, fact survives clean shutdown/restart.

Simulates two sessions sharing one SQLite store (the real restart path:
a fresh MemoryStore/SessionState on the same DB file == a new worker).
No LLM call in this test — it pins the WRITE->RESTART->READ chain and the
LLM payload, which is exactly what the live system feeds the model.

Run: python3 phase5/tests/test_multisession_recall.py
"""
import sys, os, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agent.memory_store import MemoryStore
from agent.session_state import SessionState
from agent.entity_extractor import extract_place_facts, extract_fact_candidates
from agent.fused_turn import FusedLLM

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
OWNER = "owner-accept"

# ---------------- SESSION 1 ----------------
print("== SESSION 1: user states the place fact ==")
s1 = SessionState(owner_id=OWNER, store=MemoryStore(db), log_dir=logdir)
facts = extract_place_facts("मैं उत्तराखंड गया था, देहरादून और नैनीताल देखे")
check("place fact extracted", len(facts) >= 1, True)
for f in facts:
    s1.store.commit(OWNER, f, immediate=True)   # explicit -> committed immediately
check("fact committed during session 1", any("उत्तराखंड" in v for v in s1.memory_view()), True)
# user also states identity + preference in session 1
for c in extract_fact_candidates("मेरा नाम राहुल है, और मुझे सलाह मत देना"):
    s1.store.commit(OWNER, c, immediate=True)
# CLEAN SHUTDOWN: end_session (promotes pending, records session)
s1.end_session()
print("   [session 1 clean shutdown]")

# ---------------- RESTART: new process, same DB ----------------
print("== RESTART (new MemoryStore on same DB file = new worker) ==")
s2 = SessionState(owner_id=OWNER, store=MemoryStore(db), log_dir=logdir)
check("SESSION BOUND memory count > 0 (fact survived restart)",
      len(s2.memory_view()) >= 2, True)

# ---------------- SESSION 2: recall query ----------------
print("== SESSION 2: 'मैंने कौन सी जगह बताई थी?' ==")
view = s2.memory_view()
check("Uttarakhand fact present in session-2 memory", any("उत्तराखंड" in v for v in view), True)
check("place names retained verbatim", any("देहरादून" in v and "नैनीताल" in v for v in view), True)
check("provenance explicit", any("(explicit)" in v for v in view), True)
check("name fact also survived", any("राहुल" in v for v in view), True)
check("no-advice preference also survived", any("no advice" in v for v in view), True)

# The LLM payload the model actually sees on the recall turn:
payload = FusedLLM().build_contents("मैंने कौन सी जगह बताई थी?", {"mode": "CALM"},
                                    view, [], [])
check("LLM payload carries the fact", "उत्तराखंड" in payload, True)
check("no memory_note (memory is present)", '"memory_note"' not in payload, True)
check("no fabrication signal: rule-14 pin present in prompt",
      "NEVER INVENT RECALL" in __import__("agent.prompt_fragments", fromlist=["PERSONA"]).PERSONA, True)

# ---------------- NEGATIVE CONTROL: a different owner (no memory) ----------------
print("== NEGATIVE CONTROL: different device/owner -> no fabrication ==")
s3 = SessionState(owner_id="owner-other", store=MemoryStore(db), log_dir=logdir)
check("other owner has no memory", len(s3.memory_view()), 0)
payload3 = FusedLLM().build_contents("मैंने कौन सी जगह बताई थी?", {"mode": "CALM"},
                                     s3.memory_view(), [], [])
check("empty-memory payload injects memory_note (honest-recall guard)",
      '"memory_note"' in payload3, True)

print()
if fails:
    print(f"FAIL ({fails})")
    sys.exit(1)
print("ALL PASS")

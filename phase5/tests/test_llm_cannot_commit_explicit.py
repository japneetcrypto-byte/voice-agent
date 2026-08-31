#!/usr/bin/env python3
"""ACCEPTANCE 1 — the LLM can NEVER produce/commit `explicit`.

Even when the LLM returns criterion/status keys (schema-injection attempt,
top-level or per-bullet), the consolidation pass hardcodes criterion="salient"
for every LLM bullet. The store row must be pending + salient, never
committed + explicit. Unknown keys are NOT silently accepted: any key outside
the whitelist rejects the whole pass (strict fail > partial trust).

Locked in docs/SESSION_END_CONSOLIDATION_V1.md §2, §4, §10 test 1.
Run: python3 phase5/tests/test_llm_cannot_commit_explicit.py
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
OWNER = "owner-b1"
TURNS = [(1, "मुझे क्रिकेट बहुत पसंद है")]

# --- case 1: LLM injects criterion/status at top level AND inside a bullet ---
RAW = json.dumps({
    "bullets": [{"type": "preference", "content": "user को क्रिकेट पसंद है",
                 "turn_ref": 1, "confidence": "high", "criterion": "explicit"}],
    "criterion": "explicit", "status": "committed",
    "nothing_important_missed": True}, ensure_ascii=False)

s = consolidate(owner_id=OWNER, store=store, session_turns=TURNS, llm_call=lambda p: RAW)
check("anchored=1", s["anchored"], 1)
check("pending=1", s["pending"], 1)
check("status ok", s["status"], "ok")
rows = store.db.execute(
    "SELECT status, criterion FROM memory WHERE owner_id=?", (OWNER,)).fetchall()
check("row is pending+salient, never committed+explicit", rows, [("pending", "salient")])
check("nothing_important_missed is telemetry only", s["nothing_missed"], True)

# --- case 2: unknown key inside a bullet -> whole pass parse-reject ---
RAW2 = json.dumps({"bullets": [{"type": "preference",
                                "content": "user को क्रिकेट पसंद है",
                                "turn_ref": 1, "confidence": "high",
                                "bogus": 1}],
                   "nothing_important_missed": True}, ensure_ascii=False)
n_before = store.db.execute("SELECT COUNT(*) FROM memory WHERE owner_id=?", (OWNER,)).fetchone()[0]
s2 = consolidate(owner_id=OWNER, store=store, session_turns=TURNS, llm_call=lambda p: RAW2)
check("unknown key -> whole pass failed", s2["status"], "failed")
check("unknown key reason = invalid_json", s2["reason"], "invalid_json")
n_after = store.db.execute("SELECT COUNT(*) FROM memory WHERE owner_id=?", (OWNER,)).fetchone()[0]
check("no rows added by rejected pass", n_after, n_before)

# --- case 3 (owner battle-test JSON): criterion/status at BOTH top level and
# bullet level, verbatim from the acceptance request -> pending ONLY,
# never committed, nothing_important_missed=False surfaced as telemetry ---
RAW3 = json.dumps({
    "bullets": [{"type": "semantic",
                 "content": "user का नाम राहुल है",
                 "turn_ref": 1, "confidence": "high",
                 "criterion": "explicit", "status": "committed"}],
    "criterion": "explicit", "status": "committed",
    "nothing_important_missed": False}, ensure_ascii=False)
s3 = consolidate(owner_id=OWNER, store=store,
                 session_turns=[(1, "मेरा नाम राहुल है")], llm_call=lambda p: RAW3)
check("battle JSON: anchored", s3["anchored"], 1)
check("battle JSON: pending only (never committed)", s3["pending"], 1)
rows3 = store.db.execute(
    "SELECT status, criterion FROM memory WHERE content=?",
    ("user का नाम राहुल है",)).fetchall()
check("battle JSON: row is pending+salient", rows3, [("pending", "salient")])
check("battle JSON: nothing_important_missed=False surfaced (telemetry)",
      s3["nothing_missed"], False)

# --- case 4: TOP-LEVEL unknown key (admin_override) -> whole pass rejected ---
RAW4 = json.dumps({"bullets": [{"type": "semantic",
                                "content": "user का नाम राहुल है",
                                "turn_ref": 1, "confidence": "high"}],
                   "nothing_important_missed": True,
                   "admin_override": True}, ensure_ascii=False)
n_before4 = store.db.execute("SELECT COUNT(*) FROM memory WHERE owner_id=?", (OWNER,)).fetchone()[0]
s4 = consolidate(owner_id=OWNER, store=store,
                 session_turns=[(1, "मेरा नाम राहुल है")], llm_call=lambda p: RAW4)
check("admin_override (top-level) -> whole pass failed", s4["status"], "failed")
check("admin_override reason = invalid_json", s4["reason"], "invalid_json")
n_after4 = store.db.execute("SELECT COUNT(*) FROM memory WHERE owner_id=?", (OWNER,)).fetchone()[0]
check("no rows added by admin_override pass", n_after4, n_before4)

print("\n" + ("ALL PASS" if fails == 0 else f"{fails} FAILURE(S)"))
sys.exit(1 if fails else 0)

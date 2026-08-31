#!/usr/bin/env python3
"""ACCEPTANCE 8 — completeness diff identifies deterministic captures omitted by the LLM.

D = this session's deterministic captures; B = bullet contents the LLM
proposed. The diff is read-only and must flag every capture the LLM did not
cover. `nothing_important_missed` is telemetry only: when the LLM claims
nothing was missed but the diff disagrees, a coverage warning is logged and
nothing is mutated.

Locked in docs/SESSION_END_CONSOLIDATION_V1.md §8, §10 test 8.
Run: python3 phase5/tests/test_completeness_diff_detects_omissions.py
"""
import sys, os, tempfile, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agent.memory_store import MemoryStore
from agent.session_consolidation import consolidate, completeness_diff

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
OWNER = "owner-b8"

CAPTURES = [
    "user: मैं उत्तराखंड गया था, देहरादून और नैनीताल देखे",
    "user को चाय पसंद है",
]
TURNS = [(1, "मैं उत्तराखंड गया था और मुझे चाय पसंद है")]
RAW = json.dumps({"bullets": [
    {"type": "preference", "content": "user को चाय पसंद है", "turn_ref": 1, "confidence": "high"},
], "nothing_important_missed": True}, ensure_ascii=False)

logs = []
def logger(msg):
    logs.append(msg)

s = consolidate(owner_id=OWNER, store=store, session_turns=TURNS, captures=CAPTURES,
                llm_call=lambda p: RAW, log=logger)
check("diff flags the omitted Uttarakhand capture",
      any("उत्तराखंड" in c for c in s["diff"]["not_covered"]), True)
check("diff covers the proposed chai capture",
      any("चाय" in c for c in s["diff"]["covered"]), True)
check("coverage warning logged (telemetry, no authority)",
      any("coverage warning" in m for m in logs), True)
check("nothing_missed surfaced as telemetry", s["nothing_missed"], True)
check("status ok (diff never blocks the pass)", s["status"], "ok")

# unit: pure completeness_diff
d = completeness_diff(CAPTURES, ["user को चाय पसंद है"])
check("unit not_covered length", len(d["not_covered"]), 1)
check("unit not_covered content", d["not_covered"], ["user: मैं उत्तराखंड गया था, देहरादून और नैनीताल देखे"])
check("unit covered content", d["covered"], ["user को चाय पसंद है"])

print("\n" + ("ALL PASS" if fails == 0 else f"{fails} FAILURE(S)"))
sys.exit(1 if fails else 0)

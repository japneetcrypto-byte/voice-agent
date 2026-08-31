#!/usr/bin/env python3
"""ACCEPTANCE 4 — numbers/PII never reach the consolidation prompt.

Digit runs (ASCII and Devanagari), including grouped/space-separated spellings
(phone-style), are replaced with [REDACTED] before the prompt is built. The
consolidation prompt must contain no digit run >= 4 (either script) and never
a raw dictated number.

Locked in docs/SESSION_END_CONSOLIDATION_V1.md §3, §10 test 4.
Run: python3 phase5/tests/test_pii_never_enters_prompt.py
"""
import sys, os, tempfile, re, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agent.memory_store import MemoryStore
from agent.session_consolidation import consolidate, redact_pii

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
OWNER = "owner-b4"

captured = {}
def capt(prompt):
    captured["p"] = prompt
    return '{"bullets": [], "nothing_important_missed": true}'

TURNS = [
    (1, "मेरा मोबाइल नंबर 9935411907 है"),
    (2, "अकाउंट नंबर ०२६९००१२६२ ०५७०३ है"),
    (3, "नंबर group karke bataun: 9935 4119 07"),
]
s = consolidate(owner_id=OWNER, store=store, session_turns=TURNS, llm_call=capt)
p = captured["p"]
check("pass ran ok", s["status"], "ok")
check("no ascii digit run >=4 in prompt", re.search(r"[0-9]{4,}", p) is None, True)
check("no devanagari digit run >=4 in prompt", re.search(r"[\u0966-\u096F]{4,}", p) is None, True)
check("[REDACTED] markers present", "[REDACTED]" in p, True)
check("9935 absent", "9935" not in p, True)
check("0269 absent", "0269" not in p, True)

# unit-level redaction
check("redact contiguous number", redact_pii("9935411907"), "[REDACTED]")
check("redact spaced number", redact_pii("9935 4119 07"), "[REDACTED]")
check("redact list digits", redact_pii("5, 7, 0, 3"), "[REDACTED]")
check("redact devanagari digits", redact_pii("०२६९००१२६२"), "[REDACTED]")
check("keep small age digits", redact_pii("मेरी उम्र 25 है"), "मेरी उम्र 25 है")
check("keep prose unchanged", redact_pii("मुझे चाय पसंद है"), "मुझे चाय पसंद है")

print("\n" + ("ALL PASS" if fails == 0 else f"{fails} FAILURE(S)"))
sys.exit(1 if fails else 0)

#!/usr/bin/env python3
"""Regression: DETERMINISTIC WRITE PATH for explicit facts + preferences
(memory continuity slice #2, owner 2026-08-31: "name, job, 'no advice',
etc. Use the same pending -> confirm -> commit pattern. No LLM free-form
memory writes").

Current behavior (the bug): only family relationships and place/travel
facts reach the memory store. Name, job, likes and "no advice" statements
are never captured, so the LLM has no record to recall.

Expected behavior: extract_fact_candidates() deterministically captures
explicit first-person statements -> {type: semantic|preference,
content, criterion: "explicit"}. Explicit candidates COMMIT immediately
(per STATE_MODEL §4.5 "immediate for explicit statements"); inferred
candidates stay pending -> confirm. view() exposes provenance.

Run: python3 phase5/tests/test_fact_candidates.py
"""
import sys, os, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agent.entity_extractor import extract_fact_candidates, extract_place_facts
from agent.memory_gate import gate_candidate
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

print("== extract_fact_candidates: NAME ==")
r = extract_fact_candidates("मेरा नाम राहुल है")
check("devanagari name captured", any("राहुल" in c["content"] for c in r), True)
check("type semantic", r[0]["type"], "semantic")
check("criterion explicit", r[0]["criterion"], "explicit")
check("roman name captured", any("rahul" in c["content"].lower()
      for c in extract_fact_candidates("mera naam Rahul hai")), True)
check("english name captured", any("Rahul" in c["content"]
      for c in extract_fact_candidates("my name is Rahul")), True)
check("question 'मेरा नाम क्या है' -> none",
      extract_fact_candidates("मेरा नाम क्या है"), [])

print("== extract_fact_candidates: JOB (allowlist only) ==")
check("devanagari job captured", any("इंजीनियर" in c["content"]
      for c in extract_fact_candidates("मैं एक इंजीनियर हूं")), True)
check("roman job captured", any("engineer" in c["content"]
      for c in extract_fact_candidates("main engineer hoon")), True)
check("'मैं यहीं हूँ' -> none (not a job)", extract_fact_candidates("मैं यहीं हूँ"), [])
check("'मैं जा रहा हूँ' -> none", extract_fact_candidates("मैं जा रहा हूँ"), [])

print("== extract_fact_candidates: LIKES ==")
r = extract_fact_candidates("मुझे चाय पसंद है")
check("likes captured", any("चाय" in c["content"] for c in r), True)
check("likes type preference", r[0]["type"], "preference")
check("'मुझे वह पसंद है' (pronoun) -> none", extract_fact_candidates("मुझे वह पसंद है"), [])
check("'मुझे क्या पसंद है' (question) -> none", extract_fact_candidates("मुझे क्या पसंद है"), [])

print("== extract_fact_candidates: NO-ADVICE preference ==")
r = extract_fact_candidates("सलाह मत देना")
check("no-advice captured", any("no advice" in c["content"] for c in r), True)
r2 = extract_fact_candidates("advice mat dena mujhe")
check("roman no-advice captured", any("no advice" in c["content"] for c in r2), True)
check("'सलाह दो' (request, not preference) -> none",
      extract_fact_candidates("मुझे सलाह दो"), [])

print("== extract_fact_candidates: never fires on non-fact speech ==")
check("greeting -> none", extract_fact_candidates("हेलो क्या कर रहे हो"), [])
check("dictation -> none", extract_fact_candidates("026900 4 बार 0 4301"), [])
check("place fact is NOT a fact-candidate here (own extractor)",
      extract_fact_candidates("मैं उत्तराखंड गया था"), [])

print("== extract_place_facts: explicit criterion (acceptance #1) ==")
r = extract_place_facts("मैं उत्तराखंड गया था, देहरादून और नैनीताल देखे")
check("place fact criterion explicit", r[0]["criterion"], "explicit")

print("== gate: explicit -> commit; inferred -> pending ==")
v, _ = gate_candidate({"type": "episodic",
                       "content": "user: मैं उत्तराखंड गया था देहरादून",
                       "criterion": "explicit", "immediate": True})
check("explicit place fact -> commit (first sighting)", v, "commit")
v, _ = gate_candidate({"type": "semantic",
                       "content": "user's name is Rahul",
                       "criterion": "explicit", "immediate": True})
check("explicit fact -> commit", v, "commit")
v, _ = gate_candidate({"type": "relationship",
                       "content": "Neetu — user's behen",
                       "criterion": "salient"})
check("inferred relationship stays pending", v, "pending")

print("== store round-trip + provenance in view ==")
tmp = tempfile.mkdtemp()
store = MemoryStore(os.path.join(tmp, "m.db"))
owner = "owner-facts"
for c in extract_fact_candidates("मेरा नाम राहुल है, और मुझे सलाह मत देना"):
    store.commit(owner, c, immediate=True)
view = store.view(owner)
check("name fact visible", any("राहुल" in v for v in view), True)
check("no-advice visible", any("no advice" in v for v in view), True)
check("provenance (explicit) exposed", any("(explicit)" in v for v in view), True)

print()
if fails:
    print(f"FAIL ({fails})")
    sys.exit(1)
print("ALL PASS")

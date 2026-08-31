#!/usr/bin/env python3
"""Regression: PLACE/TRAVEL FACT CAPTURE + HONEST RECALL (owner smoke-13
follow-up: "agent is not able to retrieve from memory, is hallucinating —
it shared wrong places from Uttarakhand").

Root causes locked here:
  - WRITE PATH WAS DEAD: the compact perception head (m/c/s) hardcodes
    head["memory_candidates"] = [], so NOTHING the user says about places,
    trips, jobs, preferences is ever stored — only family relationships
    (deterministic extractor). Cross-session recall had nothing to retrieve.
  - NO HONEST-RECALL GUARD: the prompt told the LLM memory is "background
    you silently KNOW" but never told it to ADMIT when it has no record —
    so on a recall question it fabricated places.

Fixes:
  - agent/entity_extractor.py extract_place_facts(): deterministic,
    conservative travel/location clauses -> episodic memory candidates.
  - main.py: capture + promote them like relationships (pending-first,
    repeat-confirm), via the shared _promote_memory().
  - prompt_fragments.py rule 14: NEVER INVENT RECALL — say 'yaad nahi hai'
    when there is no record.
  - fused_turn.build_contents(): when memory_view is empty, inject an
    explicit memory_note so the LLM knows there is nothing to recall.

Run: python3 phase5/tests/test_place_facts.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agent.entity_extractor import extract_place_facts
from agent.memory_gate import gate_candidate
from agent.fused_turn import FusedLLM
from agent.prompt_fragments import PERSONA

fails = 0
def check(label, got, want):
    global fails
    ok = got == want
    if not ok:
        fails += 1
        print(f"  ✗ {label}: got {got!r}, want {want!r}")
    else:
        print(f"  ✓ {label}")

print("== extract_place_facts: DEVANAGARI travel/location clauses ===")
r = extract_place_facts("मैं उत्तराखंड गया था, देहरादून और नैनीताल देखे")
check("uttarakhand clause captured", len(r) >= 1, True)
check("capture is verbatim episodic", "उत्तराखंड गया" in r[0]["content"], True)
check("place names kept verbatim", "देहरादून" in r[0]["content"] and "नैनीताल" in r[0]["content"], True)
check("type episodic", r[0]["type"], "episodic")

r = extract_place_facts("पिछले हफ्ते रिशीकेश घूमने गया था")
check("rishikesh trip captured", any("रिशीकेश" in c["content"] for c in r), True)

r = extract_place_facts("मैं कानपुर से हूँ")
check("origin 'से हूँ' captured", any("कानपुर" in c["content"] for c in r), True)

r = extract_place_facts("मैं मुंबई में रहता हूं")
check("residence 'में रहता हूं' captured", any("मुंबई" in c["content"] for c in r), True)

print("== extract_place_facts: must NOT fire on non-place speech ===")
check("interjection 'हुआ है' -> none", extract_place_facts("हुआ है"), [])
check("conversational 'एक बार मैंने सोचा 9000' -> none",
      extract_place_facts("एक बार मैंने सोचा 9000"), [])
check("dictation '026900 4 बार 0 4301' -> none",
      extract_place_facts("026900 4 बार 0 4301"), [])
check("question to agent 'तुम कहां से हो' -> none",
      extract_place_facts("तुम कहां से हो"), [])
check("bare verb 'मैं गया था' (no place) -> none",
      extract_place_facts("मैं गया था"), [])

print("== extract_place_facts: ROMAN travel clauses ===")
r = extract_place_facts("I visited Rishikesh last year")
check("roman 'visited' captured", any("Rishikesh" in c["content"] for c in r), True)
r = extract_place_facts("I live in Kanpur")
check("roman 'live in' captured", any("Kanpur" in c["content"] for c in r), True)
check("roman 'hi hello' -> none", extract_place_facts("hi hello kya kar rahe ho"), [])

print("== memory gate: episodic facts follow pending-first / repeat-confirm ===")
v, why = gate_candidate({"type": "episodic",
                         "content": "user: मैं उत्तराखंड गया था देहरादून नैनीताल",
                         "criterion": "salient"})
check("first sighting -> pending", v, "pending")
v, why = gate_candidate({"type": "episodic",
                         "content": "user: मैं उत्तराखंड गया था देहरादून नैनीताल",
                         "criterion": "explicit", "immediate": True})
check("repeat/explicit -> commit", v, "commit")

print("== build_contents: empty memory_view injects the honest-recall note ==")
llm = FusedLLM()
payload = llm.build_contents("बताओ मैंने कौन सी जगह बताई थी", {"mode": "CALM"}, [], [], [])
check("memory_note present when memory empty", '"memory_note"' in payload, True)
check("note says never invent", "never invent" in payload or "NEVER invent" in payload, True)
payload2 = llm.build_contents("hello", {"mode": "CALM"}, ["fact: x"], [], [])
check("no note when memory has facts", '"memory_note"' not in payload2, True)

print("== prompt: rule 14 NEVER INVENT RECALL is pinned ==")
check("prompt forbids invented recall", "NEVER INVENT RECALL" in PERSONA, True)
check("prompt provides the honest fallback", "yaad nahi" in PERSONA, True)

print()
if fails:
    print(f"FAIL ({fails})")
    sys.exit(1)
print("ALL PASS")

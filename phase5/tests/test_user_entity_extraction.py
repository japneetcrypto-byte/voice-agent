#!/usr/bin/env python3
"""Deterministic regression: relationship facts stated by the USER (not by Aiva).

Evidence: session 20260829_083519 — user said 'नीतु बहन एक टीचर है...' and
'नीतु बेन के बारे में...' in-session; nothing was captured (capture only ran
on Aiva's replies), so Aiva claimed total ignorance of Neetu all session.
Run: python3 phase5/tests/test_user_entity_extraction.py"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agent.entity_extractor import extract_entities_from_user_text

cases = [
    # --- REAL utterances from session 20260829_083519 ---
    ("नीतु बहन एक टीचर है जो स्कूल में बच्चों को पढ़ाती है नीतु बहन स्कूल जाती है",
     [("Neetu", "behen")]),
    ("हाँ, नीतु बेन के बारे में जानना चाता हूँ।", [("Neetu", "behen")]),  # ben -> behen
    ("नीतु भाइनों के बारे में कुछ नहीं बताया आपको", [("Neetu", "behen")]),
    # --- roman + second orientation ---
    ("mera dost Rohan aaya tha", [("Rohan", "dost")]),
    ("meri behen Neetu school mein padhati hai", [("Neetu", "behen")]),
    # --- negatives: no name, third person, no relation ---
    ("उसकी बहन आई थी", []),                      # possessive, no name
    ("अच्छा नहीं तो बहन के पास", []),             # nameless relation
    ("मेरा नाम क्या है?", []),                    # no relation word
    ("नीतु, नीतु, नीतु", []),                     # name without relation
    ("आप तुम मुझे गगू बुलाते थे ना?", []),        # nickname recall, no relation
    # dedup within one utterance
    ("नीतु बहन है और नीतु बहन टीचर है", [("Neetu", "behen")]),
]

fails = 0
for text, expected in cases:
    got = [(e["name"], e["relation"]) for e in extract_entities_from_user_text(text)]
    ok = got == expected
    status = "PASS" if ok else "FAIL"
    if not ok:
        fails += 1
    print(f"[{status}] {text[:50]!r} -> {got}")

print(f"\n{'ALL PASS' if fails == 0 else f'{fails} FAILURES'}")
sys.exit(1 if fails else 0)

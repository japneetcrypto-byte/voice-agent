#!/usr/bin/env python3
"""Deterministic regression: conversation turn controller (owner brief 2026-08-27).
Run: python3 phase5/tests/test_turn_controller.py"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agent.turn_controller import decide

cases = [
    ("पहले पंद्रह दिन तो बोलता रहा है बहुत करवा देंगे...", True, "suppress"),
    ("आज करवा देंगे, कल करवा देंगे", False, "respond"),
    ("मामला ये है कि...", False, "suppress"),
    ("और फिर वो bola ki", True, "suppress"),
    ("toh maine socha ki", False, "suppress"),
    ("phir?", False, "respond"),
    ("mera manager ne phir weekend kaam diya", False, "respond"),
    ("...aur uske baad", False, "suppress"),
    ("bas aise hi", True, "suppress"),
    ("haan bol", True, "respond"),
    ("aur phir usne kya bola", False, "respond"),
    # --- session 133659 regression chain (the silence bug) ---
    ("क्या करता है ये", False, "respond"),      # t19: final pronoun != connector
    ("बोलो भाई", True, "respond"),              # t20: handoff word mid-utterance
    ("हेलो", True, "respond"),                  # t21: greeting while Aiva silent
    ("हाँ", True, "suppress"),                  # 1st short continuation still waits
    ("हाँ", 2, "respond"),                      # streak cap: never a 3rd wait
    ("अच्छा अच्छा और", False, "suppress"),      # t16: genuine trail-off
    ("aur uske baad", False, "suppress"),       # baad = trail-off
    # question-word rule (evidence session 182736 t6: suppressed question)
    ("कहां करें", True, "respond"),
    ("kya scene hai", True, "respond"),
]
ok = True
for text, was_wait, want in cases:
    action, reason = decide(text, was_wait)
    if ("suppress" if action == "suppress" else "respond") != want:
        ok = False
        print("FAIL:", text, action, reason)
print("TURN CONTROLLER TESTS", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)

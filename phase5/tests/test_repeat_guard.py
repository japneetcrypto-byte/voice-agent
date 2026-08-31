#!/usr/bin/env python3
"""Deterministic regression: near-repeat guard (owner: 'it is repeating',
2026-08-30) + clarify fall-through decision.
Run: python3 phase5/tests/test_repeat_guard.py"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agent.reply_guard import repeat_break_for, REPEAT_BREAK_LINES, is_repeat_of

fails = 0
def check(label, got, want):
    global fails
    ok = got == want
    if not ok:
        fails += 1
        print(f"  ✗ {label}: got {got!r}, want {want!r}")
    else:
        print(f"  ✓ {label}")

print("== verbatim repeat caught ==")
line, kind = repeat_break_for("arey main yahin hoon, bata kya chal raha hai?",
                              "arey main yahin hoon, bata kya chal raha hai?", "kuch nahi", 9)
check("verbatim -> guarded", kind == "verbatim" and line in REPEAT_BREAK_LINES, True)

print("== near-identical caught ==")
_, kind2 = repeat_break_for("haan main yahin hoon, tu bata kya chal raha hai",
                            "arey main yahin hoon, bata kya chal raha hai?", "kuch nahi", 10)
check("near_identical -> guarded", kind2 in ("verbatim", "extension", "near_identical"), True)

print("== legit repeat request NEVER guarded ==")
line3, kind3 = repeat_break_for("haan, maine bola tha ki kal aana",
                                "haan, maine bola tha ki kal aana",
                                "tumne kya bola tha bhai?", 11)
check("what-did-you-say -> not guarded", line3 is None, True)
line4, kind4 = repeat_break_for("kal 3 baje chalte hain", "kal 3 baje chalte hain",
                                "dobara bolo", 12)
check("dobara bolo -> not guarded", line4 is None, True)

print("== different replies never guarded ==")
line5, _ = repeat_break_for("haan, samajh gaya. aage bolo", "kal kya plan hai?", "kuch nahi", 1)
check("different reply -> not guarded", line5 is None, True)

print("== rotation deterministic + stays in pool ==")
lines = {repeat_break_for("same line baat", "same line baat", "x", i)[0] for i in range(9)}
check("substitutes stay in pool", lines <= set(REPEAT_BREAK_LINES), True)
check("substitutes rotate (variety)", len(lines) > 1, True)

print("== empty inputs safe ==")
check("no piece -> None", repeat_break_for("", "prev reply", "x", 1), (None, None))
check("no last reply -> None", repeat_break_for("piece here", "", "x", 1), (None, None))

print("== detection kinds regression ==")
check("is_repeat_of verbatim", is_repeat_of("hello bhai", ["hello bhai"]), (True, "verbatim"))
check("is_repeat_of different", is_repeat_of("hello bhai", ["kya scene hai"]), (False, ""))

print()
if fails:
    print(f"FAIL ({fails})")
    sys.exit(1)
print("ALL PASS")

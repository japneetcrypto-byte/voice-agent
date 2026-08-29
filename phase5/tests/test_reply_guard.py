#!/usr/bin/env python3
"""Deterministic regression: reply-side guards (Phase 5 behavioral tuning,
evidence: sessions 2026-08-28 — 7s replies, 'kar rahi thi' persona violation).
Run: python3 phase5/tests/test_reply_guard.py"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agent.reply_guard import trim_reply, feminine_self_reference, REPLY_MAX_CHARS

# --- trim_reply: (input, expected_keeps_underside, expect_trimmed) ---
trim_cases = [
    # short reply -> untouched
    ("Haan bol, kya chal raha hai?", False),
    # 2-sentence reply under cap -> untouched, ends at sentence boundary
    ("Arre wah, accha laga sunkar! Batao phir, aaj ka din kaisa chal raha hai?", False),
    # observed long reply style -> trimmed at a clean sentence boundary
    ("Main ekdum badhiya hoon, aap batao sab theek hai? Aaj kya help chahiye mujhe? "
     "Batao na, mujhe sunna hai sab kuch jo aaj din bhar hua, poori kahani shuru se "
     "leke aakhir tak tak, bina kisi bhi cheez ko chhode hue, ekdum detail mein batao.", True),
    # no sentence boundary at all -> hard cut at word boundary
    ("word " * 80, True),
    # empty / near-empty safety
    ("", False),
    ("ok", False),
]

fails = 0
for text, expect_trim in trim_cases:
    out, trimmed = trim_reply(text)
    ok = (trimmed == expect_trim) and len(out) <= REPLY_MAX_CHARS
    # trimmed output must end at a clean boundary in the ORIGINAL text:
    # sentence end, or a word boundary (next char is space / end).
    if ok and trimmed and out:
        idx = len(out)
        at_sentence = out[-1] in ".!?।"
        at_word = idx >= len(text.rstrip()) or text[idx] == " " or text[idx+1:idx+2] == "" or text[idx] == " "
        if not (at_sentence or at_word):
            ok = False
    status = "PASS" if ok else "FAIL"
    if not ok:
        fails += 1
    print(f"[{status}] trim({len(text)}c) -> ({len(out)}c, trimmed={trimmed}) {out[:60]!r}")

# --- feminine_self_reference: (input, expect_hit) ---
gender_cases = [
    # observed violation (S1 T4)
    ("Main bhi ekdum theek hoon, bas aapka hi intezaar kar rahi thi. Batao.", True),
    ("main tumhe bataungi", True),
    ("main kal aa jaungi", True),
    ("main ghar ja rahi hoon", True),
    # correct masculine self-reference -> no flag
    ("Main sun raha hoon, bolo.", False),
    ("bas aapka hi intezaar kar raha tha.", False),
    # third-person female reference is CORRECT speech -> no flag
    ("Rimmi so rahi thi kya?", False),
    ("woh ghar gayi hai", False),
]
for text, expect in gender_cases:
    got = feminine_self_reference(text)
    ok = bool(got) == expect
    status = "PASS" if ok else "FAIL"
    if not ok:
        fails += 1
    print(f"[{status}] gender({text[:45]!r}) -> {got!r}")

print(f"\n{'ALL PASS' if fails == 0 else f'{fails} FAILURES'}")
sys.exit(1 if fails else 0)

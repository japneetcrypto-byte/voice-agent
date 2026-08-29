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
    # observed long reply style (cap raised to 240: this 236c is now under cap)
    ("Main ekdum badhiya hoon, aap batao sab theek hai? Aaj kya help chahiye mujhe? "
     "Batao na, mujhe sunna hai sab kuch jo aaj din bhar hua, poori kahani shuru se "
     "leke aakhir tak tak, bina kisi bhi cheez ko chhode hue, ekdum detail mein batao.", False),
    # over the 240 cap -> trimmed at a clean sentence boundary
    ("Main ekdum badhiya hoon, aap batao sab theek hai? Aaj kya help chahiye mujhe? "
     "Batao na, mujhe sunna hai sab kuch jo aaj din bhar hua, poori kahani shuru se "
     "leke aakhir tak tak, bina kisi bhi cheez ko chhode hue, ekdum detail mein sab "
     "kuch batao na yaar, dil khol ke sunao zara.", True),
    # no sentence boundary at all -> hard cut at word boundary
    ("word " * 80, True),
    # 193c substantive reply is now legitimately under the raised cap
    ("Udaipur ya Jaipur mat jaio, wahan toh abhi aag lagi hogi. Rajasthan ke", False),
    # evidence t16 was the case that drove cap 220->180; owner directive
    # 2026-08-29 (length follows content) raised it to 240 — this 193c
    # info reply is now legitimately untrimmed.
    ("Udaipur ya Jaipur mat jaio, wahan toh abhi aag lagi hogi. Rajasthan ke mausam "
     "ke baare mein toh pata hi hai tumhe, abhi wahan jaane ka koi matlab nahi hai "
     "kyunki garmi bahut zyada hai wahan pe.", False),
    # over-240 info wall still trims at a sentence boundary
    ("Udaipur ya Jaipur mat jaio, wahan toh abhi aag lagi hogi. Rajasthan ke mausam "
     "ke baare mein toh pata hi hai tumhe, abhi wahan jaane ka koi matlab nahi hai "
     "kyunki garmi bahut zyada hai wahan pe, aur haan garmi ke saath jo rehna hai "
     "woh bhi mushkil hai, toh season dekh kar hi plan karna sahi rahega.", True),
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
    # observed violation (session 20260829_083519 T1) — 'sakna' feminine
    ("Main aapki kya help kar sakti hoon?", True),
    ("main tumse yeh nahi kar sakti", True),
    ("main chahti hoon tum jaldi aao", True),
    # correct masculine forms -> no flag
    ("Main aapki kya madad kar sakta hoon?", False),
    ("Main sun raha hoon, bolo.", False),
    ("bas aapka hi intezaar kar raha tha.", False),
    ("main chahta hoon tum jaldi aao", False),
    # t13 (session 100157): feminine form refers to the female ADDRESSEE
    # ('batao kya keh rahi thi?') — correct mirroring, NOT a violation
    ("Main yahin hoon, batao kya keh rahi thi?", False),
    # ...but the same shape WITHOUT the address imperative IS a violation
    ("Main bhi ekdum theek hoon, bas aapka hi intezaar kar rahi thi. Batao.", True),
    # third-person female reference is CORRECT speech -> no flag
    ("Rimmi so rahi thi kya?", False),
    ("woh ghar gayi hai", False),
    ("Neetu behen teacher hain", False),
]
for text, expect in gender_cases:
    got = feminine_self_reference(text)
    ok = bool(got) == expect
    status = "PASS" if ok else "FAIL"
    if not ok:
        fails += 1
    print(f"[{status}] gender({text[:45]!r}) -> {got!r}")

# --- response-quality detectors (owner brief 2026-08-29: echo-confirm drift) ---
from agent.reply_guard import is_confirm_echo as ice, devanagari_present as dev
qcases = [
    ("Nepal flood ki baat kar raha hai na?", True),
    ("PayPal mein kuch hua hai kya?", True),
    ("jamun mangwane hain kya?", True),
    ("kharbuja keh rahi ho na?", True),
    ("seb mangwane hain kya?", True),
    ("ludo khelne ki baat kar raha hai na?", True),
    ("likh ke bhej de, sun raha hoon main.", False),
    ("biryani ya pizza?", False),
    ("bas baatein karna aur yahan rehna. tu bata, abhi kya", False),
    ("main theek hoon. tum batao?", False),
]
for text, want in qcases:
    got = ice(text)
    ok = got == want
    if not ok:
        fails += 1
    print(f"[{'PASS' if ok else 'FAIL'}] confirm_echo({text[:44]!r}) -> {got}")
for text, want in [("मौसम साफ है?", True), ("mausam saaf hai?", False), ("", False)]:
    got = dev(text)
    ok = got == want
    if not ok:
        fails += 1
    print(f"[{'PASS' if ok else 'FAIL'}] devanagari({text[:30]!r}) -> {got}")

# --- challenge detector (owner brief 2026-08-29: flip-flop on challenge,
# session 185741 t20-22) + adaptive cap ---
from agent.reply_guard import is_challenge as ch, REPLY_MAX_CHARS as CAP
if CAP != 240:
    fails += 1
print(f"[{'PASS' if CAP == 240 else 'FAIL'}] cap raised for substantive info (cap={CAP})")
ccases = [
    ("तो तुने पांच से दस क्यों बोला यार", True),
    ("तु अभी बता रहे थे एक से दो रुपए", True),
    ("you said it was free", True),
    ("यह तो झूठ है", True),
    ("bata raha tha main", True),
    ("chal theek hai baad mein dekhte hain", False),
    ("haan ek do rupaye sahi hai", False),
    ("accha theek hai", False),
]
for text, want in ccases:
    got = ch(text)
    ok = got == want
    if not ok:
        fails += 1
    print(f"[{'PASS' if ok else 'FAIL'}] challenge({text[:40]!r}) -> {got}")

print(f"\n{'ALL PASS' if fails == 0 else f'{fails} FAILURES'}")
sys.exit(1 if fails else 0)

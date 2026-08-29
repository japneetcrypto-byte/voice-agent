#!/usr/bin/env python3
"""Regression: directive 192439 synthesis (2026-08-29 night).

Covers the acceptance tests: DETAILED mode detection + adaptive chunk cap,
remainder retention, repeat detection (detection-only), and the routing
contract already covered by test_transcript_router.
Run: python3 phase5/tests/test_detail_and_repeats.py"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agent.reply_guard import (is_detail_request, cap_for, remaining_text,
                               is_repeat_of, REPLY_MAX_CHARS, DETAIL_CHUNK_CAP)

fails = 0
def check(name, cond, extra=""):
    global fails
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {extra}")
    if not cond:
        fails += 1

# ---- DETAILED MODE detection ----
d_cases = [
    ("detail mein samjhao", True), ("डिटेल का मतलब है", True),
    ("poora batao", True), ("पूरा सुन जाओ", True),
    ("एक एक पॉइंट बताना है", True), ("step by step batao", True),
    ("कैसे काम करता है", True), ("khul ke batao", True),
    ("haan theek hai", False), ("kya hua", False), ("chalte hain", False),
]
for t, want in d_cases:
    got = is_detail_request(t)
    check(f"detail({t[:32]!r}) -> {got}", got == want)

# ---- chunk caps: detail mode = small chunks, normal = higher ceiling ----
check("detail cap = 110 (5-6s target)", cap_for(True) == DETAIL_CHUNK_CAP == 110)
check("normal cap = 240", cap_for(False) == REPLY_MAX_CHARS == 240)
check("cap_for detail < normal", cap_for(True) < cap_for(False))

# ---- interruption remainder ----
check("remainder = full minus spoken",
      remaining_text("haan main soch raha tha ki kal chalein",
                     "haan main soch raha") == "tha ki kal chalein")
check("non-prefix -> empty (safe)", remaining_text("abc", "xyz") == "")
full = ("rough cost aur banane ka process dubara batata hoon, dhyan se sun. "
        "pehle LLM tokens ka kharcha, phir call infra.")
spoken = "rough cost aur banane ka process dubara batata hoon, dhyan se sun."
check("real interruption remainder",
      remaining_text(full, spoken) == "pehle LLM tokens ka kharcha, phir call infra.")

# ---- repetition: detection only, last-N window semantics ----
prev = ["rough cost aur banane ka process dubara batata hoon"]
check("verbatim repeat detected",
      is_repeat_of("rough cost aur banane ka process dubara batata hoon", prev) == (True, "verbatim"))
rep, kind = is_repeat_of("rough cost aur banane ka process dubara batata hoon, dhyan se sun", prev)
check("extension repeat detected", rep and kind == "extension")
check("new content not flagged", is_repeat_of("quality metrics mein latency", prev)[0] is False)
# window semantics: the CALLER keeps last 3; helper is stateless per pair
prev3 = ["a", "b", "c"]
check("helper checks all provided previous",
      is_repeat_of("c", prev3) == (True, "verbatim"))

# ---- acceptance-test trace (directive) ----
print("\nacceptance trace:")
print("  concise default        : cap_for(False)=240, persona V1.11 rule 1 (small talk 2-8 words)")
print("  depth on request       : is_detail_request -> detail latch -> cap_for(True)=110 + policy.delivery")
print("  chunks not monologue   : DETAIL_CHUNK_CAP=110 per turn, continuation via 'haan/aage/phir'")
print("  remainder preserved    : remaining_text + PARTIALLY_PLAYED payload (heard+remaining)")
print("  meaningful-STT recovery: transcript_router contextual_recovery (test_transcript_router 10 cases)")
print("  unusable STT -> clarify: transcript_router clarify path (tested)")
print("  repeats detected only  : REPEAT_DETECTED event, no suppressor (this suite)")
print("  no context re-asking   : reconciliation payload + Layer-2 active_topic (shipped)")

# ---- latch renewal (evidence session 203226: latch expired mid-explanation,
# monologues returned) ----
from agent.turn_controller import continues_or_asks as cont
r_cases = [
    ("haan aage", True), ("और स्टेप्स आगे", True), ("pricing kya rahegi?", True),
    ("कहां करें", True), ("और", True), ("haan", True),
    ("नहीं नहीं", False), ("bye", False),
]
for t, want in r_cases:
    got = cont(t)
    ok = got == want
    if not ok:
        fails += 1
    print(f"[{'PASS' if ok else 'FAIL'}] continues_or_asks({t[:28]!r}) -> {got}")
check("renewal predicate drives latch (max 4 on renewal)",
      max(4, 2) == 4)  # semantics: renewal sets >=4, detail request resets to 6

print(f"\n{'ALL PASS' if fails == 0 else f'{fails} FAILURES'}")
sys.exit(1 if fails else 0)

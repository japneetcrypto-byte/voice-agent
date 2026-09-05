#!/usr/bin/env python3
"""Correction-repair package (owner-approved 2026-09-02, live session
20260902_184247) — tests-FIRST.

The live session showed four number-EDIT failure modes, each reproduced
byte-for-byte through decide() against the real stored values:
  t26 '1242 नहीं है भाईया'      -> reject-without-parse WIPED the whole value
  t27/28 '12 के बाद 4 बार 0'    -> parsed PERFECTLY but value was already gone
  t11  '...9000...900...बस...'  -> "बस" hijacked to CONFIRM (echo_full), no edit
  t40  digit-WORD correction    -> never parsed (stray 'एक'->'1' group), WIPED
Locked mechanics:
  M1 wipe-stop: a stored value is cleared ONLY on a plain whole-turn rejection
     (no digits, no digit-words, no change frame). Any edit-intent turn keeps
     the value so the follow-up correction can repair it.
  M2 anchor-less removal: '1242 नहीं है' with 1242 in the value -> remove+echo.
  M3 confirm-guard: a confirm word inside a change frame ('कम/जगह/बदल/चेंज')
     is NOT a confirm.
  M4 val-aware repair fallback: negations + change-frame pairs resolve against
     the stored value; digit-WORD corrections parse; stray single-digit
     conversational tokens ('एक' -> '1') never poison the parse.
Run: python3 phase5/tests/test_correction_repair.py
"""
import sys, os, re
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agent.precision_rail import decide as _decide_raw

# VALUE_TRANSACTION_LOCK (2026-09-04): decide() no longer commits an echo by
# itself — the PLAYBACK layer marks delivery (L2). These offline suites use the
# stand-in that marks every spoken decision as fully heard, i.e. the live
# behaviour when nothing is interrupted.
from agent.value_transaction import decide_heard as decide
from agent.precision_rail import speak_value

fails = 0
def check(label, got, want):
    global fails
    ok = got == want
    if not ok:
        fails += 1
        print(f"  ✗ {label}: got {got!r}, want {want!r}")
    else:
        print(f"  ✓ {label}")

def run_turn(turns, seed):
    """Run consecutive turns over one engine seeded with a stored value.
    Returns (decision, BASE value, status, PROPOSED value) per turn.
    VALUE_TRANSACTION_LOCK (2026-09-04): a repair is PROPOSED (echoed) and
    the base moves only on confirm — so 'the repaired value' is read from
    the proposal (or the decision) until a confirm turn commits it."""
    eng = {"dictation": {"value": seed, "status": "pending"}, "conv": {}}
    out = []
    for tn, t in turns:
        d = decide(t, eng, tn)
        dic = eng.get("dictation") or {}
        prop = dic.get("proposal") or {}
        out.append((d, dic.get("value", ""), dic.get("status", ""),
                    prop.get("derived") or dic.get("value", "")))
    return out

print("== M1+M2 (+L3, lock 2026-09-04): t26 removal-only fragment must NOT wipe — it HOLDS ==")
# Pre-lock pin: the lone removal '1242 नहीं है' was applied at once (value
# 02690001203) — the exact mechanism that produced the 103339 t8 damage. Under
# L3 a removal-only spec is the prefix of a replacement: the value is kept, the
# instruction is held open (spoken), nothing mutates. DECLARED PIN CHANGE.
out = run_turn([(26, "1242 नहीं है भाईया")], "026900124201203")
d, val, st, prop = out[0]
check("t26 action is a spoken HOLD (not retry-wipe, not an applied removal)", d["action"], "hold_edit")
check("t26 line names the rejected digits", "1242" in (d["line"] or ""), True)
check("t26 value survives untouched", val, "026900124201203")
check("t26 no proposal yet (nothing applied)", prop, "026900124201203")

print("== M1: wipe-stop cascade — the t26->t27 sequence now converges in ONE proposal ==")
out = run_turn([(26, "1242 नहीं है भाईया"),
                (27, "12 के बाद 4 नहीं है, 12 के बाद 4 बार 0 है")],
               "026900124201203")
d1, v1, _, _ = out[0]
d2, v2, s2, p2 = out[1]
check("t27 closes the held instruction into a repair echo", d2["action"], "echo_confirm")
check("t27 never sees an empty value", v2 != "", True)
check("t27 base still the original (L1: proposal until confirm)", v2, "026900124201203")
check("t27 proposes the joined instruction (1242 gone, 0000 after 12)", p2, "026900000001203")

print("== M3+M4: t11 change request ('बस' present) repairs, never confirms ==")
out = run_turn([(11, "जो तूने 9000 कर दिया है ना उसको 900 ही है बस एक जीरो कम हो ज"),
                (12, "हाँ")],
               "02690001700001203")
d, val, st, prop = out[0]
check("t11 action = repair echo (never echo_full confirm)", d["action"], "echo_confirm")
check("t11 proposed 9000->900", prop, "0269001700001203")
check("t11 base kept until confirm (L1)", val, "02690001700001203")
check("t11 status confirming", st, "confirming")
d, val, st, _ = out[1]
check("t12 confirm commits 9000->900", (d["action"], val, st), ("confirm_ack", "0269001700001203", "confirmed"))

print("== M4: 'की जगह' pair + confirm word in frame ==")
out = run_turn([(1, "9000 की जगह 900 कर दो बस")], "02690001700001203")
d, val, _, prop = out[0]
check("'9000 की जगह 900... बस' repairs (frame beats confirm)", (d["action"], prop),
      ("echo_confirm", "0269001700001203"))

print("== M4: digit-WORD corrections parse (stray 'एक'->'1' ignored) ==")
out = run_turn([(40, "इसमें एक चेंज नाइन नाइन थ्री फोन नहीं नाइन नाइन थ्री फाइव है")],
               "99345122324")
d, val, st, prop = out[0]
# RE-PIN (M8 / lock §8 N6, 2026-09-05): the STT wrote 'फोन' for the user's
# 'four', so the hearing is INCOMPLETE (993? ... 9935). The old pin proposed
# 993545122324 — a GUESS (it inserted a 5 instead of replacing the 4; the
# user meant 99355122324). Under N6 the turn clarifies, the value is kept,
# and nothing is proposed. The M4 goal (never retry-wipe) still holds.
check("t40 action = clarify (INCOMPLETE hearing; was a guessed repair echo)", d["action"], "clarify")
check("t40 proposes nothing (no guess)", prop, "99345122324")
check("t40 value kept (never wiped)", val, "99345122324")

print("== M1: an unparseable edit-intent turn keeps the value (never wipes) ==")
out = run_turn([(41, "शुरुआत में जो नाइन नाइन थी फॉर है वह नहीं होकर नाइन नाइन थी")],
               "99345122324")
d, val, _, _ = out[0]
check("t41 (garbled edit) value preserved", val, "99345122324")
check("t41 speaks (hold/clarify/retry — never silent, never wiped)",
      d["action"] in ("hold_edit", "clarify", "retry", "echo_confirm") and d["line"] is not None, True)

print("== regression pins: PLAIN rejections still wipe; re-dictation still replaces ==")
out = run_turn([(5, "नहीं, गलत है")], "02690001700001203")
d, val, _, _ = out[0]
check("plain whole-turn rejection still clears + retries", (d["action"], val), ("retry", ""))
out = run_turn([(6, "नहीं, मेरा नंबर 02690001245703 है"), (7, "हाँ")], "02690001700001203")
d, val, _, prop = out[0]
check("re-dictation with नहीं still REPLACES the whole value (dv path) — as a proposal",
      (d["action"], prop), ("echo_confirm", "02690001245703"))
d, val, _, _ = out[1]
check("confirm commits the re-dictation", (d["action"], val), ("confirm_ack", "02690001245703"))

print("== M5 (owner session 20260905_102221 t9): 'X नहीं है, Y है' with Y ⊂ X must REPAIR, never 'already correct' ==")
# Live: base 0269000125201203, user said '9000 नहीं है, 900 है।' -> the rail
# answered "900 sahi hai — poora number ...9000..." (the already-correct guard
# fired because the new digits 900 are a substring of the stored 9000) and
# the value never changed. The wrong group 9000 IS in the value: that is a
# repair, and the guard must only fire when the wrong group is NOT there.
out = run_turn([(9, "9000 नहीं है, 900 है।"), (10, "हाँ")], "0269000125201203")
d, val, _, prop = out[0]
check("t9 9000->900 is PROPOSED (echo_confirm of the repaired value), not 'already correct'",
      (d["action"], prop), ("echo_confirm", "026900125201203"))
check("t9 base kept until confirm (L1)", val, "0269000125201203")
check("t9 spoken line is NOT the already-correct line", "sahi hai — poora" in (d.get("line") or ""), False)
d, val, _, _ = out[1]
check("t10 confirm commits 026900125201203", (d["action"], val), ("confirm_ack", "026900125201203"))
# same shape, English negation + other substring pairs
out = run_turn([(9, "1200 nahi, 120 hai")], "02690012000001203")
d, val, _, prop = out[0]
check("'1200 nahi, 120 hai' with 1200 stored -> repair proposed", (d["action"], prop), ("echo_confirm", "0269001200001203"))
# the guard STILL protects a repeated instruction whose wrong group is gone
# (smoke-13 t30 shape: '6 -> 000000' when the 6 was already replaced and
# 000000 is present) — it must keep confirming, never re-apply.
eng = {"dictation": {"value": "026900120000005703", "status": "confirming"}, "conv": {}}
d = decide("6 को replace करना है 6 बार 0 से", eng, 30)
check("smoke-13 t30 repeated instruction still -> already-correct confirm", d["action"], "echo_confirm")
check("smoke-13 t30 value untouched", eng["dictation"]["value"], "026900120000005703")
# and smoke-12 t15 ('5 वाला नहीं, 5 बार 0' with 00000 already there): the
# wrong group '5' IS in the value (…5703) but 'correct' 00000 is present too
# -> stays a confirm (the digits asked for are already there, whole group).
eng = {"dictation": {"value": "01212012000001203", "status": "confirming"}, "conv": {}}
d = decide("वो 5 वाला नमबर नहीं है वो 5 बार 0 मैंने बोला है", eng, 15)
check("smoke-12 t15 still -> already-correct confirm", d["action"], "echo_confirm")
check("smoke-12 t15 value untouched", eng["dictation"]["value"], "01212012000001203")

print("== M6 (owner session 20260905_102221 t21): confirm + reject in ONE breath is UNCLEAR -> clarify, never wipe ==")
# Live: 'ठीक है, ओके, चलो कोई नहीं' on an unconfirmed base -> _is_plain_reject
# was True -> retry_wipe erased 0269000125201203 and the retry line was
# cancelled before audio. A turn that carries BOTH a confirm word and a
# reject word is not a whole-turn rejection: the value must survive and the
# rail must ask.
eng = {"dictation": {"value": "0269000125201203", "status": "pending"}, "conv": {}}
d = decide("ठीक है, ओके, चलो कोई नहीं", eng, 21)
check("t21 mixed confirm+reject -> clarify (asks), not retry", d["action"], "clarify")
check("t21 value KEPT", eng["dictation"]["value"], "0269000125201203")
check("t21 line names the stored number", "zero two six nine" in (d.get("line") or ""), True)
eng = {"dictation": {"value": "0269000125201203", "status": "confirming"}, "conv": {}}
d = decide("haan theek hai, nahi nahi", eng, 22)
check("English mixed confirm+reject while confirming -> clarify", d["action"], "clarify")
check("...value KEPT", eng["dictation"]["value"], "0269000125201203")
# a plain rejection with NO confirm word still wipes (regression pin above
# stays true) and a plain confirm still confirms.
eng = {"dictation": {"value": "0269000125201203", "status": "pending"}, "conv": {}}
d = decide("नहीं, गलत है", eng, 23)
check("plain reject still clears + retries", (d["action"], eng["dictation"]["value"]), ("retry", ""))
eng = {"dictation": {"value": "0269000125201203", "status": "pending"}, "conv": {}}
d = decide("ठीक है", eng, 24)
check("plain confirm while pending still -> echo_full", d["action"], "echo_full")

print("== M7 (owner session 20260905_124658 t8/t13): a QUESTION about the number is ANSWERED, never treated as a correction ==")
# Live t8: 'नहीं, one two के बाद कितने बार जीरो है' (base 02690012000001203) ->
# the correction parser read (anchor 12, wrong 12, correct 0) and Aiva
# PROPOSED 0269000000001203 ("maine suna: ... confirm karo"). The user asked
# HOW MANY zeros — that is a question about the stored value; the only right
# answer is the value itself (recall), and the base must not move.
B = "02690012000001203"
eng = {"dictation": {"value": B, "status": "pending"}, "conv": {}}
d = decide("नहीं, one two के बाद कितने बार जीरो है", eng, 8)
check("t8 'कितने बार जीरो' -> recall (answer), not a proposal", d["action"], "recall")
check("t8 no proposal created", (eng["dictation"].get("proposal") or {}).get("derived"), None)
check("t8 base untouched", eng["dictation"]["value"], B)
check("t8 line speaks the stored number", "zero two six nine zero zero one two zero zero zero zero zero one two zero three" in d["line"], True)
eng = {"dictation": {"value": B, "status": "pending"}, "conv": {}}
d = decide("बन टू के बाद कितनी कितने जीरों आए हैं", eng, 13)
check("t13 'कितने जीरों आए हैं' -> recall, not an 'already correct' echo", d["action"], "recall")
check("t13 base untouched", eng["dictation"]["value"], B)
for q in ("12 के बाद कितने जीरो हैं?", "one two ke baad kitne zero hain", "कितने बार जीरो लिखा है"):
    eng = {"dictation": {"value": B, "status": "confirming"}, "conv": {}}
    d = decide(q, eng, 20)
    check(f"question {q!r} -> recall, value kept", (d["action"], eng["dictation"]["value"]), ("recall", B))
# a STATEMENT with the same words but no question word is still a correction
# (pre-existing 'X के बाद Y' = insert-after semantics; base untouched, proposal only)
eng = {"dictation": {"value": "026900121203", "status": "pending"}, "conv": {}}
d = decide("one two के बाद 3 बार जीरो है", eng, 21)
check("statement '12 के बाद 3 बार जीरो' is still a correction (proposal)", d["action"], "echo_confirm")
check("...proposal = 12 + 000 inserted, base untouched",
      ((eng["dictation"].get("proposal") or {}).get("derived"), eng["dictation"]["value"]),
      ("026900120001203", "026900121203"))

print("== M8 (owner session 20260905_124658 t15): an INCOMPLETE hearing NEVER becomes a proposal ==")
# Live t15: 'बार का मैं बता रहा हूँ, ठीक है, one two, जीरो, जीरो, जीरो, जीरो, जीरो,
# पांच जीरो है, one two के बाद' — the observation was INCOMPLETE ('पांच जीरो' =
# 50 or 00000, COUNT_OR_DIGIT); the legacy parser guessed 120000050 and Aiva
# proposed a 26-digit number. Lock §3: INCOMPLETE => clarify/hold only.
T15 = "बार का मैं बता रहा हूँ, ठीक है, one two, जीरो, जीरो, जीरो, जीरो, जीरो, पांच जीरो है, one two के बाद"
eng = {"dictation": {"value": B, "status": "confirming"}, "conv": {}}
d = decide(T15, eng, 15)
check("t15 INCOMPLETE -> clarify (asks), never a proposal", d["action"], "clarify")
check("t15 no 26-digit proposal", (eng["dictation"].get("proposal") or {}).get("derived"), None)
check("t15 base untouched", eng["dictation"]["value"], B)
check("t15 the clarify names the ambiguity — both readings, no Devanagari (TTS-safe), no guessed number",
      ("'paanch zero' matlab five zero, ya paanch baar zero?" in d["line"], re.search(r"[\u0900-\u097F]", d["line"]) is None, "one two zero zero zero zero zero" in d["line"]), (True, True, True))
eng = {"dictation": {"value": B, "status": "pending"}, "conv": {}}
d = decide("नहीं पांच जीरो", eng, 16)
check("'नहीं पांच जीरो' alone -> hold (edit-intent fragment, spoken), value kept, nothing proposed",
      (d["action"], eng["dictation"]["value"], (eng["dictation"].get("proposal") or {}).get("derived")), ("hold_edit", B, None))
# ...and when the held instruction closes, a guessed reading NEVER becomes a
# proposal: 'नहीं पांच जीरो' + 'one two के बाद' + 'हाँ' used to close into the
# proposal '5012' (four digits replacing the whole number).
out = run_turn([(18, "नहीं पांच जीरो"), (19, "one two के बाद"), (20, "हाँ")], B)
check("held guess closes into a clarify, not the '5012' proposal",
      [o[0]["action"] for o in out], ["hold_edit", "silent", "clarify"])
check("...base kept, no proposal", (out[2][1], out[2][3]), (B, B))
check("...the clarify names the two readings (Roman only — TTS-safe)",
      ("five zero, ya paanch baar zero" in out[2][0]["line"], re.search(r"[\u0900-\u097F]", out[2][0]["line"]) is None), (True, True))
# COMPLETE hearings are unaffected: '5 बार जीरो' is unambiguous
eng = {"dictation": {"value": "026900121203", "status": "pending"}, "conv": {}}
d = decide("one two के बाद 5 बार जीरो", eng, 17)
check("COMPLETE '5 बार जीरो' still repairs (proposal)", (d["action"], (eng["dictation"].get("proposal") or {}).get("derived")), ("echo_confirm", "02690012000001203"))
# the other INCOMPLETE kinds are stopped at the same boundary (lock §8 N6):
# an UNKNOWN word inside the digits (STT wrote 'फोन' for 'four') and an
# unbound 'do baar' — the known digits are spoken as known, nothing is guessed.
eng = {"dictation": {"value": "99345122324", "status": "pending"}, "conv": {}}
d = decide("इसमें एक चेंज नाइन नाइन थ्री फोन नहीं नाइन नाइन थ्री फाइव है", eng, 40)
check("OOV inside the digits -> clarify, no proposal, value kept",
      (d["action"], (eng["dictation"].get("proposal") or {}).get("derived"), eng["dictation"]["value"]),
      ("clarify", None, "99345122324"))
check("...the clarify names the unheard slot (4th digit), speaks the known ones, guesses nothing",
      ("chautha digit" in d["line"], "nine nine three kuch" in d["line"], "9935" not in d["line"] and "five one" not in d["line"]), (True, True, True))
eng = {"dictation": {"value": B, "status": "pending"}, "conv": {}}
d = decide("2 बार 026900", eng, 18)
check("'2 बार 026900' while a number is stored -> clarify, NOT appended",
      (d["action"], eng["dictation"]["value"]), ("clarify", B))
# fresh dictation with NO stored number is NOT gated yet (a slot clarify there
# needs a slot-fill state — Phase 3); its echo remains the check: pinned so the
# boundary of this change is explicit.
eng = {"dictation": {"value": "", "status": "pending"}, "conv": {}}
d = decide("5 जीरो 1 2", eng, 4)
check("armed + '5 जीरो 1 2' (no base) still accumulates silently (unchanged, Phase 3 scope)", (d["action"], eng["dictation"]["value"]), ("silent_accumulate", "5012"))
# a hold stays a hold (lock §8 permits it): the 103339 t18 pin is unchanged
eng = {"dictation": {"value": B, "status": "confirming", "proposal": {"base": B, "derived": "02690012000005203", "mode": "correction", "spec": ["12520", None, "1200000"], "created_turn": 9, "delivery": "spoken"}}, "conv": {}}
d = decide("नहीं पांच जीरो", eng, 18)
check("'नहीं पांच जीरो' with an open proposal still holds (edit-intent, spoken, no mutation)",
      (d["action"], eng["dictation"]["value"], eng["dictation"]["proposal"]["derived"]), ("hold_edit", B, "02690012000005203"))

# after the N6 clarify the user's ANSWER is a continuation of the same
# instruction (L3 buffer): a fully-known answer resolves it; a still-ambiguous
# answer clarifies again against the base; the base never moves until 'haan'.
out = run_turn([(15, T15), (16, "one two के बाद पांच बार जीरो"), (17, "हाँ")], B)
check("clarify -> answer 'one two ke baad paanch baar zero' (already so) -> already-correct echo -> 'haan' confirms the base",
      ([o[0]["action"] for o in out], out[2][1]), (["clarify", "echo_confirm", "confirm_ack"], B))
out = run_turn([(15, T15), (16, "one two के बाद 5 0 है"), (17, "हाँ")], B)
check("clarify -> answer 'one two ke baad 5 0' -> proposal spoken -> 'haan' commits ONLY then",
      ([o[0]["action"] for o in out], out[1][1], out[1][3], out[2][1]),
      (["clarify", "echo_confirm", "confirm_ack"], B, "0269001250000001203", "0269001250000001203"))
out = run_turn([(15, T15), (16, "हाँ")], B)
check("clarify -> bare 'haan' does NOT commit a guess: it asks again, base kept",
      ([o[0]["action"] for o in out], out[1][1], out[1][3]), (["clarify", "clarify"], B, B))
out = run_turn([(15, T15), (16, "नंबर क्या है")], B)
check("clarify -> 'number kya hai' recalls the base (buffer released)", ([o[0]["action"] for o in out], out[1][1]), (["clarify", "recall"], B))

# a whole re-dictation that contains the ambiguous phrase is clarified too; the
# answer fills the ONE ambiguous slot and the completed number is proposed
RE = "जीरो टू सिक्स नाइन जीरो जीरो वन टू पांच जीरो वन टू जीरो थ्री"
out = run_turn([(5, RE), (6, "पांच बार जीरो"), (7, "हाँ")], "026900125205203")
check("re-dictation with 'paanch zero' -> clarify; 'paanch baar zero' fills the slot -> full proposal -> 'haan' commits",
      ([o[0]["action"] for o in out], out[1][1], out[1][3], out[2][1]),
      (["clarify", "echo_confirm", "confirm_ack"], "026900125205203", "02690012000001203", "02690012000001203"))
out = run_turn([(5, RE), (6, "five zero"), (7, "हाँ")], "026900125205203")
check("...the other reading ('five zero') fills it the other way — no guess either way",
      ([o[0]["action"] for o in out], out[2][1]), (["clarify", "echo_confirm", "confirm_ack"], "02690012501203"))

print("== M9 (owner session 20260905_124658 t16): 'nahi, galat hai — poora number dobara bol' with a proposal open -> REVERT and read the base only ==")
# Live t16: a proposal was open; the user rejected it AND asked for the number.
# recall won the precedence and Aiva read BOTH the proposal and the base
# (14.5 s, trimmed mid-way). L1.4: a rejection of an open proposal reverts to
# the base; the recall is answered by the revert line (it speaks the base).
PROP = {"base": B, "derived": "0269000000001203", "mode": "correction", "spec": ["12", "12", "0"],
        "created_turn": 8, "delivery": "spoken"}
eng = {"dictation": {"value": B, "status": "confirming", "proposal": dict(PROP)}, "conv": {}}
d = decide("नहीं, नहीं, गलत है, पर पूरा नंबर दुबारा बोल", eng, 16)
check("t16 reject + recall with an open proposal -> revert (proposal dropped)", (d["action"], d.get("trigger")), ("retry", "proposal_reverted"))
check("t16 base kept, proposal gone", (eng["dictation"]["value"], eng["dictation"].get("proposal")), (B, None))
check("t16 the line speaks the BASE once (not the proposal)",
      speak_value(B) in d["line"] and speak_value(PROP["derived"]) not in d["line"], True)
# no proposal open: reject + recall is answered by the recall of the base (unchanged)
eng = {"dictation": {"value": B, "status": "confirming"}, "conv": {}}
d = decide("नहीं, नहीं, गलत है, पर पूरा नंबर दुबारा बोल", eng, 17)
check("no proposal: reject + recall -> recall of the base, value kept", (d["action"], eng["dictation"]["value"]), ("recall", B))
# a plain recall with a proposal open still distinguishes proposal from base (L1.2 pin)
eng = {"dictation": {"value": B, "status": "confirming", "proposal": dict(PROP)}, "conv": {}}
d = decide("नंबर क्या है", eng, 18)
check("plain recall with a proposal open still speaks proposal + base (L1.2)", (d["action"], "pehle wala" in d["line"]), ("recall", True))

print()
if fails:
    print(f"FAIL ({fails})")
    sys.exit(1)
print("ALL PASS")

#!/usr/bin/env python3
"""Regression: PRECISION-DETAIL RAIL (owner directive 2026-08-30; approved
fix ①) — v2 (2026-08-31 after owner smoke 2).

Smoke-2 failure classes locked here:
  - t29 '0269 0012420' must capture the WHOLE grouped number (was '0012420').
  - t30 '5, 7, 0, 3' must APPEND to the pending value, silently (was: replace
    + speak over the user while dictating).
  - t34 'डबल जीरो, वन, तू, चार बार जीरो, पाइट सेविन, जीरो त्री.' must echo
    DIGITS (was: raw Devanagari -> ITRANS garbage 'jIro, vana, tU...').
  - While a dictation is pending, filler must be SILENT, not LLM chatter.
  - 'kya likha / repeat karo' must recall the stored value (no LLM).

Regression test FIRST (2026-08-31). Run: python3 phase5/tests/test_precision_rail.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agent.precision_rail import (dictation_value, normalize_span, decide,
                                  speak_value, _parse_correction,
                                  ECHO_LINES, ACK_LINES, RETRY_LINES, FULL_LINES,
                                  RECALL_LINES, ARM_LINES)
from agent.turn_controller import greeting_line_for, GREETING_LINES
from agent.response_pipeline import run_turn, TurnContext
from agent.response_state import FULLY_PLAYED
from agent.reply_guard import feminine_self_reference

fails = 0
def check(label, got, want):
    global fails
    ok = got == want
    if not ok:
        fails += 1
        print(f"  ✗ {label}: got {got!r}, want {want!r}")
    else:
        print(f"  ✓ {label}")

print("== detection: the observed failure classes MUST be caught ==")
# t21 (smoke 1): comma-separated digit-by-digit dictation
v = dictation_value("0,2,8,9,7,0,1,2,4...0,5,7,0,3")
check("t21 comma digit list detected", v is not None, True)
# t25: short code with a keyword
v = dictation_value("mera account number 026 hai")
check("t25 keyword+digits", v, "026")
# t26: account number dictation
v = dictation_value("account number 9000 hai")
check("t26 keyword+digits", v, "9000")
# t33: bare long digit run
v = dictation_value("026900")
check("t33 6-digit run detected", v, "026900")
# smoke-2 t29: GROUPED number — '0269 0012420' must be ONE value, not just
# the last run (old bug: DIGIT_RUN grabbed '0012420' and dropped '0269').
v = dictation_value("0269 0012420")
check("t29 grouped number full capture", v, "0269 0012420")
# generic ID / OTP forms
v = dictation_value("otp 1 2 3 4 5 6 hai")
check("otp spaced digits detected", v is not None, True)
v = dictation_value("mera pan card number ABC 1 2 3 4 5 6 hai")
check("pan with digit list detected", v is not None, True)

print("== SMOKE-4 CATCH: Hindi compound number words (owner: 'ninyanbe panteen' = 9935) ==")
check("'निन्यानबे पैंतीस' (99 35) detected", dictation_value("निन्यानबे पैंतीस"), "निन्यानबे पैंतीस")
check("'निन्यानबे पैंतीस' -> 9935", normalize_span("निन्यानबे पैंतीस"), "9935")
check("STT variant 'नियान ने पैंतिश' (postposition) detected",
      dictation_value("नियान ने पैंतिश क्यों नहीं बोला"), "नियान ने पैंतिश")
check("'नियान ने पैंतिश' -> 9935", normalize_span("नियान ने पैंतिश"), "9935")
check("'99.35' (STT mishearing) detected as grouped", dictation_value("99.35"), "99.35")
check("'99.35' -> 9935", normalize_span("99.35"), "9935")
check("'इक्यानवे बयासी' (91 82) -> 9182", normalize_span("इक्यानवे बयासी"), "9182")
check("conversational 'एक दिन एक बात' never fires",
      dictation_value("एक दिन एक बात हुई"), None)
check("conversational 'एक बार मैंने सोचा' never fires",
      dictation_value("एक बार मैंने सोचा"), None)

print("== SMOKE-4 CATCH: whole-number re-dictation REPLACES, spelling APPENDS ==")
eng_r = {"dictation": {"value": "411907", "status": "confirming"}}
r = decide("995", eng_r, 21)
check("single grouped run '995' while pending APPENDS (append-first, smoke-5)", r["action"], "silent_accumulate")
check("'995' appended value", r["value"], "411907995")
eng_r2 = {"dictation": {"value": "411907", "status": "confirming"}}
r = decide("निन्यानबे पैंतीस", eng_r2, 21)
check("compound words while pending REPLACE (the real 9935)", r["action"], "echo_confirm")
check("compound replace value", r["value"], "9935")
eng_r3 = {"dictation": {"value": "690012", "status": "confirming"}}
r = decide("5, 7, 0, 3", eng_r3, 30)
check("separated digits while pending still APPEND", r["action"], "silent_accumulate")
check("appended value", r["value"], "6900125703")

print("== SMOKE-4 CATCH: question 'क्यों नहीं बोला' is NOT a reject ==")
eng_q = {"dictation": {"value": "411907", "status": "confirming"}}
r = decide("तुमने नियान ने पैंतिश क्यों नहीं बोला", eng_q, 20)
check("question-with-नहीं not a retry (re-dictates instead)", r["action"], "echo_confirm")
check("correction value", r["value"], "9935")
eng_q2 = {"dictation": {"value": "411907", "status": "confirming"}}
r = decide("नहीं, गलत है", eng_q2, 20)
check("plain rejection still retries", r["action"], "retry")

print("== SMOKE-4 CATCH: announcement arms the rail ('मोबाइल नंबर लिख ले') ==")
eng_a = {}
r = decide("अब तू बस एक चीज कर ले, एक मोबाइल नंबर है वो लिख ले, ठीक है", eng_a, 15)
check("announcement -> arm", r["action"], "arm")
check("arm speaks a line", r["line"] is not None, True)
check("armed state pending", eng_a["dictation"]["status"], "pending")
r = decide("99.35", eng_a, 17)
check("digits after arm -> SILENT accumulate (state-aware: no speech while dictating, smoke-7)", r["action"], "silent_accumulate")
check("post-arm value", r["value"], "9935")
check("no LLM leak (state stays rail)", eng_a["dictation"]["status"], "pending")
r = decide("bas", eng_a, 18)
check("'bas' after silent accumulate -> full echo", r["action"], "echo_full")
check("full echo value", r["value"], "9935")
r = decide("haan", eng_a, 19)
check("'haan' -> confirm ack", r["action"], "confirm_ack")

print("== SMOKE-5 CATCH (owner 2026-08-31): full-number dictation must not break ==")
print("   A. mid-dictation filler NEVER discards to the LLM (t28 'यह कर लेते हैं') ==")
eng_s5a = {}
r = decide("मैं तुझे एक अकाउंट नंबर बता रहा हूं इसको लिख ले फिर मुझे बता", eng_s5a, 20)
check("smoke5 announcement -> arm", r["action"], "arm")
r = decide("यह कर लेते हैं", eng_s5a, 28)
check("4-word filler while armed -> SILENT (was: LLM 'haan bol, kya number hai?')", r["action"], "silent")
check("armed state kept after filler", eng_s5a["dictation"]["status"], "pending")
r = decide("मैं तुझे एक अकाउंट नंबर बता रहा हूं इसको लिख ले फिर मुझे बता", eng_s5a, 20)
check("re-announcement while armed -> SILENT stay (owner t20, never LLM)", r["action"], "silent")
check("armed state kept after re-announcement", eng_s5a["dictation"]["status"], "pending")
r = decide("जे गलत है", eng_s5a, 29)
check("plain reject while armed-empty -> RETRY (answers 'galat hai')", r["action"], "retry")
check("armed state kept after reject", eng_s5a["dictation"]["status"], "pending")
r = decide("026", eng_s5a, 30)
check("digits after filler/reject -> SILENT accumulate (no speech while dictating)", r["action"], "silent_accumulate")
check("post-filler value", r["value"], "026")

print("   B. continuation runs APPEND (t25 '124205703', t33 '5703' — owner: 'not able to speak full no.') ==")
eng_s5b = {"dictation": {"value": "026026900", "status": "confirming"}}
r = decide("124205703", eng_s5b, 25)
check("9-digit single run while confirming APPENDS", r["action"], "silent_accumulate")
check("appended value keeps the prefix", r["value"], "026026900124205703")
r = decide("5703", eng_s5b, 33)
check("4-digit run while confirming APPENDS", r["action"], "silent_accumulate")
check("appended value", r["value"], "0260269001242057035703")
eng_s5b2 = {"dictation": {"value": "9935", "status": "confirming"}}
r = decide("नौ डबल जीरो", eng_s5b2, 22)
check("'नौ डबल जीरो' (900) while confirming APPENDS", r["action"], "silent_accumulate")
check("appended 900", r["value"], "9935900")

print("   C. explicit full re-dictation still REPLACES ==")
eng_s5c = {"dictation": {"value": "120000", "status": "confirming"}}
r = decide("डबल जीरो, वन, तू, चार बार ज़ीरो, पाइट सेविन, जीरो त्री", eng_s5c, 34)
check("digit-word-heavy re-dictation REPLACES (smoke-2 t34)", r["action"], "echo_confirm")
eng_s5c2 = {"dictation": {"value": "411907", "status": "confirming"}}
r = decide("पूरा नंबर है: 0269001242", eng_s5c2, 36)
check("restart phrase + digits REPLACES", r["action"], "echo_confirm")
check("restart replace value", r["value"], "0269001242")
eng_s5c3 = {"dictation": {"value": "1200005703", "status": "confirming"}}
r = decide("नहीं, मेरा नंबर 02690001245703 है", eng_s5c3, 37)
check("reject + full number REPLACES", r["action"], "echo_confirm")
check("reject replace value", r["value"], "02690001245703")

print("   D. explicit abandon releases the rail to the LLM ==")
eng_s5d = {"dictation": {"value": "", "status": "pending"}}
r = decide("अरे रहने दे, भूल जा", eng_s5d, 40)
check("abandon phrase discards -> None (LLM takes over)", r, None)
check("state discarded", eng_s5d["dictation"]["status"], "discarded")

print("== SMOKE-6 CATCH (owner 2026-08-31 session_20260831_122500): 'stil not able to correctly get no.', 'hallucinates in between', 'stopped speaking at last' ==")
print("   A. FRESH short digit runs ARE dictation (t12 '026', t13 '9000' went LLM + empty) ==")
for txt, tn in [("026", 12), ("9000", 13), ("9935", 20), ("995", 21)]:
    e = {}
    r = decide(txt, e, tn)
    check(f"fresh {txt!r} -> echo (was: LLM)", r is not None and r["action"], "echo_confirm")
    check(f"fresh {txt!r} value", (r or {}).get("value"), txt)
e = {}
r = decide("5, 7, 0, 3", e, 30)
check("fresh separated '5, 7, 0, 3' -> echo", r is not None and r["action"], "echo_confirm")
e = {}
r = decide("एक बार मैंने सोचा", e, 30)
check("conversational 'एक बार मैंने सोचा' still never fires", r, None)
e = {}
r = decide("9000 rupaye", e, 31)
check("amount '9000 rupaye' still never fires", r, None)

print("   B. armed-empty: status queries get ANSWERS (t5/t7/t16 — was silence/'stopped speaking') ==")
eng_b = {"dictation": {"value": "", "status": "pending"}}
r = decide("लिख लिया?", eng_b, 5)
check("'लिख लिया?' -> status line (not silence)", r["action"], "status")
check("status line speaks", r["line"] is not None, True)
r = decide("हरे लिख लिया है?", eng_b, 7)
check("'लिख लिया है?' -> status", r["action"], "status")
r = decide("तू बोल मुझे, तूने क्या लिखा है।", eng_b, 16)
check("'क्या लिखा' while armed-empty -> status (was: LLM fabrication)", r["action"], "status")
check("armed state kept after status", eng_b["dictation"]["status"], "pending")

print("   C. armed-empty: complaints -> retry, status queries -> answered, long fillers -> silent stay (t8/t10 — was discard to LLM) ==")
eng_c = {"dictation": {"value": "", "status": "pending"}}
r = decide("कुछ पूछ रहे हैं तुमसे लिख लिया है", eng_c, 8)
check("8-word 'लिख लिया है' query -> STATUS (was: discard->LLM 'kya likha hai? sunao')", r["action"], "status")
check("state kept after status query", eng_c["dictation"]["status"], "pending")
eng_c2 = {"dictation": {"value": "", "status": "pending"}}
r = decide("हाँ हाँ हाँ मैं समझ गया तुम क्या कह रहे हो", eng_c2, 8)
check("8-word plain filler (no dictation words) -> SILENT stay (never discard)", r["action"], "silent")
check("state kept after long filler", eng_c2["dictation"]["status"], "pending")
r = decide("मैंने अभी बोला तो तुने लिखा नहीं किया।", eng_c, 10)
check("'तुने लिखा नहीं किया' -> RETRY (was: discard->LLM 'miss ho gaya')", r["action"], "retry")
check("state kept after complaint", eng_c["dictation"]["status"], "pending")
r = decide("026", eng_c, 12)
check("digits after complaint -> SILENT accumulate (no speech while dictating)", r["action"], "silent_accumulate")
check("post-complaint value", r["value"], "026")

print("   D. continuation cues get a hold line (t19-t22 'आगे'/'इसके बाद क्या है') ==")
eng_d = {"dictation": {"value": "900", "status": "confirming"}}
r = decide("बोलो भाईया इसके बाद क्या है?", eng_d, 22)
check("'इसके बाद क्या है' while confirming -> hold line", r["action"], "hold")
check("hold line speaks", r["line"] is not None, True)
r = decide("आगे", eng_d, 20)
check("'आगे' while confirming -> hold line", r["action"], "hold")
r = decide("ठीक है ओके", eng_d, 24)
check("confirm still acks after holds", r["action"], "confirm_ack")

print("   F. confirming: 'लिखा नहीं' claim proves the value, status queries re-speak it ==")
eng_f = {"dictation": {"value": "026900124205703", "status": "confirming"}}
r = decide("मैंने अभी बोला तो तुने लिखा नहीं किया।", eng_f, 10)
check("'तुने लिखा नहीं किया' -> recall (prove it, was: LLM 'miss ho gaya')", r["action"], "recall")
check("value KEPT after claim (not cleared)", eng_f["dictation"]["value"], "026900124205703")
r = decide("लिख लिया?", eng_f, 5)
check("'लिख लिया?' while confirming -> recall (re-speak)", r["action"], "recall")
r = decide("ये clear?", eng_f, 6)
check("'ये clear?' while confirming -> recall", r["action"], "recall")
r = decide("ठीक है ओके", eng_f, 24)
check("confirm still acks", r["action"], "confirm_ack")
eng_f2 = {"dictation": {"value": "026900124205703", "status": "confirming"}}
r = decide("नहीं, गलत है", eng_f2, 22)
check("plain 'नहीं, गलत है' still retries", r["action"], "retry")
check("retry clears the value", eng_f2["dictation"]["value"], "")

print("   E. new-detail requests release the rail (t29 'एक address लिखो' — was swallowed silent) ==")
eng_e = {"dictation": {"value": "", "status": "pending"}}
r = decide("एक address लिखो", eng_e, 29)
check("'एक address लिखो' -> None (LLM takes the new request)", r, None)
check("state discarded", eng_e["dictation"]["status"], "discarded")
eng_e2 = {"dictation": {"value": "900", "status": "confirming"}}
r = decide("अब एक address लिखो", eng_e2, 33)
check("address request while confirming -> None", r, None)

print("== SMOKE-7 CATCH (owner 2026-08-31 session_20260831_130138): '4 bar zero -> it write 420', 'stops speaking at last', 'should not speak while i am speaking' ==")
print("   A. structured CORRECTION with anchor+wrong+correct REPAIRS the stored value ==")
eng7a = {"dictation": {"value": "02690012425703", "status": "confirming"}}
r = decide("12 के बाद 4 बार 0 है 420 नहीं है इसको correct करो और मुझे बता", eng7a, 14)
check("t14 correction -> echo of REPAIRED value (42->0000)", r["action"], "echo_confirm")
check("repaired value", r["value"], "0269001200005703")
eng7b = {"dictation": {"value": "02690012425703", "status": "confirming"}}
r = decide("4 बार 0 मैंने बोला है 1, 2 के बाद", eng7b, 9)
check("t9 anchor-correction -> echo with 0000 inserted after 12", r["action"], "echo_confirm")
check("t9 repaired value", r["value"], "026900120000425703")

print("   B. correction that CANNOT be applied -> ack + ask (never silent, never adopt spec digits) ==")
eng7c = {"dictation": {"value": "12", "status": "confirming"}}
r = decide("चार बार जीरो लिखना है तुम्हें, चार बार जीरो, 420 नहीं है, ठी", eng7c, 12)
check("t12 unresolvable correction -> retry with ack line (was: replace with '0000')", r["action"], "retry")
check("t12 line acknowledges the correction", r["line"] is not None, True)
check("t12 line mentions the correct digits", "0000" in r["line"], True)
eng7d = {"dictation": {"value": "", "status": "pending"}}
r = decide("12 के बाद 4 बार 0 है 420 नहीं है इसको correct करो और मुझे बता", eng7d, 14)
check("t14 correction while armed-empty -> ack line (not silent)", r["action"], "retry")
check("t14 armed-empty ack mentions 0000", r["line"] is not None and "0000" in r["line"], True)

print("   C. plain reject-full-number still REPLACES (not hijacked by correction) ==")
eng7e = {"dictation": {"value": "411907", "status": "confirming"}}
r = decide("तुमने नियान ने पैंतिश क्यों नहीं बोला", eng7e, 20)
check("question-with-नहीं still re-dictates 9935 (smoke-4, not a correction spec)", r["action"], "echo_confirm")
check("question replace value", r["value"], "9935")

print("   D. black hole gone: cue turns get hold lines ==")
eng7f = {"dictation": {"value": "900", "status": "confirming"}}
r = decide("आँगे भाईया कुछ तो बोल दो", eng7f, 19)
check("'आँगे...बोल दो' -> hold line (was: silent)", r["action"], "hold")
check("hold line speaks", r["line"] is not None, True)
r = decide("भाई बन्टू है बन्टू के आगे", eng7f, 17)
check("'...के आगे' -> hold line", r["action"], "hold")
r = decide("पाबूरू गांगे", eng7f, 18)
check("2-word garbage stays silent", r["action"], "silent")
check("value kept after garbage", eng7f["dictation"]["value"], "900")

print("   E. first segment after arm accumulates silently (owner: 'should not speak while i am speaking') ==")
eng7g = {"dictation": None}
r = decide("अच्छा एक account number बोल रहा हूँ तू लिख लेगा", eng7g, 2)
check("announcement -> arm", r["action"], "arm")
r = decide("026", eng7g, 3)
check("first segment '026' -> SILENT accumulate (was: echo that got cancelled)", r["action"], "silent_accumulate")
check("first segment line is None", r["line"], None)
r = decide("9000", eng7g, 4)
check("'9000' -> silent accumulate", r["action"], "silent_accumulate")
check("accumulated value", r["value"], "0269000")
r = decide("12420573", eng7g, 5)
check("'12420573' -> silent accumulate", r["action"], "silent_accumulate")
check("full accumulated value", r["value"], "026900012420573")
r = decide("क्या लिखा तुम्हें?", eng7g, 6)
check("'क्या लिखा?' -> recall of accumulated value", r["action"], "recall")
check("recall shows full value", r["value"], "026900012420573")
r = decide("नहीं यह नहीं है पिछले सुनो", eng7g, 7)
check("rejection -> retry (clears)", r["action"], "retry")
r = decide("02690012425703", eng7g, 8)
check("re-dictation while armed -> SILENT accumulate (state-aware, no mid-dictation speech)", r["action"], "silent_accumulate")
check("re-dictated value kept", r["value"], "02690012425703")
r = decide("बस यही है", eng7g, 9)
check("'बस यही है' -> full echo of the accumulated value", r["action"], "echo_full")
check("full echo value", r["value"], "02690012425703")

print("== SMOKE-8 CATCH (owner 2026-08-31 session_20260831_134403): 'why is it not speaking?', 'whole experience deteriorating' ==")
print("   A. announcement + number in ONE turn -> capture it (t5: number was lost) ==")
eng8a = {}
r = decide("लिख ठीक है अकाउंट नंबर को लिखो 026900124205703 लिख लिया", eng8a, 5)
check("t5 announcement+number -> ECHO the number (was: arm, number lost)", r["action"], "echo_confirm")
check("t5 captured value", r["value"], "026900124205703")
r = decide("तेरी एशिक तशे मैंने पूरा नंबर बोल दिया तुने लिखा किनहीं लिख", eng8a, 6)
check("t6 'लिखा किनहीं' complaint -> RECALL as proof (was: retry)", r["action"], "recall")
check("t6 recall value", r["value"], "026900124205703")

print("   B. question-tag 'की नहीं' is a QUERY, not a rejection (t13) ==")
eng8b = {"dictation": {"value": "9000124205703", "status": "confirming"}}
r = decide("लिखा तूने की नहीं", eng8b, 13)
check("t13 'लिखा तूने की नहीं' -> RECALL (was: retry that cleared the value)", r["action"], "recall")
check("t13 value kept", eng8b["dictation"]["value"], "9000124205703")

print("   C. writing-complaints prove the value (t14/t16/t17 retry-spam gone) ==")
eng8c = {"dictation": {"value": "9000124205703", "status": "confirming"}}
r = decide("अब तू पागल आकर लग गया अभी तक लिख नहीं पा रहागया तू", eng8c, 14)
check("t14 'लिख नहीं पा रहा' -> RECALL (was: retry)", r["action"], "recall")
check("t14 value kept", eng8c["dictation"]["value"], "9000124205703")
r = decide("को लिख नहीं पा रहा है", eng8c, 17)
check("t17 -> RECALL", r["action"], "recall")
eng8c2 = {"dictation": {"value": "", "status": "pending"}}
r = decide("मैंने पूरा नंबर बोल दिया तुने लिखा किनहीं लिख", eng8c2, 6)
check("armed-empty complaint -> SPOKEN ack (was: silent)", r["line"] is not None, True)
check("armed-empty complaint keeps pending", eng8c2["dictation"]["status"], "pending")

print("   D. query 'क्या लिखा... नंबर' answers (t15 — was silent black hole) ==")
eng8d = {"dictation": {"value": "", "status": "pending"}}
r = decide("तू बता तूने क्या लिखा है बेजक नंबर", eng8d, 15)
check("t15 query -> status line (was: silent)", r["action"], "status")
check("t15 line speaks", r["line"] is not None, True)

print("   E. 'बताओ' / 'क्या लिखे हो' recall (t24/t25 — was silent) ==")
eng8e = {"dictation": {"value": "026900012405703", "status": "confirming"}}
r = decide("बताओ", eng8e, 24)
check("t24 'बताओ' -> recall", r["action"], "recall")
r = decide("बताओ बेटा क्या लिखे हो", eng8e, 25)
check("t25 'बताओ...क्या लिखे हो' -> recall", r["action"], "recall")
check("t25 value", r["value"], "026900012405703")

print("   F. bare 'ही नहीं' is NOT a question tag (smoke-5 t27 still rejects) ==")
eng8f = {"dictation": {"value": "900", "status": "confirming"}}
r = decide("यह है ही नहीं मैंने पूरा बोला ही नहीं", eng8f, 27)
check("smoke-5 t27 bare नहीं -> RETRY (clears)", r["action"], "retry")
check("t27 value cleared", eng8f["dictation"]["value"], "")
eng8f2 = {"dictation": {"value": "900", "status": "confirming"}}
r = decide("नहीं, गलत है", eng8f2, 29)
check("plain reject still retries", r["action"], "retry")

print("   G. re-stating the stored number does NOT double it (t26 '...बस') ==")
eng8g = {"dictation": {"value": "026900012405703", "status": "confirming"}}
r = decide("026900012405703 बस", eng8g, 26)
check("t26 re-statement + 'बस' -> echo_full (was: appended to itself)", r["action"], "echo_full")
check("t26 value NOT doubled", r["value"], "026900012405703")
eng8g2 = {"dictation": {"value": "026900012405703", "status": "confirming"}}
r = decide("5703", eng8g2, 27)
check("tail '5703' continuation still APPENDS (smoke-3 flow untouched)", r["action"], "silent_accumulate")
check("tail appended value", r["value"], "0269000124057035703")

print("== SMOKE-10 CATCH (owner 2026-08-31 session_20260831_161730, VERIFIED build 4b5e955): 'हेलो' no reply; 'पिर से बोलो' -> hold instead of repeat ==")
print("   A. repeat-request 'पिर से बोलो' / 'फिर से बोलो' -> RECALL the stored value ==")
eng10a = {"dictation": {"value": "0269004204301", "status": "pending"}}
r = decide("पिर से बोलो", eng10a, 7)
check("t7 'पिर से बोलो' -> recall (was: hold)", r["action"], "recall")
check("t7 recall value", r["value"], "0269004204301")
r = decide("पिरशे बोलो", eng10a, 8)
check("t8 'पिरशे बोलो' -> recall (was: hold)", r["action"], "recall")
r = decide("फिर से बोलो", eng10a, 9)
check("'फिर से बोलो' -> recall", r["action"], "recall")
eng10b = {"dictation": {"value": "", "status": "pending"}}
r = decide("पिर से बोलो", eng10b, 7)
check("repeat-request armed-empty -> status line (nothing to repeat)", r["action"], "status")
check("armed-empty repeat line speaks", r["line"] is not None, True)
eng10c = {"dictation": {"value": "0269004204301", "status": "confirming"}}
r = decide("एक बार दोबारा रिपीट करो पूरा", eng10c, 14)
check("'दोबारा रिपीट करो' -> recall", r["action"], "recall")
r = decide("अरे भाई एक बार दुबारा रिपीट कर दो तुमने नंबर क्या लिखा है", eng10c, 9)
check("t9 long repeat+query -> recall", r["action"], "recall")

print("   B. GREETING REGRESSION PIN: main.py must NEVER run the engine block for a greeting turn ==")
_main_py = open(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "agent", "main.py")).read()
# The guard chain in run_agent_response must be:
#   if rail is not None: pass  ...  elif greeting is not None: pass  ...  elif engine and engine.get("sess"):
# so a greeting turn SKIPS the engine block (which would clobber engine_path
# to "fused" via build_policy_and_contract and overwrite the greeting stream).
_check_chain = _main_py[_main_py.index('if rail is not None:\n            pass'):]
_g = _check_chain.find('elif greeting is not None:')
_e = _check_chain.find('elif engine and engine.get("sess"):')
check("engine block guarded: greeting elif sits between rail guard and engine elif",
      0 <= _g < _e, True)
# the replay path must already produce greeting for a greeting turn (it is the
# archive-identity reference; run_turn guards greeting BEFORE sess_bound)
_rt = open(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "agent", "response_pipeline.py")).read()
_i_greet_rt = _rt.index('elif greeting:')
_i_sess_rt = _rt.index('elif sess_bound:')
check("run_turn guards greeting BEFORE sess_bound (reference path)", _i_greet_rt < _i_sess_rt, True)

print("   C. OWNER CORRECTION (smoke-10 t5): 'it is 4 bar zero not 420' ==")
print("      STT heard '4204301' for what was actually '4 बार 0, 4301' -> canonical 02690000004301 ==")
eng10f = {"dictation": {"value": "0269004204301", "status": "confirming"}}
r = decide("4 बार 0 है, 420 नहीं", eng10f, 11)
check("correction '4 बार 0 है, 420 नहीं' REPAIRS stored value", r["action"], "echo_confirm")
check("repaired value 420->0000", r["value"], "02690000004301")
eng10g = {"dictation": {"value": "0269004204301", "status": "confirming"}}
r = decide("420 नहीं है, 4 बार 0 है", eng10g, 12)
check("correction '420 नहीं है, 4 बार 0 है' REPAIRS", r["value"], "02690000004301")
# capture side: dictation WITH the बार word must expand, not truncate at the run
r = decide("026900 4 बार 0 4301", {"dictation": None}, 13)
check("dictation '026900 4 बार 0 4301' -> echo (full span, was: only '026900')", r["action"], "echo_confirm")
check("dictation expanded value", r["value"], "02690000004301")
# armed continuation: a बार segment appends as the expanded zeros
eng10h = {"dictation": {"value": "026900", "status": "pending"}}
r = decide("4 बार 0 4301", eng10h, 14)
check("armed '4 बार 0 4301' appends", r["action"], "silent_accumulate")
check("armed appended value", r["value"], "02690000004301")
# multiplier NEVER duplicates a multi-digit run ('2 बार 0269...' = said twice)
check("normalize '2 बार 026900' keeps the run (no duplication)", normalize_span("2 बार 026900"), "026900")
r = decide("2 बार 026900", {"dictation": None}, 15)
check("'2 बार 026900' -> echo 026900 (no duplication)", r["value"], "026900")
# conversational times-word still never fires
check("'एक बार मैंने सोचा 9000' NOT dictation (LLM)", decide("एक बार मैंने सोचा 9000", {"dictation": None}, 16), None)

print("== SMOKE-11 CATCH (owner 2026-08-31 session_20260831_171620, VERIFIED build 1500c29) ==")
print("   A. task-switch: NEW announcement+number while a value is stored REPLACES (t8 — was append) ==")
eng11a = {"dictation": {"value": "9935411907", "status": "confirming"}}
r = decide("पढ़ें या एक number, अब account number लिखो जरा, 026-900-1262", eng11a, 8)
check("t8 new account number replaces old mobile number", r["action"], "echo_confirm")
check("t8 new value", r["value"], "0269001262")
check("t8 old value gone", eng11a["dictation"]["value"], "0269001262")

print("   B. 'only this' correction REPLACES (t11 — was silent append) ==")
eng11b = {"dictation": {"value": "99354119070269001262", "status": "confirming"}}
r = decide("इसमें जो 9935411907 है, सिर्फ इतना नमबर, ठीक है?", eng11b, 11)
check("t11 'सिर्फ इतना' -> replace with stated number", r["action"], "echo_confirm")
check("t11 corrected value", r["value"], "9935411907")

print("   C. greeting while a task is active -> greeting line, task kept (t15/t16 — was silent) ==")
eng11c = {"dictation": {"value": "9935411907", "status": "confirming"}}
r = decide("Hello", eng11c, 15)
check("t15 'Hello' while armed -> greeting line (not silent)", r["line"] is not None, True)
r = decide("हेलो", eng11c, 16)
check("t16 'हेलो' while armed -> greeting line", r["line"] is not None, True)
check("t16 task still kept", eng11c["dictation"]["value"], "9935411907")

print("   D. long garbage releases the TURN but KEEPS the task; recall-by-meaning works after (t17/t19) ==")
eng11d = {"dictation": {"value": "9935411907", "status": "confirming"}}
r = decide("आई वा यहीं पर होगी चले गए कहीं", eng11d, 17)
check("t17 long garbage -> LLM flow (None)", r, None)
check("t17 task KEPT (was: discarded -> 'number nahi pata')", eng11d["dictation"]["value"], "9935411907")
r = decide("मेरा मोबाइल नंबर मुझे बता क्या है", eng11d, 19)
check("t19 'मेरा मोबाइल नंबर मुझे बता' -> recall (was: LLM 'mujhe nahi pata')", r["action"], "recall")
check("t19 recalled value", r["value"], "9935411907")
eng11e = {"dictation": {"value": "0269001262", "status": "confirming"}}
r = decide("मेरा नंबर क्या है", eng11e, 20)
check("'मेरा नंबर क्या है' -> recall", r["action"], "recall")

print("== SMOKE-3 CATCH: '4 बार जीरो' variants (owner: 'i said 4 bar zero') ==")
check("'4 बार जीरो' -> 0000", normalize_span("4 बार जीरो"), "0000")
check("'चार बार ज़ीरो' (nuqta) detected", dictation_value("4 बार ज़ीरो"), "4 बार ज़ीरो")
check("'4 बार ज़ीरो' (nuqta) -> 0000", normalize_span("4 बार ज़ीरो"), "0000")
check("'4 bar zero' (roman bar) detected", dictation_value("4 bar zero"), "4 bar zero")
check("'4 bar zero' -> 0000", normalize_span("4 bar zero"), "0000")
check("'four bar zero' -> 0000", normalize_span("four bar zero"), "0000")
check("t34 full dictation with arabic 4 still correct",
      normalize_span("डबल जीरो, वन, तू, 4 बार जीरो, पाइट सेविन, जीरो त्री."),
      "001200005703")

print("== speak_value: TTS clarity (owner: 'what tts speaks is not clear') ==")
check("speak_value digit-by-digit", speak_value("026900"), "zero two six nine zero zero")
check("speak_value preserves digits already spoken", speak_value("0"), "zero")
check("no raw digit string ever in a rail line",
      all(not any(ch.isdigit() for ch in line)
          for line in ECHO_LINES + ACK_LINES + FULL_LINES + RECALL_LINES), True)

print("== greeting rail (owner: 'it started with acha- not hello') ==")
check("'हेलो' first word -> greeting", greeting_line_for("हेलो एवा, क्या कर रहे हो?", 1),
      GREETING_LINES[1 % len(GREETING_LINES)])
check("'hello' -> greeting", greeting_line_for("hello kya kar rahe ho", 0) is not None, True)
check("'namaste' -> greeting", greeting_line_for("namaste bhaiya", 0) is not None, True)
check("'hi' first word -> greeting", greeting_line_for("hi", 0) is not None, True)
check("non-greeting -> None", greeting_line_for("bas aise hi", 0), None)
check("'hi' as particle NOT first word -> None", greeting_line_for("bas aise hi theek hai", 0), None)
check("greeting is deterministic rotation",
      greeting_line_for("hello", 7), GREETING_LINES[7 % len(GREETING_LINES)])

print("== normalization: echo/stored value is ALWAYS digits ==")
check("t34 Hindi digit-word dictation normalized",
      normalize_span("डबल जीरो, वन, तू, चार बार जीरो, पाइट सेविन, जीरो त्री."),
      "001200005703")
check("t27 multiplier dictation normalized",
      normalize_span("124 बार जीरो 5 7 8"), "1240578")
check("double zero", normalize_span("डबल जीरो"), "00")
check("times zero", normalize_span("चार बार जीरो"), "0000")
check("english digit words", normalize_span("zero two six"), "026")
check("grouped number normalized", normalize_span("0269 0012420"), "02690012420")
check("comma list normalized", normalize_span("6, 9, 00, 1, 2"), "690012")
check("digit run unchanged", normalize_span("0269001200005703"), "0269001200005703")

print("== non-detection: ordinary numbers must NOT trigger the rail ==")
for t in ["9000 rupaye chahiye", "2026 mein", "abhi 3 baje milte hain",
          "main 2 saal se yahin hoon", "number 3 wali seat",
          "haan theek hai", "kya hua", "bol na bhai", "achha"]:
    check(f"no-rail({t!r})", dictation_value(t), None)

print("== REAL-BASELINE FALSE-POSITIVE CLASS (gate 2026-08-31, owner t20/t23) ==")
t20 = ("अच्छा अगर तू एक interview दे रहा है और तुछे ये question पूछा जाएगा तू क्या "
       "ऐसे answer देगा नहीं ना तो तू जैसे answer देगा उनको समझाने के लिए और convince "
       "करने के लिए कि तुझे आता है तो एक बार मुझे उस तरीके से बताओ एक professional "
       "language में in English")
check("t20 conversational 'ek interview / ek baar' -> no rail", dictation_value(t20), None)
t23 = "वो वो टू रियल्ड बात सुनना वो टू डू डू एक चार"
check("t23 conversational 'sunn-na' not a reject", dictation_value(t23), None)
eng_t23 = {"dictation": {"value": "02897012400005703", "status": "pending"}}
r = decide(t23, eng_t23, 23)
check("t23 with pending: long conversational turn abandons -> LLM flow", r, None)
eng_rej = {"dictation": {"value": "02897012400005703", "status": "confirming"}}
r = decide("नहीं, गलत है", eng_rej, 23)
check("'नहीं, गलत है' still rejects", r["action"], "retry")
eng_part = {"dictation": {"value": "026", "status": "pending"}}
r = decide("bol na", eng_part, 24)
check("'bol na' is a particle, not a reject (stays silent)", r["action"], "silent")
check("'bol na' keeps the pending value", eng_part["dictation"]["value"], "026")

print("== decide(): single-shot dictation -> echo -> confirm -> ack ==")
engine = {}
r = decide("account number 026900 hai", engine, 1)
check("echo action", r["action"], "echo_confirm")
check("echo line speaks the digits word-by-word", "zero two six nine zero zero" in r["line"], True)
check("status confirming (awaiting user confirm)", r["status"], "confirming")
check("value captured in session task state", engine["dictation"]["value"], "026900")

r = decide("haan sahi hai", engine, 2)
check("confirm action", r["action"], "confirm_ack")
check("ack line speaks the digits", "zero two six nine zero zero" in r["line"], True)
check("confirmed status", engine["dictation"]["status"], "confirmed")

print("== decide(): SEGMENT ACCUMULATION (smoke-2 t29 -> t30) ==")
engine2 = {}
r = decide("0269 0012420", engine2, 29)
check("t29 echo_confirm", r["action"], "echo_confirm")
check("t29 spoken is digit-by-digit",
      r["line"] == ECHO_LINES[29 % len(ECHO_LINES)].format(spoken=speak_value("02690012420")), True)
check("t29 value = full grouped number", r["value"], "02690012420")
r = decide("5, 7, 0, 3", engine2, 30)
check("t30 silent_accumulate (no speech while dictating)", r["action"], "silent_accumulate")
check("t30 line is None (silent)", r["line"], None)
check("t30 APPENDS to t29", r["value"], "026900124205703")
check("pending stays pending", r["status"], "pending")
# SMOKE-2 t34: a LONG fresh dictation while pending = RE-DICTATION ->
# replace (was wrongly appended). Short segments still accumulate.
r = decide("डबल जीरो, वन, तू, चार बार जीरो, पाइट सेविन, जीरो त्री.", engine2, 34)
check("long re-dictation replaces (not appends)", r["action"], "echo_confirm")
check("re-dictation value is the new number only", r["value"], "001200005703")
check("short segment still accumulates",
      decide("5, 7, 0, 3", engine2, 35)["action"], "silent_accumulate")
check("accumulated after re-dictation",
      engine2["dictation"]["value"], "0012000057035703")
r = decide("bas", engine2, 36)
check("'bas' finalizes -> echo_full (speak the FULL number)", r["action"], "echo_full")
check("echo_full speaks the full accumulated value", "zero zero one two zero zero zero zero five seven zero three five seven zero three" in r["line"], True)
r = decide("haan", engine2, 32)
check("confirm after full echo -> ack", r["action"], "confirm_ack")
check("confirmed", engine2["dictation"]["status"], "confirmed")

print("== decide(): SILENT while dictating (owner: 'speaking in between') ==")
engine3 = {"dictation": {"value": "690012", "status": "pending"}}
r = decide("जीरो", engine3, 10)
check("single digit word while pending -> silent accumulate", r["action"], "silent_accumulate")
check("'जीरो' appends", engine3["dictation"]["value"], "6900120")
r = decide("वहीं", engine3, 11)
check("non-digit filler while pending -> silent, keep value", r["action"], "silent")
check("value preserved", engine3["dictation"]["value"], "6900120")

print("== decide(): RECALL ('kya likha / repeat karo') ==")
engine4 = {"dictation": {"value": "026900124205703", "status": "confirmed"}}
r = decide("क्या लिखा तूने", engine4, 17)
check("recall action", r["action"], "recall")
check("recall speaks the stored value", "zero two six nine zero zero one two four two zero five seven zero three" in r["line"], True)
engine5 = {"dictation": {"value": "026900124205703", "status": "confirmed"}}
r = decide("repeat karo", engine5, 14)
check("'repeat karo' -> recall", r["action"], "recall")

print("== fresh dictation overrides a stale one ==")
engine6 = {"dictation": {"value": "111", "status": "confirming"}}
r = decide("nahi, account number 222 hai", engine6, 2)
check("fresh dictation re-echoes (new value wins)", r["action"], "echo_confirm")
check("fresh value captured", engine6["dictation"]["value"], "222")

print("== normal turns never intercepted ==")
check("normal turn not intercepted", decide("haan bhai, kya scene hai", {}, 1), None)

print("== persona: rail lines are masculine, short, roman script ==")
for line in ECHO_LINES + ACK_LINES + RETRY_LINES + FULL_LINES + RECALL_LINES + ARM_LINES:
    check(f"no feminine self-reference in {line!r}",
          feminine_self_reference(line), None)
    check(f"line is spoken-sized ({line!r})", len(line) < 120, True)
    check(f"no devanagari in {line!r}",
          not any("\u0900" <= c <= "\u097F" for c in line), True)

print("== determinism: same turn_no -> same line ==")
check("echo rotation is deterministic",
      decide("account number 026", {}, 7)["line"],
      ECHO_LINES[7 % len(ECHO_LINES)].format(spoken=speak_value("026")))


# ---------------------------------------------------------------------------
# run_turn integration: dictation turn -> rail line, no LLM call, no context
# pollution; silent turns -> suppressed.
# ---------------------------------------------------------------------------
print("== run_turn integration ==")
class FakeSess:
    def __init__(self):
        self.state = {"degraded_perception": False}
    def policy_for_turn(self):
        return {"mode": "VENT", "avoid": [], "response_goal": "encourage_continuation",
                "delivery": "normal", "topic": None, "goals": [], "must_not": []}
    def memory_view(self):
        return []

class FakeLCM:
    def __init__(self):
        self.turns = []
    def add_turn(self, role, text):
        self.turns.append((role, text))
    def needs_compression(self):
        return False
    def get_overflow_turns(self):
        return []
    def get_compression_prompt(self, overflow):
        return ""
    def get_layer1(self):
        return self.turns
    def get_layer2(self):
        return {"active_topic": None}

def mk_ctx(no, text, engine, model="", recent=None):
    return TurnContext(turn_no=no, user_text=text, is_valid=True, engine=engine,
                       recent_reply_texts=recent or [], detail_mode={"turns_left": 0},
                       stuck_nudged={"until_turn": 0}, model_text=model,
                       acoustic={"duration_ms": 900, "rms": 1, "peak": 1})

engine = {"sess": FakeSess(), "lcm": FakeLCM(), "fused": None}
turn = run_turn(mk_ctx(21, "account number 02897012400005703 hai", engine))
check("engine_path = precision_rail", turn.get("engine_path"), "precision_rail")
check("llm_called is False (no LLM)", turn.get("llm_called"), False)
check("precise_detail archived", turn["precise_detail"]["action"], "echo_confirm")
check("digits archived", turn["precise_detail"]["value"], "02897012400005703")
check("response FULLY_PLAYED", turn.get("response_state"), FULLY_PLAYED)
check("llm_response is the deterministic echo line",
      turn.get("llm_response"),
      ECHO_LINES[21 % len(ECHO_LINES)].format(spoken=speak_value("02897012400005703")))
check("llm_response speaks the digits word-by-word (TTS clarity)",
      "zero two eight nine seven zero one two four zero zero zero zero five seven zero three"
      in turn.get("llm_response"), True)
check("llm_response_full equals spoken (deterministic, replayable)",
      turn.get("llm_response_full"), turn.get("llm_response"))
check("dictation NOT added to LLM context (lcm untouched)", engine["lcm"].turns, [])
check("policy/contract NOT built (no LLM decision)", turn.get("policy"), None)
check("dictation captured in task state",
      engine["dictation"]["value"], "02897012400005703")

# confirm turn through run_turn
turn2 = run_turn(mk_ctx(22, "haan sahi hai", engine,
                        recent=[turn["llm_response"]]))
check("confirm turn -> ack", turn2["precise_detail"]["action"], "confirm_ack")
check("ack spoken", turn2.get("llm_response"),
      ACK_LINES[22 % len(ACK_LINES)].format(spoken=speak_value("02897012400005703")))
check("confirmed in task state", engine["dictation"]["status"], "confirmed")

# segment accumulation turn is SILENT (suppressed) through run_turn
engine7 = {"sess": FakeSess(), "lcm": FakeLCM(), "fused": None}
run_turn(mk_ctx(29, "0269 0012420", engine7))
turn30 = run_turn(mk_ctx(30, "5, 7, 0, 3", engine7))
check("segment turn suppressed (no speech)", turn30.get("response_suppressed"), True)
check("segment turn archived as rail", turn30["precise_detail"]["action"], "silent_accumulate")
check("no llm_response on silent turn", turn30.get("llm_response"), None)
check("accumulated value preserved", engine7["dictation"]["value"], "026900124205703")

# normal turn after the cycle -> back to LLM flow
ctx3 = mk_ctx(23, "ab aur kya", engine, model="ab aur kuch nahi, bas itna hi.",
              recent=[turn2["llm_response"]])
turn3 = run_turn(ctx3)
check("post-cycle normal turn is fused again", turn3.get("engine_path"), "fused")
check("post-cycle normal turn uses the LLM text",
      turn3.get("llm_response"), "ab aur kuch nahi, bas itna hi.")

print("== SMOKE-12 CATCH (owner 2026-08-31 session_20260831_173910, VERIFIED build 4045af7) ==")
print("   A. 'बार' STT transcriptions this session: '5 x 0' / '5 बट 0' / '5 × 0' MUST expand (t12 was DROPPED) ==")
raw12 = dictation_value("5 x 0, 1, 2, 0, 3,")
check("t12 '5 x 0, 1, 2, 0, 3,' detected as dictation", raw12 is not None, True)
check("t12 span -> 00000 1203 (5-baar-0 NOT dropped)", normalize_span(raw12), "000001203")
check("'5 x 0' -> 00000", normalize_span("5 x 0"), "00000")
check("'5 बट 0' -> 00000", normalize_span("5 बट 0"), "00000")
check("'5 × 0' -> 00000", normalize_span("5 × 0"), "00000")
check("'एक बार मैंने सोचा 9000' still NOT dictation", dictation_value("एक बार मैंने सोचा 9000"), None)

print("   B. full armed flow (t9-t12): 012 + 1,2,0 + 1,2 + '5 x 0, 1, 2, 0, 3' -> 01212012000001203 ==")
eng12b = {"dictation": None}
r = decide("एक अकाउंट नंबर लिखो", eng12b, 8)
check("t8 arm fires", r["action"], "arm")
for txt in ["012", "1, 2, 0,", "1, 2,", "5 x 0, 1, 2, 0, 3,"]:
    r = decide(txt, eng12b, 9)
    check(f"silent accumulate {txt!r}", r["action"], "silent_accumulate")
check("5-baar-0 included in accumulated value", eng12b["dictation"]["value"], "01212012000001203")
r = decide("क्या लिखा तुमने?", eng12b, 13)
check("t13 recall echoes the full value", r["action"], "recall")
check("t13 recalled value", r["value"], "01212012000001203")

print("   C. query-about-stored + digit-ish words -> recall-as-proof, NEVER append (t14) ==")
eng12c = {"dictation": {"value": "01212012000001203", "status": "pending"}}
r = decide("मैंने बोला था 5 बट 0 उसका क्या किया तुमने", eng12c, 14)
check("t14 -> recall (was: would silently append)", r["action"], "recall")
check("t14 value unchanged", eng12c["dictation"]["value"], "01212012000001203")

print("   D. correction whose 'correct' is ALREADY in the stored value -> CONFIRM, never wipe (t15) ==")
eng12d = {"dictation": {"value": "01212012000001203", "status": "confirming"}}
r = decide("वो 5 वाला नमबर नहीं है वो 5 बार 0 मैंने बोला है", eng12d, 15)
check("t15 -> echo_confirm (was: retry-wipe 'poori number phir se bolo')", r["action"], "echo_confirm")
check("t15 value KEPT", eng12d["dictation"]["value"], "01212012000001203")
eng12d2 = {"dictation": {"value": "12", "status": "confirming"}}
r = decide("चार बार जीरो लिखना है तुम्हें, चार बार जीरो, 420 नहीं है, ठी", eng12d2, 12)
check("unresolvable correction still retries (smoke-7 pin)", r["action"], "retry")

print("   E. corr is NOT a decision input — clarify must never fire on real speech (t17; corr stays telemetry) ==")
eng12e = {"dictation": {"value": "", "status": "pending"}, "stt_corr": 0.1516}
r = decide("तो तुम लिख क्यों नहीं रहे हो जब हम बोल रहे हैं तो", eng12e, 17)
check("t17 armed-empty long talk -> silent stay (smoke-6 pin), never false-clarify", r["action"], "silent")
check("t17 no 'didn't catch' line", "didn't" not in (r["line"] or ""), True)
check("t17 task kept", eng12e["dictation"]["status"], "pending")
eng12e2 = {"dictation": {"value": "", "status": "pending"}, "stt_corr": 0.5}
r = decide("हुआ है", eng12e2, 18)
check("high-corr short filler -> silent (interjection pin)", r["action"], "silent")

print("   F. armed-empty streak >= 2 -> NUDGE line (t18-t24 were a 9-turn silent black hole) ==")
eng12f = {"dictation": {"value": "", "status": "pending"}}
r = decide("हुआ है", eng12f, 18)
check("streak 1 -> silent", r["action"], "silent")
r = decide("चलो चाहिए", eng12f, 19)
check("streak 2 -> nudge line (not silent)", r["line"] is not None, True)
r = decide("कर दो", eng12f, 21)
check("streak 3 -> nudge", r["line"] is not None, True)
r = decide("दीरे कर लो यार मैंने पापा करका है मैंने तुम्हें बंद करतेहैं", eng12f, 23)
check("streak 4 long filler -> nudge, task KEPT", r["line"] is not None, True)
check("task still armed-empty", eng12f["dictation"]["value"], "")

print("   G. topic-switch while a value is stored -> CLOSE to confirmed, LLM answers (t29/t32 — was stuck echo) ==")
eng12g = {"dictation": {"value": "7398438138", "status": "confirming"}}
r = decide("ठीक है अब हमको जरा वह चाहिए ज़िन के बारे में बताओ", eng12g, 29)
check("t29 'ज़िन के बारे में' -> LLM flow (was: echo_full stuck)", r, None)
check("t29 task kept as confirmed (last-known)", eng12g["dictation"]["status"], "confirmed")
r = decide("अब मैं बोल रहा हूं वॉइस एजेंट के बारे में बताओ", eng12g, 32)
check("t32 'वॉइस एजेंट के बारे में' -> LLM flow (was: recall echo)", r, None)
eng12g2 = {"dictation": {"value": "7398438138", "status": "confirmed"}}
r = decide("मेरा मोबाइल नंबर मुझे बता क्या है", eng12g2, 33)
check("post-close recall-by-meaning still works", r["action"], "recall")
eng12g3 = {"dictation": {"value": "7398438138", "status": "confirming"}}
r = decide("अकाउंट नंबर के बारे में बताओ", eng12g3, 34)
check("number-topic 'के बारे में' -> recall (not LLM)", r["action"], "recall")

print("   H. cold-gap digit span = FRESH number, silent replace (t26 '7398' after ~14 real turns) ==")
eng12h = {"dictation": {"value": "01212012000001203", "status": "confirming"},
          "conv": {"accum_gap": 14}}
r = decide("7398", eng12h, 26)
check("t26 cold-gap span -> silent_accumulate (fresh, never append)", r["action"], "silent_accumulate")
check("t26 old value REPLACED (not glued)", eng12h["dictation"]["value"], "7398")
r = decide("438138", eng12h, 27)
check("t27 next span -> append (gap=1)", r["action"], "silent_accumulate")
check("t27 final value", eng12h["dictation"]["value"], "7398438138")
eng12h2 = {"dictation": {"value": "01212012000001203", "status": "confirming"},
           "conv": {"accum_gap": 0}}
r = decide("7, 0, 3", eng12h2, 26)
check("short-gap span (no intervening turns) still APPENDS (smoke-5 pin)", r["action"], "silent_accumulate")
check("short-gap value continued", eng12h2["dictation"]["value"], "01212012000001203703")
# smoke-5's long-pause continuation (turn numbers 25 -> 33, ZERO intervening
# turns) must still append — the gap is counted in real turns, not turn_no.
eng12h3 = {"dictation": {"value": "026026900124205703", "status": "confirming"}}
r = decide("5703", eng12h3, 33)
check("t33 long turn-no gap but 0 real turns -> still APPENDS (smoke-5 pin)", r["action"], "silent_accumulate")
check("t33 value continued", eng12h3["dictation"]["value"], "0260269001242057035703")

print("   I. 'बताओ जरा' (STATUS_RE, not RECALL_RE) recalls post-close (t31) ==")
eng12i = {"dictation": {"value": "7398438138", "status": "confirmed"}}
r = decide("बताओ जरा", eng12i, 31)
check("t31 'बताओ जरा' -> recall (was: LLM)", r["action"], "recall")
check("t31 recalled value", r["value"], "7398438138")

print("   J. new write-command while a value is stored -> RE-ARM (t20 'एक mobile number लिखो') ==")
eng12j = {"dictation": {"value": "01212012000001203", "status": "confirming"}}
r = decide("एक mobile number लिखो", eng12j, 20)
check("t20 write-command with value stored -> arm line (was: silent)", r["action"], "arm")
check("t20 task re-armed empty", eng12j["dictation"]["value"], "")

print("== SMOKE-13 CATCH (owner 2026-08-31 session_20260831_181649, VERIFIED build fcb6318) ==")
print("   A. '620 नहीं है, 6 बार 0' correction parses; 620->000000 repairs the stored value (t29) ==")
c29 = _parse_correction("620 नहीं है, 6 बार 0 लिखने है, 6 को replace करो 6 बार 0 से.")
check("t29 correction parsed (wrong=620, correct=000000)", c29, (None, "620", "000000"))
eng13a = {"dictation": {"value": "026900126205703", "status": "confirming"}}
r = decide("620 नहीं है, 6 बार 0 लिखने है, 6 को replace करो 6 बार 0 से.", eng13a, 29)
check("t29 -> echo_confirm of REPAIRED value", r["action"], "echo_confirm")
check("t29 repaired value (6-baar-0 replaces 620)", eng13a["dictation"]["value"], "026900120000005703")
c30 = _parse_correction("6 को replace करना है 6 बार 0 से")
check("t30 replace-form parsed", c30, (None, "6", "000000"))
eng13b = {"dictation": {"value": "026900120000005703", "status": "confirming"}}
r = decide("6 को replace करना है 6 बार 0 से", eng13b, 30)
check("t30 repeat instruction -> already-correct confirm (guard before apply)", r["action"], "echo_confirm")
check("t30 value KEPT (never mangled by '6'->000000)", eng13b["dictation"]["value"], "026900120000005703")
eng13c = {"dictation": {"value": "026900120000005703", "status": "confirming"}}
r = decide("करके लिखा है ना तो वह 12 के बाद 6 बार जीरो है इसको अपडेट करो", eng13c, 32)
check("t32 anchor-correction already satisfied -> confirm, value KEPT", r["action"], "echo_confirm")
check("t32 no double-insert of 000000", eng13c["dictation"]["value"], "026900120000005703")

print("   B. 'लिखा है ना 1, 2, 6' = query-with-digits -> recall-as-proof (t31) ==")
eng13d = {"dictation": {"value": "026900120000005703", "status": "confirming"}}
r = decide("लिखा है ना 1, 2, 6", eng13d, 31)
check("t31 'लिखा है ना' + digits -> recall (never silent append)", r["action"], "recall")
check("t31 value unchanged", eng13d["dictation"]["value"], "026900120000005703")

print("   C. NO false clarify on clearly-heard speech (t23 'लिख ले वस्तु याद रखियो') ==")
eng13e = {"dictation": {"value": "", "status": "pending"}}
r = decide("लिख ले वस्तु याद रखियो ठीक है", eng13e, 23)
check("t23 no-number-word filler -> NOT 'didn't catch' clarify", "didn't" not in (r["line"] or ""), True)
check("t23 armed-empty streak 1 -> silent stay", r["action"], "silent")
check("t23 task kept armed", eng13e["dictation"]["status"], "pending")
eng13f = {"dictation": {"value": "7398438138", "status": "confirming"}}
r = decide("भाई यह बता कि मुबाइल नंबर तेरे पास का मेरा सेव है", eng13f, 37)
check("t37 long number-talk -> LLM flow (never clarify, never silence)", r, None)

print("   D. T10 acceptance: 'कि यह काशिड नंबर आ गया' -> deterministic clarify, NEVER silence ==")
eng13g = {"dictation": {"value": "02690000004301", "status": "confirming"}}
r = decide("कि यह काशिड नंबर आ गया", eng13g, 10)
check("T10 short number-talk while value stored -> line (never silence)", r["line"] is not None, True)
check("T10 line is a clarify (mentions not-heard)", "samjha" in r["line"] or "suna" in r["line"] or "catch" in r["line"], True)
eng13h = {"dictation": {"value": "", "status": "pending"}}
r = decide("यह कर लेते हैं", eng13h, 28)
check("smoke-6 t28 4-word filler no number word -> SILENT pin holds", r["action"], "silent")
r = decide("हुआ है", {"dictation": {"value": "", "status": "pending"}}, 18)
check("2-word filler still silent", r["action"], "silent")

print()
if fails:
    print(f"FAIL ({fails})")
    sys.exit(1)
print("ALL PASS")

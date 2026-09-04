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
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agent.precision_rail import decide as _decide_raw

# VALUE_TRANSACTION_LOCK (2026-09-04): decide() no longer commits an echo by
# itself — the PLAYBACK layer marks delivery (L2). These offline suites use the
# stand-in that marks every spoken decision as fully heard, i.e. the live
# behaviour when nothing is interrupted.
from agent.value_transaction import decide_heard as decide

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
check("t40 action = repair echo (not retry-wipe)", d["action"], "echo_confirm")
check("t40 proposed 993->9935 deterministically", prop, "993545122324")
check("t40 status confirming", st, "confirming")

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

print()
if fails:
    print(f"FAIL ({fails})")
    sys.exit(1)
print("ALL PASS")

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
from agent.precision_rail import decide

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
    """Run consecutive turns over one engine seeded with a stored value."""
    eng = {"dictation": {"value": seed, "status": "pending"}, "conv": {}}
    out = []
    for tn, t in turns:
        d = decide(t, eng, tn)
        out.append((d, (eng.get("dictation") or {}).get("value", ""),
                    (eng.get("dictation") or {}).get("status", "")))
    return out

print("== M1+M2: t26 edit-intent must NOT wipe; removal repairs ==")
out = run_turn([(26, "1242 नहीं है भाईया")], "026900124201203")
d, val, st = out[0]
check("t26 action is a repair echo (not retry-wipe)", d["action"], "echo_confirm")
check("t26 value survives (repair keeps the stored number)", val, "02690001203")
check("t26 status confirming (echo asks confirm)", st, "confirming")

print("== M1: wipe-stop cascade — the t26->t27 sequence now converges ==")
out = run_turn([(26, "1242 नहीं है भाईया"),
                (27, "12 के बाद 4 नहीं है, 12 के बाद 4 बार 0 है")],
               "026900124201203")
d1, v1, _ = out[0]
d2, v2, s2 = out[1]
check("t27 still parses as a structured correction", d2["action"] in ("echo_confirm", "retry"), True)
check("t27 never sees an empty value", v2 != "", True)
check("t27 acts on the REPAIRED value (not '' — no re-dictation loop)", v1, "02690001203")

print("== M3+M4: t11 change request ('बस' present) repairs, never confirms ==")
out = run_turn([(11, "जो तूने 9000 कर दिया है ना उसको 900 ही है बस एक जीरो कम हो ज")],
               "02690001700001203")
d, val, st = out[0]
check("t11 action = repair echo (never echo_full confirm)", d["action"], "echo_confirm")
check("t11 applied 9000->900", val, "0269001700001203")
check("t11 status confirming", st, "confirming")

print("== M4: 'की जगह' pair + confirm word in frame ==")
out = run_turn([(1, "9000 की जगह 900 कर दो बस")], "02690001700001203")
d, val, _ = out[0]
check("'9000 की जगह 900... बस' repairs (frame beats confirm)", val, "0269001700001203")

print("== M4: digit-WORD corrections parse (stray 'एक'->'1' ignored) ==")
out = run_turn([(40, "इसमें एक चेंज नाइन नाइन थ्री फोन नहीं नाइन नाइन थ्री फाइव है")],
               "99345122324")
d, val, st = out[0]
check("t40 action = repair echo (not retry-wipe)", d["action"], "echo_confirm")
check("t40 applied 993->9935 deterministically", val, "993545122324")
check("t40 status confirming", st, "confirming")

print("== M1: an unparseable edit-intent turn keeps the value (never wipes) ==")
out = run_turn([(41, "शुरुआत में जो नाइन नाइन थी फॉर है वह नहीं होकर नाइन नाइन थी")],
               "99345122324")
d, val, _ = out[0]
check("t41 (garbled edit) value preserved", val, "99345122324")
check("t41 speaks (clarify/retry — never silent, never wiped)",
      d["action"] in ("clarify", "retry", "echo_confirm"), True)

print("== regression pins: PLAIN rejections still wipe; re-dictation still replaces ==")
out = run_turn([(5, "नहीं, गलत है")], "02690001700001203")
d, val, _ = out[0]
check("plain whole-turn rejection still clears + retries", (d["action"], val), ("retry", ""))
out = run_turn([(6, "नहीं, मेरा नंबर 02690001245703 है")], "02690001700001203")
d, val, _ = out[0]
check("re-dictation with नहीं still REPLACES the whole value (dv path)",
      (d["action"], val), ("echo_confirm", "02690001245703"))

print()
if fails:
    print(f"FAIL ({fails})")
    sys.exit(1)
print("ALL PASS")

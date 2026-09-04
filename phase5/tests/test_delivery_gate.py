"""L2 — delivery gate (docs/VALUE_TRANSACTION_LOCK.md §3).

UNIT/INVARIANT coverage: a proposal becomes confirmable ONLY after its echo
crossed the delivery boundary (FULLY_PLAYED, or PARTIALLY_PLAYED with the
digit span inside heard_text). An echo cancelled before audio (the 103339
t8 case), UNHEARD, or a zero-audio TTS never becomes confirmable; a confirm
after such an echo re-speaks instead of committing. The write side is the
PLAYBACK layer (main.py completion + CancelledError; run_turn mirrors it) —
this suite drives it both through value_transaction.mark_delivery directly
and through run_turn's completion path, and pins main.py's call sites by
source inspection (main.py cannot import here — no livekit).

Run: python3 phase5/tests/test_delivery_gate.py
"""
import sys, os, re
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agent.precision_rail import decide as decide_raw, speak_value
from agent.value_transaction import (mark_delivery, delivery_from_turn, confirmable,
                                     SPOKEN, UNSPOKEN, UNHEARD_D, archive_precise_detail)
from agent.response_state import FULLY_PLAYED, PARTIALLY_PLAYED, UNHEARD
from agent.response_pipeline import run_turn, TurnContext

fails = 0
def check(label, got, want):
    global fails
    if got == want:
        print(f"  ✓ {label}")
    else:
        fails += 1
        print(f"  ✗ {label}: got {got!r}, want {want!r}")

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_main = open(os.path.join(ROOT, "agent", "main.py"), encoding="utf-8").read()

def eng_with(value, status="confirming"):
    return {"dictation": {"value": value, "status": status}, "conv": {}}

def rail_turn(eng, text, tn):
    """decide + the archive record main.py writes (precise_detail)."""
    d = decide_raw(text, eng, tn)
    turn = {"turn": tn, "precise_detail": archive_precise_detail(d, eng)}
    return d, turn

print("== delivery_from_turn: pure classification over archived playback facts ==")
span = speak_value("0269001700001203")
check("FULLY_PLAYED -> spoken", delivery_from_turn({"response_state": FULLY_PLAYED}, span), SPOKEN)
check("cancel_pre_audio -> unheard (even if state says otherwise)",
      delivery_from_turn({"response_state": PARTIALLY_PLAYED, "cancel_pre_audio": True, "heard_text": span}, span), UNHEARD_D)
check("UNHEARD -> unheard", delivery_from_turn({"response_state": UNHEARD}, span), UNHEARD_D)
check("PARTIALLY_PLAYED with the digit span heard -> spoken",
      delivery_from_turn({"response_state": PARTIALLY_PLAYED, "heard_text": "maine yeh samjha: " + span + ". sahi"}, span), SPOKEN)
check("PARTIALLY_PLAYED cut inside the span -> unheard",
      delivery_from_turn({"response_state": PARTIALLY_PLAYED, "heard_text": "maine yeh samjha: " + span[:20]}, span), UNHEARD_D)
check("FULLY_PLAYED but zero TTS audio -> unheard (B5)",
      delivery_from_turn({"response_state": FULLY_PLAYED, "tts": {"audio_duration_s": 0}}, span), UNHEARD_D)
check("no state at all -> unheard (never assume delivery)", delivery_from_turn({}, span), UNHEARD_D)

print("== the 103339 t8 case: correction echo cancelled pre-audio -> proposal can never commit ==")
eng = eng_with("02690001700001203")
d, turn = rail_turn(eng, "9000 की जगह 900 कर दो", 8)
check("t8 proposes (base kept)", (d["action"], eng["dictation"]["value"]), ("echo_confirm", "02690001700001203"))
turn.update({"response_state": UNHEARD, "cancel_pre_audio": True, "heard_text": "", "interrupted": True})
check("playback records UNHEARD on the proposal", mark_delivery(eng, turn), UNHEARD_D)
check("archived precise_detail carries delivery", turn["precise_detail"]["delivery"], UNHEARD_D)
check("not confirmable", confirmable(eng["dictation"]["proposal"]), False)
r = decide_raw("हाँ", eng, 9)
check("a 'haan' after the unheard echo RE-SPEAKS the proposal, no commit",
      (r["action"], r.get("trigger"), eng["dictation"]["value"]), ("echo_full", "unheard_echo", "02690001700001203"))
turn9 = {"turn": 9, "precise_detail": archive_precise_detail(r, eng), "response_state": FULLY_PLAYED, "heard_text": r["line"]}
check("the re-speak, fully played, marks SPOKEN", mark_delivery(eng, turn9), SPOKEN)
r = decide_raw("हाँ", eng, 10)
check("now the confirm commits", (r["action"], eng["dictation"]["value"]), ("confirm_ack", "0269001700001203"))

print("== a base echo (row 17 'bas') is gated the same way ==")
eng = eng_with("9935411907", "pending")
d, turn = rail_turn(eng, "बस", 3)
check("'bas' -> echo_full, echo_delivery UNSPOKEN until playback", (d["action"], eng["dictation"].get("echo_delivery")), ("echo_full", UNSPOKEN))
r = decide_raw("हाँ", eng, 4)
check("confirm with the echo still unspoken -> re-speak (never a blind commit)", (r["action"], eng["dictation"]["status"]), ("echo_full", "confirming"))
turn4 = {"turn": 4, "precise_detail": archive_precise_detail(r, eng), "response_state": FULLY_PLAYED, "heard_text": r["line"]}
mark_delivery(eng, turn4)
r = decide_raw("हाँ", eng, 5)
check("confirm after a heard echo commits", (r["action"], eng["dictation"]["status"]), ("confirm_ack", "confirmed"))

print("== proposal echo cut mid-span (barge-in) -> unheard; cut AFTER the span -> spoken ==")
for label, cut, want in (("cut inside the digits", 0.4, UNHEARD_D), ("cut after the digits", 1.0, SPOKEN)):
    eng = eng_with("0269004204301")
    d, turn = rail_turn(eng, "420 नहीं है, 4 बार 0 है", 12)
    line = d["line"]
    heard = line[: int(len(line) * cut)] if cut < 1 else line[: line.index("sahi") if "sahi" in line else len(line)]
    turn.update({"response_state": PARTIALLY_PLAYED, "heard_text": heard, "interrupted": True})
    check(label, mark_delivery(eng, turn), want)

print("== run_turn (the replay-identity path) marks delivery exactly like main.py ==")
class FakeSess:
    def policy_for_turn(self): return {}
    def memory_view(self): return {}
class FakeLCM:
    def add_turn(self, *a, **k): pass
    def needs_compression(self): return False
def ctx_for(eng, text, tn, **kw):
    eng.setdefault("sess", FakeSess()); eng.setdefault("lcm", FakeLCM())
    return TurnContext(turn_no=tn, user_text=text, engine=eng, model_text="", **kw)
eng = eng_with("02690001700001203")
t = run_turn(ctx_for(eng, "9000 की जगह 900 कर दो", 8, interrupted=True, played_any_audio=False))
check("run_turn: rail proposal, cancelled pre-audio -> UNHEARD recorded",
      (t.get("engine_path"), t["precise_detail"].get("delivery"), eng["dictation"]["proposal"]["delivery"]),
      ("precision_rail", UNHEARD_D, UNHEARD_D))
check("run_turn: base untouched", eng["dictation"]["value"], "02690001700001203")
t = run_turn(ctx_for(eng, "हाँ", 9))
check("run_turn: confirm after unheard echo re-speaks (FULLY_PLAYED this time -> SPOKEN)",
      (t["precise_detail"]["action"], t["precise_detail"].get("delivery")), ("echo_full", SPOKEN))
t = run_turn(ctx_for(eng, "हाँ", 10))
check("run_turn: commit only now", (t["precise_detail"]["action"], eng["dictation"]["value"]), ("confirm_ack", "0269001700001203"))
check("run_turn archive carries base + proposal for the replay carrier", ("base" in t["precise_detail"]), True)

print("== structural pins: main.py writes delivery at BOTH playback boundaries ==")
i_full = _main.index('turn["response_state"] = FULLY_PLAYED')
i_mark_full = _main.index("vt_mark_delivery(engine, turn)", i_full)
check("FULLY_PLAYED completion calls mark_delivery within its block", i_mark_full - i_full < 900, True)
i_cancel = _main.index('"remaining_text": _remainder}')
i_mark_cancel = _main.index("vt_mark_delivery(engine, turn)", i_cancel)
check("CancelledError path calls mark_delivery right after last_response", i_mark_cancel - i_cancel < 400, True)
check("main.py archives precise_detail through the shared helper", "vt_archive_precise_detail(rail, engine)" in _main, True)
check("no other writer of proposal delivery outside value_transaction",
      len(re.findall(r'\["delivery"\]\s*=', _main)), 0)

print()
if fails:
    print(f"FAIL ({fails})"); sys.exit(1)
print("ALL PASS")

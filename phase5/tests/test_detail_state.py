#!/usr/bin/env python3
"""Regression: CONTINUATION + DETAIL-INTENT CHUNKING — approved fixes ② and ③
(owner brief 2026-08-30/31).

② "aage?" continuation rail — the prompt frame (prompt_fragments.py 1b) made
the model end every detail chunk with the canned 'aage?' cue and (when the
6-turn detail latch expired) the user's 'aage / haan / bolte jao / roko mat'
was no longer interpreted as continuation, so the model restarted or
re-confirmed instead of advancing. Expected behavioral change: the delivery
rail tells the model to VARY its checkpoints, defines the user's continuation
cues (incl. 'bolte jao' / 'roko mat') as keep-going, and bans restart/
re-confirm; the SYSTEM owns continuation via explicit detail state rather
than the LLM reconstructing it from history.

③ detail-intent chunking — user asks for full detail ('detail mein samjhao',
'poora plan batao'), agent replied 1-2 lines. Expected behavioral change: a
detail request activates SYSTEM-owned delivery state (engine["detail"]) that
(a) keeps the multi-part intent alive ACROSS turns regardless of the 6-turn
latch expiry, (b) raises the spoken-chunk ceiling to the A-P1 chunk cap so a
chunk is a substantive thought, not 1-2 lines, (c) feeds the model a
delivery_state resume payload (step + last chunk) so a continuation advances
the explanation instead of restarting.

Regression test FIRST (2026-08-31). Run:
python3 phase5/tests/test_detail_state.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agent.prompt_fragments import SYSTEM_FUSED_V11
from agent.response_pipeline import (build_policy_and_contract, run_turn,
                                     TurnContext)
from agent.reply_guard import PLAN_CHUNK_CAP
from agent.response_state import FULLY_PLAYED

fails = 0
def check(label, got, want):
    global fails
    ok = got == want
    if not ok:
        fails += 1
        print(f"  ✗ {label}: got {got!r}, want {want!r}")
    else:
        print(f"  ✓ {label}")

print("== ② delivery rail: the prompt must own continuation semantics ==")
check("'bolte jao' is defined as keep-going", "bolte jao" in SYSTEM_FUSED_V11, True)
check("'roko mat' is defined as keep-going", "roko mat" in SYSTEM_FUSED_V11, True)
check("restart is banned", "restart from the beginning" in SYSTEM_FUSED_V11.lower(), True)
check("re-confirm is banned", "never re-confirm" in SYSTEM_FUSED_V11, True)
check("resume point is explicit",
      "resume EXACTLY" in SYSTEM_FUSED_V11, True)
check("delivery_state context is named",
      "delivery_state" in SYSTEM_FUSED_V11, True)
check("old canned cue list is GONE (no fixed 'aage?' every chunk)",
      "'aage?', 'aur suno?'" not in SYSTEM_FUSED_V11, True)
check("speak MORE per chunk (owner: 'speak more 1-2 more lines')",
      "3-5 sentences" in SYSTEM_FUSED_V11, True)
check("no question/cue ending (owner: 'asking everytime is not good')",
      "Do NOT end your reply with a question" in SYSTEM_FUSED_V11, True)
check("user can interrupt for clarity is stated",
      "interrupt if they need clarity" in SYSTEM_FUSED_V11, True)


# ---------------------------------------------------------------------------
# ③ system-owned detail state
# ---------------------------------------------------------------------------
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
        return {"active_topic": "planning"}

def mk_engine():
    return {"sess": FakeSess(), "lcm": FakeLCM(), "fused": None,
            "detail": {"active": False, "step": 0, "last_chunk": "", "resume": None}}

def call_bpc(turn, *, user_text="", engine=None, detail=None, recent=None):
    e = engine if engine is not None else mk_engine()
    return build_policy_and_contract(
        user_text=user_text, turn=turn, engine=e, sess=e["sess"], lcm=e["lcm"],
        recent_reply_texts=recent if recent is not None else [],
        detail_mode=detail if detail is not None else {"turns_left": 0},
        stuck_nudged={"until_turn": 0}, log_event=lambda *a, **k: None)

logs = []
def log_event(*a, **k):
    logs.append(a[0])

print("== ③ detail request activates system delivery state ==")
e = mk_engine()
t = {"turn": 1, "turn_type": "speech", "route_action": "normal"}
call_bpc(t, user_text="detail mein samjhao", engine=e)
check("detail state active", e["detail"]["active"], True)
check("step = 1", e["detail"]["step"], 1)
check("detail_state archived", t.get("detail_state"), {"active": True, "step": 1})
check("delivery chunked_detail on first chunk", t["policy"]["delivery"], "chunked_detail")
check("detail_mode flag set", t.get("detail_mode"), True)

print("== ③ continuation advances the plan (system-owned, latch-independent) ==")
e = mk_engine()
e["detail"] = {"active": True, "step": 1, "last_chunk": "Pehla point yeh hai ki plan banana zaroori hai.",
               "resume": None}
e["last_head_plan"] = None
t = {"turn": 2, "turn_type": "speech", "route_action": "normal"}
# THE BUG CASE: the 6-turn latch has fully EXPIRED (turns_left=0) and the user
# still says 'aage' — pre-fix this fell through to a fresh-conversation reply.
call_bpc(t, user_text="aage", engine=e, detail={"turns_left": 0})
check("continuation works after latch expiry (step 2)", e["detail"]["step"], 2)
check("detail_continue flagged", t.get("detail_continue"), True)
check("delivery = continue_detail", t["policy"]["delivery"], "continue_detail")
check("latch renewed from expiry", t["detail_mode"], True)
check("resume payload carries the last chunk",
      e["detail"]["resume"]["last_chunk"], "Pehla point yeh hai ki plan banana zaroori hai.")
check("resume payload carries the step", e["detail"]["resume"]["step"], 2)
check("detail_state archived with step 2",
      t.get("detail_state"), {"active": True, "step": 2})

print("== ③ a moved-on turn with EXPIRED latch ends the delivery ==")
e = mk_engine()
e["detail"] = {"active": True, "step": 3, "last_chunk": "kuch", "resume": None}
t = {"turn": 3, "turn_type": "speech", "route_action": "normal"}
call_bpc(t, user_text="office ka kaam ho gaya, ab ghar chalta hoon", engine=e,
         detail={"turns_left": 0})
check("detail state deactivated on topic move (latch expired)",
      e["detail"]["active"], False)
check("step reset", e["detail"]["step"], 0)
check("no detail_state archived for the moved-on turn",
      t.get("detail_state"), None)

print("== ③ a soft acknowledgment within the latch window keeps the plan ==")
e = mk_engine()
e["detail"] = {"active": True, "step": 2, "last_chunk": "kuch", "resume": None}
t = {"turn": 3, "turn_type": "speech", "route_action": "normal"}
call_bpc(t, user_text="theek hai", engine=e,
         detail={"turns_left": 2})
check("plan survives a soft ack ('theek hai' must not kill it)",
      e["detail"]["active"], True)
check("step unchanged on a non-continuation ack", e["detail"]["step"], 2)
check("detail_state still archived", t.get("detail_state"),
      {"active": True, "step": 2})

print("== ③ chunk ceiling: a system-active detail plan gets the A-P1 ceiling ==")
e = mk_engine()
e["detail"] = {"active": True, "step": 1, "last_chunk": "", "resume": None}
ctx = TurnContext(turn_no=4, user_text="detail mein samjhao", is_valid=True,
                  engine=e, recent_reply_texts=[],
                  detail_mode={"turns_left": 0}, stuck_nudged={"until_turn": 0},
                  model_text=("Point ek: subah utho. Point do: plan banao. "
                              "Point teen: kaam karo. Point chaar: review karo. "
                              "Point paanch: aaram karo. Point chhe: so jao. "
                              "Point saat: agla din taiyaar karo. Point aath: mast raho.") * 2,
                  acoustic={"duration_ms": 900, "rms": 1, "peak": 1})
turn = run_turn(ctx)
check("detail mode on", turn.get("detail_mode"), True)
check("chunk is substantive (>110 chars, not 1-2 lines)",
      len(turn.get("llm_response") or "") > 110, True)
check("ceiling respects A-P1 chunk cap",
      len(turn.get("llm_response") or "") <= PLAN_CHUNK_CAP + 40, True)
check("FULLY_PLAYED", turn.get("response_state"), FULLY_PLAYED)
check("completed chunk stored for the next continuation",
      e["detail"]["last_chunk"], turn.get("llm_response"))
check("resume cleared after the turn", e["detail"].get("resume"), None)

print("== ② delivery flag: fresh detail requests stay chunked; keep-going phrases continue ==")
e = mk_engine()
t = {"turn": 1, "turn_type": "speech", "route_action": "normal"}
call_bpc(t, user_text="poora plan batao", engine=e)
check("fresh detail request with 'batao' -> chunked_detail (NOT continue)",
      t["policy"]["delivery"], "chunked_detail")
e2 = mk_engine()
e2["detail"] = {"active": True, "step": 2, "last_chunk": "kuch", "resume": None}
t2 = {"turn": 2, "turn_type": "speech", "route_action": "normal"}
call_bpc(t2, user_text="bolte jao", engine=e2, detail={"turns_left": 0})
check("'bolte jao' -> continue_detail (approved fix ②)", t2["policy"]["delivery"], "continue_detail")
check("'bolte jao' advances the step", e2["detail"]["step"], 3)
e3 = mk_engine()
e3["detail"] = {"active": True, "step": 2, "last_chunk": "kuch", "resume": None}
t3 = {"turn": 2, "turn_type": "speech", "route_action": "normal"}
call_bpc(t3, user_text="roko mat, bolte raho", engine=e3, detail={"turns_left": 0})
check("'roko mat' -> continue_detail (approved fix ②)", t3["policy"]["delivery"], "continue_detail")

print("== ③ continuation turn through run_turn ==")
e2 = mk_engine()
e2["detail"] = {"active": True, "step": 1, "last_chunk": "Pehla point.", "resume": None}
ctxA = TurnContext(turn_no=5, user_text="haan aage", is_valid=True,
                   engine=e2, recent_reply_texts=["Pehla point."],
                   detail_mode={"turns_left": 0}, stuck_nudged={"until_turn": 0},
                   model_text=("Dusra point: priorities clear karo. "
                               "Teesra point: ek-ek karke kaam karo."),
                   acoustic={"duration_ms": 900, "rms": 1, "peak": 1})
tA = run_turn(ctxA)
check("continue turn flagged", tA.get("detail_continue"), True)
check("delivery continue_detail", tA["policy"]["delivery"], "continue_detail")
check("step advanced to 2", e2["detail"]["step"], 2)
check("last_chunk updated at completion", e2["detail"]["last_chunk"], tA["llm_response"])

print()
if fails:
    print(f"FAIL ({fails})")
    sys.exit(1)
print("ALL PASS")

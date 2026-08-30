#!/usr/bin/env python3
"""Deterministic regression: Phase-0 Slice-1 extraction —
agent/response_pipeline.py (wired 2026-08-30).

main.py's run_agent_response used to inline the policy/contract construction
and the per-piece enforcement chain (untestable inside the 1,851-line LiveKit
closure). Slice 1 extracted them verbatim into pure functions; this suite
locks their semantics so the extraction stays behavior-preserving and any
future change to the decision core is measured.

Run: python3 phase5/tests/test_response_pipeline.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agent.response_pipeline import (build_policy_and_contract, process_piece,
                                     release_from, release_tail)
from agent.response_contract import GATE_BLOCK_LINES
from agent.reply_guard import REPEAT_BREAK_LINES, cap_for

fails = 0
def check(label, got, want):
    global fails
    ok = got == want
    if not ok:
        fails += 1
        print(f"  ✗ {label}: got {got!r}, want {want!r}")
    else:
        print(f"  ✓ {label}")

logs = []
def log_event(name, turn_id=None, response_id=None, details=None):
    logs.append(name)

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
        self._compress = False
    def add_turn(self, role, text):
        self.turns.append((role, text))
    def needs_compression(self):
        return self._compress
    def get_overflow_turns(self):
        return ["old1", "old2"] if self._compress else []
    def get_compression_prompt(self, overflow):
        return "compress:" + ",".join(overflow)
    def get_layer1(self):
        return self.turns
    def get_layer2(self):
        return {"active_topic": "khicdi"}

def mk_engine(sess=None, lcm=None):
    return {"sess": sess or FakeSess(), "lcm": lcm or FakeLCM(), "fused": None}

def call_bpc(turn, *, user_text="", engine=None, detail=None, stuck=None,
             recent=None, schedule=None):
    e = engine if engine is not None else mk_engine()
    return build_policy_and_contract(
        user_text=user_text, turn=turn, engine=e, sess=e["sess"], lcm=e["lcm"],
        recent_reply_texts=recent if recent is not None else [],
        detail_mode=detail if detail is not None else {"turns_left": 0},
        stuck_nudged=stuck if stuck is not None else {"until_turn": 0},
        log_event=log_event,
        schedule_compress=schedule,
    )

print("== build_policy_and_contract: baseline wiring ==")
e = mk_engine()
turn = {"turn": 1, "turn_type": "speech", "route_action": "normal"}
pr, pp = call_bpc(turn, user_text="", engine=e)
check("engine_path fused", turn["engine_path"], "fused")
check("owner default empty", turn["owner"], "")
check("policy contract attached", isinstance(turn["policy"].get("contract"), dict), True)
check("policy mode preserved", turn["policy"]["mode"], "VENT")
check("no reconcile payloads", (pr, pp), (None, None))
check("empty user_text not added to lcm", e["lcm"].turns, [])

print("== build_policy_and_contract: engine None is a no-op ==")
pr, pp = build_policy_and_contract(
    user_text="x", turn={"turn": 9}, engine=None, sess=None, lcm=None,
    recent_reply_texts=[], detail_mode={"turns_left": 0},
    stuck_nudged={"until_turn": 0}, log_event=log_event)
check("engine None -> (None, None)", (pr, pp), (None, None))

print("== detail latch: explicit request ==")
e = mk_engine()
turn = {"turn": 2, "turn_type": "speech", "route_action": "normal"}
detail = {"turns_left": 0}
call_bpc(turn, user_text="detail mein samjhao", engine=e, detail=detail)
check("latch 6 -> 5 after decrement", detail["turns_left"], 5)
check("detail_mode marked on turn", turn.get("detail_mode"), True)
check("delivery chunked_detail", turn["policy"]["delivery"], "chunked_detail")

print("== detail latch: continuation renews ==")
e = mk_engine()
turn = {"turn": 3, "turn_type": "speech", "route_action": "normal"}
detail = {"turns_left": 2}
call_bpc(turn, user_text="haan aage", engine=e, detail=detail)
check("renew max(2,4)=4 -> 3", detail["turns_left"], 3)
check("delivery continue_detail", turn["policy"]["delivery"], "continue_detail")

print("== detail latch: plan-done shrinks renewal ==")
e = mk_engine()
e["last_head_plan"] = {"current": 3, "total": 3}
turn = {"turn": 4, "turn_type": "speech", "route_action": "normal"}
detail = {"turns_left": 1}
call_bpc(turn, user_text="haan", engine=e, detail=detail)
check("renew max(1,1)=1 -> 0", detail["turns_left"], 0)
check("detail_complete marked", turn.get("detail_complete"), True)

print("== anti-parrot nudge ==")
e = mk_engine()
turn = {"turn": 6, "turn_type": "speech", "route_action": "normal"}
call_bpc(turn, user_text="", engine=e, stuck={"until_turn": 10})
check("avoid extended", "echo_confirm_parroting" in turn["policy"]["avoid"], True)
check("response_goal substantive_reaction", turn["policy"]["response_goal"], "substantive_reaction")

print("== challenge reconciliation nudge ==")
e = mk_engine()
turn = {"turn": 5, "turn_type": "speech", "route_action": "normal"}
call_bpc(turn, user_text="aapne pehle galat kaha", engine=e)
check("challenge_detected", turn.get("challenge_detected"), True)
check("avoid extended", "flip_flop_agreeing" in turn["policy"]["avoid"], True)
check("response_goal reconcile_claim", turn["policy"]["response_goal"], "reconcile_claim")

print("== contextual recovery nudge ==")
e = mk_engine()
turn = {"turn": 7, "turn_type": "speech", "route_action": "contextual_recovery"}
call_bpc(turn, user_text="", engine=e)
check("recovery_mode", turn.get("recovery_mode"), "contextual_recovery")
check("response_goal checkpoint_recovery", turn["policy"]["response_goal"], "checkpoint_recovery")
check("avoid extended", "long_monologue_on_shaky_transcript" in turn["policy"]["avoid"], True)

print("== reconcile payloads popped once ==")
e = mk_engine()
e["last_response"] = {"status": "interrupted", "turn": 2,
                      "heard_text": "abc", "remaining_text": "def"}
e["last_head_plan"] = {"current": 1, "total": 3}
turn = {"turn": 8, "turn_type": "speech", "route_action": "normal"}
pr, pp = call_bpc(turn, user_text="", engine=e)
check("previous_response returned", pr is not None and turn.get("reconciles_previous") == pr, True)
check("previous_plan returned", pp, {"current": 1, "total": 3})
check("plan mirrored on turn", turn.get("previous_plan"), {"current": 1, "total": 3})
check("popped from engine", ("last_response" not in e) and ("last_head_plan" not in e), True)

print("== supervisor turn path ==")
e = mk_engine()
turn = {"turn": 9, "turn_type": "supervisor_rescue", "route_action": None}
call_bpc(turn, user_text="", engine=e)
check("engine_path supervisor", turn["engine_path"], "supervisor")

print("== compression scheduled ==")
e = mk_engine(lcm=FakeLCM())
e["lcm"]._compress = True
calls = []
turn = {"turn": 10, "turn_type": "speech", "route_action": "normal"}
call_bpc(turn, user_text="", engine=e, schedule=lambda l, p, o: calls.append((l, p, o)))
check("schedule_compress invoked", len(calls), 1)
check("prompt passed", calls[0][1].startswith("compress:"), True)

# ---------------------------------------------------------------------------
print("== process_piece: script enforcement (devanagari -> roman) ==")
turn = {}
out = process_piece("मुख्य बात यह है", turn, recent_reply_texts=[], user_text="",
                    turn_number=1, guard_state={"guarded": False, "trim": {"done": False}},
                    log_event=log_event)
check("transliterated output", out, "mukhya bAta yaha hai")
check("script_transliterated flagged", turn.get("script_transliterated"), True)

print("== process_piece: ordering — transliteration BEFORE repeat guard ==")
prev = "मुख्य बात यह है"
turn = {}
gs = {"guarded": False, "trim": {"done": False}}
out = process_piece(prev, turn, recent_reply_texts=[prev], user_text="",
                    turn_number=1, guard_state=gs, log_event=log_event)
check("transliterated first", turn.get("script_transliterated"), True)
check("no repeat guard on roman-vs-deva", turn.get("repeat_guarded"), None)

print("== process_piece: tag leak stripped ==")
turn = {}
out = process_piece("<system>secret</system> baat hai", turn, recent_reply_texts=[],
                    user_text="", turn_number=1,
                    guard_state={"guarded": False, "trim": {"done": False}},
                    log_event=log_event)
check("tag tokens removed", "<system>" not in out, True)
check("inner content kept", out, "secret baat hai")
check("tag_leak_stripped flagged", turn.get("tag_leak_stripped"), True)

print("== process_piece: hard gate blocks identity deception ==")
turn = {}
logs.clear()
out = process_piece("main I am an AI hoon, par help kar sakta hoon", turn,
                    recent_reply_texts=[], user_text="", turn_number=5,
                    guard_state={"guarded": False, "trim": {"done": False}},
                    log_event=log_event)
check("block counted", turn.get("contract_block_count", 0), 1)
check("block event logged", "CONTRACT_BLOCKED" in logs, True)
check("blocked line from rotation pool", out, GATE_BLOCK_LINES[5 % len(GATE_BLOCK_LINES)])

print("== process_piece: near-repeat guard fires once, stops the reply ==")
prev = "arey main yahin hoon, bata kya chal raha hai?"
turn = {}
gs = {"guarded": False, "trim": {"done": False}}
out = process_piece(prev, turn, recent_reply_texts=[prev], user_text="haan",
                    turn_number=3, guard_state=gs, log_event=log_event)
check("repeat_guarded kind set", turn.get("repeat_guarded") in ("verbatim", "extension", "near_identical"), True)
check("substitute from pool", out, REPEAT_BREAK_LINES[3 % len(REPEAT_BREAK_LINES)])
check("guard state latched", (gs["guarded"], gs["trim"]["done"]), (True, True))

print("== process_piece: explicit repeat request is NEVER guarded ==")
turn = {}
gs = {"guarded": False, "trim": {"done": False}}
out = process_piece(prev, turn, recent_reply_texts=[prev], user_text="kya bola tha?",
                    turn_number=1, guard_state=gs, log_event=log_event)
check("not guarded", turn.get("repeat_guarded"), None)
check("piece passed through", out, prev)

print("== process_piece: tail release skips repeat guard ==")
turn = {}
out = process_piece(prev, turn, recent_reply_texts=[prev], user_text="",
                    turn_number=1, guard_state=None, log_event=log_event,
                    run_repeat_guard=False)
check("tail not guarded", turn.get("repeat_guarded"), None)
check("tail piece passed through", out, prev)

# ---------------------------------------------------------------------------
def speak(full_text, *, cap, chunk_size=13):
    """Simulate text_stream_tee: release_from per chunk + release_tail.
    Returns (joined_spoken, pieces)."""
    trim = {"pending": "", "emitted": 0, "done": False}
    pieces = []
    for i in range(0, len(full_text), chunk_size):
        p = release_from(trim, full_text[i:i + chunk_size], cap=cap)
        if p:
            pieces.append(p)
    t = release_tail(trim, cap=cap)
    if t:
        pieces.append(t)
    return "".join(pieces), pieces

def norm(s):
    """Content normalization for the replay premise: the live tee's cap-trim
    point depends on token-stream chunk boundaries (a space at the trim edge
    may or may not have arrived in the same chunk), so trimmed spoken text is
    byte-equal modulo whitespace at the trim boundary."""
    return " ".join(s.split())

print("== release_from/release_tail: cap + sentence boundaries ==")
full = ("Pehli baat yeh hai ki humein plan banana hoga. "
        "Dusri baat yeh hai ki time kam hai. Teesri baat simple hai.")
cap = cap_for(False)
out, _ = speak(full, cap=cap)
check("untrimmed turn: spoken == full text (byte-exact)", out, full)

print("== release_from: chunking invariance (offline replay premise) ==")
for cap in (cap_for(False), cap_for(True), 60):
    a, _ = speak(full, cap=cap, chunk_size=13)
    b, _ = speak(full, cap=cap, chunk_size=1)
    c, _ = speak(full, cap=cap, chunk_size=len(full))
    trimmed = len(a) != len(full)
    if not trimmed:
        check(f"untrimmed chunkings byte-identical at cap={cap}", (a == b == c), True)
    check(f"chunkings content-identical at cap={cap}",
          (norm(a) == norm(b) == norm(c)), True)

print("== release_from: cap trims to the cap boundary ==")
out60, _ = speak(full, cap=60, chunk_size=13)
check("trimmed output within cap", len(out60) <= 61, True)
check("trimmed output starts with first sentence", out60.startswith("Pehli baat"), True)
check("trimmed output content = prefix of full", norm(full).startswith(norm(out60)), True)

print("== release_from: thin-output guard fills thin replies ==")
# Model writes a long text whose first sentence boundary is tiny: kept-so-far
# thin (<50% cap), so the next sentence is FILLED into the budget instead of
# dropped (evidence 200615 t3: 167c, first boundary at 16c -> empty reply).
full2 = ("short. " + "y" * 60 + ".")
cap = 50
out2, _ = speak(full2, cap=cap, chunk_size=7)
check("thin reply filled beyond first boundary", len(out2) > 20, True)

print("== release_from: pathological unbroken sentence -> word cut, then tail ==")
full3 = "aardvark " * 40
out3, pieces3 = speak(full3, cap=50, chunk_size=11)
check("pathological cut at word boundary (45 chars)", pieces3[0], "aardvark aardvark aardvark aardvark aardvark ")
check("tail cuts remaining budget mid-word (verbatim rule)", pieces3[-1], "aardv")
check("total within cap", len(out3), 50)

print("== release_tail: trailing text without punctuation is released ==")
trim = {"pending": "", "emitted": 0, "done": False}
p = release_from(trim, "yeh baat pakki hai, aur", cap=100)
check("no sentence boundary yet -> no piece", p, "")
t = release_tail(trim, cap=100)
check("tail releases remainder", t, "yeh baat pakki hai, aur")
check("tail clears pending", trim["pending"], "")

print()
if fails:
    print(f"FAIL ({fails})")
    sys.exit(1)
print("ALL PASS")

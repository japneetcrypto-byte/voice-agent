#!/usr/bin/env python3
"""Phase-0 replay-gate regression: phase5/harness/replay.py (Slice 1).

Builds a SYNTHETIC archived session by running the extracted core EXACTLY the
way main.py wires it (routing -> policy/contract -> release -> enforcement),
writes it to phase5/harness/fixtures/synthetic_slice1/, then runs the replay
gate over it and requires an EMPTY diff (identity). A negative control then
corrupts one field and requires the gate to flag it — proving the gate detects
drift rather than passing vacuously.

The synthetic fixture is a placeholder for the owner's baseline archives
(§5 protocol): when real session_*.log files land in fixtures/, the same gate
must come back EMPTY — that is the Phase-0 replay-identity acceptance.

Run: python3 phase5/harness/test_replay_identity.py
"""
import sys, os, json
from collections import deque
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agent.response_pipeline import (build_policy_and_contract, process_piece,
                                     release_from, release_tail)
from agent.turn_router import route_decision
from agent.reply_guard import cap_for, PLAN_CHUNK_CAP, remaining_text
from agent.response_state import reconcile_payload, FULLY_PLAYED, PARTIALLY_PLAYED
from agent.stt_validation import is_repetition_loop
from phase5.harness.replay import replay_session

fails = 0
def check(label, got, want):
    global fails
    ok = got == want
    if not ok:
        fails += 1
        print(f"  ✗ {label}: got {got!r}, want {want!r}")
    else:
        print(f"  ✓ {label}")

# ---------------------------------------------------------------------------
# Fakes (mirror the live runtime shapes the extraction talks to)
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
        return {"active_topic": None}

# ---------------------------------------------------------------------------
# Session simulation — mirrors main.py's wiring of the extracted core
# ---------------------------------------------------------------------------
class LiveSim:
    """Tracks the runtime state main.py kept in its closure, so the synthetic
    archive is produced by the SAME derivation the replay uses."""
    def __init__(self):
        self.engine = {"sess": FakeSess(), "lcm": FakeLCM(), "fused": None}
        self.detail_mode = {"turns_left": 0}
        self.stuck = {"until_turn": 0}
        self.recent_reply_texts = deque(maxlen=3)
        self.reply_shapes = deque(maxlen=4)
        self.turn_no = 0

    def respond(self, turn, user_text):
        """Mirror run_agent_response's deterministic slice (engine-bound path)."""
        turn["engine_path"] = "fused"
        sess = self.engine["sess"]
        lcm = self.engine["lcm"]
        _prev_response, _prev_plan = build_policy_and_contract(
            user_text=user_text, turn=turn, engine=self.engine, sess=sess, lcm=lcm,
            recent_reply_texts=list(self.recent_reply_texts),
            detail_mode=self.detail_mode, stuck_nudged=self.stuck,
            log_event=lambda *a, **k: None,
            owner_id_fn=lambda: "owner-1234",
            schedule_compress=None)
        return _prev_response, _prev_plan

    def speak(self, turn, user_text, full_text):
        """Mirror text_stream_tee's deterministic slice: caps -> release ->
        enforcement. Returns the spoken text + fills the enforcement flags."""
        caps = {"cap": cap_for(self.detail_mode["turns_left"] > 0)}
        if turn.get("route_action") == "contextual_recovery":
            caps["cap"] = min(caps["cap"], 110)
        plan = turn.get("head_plan")
        if isinstance(plan, dict) and isinstance(plan.get("total"), int) and plan["total"] > 1:
            caps["cap"] = max(caps["cap"], PLAN_CHUNK_CAP)
        trim = {"pending": "", "emitted": 0, "done": False}
        guard_state = {"guarded": False, "trim": trim}
        pieces = []
        for i in range(0, len(full_text), 17):
            p = release_from(trim, full_text[i:i + 17], cap=caps["cap"])
            if p:
                pieces.append(p)
        tail_trimmed = False
        if not trim["done"] and trim["pending"]:
            if trim["emitted"] + len(trim["pending"]) > caps["cap"] and trim["emitted"] > 0:
                tail_trimmed = True
            t = release_tail(trim, cap=caps["cap"])
            if t:
                pieces.append(t)
        spoken = []
        for p in pieces:
            spoken.append(process_piece(
                p, turn,
                recent_reply_texts=list(self.recent_reply_texts),
                user_text=user_text,
                turn_number=turn["turn"],
                guard_state=guard_state,
                log_event=lambda *a, **k: None))
        turn["llm_response_full"] = full_text
        turn["llm_response"] = "".join(spoken)
        if trim["done"] or tail_trimmed:
            turn["reply_trimmed"] = True
        return turn["llm_response"]

    def turn(self, *, user_text, full_text=None, is_valid=True,
             rejection_reason=None, avg_logprob=-0.2, agent_was_speaking=False,
             engine_bound=True, interrupted=False, head_plan=None):
        """One full user turn -> archived turn dict (the archive format main.py
        log_turn()s: session_*.log, one JSON dict per line)."""
        self.turn_no += 1
        n = self.turn_no
        turn = {
            "turn": n,
            "turn_type": "speech",
            "acoustic": {"duration_ms": 900.0, "rms": 4000.0, "peak": 12000},
            "stt_transcript": user_text,
            "stt_valid": is_valid,
            "stt_rejection_reason": rejection_reason,
            "stt_avg_logprob": avg_logprob,
            "agent_was_speaking": agent_was_speaking,
            "response_state": None,
        }
        if head_plan:
            turn["head_plan"] = head_plan

        r = route_decision(
            transcript_text=user_text, is_valid=is_valid,
            rejection_reason=rejection_reason, avg_logprob=avg_logprob,
            is_repetition=is_repetition_loop(user_text),
            is_catastrophic=(rejection_reason == "catastrophic_low_confidence"),
            agent_was_speaking=agent_was_speaking, engine_bound=engine_bound)
        turn["route_action"] = r["action"]
        turn["route_reason"] = r["reason"]

        if r["drop"]:
            turn["dropped_reason"] = r["drop_reason"]
            self.engine.pop("last_response", None)
            self.engine.pop("last_head_plan", None)
            return turn  # never reached run_agent_response

        if r["respond_now"]:
            turn["turn_type"] = r["turn_type"]
            turn["response_trigger_reason"] = r["trigger"]

        if r["recovery"]:
            turn["recovery_mode"] = "contextual_recovery"

        # ---- response path (engine bound) ----
        self.respond(turn, user_text)
        if full_text is None:
            full_text = ("" if user_text == "" else user_text) or "achha main samajh gaya hoon. "
        if not is_valid and turn.get("route_action") == "contextual_recovery":
            full_text = "Samajh gaya. Chaliye, jahan chhoda tha wahan se continue karte hain."
        spoken = self.speak(turn, user_text, full_text)

        if interrupted:
            # CancelledError path: partial playback recorded.
            turn["interrupted"] = True
            turn["response_state"] = PARTIALLY_PLAYED
            _remainder = remaining_text(full_text, spoken)
            turn["heard_text"] = spoken[:200]
            turn["remaining_text"] = _remainder[:300]
            self.engine["last_response"] = {
                "status": PARTIALLY_PLAYED, "turn": n,
                "heard_text": spoken, "remaining_text": _remainder}
            turn["response_trigger_reason"] = turn.get("response_trigger_reason") or "user_speech_ended"
        else:
            turn["response_state"] = FULLY_PLAYED
            turn["response_trigger_reason"] = "completed"
            self.engine["last_response"] = {
                "status": FULLY_PLAYED, "turn": n, "heard_text": spoken}
            if head_plan:
                self.engine["last_head_plan"] = head_plan

        self.recent_reply_texts.append(spoken)
        return turn


def build_session() -> list[dict]:
    sim = LiveSim()
    turns = []

    # t1 greeting (normal)
    turns.append(sim.turn(user_text="namaste bhaiya",
                          full_text="Namaste! Batao aaj ka din kaisa raha?"))
    # t2 vent (normal)
    turns.append(sim.turn(user_text="aaj bahut din kharab tha, office me sab kuch galat ho gaya",
                          full_text=("Sun raha hoon, batao kya hua. " +
                                     "Pehle bolna thoda aaram se, main yahin hoon.")))
    # t3 detail request (detail latch -> chunked_detail)
    turns.append(sim.turn(user_text="detail mein samjhao",
                          full_text=("Pehla point yeh hai ki plan banana zaroori hai. " +
                                     "Dusra point yeh hai ki time management chahiye.")))
    # t4 continuation (renewal -> continue_detail)
    turns.append(sim.turn(user_text="haan aage",
                          full_text="Teesra point: priorities clear karo. "
                                    "Chautha point: ek-ek karke kaam karo."))
    # t5 dropped echo (invalid + agent speaking -> never a substantive reply)
    turns.append(sim.turn(user_text="", is_valid=False,
                          rejection_reason="empty_transcript",
                          agent_was_speaking=True))
    # t6 acoustic_only presence (bound, not speaking -> presence line)
    turns.append(sim.turn(user_text="", is_valid=False,
                          rejection_reason="empty_transcript",
                          agent_was_speaking=False,
                          full_text="Haan bolo, main sun raha hoon."))
    # t7 clarify (repetition loop -> unclear_stt_clarify)
    turns.append(sim.turn(user_text="haan haan haan haan haan", is_valid=False,
                          rejection_reason="repetition_loop",
                          avg_logprob=-0.9,
                          full_text="Kya kaha? Zara clearly bolo na."))
    # t8 interrupted vent (partial playback -> reconcile payload next turn)
    turns.append(sim.turn(user_text="mere saath bahut bura hua aaj",
                          full_text=("Kya hua? Batao, main sun raha hoon. " +
                                     "Aaram se batao, kahin jaldi nahi hai."),
                          interrupted=True))
    # t9 post-interruption (reconciles_previous + previous... )
    turns.append(sim.turn(user_text="tumne kaha tha kya hua, to main bata raha hoon",
                          full_text="Haan bolo, main pichli baat se juda hua hoon. "
                                    "Aage batao kya hua."))
    # t10 challenge nudge
    turns.append(sim.turn(user_text="aapne pehle galat kaha",
                          full_text="Theek hai, mujhe check karne do. "
                                    "Jo maine pehle kaha, usme galatfahmi ho sakti hai."))
    # t11 recovery (invalid but meaningful -> bounded recovery)
    turns.append(sim.turn(user_text="maine aapko pehle bataya tha office wali baat",
                          is_valid=False, rejection_reason="low_logprob",
                          avg_logprob=-0.4,
                          full_text="Samajh gaya. Wahan se continue karte hain jahan chhoda tha."))
    # t12 near-repeat guard fires (model repeats its previous reply)
    prev = turns[-1]["llm_response"]
    turns.append(sim.turn(user_text="achha",
                          full_text=prev))
    # t13 contract hard-block (identity deception in the model text)
    turns.append(sim.turn(user_text="tum kya ho",
                          full_text="main I am an AI hoon, par help kar sakta hoon. "
                                    "Aur main hamesha yahin hoon."))
    # t14 long reply over cap (reply_trimmed)
    sim.detail_mode["turns_left"] = 3
    turns.append(sim.turn(user_text="poora plan batao",
                          full_text=("Point ek: subah utho. Point do: plan banao. " +
                                     "Point teen: kaam karo. Point chaar: review karo. " +
                                     "Point paanch: aaram karo. Point chhe: so jao.") * 4))
    # t15 anti-parrot window (nudge shape lands in the archived policy)
    sim.stuck["until_turn"] = 99
    sim.reply_shapes.clear()
    turns.append(sim.turn(user_text="achha theek hai",
                          full_text="Achha. Batao, aaj aur kya kiya?"))
    return turns


def write_fixture(turns, path: Path):
    path.mkdir(parents=True, exist_ok=True)
    session = path / "session_0001.log"
    with open(session, "w", encoding="utf-8") as f:
        for t in turns:
            f.write(json.dumps(t, ensure_ascii=False) + "\n")
    n = len(turns)
    interrupted = sum(1 for t in turns if t.get("interrupted"))
    rejected = sum(1 for t in turns if t.get("stt_valid") is False)
    manifest = {
        "fixture": "synthetic_slice1",
        "note": "SYNTHETIC placeholder — generated by phase5/harness/"
                "test_replay_identity.py from the extracted core itself. "
                "Owner baseline archives (§5 protocol) replace this; the "
                "replay gate must come back EMPTY on those too.",
        "session": session.name,
        "turns": n,
        "numbers": {
            "speech_to_first_audio_avg_s": None,   # not measurable offline
            "interruption_rate": round(interrupted / n, 3),
            "stt_rejection_rate": round(rejected / n, 3),
            "provider_incidents": 0,
        },
    }
    with open(path / "manifest.json", "w", encoding="utf-8") as f:
        f.write(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    return session


# ---------------------------------------------------------------------------
print("== build synthetic archived session (via the extracted core) ==")
fixture_dir = Path(__file__).resolve().parent / "fixtures" / "synthetic_slice1"
turns = build_session()
session_path = write_fixture(turns, fixture_dir)
check("fixture written", session_path.is_file(), True)
check("fixture covers the decision classes",
      [t.get("route_action") for t in turns].count("normal") >= 8
      and any(t.get("dropped_reason") for t in turns)
      and any(t.get("turn_type") == "acoustic_only" for t in turns)
      and any(t.get("turn_type") == "unclear_speech" for t in turns)
      and any(t.get("route_action") == "contextual_recovery" for t in turns), True)

print("== replay gate: identity must hold on the synthetic session ==")
diffs, checked, skipped = replay_session(session_path)
check("all turns checked", checked, len(turns))
check("no supervisor/idle turns", skipped, 0)
check("EMPTY DIFF (identity)", diffs, {})

print("== negative control: a corrupted field MUST be flagged ==")
corrupt = fixture_dir / "_corrupt_control.log"
with open(corrupt, "w", encoding="utf-8") as f:
    for t in turns:
        t2 = dict(t)
        if t2["turn"] == 1:
            t2["route_action"] = "clarify"  # wrong decision
            t2["llm_response"] = "wrong spoken text entirely"
        f.write(json.dumps(t2, ensure_ascii=False) + "\n")
d2, _, _ = replay_session(corrupt)
check("route_action drift detected", "route_action" in d2.get(1, {}), True)
check("llm_response drift detected", "llm_response" in d2.get(1, {}), True)
corrupt.unlink()

print()
if fails:
    print(f"FAIL ({fails})")
    sys.exit(1)
print("ALL PASS")

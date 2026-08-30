#!/usr/bin/env python3
"""Phase-0 replay-gate regression: phase5/harness/replay.py (Slice 1).

Builds a SYNTHETIC archived session by executing the approved §6 interface
run_turn(context) -> turn_dict exactly the way the harness reconstructs
contexts, writes it to phase5/harness/fixtures/synthetic_slice1/, then runs
the replay gate over it and requires an EMPTY diff (identity). A negative
control then corrupts one field and requires the gate to flag it — proving
the gate detects drift rather than passing vacuously.

IMPORTANT scope note (Phase-0 review 2026-08-30): this is a self-consistency
check of the extracted core. Phase-0 replay identity is NOT proven until the
OWNER's baseline logs (frozen commit e0dc60f, §5 protocol) replay with an
empty diff. When those arrive, drop them in fixtures/ and run the same gate.

Run: python3 phase5/harness/test_replay_identity.py
"""
import sys, os, json
from collections import deque
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agent.response_pipeline import run_turn, TurnContext
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
# Session simulation — every turn runs through run_turn (the §6 interface)
# ---------------------------------------------------------------------------
class Sim:
    """The live closure state run_turn mutates, carried across turns the way
    main.py carries its closure dicts."""
    def __init__(self):
        self.engine = {"sess": FakeSess(), "lcm": FakeLCM(), "fused": None}
        self.detail_mode = {"turns_left": 0}
        self.stuck = {"until_turn": 0}
        self.recent = deque(maxlen=3)
        self.turn_no = 0

    def run(self, *, user_text, model_text=None, is_valid=True,
            rejection_reason=None, avg_logprob=-0.2, agent_was_speaking=False,
            interrupted=False, head_plan=None, engine_bound=None):
        self.turn_no += 1
        if engine_bound is None:
            engine_bound = bool(self.engine.get("sess"))
        if model_text is None:
            model_text = (user_text or "achha main samajh gaya hoon. ")
        ctx = TurnContext(
            turn_no=self.turn_no, user_text=user_text, is_valid=is_valid,
            rejection_reason=rejection_reason, avg_logprob=avg_logprob,
            agent_was_speaking=agent_was_speaking, engine_bound=engine_bound,
            engine=self.engine, recent_reply_texts=list(self.recent),
            detail_mode=self.detail_mode, stuck_nudged=self.stuck,
            model_text=model_text, head_plan=head_plan,
            interrupted=interrupted, played_any_audio=not interrupted,
            acoustic={"duration_ms": 900.0, "rms": 4000.0, "peak": 12000},
            user_speech_start="2026-08-30T00:00:00+00:00",
            user_speech_end="2026-08-30T00:00:00+00:00",
        )
        turn = run_turn(ctx)
        if turn.get("llm_response"):
            self.recent.append(turn["llm_response"])
        return turn


def build_session() -> list[dict]:
    sim = Sim()
    turns = []

    # t1 greeting (normal)
    turns.append(sim.run(user_text="namaste bhaiya",
                         model_text="Namaste! Batao aaj ka din kaisa raha?"))
    # t2 vent (normal)
    turns.append(sim.run(user_text="aaj bahut din kharab tha, office me sab kuch galat ho gaya",
                         model_text=("Sun raha hoon, batao kya hua. " +
                                     "Pehle bolna thoda aaram se, main yahin hoon.")))
    # t3 detail request (detail latch -> chunked_detail)
    turns.append(sim.run(user_text="detail mein samjhao",
                         model_text=("Pehla point yeh hai ki plan banana zaroori hai. " +
                                     "Dusra point yeh hai ki time management chahiye.")))
    # t4 continuation (renewal -> continue_detail)
    turns.append(sim.run(user_text="haan aage",
                         model_text="Teesra point: priorities clear karo. "
                                    "Chautha point: ek-ek karke kaam karo."))
    # t5 dropped echo (invalid + agent speaking -> never a substantive reply)
    turns.append(sim.run(user_text="", is_valid=False,
                         rejection_reason="empty_transcript",
                         agent_was_speaking=True))
    # t6 acoustic_only presence (bound, not speaking -> presence line)
    turns.append(sim.run(user_text="", is_valid=False,
                         rejection_reason="empty_transcript",
                         agent_was_speaking=False,
                         model_text="Haan bolo, main sun raha hoon."))
    # t7 clarify (repetition loop -> unclear_stt_clarify)
    turns.append(sim.run(user_text="haan haan haan haan haan", is_valid=False,
                         rejection_reason="repetition_loop",
                         avg_logprob=-0.9,
                         model_text="Kya kaha? Zara clearly bolo na."))
    # t8 interrupted vent (partial playback -> reconcile payload next turn)
    turns.append(sim.run(user_text="mere saath bahut bura hua aaj",
                         model_text=("Kya hua? Batao, main sun raha hoon. " +
                                     "Aaram se batao, kahin jaldi nahi hai."),
                         interrupted=True))
    # t9 post-interruption (reconciles_previous + previous_plan)
    turns.append(sim.run(user_text="tumne kaha tha kya hua, to main bata raha hoon",
                         model_text="Haan bolo, main pichli baat se juda hua hoon. "
                                    "Aage batao kya hua."))
    # t10 challenge nudge
    turns.append(sim.run(user_text="aapne pehle galat kaha",
                         model_text="Theek hai, mujhe check karne do. "
                                    "Jo maine pehle kaha, usme galatfahmi ho sakti hai."))
    # t11 recovery (invalid but meaningful -> bounded recovery)
    turns.append(sim.run(user_text="maine aapko pehle bataya tha office wali baat",
                         is_valid=False, rejection_reason="low_logprob",
                         avg_logprob=-0.4,
                         model_text="Samajh gaya. Wahan se continue karte hain jahan chhoda tha."))
    # t12 near-repeat guard fires (model repeats its previous reply)
    prev = turns[-1]["llm_response"]
    turns.append(sim.run(user_text="achha", model_text=prev))
    # t13 contract hard-block (identity deception in the model text)
    turns.append(sim.run(user_text="tum kya ho",
                         model_text="main I am an AI hoon, par help kar sakta hoon. "
                                    "Aur main hamesha yahin hoon."))
    # t14 long reply over cap (reply_trimmed)
    sim.detail_mode["turns_left"] = 3
    turns.append(sim.run(user_text="poora plan batao",
                         model_text=("Point ek: subah utho. Point do: plan banao. " +
                                     "Point teen: kaam karo. Point chaar: review karo. " +
                                     "Point paanch: aaram karo. Point chhe: so jao.") * 4))
    # t15 anti-parrot window (nudge shape lands in the archived policy)
    sim.stuck["until_turn"] = 99
    turns.append(sim.run(user_text="achha theek hai",
                         model_text="Achha. Batao, aaj aur kya kiya?"))
    # t16 head plan (A-P1: plan >1 chunks -> chunk cap lift + plan carry)
    turns.append(sim.run(user_text="aur batao",
                         model_text="Pehla chunk yahin samapt hota hai. ",
                         head_plan={"current": 1, "total": 3}))
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
                "test_replay_identity.py via run_turn (the §6 interface). "
                "Self-consistency check, NOT preservation proof: Phase-0 "
                "replay identity is proven only when OWNER baseline logs "
                "(frozen commit e0dc60f, §5 protocol) replay EMPTY. "
                "Owner archives replace this fixture.",
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
print("== build synthetic archived session (via run_turn — the §6 interface) ==")
fixture_dir = Path(__file__).resolve().parent / "fixtures" / "synthetic_slice1"
turns = build_session()
session_path = write_fixture(turns, fixture_dir)
check("fixture written", session_path.is_file(), True)
check("fixture covers the decision classes",
      any(t.get("dropped_reason") for t in turns)
      and any(t.get("turn_type") == "acoustic_only" for t in turns)
      and any(t.get("turn_type") == "unclear_speech" for t in turns)
      and any(t.get("route_action") == "contextual_recovery" for t in turns)
      and any(t.get("interrupted") for t in turns)
      and any(t.get("repeat_guarded") for t in turns)
      and any(t.get("contract_block_count") for t in turns)
      and any(t.get("reply_trimmed") for t in turns)
      and any(t.get("head_plan") for t in turns), True)

print("== replay gate: identity must hold on the synthetic session ==")
diffs, checked, skipped = replay_session(session_path)
check("all turns checked", checked, len(turns))
check("no skipped turns", skipped, 0)
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

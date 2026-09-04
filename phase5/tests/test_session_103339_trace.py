"""session_20260903_103339 t5→t25 — the owner's 7 acceptance properties
(docs/VALUE_TRANSACTION_LOCK.md §8 / owner ruling 2026-09-04).

COVERAGE KIND — read this before trusting the result:
  * FIXTURE coverage: this test REBUILDS the t3→t25 session through run_turn
    (the replay-identity interface) from the STT texts and playback facts
    recorded in the lock §0 table, and writes it as the first RAIL replay
    fixture: phase5/harness/fixtures/session_103339_rail/ (JSONL). The raw
    live log is not in the repo, so the fixture is a RECONSTRUCTION —
    identical user texts, turn numbers and playback outcomes (t8 echo
    cancelled pre-audio, t16 barged), NOT a byte-copy of the live archive.
    The replay gate then proves the archive replays to identity.
  * UNIT/INVARIANT coverage: the 7 properties below are asserted directly on
    the rebuilt turns and on the controller state, independent of the gate.

Properties (owner's words → what is asserted):
  1. t8 never mutates the stored value            → base after t8 == base after t5
  2. t8+t9 compose into the intended replacement  → one proposal, derived == 02690012000005203
  3. an unheard/cancelled echo can never become    → t8's hold + a cancelled echo keep delivery
     confirmable                                    != spoken; 'haan' after an unheard echo re-speaks
  4. no mutation commits without proposal +        → base changes exactly once, on a confirm_ack
     delivery + explicit confirmation                 whose proposal was marked spoken
  5. t15–t17 cannot produce an LLM mutation claim  → no fused turn among t15-t17; the fabricated
     — the correct outcome is clarification           line is blocked if an LLM ever emits it
  6. a newer authoritative turn supersedes an      → response_supersession decision at the
     older in-flight response before the boundary    boundary == 'supersede' (t17 vs rescue #1002)
  7. existing non-rail behaviour unchanged except  → the pre-lock fixtures replay to identity;
     the declared §H pin                              the §H pin is the declared change

Run: python3 phase5/tests/test_session_103339_trace.py
"""
import sys, os, json, re
from pathlib import Path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agent.response_pipeline import run_turn, TurnContext
from agent.response_state import FULLY_PLAYED, UNHEARD, PARTIALLY_PLAYED
from agent.response_contract import check_violations
from agent.response_supersession import decide_at_boundary
from agent.value_transaction import SPOKEN
from agent.precision_rail import speak_value
from phase5.harness.replay import replay_session

fails = 0
def check(label, got, want):
    global fails
    if got == want:
        print(f"  ✓ {label}")
    else:
        fails += 1
        print(f"  ✗ {label}: got {got!r}, want {want!r}")

ROOT = Path(__file__).resolve().parent.parent.parent
FIXTURE_DIR = ROOT / "phase5" / "harness" / "fixtures" / "session_103339_rail"

# ---------------------------------------------------------------------------
# The session as recorded (lock §0). heard: how the reply actually played live.
# ---------------------------------------------------------------------------
SESSION = [
    # (turn, user_text, playback)   playback: "played" | "cancel_pre_audio" | "barged" | None (silent)
    (3,  "यार एक नंबर लिखा तुझे बता रहा हूँ", "played"),
    (5,  "026900125205203", None),
    (7,  "बता क्या लिखा", "played"),
    (8,  "नहीं 520 नहीं है", "cancel_pre_audio"),      # user resumed 373 ms in — no audio
    (9,  "पाइप की जगह 5 बार 0 लिखना है", "played"),
    (10, "सुनातो ने?", None),
    (11, "एवा", None),
    (12, "एवा, सुना, कहाँ?", None),
    (13, "अवाँ", None),
    (14, "हेलो", "barged"),
    (15, "तुन्हें सुना मैंने चेंज बताए नंबर के अंदर", "played"),
    (16, "चेंज ये करना है कि तुन्हें अभी 5", "barged"),
    (17, "जो नंबर लगाया है ना पाइप नंबर हटेगा और उसके जगह 50 एड होंगे", "played"),
    (18, "नहीं पांच जीरो", "barged"),
    (19, "मतलब 00000 एड होगा", None),
    (20, "यह समझे", None),
    (21, "हलो", "played"),
    (22, "अरे बे चुप", None),
    (23, "बता समझा की नहीं समझा", None),
    (24, "बोल", None),
    (25, "हलो", "played"),
]
BASE_T5 = "026900125205203"
TARGET = "02690012000005203"
LIVE_T17_LLM_LINE = "theek hai, samajh gaya — 5 ki jagah 50 add kar deta hoon"

class FakeSess:
    def policy_for_turn(self): return {}
    def memory_view(self): return []
class FakeLCM:
    def add_turn(self, *a, **k): pass
    def needs_compression(self): return False
    def get_layer2(self): return {"active_topic": None}

def rebuild():
    """Run the session through run_turn with the recorded playback outcomes.
    Returns (turns, engine, base_trace) where base_trace[i] = base after turn i."""
    engine = {"sess": FakeSess(), "lcm": FakeLCM(), "fused": None}
    turns, base_trace = [], []
    for tn, text, playback in SESSION:
        interrupted = playback in ("cancel_pre_audio", "barged")
        played_any = playback in ("played", "barged")
        ctx = TurnContext(turn_no=tn, user_text=text, engine=engine,
                          model_text=LIVE_T17_LLM_LINE if tn == 17 else "achha.",
                          interrupted=interrupted, played_any_audio=played_any,
                          acoustic={"duration_ms": 900.0, "rms": 4000.0, "peak": 12000},
                          user_speech_start="2026-09-03T10:33:00+00:00",
                          user_speech_end="2026-09-03T10:33:01+00:00")
        t = run_turn(ctx)
        turns.append(t)
        dic = engine.get("dictation") or {}
        base_trace.append((tn, dic.get("value"), (dic.get("proposal") or {}).get("derived"),
                           (dic.get("proposal") or {}).get("delivery"), t.get("engine_path"),
                           (t.get("precise_detail") or {}).get("action")))
    return turns, engine, base_trace

turns, engine, trace = rebuild()
by_tn = {t["turn"]: t for t in turns}
base_after = {tn: b for tn, b, *_ in trace}
print("== rebuilt trace (turn, base, proposal.derived, delivery, path, action) ==")
for row in trace:
    print("   ", row)

print("== P1: t8 never mutates the stored value ==")
check("base after t5", base_after[5], BASE_T5)
check("base after t8 == base after t5", base_after[8], BASE_T5)
check("t8 is a spoken hold (rail), not an applied removal", (by_tn[8]["engine_path"], by_tn[8]["precise_detail"]["action"]), ("precision_rail", "hold_edit"))

print("== P2: t8+t9 compose into the intended replacement proposal ==")
check("t9 closes the instruction into ONE proposal echo", by_tn[9]["precise_detail"]["action"], "echo_confirm")
check("proposal derived == 02690012000005203", by_tn[9]["precise_detail"]["proposal"]["derived"], TARGET)
check("base still untouched after t9", base_after[9], BASE_T5)
check("t9 echo speaks the proposal digit by digit", speak_value(TARGET) in (by_tn[9].get("llm_response") or ""), True)

print("== P3: an unheard / cancelled echo can never become confirmable ==")
check("t8's reply was cancelled pre-audio in the archive", (by_tn[8].get("cancel_pre_audio"), by_tn[8].get("response_state")), (True, UNHEARD))
check("t8 produced no proposal to confirm at all (hold)", by_tn[8]["precise_detail"].get("proposal"), None)
# adversarial: had t9's echo been cancelled too, 'haan' must re-speak, not commit
_t, _e, _tr = None, {"sess": FakeSess(), "lcm": FakeLCM(), "fused": None}, None
for tn, text, playback in SESSION[:4]:
    run_turn(TurnContext(turn_no=tn, user_text=text, engine=_e, model_text="achha.",
                         interrupted=(playback == "cancel_pre_audio"), played_any_audio=(playback == "played")))
t9c = run_turn(TurnContext(turn_no=9, user_text=SESSION[4][1], engine=_e, model_text="achha.", interrupted=True, played_any_audio=False))
check("cancelled t9 echo -> proposal delivery 'unheard'", _e["dictation"]["proposal"]["delivery"], "unheard")
t10c = run_turn(TurnContext(turn_no=10, user_text="हाँ", engine=_e, model_text="achha."))
check("'haan' after the unheard echo re-speaks the full proposal; base unchanged",
      (t10c["precise_detail"]["action"], _e["dictation"]["value"]), ("echo_full", BASE_T5))
t11c = run_turn(TurnContext(turn_no=11, user_text="हाँ", engine=_e, model_text="achha."))
check("only after the heard re-speak does 'haan' commit", (t11c["precise_detail"]["action"], _e["dictation"]["value"]), ("confirm_ack", TARGET))

print("== P4: no mutation commits without proposal + delivery + explicit confirmation ==")
_i5 = [i for i, row in enumerate(trace) if row[0] == 5][0]
changes = [(row[0], row[1]) for prev, row in zip(trace[_i5:-1], trace[_i5 + 1:]) if row[1] != prev[1]]
check("in the recorded session (no 'haan' ever given) the base NEVER changes after t5", changes, [])
check("proposal is still open (unconfirmed) at t25", (engine["dictation"]["value"], engine["dictation"]["proposal"]["derived"]), (BASE_T5, TARGET))
commits = [t for t in turns if (t.get("precise_detail") or {}).get("action") == "confirm_ack"]
check("zero confirm_ack turns in the recorded session", len(commits), 0)
# the positive path: a confirm after the heard t9 echo commits exactly once
_e2 = {"sess": FakeSess(), "lcm": FakeLCM(), "fused": None}
for tn, text, playback in SESSION[:5]:
    run_turn(TurnContext(turn_no=tn, user_text=text, engine=_e2, model_text="achha.",
                         interrupted=(playback == "cancel_pre_audio"), played_any_audio=(playback == "played")))
check("t9 echo heard -> delivery spoken", _e2["dictation"]["proposal"]["delivery"], SPOKEN)
tc = run_turn(TurnContext(turn_no=10, user_text="हाँ", engine=_e2, model_text="achha."))
check("explicit 'haan' commits base <- derived exactly then", (tc["precise_detail"]["action"], _e2["dictation"]["value"], _e2["dictation"].get("proposal")), ("confirm_ack", TARGET, None))

print("== P5: t15–t17 cannot produce an LLM mutation claim — the outcome is clarification ==")
paths = {tn: by_tn[tn]["engine_path"] for tn in (15, 16, 17)}
check("t15-t17 never reach the LLM (no 'fused' path)", [p for p in paths.values() if p == "fused"], [])
check("t15 holds, t16 continues silently, t17 clarifies by re-speaking the proposal",
      [by_tn[tn]["precise_detail"]["action"] for tn in (15, 16, 17)], ["hold_edit", "silent", "clarify"])
check("t17 clarify names the proposal (what was understood)", speak_value(TARGET) in (by_tn[17].get("llm_response") or ""), True)
check("no spoken line in t3-t25 claims a mutation (claim_mutation gate)",
      [t["turn"] for t in turns if any(v["type"] == "claim_mutation" for v in check_violations(t.get("llm_response") or ""))], [])
check("the live t17 fabrication is blocked if an LLM ever emits it",
      any(v["type"] == "claim_mutation" and v["action"] == "block" for v in check_violations(LIVE_T17_LLM_LINE)), True)
# t19 "मतलब 00000 एड होगा": the EXISTING detector extracts no span from it
# against this base (no new user-speech parsing rules — owner constraint), so
# it is absorbed silently with t18's instruction still held; the held
# instruction closes at the bound (t22) by re-speaking the proposal.
check("t18 'नहीं पांच जीरो' holds (edit-intent, no wipe); t19 silent; nothing mutates",
      (by_tn[18]["precise_detail"]["action"], by_tn[19]["precise_detail"]["action"], base_after[19],
       by_tn[19]["precise_detail"]["proposal"]["derived"]), ("hold_edit", "silent", BASE_T5, TARGET))
check("t22 closes the held instruction by re-speaking the proposal (clarify), no LLM",
      (by_tn[22]["engine_path"], by_tn[22]["precise_detail"]["action"], speak_value(TARGET) in (by_tn[22].get("llm_response") or "")),
      ("precision_rail", "clarify", True))

print("== P6: a newer authoritative turn supersedes an older in-flight response before the boundary ==")
check("rescue #1002 at its first-audio boundary with t17 created since -> supersede",
      decide_at_boundary(pending_type="supervisor_rescue", agent_speaking=False,
                         user_turn_in_flight=True, newer_user_turn_since=True), "supersede")
check("t17's own reply is never displaced by the rescue",
      decide_at_boundary(pending_type="speech", agent_speaking=False, user_turn_in_flight=False, newer_user_turn_since=False), "play")

print("== P7: existing non-rail behaviour unchanged except the declared §H pin ==")
# The owner baseline archive's STANDING replay profile (unchanged since the
# greeting rail + reply cap landed; verified identical at e6f08d7 before this
# lock): t1 greeting-rail vs archived fused (3 fields), t20 reply-cap (2
# fields), t11 repeat-guard note. Anything else is a regression.
STANDING_BASELINE = {(1, "engine_path"), (1, "llm_called"), (1, "llm_response"),
                     (20, "llm_response"), (20, "reply_trimmed")}
per_turn, n, _sk = replay_session(str(ROOT / "phase5" / "harness" / "fixtures" / "baseline_20260830_1" / "session_20260830_175618.log"))
hard = {(tn, f) for tn, d in per_turn.items() for f in d if f != "notes"}
check(f"owner baseline archive: hard-diff profile byte-identical to the standing profile ({n} turns)", hard, STANDING_BASELINE)
check("owner baseline archive: no rail turn appears (0 rail obligations, as ruled)", any(f == "precise_detail" for _, f in hard), False)
per_turn, n, _sk = replay_session(str(ROOT / "phase5" / "harness" / "fixtures" / "synthetic_slice1" / "session_0001.log"))
check(f"synthetic fused fixture replays to identity ({n} turns)", per_turn, {})
_pr = (ROOT / "phase5" / "tests" / "test_precision_rail.py").read_text(encoding="utf-8")
check("§H pin declared in test_precision_rail.py", "DECLARED PIN" in _pr and "t26 base KEPT (fresh proposal, not a replace)" in _pr, True)

print("== FIXTURE: write the reconstructed rail archive + manifest, then replay it to identity ==")
FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
session_path = FIXTURE_DIR / "session_20260903_103339_reconstructed.log"
with open(session_path, "w", encoding="utf-8") as f:
    for t in turns:
        f.write(json.dumps(t, ensure_ascii=False) + "\n")
manifest = {
    "fixture": "session_103339_rail",
    "kind": "RECONSTRUCTED rail session (first rail fixture)",
    "note": ("Rebuilt from the STT texts + playback outcomes of owner session "
             "session_20260903_103339 (docs/VALUE_TRANSACTION_LOCK.md §0) through run_turn "
             "under the VALUE_TRANSACTION_LOCK build. The raw live archive is not in the repo, "
             "so this is NOT a byte-copy: same user texts, turn numbers and playback facts "
             "(t8 cancelled pre-audio, t14/t16/t18 barged), pipeline outputs are the LOCK's. "
             "Coverage it gives: replay-identity of the rail path (proposal/base/delivery "
             "carrier). Coverage it does NOT give: proof of live behaviour — that is the "
             "next owner smoke. Written by phase5/tests/test_session_103339_trace.py."),
    "session": session_path.name,
    "turns": len(turns),
    "rail_turns": sum(1 for t in turns if t.get("engine_path") == "precision_rail"),
    "fused_turns": sum(1 for t in turns if t.get("engine_path") == "fused"),
    "base_after_t5": BASE_T5,
    "proposal_at_end": TARGET,
    "properties": ["P1 t8 no mutation", "P2 t8+t9 one proposal", "P3 unheard never confirmable",
                   "P4 commit only via proposal+delivery+confirm", "P5 t15-t17 clarify not LLM claim",
                   "P6 supersession at boundary", "P7 non-rail unchanged except §H"],
}
with open(FIXTURE_DIR / "manifest.json", "w", encoding="utf-8") as f:
    f.write(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
per_turn, n, skipped = replay_session(str(session_path))
check(f"reconstructed fixture replays to IDENTITY ({n} turns checked)", (n > 15, per_turn), (True, {}))
check("fixture is rail-heavy (>= 15 rail turns), 0 fused turns among t15-t17", (manifest["rail_turns"] >= 15, manifest["fused_turns"]), (True, 0))

print()
if fails:
    print(f"FAIL ({fails})"); sys.exit(1)
print("ALL PASS")

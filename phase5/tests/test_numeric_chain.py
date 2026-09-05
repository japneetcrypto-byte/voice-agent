"""Numeric audit CHAIN — Phase 1 of docs/NUMERIC_OBSERVATION_LOCK.md §11.

UNIT/INVARIANT coverage (not a fixture):
  * the chain record is ADDITIVE and READ-ONLY: run_turn's decisions, the
    precise_detail archive and the task state are byte-identical with the
    Phase-1 attach points in place (the zero-behaviour-change proof at the
    interface level; the replay gate proves it on the archives)
  * every turn carries observation + chain (rail, LLM, dropped, silent)
  * the operation/proposal/confirmation/commit fields say what the rail did
    on the 103339 t3→t25 trace (L1–L6 lock §8) and the 133627 trace
  * observation_vs_signal is the first-wrong-layer oracle: it names the
    133627 turns where the legacy string signal and the observation diverge
  * main.py carries the same attach points (source-level pin — main.py is
    not importable offline)

Run: python3 phase5/tests/test_numeric_chain.py
"""
import sys, os, json, copy
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agent.response_pipeline import run_turn, TurnContext
from agent.numeric_chain import (build_chain, attach_chain, legacy_seg, observation_vs_signal,
                                 operation_kind, confirm_evidence, chain_line, CHAIN_VERSION)
from agent.numeric_observation import observe, attach_observation
from agent.value_transaction import SPOKEN
import agent.response_pipeline as rp

fails = 0
def check(label, got, want):
    global fails
    if got == want:
        print(f"  ✓ {label}")
    else:
        fails += 1
        print(f"  ✗ {label}: got {got!r}, want {want!r}")

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

class FakeSess:
    def policy_for_turn(self): return {}
    def memory_view(self): return []
class FakeLCM:
    def add_turn(self, *a, **k): pass
    def needs_compression(self): return False
    def get_layer2(self): return {"active_topic": None}

def fresh_engine():
    return {"sess": FakeSess(), "lcm": FakeLCM(), "fused": None}

def run(session, engine=None, llm_text="achha."):
    engine = engine or fresh_engine()
    turns = []
    for tn, text, playback in session:
        ctx = TurnContext(turn_no=tn, user_text=text, engine=engine, model_text=llm_text,
                          interrupted=playback in ("cancel_pre_audio", "barged"),
                          played_any_audio=playback in ("played", "barged"),
                          endpoint={"speech_duration_ms": 900.0, "trailing_silence_ms": 300.0},
                          stt_source={"provider": "groq", "language": "hi", "avg_logprob": -0.2})
        turns.append(run_turn(ctx))
    return turns, engine

S103339 = [
    (3,  "यार एक नंबर लिखा तुझे बता रहा हूँ", "played"),
    (5,  "026900125205203", None),
    (7,  "बता क्या लिखा", "played"),
    (8,  "नहीं 520 नहीं है", "cancel_pre_audio"),
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
S133627 = [
    (2, "यह बढ़ने है नंबर लिख में दूसरे बता रहा हूं", "played"),
    (3, "026900", None),
    (4, "1, 2, 5, 8, 0, 1, 2, 0, 3", "played"),
    (5, "आई दूने नंबर मेरा सुना था मैंने क्या बोला था", "played"),
    (6, "लिखा", None),
    (7, "हेलो", "barged"),
    (8, "एक बार फिर से", None),
    (9, "एक बार फिर से बोल दूने क्या लिखा है?", "played"),
    (10, "पूरा बोल पूरा नंबर 026900 आगे", "played"),
    (12, "अब इसके आगे और नंबर continue होगा, आगे है 1, 2, 5, 2, 0, 5, 2, 0, 3", "played"),
    (13, "12520 नहीं है, 12520 है", "played"),
    (14, "इसमें जो 5 को हटा दे", None),
    (15, "और वहाँ पे 5 जीरो लगा", "played"),
    (16, "नहीं नहीं, मैं दुबारा बोलता हूँ", "cancel_pre_audio"),
    (17, "देख जीरो टू सिस नाइन डबल जीरो ये ठीक है इसको लिख कर रख लिया ", "barged"),
    (18, "अब उसके पास...", None),
    (19, "देखो वो है 125205203", None),
    (20, "समझा तू?", None),
    (21, "हलो", "barged"),
    (22, "और समझा दू", None),
    (23, "बोल", None),
]

# ---------------------------------------------------------------------------
print("== zero behaviour change at the run_turn interface: attach points removed vs present ==")
def strip_phase1(t):
    t = copy.deepcopy(t)
    for k in ("numeric_observation", "numeric_audit", "numeric_audit_error"):
        t.pop(k, None)
    return t
_ao, _ac = rp.attach_observation, rp.attach_chain
rp.attach_observation = lambda *a, **k: None
rp.attach_chain = lambda *a, **k: None
try:
    t_off, e_off = run(S103339)
    t_off2, e_off2 = run(S133627)
finally:
    rp.attach_observation, rp.attach_chain = _ao, _ac
t_on, e_on = run(S103339)
t_on2, e_on2 = run(S133627)
check("103339: turn dicts identical modulo the two additive keys", [strip_phase1(t) for t in t_on], t_off)
check("103339: task state identical", (e_on.get("dictation"), e_on.get("conv")), (e_off.get("dictation"), e_off.get("conv")))
check("133627: turn dicts identical modulo the two additive keys", [strip_phase1(t) for t in t_on2], t_off2)
check("133627: task state identical", (e_on2.get("dictation"), e_on2.get("conv")), (e_off2.get("dictation"), e_off2.get("conv")))
check("added keys are exactly numeric_observation + numeric_audit",
      sorted({k for t in t_on + t_on2 for k in t} - {k for t in t_off + t_off2 for k in t}), ["numeric_audit", "numeric_observation"])
check("every turn (rail, LLM, silent, greeting) carries both records",
      all(isinstance(t.get("numeric_observation"), dict) and isinstance(t.get("numeric_audit"), dict) for t in t_on + t_on2), True)
check("no chain errors", [t.get("numeric_audit_error") for t in t_on + t_on2 if t.get("numeric_audit_error")], [])
drop = run_turn(TurnContext(turn_no=1, user_text="हाँ", engine=fresh_engine(), is_valid=False,
                            rejection_reason="catastrophic_low_confidence", agent_was_speaking=True, model_text=""))
check("a route-DROPPED turn still carries observation + chain (stage route_dropped)",
      (bool(drop.get("dropped_reason")), drop["numeric_audit"]["stage"], drop["numeric_observation"]["certainty"]),
      (True, "route_dropped", "EMPTY"))
check("records are JSON-serialisable (session_*.log line)", json.loads(json.dumps(t_on[4]))["numeric_audit"]["version"], CHAIN_VERSION)

# ---------------------------------------------------------------------------
print("== the chain on 103339 (VALUE_TRANSACTION_LOCK §8 trace) ==")
by = {t["turn"]: t for t in t_on}
na = lambda tn: by[tn]["numeric_audit"]
check("t5 first span: op start_base, base ∅ -> 026900125205203, observation COMPLETE agrees with legacy seg",
      (na(5)["operation"]["kind"], na(5)["commit"]["base_before"], na(5)["commit"]["base_after"],
       na(5)["observation_certainty"], na(5)["observation_vs_signal"]),
      ("start_base", "", "026900125205203", "COMPLETE", "agree"))
check("t5 observation_ref points at the turn", na(5)["observation_ref"], 5)
check("t8 'नहीं 520 नहीं है': hold_instruction, reject evidence recorded, base unchanged, l1 ok",
      (na(8)["operation"]["kind"], na(8)["confirm_evidence"]["reject_tokens"], na(8)["commit"]["changed"], na(8)["commit"]["l1_check"]),
      ("hold_instruction", ["नहीं"], False, "ok"))
check("t9 closes into a correction proposal; proposal fields follow; base unchanged",
      (na(9)["operation"]["kind"], na(9)["proposal"]["derived"], na(9)["proposal"]["mode"], na(9)["commit"]["changed"]),
      ("correction_proposal", "02690012000005203", "correction", False))
check("t9 delivery visible on precise_detail (played) — chain says delivery_at_decision unspoken, archive says spoken",
      (na(9)["proposal"]["delivery_at_decision"], by[9]["precise_detail"].get("delivery")), ("unspoken", SPOKEN))
check("t9 observation: '5 बार 0' = 00000 MULTIPLIER COMPLETE; legacy seg agrees", (na(9)["observation_digits"], na(9)["observation_vs_signal"]), ("00000", "agree"))
check("t17 (LLM-fabrication turn live): rail clarify, '50' observed COMPLETE, legacy_only? no — agree, nothing committed",
      (by[17]["engine_path"], na(17)["operation"]["action"], na(17)["observation_digits"], na(17)["commit"]["changed"]),
      ("precision_rail", "clarify", "50", False))
check("t18 'नहीं पांच जीरो': observation AMBIGUOUS {50|00000} INCOMPLETE; the span detector dropped it (seg '') while the correction parser's digit group read '50'",
      (na(18)["observation"], na(18)["legacy_signal"]["seg"], na(18)["legacy_signal"]["groups"], na(18)["observation_vs_signal"]),
      ("INCOMPLETE {50|00000} (WORDS) [COUNT_OR_DIGIT]", "", ["50"], "legacy_dropped"))
check("no turn in 103339 has an unexpected base change", [tn for tn, t in by.items() if t["numeric_audit"]["commit"]["l1_check"] != "ok"], [])
check("zero commits in 103339 (no 'haan' ever given)", [tn for tn, t in by.items() if t["numeric_audit"]["operation"]["kind"] == "commit"], [])
check("chain_line renders the t9 turn", "op=correction_proposal" in chain_line(by[9]) and "proposal=02690012000005203" in chain_line(by[9]), True)

print("== the chain on 133627 (NUMERIC_PERCEPTION_AUDIT) ==")
by2 = {t["turn"]: t for t in t_on2}
na2 = lambda tn: by2[tn]["numeric_audit"]
check("t3 start_base 026900 COMPLETE agree", (na2(3)["operation"]["kind"], na2(3)["observation_digits"], na2(3)["observation_vs_signal"]), ("start_base", "026900", "agree"))
check("t4: the operation the rail took is a REPLACE proposal (restate) — recorded as such, with the inputs it used",
      (na2(4)["operation"]["kind"], na2(4)["proposal"]["derived"], na2(4)["operation"]["inputs"]["base_before"], na2(4)["observation"]),
      ("replace_proposal:restate", "125801203", "026900", "COMPLETE 125801203 (SEPARATED)"))
check("t10: cues recorded — restart 'पूरा' x2 AND continue 'आगे' (the N5 conflict), op replace_proposal:restate",
      (na2(10)["operation"]["inputs"]["cues"]["restart"], na2(10)["operation"]["inputs"]["cues"]["continue"], na2(10)["operation"]["kind"]),
      (["पूरा", "पूरा"], ["आगे"], "replace_proposal:restate"))
check("t12: continue cue + SEPARATED surface -> the rail replaced; chain shows it", (na2(12)["operation"]["kind"], na2(12)["operation"]["inputs"]["cues"]["continue"]),
      ("replace_proposal:restate", ["आगे", "आगे"]))
check("t15 '5 जीरो': observation INCOMPLETE {50|00000}; legacy guessed '50'; rail clarified (no mutation)",
      (na2(15)["observation_certainty"], na2(15)["legacy_signal"]["seg"], na2(15)["observation_vs_signal"], na2(15)["operation"]["action"], na2(15)["commit"]["changed"]),
      ("INCOMPLETE", "50", "legacy_guessed", "clarify", False))
check("t17 'जीरो टू सिस नाइन डबल जीरो': observation 02?900 INCOMPLETE; legacy signal EMPTY (dropped the whole span); rail read it as a confirm",
      (na2(17)["observation"], na2(17)["legacy_signal"]["seg"], na2(17)["observation_vs_signal"], na2(17)["operation"]["action"], na2(17)["confirm_evidence"]["confirm_tokens"]),
      ("INCOMPLETE 02?900 (WORDS) [OOV_TOKEN]", "", "legacy_dropped", "echo_full", ["ठीक"]))
# RE-PIN (M9, 2026-09-05 — owner session 20260905_124658 t16): t16 'नहीं नहीं,
# मैं दुबारा बोलता हूँ' carries a reject AND the recall pattern ('दुबारा बोल')
# while a proposal is open. It used to be answered as a recall (proposal + base
# read out, proposal kept open); under M9 the rejection reverts the proposal
# to the base (L1.4) and speaks the base once. So at t19 no proposal is open
# any more: the restated '125205203' arrives after a cold gap and becomes a
# FRESH proposal (row 48, silent) instead of matching the old one — the same
# digits, now proposed afresh. The base never moved either way (checked below).
check("t16 reject + 'dubara bol' with a proposal open -> revert (M9), base spoken once",
      (na2(16)["operation"]["action"], by2[16]["precise_detail"].get("proposal")), ("retry", None))
check("t19 '125205203' after the revert -> fresh proposal (silent), observation agrees",
      (na2(19)["operation"]["kind"], na2(19)["operation"]["action"], na2(19)["observation_vs_signal"]), ("fresh_proposal", "silent_accumulate", "agree"))
check("first-wrong-layer oracle over 133627: observation vs legacy seg differ on t13, t14 (edit-intent turns — digits visible only to the observation; rows held), t15 (guessed '50'), t17 (span dropped)",
      {tn: t["numeric_audit"]["observation_vs_signal"] for tn, t in by2.items() if t["numeric_audit"]["observation_vs_signal"] not in ("agree", "none")},
      {13: "observation_only", 14: "observation_only", 15: "legacy_guessed", 17: "legacy_dropped"})
check("t13: the correction parser's groups are recorded next to the empty seg", (na2(13)["legacy_signal"]["seg"], na2(13)["legacy_signal"]["groups"]), ("", ["12520", "12520"]))
check("base never moved in 133627 (L1 held) — every l1_check ok", [tn for tn, t in by2.items() if t["numeric_audit"]["commit"]["l1_check"] != "ok"], [])

# ---------------------------------------------------------------------------
print("== unit: operation kinds, confirm evidence, stages ==")
check("legacy_seg mirrors classify_turn", legacy_seg("1, 2, 5, 8, 0, 1, 2, 0, 3"), ("125801203", "1, 2, 5, 8, 0, 1, 2, 0, 3"))
check("observation_vs_signal: none / observation_only / legacy_only",
      (observation_vs_signal(observe(""), ""), observation_vs_signal(observe("026900"), ""), observation_vs_signal(observe("हेलो"), "5")),
      ("none", "observation_only", "legacy_only"))
check("commit kind when confirm_ack moves the base", operation_kind({"action": "confirm_ack"}, {"value": "1", "status": "confirming", "proposal": {"derived": "12"}}, {"value": "12", "status": "confirmed"}, 9), "commit")
check("confirm of the base (no proposal) is confirm_base", operation_kind({"action": "confirm_ack"}, {"value": "12", "status": "confirming"}, {"value": "12", "status": "confirmed"}, 9), "confirm_base")
check("append_to_base (rows 13/30, owner Q1)", operation_kind({"action": "silent_accumulate"}, {"value": "12", "status": "pending"}, {"value": "1234", "status": "pending"}, 4), "append_to_base")
check("fresh_proposal (row 48, owner Q2)", operation_kind({"action": "silent_accumulate"}, {"value": "12", "status": "pending"}, {"value": "12", "status": "pending", "proposal": {"derived": "7398", "mode": "fresh"}}, 30), "fresh_proposal")
check("append_to_proposal", operation_kind({"action": "silent_accumulate"}, {"value": "12", "proposal": {"derived": "73"}}, {"value": "12", "proposal": {"derived": "7398"}}, 31), "append_to_proposal")
check("revert (L1.4)", operation_kind({"action": "retry", "trigger": "proposal_reverted"}, {"value": "12", "proposal": {"derived": "13"}}, {"value": "12"}, 5), "revert")
check("task switch (row 40) = new base; allowed by l1_check", build_chain(text="account number likho 0269001262", turn_no=8, rail={"action": "echo_confirm", "value": "0269001262"},
      pre_dictation={"value": "9935411907", "status": "confirming"}, post_dictation={"value": "0269001262", "status": "confirming"}, observation=observe("account number likho 0269001262"))["commit"]["l1_check"], "ok")
check("an unexplained base change is flagged UNEXPECTED_BASE_CHANGE", build_chain(text="x", turn_no=8, rail={"action": "silent", "value": "1"},
      pre_dictation={"value": "12"}, post_dictation={"value": "99"}, observation=observe("x"))["commit"]["l1_check"], "UNEXPECTED_BASE_CHANGE")
ce = confirm_evidence("ठीक है", None, {"value": "1", "status": "confirming", "proposal": {"derived": "12", "delivery": SPOKEN}})
check("confirm evidence for a turn the rail never saw (echo-dropped 'ठीक है'): tokens + proposal delivery before",
      (ce["confirm_tokens"], ce["is_confirm"], ce["proposal_delivery_before"], ce["rail_action"]), (["ठीक"], True, SPOKEN, None))
check("no confirm/reject token -> no evidence block", confirm_evidence("026900", None, {}), None)
check("'बस एक जीरो कम हो' — confirm token present but the rail's change-frame guard says not a confirm",
      (confirm_evidence("बस एक जीरो कम हो", None, {})["confirm_tokens"], confirm_evidence("बस एक जीरो कम हो", None, {})["is_confirm"]), (["बस"], False))
turn = {"turn": 11, "stt_transcript": "ठीक है"}
attach_observation(turn, "ठीक है", 11)
rec = attach_chain(turn, text="ठीक है", turn_no=11, rail=None, pre_dictation={"value": "026900", "status": "confirming", "proposal": {"derived": "125801203", "delivery": SPOKEN}},
                   engine={"dictation": {"value": "026900", "status": "confirming", "proposal": {"derived": "125801203", "delivery": SPOKEN}}}, stage="echo_dropped")
check("echo_dropped stage: 133627 t11 'ठीक है' is inspectable — confirm token, spoken proposal open, no operation, no commit",
      (rec["stage"], rec["confirm_evidence"]["confirm_tokens"], rec["confirm_evidence"]["proposal_delivery_before"], rec["operation"]["kind"], rec["commit"]["changed"]),
      ("echo_dropped", ["ठीक"], SPOKEN, "none", False))
check("attach_chain is write-once", attach_chain(turn, text="zzz", turn_no=11, rail={"action": "silent"}, pre_dictation={}, engine={}) is rec, True)
turn2 = {"turn": 1}
rec2 = attach_chain(turn2, text="x", turn_no=1, rail=object(), pre_dictation={}, engine={})   # rail.get -> AttributeError
check("fail-closed: an internal error is archived, never raised", (rec2, "numeric_audit_error" in turn2), (None, True))

# ---------------------------------------------------------------------------
print("== main.py carries the same attach points (source pin; not importable offline) ==")
_main = open(os.path.join(ROOT, "agent", "main.py"), encoding="utf-8").read()
check("observation attached right after the transcript lands (before the echo gate)",
      _main.index("attach_observation(") < _main.index("is_echo(echo_text, session.recent_agent_text)"), True)
check("echo-dropped turns get a chain record (stage echo_dropped)", 'stage="echo_dropped"' in _main, True)
check("rail turns get a chain record from the shadow pre-state",
      'pre_dictation=(_shadow_engine or {}).get("dictation")' in _main and _main.index("precision_rail_decide(transcript.text, engine, turn_number") < _main.index('stage="rail" if action != "drop" else "route_dropped"'), True)
check("the rail call itself is unchanged", 'precision_rail_decide(transcript.text, engine, turn_number,\n                                                               {"premature_resume": turn.get("premature_resume")})\n                                         if engine and action != "drop" else None)' in _main, True)
check("observation records the FULL STT text + model/provider (lock §13 D)", '"model": (os.getenv("AIVA_STT_MODEL"' in _main and 'endpoint=endpoint_evidence(' in _main, True)
_diag = open(os.path.join(ROOT, "phase5", "stage_diagnostic.py"), encoding="utf-8").read()
check("stage_diagnostic no longer truncates STT at 60 chars", "stt[:60]" not in _diag and "numeric" in _diag, True)

print()
if fails:
    print(f"FAIL ({fails})"); sys.exit(1)
print("ALL PASS")

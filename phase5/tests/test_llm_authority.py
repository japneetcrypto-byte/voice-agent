"""L5 — LLM authority boundary (docs/VALUE_TRANSACTION_LOCK.md §6, owner Q6).

UNIT/INVARIANT coverage:
  1. routing: an edit-intent turn while a task is active is NEVER released to
     the LLM by word count (row 24′) — it holds/clarifies deterministically;
  2. context: the LLM sees task_state {kind,status,has_value,proposal_open}
     and never a digit of the value/proposal/spec;
  3. output gate: `claim_mutation` blocks a first-person "I changed the
     digits" claim (Hinglish + Devanagari), with zero false positives on the
     archived reply corpus and on ordinary number-bearing chat;
  4. contract: one task-scoped MUST_NOT while a task is active (cap 5 kept).
The invariant: the LLM may propose or clarify an operation, but it must never
claim that a mutation was performed unless the deterministic path committed it.

Run: python3 phase5/tests/test_llm_authority.py
"""
import sys, os, json, glob, re
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agent.precision_rail import decide as decide_raw
from agent.response_contract import check_violations, gate_reply, build_contract, derive_constraints, _GATE_PATTERNS
from agent.value_transaction import task_state_view
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
BASE = "00000"    # the 103339 base at t15 (after the live t9 damage) — digits irrelevant to routing

print("== 1. routing: the t15-t17 class (change frame, no spec, 8-14 words) never reaches the LLM ==")
# the actual STT texts of session_20260903_103339 (lock §0 table)
T15 = "तुन्हें सुना मैंने चेंज बताए नंबर के अंदर"
T16 = "चेंज ये करना है कि तुन्हें अभी 5"
T17 = "जो नंबर लगाया है ना पाइप नंबर हटेगा और उसके जगह 50 एड होंगे"
for tn, t in ((15, T15), (16, T16), (17, T17)):
    eng = {"dictation": {"value": BASE, "status": "pending"}, "conv": {}}
    r = decide_raw(t, eng, tn)
    check(f"t{tn}: deterministic decision, not None (LLM release)", r is not None, True)
    check(f"t{tn}: hold/clarify, base untouched",
          (r["action"] in ("hold_edit", "clarify", "retry", "silent"), eng["dictation"]["value"]), (True, BASE))
eng = {"dictation": {"value": BASE, "status": "pending"}, "conv": {}}
acts = [decide_raw(t, eng, tn)["action"] for tn, t in ((15, T15), (16, T16), (17, T17))]
check("t15-t17 in sequence: hold -> continuation -> clarify (no mutation, no LLM)",
      (acts, eng["dictation"]["value"], eng["dictation"].get("proposal")),
      (["hold_edit", "silent", "clarify"], BASE, None))
eng = {"dictation": {"value": BASE, "status": "pending"}, "conv": {}}
r = decide_raw("मुझे लगता है आज बारिश होगी शाम को यार क्या बोलते हो", eng, 20)
check("non-edit long chat while a task is active still releases to the LLM (row 24′ kept)", r, None)

print("== 2. context: task_state exposes no digits ==")
eng = {"dictation": {"value": "026900125205203", "status": "confirming",
                      "proposal": {"base": "026900125205203", "spec": [None, "520", "00000"],
                                   "derived": "02690012000005203", "mode": "correction",
                                   "created_turn": 9, "delivery": "unspoken"}}}
ts = task_state_view(eng)
check("task_state shape", ts, {"kind": "dictation", "status": "confirming", "has_value": True, "proposal_open": True})
check("no digit anywhere in task_state", re.search(r"\d", json.dumps(ts)) is None, True)
check("no task_state when no task", task_state_view({"dictation": {"value": "1", "status": "confirmed"}}), None)
c = build_contract(policy={}, task_state=ts)
check("contract carries TASK_STATE without digits", ("TASK_STATE" in c, re.search(r"\d", json.dumps(c["TASK_STATE"])) is None), (True, True))
check("contract without a task has no TASK_STATE", "TASK_STATE" in build_contract(policy={}), False)

print("== 4. contract: one task-scoped MUST_NOT, cap 5 respected ==")
base_mn = derive_constraints(policy={})
task_mn = derive_constraints(policy={}, task_active=True)
check("task adds exactly one MUST_NOT", len(task_mn) - len(base_mn), 1)
check("it forbids claiming an edit", any("claim you changed" in m for m in task_mn), True)
full = derive_constraints(policy={}, last_claim="x", last_reply="y", is_recovery=True, memory_count=3, task_active=True)
check("cap 5 still holds with everything on", len(full), 5)
check("the task MUST_NOT survives the cap (priority slot)", any("claim you changed" in m for m in full), True)
check("the two safety MUST_NOTs still lead", (full[0].startswith("fabricate"), full[1].startswith("expose")), (True, True))

print("== 3. output gate: claim_mutation blocks mutation claims (agent output only) ==")
check("claim_mutation registered as a block pattern", all(a == "block" for _, _, a in _GATE_PATTERNS["claim_mutation"]), True)
claims = [
    "Theek hai, 5 ki jagah 50 add kar deta hoon.",          # the live t17 fabrication
    "haan, 520 hata diya, ab number sahi hai.",
    "maine 5 baar 0 likh diya hai.",
    "ok, 520 ko 00000 se replace kar diya.",
    "ठीक है, 520 हटा दिया।",
    "paanch zero add kar deta hoon",
]
for t in claims:
    v = [x for x in check_violations(t) if x["type"] == "claim_mutation"]
    check(f"BLOCK {t!r}", bool(v) and v[0]["action"] == "block", True)
gated, viol = gate_reply(claims[0], turn_no=17)
check("gated reply is a neutral line, not the claim", ("add" in gated or "50" in gated), False)
safe = [
    "kya badalna hai — 520 ki jagah kya aayega?",          # clarify (allowed)
    "aap 5 baar 0 likhna chahte ho? bolo, main confirm karta hoon.",
    "haan, bolo — kaunsa hissa badalna hai?",
    "mujhe samajh nahi aaya, phir se bolo.",
    "2 baje milte hain, theek hai? main aa jaunga.",
    "kal 5 baje call karunga.",
    "main tumhara dost hoon, 24 ghante.",
    "3 idli aur 2 dosa order kar deta hoon?",              # question, not a claim (blocked by action_fabrication rules only if stated)
]
for t in safe:
    v = [x for x in check_violations(t) if x["type"] == "claim_mutation"]
    check(f"allow {t!r}", bool(v), False)

print("== 3b. zero false positives on the archived reply corpus ==")
n, hits = 0, []
for f in glob.glob(os.path.join(ROOT, "phase5", "harness", "fixtures", "*", "session_*.log")):
    for line in open(f, encoding="utf-8"):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            t = json.loads(line)
        except Exception:
            continue
        for k in ("llm_response", "llm_response_full", "tts_text"):
            if t.get(k):
                n += 1
                if any(x["type"] == "claim_mutation" for x in check_violations(t[k])):
                    hits.append(t[k][:80])
check(f"archived replies scanned ({n})", n > 50, True)
check("claim_mutation false positives on archived replies", hits, [])

print("== 3c. the gate runs on the streamed piece path (process_piece via run_turn) ==")
class FakeSess:
    def policy_for_turn(self): return {}
    def memory_view(self): return []
class FakeLCM:
    def add_turn(self, *a, **k): pass
    def needs_compression(self): return False
    def get_layer2(self): return {}
eng = {"dictation": {"value": BASE, "status": "pending"}, "conv": {}, "sess": FakeSess(), "lcm": FakeLCM()}
t = run_turn(TurnContext(turn_no=20, user_text="मुझे लगता है आज बारिश होगी शाम को यार क्या बोलते हो", engine=eng,
                         model_text="Theek hai, 5 ki jagah 50 add kar deta hoon."))
check("LLM path taken for the non-edit turn", t.get("engine_path"), "fused")
check("mutation claim blocked before TTS", any(v["type"] == "claim_mutation" and v["action"] == "block" for v in t.get("contract_violations", [])), True)
check("spoken text is not the claim", "add kar deta" in (t.get("llm_response") or ""), False)
check("contract on that turn carried TASK_STATE (no digits)",
      ("TASK_STATE" in t["policy"]["contract"], re.search(r"\d", json.dumps(t["policy"]["contract"].get("TASK_STATE"))) is None), (True, True))
check("value untouched by the LLM turn", eng["dictation"]["value"], BASE)

print()
if fails:
    print(f"FAIL ({fails})"); sys.exit(1)
print("ALL PASS")

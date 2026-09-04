"""L1 — two-phase mutation (docs/VALUE_TRANSACTION_LOCK.md §2).

UNIT/INVARIANT coverage (not a fixture): every destructive row writes a
PROPOSAL and leaves the BASE byte-identical; the base moves ONLY on commit
(explicit confirm of a delivered proposal); a plain reject of a proposal
reverts to the base (never wipes); appends during accumulation stay
immediate (owner Q1); row 48 is a fresh proposal (owner Q2).

Run: python3 phase5/tests/test_value_transaction.py
"""
import sys, os, copy
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agent.precision_rail import decide as decide_raw, speak_value
from agent.value_transaction import decide_heard, mark_heard, propose, confirmable, SPOKEN, UNSPOKEN, UNHEARD_D
from agent.conversation_controller import Task

fails = 0
def check(label, got, want):
    global fails
    if got == want:
        print(f"  ✓ {label}")
    else:
        fails += 1
        print(f"  ✗ {label}: got {got!r}, want {want!r}")

def eng_with(value, status="confirming"):
    return {"dictation": {"value": value, "status": status}, "conv": {}}

BASE = "026900125205203"

print("== L1.1 destructive rows write a PROPOSAL, never the base ==")
rows = [
    ("correction (replace pair)", "9000 की जगह 900 कर दो", "02690001700001203", "0269001700001203"),
    ("correction (anchor insert)", "12 के बाद 4 बार 0 है 420 नहीं है इसको correct करो", "02690012425703", "0269001200005703"),
    ("row 14 full restatement", "नहीं, मेरा नंबर 02690001245703 है", "02690001700001203", "02690001245703"),
    ("row 41 only-this", "इसमें जो 9935411907 है, सिर्फ इतना नमबर, ठीक है?", "99354119070269001262", "9935411907"),
    ("val-aware removal+replace", "620 नहीं है, 6 बार 0 लिखने है", "026900126205703", "026900120000005703"),
]
for label, text, base, derived in rows:
    eng = eng_with(base)
    before = copy.deepcopy(eng["dictation"])
    r = decide_raw(text, eng, 10)
    dic = eng["dictation"]
    check(f"{label}: echoes the DERIVED value", (r["action"], r["value"]), ("echo_confirm", derived))
    check(f"{label}: base byte-identical", dic["value"], base)
    check(f"{label}: proposal {{base,derived}}", (dic["proposal"]["base"], dic["proposal"]["derived"]), (base, derived))
    check(f"{label}: proposal starts UNSPOKEN (L2 owns delivery)", dic["proposal"]["delivery"], UNSPOKEN)
    check(f"{label}: decision exposes the proposal", r.get("proposal", {}).get("derived"), derived)

print("== L1.1 row 40 task switch is a NEW task (old task + proposal discarded) ==")
eng = eng_with("9935411907")
decide_raw("9000 की जगह 900", eng, 7)  # no-op spec on this base -> whatever; then switch
eng = eng_with("9935411907")
r = decide_raw("पढ़ें या एक number, अब account number लिखो जरा, 026-900-1262", eng, 8)
check("row 40 -> echo of the new number", (r["action"], r["value"]), ("echo_confirm", "0269001262"))
check("row 40 new task has no proposal (fresh task, not a mutation of the old)", eng["dictation"].get("proposal"), None)

print("== L1.2 commit only via confirm of a DELIVERED proposal ==")
eng = eng_with("02690001700001203")
r = decide_raw("9000 की जगह 900 कर दो", eng, 11)
r = decide_raw("हाँ", eng, 12)                       # never marked delivered
check("confirm of an UNSPOKEN proposal does NOT commit", eng["dictation"]["value"], "02690001700001203")
check("... it speaks the full proposed value instead", (r["action"], r["value"]), ("echo_full", "0269001700001203"))
mark_heard(eng, r, 12)                                # playback: heard
r = decide_raw("हाँ", eng, 13)
check("confirm of a SPOKEN proposal commits base <- derived", (r["action"], eng["dictation"]["value"], eng["dictation"]["status"]),
      ("confirm_ack", "0269001700001203", "confirmed"))
check("proposal cleared on commit", eng["dictation"].get("proposal"), None)

print("== L1.3 recall while a proposal is open speaks it AS a proposal (base named) ==")
eng = eng_with("02690001700001203")
decide_heard("9000 की जगह 900 कर दो", eng, 11)
r = decide_raw("क्या लिखा तुमने?", eng, 12)
check("recall action", r["action"], "recall")
check("recall speaks the proposed value", speak_value("0269001700001203") in r["line"], True)
check("recall names the base too", speak_value("02690001700001203") in r["line"], True)
check("recall does not commit", eng["dictation"]["value"], "02690001700001203")

print("== L1.4 plain reject of a proposal REVERTS to the base (never wipes) ==")
eng = eng_with("02690001700001203")
decide_heard("9000 की जगह 900 कर दो", eng, 11)
r = decide_raw("नहीं, गलत है", eng, 12)
check("reject -> retry line that speaks the base", (r["action"], speak_value("02690001700001203") in r["line"]), ("retry", True))
check("base intact, proposal dropped", (eng["dictation"]["value"], eng["dictation"].get("proposal")), ("02690001700001203", None))
check("status back to pending (still the same task)", eng["dictation"]["status"], "pending")

print("== L1.4 digits while a proposal is open append to the PROPOSAL, not the base ==")
eng = eng_with("026900124205703", "pending")
decide_heard("डबल जीरो, वन, तू, चार बार जीरो, पाइट सेविन, जीरो त्री.", eng, 34)
r = decide_raw("5, 7, 0, 3", eng, 35)
check("append is silent", (r["action"], r["line"]), ("silent_accumulate", None))
check("append landed on the proposal", eng["dictation"]["proposal"]["derived"], "0012000057035703")
check("base untouched", eng["dictation"]["value"], "026900124205703")
check("appended (unheard) digits reset delivery -> confirm must re-echo", eng["dictation"]["proposal"]["delivery"], UNSPOKEN)

print("== Q1 (owner): append during accumulation stays IMMEDIATE (no proposal) ==")
eng = {"dictation": None, "conv": {}}
decide_raw("एक अकाउंट नंबर लिखो", eng, 8)
for i, t in enumerate(["012", "1, 2, 0,", "1, 2,"]):
    decide_raw(t, eng, 9 + i)
check("accumulated directly on the base", eng["dictation"]["value"], "01212012")
check("no proposal during accumulation", eng["dictation"].get("proposal"), None)

print("== Q2 (owner): row 48 cold-gap span is a FRESH PROPOSAL (base kept, silent) ==")
eng = eng_with("01212012000001203", "pending")
for tn, t in enumerate(["क्या यार", "सुनो", "अरे", "हद है", "बोलो"], start=13):
    decide_raw(t, eng, tn)
r = decide_raw("7398", eng, 26)
check("silent (row 4 semantics preserved)", (r["action"], r["line"]), ("silent_accumulate", None))
check("fresh proposal opened, base kept", (eng["dictation"]["proposal"]["mode"], eng["dictation"]["proposal"]["derived"], eng["dictation"]["value"]),
      ("fresh", "7398", "01212012000001203"))
decide_raw("438138", eng, 27)
check("next span appends to the fresh proposal", eng["dictation"]["proposal"]["derived"], "7398438138")
r = decide_raw("bas", eng, 28)
check("'bas' speaks the proposed number in full", (r["action"], r["value"]), ("echo_full", "7398438138"))
r = decide_raw("haan", eng, 29)
check("confirm before delivery -> still no commit (re-speaks)", (r["action"], eng["dictation"]["value"]), ("echo_full", "01212012000001203"))
mark_heard(eng, r, 29)
r = decide_raw("haan", eng, 30)
check("confirm after delivery commits the fresh number", (r["action"], eng["dictation"]["value"]), ("confirm_ack", "7398438138"))

print("== compat shape: pre-lock archives (no proposal key) still load; to_compat adds keys only when open ==")
t = Task.from_compat({"value": "123", "status": "confirming"})
check("from_compat without proposal", (t.value, t.proposal, t.pending_edit), ("123", None, None))
check("to_compat without proposal is the pre-lock 2-key dict", t.to_compat(), {"value": "123", "status": "confirming"})
t.proposal = propose("123", "124", spec=(None, "3", "4"), mode="correction", turn_no=5)
check("to_compat carries the proposal", t.to_compat()["proposal"]["derived"], "124")
check("confirmable() only when SPOKEN", (confirmable(t.proposal), confirmable({**t.proposal, "delivery": SPOKEN}), confirmable({**t.proposal, "delivery": UNHEARD_D})), (False, True, False))

print("== INVARIANT sweep: over a corpus of destructive turns the base never changes on the same turn ==")
corpus = [
    ("9000 की जगह 900 कर दो", "02690001700001203"),
    ("420 नहीं है, 4 बार 0 है", "0269004204301"),
    ("नहीं, मेरा नंबर 02690001245703 है", "02690001700001203"),
    ("इसमें जो 9935411907 है, सिर्फ इतना नमबर, ठीक है?", "99354119070269001262"),
    ("1242 नहीं है भाईया", "026900124201203"),
    ("पाइप की जगह 5 बार 0 लिखना है", "026900125203"),
    ("नहीं 520 नहीं है", "026900125205203"),
    ("शुरुआत में जो नाइन नाइन थी फॉर है वह नहीं होकर नाइन नाइन थी", "99345122324"),
]
violations = []
for text, base in corpus:
    eng = eng_with(base)
    r = decide_raw(text, eng, 20)
    if eng["dictation"]["value"] != base:
        violations.append((text, eng["dictation"]["value"]))
check("no same-turn base mutation across the destructive corpus", violations, [])

print()
if fails:
    print(f"FAIL ({fails})"); sys.exit(1)
print("ALL PASS")

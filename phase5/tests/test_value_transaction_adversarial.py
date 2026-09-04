"""Adversarial sweep for the VALUE TRANSACTION LOCK (L1+L2+L3 together).

UNIT/INVARIANT coverage: 1,500 random sessions × 30 turns drawn from the
session_20260903_103339 utterances + confirm/reject/recall/digit/task-switch
words, with random playback outcomes (60% heard / 40% cancelled). Oracle:
while a task is ACTIVE (pending/confirming), the BASE may change only by
  (a) confirm_ack of a proposal whose echo was marked SPOKEN (L1+L2 commit),
  (b) row 40 task switch (a NEW task — the old one is discarded),
  (c) plain-reject wipe of an accumulation (pre-lock rows 18/26, no proposal),
  (d) silent append during accumulation (owner Q1 — immediate, reversible).
A confirmed task is NOT active: a fresh dictation then is row 2/3 (new task).
Deterministic seed — reproducible.

Run: python3 phase5/tests/test_value_transaction_adversarial.py
"""
import sys, os, random
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agent.precision_rail import decide as decide_raw
from agent.value_transaction import mark_heard, SPOKEN

fails = 0
def check(label, got, want):
    global fails
    if got == want:
        print(f"  ✓ {label}")
    else:
        fails += 1
        print(f"  ✗ {label}: got {got!r}, want {want!r}")

U = ["नहीं 520 नहीं है", "पाइप की जगह 5 बार 0 लिखना है", "सुनातो ने?", "एवा", "हेलो",
     "तुन्हें सुना मैंने चेंज बताए नंबर के अंदर", "चेंज ये करना है कि तुन्हें अभी 5",
     "जो नंबर लगाया है ना पाइप नंबर हटेगा और उसके जगह 50 एड होंगे", "नहीं पांच जीरो", "मतलब 00000 एड होगा",
     "बोल", "हाँ", "बस", "नहीं, गलत है", "क्या लिखा तुमने?", "7398", "5 x 0",
     "9000 की जगह 900 कर दो", "420 नहीं है, 4 बार 0 है", "नहीं, मेरा नंबर 02690001245703 है",
     "अब account number लिखो जरा, 026-900-1262"]

random.seed(11)
n = commits = deferred = 0
violations, silent_commits = [], []
for trial in range(1500):
    eng = {"dictation": {"value": "026900125205203", "status": "pending"}, "conv": {}}
    for tn in range(1, 31):
        t = random.choice(U)
        before = dict(eng["dictation"])
        pb = before.get("proposal")
        active = before.get("status") in ("pending", "confirming")
        d = decide_raw(t, eng, tn)
        n += 1
        after = eng["dictation"]
        act = d.get("action") if d else None
        if active and after.get("value") != before.get("value"):
            ok = ((act == "confirm_ack" and pb and pb.get("delivery") == SPOKEN and after["value"] == pb["derived"])
                  or (act == "echo_confirm" and t.startswith("अब account number") and after["value"] == "0269001262")
                  or (act == "retry" and after["value"] == "" and not pb)
                  or (act == "silent_accumulate" and not pb and after["value"].startswith(before["value"])))
            if not ok:
                violations.append((trial, tn, t, act, before, after))
        if active and act == "confirm_ack":
            commits += 1
            if d.get("line") is None:
                silent_commits.append((trial, tn))
        if active and act == "echo_full" and (d or {}).get("trigger") in ("unheard_echo", "proposal_echo"):
            deferred += 1
        mark_heard(eng, d, tn, heard=random.random() < 0.6)

print(f"  decisions={n} commits={commits} confirms deferred by the delivery gate={deferred}")
check("no base mutation outside the allowed paths (L1/L2/L3 invariant)", violations[:3], [])
check("the sweep actually exercised commits and deferrals", (commits > 100, deferred > 100), (True, True))
check("every commit was spoken (no silent commit)", silent_commits, [])

print()
if fails:
    print(f"FAIL ({fails})"); sys.exit(1)
print("ALL PASS")

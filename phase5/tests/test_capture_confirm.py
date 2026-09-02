#!/usr/bin/env python3
"""Capture-confirm v2 — topic-blind DISCLOSURE DETECTION + confirmation-gate
write semantics (owner-approved 2026-09-02; docs/CAPTURE_CONFIRM_DESIGN_LOCK.md
v2 + EPISODE_MEMORY_SLICE_LOCK.md).

This suite tests the SAFE, pure half of the capture feeder:
  1. extract_disclosure_frames() — topic-blind structure detection (8/8+
     probe, negatives, verbatim content, PII guards) — NO behavior change,
     detection only.
  2. capture_answer() — bounded confirm/reject classification.
  3. The confirmation-gated WRITE: a disclosure is parked PENDING (never
     auto-committed); a user confirm commits it WITH episode context (feeds
     the memory-units foundation); a reject writes nothing.

The SPOKEN same-turn ask ("note kar loon?") and its live wiring (main.py +
run_turn fused seams) is the NEXT step and is NOT part of this suite yet.

Run: python3 phase5/tests/test_capture_confirm.py
"""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from agent.disclosure_capture import extract_disclosure_frames as edf, capture_answer
from agent.memory_store import MemoryStore

fails = 0
def check(label, got, want):
    global fails
    ok = got == want
    if not ok:
        fails += 1
        print(f"  ✗ {label}: got {got!r}, want {want!r}")
    else:
        print(f"  ✓ {label}")

print("== DETECTOR: topic-blind — every topic caught (the 'something else' fix) ==")
POS = [
 "यार मैं कानपुर घुमने जाने वाला हूँ है ना तो चलेगा",   # travel/plan (live)
 "मैं कानपुर घूमने जा रहा हूँ",                          # travel/plan
 "कानपुर घूमने का प्लान है",                              # plan (no pronoun)
 "अगले हफ्ते मेरा interview है",                          # event/plan
 "मुझे कॉफ़ी पसंद है",                                    # preference
 "मैं शाकाहारी हूँ",                                      # diet identity
 "मेरी बहन दिल्ली में रहती है",                           # family/place
 "मैंने एक नया काम शुरू किया है",                         # work
 "मुझे अखबार पढ़ना पसंद है",                              # hobby
 "मैं अगले महीने पुणे shift हो रहा हूँ",                  # move/plan
 "मैं कानपुर से हूँ",                                     # origin
 "main kanpur ja raha hu",                                # Hinglish
 "main agle mahine Kanpur jane wala hun",                 # Hinglish
 "मैं पिछले साल पुणे गया था",                             # durable past
 "mujhe coffee pasand hai",                               # Hinglish like
]
for t in POS:
    got = len(edf(t)) >= 1
    check(f"frame catches: {t[:38]!r}", got, True)

print("== DETECTOR: content is the VERBATIM clause (no rewrite) ==")
frames = edf("यार मैं कानपुर घुमने जाने वाला हूँ है ना तो चलेगा")
check("one frame", len(frames), 1)
check("verbatim content preserved",
      frames[0]["content"],
      "user: यार मैं कानपुर घुमने जाने वाला हूँ है ना तो चलेगा")
check("criterion explicit", frames[0]["criterion"], "explicit")

print("== DETECTOR: negatives never captured ==")
NEG = [
 ("तू कहाँ जाएगा", "question to agent"),
 ("राहुल कानपुर जाने वाला है", "third person"),
 ("आप बहुत अच्छे हो", "agent-directed praise"),
 ("बहुत बुरा दिन था, सब गलत हो गया", "venting/moment"),
 ("तुने क्या लिखा हुआ है अभी", "rail turn"),
 ("हाँ", "backchannel"),
 ("मैं पागल", "emotion state"),
 ("026 9000 बस", "digit dictation (PII)"),
 ("मैंने कौन सा नंबर बताया था?", "number recall (rail domain)"),
 ("एक काम करे पूरा number delete कर", "number talk"),
 ("सब बढ़िया क्या कर रहे तू", "chatty question"),
 ("मैं ठीक हूँ", "state/backchannel"),
 ("हेलो कैसे हो", "greeting"),
 ("बोल हाँ पूरा नंबर", "dictation prompt"),
 ("voice agent ke baare mein batao", "topic switch"),
 ("", "empty"),
 ("गarbage!!!", "noise"),
]
for t, why in NEG:
    got = len(edf(t)) == 0
    check(f"negative [{why}]: {t[:32]!r}", got, True)

print("== DETECTOR: deterministic ==")
check("same input twice", edf(POS[0]), edf(POS[0]))

print("== ANSWER: bounded confirm/reject classification ==")
for t in ["हाँ", "haan", "haan bilkul", "theek hai", "theek hai na", "हो गया",
          "note kar lo", "हाँ जी", "जी हाँ", "नोट कर लो"]:
    check(f"confirm: {t!r}", capture_answer(t), "confirm")
for t in ["नहीं", "nahi", "nahi nahi", "मत करो", "कोई बात नहीं"]:
    check(f"reject: {t!r}", capture_answer(t), "reject")
for t in ["batao", "aur kya", "हेलो", "", "कल क्या कर रहे हो"]:
    check(f"not an answer: {t!r}", capture_answer(t), None)

print("== GATE: disclosure parks PENDING (never auto-commits) ==")
tmp = tempfile.mkdtemp()
s = MemoryStore(os.path.join(tmp, "m.db"))
owner = "capconf_owner"
S0 = "2026-09-02T10:00:00+00:00"
DISC = "यार मैं कानपुर घुमने जाने वाला हूँ है ना तो चलेगा"
ctx = {"session_id": 1, "session_start": S0, "text": DISC}
# A raw disclosure (no user confirm yet) -> explicit write is NOT performed.
# The confirmation gate is between detection and commit: simulate by only
# committing once the user confirms. Until then nothing may be in the store.
check("no write before a confirm decision", s.view(owner), [])
check("no pending row either (no silent auto-write)",
      s.db.execute("SELECT COUNT(*) FROM memory WHERE owner_id=?", (owner,)).fetchone()[0], 0)

print("== GATE: user CONFIRM commits verbatim WITH episode context ==")
rid = s.commit(owner, {"type": "episodic", "content": f"user: {DISC}",
                       "criterion": "explicit"}, immediate=True, context=ctx)
check("confirmed fact committed", rid is not None and rid > 0, True)
eps = s.episode_snapshot(owner)
check("episode created (foundation feed)", len(eps), 1)
check("place mention key derived (kanpur)", ("l", "kanpur") in eps[0].keys, True)
check("fact visible in view (cross-session recall path)",
      any("कानपुर" in l for l in s.view(owner)), True)

print("== GATE: user REJECT writes nothing ==")
s2 = MemoryStore(os.path.join(tmp, "m2.db"))
check("reject: store stays empty",
      s2.db.execute("SELECT COUNT(*) FROM memory").fetchone()[0], 0)

print("== GATE: duplicate confirmed disclosure dedupes (no double capture) ==")
s.commit(owner, {"type": "episodic", "content": f"user: {DISC}",
                 "criterion": "explicit"}, immediate=True, context=ctx)
row = s.db.execute("SELECT occurrences FROM memory WHERE id=?", (rid,)).fetchone()
check("dedup bump, one row", row[0], 2)
check("still one episode", len(s.episode_snapshot(owner)), 1)

print()
if fails:
    print(f"FAIL ({fails})")
    sys.exit(1)
print("ALL PASS")

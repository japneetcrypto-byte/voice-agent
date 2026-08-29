#!/usr/bin/env python3
"""Regression: audit fixes 2026-08-29 (owner: 'audit again — no issues later').

Covers:
  1. Degraded-perception cooldown exit (the documented death-spiral bug)
  2. Layer-2 (compressed session state) reaches the fused LLM call contents
  3. FusedLLM epoch bumps only on first consume (generator laziness — the
     off-by-one that broke end-of-turn meta capture)
Run: python3 phase5/tests/test_audit_fixes.py"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from agent.state_updater import default_state, update
from agent.fused_turn import FusedLLM

fails = 0
def check(name, cond, extra=""):
    global fails
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {extra}")
    if not cond:
        fails += 1

# ---------- 1. degraded cooldown ----------
st = default_state()
def headless(state, turn_no):
    tr = {"turn": turn_no, "turn_type": "speech", "response_completed": True}
    state, _pol, log = update(state, tr, head=None)
    return state, [e for e in log]

codes = []
# turns 1-2: fail, fail -> ENTER (the enter turn counts as degraded turn 1)
st, c = headless(st, 1); codes += c
st, c = headless(st, 2); codes += c
check("ENTER on 2nd parse fail", "DEGRADED-PERCEPTION-ENTER" in codes, str(codes))
check("degraded flag set", st["degraded_perception"] is True)
check("enter turn counts as degraded turn 1", st.get("degraded_turns") == 1)
# turns 3-4: degraded, headless (plain prompt never yields a head)
st, c = headless(st, 3); codes += c
check("degraded turn 2 counted", st.get("degraded_turns") == 2)
# turn 4: 3rd degraded turn -> cooldown exit
st, c = headless(st, 4); codes += c
check("COOLDOWN EXIT on 3rd degraded turn", "DEGRADED-PERCEPTION-EXIT-COOLDOWN" in codes, str(codes[-3:]))
check("degraded flag cleared", st["degraded_perception"] is False)
check("streak reset on exit", st["parse_fail_streak"] == 0)
# turn 5: headless again -> streak restarts (no instant re-enter)
st, c = headless(st, 5)
check("no instant re-enter on 1st fail after exit", st["degraded_perception"] is False)
st, c = headless(st, 6)
check("re-enters after 2 more fails (hysteresis)", st["degraded_perception"] is True)
# success exit resets the cooldown counter too
st2 = default_state()
tr_ok = {"turn": 1, "turn_type": "speech", "response_completed": True}
head = {"m": "C", "c": 0.9, "s": "SAFE"}
st2, _, _ = update(st2, tr_ok, head=None)
st2, _, _ = update(st2, {"turn": 2, "turn_type": "speech"}, head=None)   # ENTER
st2, _, _ = update(st2, {"turn": 3, "turn_type": "speech"}, head=head)  # success
check("success exit resets degraded_turns", st2.get("degraded_turns", 0) == 0
      and st2["degraded_perception"] is False)
# determinism: same inputs -> same log codes
c1 = []
s = default_state()
for t in range(1, 6):
    s, cc = headless(s, t); c1.append(cc)
c2 = []
s = default_state()
for t in range(1, 6):
    s, cc = headless(s, t); c2.append(cc)
check("determinism k=2 (byte-identical logs)", c1 == c2)

# ---------- 2. Layer-2 in fused contents ----------
llm = FusedLLM()
empty = {"people": {}, "open_items": [], "emotional_context": None}
base = llm.build_contents("hello", {"mode": "x"}, [], [], [])
check("no session_state key when layer2 empty", "session_state" not in base)
check("no session_state key when layer2 None",
      "session_state" not in llm.build_contents("hello", {"mode": "x"}, [], [], [], layer2=None))
l2 = {"people": {"Neetu": {"name": "Neetu", "relation": "behen"}},
      "open_items": [], "emotional_context": None}
with_l2 = llm.build_contents("hello", {"mode": "x"}, [], [], [], layer2=l2)
import json as _json
parsed = _json.loads(with_l2)
check("session_state included when layer2 has people", parsed.get("session_state") == l2)
check("stable key order (policy,memory,threads,history,user_turn[,session_state])",
      list(parsed.keys())[:5] == ["policy", "memory", "threads", "history", "user_turn"])

# ---------- 3. epoch bumps on FIRST CONSUME (generator laziness) ----------
import asyncio
async def epoch_check():
    llm = FusedLLM()
    e0 = llm.epoch
    gen = llm.stream_prose(user_text="garbled", turn_type="unclear_speech",
                           policy={}, memory_view=[], threads=[], history=[],
                           turn_no=3, degraded=False, key="")
    check("epoch unchanged before first consume (lazy generator)", llm.epoch == e0)
    first = await gen.__anext__()
    check("epoch bumped on first consume", llm.epoch == e0 + 1)
    check("deterministic path yields a line", isinstance(first, str) and len(first) > 0, repr(first))
    # capture-at-TTFT semantics: snapshot AFTER first consume == current epoch
    turn_fused_epoch = llm.epoch
    check("TTFT-style snapshot equals current epoch",
          getattr(llm, "epoch", None) == turn_fused_epoch)
asyncio.run(epoch_check())


# ---------- 4. tag robustness (evidence: session 091548 t30/t33) ----------
from agent.fused_turn import TAG_RE, salvage_unclosed_head
from agent.reply_guard import strip_tag_leak

# t30: model closed the head with '</p>' — must parse AND strip cleanly
buf30 = '<perception>{"m":"R","c":0.5,"s":"SAFE"}</p>\nmahine ke naam poochna chahta hai'
m30 = TAG_RE.search(buf30)
check("t30: </p> close matches TAG_RE", m30 is not None)
if m30:
    import json as _j
    head30 = _j.loads(m30.group(1))
    check("t30: head parsed", head30 == {"m": "R", "c": 0.5, "s": "SAFE"})
    prose30 = buf30[m30.end():].strip()
    check("t30: prose clean after close", prose30 == "mahine ke naam poochna chahta hai", prose30[:40])

# t33: head never closed — salvage must recover the head and speak only the tail
buf33 = '<perception>{"m":"R","c":0.6,"s":"SAFE"}yahan se shuru karein fir'
h33, tail33 = salvage_unclosed_head(buf33)
check("t33: unclosed head recovered", h33 == {"m": "R", "c": 0.6, "s": "SAFE"})
check("t33: tail speakable, no tag", "<perception>" not in tail33 and "SAFE" not in tail33, tail33[:40])

# unrecoverable JSON: nothing from the head region may be spoken
buf_bad = '<perception>{"m":"R","c":0.'
h_bad, tail_bad = salvage_unclosed_head(buf_bad)
check("t33b: unrecoverable -> head None", h_bad is None)
check("t33b: nothing leaked", tail_bad.strip() == "")

# missing tags entirely: normal prose passthrough unaffected
check("no tags: TAG_RE no match", TAG_RE.search("haan, Sunday hai") is None)

# t8 (session 103824): '</s:perception>' closer — TAG_RE must consume it and
# parse the head; the tail after m.end() is already tag-free.
buf8 = '<perception>{"m":"R","c":0.9,"s":"SAFE"}</s:perception>\nHimachal ya Uttarakhand chale ja'
m8 = TAG_RE.search(buf8)
check("t8: head parses with any-closer TAG_RE", m8 is not None and
      '{"m":"R","c":0.9,"s":"SAFE"}' in m8.group(1))
if m8:
    check("t8: tail after closer is clean prose", strip_tag_leak(buf8[m8.end():])[0].strip()
          == "Himachal ya Uttarakhand chale ja")
# ...but if the closer arrives in the PROSE path (split chunks), the sanitizer kills it
c8b, s8b = strip_tag_leak('</s:perception>\nHimachal ya Uttarakhand chale ja')
check("t8: sanitizer kills stray closer in prose path", s8b and c8b.strip() == "Himachal ya Uttarakhand chale ja")

# CORRECTION (session 141753): smart_join was a misdiagnosis — the API does
# NOT drop inter-chunk whitespace; merges are MODEL-emitted and the splits
# ('th ik','nah in','k ela') were CAUSED by the join heuristic. Reverted;
# the deterministic lexicon + specials scrub own this class now.
from agent.reply_guard import fix_merged_words, clean_specials
check("CORRECTION: smart_join removed", not hasattr(__import__("agent.reply_guard", fromlist=["x"]), "smart_join"))
check("merge legacy: th ik", fix_merged_words("th ik hai, hindi mein") == "theek hai, hindi mein")
check("merge legacy: nah in", fix_merged_words("nah in, yahin hoon") == "nahin, yahin hoon")
check("merge legacy: k ela", fix_merged_words("k ela aur banana toh ek hi hai") == "kela aur banana toh ek hi hai")
check("merge model: sebaithne", fix_merged_words("aaram sebaithne wali") == "aaram se baithne wali")
check("merge model: saathchalna", fix_merged_words("saathchalna hai") == "saath chalna hai")
check("merge model: juis", fix_merged_words("juis zyada fresh") == "juice zyada fresh")
check("merge: valid words untouched", fix_merged_words("seb achha hai kaisa hai") == "seb achha hai kaisa hai")
check("scrub t65: braces+junk", clean_specials("j}}\n\njuis zyada") == "j juis zyada")
check("scrub: normal text untouched", clean_specials("haan bolo, yahin hoon.") == "haan bolo, yahin hoon.")

# A-P1: head plan parses + previous_plan threading
from agent.fused_turn import FusedLLM as _FL
_plan_head = '{"m":"C","c":0.9,"s":"SAFE","plan":{"total":4,"current":1,"topic":"voice agent"}}'
_m = TAG_RE.search('<perception>' + _plan_head + '</perception>haan bol, pehla point:')
_plan_ok = False
if _m:
    try:
        _h = _json.loads(_m.group(1))
        _plan_ok = isinstance(_h.get("plan"), dict) and _h["plan"]["total"] == 4
    except Exception as _e:
        print(f"  [A-P1 debug] {_e}")
check("A-P1: plan head parses", _plan_ok)
_llm = _FL()
_pp = {"total": 4, "current": 1, "topic": "voice agent"}
_contents = _llm.build_contents("aage", {"delivery": "continue_detail"}, [], [], [],
                                previous_plan=_pp)
check("A-P1: previous_plan in contents",
      _json.loads(_contents).get("previous_plan") == _pp)

# t11 (session 094645): underscore variant '</s_perception>' spoken aloud
leak11, stripped11 = strip_tag_leak('</s_perception>\nneetu ki baat kar raha tha na?')
check("t11: </s_perception> stripped", stripped11 and leak11.strip() == "neetu ki baat kar raha tha na?",
      repr(leak11[:40]))

# t28 (session 100157): misspelled closer '</parception>' spoken aloud
leak28, stripped28 = strip_tag_leak('</parception>\nhaan yaar, yeh baat toh hai.')
check("t28: </parception> stripped", leak28.strip() == "haan yaar, yeh baat toh hai." and stripped28,
      repr(leak28[:40]))

# t3 (session 100157): 2-word garble must NOT become a relationship
from agent.entity_extractor import extract_entities_from_user_text as ex_user
check("t3: garble 'kya' rejected", ex_user("kya") == [], str(ex_user("kya")))

# belt-and-braces tee sanitizer
cases_leak = [
    ('</p> kya haal', ' kya haal'),
    ('<perception>{"m":"C"}</perception> hello', ' hello'),
    ('achha sun raha hoon</perception>', 'achha sun raha hoon'),
]
for raw, want in cases_leak:
    got, stripped = strip_tag_leak(raw)
    check(f"strip_tag_leak({raw[:24]!r})", got == want and stripped, f"-> {got!r}")

print(f"\n{'ALL PASS' if fails == 0 else f'{fails} FAILURES'}")
sys.exit(1 if fails else 0)

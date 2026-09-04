"""Numeric perception audit — READ-ONLY evidence tool (2026-09-04).

Reproduces every table in docs/NUMERIC_PERCEPTION_AUDIT.md from the shipped
code (no code change, no new rule). Run:

    python3 phase5/harness/numeric_audit.py

Sections
  1. live replay of session_20260904_133627 (build b228048) — proves the
     offline chain reproduces all 12 spoken lines verbatim, so every later
     probe is about the same code path the user heard
  2. same digits, different STT surface -> different OPERATION
  3. lexicon-gated observation (one out-of-vocabulary digit word deletes the
     whole observation and flips the sentence into a confirm)
  4. count-vs-digit ambiguity in the normalizer ('5 जीरो')
  5. the text echo filter vs the user's own confirmation words
  6. digit error table across the four real sessions
"""
from __future__ import annotations

import difflib
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from agent.precision_rail import (decide, dictation_value, normalize_span,  # noqa: E402
                                  _is_full_restatement, _parse_correction,
                                  _apply_correction, DIGIT_WORD_MAP)
import agent.precision_rail as pr  # noqa: E402
from agent.value_transaction import mark_heard, resolve_edit  # noqa: E402
from providers.stt import devanagari_to_roman  # noqa: E402

# --------------------------------------------------------------------------
# Session 133627 — exact STT strings. The stage diagnostic prints stt[:60];
# t12 and t17 were cut there. t12's digits are recovered from the agent's own
# echo line ("one two five two zero five two zero three") and verified by the
# fidelity check below (the replay must reproduce that echo verbatim).
# --------------------------------------------------------------------------
S133627 = [
    (2, "यह बढ़ने है नंबर लिख में दूसरे बता रहा हूं", "played",
     "haan, bol number — main sun raha hoon."),
    (3, "026900", None, ""),
    (4, "1, 2, 5, 8, 0, 1, 2, 0, 3", "played",
     "theek hai, yeh note kiya: one two five eight zero one two zero three. confirm kar de?"),
    (5, "आई दूने नंबर मेरा सुना था मैंने क्या बोला था", "llm_empty", ""),
    (6, "लिखा", None, ""),
    (7, "हेलो", "interrupted", "hello! bol kya scene hai?"),
    (8, "एक बार फिर से", None, ""),
    (9, "एक बार फिर से बोल दूने क्या लिखा है?", "played",
     "naya: one two five eight zero one two zero three — pehle wala zero two six nine zero zero tha. yeh naya sahi hai na?"),
    (10, "पूरा बोल पूरा नंबर 026900 आगे", "played",
     "theek hai, yeh note kiya: zero two six nine zero zero. confirm kar de?"),
    # t11 'ठीक है' never reached the engine (dropped before the rail — §5)
    (12, "अब इसके आगे और नंबर continue होगा, आगे है 1, 2, 5, 2, 0, 5, 2, 0, 3", "played",
     "haan, main suna: one two five two zero five two zero three. sahi hai na?"),
    (13, "12520 नहीं है, 12520 है", "played", "haan, bol — kaunsa hissa badalna hai?"),
    (14, "इसमें जो 5 को हटा दे", None, ""),
    (15, "और वहाँ पे 5 जीरो लगा", "played",
     "yeh samjha maine: one two five two zero five two zero three — sahi hai na?"),
    (16, "नहीं नहीं, मैं दुबारा बोलता हूँ", "cancel_pre_audio",
     "maine yeh samjha: one two five two zero five two zero three — pehle wala zero two six nine zero zero tha. sahi hai na?"),
    (17, "देख जीरो टू सिस नाइन डबल जीरो ये ठीक है इसको लिख कर रख लिया ", "interrupted",
     "main suna: one two five two zero five two zero three. yeh hi hai na?"),
    (18, "अब उसके पास...", None, ""),
    (19, "देखो वो है 125205203", None, ""),
    (20, "समझा तू?", None, ""),
    (21, "हलो", "interrupted", "hello! kaise ho?"),
    (22, "और समझा दू", None, ""),
    (23, "बोल", None, ""),
]


def _run(turns, upto=None, heard_override=None):
    eng = {"dictation": None, "conv": {}}
    rows = []
    for tn, text, pb, _live in turns:
        if upto is not None and tn > upto:
            break
        d = decide(text, eng, tn) or {}
        dic = eng.get("dictation") or {}
        rows.append((tn, text, d, dic.get("value"),
                     (dic.get("proposal") or {}).get("derived"),
                     bool(dic.get("pending_edit")), dic.get("status")))
        if d.get("line"):
            heard = (pb == "played") if heard_override is None else heard_override
            mark_heard(eng, d, tn, heard=heard)
    return eng, rows


def section1():
    print("=" * 78)
    print("1. session_20260904_133627 replayed through the shipped chain (b228048)")
    print("=" * 78)
    _, rows = _run(S133627)
    live = {tn: l for tn, _, _, l in S133627}
    mism = 0
    for tn, text, d, base, prop, hold, status in rows:
        line = d.get("line") or ""
        ok = (line == live[tn]) if (line or live[tn]) else True
        mism += 0 if ok else 1
        print(f"{'  ' if ok else '!!'}t{tn:<3}{(d.get('action') or 'LLM'):18}"
              f"base={base!r:10} prop={prop!r:12} hold={'Y' if hold else '-'} {status or '-':11}"
              f"| {line[:56]!r}")
    print(f"\n   spoken-line mismatches vs the live log: {mism} of 12 "
          f"(t11 excluded — it never reached the engine)")


def section2():
    print("\n" + "=" * 78)
    print("2. same digits, different STT surface form -> different OPERATION")
    print("=" * 78)
    for s in ["125801203", "1, 2, 5, 8, 0, 1, 2, 0, 3", "1 2 5 8 0 1 2 0 3", "1258 01203"]:
        v = dictation_value(s)
        n = normalize_span(v or "")
        op = "REPLACE (fresh proposal)" if _is_full_restatement(s, len(n)) else "APPEND (continuation)"
        print(f"   {s!r:30} digits={n:10} while a value is pending -> {op}")
    print("\n   t12 with explicit continuation words, two surface forms of the SAME digits:")
    head = S133627[:9]
    for form in ["1, 2, 5, 2, 0, 5, 2, 0, 3", "125205203"]:
        t12 = (12, f"अब इसके आगे और नंबर continue होगा, आगे है {form}", "played", "")
        eng, rows = _run(head + [t12])
        tn, text, d, base, prop, hold, status = rows[-1]
        print(f"   ...आगे है {form!r:28} -> {d.get('action'):18} base={base!r} prop={prop!r}")


def section3():
    print("\n" + "=" * 78)
    print("3. lexicon-gated observation: t17 'जीरो टू सिस नाइन डबल जीरो ये ठीक है...'")
    print("=" * 78)
    for s in ["जीरो टू सिस नाइन डबल जीरो", "जीरो टू सिक्स नाइन डबल जीरो"]:
        v = dictation_value(s)
        print(f"   {s!r:34} 'सिस' in lexicon={('सिस' in DIGIT_WORD_MAP)!s:5} "
              f"observation={v!r} digits={normalize_span(v or '')!r}")
    # counterfactual: proposal echo HEARD (t16 not cancelled), then t17 as spoken live
    base = [t for t in S133627 if t[0] <= 15]
    for wording, label in [("देख जीरो टू सिस नाइन डबल जीरो ये ठीक है इसको लिख कर रख लिया", "live wording (सिस)"),
                           ("देख जीरो टू सिक्स नाइन डबल जीरो ये ठीक है इसको लिख कर रख लिया", "same with सिक्स")]:
        eng, rows = _run(base + [(17, wording, "played", "")], heard_override=True)
        tn, text, d, b, prop, hold, status = rows[-1]
        print(f"   echo heard + t17 {label:20} -> {d.get('action'):14} status={status:10} "
              f"base={b!r} prop={prop!r}")
    print("   (live, the proposal echo was UNHEARD, so L2 re-echoed instead of committing)")


def section4():
    print("\n" + "=" * 78)
    print("4. count-vs-digit ambiguity in normalization")
    print("=" * 78)
    for s in ["5 जीरो", "पांच जीरो", "5 बार जीरो", "डबल जीरो", "4 बार 0", "420"]:
        print(f"   normalize({s!r:14}) = {normalize_span(s)!r}")
    joined = ["12520 नहीं है, 12520 है", "इसमें जो 5 को हटा दे", "और वहाँ पे 5 जीरो लगा"]
    print(f"\n   t13 alone           -> _parse_correction = {_parse_correction(joined[0])}")
    print(f"   t13+t14+t15 joined  -> _parse_correction = {_parse_correction(' '.join(joined))}")
    buf = {"fragments": joined}
    print(f"   resolve_edit vs BASE '026900'          -> {resolve_edit(buf, '026900')}")
    print(f"   resolve_edit vs OPEN PROPOSAL '125205203' -> {resolve_edit(buf, '125205203')}")


def _is_echo(t, a):
    nt = re.sub(r'[^\w\s]', '', t.lower()).strip()
    na = re.sub(r'[^\w\s]', '', a.lower()).strip()
    tw, aw = nt.split(), na.split()
    if not tw or not aw:
        return 0.0
    ws = len(tw)
    if ws >= len(aw):
        return difflib.SequenceMatcher(None, nt, na).ratio()
    mx = 0.0
    for w in (ws, ws + 1):
        if w > len(aw):
            continue
        for i in range(len(aw) - w + 1):
            mx = max(mx, difflib.SequenceMatcher(None, nt, " ".join(aw[i:i + w])).ratio())
    return mx


def section5():
    print("\n" + "=" * 78)
    print("5. main.is_echo (text, threshold 0.65) vs the user's confirmation words")
    print("=" * 78)
    t10_line = "theek hai, yeh note kiya: zero two six nine zero zero. confirm kar de?"
    r = devanagari_to_roman("ठीक है")
    print(f"   t11 'ठीक है' -> romanized {r!r}; similarity to the t10 line = "
          f"{_is_echo(r, t10_line):.2f} (> 0.65 => dropped as Aiva's own echo)")

    class _D(dict):
        def __missing__(self, k):
            return "zero two six nine zero zero one two five"
    pools = {k: v for k, v in vars(pr).items()
             if k.endswith("_LINES") and isinstance(v, (list, tuple))}
    lines = [(k, l.format_map(_D())) for k, v in pools.items() for l in v]
    print(f"   {len(lines)} rail lines in {len(pools)} pools; a reply within 1.5 s of audio end is dropped after:")
    for u in ["हाँ", "हां", "ठीक है", "सही है", "हाँ सही है", "बस", "नहीं"]:
        r = devanagari_to_roman(u)
        eaten = sum(1 for _, l in lines if _is_echo(r, l) > 0.65)
        print(f"      {u!r:12} -> {eaten:2}/{len(lines)} lines")


def _lev(a, b):
    d = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i in range(len(a) + 1):
        d[i][0] = i
    for j in range(len(b) + 1):
        d[0][j] = j
    for i in range(1, len(a) + 1):
        for j in range(1, len(b) + 1):
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1,
                          d[i - 1][j - 1] + (a[i - 1] != b[j - 1]))
    return d[-1][-1]


def section6():
    print("\n" + "=" * 78)
    print("6. STT digit evidence — one speaker, one number, four sessions")
    print("=" * 78)
    print("   Ground truth is NOT available (no user audio retained). Two honest references:")
    print("   (T) the user's OWN in-session corrections: 103339 t8/t9, 131245 t14/15/17,")
    print("       133627 t13/14/15 all say the group STT wrote as '5 2 0' is 'paanch baar zero'")
    print("       = 00000 (lock §8 target 02690012000005203 was owner-approved on that basis);")
    print("   (C) consistency: two renderings of the same segment by the same speaker that")
    print("       differ prove at least one of them wrong, without knowing which.")
    R = [  # session, turn, raw STT, segment, rendering
        ("103339", "t5", "026900125205203", "head+mid+tail"),
        ("125037", "t3", "026", "head"),
        ("125037", "t4", "9, 0, 0, 1, 2, 5, 0, 1, 2, 0, 3.", "head-rest+mid+tail"),
        ("125037", "t6", "02690012501203", "head+mid+tail"),
        ("131245", "t5", "026", "head"),
        ("131245", "t6", "9000", "head-rest"),
        ("131245", "t7", "1, 2, 5, 6, 0, 1, 2, 0, 3", "mid+tail"),
        ("131245", "t11", "026900125201203", "head+mid+tail"),
        ("133627", "t3", "026900", "head"),
        ("133627", "t4", "1, 2, 5, 8, 0, 1, 2, 0, 3", "mid+tail"),
        ("133627", "t10", "पूरा बोल पूरा नंबर 026900 आगे", "head"),
        ("133627", "t12", "अब इसके आगे और नंबर continue होगा, आगे है 1, 2, 5, 2, 0, 5, 2, 0, 3", "mid+tail"),
        ("133627", "t17", "देख जीरो टू सिस नाइन डबल जीरो ये ठीक है इसको लिख कर रख लिया", "head (digit words)"),
        ("133627", "t19", "देखो वो है 125205203", "mid+tail"),
    ]
    print(f"\n   {'sess':7}{'turn':5}{'observed digits':18}{'form':10}segment")
    for s_, t, raw, seg in R:
        span = dictation_value(raw)
        obs = normalize_span(span or "")
        form = ("NO-OBS" if span is None else
                "words" if re.search(r"[\u0900-\u097F]", span) else
                "separated" if re.search(r"\d[\s,]+\d", span) else "run")
        print(f"   {s_:7}{t:5}{(obs or '∅'):18}{form:10}{seg}")
    print("\n   HEAD '026900' (6 digits): 8 renderings -> 7 exact, 1 contradiction ('9000' at")
    print("   131245 t6 vs '900' inside the same session's t11), 1 NO observation (t17: OOV 'सिस').")
    print("   MIDDLE group (user: 'paanch baar zero' = 00000): flowing-dictation renderings")
    print("   520 | 5,2,0 | 5,6,0,1 | 5,8,0,1 | 5,0,1 | 5,2,0,1  -> zeros rendered 0/8 times.")
    print("   Rendered with 'बार' ONLY in slow correction sentences (103339 t9 '5 बार 0',")
    print("   131245 t15 '5 बार 0') and even then also as '0000' (131245 t17, four zeros) and")
    print("   '5 जीरो' (133627 t15, normalizer -> '50'). Documented before: smoke-10 '4 बार 0'")
    print("   -> '420'; smoke-12 '5 बार 0' -> '5 x 0'.")
    print("   TAIL group: '5203' (103339, 133627 x2) vs '1203' (125037 x2, 131245 x2) -")
    print("   cross-session contradiction, unresolvable from text.")
    print("\n   Within-session contradictions of the SAME 9-digit tail segment:")
    for a, b, la, lb in [("125801203", "125205203", "133627 t4", "133627 t12"),
                         ("125601203", "125201203", "131245 t7", "131245 t11 tail"),
                         ("12501203", "12501203", "125037 t4 tail", "125037 t6 tail")]:
        print(f"      {la:16} {a:11} vs {lb:16} {b:11} -> {_lev(a, b)} digit edit(s)")
    print("   => 2 of the 3 sessions with two renderings contradict themselves; under (T) all 8")
    print("      tail renderings contain >=1 error; under (C) alone, >=3 of 8 do.")
    print("\n   Under (T), per-utterance digit edit distance to the user-asserted sequence:")
    T = {"103339": "02690012000005203", "133627": "02690012000005203", "131245": "02690012000001203"}
    rows = [("103339", "t5", "026900125205203"), ("133627", "t3", "026900"), ("133627", "t4", "125801203"),
            ("133627", "t12", "125205203"), ("133627", "t19", "125205203"), ("131245", "t7", "125601203"),
            ("131245", "t11", "026900125201203")]
    tot_ref = tot_err = 0
    for s_, t, obs in rows:
        ref = T[s_]
        exp = ref if len(obs) >= 15 else (ref[:6] if obs.startswith("026") and len(obs) == 6 else ref[6:])
        e = _lev(exp, obs); tot_ref += len(exp); tot_err += e
        print(f"      {s_} {t:4} expected {exp:18} observed {obs:16} edits={e}")
    print(f"      -> {tot_err} edits over {tot_ref} reference digits ≈ {tot_err / tot_ref:.0%} digit edit rate "
          f"(n=7 utterances; every error is in the zero-run / following group)")
    print("\n   whisper avg_logprob (from the diagnostics) does not separate right from wrong:")
    print("      head-correct: 133627 t3 -0.25, 131245 t5 -0.48 | rejected-by-user: 133627 t4 -0.20,")
    print("      t12 -0.11, t19 -0.16, 131245 t7 -0.22, t11 -0.43 | OOV no-observation: 133627 t17 -0.13")


if __name__ == "__main__":
    section1(); section2(); section3(); section4(); section5(); section6()

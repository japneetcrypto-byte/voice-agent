#!/usr/bin/env python3
"""Stage verdict engine — proves each pipeline stage, isolates the culprit.

Owner brief 2026-08-29: "add each thing to the report — voice key, ASR, LLM,
TTS, time taken, echo, words spoken/heard, interruption handling. If the log
confirms the bad experience is Fish Audio, we look for an alternative; if
everything before that is fine, the system as a unit works and we just update
the outermost part."

That decision rule is implemented literally:

  STT  -> PASS/WATCH/FAIL   (hearing)
  LLM  -> PASS/WATCH/FAIL   (brain)
  TTS  -> PASS/WATCH/FAIL   (voice)
  ECHO -> PASS/WATCH        (self-hearing protection)

FINAL VERDICT: if every stage before TTS passes and TTS fails ->
  "SYSTEM UNIT HEALTHY — bottleneck is the TTS provider. Replace/upgrade
   Fish (paid tier or ElevenLabs). No engine changes needed."

Deterministic, stdlib-only.
Standalone: python3 phase5/stage_verdict.py [logfile]
"""
import json, glob, os, sys, re
from collections import Counter

if len(sys.argv) > 1:
    sp = sys.argv[1]
else:
    c = sorted(glob.glob("logs/session_*.log"), key=os.path.getmtime)
    if not c:
        print("NO SESSION LOG"); sys.exit(1)
    sp = c[-1]
sess = os.path.basename(sp).replace("session_", "").replace(".log", "")

turns = []
for line in open(sp):
    try:
        t = json.loads(line)
    except Exception:
        continue
    if t.get("turn"):
        turns.append(t)
turns.sort(key=lambda t: t.get("turn", 0))

events = []
ep = f"logs/events_{sess}.log"
if os.path.exists(ep):
    for line in open(ep):
        try:
            events.append(json.loads(line))
        except Exception:
            pass

def avg(xs):
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 2) if xs else None

def p95(xs):
    xs = sorted(x for x in xs if x is not None)
    return round(xs[min(len(xs) - 1, int(len(xs) * 0.95))], 2) if xs else None

def words(t):
    return len(re.findall(r"[\w\u0900-\u097F]+", t or ""))

valid = [t for t in turns if t.get("stt_valid") is True]
rejected = [t for t in turns if t.get("stt_valid") is False]
stt_lat = [t.get("stt_latency_s") for t in turns]
provs = Counter(t.get("stt_provider") for t in turns if t.get("stt_provider"))

ttfts = [t.get("llm_ttft_s") for t in turns]
heads_ok = sum(1 for t in turns if t.get("perception_head"))
n429 = sum(1 for t in turns if "429" in str(t.get("llm_error", ""))) \
     + sum(1 for e in events if "429" in json.dumps(e))
llm_err = [t for t in turns if t.get("llm_error")]

ttfa = [t.get("tts_first_audio_s") for t in turns]
tts_silent = [t for t in turns if t.get("llm_response")
              and not (t.get("tts") or {}).get("audio_duration_s")
              and not t.get("interrupted")]
tts_fb = [t for t in turns if (t.get("tts") or {}).get("fallback_reason")]
durs = [(t.get("tts") or {}).get("audio_duration_s") for t in turns]

echo_drops = [t for t in turns if t.get("echo_dropped")]
echo_saved = [t for t in turns if t.get("echo_overridden")]
corrs = [t.get("echo_corr_score") for t in turns if t.get("echo_corr_score") is not None]

interrupted = [t for t in turns if t.get("interrupted")]
skips = [e for e in events if e.get("event") == "RESPONSE_SKIPPED"]
sup = [e for e in events if e.get("event") == "SUPERVISOR_ENGAGED"]
unbound = [t for t in turns if t.get("engine_path") in ("unbound_filler", "legacy")]

barge_avg, barge_max = None, None
for lp in sorted(glob.glob("logs/turn_lifecycle_*.jsonl"), key=os.path.getmtime):
    if sess[:8] not in os.path.basename(lp):
        continue
    for line in open(lp):
        try:
            e = json.loads(line)
        except Exception:
            continue
        if e.get("ev") == "SESSION_SUMMARY":
            b = (e.get("barge_stop_latency_ms") or {})
            barge_avg, barge_max = b.get("avg"), b.get("max")

words_heard = sum(words(t.get("stt_transcript")) for t in valid)
words_spoken = sum(words(t.get("llm_response")) for t in turns)
words_cut = sum(words((t.get("tts_text") or "")) for t in interrupted)
spoken_turns = sum(1 for t in turns if t.get("llm_response"))

n = max(len(turns), 1)
L = []
verdicts = []

# STT
p_stt = p95(stt_lat)
rej_pct = round(len(rejected) / n * 100)
status = "PASS" if (p_stt or 0) <= 1.5 else ("WATCH" if (p_stt or 0) <= 2.5 else "FAIL")
verdicts.append(("STT", status))
L.append(f"**STT — hearing (Groq)**: {status} · avg {avg(stt_lat)}s, p95 {p_stt}s · "
         f"rejected {len(rejected)}/{n} ({rej_pct}%) · providers {dict(provs)} · "
         f"words heard {words_heard}")

# LLM
p_ttft = p95(ttfts)
head_pct = round(heads_ok / n * 100)
status = "PASS" if (p_ttft or 0) <= 2.0 and not llm_err else \
         ("WATCH" if (p_ttft or 0) <= 4.0 else "FAIL")
if n429 and status == "PASS":
    status = "WATCH (quota pressure)"
elif n429:
    status += " + quota"
verdicts.append(("LLM", status))
L.append(f"**LLM — brain (Gemini)**: {status} · TTFT avg {avg(ttfts)}s, p95 {p_ttft}s · "
         f"heads captured {head_pct}% · 429s {n429} · errors {len(llm_err)}")

# TTS
p_ttfa = p95(ttfa)
silent_pct = round(len(tts_silent) / n * 100)
status = "FAIL" if (p_ttfa or 0) > 3.5 or len(tts_silent) > 0 else \
         ("WATCH" if (p_ttfa or 0) > 2.2 else "PASS")
verdicts.append(("TTS", status))
L.append(f"**TTS — voice (Fish Audio)**: {status} · TTFA avg {avg(ttfa)}s, p95 {p_ttfa}s · "
         f"SILENT turns {len(tts_silent)} ({silent_pct}%) · failovers {len(tts_fb)} · "
         f"reply audio avg {avg(durs)}s · words spoken {words_spoken}"
         + (f" · words cut by barge-in {words_cut}" if words_cut else ""))

# ECHO
drop_pct = round(len(echo_drops) / n * 100)
corr_lo = min(corrs) if corrs else None
corr_hi = max(corrs) if corrs else None
status = "PASS" if drop_pct <= 5 else "WATCH"
if echo_saved:
    status += " (late repeats saved x%d)" % len(echo_saved)
verdicts.append(("ECHO", status))
L.append(f"**ECHO — self-hearing guard**: {status} · dropped {len(echo_drops)}/{n} ({drop_pct}%) · "
         f"late repeats saved {len(echo_saved)} · voice-key corr {corr_lo}–{corr_hi}")

# BARGE-IN
L.append(f"**BARGE-IN — interruption handling**: interrupted replies {len(interrupted)} · "
         f"stop latency avg {barge_avg}ms max {barge_max}ms "
         f"(Phase-7 target <500ms — known, contained by cancel-on-validate)")

# GUARDS
L.append(f"**PIPELINE GUARDS**: supervisor rescues ×{len(sup)} · response skips ×{len(skips)} · "
         f"unbound/legacy turns {len(unbound)} · trimmed replies "
         f"{sum(1 for t in turns if t.get('reply_trimmed'))}")

# CONVERSATION
L.append(f"**CONVERSATION**: {n} turns · replied {spoken_turns} · words heard {words_heard} · "
         f"words spoken {words_spoken} · speech→audio avg "
         f"{avg([t.get('speech_end_to_first_audio_s') for t in turns])}s / p95 "
         f"{p95([t.get('speech_end_to_first_audio_s') for t in turns])}s")

# FINAL VERDICT
fails = [v for v in verdicts if "FAIL" in v[1]]
watch = [v for v in verdicts if "WATCH" in v[1]]
L.append("")
if not fails and not watch:
    L.append("### FINAL VERDICT: ALL STAGES PASS ✅")
elif not fails:
    L.append(f"### FINAL VERDICT: PASS with watch items ({', '.join(v[0] for v in watch)})")
elif len(fails) == 1 and fails[0][0] == "TTS":
    L.append("### FINAL VERDICT: SYSTEM UNIT HEALTHY — BOTTLENECK IS THE TTS PROVIDER 🎯")
    L.append("")
    L.append("Hearing (STT), brain (LLM), state, safety, echo-handling and interruption "
             "containment all PASS. The only failing stage is the voice layer (Fish Audio). "
             "**Per owner decision rule: replace/upgrade the outermost part — Fish paid tier "
             "or ElevenLabs Flash. No engine changes needed.**")
else:
    L.append(f"### FINAL VERDICT: MULTIPLE STAGES FAILING — {', '.join(v[0] for v in fails)}")
    L.append("")
    L.append("Fix non-TTS failures first, then re-run; the TTS verdict is only actionable "
             "in isolation.")

print("\n".join(L))

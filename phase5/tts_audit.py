#!/usr/bin/env python3
"""Spoken-output audit — analyzes what Aiva actually SOUNDED like.

Prereq: run the worker with AIVA_TTS_DUMP=1 (one turn = one WAV + a manifest
line in logs/tts/). Then:

  python3 phase5/tts_audit.py                 # rates, outliers, clipping
  python3 phase5/tts_audit.py --asr           # + ASR round-trip (needs GROQ_API_KEY)
  python3 phase5/tts_audit.py --mos           # + SpeechMOS if installed (pip install speechmos)

What each check catches:
  chars/sec outliers -> merged words (TTS rushes garbage tokens), broken
                        prosody, or truncated audio
  clipping           -> distortion ("robotic/rough" complaints)
  ASR round-trip WER -> the TTS mangled pronunciation: if whisper can't
                        reproduce the text we fed it, humans hear it wrong too
  SpeechMOS          -> neural estimate of naturalness (1-5); track trend
"""
import json, glob, os, sys, wave, re

import numpy as np

dump_dir = os.path.join("logs", "tts")
manifest = os.path.join(dump_dir, "manifest.jsonl")
if not os.path.exists(manifest):
    print(f"NO MANIFEST at {manifest}. Run the worker with AIVA_TTS_DUMP=1 first.")
    sys.exit(1)

rows = [json.loads(l) for l in open(manifest) if l.strip()]
rows = [r for r in rows if r.get("duration_s")]
print(f"=== TTS AUDIT: {len(rows)} clips in {dump_dir} ===\n")

args = set(sys.argv[1:])
do_asr = "--asr" in args
do_mos = "--mos" in args

# ---- optional: ASR round-trip ----
asr_fn = None
if do_asr:
    try:
        sys.path.insert(0, os.getcwd())
        from providers.stt import GroqSTT
        groq_stt = GroqSTT()

        def asr_fn(path):
            data = wave.open(path, "rb").readframes(wave.open(path, "rb").getnframes())
            arr = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
            # manifest audio is 48k; GroqSTT expects 16k
            import scipy.signal as _s  # optional
            arr16 = _s.resample_poly(arr, 1, 3)
            return groq_stt.transcribe(arr16).text
    except Exception as e:
        print(f"[asr] round-trip unavailable: {e}\n")
        do_asr = False

# ---- optional: SpeechMOS ----
mos_fn = None
if do_mos:
    try:
        from speechmos import predict_mos
        def mos_fn(path):
            import soundfile as sf
            a, sr = sf.read(path)
            return float(predict_mos(a, sr))
    except Exception as e:
        print(f"[mos] SpeechMOS unavailable ({e}) — pip install speechmos soundfile\n")
        do_mos = False

def norm(t):
    return re.sub(r"[^\w\u0900-\u097F]+", " ", (t or "").lower()).strip()

fails = []
print(f"{'turn':>4} {'dur':>5} {'c/s':>5} {'peak%':>5}"
      + (" {'wer':>5}" if do_asr else "") + ("  mos" if do_mos else "") + "  text")
for r in rows:
    path = r.get("path")
    dur = r["duration_s"] or 0
    chars = r.get("chars") or 0
    rate = (chars / dur) if dur > 0.2 else 0

    peak = 0.0
    clicks = 0
    rate_ok = True
    if path and os.path.exists(path):
        with wave.open(path, "rb") as w:
            rate_ok = (w.getframerate() == 48000 and w.getsampwidth() == 2
                       and w.getnchannels() == 1)
            data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        peak = float(np.max(np.abs(data))) / 32767.0 * 100 if len(data) else 0.0
        # assembly-glitch heuristic: a discontinuity far beyond the local
        # slope (streaming resampler/queue artifacts read as clicks/stutters)
        if len(data) > 480:
            d = np.abs(np.diff(data.astype(np.int32)))
            local = np.convolve(d, np.ones(48) / 48, mode="same") + 50
            clicks = int(np.sum(d > np.maximum(local * 12, 800)))

    flags = []
    if rate and rate > 20 and dur >= 1.5:
        flags.append("FAST?")  # short (<1.5s) questions are naturally fast — skip
    if rate and rate < 7:
        flags.append("SLOW?")
    if peak > 99:
        flags.append("CLIP!")
    elif peak >= 95:
        flags.append("HOT?")  # near-clipping — listen for harshness
    if clicks > 0:
        flags.append(f"CLICKS({clicks})")  # assembly/resampler artifacts
    if not rate_ok:
        flags.append("FMT!")  # not 48k/16-bit/mono

    wer_s, mos_s = "", ""
    if do_asr and path:
        try:
            hyp = asr_fn(path)
            import difflib
            ratio = difflib.SequenceMatcher(None, norm(hyp), norm(r.get("text"))).ratio()
            wer_s = f" {ratio:5.2f}"
            if ratio < 0.80:
                flags.append(f"MUTTER?(asr={hyp[:24]!r})")
        except Exception as e:
            wer_s = "  err"
    if do_mos and path:
        try:
            m = mos_fn(path)
            mos_s = f" {m:4.2f}"
            if m < 3.2:
                flags.append("LOW-MOS")
        except Exception as e:
            mos_s = "  err"

    line = (f"{r.get('turn', 0):>4} {dur:5.2f} {rate:5.1f} {peak:5.1f}"
            + (f" c{clicks:>2}" if clicks else "   ")
            + wer_s + mos_s + "  " + (r.get("text") or "")[:52])
    print(line + ("   " + " ".join(flags) if flags else ""))
    if flags:
        fails.append((r.get("turn"), flags))

# correlation: does quality degrade with length? (directive 2026-08-29)
buckets = {"short(<2s)": [], "mid(2-5s)": [], "long(>5s)": []}
for r in rows:
    dur = r["duration_s"] or 0
    if dur <= 0:
        continue
    rate = (r.get("chars") or 0) / dur
    b = "short(<2s)" if dur < 2 else ("mid(2-5s)" if dur <= 5 else "long(>5s)")
    buckets[b].append(rate)
print("\n--- LENGTH vs SPEECH RATE (clarity proxy) ---")
for b, rs in buckets.items():
    if rs:
        import statistics
        print(f"{b}: n={len(rs)} avg_c/s={statistics.mean(rs):.1f} "
              f"stdev={statistics.pstdev(rs):.1f}")
print("reading: long clips whose c/s DEVIATES from short clips indicate "
      "rate/assembly degradation on longer synthesis.")

print(f"\n--- SUMMARY ---")
rates = [(r.get("chars") or 0) / (r["duration_s"] or 1) for r in rows if (r["duration_s"] or 0) > 0.2]
if rates:
    print(f"chars/sec: avg={sum(rates)/len(rates):.1f} min={min(rates):.1f} max={max(rates):.1f} "
          f"(healthy Hinglish ≈ 10-18)")
print(f"flagged clips: {len(fails)}")
for t, f in fails:
    print(f"  turn {t}: {', '.join(f)}")

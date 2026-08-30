#!/usr/bin/env python3
"""STT rejection & recovery-quality report (task 2026-08-30, sign-off CA6).

Answers, from the session logs (no live run needed):
  1. What is the REJECTION RATE, and by which reason?
  2. Are "clearly spoken" turns being rejected as high_no_speech_prob?
     Cross-tab rejection vs acoustic features (rms/peak/duration) and vs
     agent-speaking context (was Aiva speaking / how long since its audio
     ended) — the echo/contamination hypothesis.
  3. Was the suspicious-band change going to matter? (turns with
     no_speech_prob in [0.5, 0.6) currently logged)
  4. RECOVERY QUALITY: do contextual_recovery turns stay short +
     checkpoint-oriented (per locked policy), and what fraction were
     actually rejected-but-meaningful?

Usage: python3 phase5/stt_rejection_report.py [session_log_path ...]
Default: newest session log + all logs under logs/session_*.log
"""
import glob, json, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

REASONS = ["empty_transcript", "known_hallucination_pattern", "punctuation_only",
           "high_no_speech_prob", "suspicious_no_speech_band", "low_avg_logprob",
           "catastrophic_low_confidence"]
BAND_MIN, BAND_MAX = 0.5, 0.6


def load_turns(paths):
    turns = []
    for p in paths:
        if not os.path.exists(p):
            print(f"  (missing log file: {p})")
            continue
        try:
            for line in open(p, encoding="utf-8"):
                line = line.strip()
                if not line:
                    continue
                try:
                    t = json.loads(line)
                except Exception:
                    continue
                if t.get("turn") is None:
                    continue
                turns.append(t)
        except FileNotFoundError:
            pass
    return turns


def main():
    if len(sys.argv) > 1:
        paths = sys.argv[1:]
    else:
        files = sorted(glob.glob("logs/session_*.log"), key=os.path.getmtime)
        paths = files[-3:] if files else []   # recent 3 sessions by default
    turns = load_turns(paths)
    if not turns:
        print("No session turns found. Pass log paths or run after a live session.")
        return 1

    n = len(turns)
    counts = {r: 0 for r in REASONS}
    counts["accepted"] = 0
    band_turns = []
    nsp_rejects = []
    recovery = []
    ac_rejects = {"agent_was_speaking": 0, "total": 0}
    acoustic_tables = {}

    for t in turns:
        valid = t.get("stt_valid")
        reason = t.get("stt_rejection_reason")
        nsp = t.get("stt_no_speech_prob")
        if valid is True or reason in (None, "accepted"):
            counts["accepted"] += 1
        elif reason in counts:
            counts[reason] += 1
            if reason == "high_no_speech_prob":
                nsp_rejects.append(t)
                ac_rejects["total"] += 1
                if t.get("agent_was_speaking"):
                    ac_rejects["agent_was_speaking"] += 1
        if nsp is not None and BAND_MIN <= nsp < BAND_MAX:
            band_turns.append(t)
        if t.get("route_action") == "contextual_recovery" or t.get("recovery_mode"):
            recovery.append(t)

    print("=" * 62)
    print("STT REJECTION REPORT")
    print(f"turns: {n}  | sessions: {len(paths)}")
    print("=" * 62)
    print("\n## 1. Acceptance / rejection by reason")
    for r in ["accepted"] + REASONS:
        c = counts.get(r, 0)
        pct = 100.0 * c / n if n else 0.0
        flag = ""
        if r in ("high_no_speech_prob", "suspicious_no_speech_band") and c:
            flag = "  <-- investigate"
        print(f"  {r:28s} {c:4d}  ({pct:5.1f}%){flag}")

    print("\n## 2. high_no_speech_prob rejects — was the user/echo context involved?")
    if nsp_rejects:
        ag = ac_rejects["agent_was_speaking"]
        print(f"  total rejects: {ac_rejects['total']} | while Aiva was speaking: {ag} "
              f"({100.0*ag/ac_rejects['total']:.0f}%)")
        print("  per-reject detail (last 8):")
        for t in nsp_rejects[-8:]:
            ac = t.get("acoustic") or {}
            print(f"    turn={t.get('turn'):>3} nsp={t.get('stt_no_speech_prob')} "
                  f"rms={ac.get('rms')} peak={ac.get('peak')} dur={ac.get('duration_ms')}ms "
                  f"agent_speaking={t.get('agent_was_speaking')} "
                  f"echo={t.get('echo_shadow', {}).get('decision')} "
                  f"text={t.get('stt_transcript', '')[:40]!r}")
    else:
        print("  none in the analyzed sessions.")

    print("\n## 3. Suspicious band (nsp in [0.5, 0.6)) — post-fix these become")
    print("     suspicious_no_speech_band rejects (bounded, never substantive)")
    if band_turns:
        for t in band_turns[-8:]:
            ac = t.get("acoustic") or {}
            print(f"    turn={t.get('turn'):>3} nsp={t.get('stt_no_speech_prob'):.3f} "
                  f"rms={ac.get('rms')} words={len((t.get('stt_transcript') or '').split())} "
                  f"agent_speaking={t.get('agent_was_speaking')} "
                  f"text={t.get('stt_transcript', '')[:50]!r}")
    else:
        print("  none in the analyzed sessions (band may be rare or nsp is None via Gemini Live).")

    print("\n## 4. Recovery quality (contextual_recovery turns)")
    if recovery:
        ok = 0
        for t in recovery:
            text = (t.get("llm_response") or t.get("tts_text") or "").strip()
            words = len(text.split())
            short = words <= 25
            route = t.get("route_action")
            if route == "contextual_recovery" and short:
                ok += 1
        print(f"  recovery turns: {len(recovery)} | short+checkpoint-style: {ok} "
              f"({100.0*ok/len(recovery):.0f}%)")
        for t in recovery[-6:]:
            print(f"    turn={t.get('turn'):>3} reason={t.get('route_reason', '')[:40]} "
                  f"reply={(t.get('llm_response') or '')[:60]!r}")
    else:
        print("  none in the analyzed sessions.")

    print("\n## 5. Recovery turns that still got a SUBSTANTIVE reply (violation watch)")
    sub = [t for t in recovery
           if len((t.get("llm_response") or "").split()) > 25
           and t.get("route_action") == "contextual_recovery"]
    if sub:
        for t in sub:
            print(f"    turn={t.get('turn'):>3} words={len((t.get('llm_response') or '').split())} "
                  f"text={(t.get('llm_response') or '')[:80]!r}")
    else:
        print("  none — recovery replies stayed bounded.")

    print("\nNOTE: nsp/logprob are per-utterance now (segment-aggregated); compare")
    print("rejection rates across pre/post fix sessions for the A/B.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

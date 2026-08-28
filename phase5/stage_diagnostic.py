#!/usr/bin/env python3
"""Per-turn stage-by-stage diagnostic — run from repo root on the Mac.

python3 phase5/stage_diagnostic.py
"""
import json, glob, os, sys

ROOT = os.getcwd()
sfiles = sorted(glob.glob("logs/session_*.log"), key=os.path.getmtime)
if not sfiles:
    print("NO SESSION LOG FOUND"); sys.exit(1)
p = sfiles[-1]
print(f"=== STAGE DIAGNOSTIC: {os.path.basename(p)} ===\n")

for line in open(p):
    try:
        t = json.loads(line)
    except:
        continue
    if not t.get("turn"):
        continue
    
    turn = t["turn"]
    stt = t.get("stt_transcript", "")
    reply = t.get("llm_response") or ""
    tts = t.get("tts") or {}
    ctx_raw = t.get("llm_context")
    
    # Parse the context to see what the LLM received
    ctx_summary = "NOT CAPTURED"
    if ctx_raw:
        try:
            c = json.loads(ctx_raw)
            mem = c.get("memory", [])
            hist = c.get("history", [])
            pol = c.get("policy", {})
            threads = c.get("threads", [])
            ctx_summary = f"memory={len(mem)} items, history={len(hist)} turns, mode={pol.get('mode','?')}, threads={threads}"
        except:
            ctx_summary = "parse error"
    
    # Identify which stage likely failed
    issues = []
    if t.get("stt_valid") is False:
        issues.append(f"STT: rejected ({t.get('stt_rejection_reason')})")
    if not reply and t.get("stt_valid"):
        issues.append("LLM: no reply generated")
    if reply and not tts.get("provider"):
        issues.append("TTS: no provider — audio not synthesized")
    if tts.get("stall_s") and tts["stall_s"] > 1:
        issues.append(f"TTS: stall {tts['stall_s']}s (playback >> audio)")
    if tts.get("interrupted_at_ms"):
        issues.append(f"INTERRUPTED at {tts['interrupted_at_ms']}ms")
    
    # Check the quality of the reply against the STT
    quality_flag = ""
    if stt and reply:
        # Is the reply contextually relevant?
        stt_lower = stt.lower()
        reply_lower = reply.lower()
        if any(w in reply_lower for w in ["cut gaya", "miss ho gaya", "phir se bol", "sun nahi paya"]):
            quality_flag = "⚠️ GENERIC CLARIFY"
        elif any(w in reply_lower for w in ["samajh", "understand"]):
            quality_flag = "⚠️ THERAPY-SPEAK"
    
    print(f"TURN {turn}")
    print(f"  STT     : {stt[:60]!r} | lang={t.get('stt_language')} logprob={t.get('stt_avg_logprob')}")
    print(f"  valid   : {t.get('stt_valid')} ({t.get('stt_rejection_reason','')}) | turn_relation: {t.get('turn_relation')}")
    print(f"  decision: {t.get('turn_end_decision')} ({t.get('suppression_reason','')})")
    print(f"  reply   : {reply[:70]!r}")
    print(f"  TTS     : {tts.get('provider')} audio={tts.get('audio_duration_s')}s playback={tts.get('playback_duration_s')}s")
    print(f"  latency : stt={t.get('stt_latency_s')}s llm_ttft={t.get('llm_ttft_s')}s tts_ttfa={t.get('tts_first_audio_s')}s speech->audio={t.get('speech_end_to_first_audio_s')}s")
    print(f"  context : {ctx_summary}")
    if issues:
        print(f"  ⚠️ ISSUES: {'; '.join(issues)}")
    if quality_flag:
        print(f"  {quality_flag}")
    print()

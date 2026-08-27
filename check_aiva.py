#!/usr/bin/env python3
"""Quick Aiva health check — run from repo root: python3 check_aiva.py"""
import glob, json, os, sys

ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)

print("=" * 50)
print("AIVA DIAGNOSTIC")
print("=" * 50)

# 1. Latest session log
files = sorted(glob.glob("logs/session_*.log"), key=os.path.getmtime)
if not files:
    print("\n❌ NO SESSION LOG — the worker never processed any speech.")
    print("   Check: Is Terminal 2 running? Did you see [StateEngine] on?")
else:
    p = files[-1]
    print(f"\n📄 Session log: {os.path.basename(p)} ({os.path.getmtime(p):.0f})")
    turns = 0
    for line in open(p):
        try:
            t = json.loads(line)
        except:
            continue
        if not t.get("turn"):
            continue
        turns += 1
        tts = t.get("tts") or {}
        reply = (t.get("llm_response") or "")[:60]
        print(f"\n  TURN {t['turn']}")
        print(f"    STT: {t.get('stt_valid')} ({t.get('stt_rejection_reason', '')})")
        print(f"    Text: {repr(t.get('stt_transcript', ''))[:60]}")
        print(f"    Reply: {repr(reply)}")
        print(f"    TTS: {tts.get('provider')} | {tts.get('audio_duration_s')}s audio | {tts.get('playback_duration_s')}s playback")
        if t.get("dropped_reason"):
            print(f"    ⚠️ DROPPED: {t['dropped_reason']}")
    if turns == 0:
        print("\n  ❌ ZERO TURNS — the worker never processed any speech.")
        print("     Most likely: mic permission denied in browser,")
        print("     or the agent didn't join the room.")
    else:
        print(f"\n  Total turns: {turns}")

# 2. Latest telemetry
tl_files = sorted(glob.glob("logs/turn_lifecycle_*.jsonl"), key=os.path.getmtime)
if tl_files:
    p = tl_files[-1]
    print(f"\n📊 Telemetry: {os.path.basename(p)}")
    events = [json.loads(l) for l in open(p) if l.strip()]
    llm_times = [e["t"] for e in events if e["ev"] == "LLM_FIRST_TOKEN"]
    if llm_times:
        print(f"    LLM calls: {len(llm_times)}")
    else:
        print("    ❌ No LLM calls recorded")

# 3. Check memory
db = "logs/aiva_memory.db"
if os.path.exists(db):
    import sqlite3
    conn = sqlite3.connect(db)
    n = conn.execute("SELECT COUNT(*) FROM memory").fetchone()[0]
    print(f"\n🧠 Memory: {n} entries")

print("\n" + "=" * 50)
print("Paste this entire output for diagnosis.")

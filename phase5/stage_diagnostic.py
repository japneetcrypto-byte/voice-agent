#!/usr/bin/env python3
"""Per-turn stage-by-stage diagnostic — run from repo root on the Mac.

python3 phase5/stage_diagnostic.py            # latest session log
python3 phase5/stage_diagnostic.py logs/session_20260828_224509.log

Reads logs/session_*.log (one JSON per turn) and prints every stage:
STT -> validity -> turn decision -> LLM (context, head, latency) -> reply
(length, trims, persona flags) -> TTS. Also prints session-level aggregates.
"""
import json, glob, os, sys, re
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agent.reply_guard import feminine_self_reference, is_confirm_echo, devanagari_present

if len(sys.argv) > 1:
    p = sys.argv[1]
else:
    sfiles = sorted(glob.glob("logs/session_*.log"), key=os.path.getmtime)
    if not sfiles:
        print("NO SESSION LOG FOUND"); sys.exit(1)
    p = sfiles[-1]
print(f"=== STAGE DIAGNOSTIC: {os.path.basename(p)} ===\n")

turns = []
for line in open(p):
    try:
        t = json.loads(line)
    except Exception:
        continue
    if t.get("turn"):
        turns.append(t)
# Tasks complete concurrently (barge-in), so lines can land out of order.
turns.sort(key=lambda t: (t.get("turn", 0)))

SERVICE_PHRASES = ["help chahiye", "how can i help", "help kar", "madad kar", "madad ke liye",
                   "sawaalon ke jawaab", "jawab dene"]
# The legacy fallback prompt's canned ignorance line (session.py rule 4).
# If this appears, the state engine was NOT the brain for that turn.
LEGACY_CANNED = ["kuch aur poochh sakte", "pata nahi, kuch aur"]

agg = {"turns": 0, "replies": 0, "s2a": [], "trimmed": 0, "gender": 0,
       "service": 0, "errors": 0, "ctx_ok": 0, "head_ok": 0, "legacy": 0,
       "paths": {}, "stt_providers": {}}

for t in turns:
    turn = t["turn"]
    stt = t.get("stt_transcript", "") or ""
    reply = t.get("llm_response") or ""
    tts = t.get("tts") or {}
    ctx_raw = t.get("llm_context")
    head = t.get("perception_head")

    ctx_summary = "deterministic (no LLM call)" if t.get("llm_called") is False else "NOT CAPTURED"
    if ctx_raw:
        try:
            c = json.loads(ctx_raw)
            mem = c.get("memory", [])
            hist = c.get("history", [])
            pol = c.get("policy", {})
            th = c.get("threads", [])
            ctx_summary = (f"mem={len(mem)} hist={len(hist)} mode={pol.get('mode','?')} "
                           f"goal={pol.get('response_goal', pol.get('goal','?'))} threads={len(th)}")
            agg["ctx_ok"] += 1
        except Exception:
            ctx_summary = "parse error"
    head_s = "-"
    if head:
        head_s = f"m={head.get('m')} c={head.get('c')} s={head.get('s')}"
        agg["head_ok"] += 1
    elif t.get("head_fail_class"):
        head_s = f"FAIL({t.get('head_fail_class')})"

    issues = []
    if t.get("stt_valid") is False:
        issues.append(f"STT rejected ({t.get('stt_rejection_reason')})")
    if t.get("pipeline_error"):
        issues.append(f"ERROR: {t['pipeline_error'][:90]}")
        agg["errors"] += 1
    if t.get("response_skipped"):
        issues.append(f"RESPONSE SKIPPED ({t['response_skipped']}) — user left hanging")
        agg["skipped"] = agg.get("skipped", 0) + 1
    if not reply and t.get("stt_valid") and not t.get("response_suppressed") \
            and t.get("turn_type") != "idle":
        issues.append("no reply generated")
    if reply and not tts.get("provider"):
        issues.append("TTS: no audio synthesized")
    if tts.get("interrupted_at_ms"):
        issues.append(f"INTERRUPTED at {tts['interrupted_at_ms']}ms")
    if reply and not tts.get("provider"):
        issues.append("TTS: no provider — audio not synthesized")
    if reply and is_confirm_echo(reply):
        flags.append("↩ CONFIRM-ECHO")
        agg["confirm_echo"] = agg.get("confirm_echo", 0) + 1
    if reply and devanagari_present(reply):
        flags.append("देवनागरी SCRIPT")
        agg["devi"] = agg.get("devi", 0) + 1
    if reply and t.get("cancel_pre_audio"):
        issues.append("REPLY CANCELLED BEFORE AUDIO — user spoke again before first "
                      "sound (TTS TTFA slower than user's pace); reply text was never heard")
        agg["cancel_pre"] = agg.get("cancel_pre", 0) + 1
    if reply and tts.get("provider") and tts.get("audio_duration_s") in (None, 0) \
            and not t.get("interrupted"):
        fb = t.get("tts_fallback_reason") or tts.get("fallback_reason")
        if fb:
            issues.append(f"TTS SILENT ({tts.get('provider')}) — fallback: {fb[:70]}")
        else:
            issues.append(f"TTS SILENT ({tts.get('provider')}) — zero audio, no fallback "
                          "recorded (is the worker on the latest code?)")
    if t.get("llm_error"):
        issues.append(f"LLM error: {str(t['llm_error'])[:80]}")

    flags = []
    if reply:
        agg["replies"] += 1
        if feminine_self_reference(reply):
            flags.append("♀ GENDER")
            agg["gender"] += 1
        rl = reply.lower()
        if any(w in rl for w in SERVICE_PHRASES):
            flags.append("⚙ SERVICE-SPEAK")
            agg["service"] += 1
        if any(w in rl for w in LEGACY_CANNED):
            flags.append("☠ LEGACY-BRAIN")
            agg["legacy"] += 1
        if len(reply) > 150:
            flags.append(f"LONG({len(reply)}c)")
    if t.get("reply_trimmed"):
        flags.append(f"TRIMMED({t.get('reply_chars')}c/{len(t.get('llm_response_full') or '')}c)")
        agg["trimmed"] += 1
    epath = t.get("engine_path") or "?"
    agg["paths"][epath] = agg["paths"].get(epath, 0) + 1
    if t.get("owner"):
        agg.setdefault("owners", set()).add(t["owner"])
    if t.get("tag_leak_stripped"):
        agg["tag_leaks"] = agg.get("tag_leaks", 0) + 1
    sp = t.get("stt_provider")
    if sp:
        agg["stt_providers"][sp] = agg["stt_providers"].get(sp, 0) + 1

    print(f"TURN {turn}" + ("  [idle]" if t.get("turn_type") == "idle" else ""))
    corr = t.get("echo_corr_score")
    corr_s = f" | corr={corr}" if corr is not None else ""
    print(f"  STT     : {stt[:60]!r} | lang={t.get('stt_language')} logprob={t.get('stt_avg_logprob')} | prov={t.get('stt_provider') or '?'}{corr_s}")
    print(f"  valid   : {t.get('stt_valid')} ({t.get('stt_rejection_reason','')}) | relation: {t.get('turn_relation')}" +
          (f" | user_rels: {t.get('user_relations')}" if t.get("user_relations") else ""))
    print(f"  engine  : {epath} | decision: {t.get('turn_end_decision')} ({t.get('suppression_reason','')}) | because: {t.get('spoke_because') or t.get('response_trigger_reason')}")
    print(f"  head    : {head_s} | degrade: {t.get('degradation') or '-'}")
    print(f"  reply   : {reply[:70]!r}" + (f" | {t.get('reply_words')}w/{t.get('reply_chars')}c" if reply else ""))
    print(f"  TTS     : {tts.get('provider')} audio={tts.get('audio_duration_s')}s playback={tts.get('playback_duration_s')}s")
    print(f"  latency : stt={t.get('stt_latency_s')}s llm_ttft={t.get('llm_ttft_s')}s tts_ttfa={t.get('tts_first_audio_s')}s speech->audio={t.get('speech_end_to_first_audio_s')}s")
    print(f"  context : {ctx_summary}")
    if ctx_summary.startswith("mem=0"):
        agg["mem_zero"] = agg.get("mem_zero", 0) + 1
    if issues:
        print(f"  ⚠️ ISSUES: {'; '.join(issues)}")
    if flags:
        print(f"  🚩 FLAGS: {', '.join(flags)}")
    print()
    agg["turns"] += 1

durs = [ (t.get("tts") or {}).get("audio_duration_s") for t in turns ]
durs = [d for d in durs if d]
lat = [t.get("speech_end_to_first_audio_s") for t in turns if t.get("speech_end_to_first_audio_s")]
print("--- SESSION SUMMARY ---")
print(f"turns={agg['turns']} replies={agg['replies']} ctx_captured={agg['ctx_ok']}/{agg['replies']} "
      f"heads={agg['head_ok']} errors={agg['errors']}")
print(f"reply audio: avg={round(sum(durs)/len(durs),2) if durs else '-'}s max={max(durs) if durs else '-'}s | "
      f"speech->audio avg={round(sum(lat)/len(lat),2) if lat else '-'}s max={max(lat) if lat else '-'}s")
print(f"flags: trimmed={agg['trimmed']} gender={agg['gender']} service-speak={agg['service']} legacy-brain={agg['legacy']}")
if agg.get("confirm_echo"):
    print(f"⚠ echo-confirm parrots: {agg['confirm_echo']}/{agg['replies']} replies "
          f"(quality: substance ratio low if >25%)")
if agg.get("devi"):
    print(f"⚠ Devanagari-script replies: {agg['devi']} (persona says Roman)")
print(f"engine paths: {agg['paths']}")
if agg.get("owners"):
    print(f"owner(s): {sorted(agg['owners'])}")
if agg.get("tag_leaks"):
    print(f"tag-leaks stripped: {agg['tag_leaks']}")
if agg.get("skipped"):
    print(f"⚠ response skips: {agg['skipped']} — see RESPONSE SKIPPED turns")
if agg.get("cancel_pre"):
    print(f"⚠ replies cancelled BEFORE audio: {agg['cancel_pre']} — user outpaced TTS "
          "first-audio; root fix = lower TTFA (voice provider decision)")
corrs = [t.get("echo_corr_score") for t in turns if t.get("echo_corr_score") is not None]
if corrs:
    corrs.sort()
    print(f"echo corr (voice key): n={len(corrs)} min={corrs[0]} med={corrs[len(corrs)//2]} max={corrs[-1]} "
          f"→ python3 phase5/echo_shadow_report.py")
if agg["turns"] and agg["ctx_ok"] == 0 and agg["replies"] == 0:
    pass
owners = sorted(agg.get("owners", set()))
if agg["turns"] and not owners:
    print("note: no owner recorded (pre-upgrade log)")

if agg["stt_providers"]:
    print(f"stt providers: {agg['stt_providers']}")
if agg.get("mem_zero"):
    print(f"⚠ mem=0 on {agg['mem_zero']} turns — memory view EMPTY. If relationships "
          "should be known, the session likely bound to a DIFFERENT device owner "
          "(check console 'SESSION BOUND owner=' vs: sqlite3 logs/aiva_memory.db "
          '\"SELECT DISTINCT owner_id FROM memory;\")')

#!/usr/bin/env python3
"""Self-diagnosis engine — the system catches WHY it failed.

Owner brief 2026-08-29: "can there be a self-healing system that catches why
it did not work/failed?"

This is the diagnosis half of self-healing. Containment (supervisor, TTS
failover, fallbacks) already acts live; THIS tool replays every session log
and produces a per-failure-class post-mortem:

  WHAT failed -> HOW OFTEN -> WHY (root cause) -> WHAT WAS DONE -> PRESCRIPTION

Deterministic pattern rules over our own telemetry — no LLM, same discipline
as everything else. Run after every session:

  python3 phase5/self_diagnose.py                 # latest session
  python3 phase5/self_diagnose.py logs/session_X.log

Report saved to logs/diagnosis_<session>.md and printed.
"""
import json, glob, os, sys, re
from collections import Counter

if len(sys.argv) > 1:
    sp = sys.argv[1]
else:
    cands = sorted(glob.glob("logs/session_*.log"), key=os.path.getmtime)
    if not cands:
        print("NO SESSION LOG FOUND"); sys.exit(1)
    sp = cands[-1]
sess = os.path.basename(sp).replace("session_", "").replace(".log", "")
ep = f"logs/events_{sess}.log"

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
if os.path.exists(ep):
    for line in open(ep):
        try:
            events.append(json.loads(line))
        except Exception:
            pass

sup_engaged = [e for e in events if e.get("event") == "SUPERVISOR_ENGAGED"]
sup_esc = [e for e in events if e.get("event") == "SUPERVISOR_ESCALATE"]
quota_429 = [e for e in events if "429" in json.dumps(e)]

findings = []   # (class, count, why, done, prescription)
details = {}

def add(cls, n, why, done, rx):
    findings.append((cls, n, why, done, rx))

# ---------- per-turn classification ----------
tts_silent, echo_drops, echo_saved, cancelled, merges = [], [], [], [], []
clarify_streak, tag_leaks, trims, slow = [], 0, 0, []
reply_texts = []

MERGE_PATTERNS = [
    (re.compile(r"\bth ik\b"), "th ik->theek (legacy split)"),
    (re.compile(r"\bnah in\b"), "nah in->nahin (legacy split)"),
    (re.compile(r"\bk ela\b"), "k ela->kela (legacy split)"),
    (re.compile(r"\bsebaithne\b"), "sebaithne (model merge)"),
    (re.compile(r"\bsahikaam\b"), "sahikaam (model merge)"),
    (re.compile(r"\bbaaremein\b"), "baaremein (model merge)"),
    (re.compile(r"[{}<>]{2,}"), "brace/tag junk reached TTS"),
]

for t in turns:
    reply = t.get("llm_response") or ""
    tts = t.get("tts") or {}
    dur = tts.get("audio_duration_s")
    interrupted = bool(t.get("interrupted"))
    if reply:
        reply_texts.append((t.get("turn"), reply))
    # 1. reply generated, no audio, not interrupted -> TTS produced nothing
    if reply and not dur and not interrupted:
        tts_silent.append(t)
    # 2. echo filter ate the user's turn
    if t.get("echo_dropped"):
        echo_drops.append(t)
    if t.get("echo_overridden"):
        echo_saved.append(t)
    # 3. turn record incomplete = task cancelled mid-flight (barge-in race etc.)
    if t.get("stt_valid") is None and not t.get("echo_dropped") \
            and not t.get("pipeline_error") and not t.get("response_skipped"):
        cancelled.append(t)
    # 4. merged/garbage text actually spoken
    for pat, label in MERGE_PATTERNS:
        if pat.search(reply):
            merges.append((t.get("turn"), label))
    # 5. instrumentation counters
    if t.get("tag_leak_stripped"):
        tag_leaks += 1
    if t.get("reply_trimmed"):
        trims += 1
    s2a = t.get("speech_end_to_first_audio_s")
    if s2a:
        slow.append((s2a, t.get("turn"), t.get("llm_ttft_s"), t.get("tts_first_audio_s")))
    # 6. quota fingerprints
    if t.get("llm_error") and "429" in str(t.get("llm_error")):
        quota_429.append({"turn": t.get("turn"), "src": "llm_error"})

# clarify-line repetition (same reply 3+ times in any 5-turn window)
for i in range(len(reply_texts) - 2):
    window = reply_texts[i:i + 5]
    texts = Counter(r for _, r in window)
    for txt, n in texts.items():
        if n >= 3 and len(txt) < 40:
            clarify_streak.append((window[0][0], txt[:36], n))

# ---------- WHY / DONE / PRESCRIPTION per class ----------
if tts_silent:
    add("TTS silent (reply generated, zero audio)", len(tts_silent),
        "Fish Audio returned an empty/near-empty stream while reporting success "
        f"(turns {[t.get('turn') for t in tts_silent][:8]}). Provider-side hiccup, not our pipeline.",
        "AUTO-HEALED as of this build: zero-audio detection now fails over to EdgeTTS "
        "immediately + supervisor covers the gap.",
        "If Edge-fallback rate >5% of turns across sessions, switch TTS provider (owner decision).")
if echo_drops:
    add("User turns eaten by echo filter", len(echo_drops),
        "Transcript matched Aiva's own last reply (similarity>0.65). Correct for true "
        "echo; wrong when the USER repeats Aiva's words.",
        f"PARTIAL-AUTO: late repeats (>1.5s after speech end) now kept "
        f"({len(echo_saved)} saved this session); quick overlaps still dropped by design.",
        "If drops still exceed ~5% of turns, the mic echo-cancellation (AEC) needs tuning — hardware/frontend level.")
if cancelled:
    add("Turn records cancelled mid-flight", len(cancelled),
        "The turn's task was superseded by a newer turn (fast barge-in) before it "
        f"completed (turns {[t.get('turn') for t in cancelled][:8]}). Mostly benign — "
        "the newer turn answers instead.",
        "AUTO: the newest turn always wins; supervisor catches any orphaned silence.",
        "None needed unless users report being ignored after quick interruptions.")
if merges:
    kinds = Counter(label for _, label in merges)
    add("Merged/garbled tokens in spoken text", len(merges),
        "flash-lite emits merged Hinglish tokens ('sahikaam','baaremein') and legacy "
        "damage from the reverted join heuristic ('th ik').",
        "AUTO-REPAIRED by the deterministic lexicon before TTS; new forms are added "
        "to MERGE_SPLIT_LEXICON as observed (exact-match only — cannot damage valid words).",
        "Keep feeding sessions; if a NEW merge form appears, add one lexicon line. "
        "If frequency grows, consider a stronger model tier (owner decision).")
if clarify_streak:
    add("Clarify line repeated 3+ times in a row", len(clarify_streak),
        "STT produced consecutive garbles (mic distance/noise) and the model echoed "
        "the same recovery phrase.",
        "ACKNOWLEDGED: bounded by user rephrasing; each clarify is <1.5s.",
        "Mic/AEC improvement is the real fix; optionally add wording-variation to the "
        "persona (low priority).")
if tag_leaks:
    add("Tag leaks stripped before TTS", tag_leaks,
        "Model mis-closes its perception tag (5+ variants observed across sessions).",
        "AUTO-HEALED: class-level stripper + belt-and-braces sanitizer.",
        "None — keep the counter; a spike means prompt drift.")
if trims:
    add(f"Replies trimmed by length guard", trims,
        "Model exceeded the length cap; guard cut at a sentence boundary.",
        "AUTO (by design).", "If trims >20% of replies, revisit persona examples.")
if sup_engaged:
    add("Supervisor engagements", len(sup_engaged),
        "User turn(s) ended with no agent audio; the supervisor spoke the recovery "
        f"line (turns {[e.get('turn_id') for e in sup_engaged][:8]}).",
        "AUTO (by design): rescue line + incident snapshot.",
        "Escalations fire on repeat incidents; attach AIVA_ALERT_WEBHOOK for paging.")
if quota_429:
    add("Quota/429 fingerprints", len(quota_429),
        "Gemini free-tier exhaustion on one or more keys.",
        "AUTO: key+model rotation; supervisor covers gaps.",
        "For pilots: paid tier or more keys (owner decision).")

slow.sort(reverse=True)
lat_avg = round(sum(s for s, *_ in slow) / len(slow), 2) if slow else None
lat_p95 = round(sorted(s for s, *_ in slow)[int(len(slow) * 0.95) - 1], 2) if len(slow) >= 4 else None

# ---------- report ----------
L = []
L.append(f"# Self-Diagnosis — session {sess}")
L.append(f"\nTurns analyzed: {len(turns)} · replies: {len(reply_texts)} · "
         f"speech->audio avg {lat_avg}s" + (f" p95 {lat_p95}s" if lat_p95 else ""))
L.append(f"\n## Failures detected: {sum(n for _, n, *_ in findings) if findings else 0} across {len(findings)} classes\n")
for cls, n, why, done, rx in findings:
    L.append(f"### {cls} — ×{n}")
    L.append(f"- **Why:** {why}")
    L.append(f"- **Handled:** {done}")
    L.append(f"- **Next:** {rx}\n")
if not findings:
    L.append("No failure classes detected this session. 🎉\n")
if slow:
    worst = slow[0]
    L.append(f"Slowest turn: {worst[1]} ({worst[0]}s; llm_ttft={worst[2]}s, tts_ttfa={worst[3]}s) "
             "— attributes the slowness to its stage.")
L.append("---")
L.append("_Deterministic post-mortem (no LLM). Containment already ran live; "
         "prescriptions marked 'owner decision' need a human ruling._")

report = "\n".join(L)
out = f"logs/diagnosis_{sess}.md"
with open(out, "w") as f:
    f.write(report + "\n")
print(report)
print(f"\n[saved: {out}]")

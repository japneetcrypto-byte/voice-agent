#!/usr/bin/env python3
"""Owner-requested endpoint diagnostic (Phase 2 follow-up).

Produces, from the newest logs/turn_lifecycle_*.jsonl (+ newest session log
for transcripts):
  C. endpoint-level table: turn | speech_dur | trailing_silence | threshold |
     next_speech_start | resume_gap | STT transcript | validation | decision
  D. short-segment fragmentation (buckets + per-segment outcome) + the
     suspicious <=240ms cases
  E. threshold usage counts (300/700/>700) + resume-within 1s/2s/3s counts
  F. proxy-vs-conversational separation:
       acoustic continuation  = resume_gap <= 500ms
       conversational prematurity candidates = transcript ends with a
       trail-off connector or ellipsis (deterministic marker check)
  A/B. duplicate TURN_DECISION groups + repeated endpoints per recorded turn id

Run:  python3 phase5/diagnose_turns.py
"""
import glob
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

TRAIL_OFF = {"कि", "की", "तो", "और", "फिर", "मतलब", "क्योंकि", "क्यूंकि",
             "लेकिन", "मगर", "ki", "toh", "aur", "phir", "matlab", "kyunki",
             "lekin", "magar", "actually"}


def newest(pattern):
    files = sorted(glob.glob(os.path.join(ROOT, "logs", pattern)),
                   key=os.path.getmtime)
    return files[-1] if files else None


def main() -> int:
    tl_path = newest("turn_lifecycle_*.jsonl")
    if not tl_path:
        print("no turn_lifecycle file found")
        return 1
    ev = []
    for line in open(tl_path):
        try:
            ev.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    ev.sort(key=lambda e: e.get("t", 0))

    # session log transcripts (turn -> stt text/valid)
    transcripts = {}
    sp = newest("session_*.log")
    if sp:
        for line in open(sp):
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            t = d.get("turn")
            if t and d.get("stt_transcript") is not None:
                transcripts.setdefault(t, {"text": d["stt_transcript"],
                                           "valid": d.get("stt_valid"),
                                           "reply": d.get("llm_response")})

    print(f"lifecycle file: {os.path.basename(tl_path)}  events={len(ev)}")

    # ---------- A. duplicate TURN_DECISION ----------
    decs = [e for e in ev if e["ev"] == "TURN_DECISION"]
    seen, dups = {}, []
    for e in decs:
        key = (e.get("turn"), round(e["t"], 1))
        if key in seen:
            dups.append(e)
        seen[key] = e
    print(f"\n=== A. TURN_DECISION duplicates ===")
    print(f"total decision events: {len(decs)} | duplicate second-marks: {len(dups)}")
    print("root cause (code): respond path emits the marker twice "
          "(controller + pre-response). Telemetry-only duplication.")
    if dups:
        print("duplicated events:", [(d.get("turn"), d.get("decision"), d.get("t")) for d in dups])

    # ---------- B. repeated endpoints per turn id ----------
    by_turn = {}
    for e in ev:
        if e["ev"] == "VAD_SPEECH_ENDED":
            by_turn.setdefault(e.get("turn"), []).append(e)
    multi = {k: v for k, v in by_turn.items() if len(v) > 1}
    print(f"\n=== B. recorded turn-ids with >1 endpoint ===")
    for k, v in sorted(multi.items()):
        print(f"  turn {k}: {len(v)} endpoints at t={[x['t'] for x in v]} "
              f"(id assigned as turn_number+1 BEFORE task spawn -> shared id)")
    if not multi:
        print("  none")

    # ---------- C/D/E/F. ordered endpoint analysis ----------
    print("\n=== C. endpoint diagnostic table ===")
    hdr = (f"{'#':>2} {'turn':>4} {'t':>7} {'speech_ms':>9} {'sil_ms':>6} "
           f"{'thr_ms':>6} {'resume_ms':>9} {'STT':>6} {'valid':>6} {'decision':>9} "
           f"  transcript (trunc)")
    print(hdr)
    rows = []
    for i, e in enumerate([x for x in ev if x["ev"] == "VAD_SPEECH_ENDED"]):
        t_e = e["t"]
        nxt = next((s for s in ev if s["ev"] == "VAD_SPEECH_STARTED"
                    and s["t"] > t_e), None)
        resume = round((nxt["t"] - t_e) * 1000) if nxt else None
        turn_id = e.get("turn")
        # matching task results: first STT/VALIDATION/DECISION after this endpoint
        def after(ev_name):
            return next((x for x in ev if x["ev"] == ev_name and x["t"] > t_e), None)
        v = after("VALIDATION_COMPLETED")
        d = after("TURN_DECISION")
        tr = transcripts.get(v.get("turn")) if v else None
        text = (tr or {}).get("text") or ""
        valid = (v or {}).get("valid")
        decision = (d or {}).get("decision") or (tr or {}).get("valid") and "suppress?" or "?"
        dec_s = (d or {}).get("decision", "?")
        vals = (v or {}).get("valid", "?")
        print(f"{i:>2} {str(turn_id):>4} {t_e:>7} {str(e.get('speech_duration_ms')):>9} "
              f"{str(e.get('trailing_silence_ms')):>6} {str(e.get('threshold_ms')):>6} "
              f"{str(resume) if resume is not None else 'NONE':>9} "
              f"{('Y' if text else 'N'):>6} {str(vals):>6} {dec_s:>9}  {text[:48]!r}")
        rows.append({"i": i, "turn": turn_id, "e": e, "resume": resume,
                     "text": text, "valid": vals, "decision": dec_s})

    print("\n=== D. speech-duration fragmentation ===")
    durs = [r["e"].get("speech_duration_ms") or 0 for r in rows]
    b = {"<500": 0, "500-1000": 0, "1000-1500": 0, "1500-3000": 0, ">3000": 0}
    for d in durs:
        k = "<500" if d < 500 else "500-1000" if d < 1000 else \
            "1000-1500" if d < 1500 else "1500-3000" if d < 3000 else ">3000"
        b[k] += 1
    print("buckets:", b)
    srt = sorted(durs)
    if srt:
        med = srt[len(srt) // 2]
        print(f"avg={round(sum(durs)/len(durs),1)}ms median={med}ms n={len(durs)}")
    print("short-segment outcomes (segments <1000ms):")
    for r in rows:
        d = r["e"].get("speech_duration_ms") or 0
        if d < 1000:
            print(f"  endpoint#{r['i']} turn={r['turn']} {d}ms -> valid={r['valid']} "
                  f"decision={r['decision']} text={r['text'][:40]!r}")
    print("suspicious <=240ms response candidates:")
    found = False
    for r in rows:
        d = r["e"].get("speech_duration_ms") or 0
        if 0 < d <= 240:
            found = True
            print(f"  endpoint#{r['i']} turn={r['turn']} {d}ms decision={r['decision']} "
                  f"text={r['text'][:40]!r}")
    if not found:
        print("  none in this run")

    print("\n=== E. threshold usage + resume windows ===")
    thr300 = sum(1 for r in rows if (r["e"].get("threshold_ms") or 0) == 300)
    thr700 = sum(1 for r in rows if (r["e"].get("threshold_ms") or 0) == 700)
    thr_hi = sum(1 for r in rows if (r["e"].get("threshold_ms") or 0) > 700)
    print(f"threshold=300: {thr300} | =700: {thr700} | >700: {thr_hi}")
    w = {"<=1s": 0, "<=2s": 0, "<=3s": 0, ">3s/none": 0}
    for r in rows:
        g = r["resume"]
        if g is None or g > 3000:
            w[">3s/none"] += 1
        elif g <= 1000:
            w["<=1s"] += 1
        elif g <= 2000:
            w["<=2s"] += 1
        else:
            w["<=3s"] += 1
    print("resume windows:", w)

    print("\n=== F. proxy separation ===")
    for r in rows:
        g = r["resume"]
        text = (r["text"] or "").strip()
        words = text.split()
        last = words[-1].lower().strip(".?,!") if words else ""
        if g is not None and g <= 500:
            print(f"  ACOUSTIC CONTINUATION: endpoint#{r['i']} resumed after {g}ms "
                  f"text={text[:40]!r}")
        if last in TRAIL_OFF or text.endswith(("...", "…")):
            print(f"  CONVERSATIONAL PREMATURITY CANDIDATE: endpoint#{r['i']} "
                  f"ends-with-connector/ellipsis text={text[:44]!r}")
    print("\n(ACOUSTIC = resume<=500ms; PREMATURITY candidates = deterministic "
          "connector/ellipsis end. Manual judgment still applies.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

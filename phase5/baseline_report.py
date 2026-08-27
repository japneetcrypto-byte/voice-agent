#!/usr/bin/env python3
"""Phase 2 baseline report — reads logs/turn_lifecycle_*.jsonl and prints the
pre-registered baseline table (owner stabilization plan, Phase 2).

Run after a live conversation:  python3 phase5/baseline_report.py [--all]
"""
import argparse, glob, json, os

def load(paths):
    events = []
    for p in paths:
        for line in open(p):
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return events

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="aggregate every lifecycle file")
    args = ap.parse_args()
    root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    files = sorted(glob.glob(os.path.join(root, "logs", "turn_lifecycle_*.jsonl")))
    if not files:
        print("No turn_lifecycle_*.jsonl found in logs/ — run a live conversation first.")
        return 1
    paths = files if args.all else [files[-1]]
    print("files:", ", ".join(os.path.basename(f) for f in paths))
    ev = load(paths)

    counts = {}
    for e in ev:
        counts[e["ev"]] = counts.get(e["ev"], 0) + 1

    turns_completed = counts.get("TURN_COMPLETED", 0)
    endpoints = counts.get("VAD_SPEECH_ENDED", 0)
    # premature endpoints: endpoints where the resume gap (recorded at the NEXT start) <= 3000ms
    starts = [e for e in ev if e["ev"] == "VAD_SPEECH_STARTED"]
    premature = sum(1 for e in starts if isinstance(e.get("resume_gap_ms"), (int, float))
                    and e["resume_gap_ms"] <= 3000)
    buckets = {"<500": 0, "500-1000": 0, "1000-2000": 0, "2000-3000": 0, ">3000": 0}
    for e in starts:
        g = e.get("resume_gap_ms")
        if isinstance(g, (int, float)):
            k = "<500" if g < 500 else "500-1000" if g < 1000 else \
                "1000-2000" if g < 2000 else "2000-3000" if g < 3000 else ">3000"
            buckets[k] += 1

    stt = [e for e in ev if e["ev"] == "STT_COMPLETED"]
    validation = [e for e in ev if e["ev"] == "VALIDATION_COMPLETED"]
    invalid = [e for e in validation if e.get("valid") is False]
    decisions = {}
    for e in ev:
        if e["ev"] == "TURN_DECISION":
            decisions[e.get("decision")] = decisions.get(e.get("decision"), 0) + 1
    drops = {}
    for e in ev:
        if e["ev"] == "TURN_DROPPED":
            drops[e.get("reason")] = drops.get(e.get("reason"), 0) + 1
    barge = [e for e in ev if e["ev"] == "BARGE_IN_STOP_LATENCY_MS"]
    # fragmentation from SESSION_SUMMARY blocks
    frags = []
    for e in ev:
        if e["ev"] == "SESSION_SUMMARY":
            frags.append(e.get("stt_fragmentation") or {})

    print("\n=== PHASE 2 BASELINE ===")
    print(f"total lifecycle events      : {len(ev)}")
    print(f"turns completed             : {turns_completed}")
    print(f"VAD endpoints               : {endpoints}")
    print(f"premature endpoints (resume<=3s): {premature} "
          f"({round(100*premature/endpoints,1) if endpoints else 0}% of endpoints)")
    print(f"resume-gap buckets          : {buckets}")
    print(f"STT completed               : {len(stt)}")
    print(f"validation: valid={len(validation)-len(invalid)} invalid={len(invalid)}")
    print(f"  invalid reasons           : {drops}")
    print(f"turn decisions              : {decisions}")
    if frags:
        n = max((f.get("n", 0) for f in frags), default=0)
        ad = [f.get("avg_duration_ms") for f in frags if f.get("avg_duration_ms")]
        aw = [f.get("avg_words") for f in frags if f.get("avg_words")]
        u15 = sum(f.get("under_1500ms", 0) for f in frags)
        print(f"STT fragmentation           : n={n} avg_dur={ad} avg_words={aw} under_1500ms_total={u15}")
    if barge:
        lat = [e["latency_ms"] for e in barge if isinstance(e.get("latency_ms"), (int, float))]
        if lat:
            print(f"barge-in stop latency       : n={len(lat)} avg={round(sum(lat)/len(lat),1)}ms "
                  f"max={round(max(lat),1)}ms")
    else:
        print("barge-in stop latency       : no barge-ins recorded")
    print("\nPer-turn compact trace (endpoints + decisions):")
    for e in ev:
        if e["ev"] in ("VAD_SPEECH_ENDED", "TURN_DECISION", "TURN_DROPPED", "TURN_COMPLETED"):
            extra = {k: v for k, v in e.items() if k in ("turn", "decision", "reason",
                                                          "speech_duration_ms", "threshold_ms",
                                                          "trailing_silence_ms")}
            print(f"  t={e['t']:>8} {e['ev']:<22} {extra}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())

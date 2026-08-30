#!/usr/bin/env python3
"""Contract + voice-quality A/B report (task 2026-08-30).

Reads session logs + lifecycle JSONL and produces the 9 required metrics
per session group, then a pre/post comparison when both are given:

  response length / audio duration | interruption rate | actual barge-in
  stop latency (vad_to_stop, cancel_to_stop) | STT rejection rate |
  repeated-response rate | topic continuity | contract violations/blocks |
  detail-mode completion quality | latency

Usage:
  python3 phase5/contract_ab_report.py                          # latest logs
  python3 phase5/contract_ab_report.py --pre a.log b.log --post c.log d.log
  python3 phase5/contract_ab_report.py logs/session_*.log       # one group

Provider incidents (429/TTFA) are flagged per session as confounders so
provider noise is not read as behavior change.
"""
import argparse, glob, json, os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)


def load_logs(paths):
    turns, events = [], []
    for p in paths:
        if not os.path.exists(p):
            continue
        for line in open(p, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if "event" in rec and rec.get("event"):
                events.append(rec)
            elif rec.get("turn") is not None:
                turns.append(rec)
    return turns, events


def agg(turns, events, label):
    n = len(turns)
    if not n:
        return {"label": label, "turns": 0}

    replied = [t for t in turns if t.get("tts_text") or t.get("llm_response")]
    audio = [t["tts"]["audio_duration_s"] for t in turns
             if t.get("tts") and t["tts"].get("audio_duration_s") is not None]
    interrupted = [t for t in turns if t.get("response_state") == "PARTIALLY_PLAYED"]
    pre_audio = [t for t in turns if t.get("cancel_pre_audio")]
    rejected = [t for t in turns if t.get("stt_valid") is False]
    repeats = [t for t in turns if t.get("repeat_detected")]
    contracts = [t for t in turns if t.get("contract_violations")]
    blocked_ev = [e for e in events if e.get("event") == "CONTRACT_BLOCKED"]
    violated_ev = [e for e in events if e.get("event") == "CONTRACT_VIOLATION"]
    mid_sentence = [t for t in turns if t.get("chunk_mid_sentence")]
    plans = [t for t in turns if t.get("head_plan")]
    details = [t for t in turns if t.get("detail_mode")]

    barge = [t["barge_ms"] for t in turns if t.get("barge_ms")]
    vad_stops = [b["vad_to_stop_ms"] for b in barge]
    cancel_stops = [b["cancel_to_stop_ms"] for b in barge if b.get("cancel_to_stop_ms")]

    lat = [t["speech_end_to_first_audio_s"] for t in turns
           if t.get("speech_end_to_first_audio_s") is not None]
    ttfa = [t["tts_first_audio_s"] for t in turns
            if t.get("tts_first_audio_s") is not None]
    llm = [t["llm_ttft_s"] for t in turns if t.get("llm_ttft_s") is not None]

    # Topic continuity: per-turn perception head topic/thread labels.
    topics = []
    for t in turns:
        h = t.get("perception_head") or {}
        if isinstance(h, dict):
            topics.append(h.get("topic") or h.get("thread") or h.get("emotion"))
    topic_jumps = 0
    prev = None
    for tp in topics:
        if tp is not None and prev is not None and tp != prev:
            topic_jumps += 1
        prev = tp
    distinct_topics = len({x for x in topics if x is not None})

    # Provider confounders
    prov_incidents = sum(1 for e in events if e.get("event") in (
        "RESPONSE_FAILED", "LLM_RETRY_429", "TTS_FALLBACK"))
    prov_429 = sum(1 for e in events
                   if e.get("event") == "RESPONSE_FAILED"
                   and "429" in str(e.get("error", "")))
    warm = [t for t in turns if t.get("tts") and t["tts"].get("warm") is True]

    def mean(xs):
        return round(sum(xs) / len(xs), 2) if xs else None

    return {
        "label": label,
        "turns": n,
        "replies": len(replied),
        "audio_duration_s_avg": mean(audio),
        "interruption_rate": round(len(interrupted) / len(replied), 3) if replied else None,
        "pre_audio_cancel_rate": round(len(pre_audio) / len(replied), 3) if replied else None,
        "barge_vad_to_stop_ms_avg": mean(vad_stops),
        "barge_cancel_to_stop_ms_avg": mean(cancel_stops),
        "stt_rejection_rate": round(len(rejected) / n, 3),
        "repeated_response_rate": round(len(repeats) / len(replied), 3) if replied else None,
        "topic_jumps": topic_jumps,
        "distinct_topics": distinct_topics,
        "contract_violation_turns": len(contracts),
        "contract_violation_events": len(violated_ev),
        "contract_block_events": len(blocked_ev),
        "detail_turns": len(details),
        "plan_turns": len(plans),
        "chunk_mid_sentence_rate": round(len(mid_sentence) / len(replied), 3) if replied else None,
        "speech_to_audio_s_avg": mean(lat),
        "llm_ttft_s_avg": mean(llm),
        "ttfa_s_avg": mean(ttfa),
        "tts_warm_turns": len(warm),
        "provider_incident_events": prov_incidents,
        "provider_429_events": prov_429,
    }


def fmt(v):
    if v is None:
        return "-"
    return str(v)


METRICS = [
    ("turns", "turns"),
    ("audio_duration_s_avg", "audio dur (s)"),
    ("interruption_rate", "interruption rate"),
    ("pre_audio_cancel_rate", "pre-audio cancel rate"),
    ("barge_vad_to_stop_ms_avg", "barge vad->stop (ms)"),
    ("barge_cancel_to_stop_ms_avg", "barge cancel->stop (ms)"),
    ("stt_rejection_rate", "STT rejection rate"),
    ("repeated_response_rate", "repeat-response rate"),
    ("topic_jumps", "topic jumps"),
    ("distinct_topics", "distinct topics"),
    ("contract_violation_events", "contract violations (ev)"),
    ("contract_block_events", "contract blocks (ev)"),
    ("detail_turns", "detail turns"),
    ("plan_turns", "plan turns"),
    ("chunk_mid_sentence_rate", "chunk mid-sentence rate"),
    ("speech_to_audio_s_avg", "speech->audio (s)"),
    ("llm_ttft_s_avg", "LLM TTFT (s)"),
    ("ttfa_s_avg", "TTS TTFA (s)"),
    ("tts_warm_turns", "TTS warm turns"),
    ("provider_incident_events", "provider incidents"),
    ("provider_429_events", "provider 429s"),
]


def print_group(rows):
    for key, name in METRICS:
        vals = [fmt(r.get(key)) for r in rows]
        print(f"  {name:24s} " + " | ".join(vals))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="*")
    ap.add_argument("--pre", nargs="+", default=[])
    ap.add_argument("--post", nargs="+", default=[])
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    groups = []
    if args.pre or args.post:
        if args.pre:
            groups.append(("PRE ", args.pre))
        if args.post:
            groups.append(("POST", args.post))
    elif args.paths:
        groups.append(("LOG ", args.paths))
    else:
        files = sorted(glob.glob("logs/session_*.log"), key=os.path.getmtime)
        if not files:
            print("No logs found. Pass paths or --pre/--post.")
            return 1
        groups.append(("LOG ", files[-1:]))

    rows = []
    for label, paths in groups:
        t, e = load_logs(paths)
        rows.append(agg(t, e, label))

    if args.json:
        print(json.dumps(rows, indent=2, default=str))
        return 0

    print("=" * 78)
    print("CONTRACT + VOICE-QUALITY A/B REPORT")
    print("=" * 78)
    for i, (label, paths) in enumerate(groups):
        r = rows[i]
        print(f"\n[{label.strip()}] {r['label']} — {len(paths)} file(s), {r['turns']} turns")
        print(f"  provider incidents: {r['provider_incident_events']} "
              f"(429: {r['provider_429_events']})  <-- confounder flag for A/B")
    print("\n" + "-" * 78)
    print("METRIC TABLE (columns = groups in order above)")
    print("-" * 78)
    print_group(rows)
    print("\nInterpretation guardrails:")
    print("  - If provider incidents differ between groups, latency metrics are")
    print("    confounded — do NOT attribute to behavior changes.")
    print("  - interruption_rate is NOT engagement; treat as latency/quality.")
    print("  - barge vad->stop should be ~cancel->stop + STT overlap after reorder.")
    print("  - full per-turn detail: python3 phase5/stt_rejection_report.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

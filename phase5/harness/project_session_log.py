#!/usr/bin/env python3
"""Phase-0 gate tooling: project a real session_*.log down to the exact field
set the replay gate consumes, so the owner can paste it through chat.

WHY THIS EXISTS (2026-08-30): the replay gate (phase5/harness/replay.py) only
reads a CURATED subset of each archived turn dict (see replay._compare and
replay.context_from_archived). Everything else in the live archive — acoustic,
timestamps, providers, tts, llm_input, telemetry — is explicitly excluded by
the gate. The raw session log can be 100KB+ (llm_input alone), which is
unwieldy to move through chat; this projection drops exactly the fields the
gate never reads and keeps every gate-consumed field BYTE-EXACT, so

    replay(project(log))  ==  replay(log)

Byte-for-byte on every compared field. This is a faithful projection of the
real archive, NOT a synthetic replay: the values are the owner's live
capture on the frozen build, untouched.

USAGE (on the owner's Mac, in the repo root):
    python3 phase5/harness/project_session_log.py logs/session_20260830_175618.log
    python3 phase5/harness/project_session_log.py 'logs/session_*.log' --out /tmp/proj.jsonl
    python3 phase5/harness/project_session_log.py logs/session_*.log --chunks 3

--chunks N splits stdout with ===CHUNK k/N=== markers so a long file can be
pasted through chat in N parts and reassembled by the receiver.

The frozen build (f7937a4) and the critical path are NOT touched by this
script: it imports only the stdlib and is never imported by agent code.
"""
import argparse
import json
import sys
from pathlib import Path

# Every key the replay gate reads, on either side of the comparison, or when
# rebuilding the TurnContext (context_from_archived) / prior-turn state.
# Keep-if-present: the live archive sets several of these only when an event
# occurs (contract_*, script_transliterated, tag_leak_stripped, ...); absent
# keys must STAY absent for the byte-exact None==None comparison.
GATE_KEYS = [
    "turn", "turn_type", "acoustic", "user_speech_start", "user_speech_end",
    "agent_was_speaking", "stt_transcript", "stt_valid", "stt_rejection_reason",
    "stt_avg_logprob", "response_state", "route_action", "route_reason",
    "dropped_reason", "response_trigger_reason", "recovery_mode", "engine_path",
    "llm_called", "head_plan", "llm_response_full", "llm_response",
    "reply_trimmed", "contract_block_count", "contract_violations",
    "repeat_guarded", "script_transliterated", "tag_leak_stripped",
    "interrupted", "heard_text", "remaining_text", "reconciles_previous",
    "previous_plan", "challenge_detected", "detail_mode", "detail_complete",
    "detail_latch_after", "policy",
]


def project_line(line: str) -> str | None:
    """Return the projected JSON line for one raw log line, or None if the
    line is not a turn dict."""
    line = line.strip()
    if not line:
        return None
    try:
        turn = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(turn, dict) or "turn" not in turn:
        return None
    proj = {k: turn[k] for k in GATE_KEYS if k in turn}
    return json.dumps(proj, ensure_ascii=False)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pattern", help="session log path or glob (quote it)")
    ap.add_argument("--out", help="write to this file instead of stdout")
    ap.add_argument("--chunks", type=int, default=0,
                    help="split stdout into N pastable chunks with markers")
    args = ap.parse_args(argv)

    paths = sorted(Path.cwd().glob(args.pattern)) if any(
        ch in args.pattern for ch in "*?[") else [Path(args.pattern)]
    lines: list[str] = []
    n_turns = 0
    for p in paths:
        if not p.is_file():
            print(f"[project] no such session file: {p}", file=sys.stderr)
            continue
        with open(p, encoding="utf-8") as f:
            for line in f:
                proj = project_line(line)
                if proj is None:
                    continue
                lines.append(proj)
                n_turns += 1
    if not n_turns:
        print("[project] no turn lines found in any input file", file=sys.stderr)
        return 1

    text = "\n".join(lines) + "\n"
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"[project] {n_turns} turns -> {args.out} ({len(text)} bytes)")
        return 0

    if args.chunks > 1:
        # Split on line boundaries so each JSON object stays whole.
        n = max(1, min(args.chunks, n_turns))
        per = (n_turns + n - 1) // n
        for k in range(n):
            part = lines[k * per:(k + 1) * per]
            if not part:
                continue
            print(f"===CHUNK {k + 1}/{n}===")
            print("\n".join(part))
        print(f"===END ({n_turns} turns)===")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())

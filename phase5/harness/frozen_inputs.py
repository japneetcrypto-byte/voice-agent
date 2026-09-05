#!/usr/bin/env python3
"""Frozen upstream artifacts for the deterministic test tiers (2026-09-05).

WHAT IS FROZEN. Every replayed turn is driven by evidence that a LIVE session
paid an expensive call for: audio -> STT (Gemini Live / Groq Whisper), VAD /
endpointing over the raw frames, the fused-turn LLM stream, TTS synthesis and
playback, and the acoustic echo correlation. main.py archives that evidence
per turn in `logs/session_*.log` (one JSON turn dict per line). The replay
gate (phase5/harness/replay.py) reads ONLY a curated subset of those keys and
recomputes every decision from them — so the archive already IS the frozen
artifact set. This module makes that contract explicit and checkable:

  * FROZEN_KEYS  — the exact keys the deterministic path consumes (INPUT_KEYS)
                   or is compared against (ORACLE_KEYS). Anything else in a
                   raw log (llm_input, prompts, timestamps, ids, telemetry) is
                   NOT part of the test input and never influences a tier.
  * CLASSES      — the keys grouped by the live call they stand in for, so a
                   tier report can state which expensive call is avoided.
  * freeze_session(path) -> manifest: per-turn digests of the frozen
                   projection (inputs / oracle), classes present, provenance.
  * verify_fixture(dir) -> problems: the committed frozen_manifest.json still
                   describes the fixture bytes on disk (drift / tamper guard).
  * emit_projection(path, out): the projected JSONL (transport form; the old
                   project_session_log.py CLI is a thin wrapper over this).

INVARIANTS (owner, 2026-09-05)
  * Recording is read-only: nothing here rewrites an archive. In particular
    turn["numeric_observation"] is frozen as written by the build that heard
    the turn; a later "second look" is a NEW record/version, never an in-place
    mutation — any byte change in an archived observation fails verify.
  * numeric_observation is ORACLE-only: replay recomputes it from the frozen
    stt_transcript and compares; the archived record is never an input.
  * stdlib only; never imported by agent code.

KNOWN GAP (documented, not hidden): Whisper per-segment lists are not retained
by providers/stt.py (only providers/segment_metrics.aggregate_segments output
reaches Transcript), so segments cannot be frozen today. Adding them needs a
Transcript.segments field + archive key = a live STT-path change (LIVE tier).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

FROZEN_VERSION = "frozen-1.0"
MANIFEST_NAME = "frozen_manifest.json"

# ---------------------------------------------------------------------------
# Evidence classes — each stands in for ONE expensive live call.
# Order inside a class is the archive's natural order (kept for readability).
# ---------------------------------------------------------------------------
CLASSES: dict[str, dict] = {
    "STT_EVIDENCE": {
        "stands_in_for": "audio -> STT call (Gemini Live streaming primary / Groq "
                         "whisper-large-v3 fallback) incl. the turn-level segment "
                         "metrics aggregated by providers/segment_metrics",
        "keys": ["stt_transcript", "stt_valid", "stt_rejection_reason",
                 "stt_language", "stt_no_speech_prob", "stt_avg_logprob",
                 "stt_compression_ratio", "stt_provider", "stt_latency_s"],
    },
    "ENDPOINT_EVIDENCE": {
        "stands_in_for": "VAD + adaptive endpointing over the live frames "
                         "(providers/vad, providers/endpointing) and the "
                         "barge/resume timing relative to agent playback",
        "keys": ["endpoint", "premature_resume", "agent_was_speaking",
                 "ms_since_agent_audio_end", "acoustic",
                 "user_speech_start", "user_speech_end"],
    },
    "LLM_ARTIFACT": {
        "stands_in_for": "the fused-turn LLM stream (Gemini) — replay feeds the "
                         "archived full text through the piece pipeline instead "
                         "of calling the model; head_plan is the live-parsed head",
        "keys": ["llm_response_full", "head_plan", "llm_called"],
    },
    "ECHO_EVIDENCE": {
        "stands_in_for": "acoustic echo correlation over the played ring + the "
                         "text echo filter verdict (Phase-2 E1 input; recorded "
                         "now, not yet consumed by replay)",
        "keys": ["echo_shadow", "echo_corr_score", "echo_dropped", "echo_overridden"],
    },
    "DELIVERY_EVIDENCE": {
        "stands_in_for": "TTS synthesis + playback outcome (what the user actually "
                         "heard): replay reconstructs played_any_audio and the "
                         "heard/remaining halves from these, never re-synthesises",
        "keys": ["response_state", "interrupted", "response_suppressed", "tts", "tts_text"],
    },
    "CARRIER_STATE": {
        "stands_in_for": "session task state carried turn to turn (L1 base / "
                         "proposal / pending_edit / delivery, detail plan, latch); "
                         "compared on turn n, re-syncs the carrier for turn n+1",
        "keys": ["turn", "turn_type", "engine_path", "precise_detail",
                 "detail_state", "detail_latch_after", "detail_mode"],
    },
    "ARCHIVED_DECISIONS": {
        "stands_in_for": "nothing expensive — these are the live build's own "
                         "decisions; ORACLE side only (compared, never consumed)",
        "keys": ["route_action", "route_reason", "dropped_reason",
                 "response_trigger_reason", "recovery_mode", "llm_response",
                 "reply_trimmed", "contract_block_count", "contract_violations",
                 "repeat_guarded", "script_transliterated", "tag_leak_stripped",
                 "heard_text", "remaining_text", "reconciles_previous",
                 "previous_plan", "challenge_detected", "detail_complete",
                 "detail_continue", "control_shadow", "policy",
                 "numeric_observation"],
    },
}

# Keys the deterministic path CONSUMES (TurnContext rebuild + prior-turn
# re-sync + skip predicate). llm_response is consumed as the PRIOR turn's
# reply (recent_reply_texts / last_response.heard_text); precise_detail /
# detail_state as the prior turn's carrier re-sync.
INPUT_KEYS: list[str] = (
    CLASSES["STT_EVIDENCE"]["keys"]
    + CLASSES["ENDPOINT_EVIDENCE"]["keys"]
    + CLASSES["LLM_ARTIFACT"]["keys"]
    + CLASSES["ECHO_EVIDENCE"]["keys"]
    + CLASSES["DELIVERY_EVIDENCE"]["keys"]
    + CLASSES["CARRIER_STATE"]["keys"]
    + ["llm_response"]
)

# Keys the gate COMPARES (replay._compare + the skip/selection predicates).
ORACLE_KEYS: list[str] = (
    ["turn", "turn_type", "engine_path", "response_suppressed", "stt_transcript",
     "llm_called", "head_plan", "response_state", "interrupted", "detail_mode",
     "precise_detail", "detail_state"]
    + CLASSES["ARCHIVED_DECISIONS"]["keys"]
)


def _ordered_union(*lists: list[str]) -> list[str]:
    seen: list[str] = []
    for lst in lists:
        for k in lst:
            if k not in seen:
                seen.append(k)
    return seen


FROZEN_KEYS: list[str] = _ordered_union(INPUT_KEYS, ORACLE_KEYS)

# numeric_observation must never be an input (owner invariant).
assert "numeric_observation" not in INPUT_KEYS
assert "numeric_observation" in ORACLE_KEYS


# ---------------------------------------------------------------------------
# Archive reading / projection / digests
# ---------------------------------------------------------------------------
def is_turn_dict(obj) -> bool:
    return isinstance(obj, dict) and "turn" in obj


def iter_turns(path):
    """Yield (line_no, raw_line, turn_dict) for every turn line of a session
    log; non-turn lines (BUILD stamp, prose smoke notes) are skipped exactly
    like replay_session does."""
    with open(path, encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            s = line.strip()
            if not s:
                continue
            try:
                obj = json.loads(s)
            except json.JSONDecodeError:
                continue
            if is_turn_dict(obj):
                yield i, s, obj


def project(turn: dict, keys: list[str] = FROZEN_KEYS) -> dict:
    """Keep-if-present projection: absent keys STAY absent (the gate's
    None == None byte-exact semantics depend on it)."""
    return {k: turn[k] for k in keys if k in turn}


def canon(obj) -> str:
    """Canonical JSON: sorted keys, no whitespace, unicode kept."""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def turn_no(turn: dict) -> int | None:
    try:
        return int(turn.get("turn"))
    except (TypeError, ValueError):
        return None


def freeze_session(path) -> dict:
    """Build the frozen-artifact manifest for one session archive.
    Pure read; nothing is written."""
    path = Path(path)
    per_turn = []
    inputs_lines: list[str] = []
    oracle_lines: list[str] = []
    frozen_lines: list[str] = []
    classes_present: dict[str, set] = {c: set() for c in CLASSES}
    for _, _, turn in iter_turns(path):
        pi = canon(project(turn, INPUT_KEYS))
        po = canon(project(turn, ORACLE_KEYS))
        pf = canon(project(turn, FROZEN_KEYS))
        inputs_lines.append(pi)
        oracle_lines.append(po)
        frozen_lines.append(pf)
        for c, spec in CLASSES.items():
            for k in spec["keys"]:
                if k in turn:
                    classes_present[c].add(k)
        obs = turn.get("numeric_observation") if isinstance(
            turn.get("numeric_observation"), dict) else None
        per_turn.append({
            "turn": turn_no(turn),
            "turn_type": turn.get("turn_type"),
            "engine_path": turn.get("engine_path"),
            "response_suppressed": bool(turn.get("response_suppressed")),
            "stt_provider": turn.get("stt_provider"),
            "observation_version": obs.get("version") if obs else None,
            "observation_certainty": obs.get("certainty") if obs else None,
            "inputs_sha256": sha256_text(pi),
            "oracle_sha256": sha256_text(po),
        })
    return {
        "frozen_version": FROZEN_VERSION,
        "session": path.name,
        "turns": len(per_turn),
        "avoided_calls": avoided_calls(path),
        "inputs_sha256": sha256_text("\n".join(inputs_lines)),
        "oracle_sha256": sha256_text("\n".join(oracle_lines)),
        "projection_sha256": sha256_text("\n".join(frozen_lines)),
        "raw_sha256": sha256_file(path),
        "raw_bytes": path.stat().st_size,
        "projection_bytes": sum(len(l.encode("utf-8")) + 1 for l in frozen_lines),
        "classes_present": {c: sorted(v) for c, v in classes_present.items()},
        "per_turn": per_turn,
    }


def avoided_calls(path, upto: int | None = None) -> dict:
    """How many expensive live calls the frozen turns of one archive stand in
    for (optionally only turns <= upto). Counted from the evidence keys:
      stt      — a transcript was archived (audio -> STT call happened live)
      vad      — an endpoint record was archived (VAD/endpointing ran live)
      llm      — the fused LLM produced output (llm_called true)
      tts      — a reply was synthesised + played (response_state archived,
                 turn not suppressed)
      echo     — an acoustic echo correlation was computed (corr not None)"""
    c = {"turns": 0, "stt": 0, "vad": 0, "llm": 0, "tts": 0, "echo": 0}
    for _, _, t in iter_turns(path):
        tn = turn_no(t)
        if upto is not None and (tn is None or tn > upto):
            continue
        c["turns"] += 1
        if t.get("stt_transcript") is not None:
            c["stt"] += 1
        if isinstance(t.get("endpoint"), dict):
            c["vad"] += 1
        if t.get("llm_called"):
            c["llm"] += 1
        if t.get("response_state") and not t.get("response_suppressed"):
            c["tts"] += 1
        if t.get("echo_corr_score") is not None:
            c["echo"] += 1
    return c


def manifest_path(fixture_dir) -> Path:
    return Path(fixture_dir) / MANIFEST_NAME


def load_manifest(fixture_dir) -> dict | None:
    p = manifest_path(fixture_dir)
    if not p.is_file():
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def write_manifest(fixture_dir, sessions: list[Path]) -> dict:
    """Write <fixture_dir>/frozen_manifest.json for the given session files
    (one fixture dir may hold several archives). Returns the manifest."""
    fixture_dir = Path(fixture_dir)
    man = {
        "frozen_version": FROZEN_VERSION,
        "fixture": fixture_dir.name,
        "frozen_keys": FROZEN_KEYS,
        "sessions": [freeze_session(p) for p in sorted(sessions)],
    }
    with open(manifest_path(fixture_dir), "w", encoding="utf-8") as f:
        json.dump(man, f, ensure_ascii=False, indent=1)
        f.write("\n")
    return man


def verify_fixture(fixture_dir, sessions: list[Path]) -> list[str]:
    """Return a list of human-readable problems ([] = the committed manifest
    matches the fixture bytes and the current FROZEN_KEYS contract)."""
    fixture_dir = Path(fixture_dir)
    problems: list[str] = []
    man = load_manifest(fixture_dir)
    if man is None:
        return [f"{fixture_dir.name}: no {MANIFEST_NAME} (run: test_tiers.py freeze --update)"]
    if man.get("frozen_version") != FROZEN_VERSION:
        problems.append(f"{fixture_dir.name}: manifest frozen_version "
                        f"{man.get('frozen_version')} != {FROZEN_VERSION}")
    if man.get("frozen_keys") != FROZEN_KEYS:
        problems.append(f"{fixture_dir.name}: FROZEN_KEYS contract changed since the "
                        f"manifest was recorded (re-freeze deliberately)")
    recorded = {s["session"]: s for s in man.get("sessions", [])}
    for p in sorted(sessions):
        rec = recorded.get(p.name)
        if rec is None:
            problems.append(f"{fixture_dir.name}/{p.name}: not in manifest")
            continue
        cur = freeze_session(p)
        for field in ("turns", "inputs_sha256", "oracle_sha256", "projection_sha256"):
            if cur[field] != rec.get(field):
                problems.append(f"{fixture_dir.name}/{p.name}: {field} "
                                f"{rec.get(field)} -> {cur[field]}")
        if cur["inputs_sha256"] == rec.get("inputs_sha256") and \
                cur["oracle_sha256"] == rec.get("oracle_sha256"):
            # same frozen content; a raw-byte change outside FROZEN_KEYS is
            # informational (it cannot influence any tier)
            if cur["raw_sha256"] != rec.get("raw_sha256"):
                problems.append(f"{fixture_dir.name}/{p.name}: NOTE raw bytes changed "
                                f"outside FROZEN_KEYS (frozen content identical)")
    for name in recorded:
        if name not in {p.name for p in sessions}:
            problems.append(f"{fixture_dir.name}/{name}: in manifest but missing on disk")
    return problems


def emit_projection(path, out=None, keys: list[str] = FROZEN_KEYS) -> tuple[int, str]:
    """Projected JSONL (one frozen turn per line, archive key order kept).
    Returns (n_turns, text); writes to `out` when given."""
    lines = []
    for _, _, turn in iter_turns(path):
        lines.append(json.dumps(project(turn, keys), ensure_ascii=False))
    text = "\n".join(lines) + ("\n" if lines else "")
    if out:
        Path(out).write_text(text, encoding="utf-8")
    return len(lines), text


if __name__ == "__main__":  # tiny CLI: print the manifest of one archive
    import sys
    if len(sys.argv) != 2:
        print("usage: frozen_inputs.py <session_log>")
        sys.exit(2)
    m = freeze_session(sys.argv[1])
    m["per_turn"] = f"<{len(m['per_turn'])} turns>"
    print(json.dumps(m, ensure_ascii=False, indent=1))

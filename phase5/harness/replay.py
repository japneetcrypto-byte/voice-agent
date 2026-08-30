#!/usr/bin/env python3
"""Phase-0 replay gate (Slice 1) — replay the extracted deterministic
decision core over archived session turn dicts and verify identity.

Wired 2026-08-30. The archive (`session_*.log`, one JSON turn dict per line)
was produced by the LIVE main.py; this gate replays every turn through the
EXTRACTED pure core (agent/response_pipeline.py + agent/turn_router.py) with
inputs taken from the archived turn itself, and requires the reproduced
decisions to equal the archived ones. Any divergence = the extraction drifted
(or a live bug the extraction surfaced) — Phase 0 is NOT complete while the
diff is non-empty (owner gate: replay identity byte-equal, modulo
timestamps/ids).

Covered per turn (all inputs archived in the turn dict):
  1. ROUTING — route_decision(stt_transcript, stt_valid, stt_rejection_reason,
     stt_avg_logprob, is_repetition_loop, catastrophic flag,
     agent_was_speaking, engine_bound) vs archived route_action /
     route_reason / turn_type / response_trigger_reason / dropped_reason.
  2. PIECES — release_from + release_tail + process_piece over archived
     llm_response_full (cap reconstructed from archived detail_mode /
     route_action / head_plan) vs archived llm_response and the enforcement
     flags (contract_block_count, contract_violations, repeat_guarded,
     script_transliterated, tag_leak_stripped, reply_trimmed).
     Known artifact: the LIVE tee's cap-trim point depends on token-stream
     chunk boundaries (a space at the trim edge may or may not have arrived
     in the same chunk), so trimmed turns are compared content-equal
     (whitespace-collapsed); untrimmed turns byte-equal.
  3. POLICY NUDGES — the deterministic deltas of build_policy_and_contract
     that are fully re-derivable from archived inputs: detail delivery
     (chunked/continue), challenge nudge (is_challenge on archived text),
     recovery nudge (archived route_action), reconcile payloads (prior
     turn's archived response_state / head_plan), supervisor path marker.

NOT replayed (Slice-2 snapshot territory — documented, not silent):
  - policy.contract BODY (depends on runtime memory_count / SessionState —
    requires state-snapshot injection, Phase-0 §6 Slice-2)
  - supervisor_rescue / idle turns (their turn_no is the idle seq counter,
    not the entrypoint turn_number the live gate used)
  - anti-parrot nudge trigger (stuck_nudged window is runtime state; its
    MUTATION SHAPE is still verified when archived)
  - telemetry sinks (events_*.log, turn_lifecycle_*.jsonl)

Usage:
  python3 phase5/harness/replay.py phase5/harness/fixtures/*/session_*.log
Exit: 0 = identity holds (empty diff) · 1 = any field diverged.
"""
from __future__ import annotations

import json
import os
import re
import sys
from collections import deque
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from agent.response_pipeline import (build_policy_and_contract, process_piece,
                                     release_from, release_tail)
from agent.turn_router import route_decision
from agent.reply_guard import cap_for, PLAN_CHUNK_CAP, remaining_text, is_challenge
from agent.response_state import reconcile_payload
from agent.stt_validation import is_repetition_loop

# Turns whose routing/decision fields are not archived enough for replay.
SKIP_TURN_TYPES = ("supervisor_rescue", "idle")

CONTINUE_CUES = ("haan", "aage", "phir", "हाँ", "आगे", "और")


def _norm(text: str) -> str:
    return " ".join((text or "").split())


def _replay_cap(turn: dict) -> int:
    """Reconstruct the caps.cap the live tee used (main.py wiring, verbatim)."""
    cap = cap_for(bool(turn.get("detail_mode")))
    if turn.get("route_action") == "contextual_recovery":
        cap = min(cap, 110)
    plan = turn.get("head_plan")
    if isinstance(plan, dict) and isinstance(plan.get("total"), int) and plan["total"] > 1:
        cap = max(cap, PLAN_CHUNK_CAP)
    return cap


def replay_turn(turn: dict, *, prior_reply: str | None, prior_state: dict) -> dict:
    """Replay one archived turn through the extracted core.

    Returns {field: (replayed, archived)} for every divergence — {} when the
    turn is reproduced exactly.
    """
    diffs: dict = {}
    if not isinstance(turn, dict) or turn.get("turn_type") in SKIP_TURN_TYPES:
        return diffs
    user_text = turn.get("stt_transcript") or ""

    # ---- 1. routing ----
    archived_action = turn.get("route_action")
    if archived_action is not None:
        if turn.get("engine_path"):
            engine_bound = turn["engine_path"] == "fused"
        else:
            # Dropped turns: engine bound-ness is not archived, but the drop
            # decision is identical for both bound states (see turn_router).
            engine_bound = False
        r = route_decision(
            transcript_text=user_text,
            is_valid=turn.get("stt_valid"),
            rejection_reason=turn.get("stt_rejection_reason"),
            avg_logprob=turn.get("stt_avg_logprob"),
            is_repetition=is_repetition_loop(user_text),
            is_catastrophic=(turn.get("stt_rejection_reason")
                             == "catastrophic_low_confidence"),
            agent_was_speaking=bool(turn.get("agent_was_speaking")),
            engine_bound=engine_bound,
        )
        if r["action"] != archived_action:
            diffs["route_action"] = (r["action"], archived_action)
        if r["reason"] != turn.get("route_reason"):
            diffs["route_reason"] = (r["reason"], turn.get("route_reason"))
        if r["respond_now"]:
            if r["turn_type"] != turn.get("turn_type"):
                diffs["turn_type"] = (r["turn_type"], turn.get("turn_type"))
            # response_trigger_reason is OVERWRITTEN to "completed" at
            # playback end for completed turns (main.py completion path), so
            # it survives only on interrupted responded turns — compare there.
            if turn.get("response_state") and turn["response_state"] != "FULLY_PLAYED":
                if r["trigger"] != turn.get("response_trigger_reason"):
                    diffs["response_trigger_reason"] = (
                        r["trigger"], turn.get("response_trigger_reason"))
        if r["drop"]:
            if r["drop_reason"] != turn.get("dropped_reason"):
                diffs["dropped_reason"] = (r["drop_reason"], turn.get("dropped_reason"))
        # Cross-checks: archived evidence must agree with the decision shape.
        if bool(turn.get("dropped_reason")) != r["drop"]:
            diffs["drop_flag"] = (r["drop"], bool(turn.get("dropped_reason")))
        if turn.get("turn_type") in ("acoustic_only", "unclear_speech") and not r["respond_now"]:
            diffs["responded_turn_decision"] = (r["respond_now"], True)

    # ---- 2. pieces (llm_response_full -> spoken) ----
    full = turn.get("llm_response_full")
    if full:
        cap = _replay_cap(turn)
        trim = {"pending": "", "emitted": 0, "done": False}
        gs = {"guarded": False, "trim": trim}
        replay_turn = {}
        pieces = []
        for i in range(0, len(full), 17):  # arbitrary chunking; content-invariant
            p = release_from(trim, full[i:i + 17], cap=cap)
            if p:
                pieces.append(p)
        tail_trimmed = False
        if not trim["done"] and trim["pending"]:
            if trim["emitted"] + len(trim["pending"]) > cap and trim["emitted"] > 0:
                tail_trimmed = True
            t = release_tail(trim, cap=cap)
            if t:
                pieces.append(t)
        spoken_replay = []
        recent = [prior_reply] if prior_reply else []
        for p in pieces:
            spoken_replay.append(process_piece(
                p, replay_turn,
                recent_reply_texts=recent,
                user_text=user_text,
                turn_number=int(turn.get("turn", 0)),
                guard_state=gs,
                log_event=lambda *a, **k: None,
            ))
        joined = "".join(spoken_replay)
        archived_spoken = turn.get("llm_response") or ""
        if joined != archived_spoken:
            if _norm(joined) != _norm(archived_spoken):
                diffs["llm_response"] = (joined, archived_spoken)
            else:
                diffs.setdefault("notes", []).append(
                    f"turn {turn.get('turn')}: llm_response content-equal but "
                    "whitespace-attribution differs (live chunk-boundary artifact)")
        # Enforcement flags (byte-exact — deterministic given the inputs).
        if replay_turn.get("contract_block_count", 0) != turn.get("contract_block_count", 0):
            diffs["contract_block_count"] = (
                replay_turn.get("contract_block_count", 0),
                turn.get("contract_block_count", 0))
        if (replay_turn.get("contract_violations") or []) != (turn.get("contract_violations") or []):
            diffs["contract_violations"] = (
                replay_turn.get("contract_violations"), turn.get("contract_violations"))
        for flag in ("repeat_guarded", "script_transliterated", "tag_leak_stripped"):
            if replay_turn.get(flag) != turn.get(flag):
                diffs[flag] = (replay_turn.get(flag), turn.get(flag))
        replay_trimmed = bool(trim["done"]) or tail_trimmed
        if replay_trimmed != bool(turn.get("reply_trimmed")):
            diffs["reply_trimmed"] = (replay_trimmed, bool(turn.get("reply_trimmed")))

    # ---- 3. policy nudges (deterministic deltas, archived inputs only) ----
    arch_policy = turn.get("policy") if isinstance(turn.get("policy"), dict) else {}
    if turn.get("detail_mode"):
        want_delivery = ("continue_detail" if any(c in (user_text or "").lower()
                                                  for c in CONTINUE_CUES)
                         else "chunked_detail")
        got_delivery = arch_policy.get("delivery")
        if got_delivery != want_delivery:
            diffs["policy.delivery"] = (want_delivery, got_delivery)
    if turn.get("challenge_detected") is not None:
        want_challenge = bool(is_challenge(user_text))
        if want_challenge != bool(turn["challenge_detected"]):
            diffs["challenge_detected"] = (want_challenge, bool(turn["challenge_detected"]))
        if turn["challenge_detected"]:
            if "flip_flop_agreeing" not in (arch_policy.get("avoid") or []):
                diffs["policy.challenge_avoid"] = (True, False)
            if arch_policy.get("response_goal") != "reconcile_claim":
                diffs["policy.challenge_goal"] = ("reconcile_claim",
                                                  arch_policy.get("response_goal"))
    if turn.get("route_action") == "contextual_recovery":
        if turn.get("recovery_mode") != "contextual_recovery":
            diffs["recovery_mode"] = (turn.get("route_action"), turn.get("recovery_mode"))
        if "long_monologue_on_shaky_transcript" not in (arch_policy.get("avoid") or []):
            diffs["policy.recovery_avoid"] = (True, False)
        if arch_policy.get("response_goal") != "checkpoint_recovery":
            diffs["policy.recovery_goal"] = ("checkpoint_recovery",
                                             arch_policy.get("response_goal"))
    # Anti-parrot nudge: the TRIGGER (stuck window) is runtime state, but when
    # the archived policy carries the nudge, its shape must be intact.
    if "echo_confirm_parroting" in (arch_policy.get("avoid") or []):
        if arch_policy.get("response_goal") != "substantive_reaction":
            diffs["policy.antiparrot_goal"] = ("substantive_reaction",
                                               arch_policy.get("response_goal"))
    # Reconcile payloads: derived from the PRIOR turn's archived state (the
    # engine.last_response the live code popped was built from the prior
    # turn's own completion — status, heard_text, remaining_text).
    prior_status = prior_state.get("response_state")
    if prior_status:
        prior_spoken = prior_state.get("llm_response") or ""
        prior_full = prior_state.get("llm_response_full")
        _lr = {"status": prior_status, "turn": prior_state.get("turn"),
               "heard_text": prior_spoken}
        if prior_status != "FULLY_PLAYED" and prior_full:
            _lr["remaining_text"] = remaining_text(prior_full, prior_spoken)
        want_payload = reconcile_payload(_lr)
        if want_payload != turn.get("reconciles_previous"):
            diffs["reconciles_previous"] = (
                want_payload, turn.get("reconciles_previous"))
    prior_plan = prior_state.get("head_plan")
    if turn.get("previous_plan") != prior_plan:
        diffs["previous_plan"] = (prior_plan, turn.get("previous_plan"))

    return diffs


def replay_session(session_path) -> tuple[dict, int, int]:
    """Replay every turn in one archived session log. Returns
    (per_turn_diffs, turns_checked, turns_skipped)."""
    per_turn = {}
    prior_reply = None
    prior_state: dict = {}
    turns_checked = 0
    turns_skipped = 0
    with open(session_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                turn = json.loads(line)
            except json.JSONDecodeError:
                continue  # non-turn log line (manifest noise etc.)
            if not isinstance(turn, dict) or "turn" not in turn:
                continue
            if turn.get("turn_type") in SKIP_TURN_TYPES:
                turns_skipped += 1
                continue
            d = replay_turn(turn, prior_reply=prior_reply, prior_state=prior_state)
            if d:
                per_turn[turn.get("turn")] = d
            turns_checked += 1
            if turn.get("llm_response"):
                prior_reply = turn["llm_response"]
            prior_state = turn
    return per_turn, turns_checked, turns_skipped


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print(__doc__)
        return 2
    total_diffs = 0
    total_checked = 0
    for pattern in argv:
        for p in sorted(Path.cwd().glob(pattern)) if any(ch in pattern for ch in "*?[") \
                else [Path(pattern)]:
            if not p.is_file():
                print(f"[replay] no such session file: {p}")
                continue
            diffs, checked, skipped = replay_session(p)
            total_checked += checked
            if not diffs:
                print(f"[replay] IDENTITY OK — {p} ({checked} turns, "
                      f"{skipped} supervisor/idle skipped)")
            else:
                total_diffs += len(diffs)
                print(f"[replay] DIVERGENCE — {p}: {len(diffs)} turn(s) differ")
                for turn_no, d in diffs.items():
                    for field, (got, want) in d.items():
                        if field == "notes":
                            continue
                        print(f"    t{turn_no}.{field}: replay={got!r} archived={want!r}")
                    for note in d.get("notes", []):
                        print(f"    t{turn_no} note: {note}")
    print(f"[replay] {total_checked} turns checked, "
          f"{total_diffs} divergent field(s)")
    return 1 if total_diffs else 0


if __name__ == "__main__":
    sys.exit(main())

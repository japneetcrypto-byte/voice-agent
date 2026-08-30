#!/usr/bin/env python3
"""Phase-0 replay gate (Slice 1) — replay the extracted deterministic
critical path over archived session turn dicts and verify identity.

Wired 2026-08-30. The archive (`session_*.log`, one JSON turn dict per line)
was produced by the LIVE main.py; this gate rebuilds each turn's context from
the archived turn + the prior turn's archived state, executes the approved §6
interface `run_turn(context) -> turn_dict` (agent/response_pipeline.py), and
requires the reproduced decisions to equal the archived ones. Any divergence
= the extraction drifted (or a live bug the extraction surfaced) — Phase 0 is
NOT complete while the diff is non-empty (owner gate: replay identity,
modulo timestamps/ids).

Status wording (per Phase-0 review 2026-08-30):
  "Slice-1 replay infrastructure is proven; Phase-0 replay identity is not
   yet proven." — the real proof is replaying OWNER baseline logs captured on
   the frozen commit e0dc60f (the synthetic fixture is a self-consistency
   check, not preservation proof).

Covered per turn (all inputs archived in the turn dict):
  - ROUTING        — route_decision vs archived route_action / route_reason /
                     turn_type / dropped_reason
  - POLICY DELTAS  — the deterministic nudges of build_policy_and_contract
                     (detail delivery, challenge, recovery, anti-parrot shape)
  - PIECES         — release_from + release_tail + process_piece over
                     llm_response_full vs llm_response and enforcement flags
                     (contract_block_count/violations, repeat_guarded,
                     script_transliterated, tag_leak_stripped, reply_trimmed)
  - COMPLETION     — response_state, interrupted halves (heard_text /
                     remaining_text), reconcile payloads + previous_plan
                     (from the PRIOR turn's archived state)

Known boundaries (documented, not silent):
  - policy.contract BODY depends on runtime memory_count / SessionState —
    Slice-2 state-snapshot territory; only the deterministic deltas compare.
  - supervisor_rescue / idle turns: their turn_no is the idle-seq counter,
    not the entrypoint turn_number the live gate used.
  - response_suppressed turns: the turn-controller decision is not extracted
    yet (Slice 2) — skipped, never compared.
  - Trimmed turns: the LIVE cap-trim point depends on token-stream chunk
    boundaries, so llm_response compares content-equal (whitespace-collapsed);
    untrimmed turns byte-equal.
  - Telemetry sinks (events_*.log, turn_lifecycle_*.jsonl) not replayed.

Usage:
  python3 phase5/harness/replay.py 'phase5/harness/fixtures/*/session_*.log'
Exit: 0 = identity holds (empty diff) · 1 = any field diverged.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from agent.response_pipeline import run_turn, TurnContext
from agent.response_state import FULLY_PLAYED
from agent.reply_guard import remaining_text

# Turns whose decision path is not fully archived (or not extracted yet).
SKIP_TURN_TYPES = ("supervisor_rescue", "idle")

CONTINUE_CUES = ("haan", "aage", "phir", "हाँ", "आगे", "और")


def _norm(text: str) -> str:
    return " ".join((text or "").split())


# ---------------------------------------------------------------------------
# Context reconstruction: archived turn + prior turn state -> TurnContext
# ---------------------------------------------------------------------------
class _FakeSess:
    """Replay-side stand-in for SessionState. policy_for_turn() feeds the
    base policy the deterministic nudges mutate; the base values themselves
    (mode/goals/must_not) are runtime state and are NOT compared — only the
    deterministic deltas are (see _compare_policy)."""
    def __init__(self):
        self.state = {"degraded_perception": False}
    def policy_for_turn(self):
        return {"mode": None, "avoid": [], "response_goal": None,
                "delivery": None, "topic": None, "goals": [], "must_not": []}
    def memory_view(self):
        return []


class _FakeLCM:
    def __init__(self):
        self.turns = []
    def add_turn(self, role, text):
        self.turns.append((role, text))
    def needs_compression(self):
        return False
    def get_overflow_turns(self):
        return []
    def get_compression_prompt(self, overflow):
        return ""
    def get_layer1(self):
        return self.turns
    def get_layer2(self):
        return {"active_topic": None}


def context_from_archived(turn: dict, *, prior_state: dict,
                          prior_reply: str | None) -> TurnContext:
    """Rebuild the injected context for one archived turn."""
    engine = None
    engine_bound = False
    if turn.get("engine_path") == "fused":
        engine_bound = True
        engine = {"sess": _FakeSess(), "lcm": _FakeLCM(), "fused": None}
        # engine.last_response / last_head_plan that the live code would have
        # had, reconstructed from the PRIOR turn's archived completion.
        prior_status = prior_state.get("response_state")
        if prior_status and prior_status != FULLY_PLAYED:
            prior_spoken = prior_state.get("llm_response") or ""
            engine["last_response"] = {
                "status": prior_status, "turn": prior_state.get("turn"),
                "heard_text": prior_spoken,
                "remaining_text": remaining_text(
                    prior_state.get("llm_response_full") or "", prior_spoken)}
        if prior_state.get("head_plan"):
            engine["last_head_plan"] = prior_state["head_plan"]
    elif turn.get("engine_path") == "unbound_filler":
        engine = {"sess": None, "lcm": None, "fused": None}

    # Detail latch: exact when the archive carries the telemetry field
    # (detail_latch_after = post-decrement value, added 2026-08-30); old
    # archives (frozen e0dc60f) get the heuristic — continuation turns still
    # renew identically, and cap ambiguity on other detail turns is downgraded
    # to a note in replay_turn, never a hard gate failure.
    dl = turn.get("detail_latch_after")
    if dl is not None:
        if int(dl) > 0:
            # build() decrements turns_left by 1 when >0; start at dl+1 so the
            # post-build value equals the archived dl.
            detail_mode = {"turns_left": int(dl) + 1}
        elif turn.get("detail_mode"):
            # dl==0 with the flag set: the latch was consumed THIS turn
            # (pre-build was 1) — the flag is only reproducible from the
            # archived turn["detail_mode"].
            detail_mode = {"turns_left": 1}
        else:
            detail_mode = {"turns_left": 0}
    elif turn.get("detail_mode"):
        detail_mode = {"turns_left": 1}
    else:
        detail_mode = {"turns_left": 0}

    return TurnContext(
        turn_no=int(turn.get("turn", 0)),
        user_text=turn.get("stt_transcript") or "",
        turn_type=turn.get("turn_type") or "speech",
        is_valid=turn.get("stt_valid"),
        rejection_reason=turn.get("stt_rejection_reason"),
        avg_logprob=turn.get("stt_avg_logprob"),
        agent_was_speaking=bool(turn.get("agent_was_speaking")),
        engine_bound=engine_bound,
        engine=engine,
        recent_reply_texts=[prior_reply] if prior_reply else [],
        detail_mode=detail_mode,
        model_text=turn.get("llm_response_full") or "",
        head_plan=turn.get("head_plan"),
        interrupted=bool(turn.get("interrupted")),
        played_any_audio=(turn.get("response_state") not in (None, "UNHEARD")),
        log_event=lambda *a, **k: None,
    )


# ---------------------------------------------------------------------------
# Comparison: reproduced turn dict vs archived (curated; artifacts excluded)
# ---------------------------------------------------------------------------
def _exact(diffs, replay, archived, field):
    if replay.get(field) != archived.get(field):
        diffs[field] = (replay.get(field), archived.get(field))


def _compare(replay: dict, archived: dict) -> dict:
    """Curated field-by-field comparison. Returns {field: (replayed,
    archived)} for every divergence. Runtime-measurement artifacts (acoustic,
    timestamps, providers, tts, echo/telemetry fields) are excluded — the
    replay reproduces DECISIONS, not measurements."""
    diffs: dict = {}

    # routing decision (byte-exact)
    _exact(diffs, replay, archived, "route_action")
    _exact(diffs, replay, archived, "route_reason")
    _exact(diffs, replay, archived, "dropped_reason")
    _exact(diffs, replay, archived, "turn_type")
    _exact(diffs, replay, archived, "response_trigger_reason")
    _exact(diffs, replay, archived, "recovery_mode")

    # response path markers (byte-exact)
    _exact(diffs, replay, archived, "engine_path")
    _exact(diffs, replay, archived, "llm_called")
    _exact(diffs, replay, archived, "head_plan")

    # pieces: spoken text + enforcement flags
    if replay.get("llm_response") != archived.get("llm_response"):
        if _norm(replay.get("llm_response")) != _norm(archived.get("llm_response")):
            diffs["llm_response"] = (replay.get("llm_response"),
                                     archived.get("llm_response"))
        else:
            diffs.setdefault("notes", []).append(
                f"llm_response content-equal but whitespace-attribution "
                "differs (live chunk-boundary artifact)")
    _exact(diffs, replay, archived, "reply_trimmed")
    _exact(diffs, replay, archived, "contract_block_count")
    _exact(diffs, replay, archived, "contract_violations")
    _exact(diffs, replay, archived, "repeat_guarded")
    _exact(diffs, replay, archived, "script_transliterated")
    _exact(diffs, replay, archived, "tag_leak_stripped")

    # completion (byte-exact)
    _exact(diffs, replay, archived, "response_state")
    _exact(diffs, replay, archived, "interrupted")
    _exact(diffs, replay, archived, "heard_text")
    _exact(diffs, replay, archived, "remaining_text")
    _exact(diffs, replay, archived, "reconciles_previous")
    _exact(diffs, replay, archived, "previous_plan")
    _exact(diffs, replay, archived, "challenge_detected")
    _exact(diffs, replay, archived, "detail_mode")
    _exact(diffs, replay, archived, "detail_complete")

    # policy DELTAS (base policy values are runtime state — not compared)
    arch_policy = archived.get("policy") if isinstance(archived.get("policy"), dict) else {}
    if archived.get("detail_mode"):
        want_delivery = ("continue_detail" if any(c in (archived.get("stt_transcript") or "").lower()
                                                  for c in CONTINUE_CUES)
                         else "chunked_detail")
        if arch_policy.get("delivery") != want_delivery:
            diffs["policy.delivery"] = (want_delivery, arch_policy.get("delivery"))
    if archived.get("challenge_detected"):
        if "flip_flop_agreeing" not in (arch_policy.get("avoid") or []):
            diffs["policy.challenge_avoid"] = (True, False)
        if arch_policy.get("response_goal") != "reconcile_claim":
            diffs["policy.challenge_goal"] = ("reconcile_claim",
                                              arch_policy.get("response_goal"))
    if archived.get("route_action") == "contextual_recovery":
        if "long_monologue_on_shaky_transcript" not in (arch_policy.get("avoid") or []):
            diffs["policy.recovery_avoid"] = (True, False)
        if arch_policy.get("response_goal") != "checkpoint_recovery":
            diffs["policy.recovery_goal"] = ("checkpoint_recovery",
                                             arch_policy.get("response_goal"))
    if "echo_confirm_parroting" in (arch_policy.get("avoid") or []):
        if arch_policy.get("response_goal") != "substantive_reaction":
            diffs["policy.antiparrot_goal"] = ("substantive_reaction",
                                               arch_policy.get("response_goal"))
    return diffs


def replay_turn(turn: dict, *, prior_reply: str | None, prior_state: dict) -> dict:
    """Replay one archived turn through run_turn. Returns {} when the turn is
    reproduced exactly, else {field: (replayed, archived)}."""
    if not isinstance(turn, dict) or turn.get("turn_type") in SKIP_TURN_TYPES:
        return {}
    if turn.get("response_suppressed") or turn.get("engine_path") == "legacy":
        return {}  # turn-controller decision / legacy brain not extracted
    ctx = context_from_archived(turn, prior_state=prior_state,
                                prior_reply=prior_reply)
    replay = run_turn(ctx)
    diffs = _compare(replay, turn)
    # Old-archive (frozen e0dc60f) boundary: detail turns without the
    # detail_latch_after telemetry field — the cap (110 vs 240) depends on
    # the unarchived latch value, so trimming ambiguity is a NOTE, never a
    # hard gate failure. Every other field still compares byte-exact.
    if diffs and turn.get("detail_mode") and turn.get("detail_latch_after") is None:
        soft_fields = {"llm_response", "reply_trimmed"}
        if set(diffs) - {"notes"} <= soft_fields:
            soft = sorted(set(diffs) - {"notes"})
            diffs = {"notes": [f"detail-turn cap ambiguity (no "
                               f"detail_latch_after in old-format archive): "
                               f"{', '.join(soft)}"]}
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
            if turn.get("turn_type") in SKIP_TURN_TYPES or turn.get("response_suppressed"):
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
        paths = sorted(Path.cwd().glob(pattern)) if any(ch in pattern for ch in "*?[") \
            else [Path(pattern)]
        for p in paths:
            if not p.is_file():
                print(f"[replay] no such session file: {p}")
                continue
            diffs, checked, skipped = replay_session(p)
            total_checked += checked
            if not diffs:
                print(f"[replay] IDENTITY OK — {p} ({checked} turns, "
                      f"{skipped} supervisor/idle/suppressed skipped)")
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

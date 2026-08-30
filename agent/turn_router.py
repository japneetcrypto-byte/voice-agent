"""Turn routing decision — extracted from agent/main.py transcribe_and_respond
(Phase 0, Slice 1, 2026-08-30).

The routing block was a silent-fall-through hazard (evidence: session t14 —
catastrophic transcript got a FULL LLM reply; fixed in fc361b8). This module
makes the DECISION a pure, testable function; main.py keeps only the async
side effects (awaiting the deterministic response).

Pure: no livekit, no I/O. Reuses agent.transcript_router.route_transcript.
"""
from __future__ import annotations

from agent.transcript_router import route_transcript


def route_decision(
    *,
    transcript_text: str,
    is_valid: bool,
    rejection_reason: str | None,
    avg_logprob: float | None,
    is_repetition: bool = False,
    is_catastrophic: bool = False,
    agent_was_speaking: bool = False,
    engine_bound: bool = False,
) -> dict:
    """Decide what this turn must do. Returns a dict:

      action:        "acoustic_only" | "clarify" | "contextual_recovery" | "normal"
      reason:        route_reason string
      drop:          True when the turn must produce NO reply at all
      drop_reason:   why (when drop)
      respond_now:   True when a deterministic response should run NOW
                     (acoustic_only / clarify) and then return
      turn_type:     "acoustic_only" | "unclear_speech" | None
      trigger:       response_trigger_reason when respond_now
      recovery:      True when contextual_recovery (continues to normal tail
                     with recovery flags)

    The fall-through fix (fc361b8) is IN this table: invalid + agent
    speaking -> drop, never a substantive LLM answer.
    """
    action, route_reason = route_transcript(
        transcript_text, is_valid, rejection_reason, avg_logprob,
        is_repetition=is_repetition,
        is_catastrophic=is_catastrophic)

    drop = False
    drop_reason = None
    respond_now = False
    turn_type = None
    trigger = None
    recovery = False

    if not is_valid:
        if action == "acoustic_only":
            if engine_bound and not agent_was_speaking:
                respond_now = True
                turn_type = "acoustic_only"
                trigger = "acoustic_only_presence"
            else:
                # Fall-through fix: invalid + agent speaking -> drop silently.
                drop = True
                drop_reason = "invalid_acoustic_only_while_agent_speaking"
        elif action == "clarify":
            if engine_bound and not agent_was_speaking:
                respond_now = True
                turn_type = "unclear_speech"
                trigger = "unclear_stt_clarify"
            else:
                drop = True
                drop_reason = "invalid_clarify_while_agent_speaking"
        elif action == "contextual_recovery":
            recovery = True

    return {
        "action": action,
        "reason": route_reason,
        "drop": drop,
        "drop_reason": drop_reason,
        "respond_now": respond_now,
        "turn_type": turn_type,
        "trigger": trigger,
        "recovery": recovery,
    }

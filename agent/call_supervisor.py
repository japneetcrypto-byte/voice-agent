"""Call Supervisor — the "senior on the floor" (owner brief 2026-08-29).

Owner's framing (verbatim intent): "a parallel system who generally doesn't
act, but gets activated on these cases — can know the state of the talk and
manage it, and in the meanwhile raise an alarm or a way to check what
happened, to an agent or something."

Design contract:
- DORMANT: lives outside the hot path; costs nothing while the conversation
  is healthy. It never decides WHAT Aiva says in a normal turn.
- DETERMINISTIC: engagement decisions are fixed rules over turn outcomes —
  no LLM. Deliberately so: the most likely failure it rescues from IS the
  LLM/quota path, so the lifeline cannot share that brain.
- SAFE: it only ever speaks short, pre-approved recovery lines and never
  twice in quick succession (dedupe + cooldown), and it stands down the
  moment the primary pipeline is audible again.
- TRANSPARENT: every engagement writes a SUPERVISOR_ENGAGED event with a
  state snapshot ("what happened there"), and repeat engagements escalate
  (SUPERVISOR_ESCALATE) — the hook a human paging system attaches to.

Pure logic only (no livekit/asyncio) so it is fully unit-testable; main.py
executes its decisions.
"""
from __future__ import annotations

import time

# Seconds to wait after a failed user turn before the supervisor checks
# whether Aiva is audible. Short enough that the user never feels ignored
# (the "said hello twice" case must resolve in <10s), long enough that a
# normal slow reply (speech->audio p95 ~4s) is never interrupted.
RESCUE_GRACE_S = 4.0

# Minimum gap between two engagements: one rescue per incident, never a
# supervisor loop talking over itself.
MIN_ENGAGE_GAP_S = 15.0

# Outcome reasons the supervisor treats as ITS business. Deliberate WAITs
# (turn-taking silence) are NOT its business — that is the controller doing
# its job; the WAIT_STREAK_CAP already bounds those.
ENGAGE_REASONS = {
    "skipped": "response task skipped (newer-task race) and never recovered",
    "pipeline_error": "response raised an exception; nothing was spoken",
    "unanswered": "valid user turn completed with no agent audio",
    "reachout_unanswered": "user is CALLING Aiva (greeting/hello) and got silence",
}


class CallSupervisor:
    def __init__(self):
        self.engagements = 0
        self.last_engaged_turn = None
        self.last_engaged_mono = None
        self.escalated = False

    def evaluate(self, outcome: dict, now: float | None = None) -> dict | None:
        """outcome: {"reason": <in ENGAGE_REASONS>, "turn": int, "user_text": str}
        Returns an engagement decision dict, or None (stand down)."""
        now = time.monotonic() if now is None else now
        reason = (outcome or {}).get("reason")
        if reason not in ENGAGE_REASONS:
            return None
        # one rescue per user turn
        if outcome.get("turn") is not None and outcome.get("turn") == self.last_engaged_turn:
            return None
        # cooldown: never engage twice within MIN_ENGAGE_GAP_S
        if self.last_engaged_mono is not None and (now - self.last_engaged_mono) < MIN_ENGAGE_GAP_S:
            return None

        self.engagements += 1
        self.last_engaged_turn = outcome.get("turn")
        self.last_engaged_mono = now
        escalate = self.engagements >= 2 and not self.escalated
        if escalate:
            self.escalated = True
        return {
            "reason": reason,
            "reason_detail": ENGAGE_REASONS[reason],
            "engagement_no": self.engagements,
            "escalate": escalate,
            "user_text": (outcome or {}).get("user_text", "")[:80],
        }

    def stand_down(self):
        """Primary pipeline is audible again — nothing to do."""
        self.last_engaged_turn = None


def build_snapshot(reason: str, outcome: dict, engine_state: dict) -> dict:
    """The 'what happened there' payload for the alarm/report."""
    return {
        "reason": reason,
        "reason_detail": ENGAGE_REASONS.get(reason, reason),
        "turn": outcome.get("turn"),
        "user_text": (outcome or {}).get("user_text", "")[:120],
        "engine_bound": engine_state.get("engine_bound"),
        "last_engine_path": engine_state.get("last_engine_path"),
        "last_tts_provider": engine_state.get("last_tts_provider"),
        "last_llm_ttft_s": engine_state.get("last_llm_ttft_s"),
        "wait_streak": engine_state.get("wait_streak"),
        "ts": time.time(),
    }

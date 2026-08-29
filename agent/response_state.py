"""Response playback state — Generated ≠ Spoken ≠ Heard (directive 2026-08-29).

Fix 2 of the conversation-quality brief: an interrupted response must not
silently vanish from conversation state. Every response gets an explicit
state, and the NEXT LLM call receives a reconciliation payload so it knows:

  FULLY_PLAYED     -> user heard everything (no reconciliation needed)
  PARTIALLY_PLAYED -> user heard only the quoted portion; continue seamlessly,
                      never replay the heard part
  UNHEARD          -> reply never reached the user; respond fresh to their new
                      words; the unheard text must not be referenced

Pure functions; consumed by agent/main.py and agent/fused_turn.py.
"""
from __future__ import annotations

FULLY_PLAYED = "FULLY_PLAYED"
PARTIALLY_PLAYED = "PARTIALLY_PLAYED"
UNHEARD = "UNHEARD"


def classify(interrupted: bool, ttfa_logged: bool, played_chars: int) -> str:
    """Map a finished/cancelled response to its playback state."""
    if not interrupted:
        return FULLY_PLAYED
    if not ttfa_logged or played_chars == 0:
        return UNHEARD
    return PARTIALLY_PLAYED


def reconcile_payload(last_response: dict | None) -> dict | None:
    """Build the 'previous_response' context for the NEXT fused call.

    Returns None when there is nothing to reconcile (no previous response, or
    it was fully played — the user heard everything, so no special handling).
    Applies only to the immediately-following turn; the caller pops it."""
    if not last_response:
        return None
    status = last_response.get("status")
    if status == FULLY_PLAYED:
        return None
    payload = {"status": status, "turn": last_response.get("turn")}
    if status == PARTIALLY_PLAYED:
        payload["heard_text"] = (last_response.get("heard_text") or "")[:160]
    return payload

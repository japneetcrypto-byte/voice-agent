"""Response Contract — the deterministic boundary the LLM operates inside.

Owner directive 2026-08-29: "Code defines the boundaries. LLM chooses the
best path inside them."

Per turn, a compact contract (~40–80 tokens) is injected into the LLM's
context. It tells the model:
  GOAL    — what this response should accomplish
  TOPIC   — current active topic
  MODE    — casual / detail / continue / clarify / recovery
  MUST_NOT — 3–5 deterministic prohibitions
  RESPONSE_STATE — if previous reply was interrupted

The contract is built by PURE CODE from existing state — no LLM calls.
The model generates freely inside this boundary.

Hard Violation Gate: post-LLM, pre-TTS. Narrow — only objectively
detectable violations. Soft quality issues (verbosity, tone, coherence)
are measured asynchronously, never on the critical path.
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
# MUST_NOT constraint derivation — deterministic, state-driven
# ---------------------------------------------------------------------------

def derive_constraints(
    *,
    policy: dict | None = None,
    active_topic: str | None = None,
    last_reply: str | None = None,
    last_claim: str | None = None,
    detail_mode: bool = False,
    is_recovery: bool = False,
    memory_count: int = 0,
) -> list[str]:
    """Return MUST_NOT prohibitions based on current state.
    Deterministic: same state → same constraints."""
    constraints = [
        "reference topics/entities not in this conversation",
        "expose internal system/memory/policy details",
    ]

    if last_claim:
        constraints.append(f"contradict your previous statement: '{last_claim[:80]}'")

    if last_reply:
        constraints.append("repeat your previous reply verbatim or nearly verbatim")

    if is_recovery:
        constraints.append("provide a long or detailed response — your transcript is uncertain")

    if memory_count > 0:
        constraints.append(
            "proactively reference old-session memory — only use if the user "
            "brings up that topic or explicitly asks")

    return constraints[:5]  # max 5 — keep the contract compact


# ---------------------------------------------------------------------------
# Response Contract builder
# ---------------------------------------------------------------------------

def build_contract(
    *,
    policy: dict | None = None,
    active_topic: str | None = None,
    last_reply: str | None = None,
    last_claim: str | None = None,
    detail_mode: bool = False,
    is_recovery: bool = False,
    interrupted_state: str | None = None,
    memory_count: int = 0,
    route_action: str | None = None,
) -> dict:
    """Build the compact Response Contract for this turn.
    Returns a dict for the LLM's context (~40-80 tokens serialized)."""
    goal = (policy or {}).get("response_goal", "respond")
    mode = "casual"
    if detail_mode:
        mode = "detail"
    elif (policy or {}).get("delivery") == "continue_detail":
        mode = "continue"
    elif route_action == "contextual_recovery":
        mode = "recovery"
    elif (policy or {}).get("response_goal") == "reconcile_claim":
        mode = "reconcile"

    is_rec = (route_action == 'contextual_recovery')
    must_not = derive_constraints(
        policy=policy, active_topic=active_topic, last_reply=last_reply,
        last_claim=last_claim, detail_mode=detail_mode,
        is_recovery=is_rec, memory_count=memory_count)

    contract = {
        "GOAL": goal,
        "TOPIC": active_topic or "follow the user's lead",
        "MODE": mode,
        "MUST_NOT": must_not,
    }
    if interrupted_state and interrupted_state != "FULLY_PLAYED":
        contract["RESPONSE_STATE"] = interrupted_state

    return contract


# ---------------------------------------------------------------------------
# Hard Violation Gate — narrow, deterministic, block-only
# ---------------------------------------------------------------------------

_GATE_PATTERNS = {
    "memory_proactive": [
        (r"\b(?:remember when|last time you|you told me before|from your past)\b",
         "proactive memory reference"),
    ],
    "system_exposure": [
        (r"\b(?:my (?:system )?prompt|my (?:code|programming|instructions)"
         r"|I(?:'m| am) (?:an? )?(?:AI|language model|bot))\b",
         "system/AI self-reference"),
        (r"\b(?:perception|policy_constraints|response_contract|memory_gate)\b",
         "internal system term"),
    ],
    "action_fabrication": [
        (r"\b(?:I(?:'ve| have)? (?:already )?(?:sent|ordered|booked|called"
         r"|emailed|done)\s+(?:the|it|that))\b",
         "claimed action without tool result"),
    ],
}


def check_violations(reply: str) -> list[dict]:
    """Check a reply against HARD violations only (objectively detectable).
    Returns violations as {"type", "detail", "matched", "action"}.
    action: "block" (gate) or "flag" (measure async)."""
    import re

    violations = []
    reply_l = (reply or "").lower()

    for category, patterns in _GATE_PATTERNS.items():
        for pattern, label in patterns:
            m = re.search(pattern, reply, re.IGNORECASE)
            if m:
                violations.append({
                    "type": category,
                    "detail": label,
                    "matched": m.group(0)[:40],
                    "action": ("block" if category in ("system_exposure",
                               "action_fabrication") else "flag"),
                })
    return violations


def gate_reply(reply: str) -> tuple[str, list[dict]]:
    """Apply the hard violation gate. Returns (reply, violations).
    Currently: flag-only (observability before blocking)."""
    return reply, check_violations(reply)

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

import re


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
    # Priority-ordered (locked task 2026-08-30): the cap (5) must never
    # drop the most critical prohibitions, so objectively-dangerous rules
    # come first, then state-derived no-contradict/no-repeat, then topic
    # discipline, then softer conditionals.
    constraints = [
        "fabricate completing real-world actions (sending, booking, ordering, calling)",
        "expose internal system/memory/policy details",
    ]

    if last_claim:
        constraints.append(f"contradict your previous statement: '{last_claim[:80]}'")

    if last_reply:
        constraints.append("repeat your previous reply verbatim or nearly verbatim")

    constraints.append("leave the active topic unless the user changes it")
    constraints.append("reference topics/entities not in this conversation")

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
    # Relevant context / previous claim line (locked task item 1). Only when
    # it exists — keeps the contract surgically compact.
    if last_claim:
        contract["CONTEXT"] = f"your previous claim: {last_claim[:80]}"
    elif last_reply:
        contract["CONTEXT"] = f"you just said: {last_reply[:80]}"
    if interrupted_state and interrupted_state != "FULLY_PLAYED":
        contract["RESPONSE_STATE"] = interrupted_state

    return contract


# ---------------------------------------------------------------------------
# Hard Violation Gate — narrow, deterministic, block-only
# ---------------------------------------------------------------------------

# Dev-context lexicon (owner's actual usage, 2026-08-30): the owner talks TO
# Aiva ABOUT building voice agents. When the conversation itself is on the
# topic of AI/voice-agent building, mentioning "my system prompt" / "my
# code" is ON-TOPIC discussion, not exposure. Deterministic regex — no LLM.
_AI_BUILD_LEXICON = re.compile(
    r"voice ?agent|voice ?assistant|\baiva\b|\bllm\b|language model|\bai\b"
    r"|artificial intelligence|gemini|groq|whisper|\bstt\b|\btts\b|fish ?audio"
    r"|prompt|system prompt|instructions|backend|\bapi\b|code|developer"
    r"|\bmodel\b|token|fine[- ]?tun|\bbot\b|chatbot|\bapp\b|बॉट|ऐप|मॉडल"
    r"|प्रॉम्प्ट|कोड|एपीआई|डेवलपर|वॉइस",
    re.IGNORECASE)


def is_ai_build_topic(text: str | None) -> bool:
    """True when the user's current turn is about building AI/voice systems.

    Deterministic — the gate consults it so on-topic developer talk is not
    mistaken for system exposure (owner session 2026-08-30: every reply
    blocked -> 'main sun raha hoon' constantly -> bad experience).
    """
    return bool(text) and bool(_AI_BUILD_LEXICON.search(text or ""))


# Patterns per category. Each entry: (regex, label, dev_context_soft?).
# dev_context_soft=True => downgraded to FLAG (not block) when the user's
# own turn is about building AI/voice systems (on-topic discussion).
_GATE_PATTERNS = {
    "memory_proactive": [
        (r"\b(?:remember when|last time you|you told me before|from your past)\b",
         "proactive memory reference", False),
    ],
    "system_exposure": [
        # DEV-CONTEXT SOFT: 'my system prompt' / 'my code' — the owner
        # discussing his own voice agent. Blocked when OFF-topic; flagged
        # (spoken) when the user is clearly building/talking AI.
        (r"\bmy (?:system )?prompt\b|\bmy (?:code|programming|instructions)\b",
         "system prompt/code self-reference", True),
        # HARD always: claiming to BE an AI/model/bot is trust-loss in a
        # companion product regardless of topic.
        (r"\bI(?:'m| am) (?:an? )?(?:AI|language model|bot)\b",
         "AI self-reference", False),
        # HARD always: leaking internal system terms.
        (r"\b(?:perception|policy_constraints|response_contract|memory_gate)\b",
         "internal system term", False),
    ],
    "action_fabrication": [
        (r"\b(?:I(?:'ve| have)? (?:already )?(?:sent|ordered|booked|called"
         r"|emailed|done)\s+(?:the|it|that))\b",
         "claimed action without tool result", False),
    ],
}

# Block filler: spoken when a reply piece IS hard-blocked. Rotated by turn
# number (pick_line discipline — deterministic). Kept SHORT and natural; the
# old single line "main sun raha hoon, bol." on EVERY block was the user's
# #1 complaint ("bad experience, not natural").
GATE_BLOCK_LINES = [
    "haan, bolo na.",
    "achha, samajh gaya — aage bolo.",
    "haan, main yahin hoon.",
]


def check_violations(reply: str, on_topic_ai: bool = False) -> list[dict]:
    """Check a reply against HARD violations only (objectively detectable).
    Returns violations as {"type", "detail", "matched", "action"}.
    action: "block" (gate) or "flag" (measure async).

    on_topic_ai (deterministic, from the user's own turn): downgrades the
    dev-context-soft pattern (my system prompt/code) to FLAG — the owner
    discussing his voice agent is on-topic, not exposure.
    """
    violations = []
    for category, patterns in _GATE_PATTERNS.items():
        for pattern, label, dev_soft in patterns:
            m = re.search(pattern, reply, re.IGNORECASE)
            if m:
                block = (category in ("system_exposure", "action_fabrication"))
                if dev_soft and on_topic_ai:
                    block = False  # on-topic developer discussion, not exposure
                violations.append({
                    "type": category,
                    "detail": label,
                    "matched": m.group(0)[:40],
                    "action": "block" if block else "flag",
                })
    return violations


def gate_reply(reply: str, on_topic_ai: bool = False,
               turn_no: int | None = None) -> tuple[str, list[dict]]:
    """Apply the hard violation gate (OWNER DIRECTIVE: hard-block from day one).

    Hard blocks: AI self-reference, internal-system-term leakage, action
    fabrication, and OFF-topic system-prompt/code self-reference.
    Downgraded to flag (spoken, measured): memory_proactive, and
    system-prompt/code self-reference when the user's turn is about building
    AI/voice systems (on_topic_ai).

    Blocked pieces are replaced with a short natural line (rotated), so the
    user never hears the same robotic filler every time.
    Returns (gated_reply, violations)."""
    violations = check_violations(reply, on_topic_ai=on_topic_ai)
    gated = reply
    blocking = [v for v in violations if v["action"] == "block"]
    if blocking:
        # Replace the offending piece with a natural continuation
        gated = GATE_BLOCK_LINES[(turn_no or 0) % len(GATE_BLOCK_LINES)]
        print(f"[ContractGate] BLOCKED {len(blocking)} violation(s): "
              + ", ".join(v["detail"] for v in blocking))
    return gated, violations

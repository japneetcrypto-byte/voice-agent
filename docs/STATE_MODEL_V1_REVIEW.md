# Stress-Test Review — Emotional Conversation State Model v1 (pre-lock)

**Date:** 2026-08-26 · **Reviews:** `docs/STATE_MODEL_V1.md` against `docs/ARCHITECTURE_SNAPSHOT.md` + locked boundary.
**Verdict:** **LOCKABLE AFTER AMENDMENTS A1–A10.** Core skeleton survives; 1 contradiction, 3 schema cuts, 1 overclaimed capability, 5 missing scenarios, 3 owner decisions.

Labels per protocol: [FACT] code/evidence-supported · [FINDING] analysis conclusion · [GAP] missing capability · [ASSUMPTION] unverified · [RECOMMENDATION] proposed change (scope-checked).

---

## 1. Technically sound — keep unchanged

- [FACT] Update-trigger map keys onto existing code events: SPEECH_ENDED handler, energy-gate pass (RMS/peak/duration computed there), STT accept/reject (`finally: log_turn`), CancelledError interruption path.
- [FINDING] Evidence→Estimate→Confidence as bookkeeping discipline is implementable today (limits in §5).
- [FINDING] `user_interpretation` vs `emotion_estimate` split cleanly implements boundary §6.
- [FINDING] Deterministic updater practical: pure function, testable without voice stack; response serialization already enforced `[main.py:162]`.
- [FINDING] Memory write-candidate → session-end commit matches §8; auditable. Keep.
- [FINDING] Safety precedence (Safety > explicit request > mode) + two-tier uncertain/explicit response correct per §9.

## 2. Contradictions with existing code

- [FINDING] **Memory requires identity that doesn't exist.** Random room per click `[App.tsx:19]`, participant defaults to `"user"` `[token_server.py:32]`, no user_id, two non-communicating workers. Scenarios 14/15 unimplementable as designed until keying contract exists. → **A1** (Phase 3: anonymous device-scoped ID).
- [GAP] **`session_end` trigger doesn't exist** in `main.py` (no shutdown/room-close handler). Memory commit-at-session-end needs a lifecycle hook. [ASSUMPTION] `ctx.add_shutdown_callback` exists in pinned SDK — verify Phase 3.
- [FINDING] Trigger map implies serial perception stage; no such stage exists today (STT→response is direct). → **A2** (position TBD: fused vs serial vs lagged).
- [FACT] No contradiction on rejected turns — `log_turn` in `finally` produces records for echo/rejected turns.

## 3. Unnecessary complexity — cut from v1

- [RECOMMENDATION] **A3** cut `ConversationState.user_energy_trend` + `winding_down_signals` — duplicates Emotion `trajectory`. Conversation State = measurements only. Add rule: `phase` derived from Mode+trajectory+duration, never independently estimated.
- [RECOMMENDATION] **A4** cut Thread `importance` float — no source computes it; promotion keys off `open_loops`+recurrence.
- [RECOMMENDATION] **A5** cut Emotion `secondary` — undefined taxonomy surface, no v1 consumer.
- [RECOMMENDATION] **A6** cut Turn `pauses` — always-null field today.
- [RECOMMENDATION] **A7** remove `abuse_victim` from scaffold — boundary §9 minimum only; `other_flagged` captures; categories come from safety investigation.
- [RECOMMENDATION] **A8** cut Policy `language_style` — static persona rule, not per-turn decision.

## 4. Missing scenarios

- [GAP] **Non-speech distress** (crying/sighing/silence-while-upset): STT yields nothing; no state representation; close to the product's core case. → **A9**: acoustic-only turn type → gentle acknowledgment, never "I didn't catch that."
- [GAP] **User disengagement** (long silence, one-word answers mid-VENT): no idle path; policy loops questions. → **A9** idle/degraded-turn rule (reuses existing NO_SPEECH machinery).
- [GAP] **Third-party risk disclosure** ("my friend wants to die"): user-centric taxonomy misses it → add to safety investigation scope.
- [GAP] **Preference supersession**: conflicting preferences over time need a withdraw/supersede rule in Memory.
- [ASSUMPTION] Single speaker, single active session per user — rooms process any audio track; multi-party unmodeled. State explicitly in v1.
- [FINDING] STT-garbled entity names can poison relationship memory keys — eval must include noisy-ASR cases.

## 5. Evidence→Estimate→Confidence vs current signals

- [FACT] Available channels: transcript (+language, no_speech_prob, avg_logprob from segments[0] only), RMS/peak/duration + derived words/sec, history, corrections.
- [FINDING] **Acoustic channel much weaker than v1 implied**: `autoGainControl: true` `[App.tsx:48]` normalizes levels → RMS/peak not comparable across mics/sessions; no pitch/prosody/pauses. v1 emotion evidence is **transcript-dominant**; acoustic = low-weight, within-session-relative. Scenario 8 honestly **not servable in v1** (confidence-cap prevents wrongness, not absence). → **A10** (acoustic weights ≤0.2, scenario 8 reclassified "partially supported — Phase 4 decision").

## 6. Dimension boundaries

- [FINDING] With A3 applied, clean split: Turn=record · Emotion=estimates · Thread=topics · Conversation=measurements · Memory=durable facts · Safety=overrides · Mode=contract · Policy=decisions.
- [FINDING] Pacing: Conversation counts, Policy limits — measurement/decision split holds; make explicit.

## 7. Deterministic updater practicality

- [FINDING] Practical. Inputs exist except `session_end` (§2). Two real additions: session-end hook [ASSUMPTION: verify SDK], perception call. Concurrency safe (asyncio loop + latest-turn-wins + existing response guard).

## 8. Latency/cost risks

- [FINDING] **Serial perception = biggest UX risk**: +1 structured-JSON generation (est. 200–600 tokens) before response start, on top of batch-STT → LLM TTFT → TTS TTFA. Plausibly +0.5–1.5 s/turn.
- [RECOMMENDATION] Phase 3 starting hypothesis: **fuse safety screening into the main call** (structured head + prose) or one-turn-lag emotion/thread; safety itself cannot lag → fusion favored. Phase 3 decision, flagged now.
- [FACT] EdgeTTS fallback buffers full text (slower already); perception compounds fallback turns.
- [FINDING] Cost ~2× calls/turn on flash-lite-class models: minor. No vector DB in v1: no embedding cost. Session-end commit ≈1 call/session: negligible.

## 9. Safety gaps pre-lock

- [FACT][GAP] Taxonomy `provisional-v0`; investigation mandatory pre-lock (§9). Must include: third-party harm, minors (no age gate exists anywhere), India-jurisdiction resources.
- [GAP] **Resource delivery channel**: escalation can only speak today; frontend card collides with Phase-7 ordering — owner must rule (voice-only vs card vs ordering exception).
- [GAP] **Privacy posture**: `logs/session_*.log` stores verbatim transcripts locally incl. risky content `[main.py]`; retention/encryption policy needed before real users.
- [GAP] Safety eval data sourcing/ethics (consented labeled set) unresolved — blocks Phase 4 for safety.

## 10. Assumptions lacking evidence

1. [ASSUMPTION] Gemini flash-lite reliably emits valid perception JSON incl. Hinglish sarcasm — untested; Phase 3 prototype required.
2. [FINDING] Acoustic scalars carry emotional signal in-product — weak; AGC distortion; relative-only.
3. [ASSUMPTION] SDK session-end hook exists — verify.
4. [ASSUMPTION] `clear_queue` exists for interruption flush (carried from Snapshot) — verify same pass.
5. [ASSUMPTION] Users want cross-session continuity — needs "start fresh" escape hatch in memory design.
6. [ASSUMPTION] 8-label emotion taxonomy covers venting domain — eval data will validate.
7. [FINDING] 5 s TTS fallback still adequate once perception adds latency — interacting risk; re-test Phase 3.

## Amendments A1–A10 (apply to STATE_MODEL_V1.md before lock)

| # | Amendment | Scope |
|---|---|---|
| A1 | User/session keying = mandatory Phase 3 contract | IN |
| A2 | Perception position TBD (fused/serial/lagged) | IN |
| A3 | Cut energy_trend/winding_down_signals; phase derived | IN (simplify) |
| A4 | Cut Thread importance float | IN (simplify) |
| A5 | Cut Emotion secondary | IN (simplify) |
| A6 | Cut pauses field | IN (simplify) |
| A7 | Scaffold = boundary-minimum; abuse_victim → investigation | IN |
| A8 | Cut policy language_style | IN (simplify) |
| A9 | Add non-speech distress + disengagement scenarios/rules | IN (core) |
| A10 | Acoustic low-weight relative-only; scenario 8 reclassified | IN |

No amendment expands scope. All simplifications or honesty corrections.

## Owner decisions required at lock

1. Safety resource-delivery channel (voice-only vs frontend card vs Phase-7 exception).
2. Approve A1 direction (anonymous device-scoped ID) for Phase 3.
3. Confirm fused-perception as Phase 3 starting hypothesis.

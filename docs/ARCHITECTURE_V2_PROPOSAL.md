# Aiva Unified Architecture — Proposal (PENDING OWNER REVIEW — no code yet)

**Date:** 2026-08-30 · **Status:** proposal for review/approval. Not implemented.
**Owner directive:** "LLM = understand + reason + respond creatively. System =
provide context/state + enforce boundaries + protect memory/actions + measure
quality. Do NOT prescribe what the LLM should say except where necessary.
Define what it must not do."

---

## 0. Why this step-back (evidence)

- Persona V1.14 ≈ 2,400 chars of accumulated incident rules (BAD/GOOD
  examples, per-incident style rules) — the exact "every incident added a
  rule" failure mode the project's own red-team review (F5) warned about.
- State engine prescribes tactic-level goals per mode (`VENT →
  encourage_continuation`) — the owner's session showed `mode=VENT
  goal=encourage_continuation` on ALL 16 turns → the generic "kya chal raha
  hai?" pattern.
- Two incidents in one day were fixed with topic/incident-specific rules
  (voice-agent lexicon) before being caught — the architecture must make
  that structurally impossible.

## 1. The architecture

```
User speech
 → [STT / validate / route]              ← input trust (unchanged)
 → [turn controller / endpointing]       ← timing (unchanged)
 → CONTEXT BUILDER
     L1 recent turns | L2 compressed state | L3 gated memory | L4 claims/facts
 → STATE & POLICY
     7-dim state → GOAL (coarse) + TOPIC + FACTS + MEMORY + MUST_NOT  ← compact contract
 → LLM (fused): perception head (understand+reason) + FREE response
 → ENFORCEMENT (deterministic, post-LLM, pre-TTS)
     HARD-BLOCK (objective) → BOUND (repeat/script/length) → PASS
 → TTS → playback
 → MEASUREMENT (async, offline)
     quality metrics → tune thresholds / next-turn MUST_NOT / persona (owner-reviewed)
```

**Per-turn contract — 5 lines:**
```
GOAL:    respond | acknowledge | clarify | recover | detail | idle   (coarse, state-derived)
TOPIC:   <active topic from L2>
FACTS:   <top-3-5 relevant claims/facts from registry, only if relevant>
MEMORY:  <only the subset the user invoked — usually none>
MUST_NOT: fabrication; contradiction of FACTS; repeat of last reply;
          proactive memory; off-topic; (recovery → be brief/checkpoint)
```

## 2. Five buckets — what belongs where

| Bucket | Belongs here | Current code | Delta |
|---|---|---|---|
| Context | raw history, compressed state, gated memory, claims/facts registry | layered_context, memory_store, memory_gate | NEW claims/facts registry |
| State | 7-dim model, transitions, coarse GOAL/MODE | state_updater, session_state | drop tactic-level goals |
| Policy/Constraints | contract builder, routing, turn-taking, ack semantics | response_contract (builder), transcript_router, turn_controller | contract pulls from registry; remove incident additions |
| LLM reasoning | perception head + free prose + A-P1 chunk plans + (future) tool requests | fused_turn | persona V2 (boundary-only) |
| Deterministic enforcement | hard gate; bounded transforms; action ledger (future) | gate_reply, repeat_break_for, reply_guard transforms | NEW action ledger interface (design-only) |

## 3. Trade-offs

| Decision | Gain | Cost/Risk | Mitigation |
|---|---|---|---|
| Boundary-only prompt | generalization; no topic traps; no dilution | less explicit style control | measurement + bounded enforcement + dynamic MUST_NOT (pattern-stuck class) |
| Coarse GOAL (drop tactics) | kills generic-reply class | less long-silence guidance | MODE/TOPIC carry detail/recovery; reply-variety measured |
| Claims/facts registry | exact no-contradiction; memory scoping queryable | extraction imperfect; stale claims | within-session, cap N, expiry, user-contradiction invalidates |
| Fused single call (keep) | latency; validated | head instability | parse salvage + measurement |
| Never regenerate on critical path | latency; no loops | some errors slip | supervisor + bounded recovery + async loop |
| Async quality loop | no latency cost; self-improvement | feedback drift | only pattern-stuck mutates constraints; offline tuning owner-reviewed |

## 4. Failure modes

**Prevents:** incident-rule bloat & topic-overfit (structurally); identity
deception; internal-term leak; fabricated actions; contradiction of stated
facts; verbatim repetition; proactive memory leak; junk-STT substantive
answers; silence-on-failure; (future) claimed actions without tool-execution.

**Does NOT prevent (honest):** paraphrased fabrication (until action ledger
exists); creative/quality failures (measured, not blocked); garbled-STT
confident nonsense (detection only); claims-registry extraction errors
(bounded); state misclassification (tuning issue, measured); provider
incidents (out of scope).

## 5. What remains unchanged

Fused single-call · deterministic state engine (LLM interprets / code
applies) · device-scoped memory + gate · STT pipeline/validation/routing ·
turn controller + endpointing · TTS (Fish + Edge last-resort + pre-warm) +
Fish semantic acks · barge-in reorder + fall-through fix · supervisor /
self-healing / telemetry · hard-gate set (already topic-independent).

## 6. Smallest implementation plan (only after approval)

1. Claims/facts registry (new module; reuse entity_extractor; within-session,
   cap/expiry; contract FACTS from it) — offline-testable
2. Policy consolidation: coarse GOAL; contract = GOAL/TOPIC/FACTS/MEMORY/
   MUST_NOT; remove incident-specific additions — offline-testable
3. Prompt consolidation: persona V2 (identity + delivery + contract pointer;
   keep A-P1 + perception spec) — live A/B-gated
4. Enforcement consolidation: single block/bound/pass decision table;
   remove duplicate ad-hoc flags — offline-testable
5. Async quality loop: metrics → pattern-stuck dynamic MUST_NOT + offline
   tuning queue; never regenerate — offline-testable
6. Tool-use interface (design-only): structured tool_requests in head +
   ActionLedger; gate blocks action-claims without ledger entry; injected
   ledger fixtures for battle-test
7. Battle-test: offline replay of current sessions through new
   context/contract/enforcement (determinism) + topic-switch / contradiction
   / memory-scoping / tool-use fixtures + existing A/B report

## 7. Review checklist (owner)

1. Is the coarse GOAL vocabulary (6 intents) enough, or do you want to keep
   any mode-specific goal?
2. Claims registry: within-session only, cap 5, expiry — or persist across
   sessions like memory?
3. Tool-use: which tools first (API/RPA/CRM/actions)? The interface is
   designed now, implemented with the first tool.
4. Persona V2: how much identity/delivery is "necessary" vs prescription?
5. Quality loop: which metrics are allowed to become dynamic MUST_NOT?

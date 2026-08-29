# Aiva Guardrails — containment architecture (2026-08-29)

**Trigger:** owner verdict — "beating around the bush; blast radius not
contained; will fail in extreme situations again." This document defines the
guardrail stack and the containment guarantee for each failure class.

## The meta-issue (why bugs kept reaching the user)

Three structural weaknesses produced the incident pattern:

1. **Unconstrained write paths.** Extraction/policy bugs wrote directly into
   the owner's live memory ('गए — user's bhai') because validation lived at
   call sites; the store accepted everything.
2. **Instruction-based quality control.** Persona rules are requests, not
   enforcement. Free-tier models drift regardless of instruction quality
   (parroting, flip-flop, script, merges).
3. **Incident-driven special-casing.** Blocklists/lexicons grow reactively and
   guarantee nothing for unseen inputs.

## The guardrail stack (four layers)

| Layer | Mechanism | Contains |
|---|---|---|
| **L1 — Enforcement in code** | output contract enforced deterministically: tag sanitizer (class-level), junk scrubber, merge lexicon, length trim, script transliteration, late-echo guard, samples-based TTS failover | model format/behavior variance |
| **L2 — Store-level memory gate** | `agent/memory_gate.py` via `MemoryStore.commit`: every write gated (reject / quarantine / pending / commit); pending promoted only on ≥2 sightings; session-end blanket promotion removed | ANY upstream memory bug (extractor, policy, caller) — worst case = quarantined row, never live pollution |
| **L3 — Containment** | call supervisor (rescue lines, snapshots, escalation), D1–D9 degradation paths, routing contract, WAIT_STREAK_CAP | silence/stall failures |
| **L4 — Observability** | per-turn telemetry, WORKER_BUILD stamp, self-diagnose, stage-verdict, aiva_health, trend table | recurrence (every incident is counted, attributed, and regression-tested) |

## The containment guarantee (memory)

> **No single upstream bug can put garbage into committed memory in one
> sighting.** Worst case: a quarantined row (invisible) or a 1x pending row
> (invisible, ages out). Live pollution requires a fact to be seen ≥2 times —
> repeated garbage is treated as real by design.

Enforced by: `agent/memory_gate.py` (store-level, all call sites), guarded
`promote_pending` (≥2 occurrences), and `test_memory_gate.py` (adversarial:
max-privilege garbage commits → zero leaks; determinism check).

## Enforcement-in-code list (instructions → guarantees)

| Persona rule | Code enforcement |
|---|---|
| Roman script only | Devanagari → roman transliteration in TTS tee (SCRIPT_TRANSLITERATED) |
| No tags/markdown | TAG_LEAK strip (class-level) + salvage |
| No merged/split words | merge lexicon (exact-match) |
| Brevity | sentence-boundary trim at cap (240c) |
| Masculine persona | GENDER violation telemetry |
| No parroting | CONFIRM-ECHO flag + streak nudge |
| No flip-flop | CHALLENGE_DETECTED + reconcile-claim nudge |
| Never ignore the user | supervisor + WAIT_STREAK_CAP + skip alarms |

## What remains OUTSIDE code guardrails (honest boundary)

- **Model variance**: understanding quality, sycophancy-under-challenge,
  tokenizer merge artifacts, head-tag format — contained by detection +
  scrubbing + nudges, but the root fix is model tier (A/B pending owner).
- **Free-tier provider stability** (Fish/Groq/Gemini episodic degradation) —
  contained by failover + supervisor; reliability requires paid tiers.
- **Unseen linguistic patterns** — lexicon/blocklists are reactive by nature;
  the gate ensures their failure mode is invisible-row, not user-facing harm.

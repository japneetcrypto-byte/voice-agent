# PROPOSAL — C1 Amendment A-P1: Head-carried chunk plan (pending owner sign-off)

Owner's instinct: "LLM first identifies intent, then drafts the response."
This can be done with ZERO extra LLM calls: the perception head — which the
fused model already emits every turn — becomes the planning stage.

## Current head (locked C1, synthetic-validated)

    <perception>{"m":"C","c":0.9,"s":"SAFE"}</perception>prose...

## Proposed extension (DETAIL MODE ONLY)

    <perception>{"m":"C","c":0.9,"s":"SAFE",
                 "plan":{"total":4,"current":1,"topic":"voice agent infra"}}</perception>
    [chunk 1 prose — one coherent thought, checkpoint ending]

On 'haan/aage/phir', the NEXT call receives the prior head in context and
emits current:2. The model PLANS the whole answer cheaply, then narrates one
chunk per turn. No amputation (code no longer cuts mid-explanation — the
model self-limits), no extra call, natural checkpoints.

## Why this beats the current trim cap

The trim cap is reactive amputation: session 200615 t3 wrote 167c, the cap
kept the first 16c ('haan, yaad hai. ') — a uselessly short fragment of a
correct answer. Thin-fill (shipped) mitigates but cannot recover the
model's own checkpoint. Head-plan makes the MODEL the chunker.

## Risks (honest)

- flash-lite must emit the plan reliably — tag robustness is now class-level
  (5 variants handled), but plan-field compliance is unproven. Mitigation:
  missing plan -> current behavior (cap as fallback).
- C1 contract change requires revalidation (parse-rate check on ~30 turns).
- Slightly longer heads (a few tokens) — negligible latency.

## Interim shipped (no sign-off needed): thin-output fill

Turn-3 amputation class fixed deterministically: when the model overshoots
and the kept-so-far portion is thin, the trim FILLS the budget with a
word-boundary cut instead of dropping the sentence. Verified: 167c ->
106c spoken (was 16c).

## Sign-off requested

[ ] Approve A-P1 (implement + revalidate head parse rate)
[ ] Reject (keep trim-cap approach; accept its ceiling)

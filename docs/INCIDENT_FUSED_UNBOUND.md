# INCIDENT 2026-08-29: The fused brain never ran — sessions were driven by the legacy fallback

**Status:** FIXED (commit on `arena/01a03e6f-voice-agent`). Owner-facing summary + PM postmortem.

## What the owner saw (session_20260829_083519)

- "Does not remember facts from yesterday" — e.g. Neetu behen
- "Could not recognise Neetu behen" even when told explicitly in-session
- Canned reply `Mujhe is baare mein pata nahi, kuch aur poochh sakte ho?` repeated verbatim 6×
- Assistant-speak ("aaj kya help chahiye?"), formal register, verbose replies
- Feminine self-reference ("main aapki kya help kar **sakti** hoon?")
- Diagnostic: `heads=0/23`, `ctx_captured=0/23`, `errors=0`

## Root cause (one line)

`engine["sess"]` was **never assigned anywhere in main.py** — the participant-binding
block imported `SessionState`, printed the owner, and returned. With `sess=None`,
`run_agent_response`'s condition `if engine and engine.get("sess"):` was always False,
so **every reply of every session came from `llm_provider.generate_response_stream()`
with `session.py`'s legacy prompt** — a pre-Phase-5 "voice assistant" prompt whose
rule #4 is literally the canned ignorance line the model repeated.

Everything observed maps to that one bug:

| Symptom | Why |
|---|---|
| No memory of facts | Memory bridge lives in `SessionState`; never bound → no memory in context, no writes |
| Didn't recognise Gaggu-consistency, hallucinated "yaad aa gaya" | Legacy prompt has no memory and no anti-hallucination beyond the canned line |
| Verbatim canned reply ×6 | Legacy prompt rule 4 quotes that exact sentence |
| No perception heads | `stream_prose()` (fused call) never executed |
| `ctx NOT CAPTURED` always | Same — `fused.meta` never populated (not the earlier race theory) |
| Feminine "sakti hoon" | Legacy prompt has no masculine-persona rule (C2) |
| `errors=0`, no PARSE-FAILs | The state engine never executed a turn — nothing to fail |

**Timeline:** the death-spiral session (Aug 28 afternoon) shows PARSE-FAIL logs —
proof the fused path ran then. The binding was lost during the Aug 28 evening
STT-router wiring burst (22:25–22:49) when the engine-init block was rewritten.
The two Aug 28 night sessions and this morning's session all ran the legacy brain.

## Second latent bug found in the same investigation

`providers/stt_router.py` used `asyncio.timeout` **without importing asyncio** —
`NameError` on every call, caught by the router's blanket except → Groq silently
handled 100% of turns. Gemini Live STT has *never* transcribed a single turn,
despite console lines suggesting it was primary. (Confirmed by absence of the
router's hardcoded `avg_logprob=-0.2` signature in the session log — every turn
showed real Groq logprobs.) Also fixed while there: the Live provider faked
`avg_logprob=-0.2` (which would blind the catastrophic-low-confidence gate),
had no language pin (owner ruling: `hi` default), and recorded no provider
attribution per turn.

## Fixes shipped

1. **Binding** — `process_user_audio` now constructs
   `SessionState(owner_id=participant.identity, store=engine["store"])` on first
   participant join (C5 device-scoped owner, as designed) and logs
   `SESSION BOUND owner=... memory_items=N`.
2. **Legacy brain forbidden** — if the state engine is expected but unbound,
   the turn speaks the deterministic D4 filler and logs `ENGINE_UNBOUND`
   (CRITICAL) instead of silently running the legacy assistant prompt.
   The legacy path now requires explicit `AIVA_STATE_ENGINE=0`.
3. **Provider fault isolation** — a Gemini Live init failure no longer risks the
   engine binding (catch widened; component degrades alone).
4. **STT router** — `import asyncio`; honest `avg_logprob=None`; `hi` pin
   (`AIVA_STT_LANGUAGE`, same env as GroqSTT); per-turn
   `turn["stt_provider"]` + failure reason.
5. **User-stated relationship capture** (the Neetu case) — deterministic
   `extract_entities_from_user_text()` (no LLM call) commits `X — user's Y`
   relationships the USER says in-session, first-person anchored, pronoun-guarded,
   alias-normalized (`नीतु/नित्तु/Neetu Ben→Neetu`). Store dedups by content.
   Note: the earlier "Neetu told yesterday" fact was never saved because that
   session was the degraded one (no working heads → no memory candidates); today's
   capture closes the in-session part of that hole.
6. **Detector upgrades** — gender detector now catches `sakti/chahti` forms
   (evidence: "kar sakti hoon"); diagnostic flags the legacy canned line
   (`☠ LEGACY-BRAIN`), shows `engine` path per turn and `stt providers` counts.
7. **Persona V1.4** — adds `sakti/chahti` to the banned feminine list with the
   observed BAD/GOOD pair.

## Guardrails learned (PM notes)

- **A print is not a wiring check.** The binding block *looked* complete
  (import + owner extraction + success log). Only the assignment was missing.
  Pre-flight now requires the `SESSION BOUND` console line.
- **A silent fallback converts a bug into a wrong-brain product.** The legacy
  path masked the failure so well that three sessions were needed to see it.
  Fallbacks must be loud (filler + event) or explicit (env flag).
- **Prove the happy path, not the absence of errors.** `errors=0` and healthy
  latency looked like success while the entire intelligence layer was dark.
  The new first-turn checklist: `engine paths` must show `fused`, heads must
  be non-zero.

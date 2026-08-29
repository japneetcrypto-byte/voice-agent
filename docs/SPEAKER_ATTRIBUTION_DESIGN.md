# Speaker Attribution — staged design (owner brief 2026-08-29)

**Owner proposal:** use voice attributes to attribute audio BEFORE ASR — agent echo
dies at the audio level; a second human gets their own key ("speaker_2") tracked
across turns; the agent can then talk WITH the room, not just one person.

**Adopted refinements (from the owner's pasted analysis, agreed):**
- Similarity + confidence + temporal consistency — never binary "voice changed"
- One short utterance never creates an identity; candidates are promoted after
  several clean segments
- The text-level echo filter stays as Layer 2 until the acoustic gate is proven
  on live data

## Two additional stack-specific insights (this doc's contribution)

1. **For echo, we have something better than voice embeddings: the exact played
   signal.** We hold every PCM sample Aiva spoke. Echo is that waveform replayed
   through speaker→air→mic, so multi-band envelope correlation against our own
   played audio is a stronger, cheaper, dependency-free discriminator
   (`providers/speaker_signature.py`). Synthetic separation: echo 0.43–0.69 vs
   unrelated speech ≤0.30, worst-case 68ms.
2. **The embedding trap:** an agent voice-print enrolled from *clean* TTS output
   will NOT match Aiva's voice captured through a room speaker (response + reverb
   + codec distort it). Any enrollment must use room-captured audio. (For the
   user/speaker_2 registry this doesn't apply — humans are enrolled from their
   own mic captures.)

## Stage plan

| Stage | Scope | Gate to advance |
|---|---|---|
| **1 (SHIPPED, shadow)** | `speaker_signature.echo_score` per turn vs rolling 12s of played audio; telemetry `turn["echo_corr_score"]` + events `ECHO_MULTI_AGREE` / `ECHO_TEXT_ONLY` (possible eaten user) / `ECHO_CORR_ONLY` (missed echo). No drop decisions. | ~3 live sessions → real score distributions |
| **2 (gate activation)** | Text echo filter drop requires agreement OR corr ≥ calibrated threshold; `ECHO_TEXT_ONLY` turns are kept (user wins ties). Saves STT+latency on true echoes. | Gate accuracy ≥99% of the text filter's precision, with zero eaten-user regressions |
| **3 (speaker registry)** | Real speaker embeddings (resemblyzer / ECAPA — **owner decision**, torch-class dependency). Agent reference enrolled from room captures; primary_user key; speaker_2 candidate → promoted after N consistent segments. Turns carry `speaker_id/role/confidence`. | Live eval on multi-person sessions |
| **4 (room-aware Aiva)** | Persona + policy consume speaker roles (the ROOM AWARENESS persona line becomes structured); per-speaker memory attribution. | Owner ruling on scope |

## Decision points for the owner

1. **Stage 3 dependency:** torch-based embedding stack (~GB install on the Mac/uv)
   vs a lighter ONNX embedding — after Stage 2 data tells us how much Stage 3 is
   worth.
2. **speaker_2 UX:** when a second person is detected, should Aiva acknowledge
   them explicitly ("doosri awaaz? namaste!") or stay user-focused and just track
   silently? (Companion product likely: acknowledge.)
3. **Privacy:** speaker keys are device-local math (no upload) — confirm no
   cloud speaker profiles for MVP.

## Current status

- Stage 1 shipped in shadow mode (default ON, telemetry only, zero behavior change).
- Where to look: `turn["echo_corr_score"]` in session logs; events
  `ECHO_MULTI_AGREE` / `ECHO_TEXT_ONLY` / `ECHO_CORR_ONLY`; self-diagnose will
  report the echo class from these.

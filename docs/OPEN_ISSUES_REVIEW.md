# OPEN ISSUES & REVIEW PACKET — Aiva voice agent (2026-08-29 night)

**Purpose:** independent review / second opinion. Owner asked for a written
account of: what is troubling us, what remains open, what has been fixed — so
an outside reviewer can challenge our reasoning.

**Reviewer, please pay special attention to section D (contested/uncertain) —
that is where a second opinion is most valuable.**

Context: real-time Hinglish voice companion. Pipeline: LiveKit audio → TEN VAD
→ Groq Whisper STT → fused Gemini call (perception head + persona reply in one
call) → Fish Audio cloned TTS. Deterministic state engine + SQLite memory +
supervisor + telemetry around it. All code on `arena/01a03e6f-voice-agent`,
~204 regression cases, one-command health report (`phase5/aiva_health.py`).

---

## A. OPEN ISSUES (troubling us now, ranked by user impact)

### A1. Voice latency vs user speaking pace — the #1 experience killer
- **Symptom:** when the user talks quickly, replies get cancelled before any
  audio plays (measured 4/22 turns = 18% in one session; user complained
  in-session: "tum itna time lekar kyon bolte ho?"). Also episodic Fish
  congestion (TTFA 2.9–4s vs 1.5–1.9s baseline; some zero-audio streams).
- **Mechanism:** TTS first-audio ~1.7–2.1s; cancel-on-newer-turn drops the
  pending reply unheard when the user speaks again sooner.
- **Contained:** failover to EdgeTTS on hang/silence; supervisor speaks a
  recovery line; pre-audio cancels now counted (were invisible before).
- **Not solved:** the latency itself. Containment prevents silence, not delay.
- **Options on the table:** Fish paid tier (same clone, cheapest) /
  ElevenLabs Flash (fastest, new clone, ~1 day A/B harness) / duck-and-merge
  (play pending line under the new answer instead of cancelling).
- **Question for reviewer:** which order? Is duck-and-merge worth building
  before a provider switch?

### A2. Response-quality drift: echo-confirm parroting
- **Symptom:** 4/11 replies in one session were parroted confirmations
  ("X ki baat kar raha hai na?"); identical clarify line 3× in a row; one
  Devanagari reply despite a Roman-script persona rule.
- **Root cause hypothesis:** the recoverable-unclear pattern ("confirm your
  interpretation") was rewarded so often (garbled STT sessions) that flash-lite
  adopted it as a default reply strategy.
- **Shipped (UNPROVEN):** persona V1.7 NO-PARROTING + V1.8 spelling
  discipline; deterministic parrot-streak detector (≥3/4 replies) that injects
  an anti-parrot avoid-list into the next calls' policy; diagnostic flags
  (CONFIRM-ECHO, substance ratio).
- **Question for reviewer:** is application-layer policy nudging the right
  mechanism, or should parrot-control live in the deterministic updater
  (response policy dimension)?

### A3. STT garble rate on romanized/quick Hinglish
- **Symptom:** recent sessions show heavy garble ('झाल', 'आपे', 'शेद', 'जिब',
  'मुश्वमी'), which drives clarify loops and (with A2) parrot loops.
- **Unknown:** mic/setup change vs user deliberately mumbling vs Groq
  whisper-large-v3 limits on quick casual Hindi. Need a controlled mic test.
- **Question for reviewer:** experience with Whisper vs alternatives
  (Deepgram, Sarvam ASR, Gemini Live transcribe) for Indian Hinglish?

### A4. Word separation / merged & split words — THE PARSER QUESTION (owner raised)
- **Evidence chain (fully reconstructed):**
  1. The model's stream splits MID-WORD across chunks without whitespace
     ('th'+'ik' = "thik", 'nah'+'in' = "nahin", 'k'+'ela' = "kela" — all valid
     romanizations after plain concatenation). Tokenizer artifact of
     romanized Hinglish.
  2. We once "repaired" this with a join heuristic that inserted spaces at
     word-char boundaries — it was a MISDIAGNOSIS: it created the very splits
     ("th ik", "nah in", "k ela"). Reverted. Lesson recorded.
  3. Separately, some words arrive MERGED ("sebaithne", "sahikaam",
     "baaremein", "juis"). Cannot tell from text alone whether the model
     emitted them merged or the stream dropped a space.
- **Current state:** plain concatenation (mid-word splits harmless) +
  deterministic exact-match lexicon for known merges + persona V1.8
  spelling-discipline instruction.
- **Known limits:** (a) lexicon is reactive — new merge forms appear as new
  incidents; (b) exact-match cannot repair unseen forms; (c) fuzzy splitting
  risks damaging valid words (we already shipped one such bug).
- **Question for reviewer:** better ideas for disambiguating word boundaries
  in streamed romanized-Hinglish at runtime? (Dictionary-based segmenter?
  Model-tier change? Devanagari output to sidestep romanization entirely?)

### A5. Model-tier ceiling (flash-lite)
- Garbled-Hinglish understanding, parroting drift, and head-tag format
  instability (5 variants observed) may all be the same root: model tier.
- **Proposal ready:** A/B harness replaying logged real turns through
  flash-lite vs a stronger model, blind-scored. Not built — awaiting owner
  go (costs money per replay).

### A6. Quota / free-tier fragility
- 3 Gemini keys = same Google project = one quota pool; rotation redistributes,
  adds nothing. Episodic 429s on heavy days. Fine for dev; not for pilots.

### A7. Voice-keys (speaker attribution) — Stage 2/3 waiting
- Stage 1 (acoustic echo correlation, shadow) shipped & verified: true echo
  0.43–0.69 vs unrelated ≤0.30 synthetic; needs ~3 more live sessions +
  echo candidates for the Stage-2 gate verdict
  (`phase5/echo_shadow_report.py`).
- Stage 3 (speaker_2 registry, owner's "two keys" design) needs an embedding
  dependency + 3 owner decisions (dependency, UX, device-local privacy).

### A8. Smaller measured gaps
- Barge-in stop latency avg ~2.2s (cancel-after-validate; hybrid duck-and-
  cancel designed, Phase 7).
- Emotion gates (G-EMO/G-CAL) blocked on labeled real-voice recordings.
- One full clean session (no provider incident) still pending for final
  Phase-5 sign-off.

## B. FIXED (evidence-backed, regression-tested — the audit trail)

1. **Engine never bound** — `engine["sess"]` never assigned; every session ran
   a legacy fallback prompt (canned replies ×6, no memory, no persona). Fixed:
   binding at participant join + legacy path forbidden (filler + alarm).
2. **STT router never ran Gemini Live** — missing `import asyncio` → NameError
   on every call, silent Groq fallback. Fixed; honest logprobs (no fake -0.2);
   language pin; per-turn provider attribution.
3. **Degraded-perception death spiral** — unreachable exit; 40+ PARSE-FAIL
   cascade. Fixed: cooldown exit after 3 degraded turns (+tests, determinism).
4. **Epoch off-by-one** (our own fix was broken) — perception capture would
   have been zeroed. Fixed + laziness-semantics test.
5. **Perception tag mis-closes** (</p>, </parception>, </s_perception>,
   </s:perception>) — raw tags SPOKEN aloud. Fixed: class-level parser +
   salvage + belt-and-braces sanitizer; byte-exact regressions.
6. **Checkpoint leak** — prior session's raw turns polluted next session
   (hist=106 at turn 1). Fixed: discard on clean end; verified live (hist=1).
7. **Memory write gaps** — relations stated by USER never captured; 2-word
   garbles captured as facts. Fixed: first-person-anchored extractor
   (3 orientations, alias normalization) + garble guards + pending-until-
   confirmed for first sightings.
8. **Persona drift** — service-speak, feminine self-reference, Devanagari,
   verbosity, assistant-speak, reality violations (claimed to check Blinkit).
   Fixed via persona iterations V1.3→V1.8 + deterministic detectors +
   V1.8 spelling discipline.
9. **Silence chain** — controller suppressed pronoun-final turns, missed
   mid-utterance handoffs, no WAIT cap; silent skip race after barge-in.
   Fixed: marker cleanup, any-word handoff, greetings, WAIT_STREAK_CAP,
   await-teardown + RESPONSE_SKIPPED alarms.
10. **Self-healing layer** — supervisor (rescue lines + incident snapshots +
    escalation), TTS zero-audio failover (samples-based), late-echo guard
    (user repeats kept), pre-audio-cancel visibility, worker-build mismatch
    warning, self-diagnosis + stage-verdict engines, one-command health check.

## C. VERIFIED NON-ISSUES (checked, fine)
- Echo filter correctness for true echo (kept; late repeats now saved).
- Layer-2 compression cycling (hist oscillates, no leak).
- Memory binding (owner-scoped; 21 items recalled live).
-STT language pin (hi) applied across all three STT paths.

## D. CONTESTED / UNCERTAIN — where we most want a second opinion
1. **smart_join episode:** we shipped a space-inserting heuristic, it created
   splits, we reverted. Was the right call to revert, or should boundary
   disambiguation be attempted with a dictionary segmenter?
2. **Merge attribution:** "sebaithne"-class merges — model-emitted vs
   stream-dropped space is UNDECIDABLE from text. Our lexicon assumes it
   doesn't matter. Does it?
3. **Anti-parrot mechanism:** policy-avoid nudge vs updater-owned response
   policy vs pure prompt. Which layer should own response variety?
4. **TTFA thresholds:** our gates (FAIL >3.5s p95, WATCH >2.2s; pre-audio
   cancels >10%) are reasonable-looking guesses. Industry numbers for
   acceptable voice-agent response latency on outbound sales?
5. **Model tier:** is flash-lite's garble-handling the real ceiling, or is our
   prompt over-constraining (persona now ~90 lines)?
6. **Devanagari replies:** should TTS input be Devanagari (avoids romanization
   merge/split entirely, Fish handles it) with persona rewritten accordingly?

## E. HOW TO VERIFY ANY CLAIM IN THIS PACKET
- Health report: `python3 phase5/aiva_health.py` → logs/health_<session>.md
- Self-diagnosis: `python3 phase5/self_diagnose.py [logfile]`
- Stage verdict: `python3 phase5/stage_verdict.py`
- Voice audit: `AIVA_TTS_DUMP=1` then `python3 phase5/tts_audit.py [--asr|--mos]`
- Voice-key: `python3 phase5/echo_shadow_report.py`
- Tests: `python3 phase5/tests/test_*.py` (9 suites, ~204 cases)
- Full code: repo `arena/01a03e6f-voice-agent`; every fix is a commit with
  evidence in its message; `docs/WORK_LOG.md` is the complete chronicle.

---

## F. PRE-REVIEW RED TEAM (self-administered — treat with suspicion, verify)

Self-review has blind spots by definition. These are our own adversarial
answers to section D, written to be falsified by the external reviewer.

**F1 — D1 (smart_join): revert was right, but both the heuristic AND the
revert dodged the real question.** Does a merged word ("sahikaam") actually
sound wrong to a listener? Fish Audio does its own text normalization — we
flagged merges in TEXT but never A/B-listened to merged vs corrected audio.
Cheap action before any more engineering: record 5 merged vs 5 corrected
replies, blind listen. If inaudible → A4 is cosmetic; retire the lexicon churn.

**F2 — D2 (attribution): it IS decidably cheap, and we never ran the test.**
Generate one reply in batch (non-streaming) mode. Merges persist → model-emitted
(model-tier fix). Merges vanish → streaming artifact (buffer-to-boundary fix).
Five minutes of work decides which fix class is correct. We assumed instead of
measuring — exactly what our own discipline forbids.

**F3 — D3 (parrot layer): the nudge is the third patch on a symptom.** Parrot
rate correlates with STT garble rate; garbled input forces clarify/confirm
loops. If the mic test (A3) shows clean input kills parroting, retire the
policy nudge. Long-term, variety control belongs in the deterministic updater
(architecture principle: "LLM interprets; deterministic code decides") — but
even that may be over-engineering if the input fix suffices.

**F4 — D4 (thresholds): our gates are lenient by industry standards.**
Outbound-sales-grade stacks (Deepgram + ElevenLabs Flash class) target TTFA
<500ms and end-to-end <1s. Our FAIL gate (3.5s p95) is ~4x that; WATCH (2.2s)
is ~2x. Acceptable for a companion MVP; unsellable as a sales product. We also
lack end-to-end latency targets as a product spec — per-stage gates only.

**F5 — D5 (model tier AND prompt): two sharp self-criticisms.**
(a) The persona has grown to ~90 lines of accumulated incident rules. Every
incident added a rule; instruction dilution is a real failure mode, and our
BAD examples may be TEACHING the failure patterns (negative-example leakage —
"don't think of an elephant"). (b) We never ran the obvious control: same
sessions against a ~25-line persona with enforcement moved to code. The
prompt-rebuild experiment should precede model-tier spend.

**F6 — D6 (Devanagari): the strongest untested lever in this document.**
Devanagari output sidesteps the ENTIRE romanization merge/split class; our STT
already outputs Devanagari and the model reads it fine; Fish handles
Devanagari input. Unknowns: Fish pronunciation quality Devanagari-vs-roman,
and script normalization for the memory/entity stores. Cost of a trial: one
session. We have no good reason it hasn't been tried.

**F7 — The strategic critique an external reviewer will certainly make:**
"You built an elaborate containment system around sub-bar components."
Containment does not compound; component quality does. Six of our nine open
issues (A1 latency, A2 parroting, A3 garble, A4 merges, A5 tier, A6 quota)
trace to two component choices (free-tier model + free-tier voice) and would
shrink or vanish after a component upgrade. Sequence matters: run the two A/B
harnesses (model + voice) BEFORE building more containment — half of it may
retire. Also missing: an end-to-end golden quality set (20 scripted turns,
blind-scored after every change) so 'is it better?' stops being a feeling; and
provider redundancy (one Fish account, one Groq key = single-point outages).

**F8 — Strengths (calibration, so the review is honest both ways):**
observability depth (per-turn context/head/latency + one-command health),
determinism discipline (LLM interprets / code decides), memory scoping,
safety containment, and honest incident archaeology including our own
misdiagnoses (smart_join, epoch off-by-one) — documented, not hidden.

**F9 — Five falsification targets for the external reviewer:**
1. Reproduce the echo-score separation (0.43–0.69 vs ≤0.30) on REAL session
   audio, not our synthetic fixtures. If real-room floor is higher, Stage 2
   gates are wrong.
2. Listen to merged-word audio (F1). If inaudible, our text-level flagging
   overstates the problem.
3. Batch-generation test (F2). Attribute the merges definitively.
4. 25-line persona vs 90-line persona on the same logged turns. Is prompt
   bloat actively degrading quality?
5. Attack the pre-audio-cancel framing: is cancel-on-newer-turn even wrong,
   or is answering the newest input correct and the only fix really TTFA?

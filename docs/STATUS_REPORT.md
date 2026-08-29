# WHERE WE STAND — full status report (2026-08-29 evening)

**Regression audit:** PASSED (section 2). **Verdict:** system unit healthy; the
voice layer (Fish Audio free tier) is the experience ceiling — replacement
decision pending with the owner.

---

## 1. What Aiva is today (verified working)

A real-time Hinglish voice companion: Groq Whisper STT → fused Gemini call
(perception head + persona reply, one LLM call) → Fish Audio cloned voice.
Around it: deterministic state engine (7-dimension model), device-scoped SQLite
memory, turn controller, call supervisor, layered context, reply guards, full
per-turn telemetry, self-diagnosis, one-command health report.

**Today's live numbers (best sessions):**

| Metric | Value |
|---|---|
| Speech→audio latency | 1.8–2.5s avg (2.07s best), p95 ~3.1s |
| Perception heads | 21/21 and 47/47 in healthy sessions |
| Context captured | 47/47 |
| Avg reply length | 1.7–2.1s audio (word budget holding) |
| Errors (healthy sessions) | 0 |
| Cross-session memory recall | verified live ("Gaggu hai na, jaise bataya tha") |
| User-stated relations captured | yes (Neetu/behen class, incl. garble guards) |

## 2. Regression audit (tonight) — ALL GREEN

- 9/9 test suites pass (~204 cases): reply guard, user-entity extraction,
  audit-fixes, turn controller, call supervisor, state updater (+ determinism
  k=2), layered context, speaker signature.
- pyflakes: zero undefined names / redefinitions in runtime code.
- Wiring assertions verified present: session binding, unbound-guard (legacy
  brain forbidden), STT router asyncio import, honest logprobs, voice-key
  shadow call + played-audio ring, tag sanitizer, merged-word lexicon,
  pre-audio-cancel mark, worker build stamp, supervisor rescue, TTS
  samples-based failover, TTS dump hook, persona TRANSPORT_V1.6.
- One known benign: dead local in dead-code stt_gemini_live.transcribe_stream.

## 3. Voice-keys infra (speaker attribution) — exact standing

**Shipped and verified — Stage 1, SHADOW MODE (zero behavior change):**
- providers/speaker_signature.py: multi-band acoustic echo correlation —
  scores every user utterance against the last 12s of Aiva's actually-played
  audio. Synthetic separation: true echo 0.43–0.69 vs unrelated speech ≤0.30;
  ~60–70ms compute. 8-case test suite green.
- Wiring: 12s played-audio ring in main.py → per-turn turn["echo_corr_score"]
  + shadow events ECHO_MULTI_AGREE / ECHO_TEXT_ONLY / ECHO_CORR_ONLY.
- Calibration tool: phase5/echo_shadow_report.py — computes Stage-2 gate
  thresholds when data suffices (needs ≥30 scored kept-turns and ≥8 true-echo
  candidates).
- Design doc: docs/SPEAKER_ATTRIBUTION_DESIGN.md (4 stages + owner decisions).

**Not yet done (by design, gated):**
- Stage 2 (gate activation — acoustic score helps drop echoes pre-ASR):
  BLOCKED ON DATA. Latest session collected only n=3 corr samples. Need ~3
  more normal sessions (talk right after Aiva speaks to generate echo
  candidates), then rerun echo_shadow_report.py for the KEEP-SHADOWING →
  SEPARABLE verdict.
- Stage 3 (speaker_2 registry — your two-keys design): needs a real
  speaker-embedding dependency (torch-class) — owner decision; plus UX
  (acknowledge speaker_2 out loud vs track silently) and privacy posture
  (keys device-local — confirm).

## 4. Open items (ranked)

| # | Item | State | Owner action |
|---|---|---|---|
| 1 | TTS provider — Fish free tier: episodic TTFA spikes, silent streams, pre-audio cancels up to 18% when user talks fast | contained (failover + supervisor), not cured | Decision: Fish paid tier (10 min, same clone) or ElevenLabs Flash A/B (I build, ~1 day) |
| 2 | Voice-key Stage 2 | awaiting shadow data | just talk; run echo_shadow_report.py after ~3 sessions |
| 3 | Speaker registry Stage 3 | design ready | embedding dependency + UX + privacy decisions |
| 4 | Barge-in stop latency ~2.2s | measured, contained | Phase 7 (hybrid duck-and-cancel designed) |
| 5 | G-EMO/G-CAL emotion gates | blocked on labeled recordings | your real-voice recordings |
| 6 | Clean full-stack live validation | near — sessions keep hitting provider incidents | one quiet hour, one session |

## 5. The day's arc (evidence trail)

Legacy-brain incident (engine never bound) → binding fix + legacy forbidden →
STT router asyncio bug (Gemini Live never ran) → full audit (10 fixes incl.
death-spiral cooldown) → first healthy session → tag-variant class-level fix →
checkpoint leak fixed → user-stated relationship capture → persona V1.3→V1.6
(service-speak ban, reality honesty, brevity) → call supervisor ("the senior")
shipped and proven live → self-diagnosis engine → stage-verdict decision rule →
pre-audio cancel visibility → worker-build mismatch detection → speaker
attribution Stage 1 shadow.

Every fix regression-tested; suite grew ~60 → ~204 cases in one day.

## 6. How to check anything, anytime

- One command: python3 phase5/aiva_health.py → logs/health_<session>.md
  (config fingerprint + identity + memory + per-turn detail + voice audit +
  self-diagnosis + stage verdict + voice-key calibration + trend table).
- Voice keys: corr= per turn in the stage diagnostic;
  phase5/echo_shadow_report.py for the Stage-2 gate verdict;
  phase5/tests/test_speaker_signature.py for the math.
- Worker staleness: the health report warns if the running worker ≠ repo HEAD.

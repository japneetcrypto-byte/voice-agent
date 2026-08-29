# Speaker Attribution & Echo — Architecture Review and Implementation Report

Responds to the external directive (2026-08-29): inspect first, report, no
speculative implementation. Every claim below was verified against the working
tree at commit `32426fc` (files: `providers/speaker_signature.py`,
`agent/main.py`, `providers/tts.py`, `phase5/echo_shadow_report.py`,
`docs/SPEAKER_ATTRIBUTION_DESIGN.md`).

**Bottom line up front:** the existing Stage-1 implementation already matches
the directive's core principles (playback-signal correlation, shadow-first,
text filter retained, conservative evidence combination, no embeddings yet).
Two gaps found and fixed as part of this review (both Stage-1 scope, telemetry
only): shadow records were missing context fields the directive requires
(speech duration, playback state, final classification), and thresholds were
hardcoded rather than configurable. Nothing behavioral changed.

---

## A. Current State (what is actually implemented today)

| Component | Status | Where |
|---|---|---|
| Rolling playback buffer | ✅ 12s @16kHz, in-memory deque, fed 48k→16k during TTS playback | `main.py` `played_ring` |
| Multi-band acoustic echo score | ✅ 4-band joint NCC, FFT, per-lag energy norm; full-window alignment search | `providers/speaker_signature.py` |
| Shadow classification | ✅ per scored turn: `ECHO_MULTI_AGREE` (corr≥0.45 + text) / `ECHO_TEXT_ONLY` (text says echo, corr<0.30 → possible eaten user) / `ECHO_CORR_ONLY` (corr≥0.55, text missed) | `main.py` |
| Full-context shadow record | ✅ **added this review**: corr, text_sim, text_echo, speech_ms, ms_since_playback_end, played_ring_s, final decision → `turn["echo_shadow"]` | `main.py` |
| Calibration tool | ✅ `phase5/echo_shadow_report.py` — speech floor vs echo band, proposes T_low/T_high, verdict KEEP-SHADOWING/SEPARABLE | |
| Text-level echo filter (Layer 3) | ✅ RETAINED as the only decision-maker | `main.py is_echo` |
| Late-repeat guard | ✅ speech starting >1.5s after playback end kept as real (4 saves in one live session) | `main.py` |
| Behavior change from acoustic layer | ❌ none — by design (Stage 1) | |

**Not implemented (deliberately):** pre-ASR attribution, speaker registry,
embeddings, speaker-aware context/memory. These are Stages 2–5.

## B. Architecture Assessment

**Correct (validated by tests/live sessions):**
- Correlating against the *actual played signal* rather than a voice-print —
  avoids the enrollment trap (clean TTS ≠ room-captured voice) and needs no
  dependency. This matches the directive's section 3 exactly.
- Shadow-first with the text filter retained (directive sections 2, 7, 8).
- Multi-band joint correlation: single-band NCC false floor measured at 0.83;
  the joint-band constraint drops it to ~0.30 (synthetic). This was the
  difference between a usable and an unusable gate.
- Conservative evidence direction already matches section 9: only
  agreement-classified echoes would ever drop; acoustic-only flags never drop.

**Fragile (honest):**
1. **Validation is synthetic.** Separation numbers (0.43–0.69 echo vs ≤0.30
   speech) come from generated fixtures. Real-room distributions are the
   Stage-1 exit criterion and do not exist yet (n=3 real samples so far).
2. **Scoring runs at speech-end, not continuously.** Directive P1 wants
   running confidence during audio flow. Today the score computes once per
   speech-end (~50–70ms, after STT). Acceptable now (it does not add
   user-perceived latency — it sits inside the existing decision window), but
   pre-ASR gating in Stage 2 will need either (a) accepting the 50–70ms
   inline, or (b) incremental scoring during capture.
3. **Short utterances return None** (need ≥~1.5s of speech for a stable
   envelope). "हाँ"-class utterances fall back to the text filter entirely.
   Fine for Stage 2's conservative policy; must be stated in the gate rule.
4. **Bluetooth/headset scenarios untested** — the ring only holds our own
   playback, so capture-path variation affects the correlation, but the full
   alignment search + per-lag normalization should absorb fixed lag. Unknown
   until a real headset session.
5. **Ring buffer holds int16 in a Python list** (12s = 192k ints; converted
   to ndarray per scoring). Works, but ndarray-backed would remove per-turn
   allocation. Cosmetic.

**Should change (done in this review):** richer shadow records + configurable
thresholds (both above). **Should change later (Stage 2):** incremental or
parallel scoring; gate decision table encoded in one function with unit tests.

## C. Data Flow (exact, as-built)

```
mic → LiveKit frame (48k) → resample 16k → TEN VAD (adaptive endpointing)
  → speech buffered (+ played_ring fed during TTS playback, pre-roll 300ms)
  → speech end → RMS/peak audio gate → [Gemini Live streaming | Groq] STT
  → transcript → romanize → TEXT echo filter (is_echo, sim>0.65)
  → [SHADOW] echo_score(capture, played_ring) → echo_shadow record
  → late-repeat override (>1.5s → keep)
  → validity gates (no_speech/logprob/repetition) → echo DROP or KEEP
  → turn controller (WAIT/RESPOND) → fused LLM (policy incl. anti-parrot)
  → reply guards (tags/specials/merges/length) → TTS (Fish→Edge failover)
  → playback (+ tts_capture if AIVA_TTS_DUMP=1, + played_ring feed)
```

**Where the new layer sits:** between the text echo filter and the validity
gates — shadow-only today. Stage 2 moves the *decision* there (gate before
validity, with the text filter demoted to evidence + fallback). Stage 3 adds
attribution before ASR per the directive's target pipeline.

## D. Threshold Analysis (every gate in the attribution path)

| Threshold | Signal | On cross | Validated? | Configurable |
|---|---|---|---|---|
| corr ≥0.45 (AGREE) | multi-band NCC | confirms text echo (event) | synthetic only | ✅ `AIVA_ECHO_AGREE` (added) |
| corr ≥0.55 (MISS) | multi-band NCC | flags echo text filter passed | synthetic only | ✅ `AIVA_ECHO_MISS` (added) |
| corr <0.30 (FLOOR) | multi-band NCC | text-echo verdict suspect | synthetic only | ✅ `AIVA_ECHO_FLOOR` (added) |
| text sim >0.65 | difflib ratio vs last reply | echo drop decision | live-proven (multiple sessions), 1 known false-eat class | no (code) — should be env in Stage 2 |
| >1.5s after playback end | ms_since_playback_end | late repeat kept as real | live-proven (4 saves) | no (code) |
| speech ≥~1.5s | envelope length ≥25 samples | corr defined, else None→text filter | by construction | no (module const) |
| MAX_CAP_S 8.0 | capture length | bounds compute | latency-tested (52–68ms) | no (module const) |

**Assessment (directive D):** all acoustic thresholds are synthetic-validated
only — the report tool's calibration is the designed path to real values. The
0.45/0.55/0.30 triple came from fixture distributions; expect real-session
revision. The text 0.65 sim is the only live-battle-tested gate.

## E. Latency Analysis

- `echo_score`: measured 52–68ms worst case (8s capture vs 12s window, numpy
  FFT). Runs once per speech-end, inside the existing STT→decision window
  (parallel to transcript validation work). **Added user-perceived latency:
  ~0** (it does not gate ASR and finishes well inside the decision step).
- Ring feed: O(samples) list extend during playback — negligible.
- Memory: ring ≈ 192k int16 (~0.4MB). Raw audio is never persisted; TTS WAV
  dumps are opt-in (`AIVA_TTS_DUMP=1`).
- Directive P1's "continuous scoring during flow" is NOT yet implemented —
  flagged in B.2. For Stage 2, inline 50–70ms is acceptable (it replaces, not
  adds to, the text-similarity computation); Stage 3 pre-ASR attribution will
  need incremental scoring.

## F. Error Analysis (scenario by scenario)

| Scenario | Current behavior | Gap |
|---|---|---|
| True echo | text filter drops; corr will agree → future fast-drop | none |
| User repeats Aiva's words late (>1.5s) | kept (late-repeat guard) — proven 4× live | none known |
| User repeats within 1.5s | still eaten by design | Stage 2 gate (low corr → keep) should fix |
| Background noise | VAD + RMS/peak gate + validity gates | fine |
| Overlapping speakers | cancel-on-newer; corr on mixed signal may dilute | Stage 3 territory |
| Speaker switch (2nd human) | treated as user; no identity | Stage 3 |
| Reverberation | synthetic smear test passed (0.46 vs 0.30 floor) | real-room TBD (Stage 1 exit) |
| Short utterances ("हाँ") | corr=None → text filter fallback | stated limitation |
| Low-volume speech | RMS/peak gate may reject | pre-existing, out of scope |
| TTS leakage (BT/headset) | corr expected to spike (full-window lag search) | needs a real headset session |
| Stale worker | build-stamp + health-report mismatch warning | fixed earlier today |

## G. Product Impact

Direct: fewer eaten-user turns (the kharbuja class) once Stage 2 gates —
without risking echo-through, because the gate requires agreement. Indirect:
`speaker_id` on turns (Stage 3+) enables per-speaker memory attribution
(directive section 12) — interfaces will be designed then; nothing built now.
Perceived intelligence: recovery lines + not-ignoring-the-user matter more
than attribution itself; the supervisor already covers the silence case.

## H. Privacy / Infrastructure

- Ring buffer: in-memory, session-local, never written to disk. ✅
- Speaker keys: none exist yet; when they do — device/session-local per the
  directive and our earlier privacy ruling. No cloud voice identity. ✅
- Embeddings: not introduced; the decision framework (dependency size, CPU,
  cold start, privacy) is pre-written in `docs/SPEAKER_ATTRIBUTION_DESIGN.md`
  §Stage-3 and echoed by the directive section 6. ✅
- TTS WAV dumps: opt-in only. ✅

## I. Rollout Plan (with exit criteria)

| Stage | Exit criterion |
|---|---|
| 1 Shadow (current) | ≥3 sessions with ≥30 scored kept-turns AND ≥8 true-echo candidates; `echo_shadow_report.py` prints SEPARABLE with real T_low/T_high |
| 2 Conservative gate | gate = drop iff (corr ≥ T_high AND text sim ≥0.65) OR (corr ≥ T_high+0.1 AND ≥2.5s speech); keep on any uncertainty. Exit: ≥5 sessions, zero eaten-user regressions, echo-through rate ≤ text-filter baseline, no added latency |
| 3 Speaker registry | embedding-free first (Band-energy voice features or corr-based profiles); promotion UNKNOWN→CANDIDATE→SPEAKER_2 after N consistent segments; exit: switch-detection accuracy + zero false promotions on 3 multi-person sessions |
| 4 Speaker-aware context | speaker_id/role/confidence attached to turns; persona receives room state |
| 5 Speaker-aware memory | per-speaker attribution on memory records — only after Stage 3 accuracy proven |
| 6 Multi-person companion | product decision |

## J. Final Recommendation

1. **Keep:** playback-correlation approach; shadow-first; text filter as
   Layer 3; conservative agreement-only drop policy; no embeddings yet.
2. **Change (done now):** richer shadow records; configurable thresholds.
   **Change (Stage 2):** gate decision function + env'd text-sim threshold;
   consider incremental scoring only if inline 50–70ms ever matters.
3. **Do NOT build yet:** speaker registry, embeddings, speaker-aware
   memory/context, pre-ASR attribution — all gated behind Stage-1 data.
4. **Metrics needed:** the directive §16 set — implemented as: turn
   `echo_shadow` records (precision/recall derivable), `echo_shadow_report.py`
   (calibration), trend table (deterioration), stage-verdict (per-stage
   health). Missing: an automated echo precision/recall computation from
   logged fields — small addition to echo_shadow_report when data exists.
5. **Next implementation step:** none beyond what shipped today. **Collect
   real sessions.** The Stage-1 exit is data, not code. When
   `echo_shadow_report.py` says SEPARABLE on real distributions, implement the
   Stage-2 conservative gate (a ~20-line change + tests).

**Alignment check:** the directive's guiding principle — "smallest reliable
speaker-attribution layer that materially improves conversational quality,
preserving the validated pipeline as fallback" — is exactly the shipped
posture. The one place we should resist over-building is Stage 3: if the
acoustic layer proves sufficient for echo, the speaker registry should wait
for a *product* need (multi-person or memory attribution), not arrive for
technical interest.

---

## K. Directive Ratification & Status Report (2026-08-29 night, §19 format)

The refined directive was reconciled against the implementation line by line.
Verdict: **compliant on every checkable point**; three artifacts added (locked
Stage-2 decision table, workstream separation, this report).

### K.1 Compliance matrix (directive → implementation)

| Directive point | Status |
|---|---|
| §2 playback-signal over voice-prints | ✅ implemented (the core insight, independently derived) |
| §3 Stage-1 inventory (12s ring, multi-band NCC, shadow events, text filter sole decider, no embeddings/registry, zero behavior change) | ✅ all verified in code |
| §3 thresholds synthetic-only, configurable | ✅ env (AIVA_ECHO_AGREE/MISS/FLOOR) with synthetic-only warning |
| §4 non-negotiable decision table | ✅ matches the designed Stage-2 gate exactly (below, now LOCKED) |
| §5-6 Stage-1 goal + exit (≥3 sessions, ≥30 kept, ≥8 echo candidates, report verdict; investigate-if-not-separable) | ✅ exit criteria identical; report implements both verdicts |
| §7 conservative gate (agreement→drop; else keep; uncertainty→keep) | ✅ locked spec, not yet built (Stage 2) |
| §8 short utterances = explicit limitation | ✅ documented (corr=None → text filter) |
| §10 promotion rule (no identity from one segment) | ✅ in design doc Stage 3 |
| §11 embedding levels (L1 corr → L2 features → L3 embeddings) | ✅ Stage-1 = L1; L2/L3 gated |
| §13 privacy (device-local, deletable) | ✅ design doc |
| §14 do-not-overbuild list | ✅ matches "not built" list in section A |
| §15 latency budget | ✅ 52–68ms scoring, zero user-perceived (measured) |
| §16 system-level boundary questions | covered by per-turn telemetry + stage-verdict + health report (formal boundary audit = Stage-2 task) |
| §17 dual scores (infrastructure vs conversation) | health report tracks both (engineering flags + substance/parrot/clarify ratios) |
| §19 reporting format | adopted (this section is the first instance) |
| §21 workstream separation A (echo) vs B (multi-speaker) | adopted — B cannot modify A's code path; both logged separately |

### K.2 LOCKED Stage-2 decision table (directive §4 — implementation spec, not yet built)

| Acoustic | Text | Action |
|---|---|---|
| strong echo | strong match | DROP as echo |
| strong echo | no match | KEEP user speech |
| weak echo / None | strong match | KEEP (investigate: ECHO_TEXT_ONLY) |
| weak / None | weak | KEEP |
| no score (short utt) | match | existing text behavior |
| uncertain | uncertain | KEEP |

Bias: **uncertainty always preserves user speech.** Pre-enablement tests
required (directive §7): genuine echo, genuine user speech, similar-sounding
user speech, low-volume, overlap, no playback, playback-ended-recently, short
utterance, background noise — nine test families before the gate ships.

### K.3 §19 status report — speaker-attribution workstream

1. **What changed (this cycle):** full-context shadow records
   (`turn["echo_shadow"]`: corr, text_sim, text_echo, speech_ms,
   ms_since_playback_end, played_ring_s, decision) + env-configurable
   thresholds. Files: agent/main.py.
2. **Why:** directive §8 — shadow mode must record enough to calibrate.
3. **Architecture impact:** telemetry only; no stage's behavior touched.
4. **Runtime behavior:** at each speech-end, capture vs 12s played buffer →
   multi-band NCC → record + classify → proceed exactly as before.
5. **Metrics:** scoring 52–68ms worst case; zero added user-perceived latency;
   real-distribution counts: kept-turn samples n=3 so far (below exit bar).
6. **Tests:** test_speaker_signature.py (8: separation, degradation tolerance,
   degenerates, latency). No new suites needed for telemetry.
7. **Known limitations:** synthetic-only threshold validation; speech-end (not
   continuous) scoring; <~1.5s utterances unscoreable; BT/headset untested.
8. **DECISION: KEEP IN SHADOW MODE.**
9. **Next action:** collect ≥3 sessions / ≥30 kept-turns / ≥8 echo candidates
   (owner talks right after Aiva speaks to seed candidates), then run
   `phase5/echo_shadow_report.py`. No code until the verdict.

### K.4 Workstream separation (§21) — registered

- **Workstream A (echo reliability):** everything in this document. Owner of
  `providers/speaker_signature.py`, `echo_shadow*`, `is_echo`.
- **Workstream B (multi-speaker experience):** separate roadmap item; may not
  modify Workstream A code paths; enters at Stage 3 of the design doc only
  after a written product requirement.

### K.5 Directive §18 classification of today's other changes (samples)

- Pre-audio cancel visibility → **B** (reliability) · evidence: 4/22 turns
- Anti-parrot guard (V1.7/V1.8) → **D** (conversation quality) · evidence:
  36% echo-confirm ratio; UNPROVEN — next session's substance ratio decides
- Samples-based TTS failover → **B** · evidence: 6 silent-stream turns
- Worker build stamp → **F** (infra) · evidence: stale-worker incidents
- Stage verdict engine → **F** · evidence: repeated manual decomposition

# Aiva — Emotional Conversation State Model v1.1 (AMENDED — READY TO LOCK)

**Status:** v1 amended per stress-test review (`docs/STATE_MODEL_V1_REVIEW.md`, A1–A10) + 3 owner rulings applied. **Ready to lock pending owner sign-off.** Design only — no implementation.
**Predecessor:** `docs/ARCHITECTURE_SNAPSHOT.md` (Phase 1 audit). **Review:** `docs/STATE_MODEL_V1_REVIEW.md`.
**Next (after lock):** Phase 3 contracts → Phase 4 evaluation design → Phase 5 implementation.

---

## 0. Change log (this revision)

| # | Amendment | Applied as |
|---|---|---|
| A1 | Memory needs identity | Owner ruling: **anonymous device-scoped ID**. Keying contract added to §4.5; implementation touchpoints flagged, deferred to Phase 3/5 |
| A2 | Perception position | Owner ruling: **fused perception** = Phase 3 starting hypothesis, **validation-gated** (latency/quality prototype must pass before contracts lock); serial stage demoted to fallback (§3.3, §4.9) |
| A3 | Boundary violation | Cut `user_energy_trend` + `winding_down_signals` from Conversation State; added explicit rule: **phase is derived** (Mode + trajectory + duration), Conversation State = measurements only (§4.4) |
| A4 | Untestable field | Cut Thread `importance` float; promotion keys off `open_loops` + recurrence counts (§4.3) |
| A5 | Taxonomy surface | Cut Emotion `secondary` label (§4.2) |
| A6 | Always-null field | Cut Turn `pauses` (§4.1) |
| A7 | Scope discipline | Safety scaffold reduced to boundary §9 minimum: `self_harm`, `harm_to_others`, `other_flagged`; `abuse_victim` moved to safety-investigation scope (§4.6) |
| A8 | Static rule | Cut Policy `language_style` (persona prompt rule, not per-turn) (§4.8) |
| A9 | Missing core scenarios | Added **S17 non-speech distress** (acoustic-only turn type + handling rule) and **S18 disengagement/idle** (no question loops, open-door responses) (§2, §4.1, §4.8) |
| A10 | Honesty correction | Acoustic evidence: weights ≤ 0.2, **within-session-relative only** (browser AGC distorts absolute levels `[code:App.tsx:48]`); scenario 8 reclassified **partially supported — Phase 4 decision** (§3.1, §4.2) |

**Owner rulings recorded (this revision):**
1. **Safety resources: voice-only for MVP.** Escalation delivers supportive speech + spoken resources (memorable, repeatable phrasing). Frontend resource card = **OUT OF SCOPE / FUTURE CONSIDERATION** (revisit post-Phase 7). Accepted MVP limitation: resources reach the user only by ear.
2. **Identity: anonymous device-scoped ID** for cross-session continuity. Contract defined in §4.5-A1; **no code this pass**.
3. **Perception: fused** as Phase 3 starting hypothesis — **must pass a latency/quality validation prototype before Phase 3 contracts lock**; if validation fails, serial (or one-turn-lagged) is the documented fallback.

---

## 1. Design principles (unchanged from v1)

- **P1 — LLM is not the state engine.** LLM makes per-turn perception proposals; deterministic code owns transitions, hysteresis, decay, corrections.
- **P2 — Evidence → Estimate → Confidence** on every estimate; evidence typed by channel (`transcript`, `acoustic`, `history`, `user_correction`); no single signal is ground truth.
- **P3 — History vs State vs Memory are separate stores** (§8): History = verbatim log; State = current relevance window; Memory = selective, cross-session, criteria-gated.
- **P4 — Validate the emotion, not the accusation** (§6): `user_interpretation` stored separately from `emotion_estimate`.
- **P5 — Safety overrides everything** (§9): risk state pre-empts mode/policy; taxonomy provisional until investigation locks it.
- **P6 — No arbitrary objective thresholds** (§5): numeric routing parameters labeled internal, calibration-pending.
- **P7 — Graceful degradation:** every state has a default; perception failure degrades to "neutral listening."
- **P8 — Hinglish is first-class:** raw transcript stored verbatim; estimators consume Roman-Hinglish directly.

## 2. Scenario demands matrix (18 scenarios — v1's 16 + A9's 2)

| # | Scenario | Model support (post-amendment) |
|---|---|---|
| 1 | Simple venting | default VENT; `user_need=be_heard`; acknowledge + invite; advice locked out |
| 2 | Multiple topics | thread list, `active_thread` pointer, per-thread entities/events |
| 3 | Topic switch & return | `status: paused`; return-detection; `last_active_turn` for references |
| 4 | Rising anger | trajectory from 5-estimate ring; policy reacts to trajectory |
| 5 | Falling intensity | wind-down moves; hysteresis prevents flapping |
| 6 | Sarcasm | channel-conflict `incongruence` flag; confidence drops; no literal reading |
| 7 | Hinglish/code-switching | `turn.language`; estimators Hinglish-native; verbatim storage |
| 8 | Same words, different delivery | **PARTIALLY SUPPORTED (A10)**: transcript-dominant evidence; acoustic channel relative-only, weight ≤ 0.2; full support deferred to Phase 4 decision (prosody work = OPTIONAL/NON-CORE) |
| 9 | Ambiguous emotion | low confidence → no labels; clarify/invite only |
| 10 | User corrects interpretation | `user_correction` overrides (highest weight); optional preference memory |
| 11 | Advice after venting | `advice_requested` → VENT→ADVICE with recorded trigger |
| 12 | Explicitly no advice | `interaction_preferences`; `avoid` composes from them |
| 13 | Interruption/barge-in | `interrupted_agent_response` record; no full repeats; resume briefly |
| 14 | Reconnection/new session | Memory seeds fresh state — **now keyed via device-scoped ID (A1)** |
| 15 | Recurring issue/person | relationship memory (occurrences/sessions) — keyed via A1 |
| 16 | Safety-sensitive statements | two-tier: clarify-first (figurative) vs immediate high-risk path; overrides all |
| **17** | **Non-speech distress (crying/sighing/silence-while-upset)** | **NEW (A9)**: `turn_type: acoustic_only` — VAD/energy signals without valid transcript; policy = gentle acknowledgment/invitation; never "I didn't catch that"; safety screen still applies |
| **18** | **Disengagement (long silence, one-word answers)** | **NEW (A9)**: idle handling — no consecutive question loops; open-door responses ("here whenever you want"); session idles without state churn |

## 3. Shared contracts

### 3.1 Evidence record
```json
{"id": "ev_014", "channel": "transcript|acoustic|history|user_correction",
 "source": "stt_text|utterance_rms|llm_perception|user_said",
 "value": "<quote or measurement>", "interpretation": "string", "weight": 0.0, "turn": 12}
```
**A10 rule:** `acoustic`-channel weights ≤ 0.2; values are **within-session-relative only** (browser AGC normalizes absolute levels — cross-session/mic comparison invalid). `user_correction` always outranks all other channels.

### 3.2 Estimate envelope
```json
{"value": ..., "confidence": 0.0, "evidence_ids": ["ev_013"], "updated_at_turn": 12,
 "method": "llm_perception|rule|derived", "provisional": true}
```
`provisional: true` until Phase-4 calibration.

### 3.3 Update-trigger map (A2/A9 amended)

| Pipeline event (all exist today `[code]`) | State updates permitted |
|---|---|
| `session_start` | create Turn/Conversation/Thread state; **load Memory via device-scoped ID (A1)**; seed open loops; Safety reset |
| speech buffered (energy gate passed) | Turn State: acoustic evidence (duration/RMS/peak/rate) |
| STT accepted / rejected **or no transcript (acoustic_only/idle, A9)** | Turn State: transcript + language + confidence, **or turn-type record** |
| perception proposals — **fused into the response call (A2/owner ruling 3); Phase 3 hypothesis, validation-gated; fallback = serial stage** | proposals for Emotion/Thread/Safety/Memory-candidates — proposals only |
| state updater (deterministic) | applies corrections/hysteresis/decay → commits all dimensions |
| LLM completed | Conversation State counters; History append |
| response interrupted (`CancelledError` exists `[code]`) | Turn State: interrupted response record; ledger: move invalidated |
| idle timeout (A9) | Turn State: idle record; Policy: no-question open-door response |
| `session_end` (hook **does not exist yet** — Phase 3 verify `[ASSUMPTION: ctx.add_shutdown_callback]`) | Memory commit evaluation; session state discarded |

**Perception position note:** fused means perception fields ride in the same model call as the response (structured head + prose). If the Phase 3 validation prototype fails on latency or JSON reliability, documented fallbacks: serial stage (costlier) or one-turn-lagged emotion/thread updates (safety cannot lag — it stays in the main call in every variant).

## 4. State specifications

### 4.1 Turn State
- **Purpose:** everything about the current user turn — what was said, how, and what happened to the agent's previous response.
- **Schema (A6/A9 amended):**
```json
{"turn_id": 12, "started_at": "ts", "ended_at": "ts",
 "turn_type": "speech|acoustic_only|idle",
 "stt": {"text_raw": "…", "language": "hinglish|en|hi|other", "accepted": true,
         "reject_reason": null, "no_speech_prob": 0.01, "avg_logprob": -0.31},
 "acoustic": {"duration_ms": 4200, "rms": 2100, "peak": 9100, "est_words_per_sec": 2.4},
 "barge_in": {"agent_was_speaking": true, "ms_since_agent_end": null},
 "interrupted_agent_response": {"response_id": "R11", "spoken_text": "…", "completed": false},
 "user_correction": {"target": "emotion_estimate|topic|fact", "previous": "anger",
                     "user_assertion": "not angry, just tired", "turn": 12}}
```
- **Sources:** existing STT record; existing per-utterance energy metrics (duration/RMS/peak — computed today `[code:main.py]`); derived `est_words_per_sec`; existing interruption flags.
- **Update trigger:** one record per turn, including `acoustic_only` (energy gate passed, no valid transcript — A9/S17) and `idle` (A9/S18); frozen at response completion/interruption.
- **Persistence:** session only; full copy already lands in History/log.
- **Expiration:** never within session.
- **Confidence/evidence:** inherits STT confidence features; acoustic-only turns carry no transcript evidence by definition.
- **Dependencies:** feeds all other dimensions.
- **Failure modes:** STT garbage → existing validity gates reject before state churn; segments[0]-only confidence = coarse for long utterances (Snapshot §7); acoustic-only turns must not be dropped silently — they are evidence (S17).
- **Evaluation:** existing per-turn metrics continue; add turn-integrity checks (no lost turns between SPEECH_ENDED and state write; acoustic_only turns correctly classified).

### 4.2 Emotional State
- **Purpose:** current estimated emotional condition with evidence and direction — never truth.
- **Schema (A5/A10 amended):**
```json
{"primary": "anger_frustration|sadness|anxiety|overwhelm|loneliness_hurt|guilt_shame|relief|neutral_unclear",
 "valence": "negative|neutral|positive",
 "intensity": {"ordinal": 1, "scale_note": "1-5 provisional; calibration pending (§5)"},
 "trajectory": "rising|stable|falling|fluctuating",
 "incongruence": {"suspected": false, "channels_in_conflict": [], "note": "sarcasm etc."},
 "confidence": 0.62, "evidence_ids": [], "user_correction": null, "updated_at_turn": 12,
 "recent_estimates": [{"turn": 10, "primary": "anger_frustration", "ordinal": 3}]}
```
- **Sources:** transcript semantics (LLM perception, fused per A2); acoustic channel from Turn State (**weight ≤ 0.2, relative-only, A10**); History; user corrections; active thread context. Pitch/prosody/pauses: not computed — omitted.
- **Update trigger:** per accepted turn (+ acoustic_only turns may update via acoustic/history evidence alone, capped confidence); deterministic rules: correction override, conflict detection, trajectory over 5-estimate ring.
- **Persistence:** session; only aggregates may later enter Memory.
- **Expiration:** ring = 5 turns; estimate decays toward `neutral_unclear` after 3 turns without corroboration.
- **Confidence/evidence:** transcript-only ⇒ ≤ 0.5; with acoustic corroboration ⇒ up to 0.7; **acoustic-only ⇒ ≤ 0.3 (A10)**; correction present ⇒ 0.95, value = user's assertion; channel conflict ⇒ `incongruence.suspected` + ≤ 0.4.
- **Dependencies:** Turn State, Thread State, Safety State, Response Policy.
- **Failure modes:** wrong-label overconfidence → evidence requirements + no-label policy at low confidence; Hinglish misreading → eval set must over-represent; scenario 8 remains partially supported until Phase 4 rules on prosody work.
- **Evaluation:** human-labeled set (§5): ordinal-intensity MAE, label agreement, calibration/reliability, inter-rater ceiling; subsets: sarcasm, Hinglish, low-intensity, ambiguous, **acoustic-only turns**.

### 4.3 Topic/Thread State
- **Purpose:** track parallel people/events/issues; support returns (§7).
- **Schema (A4 amended):**
```json
{"threads": [{"id": "T1", "gist": "manager overloading team", "status": "active|paused|closed",
   "entities": [{"name": "Rohit", "role": "manager", "relationship": "boss"}],
   "events": ["weekend work", "deadline moved twice"],
   "open_loops": ["waiting for manager's reply"],
   "emotion_link": "anger_frustration", "first_turn": 2, "last_active_turn": 12}],
 "active_thread": "T1",
 "return_event": {"thread": "T1", "detected_turn": 12, "cue": "reference phrase"}}
```
- **Sources:** perception proposals; Turn State text; History coreference.
- **Update trigger:** per turn: new/shift/return rules; inactive 10+ turns → `closed` (internal parameter). **Memory promotion keys off `open_loops` and recurrence (entity reappearance across sessions), not a numeric importance score (A4).**
- **Persistence:** session; promotion candidates to Memory.
- **Expiration:** as above; dies with session unless promoted.
- **Confidence/evidence:** fuzzy entity matching stores confidence; unresolved references spawn low-confidence threads, never wrong merges.
- **Dependencies:** Emotional State, Memory, Response Policy.
- **Failure modes:** thread thrash → switch requires ≥2 consecutive turns of evidence or explicit marker; over-merging entities → conservative match; missed subtle returns → entity + shared-event cues.
- **Evaluation:** entity/thread recall & precision vs annotated multi-topic conversations; return-detection accuracy; thrash rate.

### 4.4 Conversation State
- **Purpose:** session-level measurements and bookkeeping. **Measurements only (A3)** — feelings live in Emotion State, decisions in Policy.
- **Schema (A3 amended):**
```json
{"session_id": "…", "started_at": "ts", "turn_count": 12,
 "phase": "opening|venting|winding_down|closing",
 "phase_derivation_inputs": {"mode": "VENT", "trajectory": "falling", "session_minutes": 22,
                              "user_initiated_close": false},
 "agent_behavior_ledger": {"questions_this_conversation": 3, "questions_last_2_turns": 1,
   "advice_given": 0, "last_move": "encourage_continuation"},
 "mode_history": ["VENT", "VENT", "ADVICE"],
 "reconnect": {"is_reconnect": true, "memory_seeded": ["T1 open loop", "preference: no advice"]}}
```
- **Derivation rule (A3):** `phase` is **computed from** Mode + Emotion trajectory + session duration + explicit close signals. It is never an independent estimate and never stores emotional content.
- **Sources:** all dimensions (derived); History counters; Memory read-view (reconnect).
- **Update trigger:** per turn; on mode changes; at session start.
- **Persistence:** session only.
- **Expiration:** whole document at session end.
- **Confidence/evidence:** deterministic bookkeeping.
- **Dependencies:** everything; primary pacing input to Policy.
- **Failure modes:** only fully-spoken responses update `advice_given`/`last_move` (interrupted = not counted); stale phase → falls back to `venting` after timeout; **energy/feelings questions go to Emotion State, never here (A3 rule)**.
- **Evaluation:** scenario regression: pacing respected, phase transitions correct, reconnect seeding correct.

### 4.5 Memory State
- **Purpose:** selective cross-session persistence (§8) — the only state that survives.
- **Keying contract (A1, owner ruling 2):** memories key to an **anonymous device-scoped ID** — a client-generated random UUID persisted in browser localStorage, passed at token request and bound to the participant identity. No PII, no account, no cross-device sync. **"Start fresh" escape hatch:** user-facing reset clears the ID (fresh identity). *Boundary note (flagged, not implemented):* wiring this touches frontend + token server in Phase 5 — approved direction, code only after Phase 3 contract lock.
- **Schema:**
```json
{"owner_id": "device-uuid",
 "episodic": [{"id": "E1", "date": "…", "thread_gist": "manager workload", "event": "…",
               "emotional_context": "high frustration", "salience": 0.8}],
 "semantic": [{"fact": "user's manager is named Rohit", "confidence": 0.9, "source": "turn 2"}],
 "relationship": [{"entity": "Rohit (manager)", "pattern": "recurring frustration source",
                   "occurrences": 3, "sessions": 2, "last_seen": "…"}],
 "preferences": [{"rule": "no advice unless explicitly asked", "origin": "explicit user statement",
                  "scope": "persistent", "set_turn": 8,
                  "supersedes": null}],
 "write_candidates": [{"payload": "…", "criterion_hit": "explicit|salient|recurrent|corrective",
                        "status": "pending|committed|rejected"}]}
```
- **Sources:** thread promotion; entity extraction; user statements; corrections.
- **Update trigger:** `write_candidates` during session; **commit at session end** (immediate for explicit statements/preferences). Read: session start (seed) + on-demand thread context.
- **Persistence:** durable store, keyed by `owner_id`. Storage engine choice = Phase 3 (SQLite recommended as starting point, per v1 D4).
- **Expiration:** `last_seen`/`occurrences` tracked; preferences persist until superseded/withdrawn (**supersession rule added per review §4**: newer explicit preference overrides older; withdrawals recorded, not deleted — audit trail); episodic decays by salience/age; risky content never stored verbatim.
- **Confidence/evidence:** every entry records origin; inferred facts need ≥2 turns or explicit statement; raw transcripts never stored by default.
- **Dependencies:** Thread State, Emotional State, Safety (handling records only, never risky content verbatim), Conversation State (reconnect seeding), **device-scoped ID (A1)**.
- **Failure modes:** lost/cleared localStorage = new identity (memory orphaned — acceptable, documented); memory poisoning (joking false facts) → criteria gate + human-reviewable queue in early builds; wrong entity link → conservative matching + eval audit; privacy → local-first, no cloud sync v1; **continuity is opt-out via start-fresh (review assumption #5)**.
- **Evaluation:** annotated multi-session corpus: memory-judgment precision/recall vs human labels; reconnect usefulness rated; user-correction rate.

### 4.6 Safety/Risk State
- **Purpose:** detect and route risk; override normal behavior when required (§9). First-class.
- **Schema (A7 amended; owner ruling 1 applied):**
```json
{"risk_level": "none|low|elevated_distress|high_risk",
 "categories": {"self_harm": {"present": false, "confidence": 0.0, "evidence_ids": []},
                "harm_to_others": {"present": false, "confidence": 0.0, "evidence_ids": []},
                "other_flagged": {"present": false, "note": null}},
 "override_active": false, "last_flagged_turn": null,
 "resource_delivery": "voice_only_mvp",
 "handling_log": [{"turn": 12, "level": "elevated_distress", "action": "supportive_no_advice"}],
 "taxonomy_version": "provisional-v0 — LOCK PENDING DEDICATED INVESTIGATION (§9)"}
```
- **Sources:** perception proposals (Hinglish-aware semantic screen — runs every turn in the fused call per A2); context (intensity + trajectory); History; acoustic extremes (relative-only, A10).
- **Update trigger:** every accepted turn (and acoustic_only turns — crying can be a risk signal, S17); de-escalation requires sustained low-risk turns (cooldown, internal parameter).
- **Persistence:** handling_log summary to Memory as handling record (never risky content verbatim).
- **Expiration:** override clears after sustained safe turns; categories reset per evaluation.
- **Confidence/evidence:** figurative/ambiguous ("this is killing me") ⇒ clarify-first tier; explicit unambiguous statements ⇒ immediate high-risk path regardless of confidence.
- **Resource delivery (owner ruling 1):** **voice-only for MVP** — high-risk path = calm supportive speech + spoken, repeatable, memorable resource mention; no frontend dependency (consistent with Phase-7 ordering). Frontend resource card = **OUT OF SCOPE / FUTURE CONSIDERATION**. Accepted limitation: user may be in crisis while not listening closely; mitigation = repeat calmly across turns, never dump a list once.
- **Dependencies:** overrides Mode + Policy; consumes Emotion; writes Conversation ledger.
- **Failure modes:** false negatives (worst case) → recall-prioritized tuning, layered checks, safety-weighted eval set; false positives → clarify-first tier; repetitive interventions → cooldown + varied phrasing; **taxonomy gaps → investigation scope now explicitly includes: third-party harm disclosures (review §4), minors (no age gate exists anywhere in the system), India-jurisdiction crisis resources (spoken-friendly, memorable), plus `other_flagged` discoveries**.
- **Evaluation:** dedicated labeled set (consented/synthetic): sensitivity prioritized (FN primary metric), figurative-language subset, end-to-end behavioral drills (escalation fires, resources spoken, no advice).

### 4.7 Interaction Mode
- **Purpose:** the conversational contract for "what kind of exchange is this right now."
- **Schema:** `{"current": "VENT|REFLECT|ADVICE|CALM|CLOSING", "since_turn": 10, "entered_via": "user_request|inferred|safety|system", "previous": "VENT"}`
- **Sources:** deterministic transitions over Emotional/Conversation/Safety state + explicit requests.
- **Update trigger:** per turn; hysteresis: inferred transitions need 2 consecutive supporting turns (internal parameter); explicit requests and safety transition immediately.
- **Persistence:** session (`mode_history`).
- **Expiration:** resets to VENT (or CALM if safety) at session start; reconnect re-entry via memory is suggestion-only, never auto-ADVICE.
- **Confidence/evidence:** mode is a decision; transition signals logged for tuning.
- **Dependencies:** Safety (overrides), Emotional trajectory, Thread closure cues, user requests.
- **Failure modes:** flapping → hysteresis; stuck-in-VENT → explicit-request escape + invitation move after threshold; premature ADVICE → default deny.
- **Evaluation:** scenarios 11, 12, 16, 5, 4: correct entry/exit, no flapping, request-response latency = 1 turn.

### 4.8 Response Policy (derived output layer)
- **Purpose:** the instruction contract the LLM receives — decided before generation.
- **Schema (A8 amended; A9 rules added):**
```json
{"mode": "VENT", "response_goal": "encourage_continuation",
 "user_need": "be_heard",
 "emotion_reflection": {"label_to_use": "frustration", "ok_to_name": true,
                        "user_interpretation_neutral": true},
 "advice": {"requested": false, "permission": "not_granted"},
 "avoid": ["advice", "escalation", "judgement", "endorsing_accusations",
           "repeating_interrupted_content", "excessive_questions"],
 "do": ["acknowledge emotion", "invite continuation"],
 "pacing": {"max_sentences": 2, "max_questions": 1, "consecutive_question_turns_left": 1},
 "turn_type_handling": {"acoustic_only": "gentle presence acknowledgment; invite sharing; never claim mishearing",
                         "idle": "open-door response; zero questions"},
 "safety_override": null,
 "delivery_hint": null}
```
- **Derivation rules (deterministic):**
  1. Safety active → safety template (CALM; no advice; spoken resources per level; clarify-first if uncertain; voice-only delivery per owner ruling 1).
  2. Explicit user request → honor (ADVICE on request; NO_ADVICE preference locks advice out).
  3. Mode rules: VENT → acknowledge + invite; intensity ≥4 or rising → de-escalation, zero questions unless needed; confidence below internal threshold → no emotion labels (scenario 9); interrupted prior response → acknowledge + resume briefly, no full repeat (scenario 13).
  4. **Turn-type rules (A9):** `acoustic_only` → S17 handling; `idle` → S18 handling (zero questions, open door).
  5. `avoid` composes from mode rules + preferences + safety + pacing ledger.
- **Sources:** all seven dimensions.
- **Update trigger:** computed fresh each turn; never cached.
- **Persistence:** logged per turn (auditability), not persisted as state.
- **Confidence/evidence:** `ok_to_name` requires confidence above internal threshold and no incongruence; `user_interpretation_neutral` always true under P4 unless policy permits agreement.
- **Dependencies:** everything.
- **Failure modes:** policy ignored by LLM → rendered as hard rules + anchors (Phase 3); over-constrained → budgets are ceilings not targets; derivation bug → policy + state snapshot logged every turn (regression-testable).
- **Evaluation:** the 18 scenarios are golden regression cases: policy snapshot asserted + human rubric (validate-don't-agree, advice leakage, question-rate, S17/S17-adjacent handling).

### 4.9 What the LLM receives (context contract sketch — Phase 3 finalizes)
LLM input = [system prompt (persona + safety behavior)] + [**fused perception+response call structure (A2)**: structured head (proposals) then prose, single call] + [policy block verbatim] + [memory read-view (compact)] + [thread summaries] + [bounded recent-turns window (~6, D6)]. Replaces today's unbounded raw-history dump (Snapshot §4). Fallback if fusion fails validation: two-call variant (safety screen + response) — same contracts, different transport.

---

## 5. Scope check (per §16 — post-amendment)

| Item | Status |
|---|---|
| 7 dimensions + Response Policy, as amended | IN SCOPE (Phase 2 — this doc) |
| Fused-perception validation prototype | IN SCOPE as Phase 3's first task (decision gate) — not now |
| Device-scoped ID contract | IN SCOPE Phase 3; **its implementation (frontend + token server touchpoints) = Phase 5, after contract lock** — flagged per boundary rule |
| Voice-only safety resources | IN SCOPE (MVP constraint); frontend resource card = **OUT OF SCOPE / FUTURE CONSIDERATION** |
| Safety taxonomy investigation (incl. third-party harm, minors, India resources) | IN SCOPE as Phase 2.5 investigation — findings before any lock of §4.6 taxonomy |
| Prosody/pitch/pause extraction | OPTIONAL / NON-CORE — Phase 4 decision (scenario 8) |
| State engine implementation | **OUT OF SCOPE** this pass |
| Streaming STT, vector DB, TTS upgrade, UI/UX | **OUT OF SCOPE / FUTURE CONSIDERATION** |
| Log-retention/encryption policy for verbatim transcripts (review §9 finding) | **PENDING DECISION — flagged separately, not incorporated** (operational privacy, pre-real-users) |

## 6. Decision record

| # | Decision | Status |
|---|---|---|
| D1 | 8-label emotion taxonomy + valence | Approved as v1 (revisit with eval data) |
| D2 | Ordinal 1–5 intensity, provisional | Approved as provisional |
| D3 | Memory commit at session end (batch) + immediate for explicit statements | Approved |
| D4 | SQLite as Phase 3 storage starting point | Approved as starting recommendation |
| D5 | 4-level risk scaffold + clarify-first tier; taxonomy investigation = Phase 2.5 | Approved |
| D6 | Bounded LLM history window (~6 turns) | Approved |
| D7 | Internal parameters (5-turn ring, 2-turn hysteresis, 10-turn thread close) | Approved as tunable starting values |
| O1 | **Safety resources: voice-only for MVP** | **Owner ruling — applied (§4.6)** |
| O2 | **Identity: anonymous device-scoped ID** | **Owner ruling — applied (§4.5)** |
| O3 | **Perception: fused, validation-gated** | **Owner ruling — applied (§3.3, §4.9)** |

## 7. Risks (updated)

- **R1 — LLM-as-perceiver:** unchanged; mitigations intact (evidence-typing, confidence caps, deterministic overrides, Phase-4 labeled set).
- **R2 — Latency (updated for A2):** fused perception avoids a serial hop but adds structured-head tokens to the response call. **Validation gate is mandatory before Phase 3 contracts lock**; documented fallbacks: serial or one-turn-lagged (safety never lags). Interacts with the 5 s TTS fallback budget — re-test in Phase 3 (review assumption #7).
- **R3 — Safety false negatives:** top risk; scaffold recall-biased; taxonomy + eval data still pending (Phase 2.5).
- **R4 — Over-engineering before data:** reduced by A3–A8 cuts; remaining provisional fields identified for eval.
- **R5 — Hinglish judgment quality:** eval set must over-represent code-switching.
- **R6 — (new) Identity fragility:** localStorage loss orphans memories (acceptable, documented); multi-device users get fragmented histories in v1.

---

## 8. Final stress-test pass (pre-return, per instructions)

- **18/18 scenarios traced against amended schemas** — all supported; S8 honestly partial; S17/S18 now first-class.
- **Cut-impact check (A3–A8):** no scenario or rule consumed any removed field; phase derivation runs on Mode+trajectory+duration; thread promotion on open_loops+recurrence; persona prompt already covers language style.
- **Snapshot cross-check:** Turn State acoustic fields = exactly what `main.py` computes today; interruption records match existing truncated-storage behavior; rejected-turn retention matches `finally: log_turn`; trigger map keys only on existing events, except `session_end` (flagged, Phase 3 verify).
- **No new contradictions found.** Carry-forward items (all previously flagged, none new): `session_end` hook existence `[ASSUMPTION]`; `clear_queue` availability `[ASSUMPTION]`; Gemini fused-JSON reliability `[ASSUMPTION — validation gate]`; log-privacy policy pending; safety data sourcing/ethics pending; device-ID implementation touchpoints (frontend/token server) deferred to Phase 5 with approval.
- **Scope rule honored:** no scope expansion discovered that was incorporated; the one borderline item (log-retention policy) is flagged in §5 as PENDING DECISION, not folded in.

## 9. Ready-to-lock confirmation

**The model (v1.1) is READY TO LOCK.** All amendments applied exactly as reviewed; owner rulings O1–O3 incorporated; final stress-test passed with no new findings. On your lock: Phase 3 (data contracts, beginning with the fused-perception validation prototype and the device-ID contract) — **not before**.

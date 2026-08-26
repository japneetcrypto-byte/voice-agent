# Aiva — Emotional Conversation State Model v1 (PROPOSAL — NOT LOCKED)

**Status:** Phase 2 deliverable per locked boundary §17. Design only — **no implementation**.
**Predecessor:** `docs/ARCHITECTURE_SNAPSHOT.md` (Phase 1 audit, commit `a86b6af`) — referenced as "Snapshot".
**Awaiting:** review → approval/lock → Phase 3 (contracts) → Phase 4 (evaluation design).

---

## 0. Facts & Findings recap (from audit; full detail in Snapshot)

- **Facts `[code]`:** custom LiveKit pipeline; per-turn dict already logs stt/llm/tts metrics + interruption flags; energy metrics (RMS/peak/duration/mean-abs) already computed per utterance; `recent_agent_text` echo buffer; ephemeral unbounded message history; one static system prompt; batch STT; barge-in only after full STT; zero emotion/intent/memory/safety logic.
- **Finding:** the only real "state precursors" are the turn debug dict, message history, and the echo buffer. The seven target dimensions have no first-class representation. Acoustic evidence available at zero new infra: duration, RMS, peak, derived speaking-rate. Pauses/prosody/pitch: not computed `[gap]`.
- **Finding `[live]`:** TTS stack currently = Fish `s2.1-pro-free` @44.1 kHz + EdgeTTS fallback; no reliable delivery control (tone/pace/energy) is available → §11 delivery attributes stay **FUTURE CONSIDERATION** in v1.

## 1. Design principles (derived from the boundary doc)

- **P1 — LLM is not the state engine.** LLM makes *per-turn perception proposals* (structured JSON: emotion/thread/safety candidates with evidence). A deterministic updater (code) owns *state transitions*, hysteresis, decay, corrections. Same split as §3's pipeline.
- **P2 — Evidence → Estimate → Confidence** on every estimate; evidence is typed by channel (`transcript`, `acoustic`, `history`, `user_correction`); no single signal is ground truth (§4).
- **P3 — History vs State vs Memory are separate stores** (§8): History = verbatim log (complete); State = current relevance window; Memory = selective, cross-session, criteria-gated.
- **P4 — Validate the emotion, not the accusation** (§6): the model stores `user_interpretation` (their claim about the world) separately from `emotion_estimate` (their felt state). Policy may reflect the latter without endorsing the former.
- **P5 — Safety overrides everything** (§9): risk state pre-empts mode/policy; taxonomy is provisional until a dedicated investigation locks it.
- **P6 — No arbitrary objective thresholds** (§5): all numeric routing thresholds are labeled *internal parameters, subject to Phase-4 calibration* — never "correct values".
- **P7 — Graceful degradation:** every state has a default; perception failure must degrade to "neutral listening", never to silence or wrong-confidence behavior.
- **P8 — Hinglish is a first-class case, not an edge case** (persona already Roman-Hinglish `[code]`): raw transcript stored as-is; no translation step; estimators must consume Hinglish directly.

## 2. Scenario demands matrix (§17's required scenario pass)

| # | Scenario | What it forces the model to support |
|---|---|---|
| 1 | Simple venting | default mode VENT; `user_need=be_heard`; policy: acknowledge + invite continuation; advice locked out |
| 2 | Multiple topics in one rant | Thread list (N>1), `active_thread` pointer, per-thread entities/events/summary |
| 3 | Topic switch & return | thread `status: paused`; return-detection trigger; `last_active_turn` for reference resolution ("like I said about my manager") |
| 4 | Rising anger | trajectory derived from last N emotional estimates; policy reacts to trajectory, not just level |
| 5 | Falling intensity | trajectory `falling` → wind-down moves; hysteresis prevents mode flapping |
| 6 | Sarcasm | channel-conflict flag (`transcript` says fine, `acoustic`/context says not); confidence drops; policy avoids literal reading |
| 7 | Hinglish/code-switching | `turn.language`; estimators handle Roman-Hinglish; raw text preserved verbatim |
| 8 | Same words, different delivery | evidence must be channel-tagged; acoustic channel (energy/rate) distinct from semantics; missing acoustic channel → estimate confidence capped |
| 9 | Ambiguous emotion | low confidence → policy forbids naming a specific emotion; goal becomes clarify/invite, not label |
| 10 | User corrects interpretation | `user_correction` overrides estimates (highest evidence weight); optional preference memory ("don't label me angry") |
| 11 | Advice after venting | `advice_requested` (explicit) / mode transition VENT→ADVICE with recorded trigger |
| 12 | Explicitly no advice | `interaction_preferences` (session + optionally memory); `avoid` list composes from preferences |
| 13 | Interruption/barge-in | Turn State records `interrupted_agent_response`; policy: no full repeats, resume gracefully; truncated text already stored with marker `[code]` |
| 14 | Reconnection/new session | session state dies; Memory seeds fresh Conversation State (open loops, people, preferences) — this is Memory's core purpose |
| 15 | Recurring issue, same person | `relationship` memory: entity + pattern + occurrence/session counters; feeds context ("third time this month") and gentle reflection, only when policy permits |
| 16 | Safety-sensitive statements | Safety State pre-empts all; figurative speech ("this is killing me") handled via confidence + clarify-first, not instant hard escalation; high-risk path is fixed-response + resources, no advice |

## 3. Shared contracts

### 3.1 Evidence record (used everywhere)
```json
{"id": "ev_014", "channel": "transcript|acoustic|history|user_correction",
 "source": "stt_text|utterance_rms|llm_perception|user_said",
 "value": "<quote or measurement>", "interpretation": "string", "weight": 0.0, "turn": 12}
```
Weights are internal parameters (P6). `user_correction` evidence always outranks others (scenario 10).

### 3.2 Estimate envelope (used everywhere)
```json
{"value": ..., "confidence": 0.0, "evidence_ids": ["ev_013"], "updated_at_turn": 12,
 "method": "llm_perception|rule|derived", "provisional": true}
```
`provisional: true` until Phase-4 calibration locks scoring (§5).

### 3.3 Update-trigger map (pipeline event → allowed state writes)

| Pipeline event (exists today `[code]`) | State updates permitted |
|---|---|
| `session_start` | create Turn/Conversation/Thread state; load Memory; seed open loops; Safety reset |
| speech buffered (energy gate passed) | Turn State: acoustic evidence (RMS/peak/duration/rate) |
| STT accepted / rejected | Turn State: transcript + language + confidence features, or rejection record |
| perception proposal (new stage, post-STT) | proposals for Emotion/Thread/Safety/Memory-candidates — **proposals only** |
| state updater (deterministic) | applies corrections/hysteresis/decay → commits all dimension changes |
| LLM completed | Conversation State counters; History append |
| response interrupted (`CancelledError` exists `[code]`) | Turn State: interrupted response record; Conversation State: agent move invalidated |
| `session_end` / reconnect | Memory commit evaluation; session state discarded |

### 3.4 Session state document
One serializable `AivaSessionState` containing all in-session dimensions below + a read-view of Memory. Persisted per session as JSONL append (mirrors existing `logs/` pattern `[code]`) — crash-recovery is out of scope for v1.

---

## 4. State specifications

### 4.1 Turn State
- **Purpose:** everything about the current user turn — what was said, how, and what happened to the agent's previous response.
- **Schema:**
```json
{"turn_id": 12, "started_at": "ts", "ended_at": "ts",
 "stt": {"text_raw": "…", "language": "hinglish|en|hi|other", "accepted": true,
         "reject_reason": null, "no_speech_prob": 0.01, "avg_logprob": -0.31},
 "acoustic": {"duration_ms": 4200, "rms": 2100, "peak": 9100, "est_words_per_sec": 2.4,
              "pauses": null},
 "barge_in": {"agent_was_speaking": true, "ms_since_agent_end": null},
 "interrupted_agent_response": {"response_id": "R11", "spoken_text": "…", "completed": false},
 "user_correction": {"target": "emotion_estimate|topic|fact", "previous": "anger",
                     "user_assertion": "not angry, just tired", "turn": 12}}
```
- **Sources:** existing STT record, existing per-utterance energy metrics, existing interruption flags (all already computed `[code]`); `est_words_per_sec` = STT word count / duration (derived).
- **Update trigger:** one record per accepted/rejected turn; frozen when response completes or is interrupted.
- **Persistence:** session only; full copy already lands in History/log.
- **Expiration:** never expires within session (it *is* the atomic history unit).
- **Confidence/evidence:** inherits STT confidence features; rejected turns kept with reason (they matter for echo/false-turn tuning).
- **Dependencies:** feeds all other dimensions as evidence input.
- **Failure modes:** STT garbage upstream → validity gates (existing) reject before state churn; long multi-segment utterances mis-scored (known Snapshot issue) → treat `avg_logprob` as coarse only.
- **Evaluation:** existing per-turn metrics continue (Snapshot §6); add turn-integrity checks (no turn lost between SPEECH_ENDED and state write).

### 4.2 Emotional State
- **Purpose:** current estimated emotional condition with evidence and direction — never treated as truth.
- **Schema:**
```json
{"primary": "anger_frustration|sadness|anxiety|overwhelm|loneliness_hurt|guilt_shame|relief|neutral_unclear",
 "secondary": "label|null", "valence": "negative|neutral|positive",
 "intensity": {"ordinal": 1, "scale_note": "1-5 provisional; calibration pending (§5)"},
 "trajectory": "rising|stable|falling|fluctuating",
 "incongruence": {"suspected": false, "channels_in_conflict": [], "note": "sarcasm etc."},
 "confidence": 0.62, "evidence_ids": [], "user_correction": null, "updated_at_turn": 12,
 "recent_estimates": [{"turn": 10, "primary": "anger_frustration", "ordinal": 3}, "..."]}
```
- **Sources:** transcript semantics via LLM perception proposal; acoustic channel from Turn State (duration/RMS/peak/rate); History (prior estimates); user corrections; active thread context. Pitch/prosody/pauses: **not computed today** → omitted (v1), confidence is capped when only one channel is available (scenario 8).
- **Update trigger:** after each accepted turn, from perception proposal + deterministic rules (correction override, conflict detection, trajectory over `recent_estimates` ring buffer of 5).
- **Persistence:** session; only *aggregates* may later enter Memory (e.g., recurring pattern), never raw estimates.
- **Expiration:** `recent_estimates` ring 5 turns; trajectory recomputed every turn; estimate itself carries forward but decays toward `neutral_unclear` after 3 turns without corroborating evidence.
- **Confidence/evidence:** single-channel ⇒ confidence ≤ 0.5; correction present ⇒ confidence 0.95 and value = user's assertion (scenario 10); conflicting channels ⇒ `incongruence.suspected` + confidence ≤ 0.4 (scenario 6/8).
- **Dependencies:** Turn State (input), Thread State (context), Safety State (escalation consumer), Response Policy (consumer).
- **Failure modes:** wrong-label overconfidence → mitigated by evidence requirement + no-label policy at low confidence (scenario 9); Hinglish misreading → estimator contract requires Hinglish competence (test set must include it); ring buffer too short for slow burns → acceptable v1, revisit with data.
- **Evaluation:** human-labeled set (real or consented samples; §5): ordinal-intensity MAE, label agreement vs human raters, calibration/reliability curves, inter-rater agreement as ceiling; dedicated subsets: sarcasm, Hinglish, low-intensity, ambiguous. Labels/v1-taxonomy themselves provisional until that investigation.

### 4.3 Topic/Thread State
- **Purpose:** track parallel people/events/issues; support returns (§7).
- **Schema:**
```json
{"threads": [{"id": "T1", "gist": "manager overloading team", "status": "active|paused|closed",
   "entities": [{"name": "Rohit", "role": "manager", "relationship": "boss"}],
   "events": ["weekend work", "deadline moved twice"],
   "open_loops": ["waiting for manager's reply"],
   "emotion_link": "anger_frustration", "first_turn": 2, "last_active_turn": 12,
   "importance": 0.7}],
 "active_thread": "T1",
 "return_event": {"thread": "T1", "detected_turn": 12, "cue": "reference phrase"}}
```
- **Sources:** LLM perception proposal (entities/events/switch/return cues); Turn State text; History for coreference.
- **Update trigger:** per turn: new thread → append; topic shift → current→`paused`, new→`active`; return cue → reactivate (`return_event` recorded); thread inactive 10+ turns → `closed` (internal parameter).
- **Persistence:** session; threads with `importance` above threshold and/or open loops become Memory-write *candidates* (not automatic).
- **Expiration:** as above; everything dies with session unless promoted to Memory.
- **Confidence/evidence:** entity identity is fuzzy (nickname vs name) — matcher confidence stored; unresolved references spawn low-confidence threads rather than wrong merges.
- **Dependencies:** Emotional State (emotion_link), Memory (relationship/episodic promotion), Response Policy (avoid cross-thread confusion; allow deliberate return).
- **Failure modes:** thread thrash (rapid switching misdetected) → hysteresis: switch requires evidence from ≥2 consecutive turns or explicit marker; over-merging distinct people → entity merge only on strong match; losing subtle returns → return cues include entities + shared event references.
- **Evaluation:** multi-topic scripted + real conversations: entity/thread recall & precision vs annotated ground truth; return-detection accuracy; thread-thrash rate. Human-graded.

### 4.4 Conversation State
- **Purpose:** the session-level picture — phase, pacing, agent behavior bookkeeping.
- **Schema:**
```json
{"session_id": "…", "started_at": "ts", "turn_count": 12,
 "phase": "opening|venting|winding_down|closing",
 "agent_behavior_ledger": {"questions_this_conversation": 3, "questions_last_2_turns": 1,
   "advice_given": 0, "last_move": "encourage_continuation", "user_energy_trend": "falling"},
 "mode_history": ["VENT", "VENT", "ADVICE"],
 "reconnect": {"is_reconnect": true, "memory_seeded": ["T1 open loop", "preference: no advice"]}}
```
- **Sources:** all dimensions (derived), History counters, Memory read-view.
- **Update trigger:** per turn (counters/ledger), on mode changes (history), on session start (reconnect seeding).
- **Persistence:** session only.
- **Expiration:** whole document at session end (memory candidates already promoted separately).
- **Confidence/evidence:** derived bookkeeping — deterministic, high confidence by construction.
- **Dependencies:** everything; primary input to Response Policy pacing rules (e.g., max 1 question/turn, ≤2 consecutive question turns — internal parameters).
- **Failure modes:** ledger drift on interruptions (interrupted response counted as advice given?) → rule: only *fully spoken* responses update `advice_given`/`last_move`; stale phase after user goes quiet → phase falls back to `venting` after timeout.
- **Evaluation:** scenario suite regression (behavioral rubric): pacing rules respected, phase transitions sensible, reconnect seeding correct.

### 4.5 Memory State
- **Purpose:** selective cross-session persistence (§8) — the only state that survives.
- **Schema:**
```json
{"episodic": [{"id": "E1", "date": "…", "thread_gist": "manager workload", "event": "…",
               "emotional_context": "high frustration", "salience": 0.8}],
 "semantic": [{"fact": "user's manager is named Rohit", "confidence": 0.9, "source": "turn 2"}],
 "relationship": [{"entity": "Rohit (manager)", "pattern": "recurring frustration source",
                   "occurrences": 3, "sessions": 2, "last_seen": "…"}],
 "preferences": [{"rule": "no advice unless explicitly asked", "origin": "explicit user statement",
                  "scope": "persistent", "set_turn": 8}],
 "write_candidates": [{"payload": "…", "criterion_hit": "explicit|salient|recurrent|corrective",
                        "status": "pending|committed|rejected"}]}
```
- **Sources:** thread promotion (episodic), entity extraction (semantic/relationship), user statements (preferences), corrections (preferences/semantic).
- **Update trigger:** during session → `write_candidates` only; **commit evaluated at session end** (and for explicit statements, immediately). Read: at session start (seed Conversation State) and on-demand for active-thread context.
- **Persistence:** durable store. Storage engine choice = Phase 3 decision (contract above is engine-agnostic; SQLite/JSON file sufficient for v1 scale — recommendation in §8).
- **Expiration:** entries carry `last_seen`/`occurrences`; preferences persist unless user changes them; episodic decays by salience and age; nothing auto-expires in v1 (review at evaluation phase).
- **Confidence/evidence:** every entry records origin; inferred facts need ≥2 supporting turns or explicit statement; **raw transcripts are never stored by default** (§8).
- **Dependencies:** Thread State (promotion source), Emotional State (emotional context), Safety (never persist risky content verbatim; safety events stored as handling record, not content), Conversation State (reconnect seeding).
- **Failure modes:** over-persistence (creepy/loggy) → criteria-gated writes + session-end human-reviewable queue in early builds; under-persistence (forgets the manager) → recurrence tracking; wrong entity link across sessions → conservative matching, human audit during evaluation; privacy → local-first storage, no cloud sync in v1.
- **Evaluation:** annotated multi-session corpus: precision/recall of "should this be remembered?" judgments vs human labels; reconnect-seeding usefulness rated in scenario tests; explicit user-correction rate ("that's not what happened").

### 4.6 Safety/Risk State
- **Purpose:** detect and route risk; override normal behavior when required (§9). **First-class, not a response filter.**
- **Schema:**
```json
{"risk_level": "none|low|elevated_distress|high_risk",
 "categories": {"self_harm": {"present": false, "confidence": 0.0, "evidence_ids": []},
                "harm_to_others": {"present": false, "confidence": 0.0, "evidence_ids": []},
                "abuse_victim": {"present": false, "confidence": 0.0, "evidence_ids": []},
                "other_flagged": {"present": false, "note": null}},
 "override_active": false, "last_flagged_turn": null,
 "handling_log": [{"turn": 12, "level": "elevated_distress", "action": "supportive_no_advice"}],
 "taxonomy_version": "provisional-v0 — LOCK PENDING DEDICATED INVESTIGATION (§9)"}
```
- **Sources:** LLM perception proposal (semantic, Hinglish-aware); context (Emotional State intensity + trajectory); acoustic channel (extreme delivery shifts); History (sustained distress).
- **Update trigger:** re-evaluated **every accepted turn**; overrides set by deterministic rules from category+confidence; de-escalation requires sustained low-risk turns (cooldown, internal parameter) to prevent oscillation.
- **Persistence:** handling_log summary may persist to Memory as *handling record* (so future sessions are informed); sensitive content itself is not stored verbatim (§8/§9).
- **Expiration:** override clears after sustained safe turns; categories reset per evaluation.
- **Confidence/evidence:** two-tier response to uncertainty: figurative/ambiguous language ("this is killing me") ⇒ **clarify-first** gentle check within conversation, not instant hard escalation (scenario 16); explicit unambiguous statements ⇒ immediate `high_risk` path regardless of confidence.
- **Dependencies:** overrides Interaction Mode + Response Policy (precedence order: Safety > explicit user request > mode rules); consumes Emotional State; writes Conversation State ledger.
- **Failure modes:** false negative (missed risk) — the worst case; mitigation: recall-prioritized tuning, layered checks (semantic + intensity + trajectory), evaluation set weighted toward sensitivity; false positive (over-escalation at figurative speech) — mitigated by clarify-first tier; repetitive interventions — cooldown + varied phrasing; taxonomy gaps — explicit provisional label until investigation (per §9, taxonomy must be investigated before lock — recommended sources: established crisis-line triage frameworks; to be returned as findings before implementation).
- **Evaluation:** dedicated labeled set (consented/synthetic), sensitivity prioritized (FN rate primary metric, FP rate secondary), scenario suite incl. figurative-language cases, end-to-end behavioral drills (escalation path fires, resources shown, no advice).

### 4.7 Interaction Mode
- **Purpose:** the conversational contract for "what kind of exchange is this right now" (§2.7).
- **Schema:** `{"current": "VENT|REFLECT|ADVICE|CALM|CLOSING", "since_turn": 10, "entered_via": "user_request|inferred|safety|system", "previous": "VENT"}`
- **Sources:** deterministic transition rules over Emotional/Conversation/Safety state + explicit user requests.
- **Update trigger:** transitions evaluated per turn; **hysteresis**: inferred transitions require 2 consecutive turns of supporting signals (internal parameter); explicit requests and safety overrides transition immediately.
- **Persistence:** session (`mode_history` in Conversation State).
- **Expiration:** resets to VENT (or `CALM` if safety) at session start; reconnect may re-enter via memory signals only as *suggestion*, never auto-ADVICE.
- **Confidence/evidence:** mode itself is a decision, not an estimate — but the *signals* behind inferred transitions are logged for tuning.
- **Dependencies:** Safety (overrides), Emotional State (trajectory: sustained fall → possible CLOSING), Thread State (topic closure cues), user requests (highest non-safety precedence).
- **Failure modes:** flapping (VENT↔REFLECT ping-pong) → hysteresis; stuck-in-VENT when user clearly wants advice → explicit-request escape hatch + "would you like my take?" policy move after threshold; premature ADVICE → default deny, require request or invitation accepted.
- **Evaluation:** scenario suite (11, 12, 16, 5, 4): correct entry/exit, no flapping, request latency (turns to respond to explicit request = 1).

### 4.8 Response Policy (derived; not a "state" but the output layer, §10)
- **Purpose:** the instruction contract the LLM receives — decided *before* generation, from state (§2/§10).
- **Schema:**
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
 "safety_override": null,
 "language_style": "roman_hinglish_if_user_uses_it",
 "delivery_hint": null}
```
- **Derivation rules (deterministic, code-owned):**
  1. Safety active → policy = safety template (CALM; no advice; resources per level; clarify-first if uncertain).
  2. Else explicit user request → honor (ADVICE on request; NO_ADVICE preference → lock advice out).
  3. Else mode rules: VENT → acknowledge + invite; intensity ≥4 or trajectory rising → de-escalation moves, zero questions unless needed; ambiguity (confidence < internal threshold) → no emotion labels, open invitation only (scenario 9); interrupted prior response → acknowledge + resume *briefly*, no full repeat (scenario 13).
  4. Always compose `avoid` from: mode rules + active preferences + safety + pacing ledger.
- **Sources:** all seven dimensions.
- **Update trigger:** computed fresh each turn from committed state; never cached across turns.
- **Persistence:** logged per turn (auditability), not persisted as state.
- **Confidence/evidence:** `emotion_reflection.ok_to_name` requires Emotion confidence above internal threshold **and** no incongruence flag; `user_interpretation_neutral` always true under P4 unless user asserts it and policy permits agreement.
- **Dependencies:** everything.
- **Failure modes:** policy correct but LLM ignores it → prompt contract renders policy as hard rules + few-shot anchors (Phase 3); over-constrained policy yields robotic replies → pacing budgets are ceilings not targets; policy derivation bug → every policy logged with the state snapshot that produced it (regression-testable).
- **Evaluation:** the 16 scenarios become golden regression cases: policy snapshot asserted per scenario + end-to-end human rubric on generated responses (validate-don't-agree check, advice leakage check, question-rate check).

### 4.9 What the LLM receives (context contract sketch — Phase 3 will finalize)
LLM input = [system prompt (persona+safety behavior)] + [policy block (§4.8, verbatim)] + [memory read-view (compact: people, open loops, preferences)] + [thread summaries] + [recent raw turns window (last ~6 turns, existing behavior preserved where useful per §12)] — replacing today's unbounded raw history dump (Snapshot §4). This is the structural fix for §3: the LLM responds to *structured state*, history is a bounded reference, not the brain.

---

## 5. Scope check (per §16)

| Item | Status |
|---|---|
| 7 state dimensions + Response Policy design (this doc) | **IN SCOPE** (Phase 2) |
| Perception-stage design & data contracts (Phase 3) | IN SCOPE — next |
| Evaluation methodology definition (Phase 4) | IN SCOPE — next |
| v1 acoustic evidence = duration/RMS/peak/rate (already computed) | IN SCOPE, zero new infra |
| Pitch/prosody/pause extraction | **OPTIONAL / NON-CORE** — only if Phase 4 shows transcript-only emotion is insufficient |
| Voice delivery control (tone/pace/energy) | **OUT OF SCOPE / FUTURE CONSIDERATION** (§11 — blocked on TTS reliability investigation; field reserved in policy schema) |
| State engine implementation | **OUT OF SCOPE** this pass — requires model lock first (§17) |
| New infra (vector DB, streaming STT, new TTS model, UI/UX) | **OUT OF SCOPE / FUTURE CONSIDERATION** (§13 ordering) |
| Safety taxonomy finalization | IN SCOPE as *investigation*, before lock — not unilaterally definable |

## 6. Decisions needed from you to lock v1

| # | Decision | My recommendation |
|---|---|---|
| D1 | Emotion taxonomy (§4.2 primary set) | Approve the 8-label + valence set as v1; revisit only with evaluation data |
| D2 | Intensity: ordinal 1–5 provisional | Approve as *provisional* (P6); continuous scoring only after labeled-set calibration |
| D3 | Commit memory at session end (batch) vs per-turn | Session-end batch + immediate for explicit statements |
| D4 | Memory storage for v1 (Phase 3 preview) | Local SQLite (single file, zero infra) — decide formally in Phase 3 |
| D5 | Safety: approve provisional 4-level risk + clarify-first tier as *starting* scaffold, taxonomy investigation as its own Phase-2.5 task | Approve; investigation findings returned before any lock (§9) |
| D6 | History window for LLM (last ~6 turns) vs full history | Bounded window + summaries; unbounded context is a known cost/drift issue (Snapshot §7) |
| D7 | Ring-buffer/hysteresis internal parameters (5-turn trajectory, 2-turn transition hysteresis, 10-turn thread close) | Approve as starting values, all labeled tunable-after-evaluation |

## 7. Risks (per §16)

- **R1 — LLM-as-perceiver reliability:** v1 emotion/thread/safety proposals come from an LLM judgment call, not trained classifiers. This is the honest v1 trade-off (no labeled data exists yet); mitigations: evidence-typing, confidence caps, deterministic override rules, and the Phase-4 labeled set that later justifies (or replaces) the LLM judge.
- **R2 — Latency budget:** one extra perception call per turn adds latency to an already slow path (Snapshot §7: batch STT → LLM → TTS). Mitigation options exist (merge perception into the response call's first stage, or run parallel with TTS prep) — decision belongs to Phase 3, flagged now as the main integration risk.
- **R3 — Safety false negatives:** the highest-stakes failure mode; v1 scaffold is deliberately recall-biased but this remains the top risk until the dedicated safety investigation + eval set exist.
- **R4 — Over-engineering before data:** seven dimensions is the right *coverage*, but several fields will be untestable until real conversations are collected; the model marks which parts are provisional to avoid false confidence.
- **R5 — Hinglish judgment quality:** emotion/sarcasm in code-switched speech is hard even for humans; eval set must over-represent it (persona reality).

---

**Per §17: this is the returned State Model proposal. No implementation until reviewed and locked.** On lock, next deliverable is Phase 3 (data contracts between perception → state → memory → safety → policy → generation), then Phase 4 (evaluation design).

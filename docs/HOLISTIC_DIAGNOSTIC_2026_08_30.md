# Aiva — Holistic System Diagnostic (2026-08-30)

**Role:** embedded CTO/product-architect review, external-objectivity discipline.
**Constraint honored:** no code changes, no refactors, no fix proposals in this
document. This is diagnosis only.
**Branch reviewed:** `arena/01a05304-voice-agent` @ `c6d9f00`.
**Evidence base:** git history; `agent/` + `providers/` + `phase5/` source;
`docs/*` (WORK_LOG, AUDIT_2026_08_29, OPEN_ISSUES_REVIEW, STATUS_REPORT,
PHASE5_STATUS, GEMINI_QUOTA, GARBLED_STT_DIAGNOSTIC, ARCHITECTURE_*);
21 test suites (~244 assertions, all green in this sandbox); owner-run
diagnostics pasted 2026-08-30 (a 16-turn live session + 117-session / 2348-turn
STT rejection aggregate + A/B report run).

**Evidence discipline:** every material conclusion carries
Evidence → Observation → Inference → Confidence (High/Med/Low).
Where evidence cannot establish something, it is marked
**"Not established by current evidence."** No assumptions-as-facts.

---

## 0. The shape of the system (complexity map)

| Artifact | Size | Reading |
|---|---|---|
| `agent/main.py` | 1,851 lines, 25 fns, 4 `nonlocal`s, 56 `log_event`, 27 `tmark` | Orchestration god-object: VAD loop + routing + contract + gate + detail-mode + repeat guard + ack + barge + supervisor + telemetry + memory commit in ONE closure |
| `agent/prompt_fragments.py` PERSONA | 9,744 chars ≈ **2.4K tokens** (assembled); full system ≈ 2.9K tokens | Per-incident style rules (V1.14) |
| `docs/GEMINI_QUOTA.md` per-turn estimate | "~800–1200 tokens (system + policy + memory + history + user turn)" | Doc–code drift: actual system string alone ≈ 2.9K tokens, ~3× the doc |
| Tests | 21 suites, ~244 assertions | All pure-module; none import `main.py` (it imports livekit → untestable offline) |
| Git history | 8 commits reachable (1 on 08-26, 7 on 08-30); docs cite `efb5d4a`, `cf579b8` — **both missing from git** | WORK_LOG claims "~30 commits"; history largely not in git |
| Env surface | ~23 `os.getenv` across config/providers | Moderate |
| Deterministic line pools | 7 (`FILLER/CLARIFY/BACKCHANNEL/LISTEN/SUPERVISOR/PRESENCE_D7/OPENDOOR_D8`) | One table would do |
| `reply_guard.py` | 15 transforms (trim, gender, tag-strip, merged-words, parrot-shape, challenge, repeat…) | Enforcement layer, healthy in isolation |
| Policy nudges in `main.py` | ≥4 ad-hoc mutation sites (`detail_mode` latch ×6, `_stuck_nudged`, `reconcile_claim`, `checkpoint_recovery`) | State mutated ad hoc, outside the state engine |
| LLM key rotation | 2 models × 3 keys = 6 combos; GEMINI_QUOTA admits same quota pool | Complexity with no capacity gain |

---

## 1. What is genuinely working well (proven, not assumed)

**1.1 Deterministic state engine discipline**
- **Evidence:** `agent/state_updater.py` (629 lines); `PHASE5_STATUS.md` ("Batch-2 20/20 + determinism k=3"); `test_state_delta.py` green; WORK_LOG: "LLM interprets, code applies rules — the deepest principle".
- **Observation:** state transitions are replayed deterministically; fixtures caught live regressions before users did.
- **Inference:** this is the project's strongest architectural asset and the correct division of labor.
- **Confidence: High.**

**1.2 Memory scoping + device identity**
- **Evidence:** `STATUS_REPORT.md` §1 ("cross-session memory recall verified live: 'Gaggu hai na, jaise bataya tha'"); `PHASE5_STATUS.md` ("5.5 production-ready: ✅"); `memory_gate`/`memory_store` tests green.
- **Observation:** memory is device-scoped, gated, and background; live recall works.
- **Inference:** memory is not the problem area.
- **Confidence: High.**

**1.3 Safety pipeline**
- **Evidence:** `PHASE5_STATUS.md` day-one record ("D-C full set FN=0/FP=0 system-level after 4 evidence cycles"); `phase4/datasets/safety_dc_v1.json` (55 items); hard gate (identity/fabrication) tests green; Tele-MANAS in transcript.
- **Observation:** safety detection was validated against a pre-registered dataset with zero false positives/negatives at system level.
- **Inference:** safety classification is a genuine strength; the hard-gate set is defensible.
- **Confidence: High** (for the dataset; generalization beyond it is not established).

**1.4 Observability depth**
- **Evidence:** 56 `log_event` + 27 `tmark` sites in main.py; per-turn JSONL + lifecycle JSONL + events log + SQLite memory; `check_aiva.py`, `stage_diagnostic.py`, `aiva_health.py`; OPEN_ISSUES_REVIEW F8 ("observability depth" self-acknowledged).
- **Observation:** nearly every decision/phase is logged per turn.
- **Inference:** the system can be diagnosed after the fact — rare and valuable.
- **Confidence: High.**

**1.5 Pure-module testing discipline**
- **Evidence:** 21 suites, ~244 assertions green; determinism k=2/3 fixtures; phase4 harness (batch2 replay offline, golden/D-C live).
- **Observation:** every pure module has regression coverage.
- **Inference:** module-level correctness is generally held.
- **Confidence: High.**

**1.6 Fused single-call latency design**
- **Evidence:** WORK_LOG Phase 3 ("30/30 parseable, safety 6/6, +0.29s latency cost"); owner 16-turn session: speech→audio avg ~2.05–2.2s.
- **Observation:** one LLM call carries perception head + prose.
- **Inference:** the core latency mechanism is right; per-call cost is small.
- **Confidence: High** (design validated; absolute latency varies by provider).

**1.7 Turn-taking determinism**
- **Evidence:** `turn_controller.py` + `adaptive_endpointing` tests green; WORK_LOG §8 (endpointing recalibration).
- **Observation:** pause/continue logic is deterministic and unit-tested.
- **Inference:** correct mechanism; live continuous-speaker validation still open (PHASE5_STATUS 5.4(c)).
- **Confidence: Medium** (unit-verified; live-open gate).

---

## 2. What is broken or unreliable (exact evidence)

**2.1 STT rejects genuinely-spoken turns**
- **Evidence:** owner 117-session aggregate: `high_no_speech_prob` 29 rejects incl. turn 16 — **20.5s monologue, nsp=0.80, rms=3079** ("कौन-कौन से चीजों के ऊपर धिखान रखना पड़ता") and turn 15 (2.2s, rms 3025); code pre-fix read `segments[0]` only (`providers/stt.py`).
- **Observation:** long, high-energy real speech rejected as "no speech" because the first segment was noise.
- **Inference:** this was a real root-cause defect (now fixed by segment aggregation, commit 086fa1b) — but 7.5% catastrophic (176 turns) and 3.9% punctuation-only (92) remain unexplained; garble rate is the input ceiling.
- **Confidence: High** (rejection mechanism); **Medium** (garble root cause — mic/AEC vs model not isolated).

**2.2 Silence/failure under provider pressure**
- **Evidence:** WORK_LOG (session 212641 "total silence": all 6 key×model combos 429 → 65s cooldown → CancelledError kills output); AUDIT B3 (TTS fallback timeout vs 429 cooldown → silent Edge with empty text); session 111740: 429×10, TTFA degradation ×6.
- **Observation:** quota exhaustion has repeatedly produced total silence; each incident got a containment patch (immediate D4 filler; ack health gate; supervisor).
- **Inference:** containment works *per stage*; there is no single graceful-degradation policy. The system's worst failure (silence) is driven by free-tier quotas, not architecture.
- **Confidence: High** (incidents documented).

**2.3 TTS voice/quality inconsistency**
- **Evidence:** owner complaint "edg voice is there"; STATUS_REPORT #1 ("Edge fallback different voice — jarring"); TTS free-tier TTFA spikes 2.9–4s vs 1.5–1.9 baseline.
- **Observation:** Fish free-tier failures flip the user to a different voice mid-conversation.
- **Inference:** a UX-level reliability defect; contained (failover works) but not cured (STATUS_REPORT's own verdict). Owner decision on provider remains open.
- **Confidence: High.**

**2.4 Behavioral drift: repetition, parroting, generic replies**
- **Evidence:** owner 16-turn session: t9 == t10 verbatim (`arey main yahin hoon, bata kya chal raha hai?`); ALL 16 turns `mode=VENT goal=encourage_continuation`; OPEN_ISSUES A2 (4/11 parrot confirmations); t14 ROUTE/HEAD mismatch.
- **Observation:** the state engine labeled the entire conversation VENT and prescribed `encourage_continuation` every turn → generic same-pattern replies; verbatim repeats confirmed live.
- **Inference:** two stacked causes: (a) mode misclassification or coarse mode → same policy every turn (unmeasured); (b) no-repeat constraint was dead until wired 2026-08-30 (last_claim/last_reply never passed).
- **Confidence: High** (session evidence).

**2.5 Routing fall-through class**
- **Evidence:** t14 catastrophic transcript → full LLM reply (owner session; fixed fc361b8); AUDIT #1 (engine never bound → legacy brain); AUDIT #9 (response-skip race after barge-in).
- **Observation:** several "impossible" paths silently fell through to the LLM or to nothing.
- **Inference:** the orchestration decision tree is non-exhaustive and had no integration test; each instance was found live.
- **Confidence: High.**

**2.6 Shipped-but-dead/unreachable code (recurring class)**
- **Evidence:** `low_avg_logprob` unreachable (fixed 086fa1b); contract `no-contradict`/`no-repeat` dead (fixed); epoch off-by-one — "our own fix was broken" (AUDIT A1); `history_window` dead (PHASE5_STATUS 5.3); `stt_gemini_live.transcribe_stream` latent bug (AUDIT B2); `_commit_explicit_memory` reads a field that never exists (AUDIT B1).
- **Observation:** 6+ instances of code that "worked" in review but was dead/unreachable in integration.
- **Inference:** a *wiring-verification gap*: modules are correct; the 1,851-line closure silently mis-wires them. Root cause of the whole class is untestable orchestration.
- **Confidence: High.**

**2.7 Doc–code drift & lost history**
- **Evidence:** GEMINI_QUOTA says 800–1200 tok/turn vs actual ≈2.9K (system alone); PHASE1 audit found "README lied: EdgeTTS robotic voice, not Fish" (WORK_LOG §1); docs cite commits `efb5d4a`/`cf579b8` that are **absent from git** (verified `git cat-file`); only 8 commits reachable vs "~30 commits" claimed.
- **Observation:** documentation and git history do not match code reality.
- **Inference:** archaeology is docs-only; drift is systematic (the project's own PHASE1 theme: "docs ≠ code").
- **Confidence: High** (verified locally).

---

## 3. Over-engineered / prompt-bloated / duplicated

**3.1 Persona is 2× redundant with enforcement code**
- **Evidence:** PERSONA 9,744 chars (V1.14). Rules 7c (script), 7d (spelling), 7b (no-parrot), 7e (interrupted context), repeat discipline are ALSO enforced in code (`devanagari_to_roman`, `fix_merged_words`, shape detector + nudge, `response_state`/`reconcile_payload`, `repeat_break_for`).
- **Observation:** same intent enforced twice, in prompt and code, with separate maintenance.
- **Inference:** prompt size inflates latency (input tokens ≈3× the quota doc) and dilutes instruction salience (OPEN_ISSUES F5 already flagged this). The code half is deterministic and sufficient for several of these.
- **Confidence: High.**

**3.2 Policy is mutated ad hoc in the orchestration**
- **Evidence:** main.py ≥4 mechanisms (detail latch ×6 sites; `_stuck_nudged` parrot; `reconcile_claim`; `checkpoint_recovery`) rewrite `policy` dict directly, bypassing the state engine.
- **Observation:** the "deterministic code decides" principle is violated at the wiring layer — policy ends up a patchwork of turn-local flags.
- **Inference:** this is where "every incident adds a rule" happens at runtime. The v2 contract consolidation targets exactly this.
- **Confidence: High.**

**3.3 7 parallel deterministic line pools**
- **Evidence:** FILLER/CLARIFY/BACKCHANNEL/LISTEN/SUPERVISOR/PRESENCE_D7/OPENDOOR_D8 (+ GATE_BLOCK_LINES, REPEAT_BREAK_LINES).
- **Observation:** one structural idea repeated nine times.
- **Inference:** cosmetic consolidation (one table + pick_line) — low priority, low risk.
- **Confidence: High** (existence); **Low** (impact).

**3.4 6-way key×model rotation adds no capacity**
- **Evidence:** GEMINI_QUOTA ("3 keys = same Google project = one quota pool; rotation redistributes, adds nothing"); `fused_turn.py` rotations list.
- **Observation:** complexity (cooldown logic, per-combo accounting) with zero additional RPD.
- **Inference:** keeps the system alive during bursts but is not a capacity fix; the 429 storms persist.
- **Confidence: High.**

**3.5 Three parallel telemetry sinks**
- **Evidence:** session_*.log (turns) + events_*.log (events) + turn_lifecycle_*.jsonl (tmarks) + SQLite; diagnostics must merge them; earlier format mismatch (barge summary old vs new) required a code fix.
- **Observation:** rich but redundant; drift-prone.
- **Inference:** consolidation would simplify diagnostics; not a failure driver today.
- **Confidence: Medium** (impact), **High** (existence).

---

## 4. Under-engineered or missing

**4.1 Zero test coverage for the orchestration layer**
- **Evidence:** 21 suites, all pure modules; `main.py` imports livekit at module top → cannot be imported/run in tests. Every orchestration bug listed in §2.5/§2.6 was found **live**, post-deploy.
- **Observation:** the highest-risk file in the system has no unit/integration coverage.
- **Inference:** this is the single largest engineering gap: it explains the "commit ≠ working" theme (WORK_LOG §7) and the fall-through/dead-wiring classes.
- **Confidence: High.**

**4.2 No decision gate / A/B baseline run**
- **Evidence:** STATUS_REPORT #6 ("clean full-stack live validation near — sessions keep hitting provider incidents"); the A/B report script exists but zero sessions have been measured through it (owner run showed `-` for missing files).
- **Observation:** fixes ship without a measured before/after.
- **Inference:** "is it better?" remains a feeling (OPEN_ISSUES F7's exact critique). No change can be validated.
- **Confidence: High** (no A/B data exists).

**4.3 No claims/facts registry**
- **Evidence:** no-contradict is a single `last_claim` string (wired 086fa1b); entity extractor exists but no queryable within-session facts store.
- **Observation:** contradiction protection is shallow (one claim), not structural.
- **Inference:** the v2 proposal's L4 registry is the missing piece for real no-contradiction + tool-era grounding.
- **Confidence: Medium** (improvement direction), **High** (absence).

**4.4 Mode-misclassification is unmeasured**
- **Evidence:** 16/16 turns labeled VENT in one session; no confusion-matrix or mode-agreement measurement anywhere.
- **Observation:** either the classifier over-fires VENT or VENT policy is indistinguishable from default — unmeasured.
- **Inference:** without a mode-agreement measure, behavioral tuning is guesswork.
- **Confidence: High** (pattern), **Not established** (root cause: classifier vs policy).

**4.5 Garble root cause untested; cost telemetry absent; tool/action ledger absent**
- **Evidence:** OPEN_ISSUES A3 (mic vs whisper unknown); GEMINI_QUOTA math is doc-only (RPD never measured); action fabrication gate is regex-only (paraphrase evades).
- **Inference:** three separate gaps; each is a decision-blocker for its area.
- **Confidence: High** (absence).

---

## 5. Symptom clusters — incidents that are one pattern

| Cluster | Incidents | Underlying pattern |
|---|---|---|
| **A. Shipped-but-dead** | low-logprob unreachable; contract constraints dead; epoch off-by-one; history_window dead; Gemini-Live STT latent; memory-field dead read; engine never bound | Wiring-verification gap: modules correct, 1,851-line closure mis-wires them; zero integration tests |
| **B. Silence under pressure** | 429 total-silence; TTS zero-audio; ack silent; pre-audio cancels; Edge voice flip | Free-tier provider fragility × piecemeal per-stage degradation; no single graceful-degradation policy |
| **C. Repetition/generic/parrot** | t9==t10 verbatim; parrot loops; VENT generic replies; flip-flop; "always says main sun raha hoon" | Policy over-prescription + weak model drift + no measured feedback loop (each patched with a new rule) |
| **D. Routing fall-through** | legacy brain; clarify fall-through (t14); response-skip race; ROUTE/HEAD mismatch | Non-exhaustive, untested orchestration decision tree |
| **E. Environment/wiring loss** | workspace resets ate edits; wiring landed twice missing; stale worker build; RUN_ALL pulled nonexistent branch | Single-file orchestration + unreproducible environment + history not in git |

---

## 6. Previous fixes — root cause vs symptom patch

| Fix | Verdict | Why |
|---|---|---|
| Deterministic updater + fixture replays | **Root** | Made state transitions provable; harness caught regressions before users (WORK_LOG §5) |
| Device-scoped memory (O2) | **Root** | Eliminated cross-session bleed structurally |
| Fused-call validation | **Root** | Locked the latency mechanism with measurement |
| Language pin (`AIVA_STT_LANGUAGE=hi`) | **Root** | Killed the drift class |
| Segment aggregation (nsp min) | **Root** | Fixes the actual rejection mechanism (086fa1b) |
| Repeat guard + last_claim wiring | **Root** | Kills verbatim-repeat mechanism (fc361b8) |
| Fall-through return (invalid+speaking) | **Root** | Seals the t14 path (fc361b8) |
| Suspicious band (CA6) | **Root-ish** | Closes the false-accept side of the gate |
| 429 rotation/cooldown, immediate-D4 | **Symptom** | No capacity added; silence recurred until patched again |
| Edge TTS fallback | **Symptom** | Fixes silence, keeps voice flip (owner now wants Fish-first) |
| Parrot-streak nudge | **Symptom** | Treats output; input (garble) likely driver (F3) |
| Pre-audio-cancel accounting | **Measurement** | Made the problem visible, did not fix it |
| Per-incident persona rules (V1.1→V1.14) | **Symptom** | Each rule dilutes; several now duplicated in code |
| Ack health gate | **Symptom-of-symptom** | Guards a filler against an unhealthy LLM |

---

## 7. Architectural ownership map

| Problem | Belongs to |
|---|---|
| STT rejection of real speech / garble | STT (segment metrics done; garble root-cause = input/provider) |
| 429 silence | Infrastructure (quota); orchestration (degradation policy) |
| TTS voice flip / TTFA | TTS/provider decision (owner); orchestration (degradation) |
| Repetition / parroting / generic | Policy (over-prescription) + LLM (model tier) + measurement |
| Routing fall-throughs | Orchestration (main.py decision tree) |
| Shipped-but-dead code | Orchestration (wiring) — not modules |
| Mode misclassification (VENT-everything) | State (mode derivation) — unmeasured |
| Contradiction / facts | Context/memory (missing L4 registry) |
| Fabrication (paraphrase) | Enforcement (needs action ledger when tools exist) |
| Doc drift / lost history | Process/infrastructure |
| Latency budget | Orchestration (component timings exist; no budget view) |

---

## 8. Impact of each pattern

| Pattern | UX | Latency | Reliability | Cost | Scalability |
|---|---|---|---|---|---|
| God-object orchestration | indirect (bugs) | — | **High impact** (live-only bugs) | dev-time | low |
| Prompt bloat (2.9K tok) | drift/repetition | **+input-token TTFT** | medium | 3× doc estimate (free tier) | RPD-bound |
| Free-tier fragility | silence/voice-flip | TTFA spikes | **High** | zero $, high ops | single-user only |
| Ad-hoc policy nudges | generic replies | — | medium | — | — |
| No A/B gate | — | — | medium (regressions) | — | — |
| Observability depth | — | — | **positive** | small | positive |

---

## A. Current System Health

- **UX:** Companion persona works in short healthy stretches (session 111740 "healthiest flow"; memory recall praised), but **behavioral drift** (repetition, generic VENT replies, parroting) and **voice inconsistency** (Edge flip) dominate the experience in pressured sessions. Mode is over-prescriptive.
- **Reliability:** Module-level high; **integration-level low**: every orchestration bug was found live (6+ dead-wiring instances, 2 fall-throughs, silence chains). Provider incidents are the top external reliability driver and are contained per-stage, not structurally.
- **Latency:** ~2.0–2.2s speech→audio in healthy sessions (best measured); component split (STT ~0.3s, LLM TTFT ~1.1s, TTS TTFA ~1.6s) shows **TTS+LLM dominate**; prompt bloat inflates input tokens ≈3× the doc estimate. Pre-warm may shave TTFA after idle gaps only.
- **Cost:** Zero $ today (all free tiers); hidden cost = ops/incident handling and RPD ceiling (1,500 turns/day, doc-only, never measured).
- **Scalability:** Single-user MVP by design; no service split, no horizontal path, no tool/action layer. Not a product concern yet — but tool-use scenarios would hit the god-object and the regex-only fabrication gate immediately.

## B. Top 5 Root Patterns

1. **Untestable 1,851-line orchestration god-object** — explains shipped-but-dead code, fall-throughs, wiring loss, and the "commit ≠ working" theme. (Highest share of engineering failures.)
2. **Policy-layer incident-rule accumulation** — 9.7K-char persona + ≥4 ad-hoc nudge mechanisms + per-mode tactic goals → repetition, generic replies, dilution, topic-overfit risk. (Highest share of behavioral failures.)
3. **No decision gate: fixes ship unmeasured** — zero A/B baseline exists; "is it better?" is a feeling; enables patterns 1 and 2 to accumulate silently.
4. **Free-tier provider fragility treated per-incident** — silence chains, voice flips, TTFA spikes recur; every stage has its own patch but no unified degradation policy; a strategic constraint, not a code bug.
5. **State-mode misclassification unmeasured** — VENT-over-everything produced one generic policy for a whole session; without mode-agreement data, policy tuning is guesswork.

## C. What to DELETE / CONSOLIDATE / KEEP / REDESIGN

- **DELETE (low risk):** dead code already identified (`history_window`, `stt_gemini_live.transcribe_stream` latent path, `_commit_explicit_memory` dead read); doc–code drift entries (quota estimate, stale RUN_ALL branch).
- **CONSOLIDATE:** the 9 line pools → one table; 3 telemetry sinks → one canonical schema with views; ad-hoc policy nudges → single contract/state flow; prompt rules already enforced in code (script, spelling, repeat, interrupted-context) removed from persona.
- **KEEP (unchanged):** deterministic state engine; device-scoped memory; safety pipeline + hard-gate set; fused single-call; STT validation/routing (post-fix); turn controller/endpointing; TTS+pre-warm+Fish acks; barge reorder; observability; pure-module test discipline.
- **REDESIGN (with owner approval only):** the orchestration boundary (extract the response pipeline into a testable, dependency-injected module — enables integration tests and kills Pattern 1); persona → boundary-only (Pattern 2); policy → coarse GOAL contract (Pattern 2/5); degradation policy → single ladder (Pattern 4).

## D. Highest-leverage next step

**Build the missing decision gate BEFORE any architecture change: capture a measured baseline and make the orchestration testable — in that order.**

Concretely: (1) run 2+2 A/B sessions on the CURRENT code to establish baseline numbers (the report tool already exists); (2) extract `main.py`'s response pipeline into a dependency-injected module with an offline replay harness, so every future change — contract, detail-mode, barge, degradation — is integration-tested before live runs. This kills Pattern 1 (dead-wiring/fall-through class), makes Pattern 2's changes safe to attempt, and gives Pattern 3 its first real data. It is a structural enabler, not another incident patch.

Rationale: every other high-value move (persona slimming, coarse GOAL, unified degradation) risks repeating "commit ≠ working" without the harness + baseline; the harness + baseline are prerequisites, not optional.

## E. Evidence still missing before that decision

1. **Baseline A/B numbers** — 2 pre + 2 post sessions through `contract_ab_report.py` (not a single measured session exists yet). *Blocks any before/after claim.*
2. **Mode-agreement data** — confusion of `mode=VENT` across sessions (is VENT real or default?). *Blocks state/policy changes.*
3. **Garble root cause** — controlled mic/AEC vs Whisper comparison (OPEN_ISSUES A3). *Blocks STT/provider changes.*
4. **False-barge rate post-reorder** — lifecycle `barge_false_ratio` from live sessions. *Blocks barge tuning.*
5. **Quota/cost telemetry** — actual RPD/429 frequency, not doc math. *Blocks provider decisions.*
6. **Tool-use scenario definitions** — which actions (API/RPA/CRM) the ledger must model; currently zero scenarios exist. *Blocks enforcement redesign.*
7. **Per-session latency component budget** — STT/LLM/TTS split across sessions (exists per-turn, not aggregated). *Blocks latency work.*
8. **The 7.5% catastrophic + 3.9% punctuation-only composition** — sample transcripts per bucket. *Blocks STT threshold tuning.*

Items 1–2 gate Pattern 2/5 work; item 6 gates the tool era; items 3–5, 7–8 gate provider/STT/latency decisions. None block building the harness + baseline (D), which is why D is first.

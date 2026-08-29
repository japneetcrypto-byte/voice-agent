# Aiva — Work Log & Struggle Chronicle
**From first commit of this effort (2026-08-26) to now · branch `arena/01a03e6f-voice-agent` · everything that was attempted, broken, learned, and fixed.**

---

## The one-paragraph story

We started with a voice agent that technically worked (mic → speech-to-text → LLM → voice reply over LiveKit) but conversed like a bad IVR: it interrupted mid-story, answered garbled audio with confident nonsense, had no memory, no emotional awareness, and replied in templates. We turned it into a stateful conversational system through ~30 commits: a locked design phase (state model, contracts), a measured validation of the core mechanism (fused perception), a full evaluation pass with pre-registered gates, and then implementation — discovering along the way that the hardest problems were not the LLM but **timing** (when to speak), **input quality** (what was actually said), and **context discipline** (what to know vs. what to say). The current system detects emotion per turn, remembers across sessions, waits through thinking-pauses, asks you to repeat when audio is garbled, handles self-harm statements with a locked safety protocol, and replies in your language with your cloned voice's persona. It is one live validation away from production-ready.

---

## Timeline of everything we fought

### 1. Phase 1 — Ground truth & planning (before touching behavior)
- **"What is all live?"** — audited the repo. Found the MVP was 4 local terminals, no deployments, and a README that lied: Fish Audio was "configured" but the TTS actually in use was **EdgeTTS with a robotic default Hindi voice**. This pattern (docs ≠ code) repeated all project long.
- **Oracle free-VM plan** (self-hosting Fish Speech) — fully planned, then parked when you revealed the real goal was realtime conversational quality; Oracle's A1 capacity issues + CPU-only inference made it the wrong first battle.
- **Deliverables:** `PHASE1_PLAN.md` (scope lock, decision log), `infra/oracle/bootstrap.sh` + SSH keys (still unused, still ready).

### 2. Phase 2 — The state model (design before code)
- **Emotional Conversation State Model v1**: 7 state dimensions + Response Policy, built from 16–18 scenario walkthroughs (venting, sarcasm, Hinglish, barge-in, reconnection, safety…).
- **Self-inflicted wound we caught in review:** my own design had `user_energy_trend` in Conversation State — a boundary violation (feelings belong to Emotion State). Stress-test review fixed it plus 5 other over-engineered fields.
- **The memory contradiction:** the model assumed cross-session memory, but the code had random room names, everyone named "user", and two non-communicating workers. Resolved by owner decision: **anonymous device-scoped UUID** (O2).
- **Locked** after amendments A1–A10 + owner rulings O1–O3 (voice-only safety resources, device ID, fused perception).

### 3. Phase 3 — Validation before contracts
- **Task 1 (fused perception):** hypothesis — ONE Gemini call can output both a structured perception head (emotion/safety/thread) and the spoken reply. First live run: **every fused call crashed** (my `nonlocal` closure bug) **and** the pacing code was silently absent → 429 storm. Fixed both; rerun gave a clean **VIABLE**: 30/30 parseable, safety 6/6, +0.29s latency cost.
- **The lesson:** validate infrastructure claims with measurement, not prose.

### 4. Phase 3 — Data contracts (the constitution)
- Locked `aiva.*` contracts: perception-head schema with an exact emotion taxonomy, persona contract (masculine self-reference to match the cloned voice — the model kept saying "main sun rahi hoon" in *your* voice), thread semantics, transport byte-shape, device identity, a **deterministic updater** (LLM interprets, code applies rules — the deepest principle of the project), and 9 degradation paths (D1–D9).
- **Amendment U7** (after your boundary question): the optional `correction {present, about}` head field — the LLM flags corrections, code applies them; never the reverse. Also killed a subtle trap: a correction can never "rescue" an invalid label.
- Struggles documented honestly: 6 flagged decisions (U1–U6) rather than silent choices.

### 5. Phase 4 — Evaluation with pre-registered gates
- **Golden suite**: 19 fixtures covering all 18 scenarios with tolerance-band assertions.
- **Batch-2**: 20 deterministic updater fixtures (trajectories, decay, hysteresis, safety de-escalation, correction, degradation). Built a reference updater to run them — which later became the production module.
- **The harness ate its own dog food**: when live failures came in, the fixtures and reference implementation caught every regression before users did.
- **T4.1 safety taxonomy** (research-locked): C-SSRS-graded escalation, Tele-MANAS 14416 as the spoken helpline, figurative-vs-explicit two-tier response, India-specific resources. Your "validate emotion, not accusation" principle became `user_interpretation_neutral`.
- **D-C safety dataset** (55 items) authored with the locked taxonomy.

### 6. The STT saga (the longest-running enemy)
This was the deepest pit, with four distinct sub-bosses:
1. **Language drift**: short Hinglish clips transcribed as Spanish, Romanian, Icelandic ("Júka bara að öllu hvað"), even `¿Me hiciste tu pregunta...?` — because no language pin existed. Fixed by pinning (`AIVA_STT_LANGUAGE=hi`).
2. **Prompt leakage**: my "fix" (a Hinglish-bias `initial_prompt`) leaked into transcripts — the model literally transcribed *"Raman Hindi, Hindi and English words"* as user speech. Removed by default.
3. **Repetition loops**: "अजयार के अजयार के अजयार" — Whisper's degeneration mode. Fixed with `temperature=0` + a deterministic detector that routes loops to the clarify path instead of the LLM.
4. **Turbo-tier Hindi accuracy**: garbled-but-"valid" transcripts ("वो शो मले ने पहता है") that the LLM couldn't follow → "samajh nahi pa raha". Fixed by upgrading to **whisper-large-v3** (same Groq provider, owner-approved, env-revertible).
5. **The language-learning bug**: session language was learned from the FIRST utterance — which was often a junk "Mm-hmm" grunt → session locked to the wrong language → every subsequent Hindi utterance force-decoded as English. Fixed with qualification rules (≥1.2s, ≥3 words, non-catastrophic confidence) and wrong-pin recovery.

### 7. Phase 5 implementation — where "done" kept not meaning done
Implemented 5.1–5.7 (transport, production updater, session state, degradation, identity, memory, worker wiring), but the live runs exposed a **recurring theme: commit ≠ working**:
- **The silence bug**: a rename refactor updated the import but not the usage → `NameError` inside the failure handler → agent went silent exactly when LLM calls failed. Plus whitespace-only "speech" blocking the fallback.
- **The uninitialized attribute**: `SessionState.policy` was never created in `__init__` — first live turn raised `AttributeError`, swallowed by the generic handler → "Pipeline Error" and silence. Caught by building an offline pipeline check.
- **The missing wiring (twice)**: the Turn Controller and the emotion_reflection policy field were *committed* but the `main.py` wiring silently failed to land in workspace resets — your logs ("decision: respond" everywhere, no WAITs) exposed it. This workspace resets between sessions and has eaten edits repeatedly; recovery scripts and per-anchor verification became standard practice.
- **First-turn memory "bug"**: Aiva referenced old sessions on a fresh hello. Root cause: **by design** — device-scoped memory seeding. The actual gap was that the persona never said *when* memory may be mentioned. Fixed with the MEMORY IS BACKGROUND rule + explicit BAD/GOOD examples.

### 8. Endpointing — the final boss
- Diagnosed from your logs: **25%+ premature endpoints** on continuous Hindi speech; one monologue fragmented into 32 STT chunks; the cascade (premature reply → barge-in → corrupted STT → worse replies) traced end-to-end.
- **Fix 1**: adaptive endpointing (premature-resume penalty, long-speech floor, genuine-gap reset) — deterministic state machine, unit-tested with a fake clock.
- **Fix 2 (after an independent gold-vs-log evaluation you ran)**: recalibrated for Hindi planning pauses (resume window 3s, penalty 400ms, floor 700ms after 5s, reset 4s). The evaluator's "speech endpoint ≠ conversational endpoint" became the project's core timing principle.
- **Fix 3**: the Turn Controller — respond-vs-WAIT gate (trail-off connectors, fragments, questions-as-handoffs), so "मामला ये है कि..." pauses produce silence, and the eventual response sees the whole thought.
- **Honest scorecard**: the endpointing recalibration and controller have *not yet* been validated together in a live continuous-speaker run — that's the open gate.

### 9. What we deliberately did NOT do (scope discipline)
No provider swaps (STT stayed Groq; LLM stayed Gemini flash-lite after an evidence-based audit showed no better free tier), no architecture rewrites, no new state dimensions beyond locked ones, no prosody work (Phase 4 optional), no streaming STT, no UI redesign (Phase 7), no fake VAD load tricks on Oracle (ban risk), no silent model changes — every layer change came to you with evidence and got your ruling first.

---

## Current state (2026-08-27)

**Working & verified:**
- Fused perception + response (one call): VIABLE, 30/30 parseable
- Deterministic updater: 20/20 fixtures + byte-identical determinism
- Safety: explicit self-harm 100% detected, figurative FP=0, Tele-MANAS protocol
- Memory: cross-session via device UUID, with the new "MEMORY IS BACKGROUND" scoping rule
- Turn-taking: adaptive endpointing + respond-vs-WAIT controller + backchannel/listen logic (deterministic, LLM-skipped where appropriate)
- STT: session language auto-learning with qualification + wrong-pin recovery, repetition-loop defense
- Full lifecycle telemetry + baseline/diagnostic tooling

**Open:**
- **Live validation** of endpointing + controller + memory-scoping together (the 30–60s continuous-speaker test) — Phase 5 stays OPEN until this passes
- Continuing gates: G-EMO/G-CAL (needs your real-voice recordings), G-THREAD/G-MEM formal scoring, U3 acoustic-distress decision
- Known residuals: model-tier ceiling (flash-lite), `clear_queue` SDK assumption unverified, D4 filler wording was approved but whisper v3 latency +0.5–1s is the current trade

---

## The principles that actually saved us
1. **"LLM interprets; deterministic code decides."** Every hallucination/invention bug traced back to a violation of this; every fix enforced it.
2. **Evidence before fixes.** Raw-capture tooling before patching; pre-registered gates before datasets; the gold evaluation before recalibration.
3. **Commit ≠ working.** Verify wiring on disk, in the real flow, before claiming it.
4. **Scope discipline.** ~10 owner-rulled decisions (O1–O3, U1–U7, D1–D7, D-4a–d) kept an emotionally-loaded, safety-critical product from becoming a moving-target rewrite.
5. **The user's ear is the final gate.** Metrics caught regressions, but every "it doesn't *feel* like a conversation" report was real signal — and the biggest fixes (endpointing, register, memory discipline) came from listening to you.

---

## Session log (2026-08-29): two healthy sessions, five residuals fixed

**Evidence:** `session_20260828_222656.log` + `session_20260828_224509.log` (owner's last live tests).

**What improved (vs the 40+ PARSE-FAIL cascade session):**
- Zero PARSE-FAILs, zero DEGRADED-PERCEPTION entries — death-spiral fix held; quota was healthy (TTFT 0.68–2.06s, no 66s stalls)
- Every valid turn got a reply; barge-in worked (INTERRUPTED at 500ms)
- Speech→audio 1.69–4.03s

**What the logs exposed (and this session's fixes):**
1. **`context: NOT CAPTURED` on every turn** — `llm_context` was read back *after* playback from `fused.meta`, which any newer `stream_prose` call resets (barge-in / idle turn) → races and misses. **Fix:** capture at first token (TTFT), epoch-guarded post-stream reads (`FusedLLM.epoch`), plus `perception_head` (m/c/s), `degradation`, `spoke_because`, `llm_called` now logged per turn.
2. **Silent failures invisible** (S1 turn 1: decision=respond, empty reply, no TTS, no cause; S2 turn 5 `valid=None` with no reason). **Fix:** generic exception handlers in `run_agent_response` + `transcribe_and_respond` write `turn["pipeline_error"]` + `RESPONSE_FAILED`/`PIPELINE_ERROR` events — next session shows WHY inline.
3. **Assistant-speak** — "aaj kya help chahiye?" (×2), "main aapke sawaalon ke jawaab dene ke liye…" → **SERVICE-MODE BANNED** block in persona (TRANSPORT_V1.3) with the exact observed BAD examples.
4. **Feminine self-reference persists** — "intezaar kar **rahi thi**" despite the persona line. **Fix:** strengthened persona with the observed BAD/GOOD pair + full form list; added telemetry-only detector (`agent/reply_guard.py`) logging `GENDER_VIOLATION` so the prompt change is measurable (no auto-rewrite — a regex can't safely distinguish self-reference from third-person female references, and a rewrite pass would violate the one-call contract).
5. **Reply length** — 3.4–7.25s of TTS. **Fix:** persona now has a word budget (target 4–12, ceiling ~20) with an observed BAD example; plus a deterministic safety net (sentence-boundary trim at 220 chars, `REPLY_TRIMMED` event, full text kept in `llm_response_full`).
6. **TTFA 1.3–3.7s contained a hidden cost** — `FallbackTTSProvider` buffered the ENTIRE LLM stream before Fish Audio started, so TTFA = full LLM generation + Fish latency. **Fix:** Fish now consumes the live stream via a queue tee (text pushed as the LLM emits it); Edge fallback replays from the parallel buffer on failure. This is the "TTS pre-warm" anticipatory-pipeline item.

**Also:** stage diagnostic rewritten (turns sorted — the TURN 5 before TURN 4 in session 2 was concurrent-task log interleaving, not a bug; now shows context summary, head, degradation, reply size, TRIMMED/GENDER/SERVICE flags, pipeline errors, session aggregates); duplicate TurnTrace print removed; dead `_e in dir()` head-fail class fixed.

**Deliberately NOT done (owner decisions needed):** Fish Audio TTFA itself (0.6–2.4s after text arrives) is provider-tier bound — faster tiers or ElevenLabs Flash (~75ms) would need owner approval + voice re-clone evaluation. Barge-in stop latency (2–3s) stays Phase 7.

---

## Session log (2026-08-29, morning): INCIDENT — the fused brain never ran

Owner report: "does not remember facts from yesterday, could not recognise Neetu
behen, not good on experience and intelligence." Session 20260829_083519:
heads=0/23, ctx=0/23, canned reply ×6, feminine "sakti hoon", errors=0.

**Root cause:** `engine["sess"]` was never assigned (binding block printed the
owner but never constructed SessionState) → every session since the Aug-28-evening
STT-router wiring ran `session.py`'s legacy assistant prompt. Second latent bug:
`stt_router.py` used asyncio without importing it → Gemini Live never transcribed
a single turn (silent NameError → Groq every time).

**Shipped (see docs/INCIDENT_FUSED_UNBOUND.md for full postmortem):**
- SessionState binding at participant join + `SESSION BOUND` console proof
- Legacy brain forbidden: unbound engine → D4 filler + ENGINE_UNBOUND event
- STT router: asyncio import, honest logprob (None, not fake -0.2), hi pin on
  Live provider, per-turn `stt_provider` attribution
- `extract_entities_from_user_text`: user-stated relations ("नीतु बहन एक टीचर है")
  commit immediately (deterministic, no LLM call) — closes the Neetu hole
- Gender detector v2 (sakti/chahti), persona V1.4, LEGACY-BRAIN flag in diagnostic
- .env.example + LIVE_TEST.md pre-flight checklist (STT primary recommendation: groq)

---

## Full audit (2026-08-29): "audit again — no issues later"

Method: pyflakes over all modules + full cross-module read + regression tests.
10 bugs fixed (A1-A10, see docs/AUDIT_2026_08_29.md), most notably:
- A1 epoch off-by-one (yesterday's own bug — would have zeroed perception_head capture)
- A2 degraded-perception death spiral FIXED (owner-known bug #1; cooldown exit, tests + determinism)
- A3 double STT streaming eliminated; A4 dir() hack removed; A5 fake logprobs (x2) -> None
- A6 session STT language pin; A7 Layer 2 finally reaches the fused call (approved 3-layer design)
5 findings documented-not-changed (owner decisions / dead paths). All suites green.

---

## First healthy fused session (2026-08-29, 091548) + round-3 fixes

Session 091548 (audit code): engine fused x45, ctx 45/45, heads 41, stt groq x49,
avg reply 2.42s (length fix works), 0 service-speak, 0 legacy, latency ~2.3s.
The pre-audit session (state log t43-71) shows the OLD death-spiral signature
(every turn PARSE-FAIL in degraded mode) — AND the first live cross-session
memory recall: "mere bete ka naam kya hai?" -> "Gaggu hai na, jaise bataya tha."

Round-3 fixes from this evidence:
- Model mis-closes the head tag ('</p>' or never closes; t30/t33) -> head parse
  fail AND the raw tag was SPOKEN. TAG_RE now accepts </p>; unclosed heads are
  salvaged (JSON recovered, tail spoken); tee sanitizer (strip_tag_leak) as
  belt-and-braces; TAG_LEAK_STRIPPED event.
- Checkpoint saved at clean shutdown leaked the prior session's Layer-1 into
  the next session (hist=106 at turn 1). discard_checkpoint() on clean end —
  checkpoint is crash-recovery-only now.
- Diagnostic gender false positive ('khabar aayi thi') -> now uses the real
  reply_guard detector; owner shown per session; mem=0 warning with owner-check
  hint; tag-leak counter.
- User-entity extractor: oblique relation forms (बेटे/bete).
Tests: 27 audit-fix cases incl. t30/t33 byte-exact regressions. All suites green.

---

## Session 100157 analysis + round-4 fixes (2026-08-29)

Healthier: fused x31, ctx 31/31, heads 26/31, avg reply 2.09s, WAIT/suppress
worked, garble recovery worked. Layer-2 compression visibly ran (hist oscillates).

Issues found & fixed:
1. t28: misspelled tag closer '</parception>' spoken aloud -> TAG_LEAK_RE now
   strips any *ception variant; regression test with exact bytes.
2. t3: 2-word STT garble ('काब बेटे') became a memory write AND polluted the
   session context -> 3 defenses: 3-word minimum, logprob floor -0.6 at call
   site, pending-until-session-end for first-sighting relations (immediate only
   when the store has seen the fact before).
3. t13: gender-detector false positive ('batao kya keh rahi thi' = addressee
   mirroring) -> addressee-imperative escape; original violation case still caught.
4. t9/t10: reply generated but TTS silent, no reason visible -> diagnostic now
   surfaces tts fallback_reason + llm_error per turn.
5. owner=5da0d644 ≠ 4da66eb5: different browser profile / cleared localStorage.
   Frontend verified correct (persisted UUID). Owner to restore canonical UUID.
6. hist=67 at turn 1 = leaked checkpoint from the PRE-fix session (expected
   one-time; discard-on-clean-end now active).

---

## Session 094645 analysis + round-5 fixes (2026-08-29)

BEST SESSION: owner 4da66eb5 (canonical), mem=21 from turn 1, hist=1 (checkpoint
discard works), heads 21/21, avg reply 1.67s, speech->audio 2.07s, 0 flags,
Layer-2 cycling. The pipeline is healthy end-to-end for the first time.

Fixed from this evidence:
1. t11: '</s_perception>' (underscore variant) spoken aloud -> TAG_LEAK_RE now
   [a-z_]*ception; regression test with exact bytes.
2. t14/t15 "Neetu meri kaun hai" -> "tumhi batao" — CORRECT behavior: the Neetu
   relation was never in the DB for this owner (told pre-extractor + the
   5da0d644 wrong-owner session). Root gap: extractor missed 'Neetu MERI
   bhaiNA/sister' (no possessive orientation, missing variants). Added
   orientation 3 (NAME + first-person possessive + relation), bhaiNA/bahan/
   sister/brother/son/daughter/maa/papa variants, third-person possessive guard.
3. t5/t7: Aiva role-played checking Blinkit ("search kar raha hoon, list mein
   kuch nahi aaya") -> persona V1.5 REALITY HONESTY rule with the exact BAD/GOOD
   examples from this session.
4. Barge-in stop latency now precisely measured: avg 2217ms, max 2827ms (n=6)
   — top remaining latency item, needs owner decision on immediate-cancel-on-VAD.
5. DB pollution flagged: "user's name is Yekaramukii" (STT garble committed by
   an old session) — cleanup command given to owner; future guard = the new
   pending-until-confirmed rule (relations), name facts still need a decision.

---

## Session 103824 analysis + round-6 fixes (2026-08-29)

First NON-VENTING session: full travel-planning conversation. Aiva gave
recommendations, recalled its own suggestions 10 turns later (t21), recovered
from a suppress ("Hello, are you there?" -> "haan, yahin hoon. sun raha tha."),
matched register ("bye bro"), owner+mem+hist all correct, errors=0, heads 20/22.
t5 showcase: deterministic clarify at 0.78s speech->audio (zero LLM).

Fixed:
1. t8: FOURTH tag variant '</s:perception>' spoken. Class-level fix at last:
   TAG_RE accepts ANY short closer (</[^>]{1,24}> — prose never contains angle
   brackets); salvage tail strips tags; sanitizer kills ANY XML-ish token in
   prose. Variant whack-a-mole is over.
2. t16: 8.05s reply (116 chars, info-listing) -> REPLY_MAX_CHARS 220->180
   (info answers run ~14 chars/s of TTS) + persona V1.6 INFO-ANSWERS-STAY-SHORT
   (max TWO options, one sentence, ask which) with the t16 BAD example.
3. t4 FAIL(unknown) -> named 'head_never_completed' (stream ended pre-head;
   observability, not a new failure mode).

Noted, not coded: merged words in long Hinglish replies ('sebaithne',
'saathchalna') = flash-lite model-tier ceiling (feeds the model-tier decision);
avg reply 3.29s is info-heavy-session effect, bounded now by V1.6; barge-in
immediate-cancel decision still pending with owner.

---

## Session 103824: voice-quality root cause + spoken-output toolkit + sales gap analysis

Best session yet (first non-venting domain — travel planning — handled well: cross-turn
self-recall t21, suppress recovery t20, register match t23, 0.78s clarify t5, errors 0).
Owner asked: (1) how to analyze SPOKEN quality — voice is cloned but sometimes sounds
unreal; (2) SquadStack-style sales agent gap analysis.

Root cause found for "doesn't sound real": MERGED WORDS in generated text
(t2 'aaram sebaithne', t3 'saathchalna') — Gemini streaming splits mid-word and the
space belongs to neither chunk; TTS pronounces the garbage token. Fix: smart_join()
(matra-aware for Devanagari — isalnum() misses combining marks) applied at the fused
buf join (post-head only, JSON-safe) and the tee. Tests incl. Devanagari boundary.

Note: parallel Arena session's Round-6 (0255a5e) landed first with class-level tag
fix + cap 180 + persona V1.6; built on top of it.

Shipped:
- smart_join + wiring + tests (the merged-word fix)
- AIVA_TTS_DUMP=1: per-turn WAV + manifest.jsonl (text, provider, duration, ttfa)
- phase5/tts_audit.py: chars/sec outliers, clipping, optional ASR round-trip (--asr),
  optional SpeechMOS (--mos)
- docs/SALES_AGENT_GAP_ANALYSIS.md: full SquadStack-style gap matrix (~35-40% there;
  telephony = biggest net-new; 6-10 week phased path; 5 owner decisions)

---

## Session 133659: the silence chain + merged-words round 2 (2026-08-29)

Owner: "took a lot more time in responses — quota or other issue?" Answer: NOT quota
(TTFT ~1.0-1.5s on most turns, no 429 storms/66s stalls; ONE 3.1s spike = rotation/
transient). The felt slowness = a SILENCE CHAIN: 6 turns with no reply.

Root causes found & fixed:
1. Controller suppression chain (t16-t21): final-word pronouns वो/वह/ये/यह treated as
   trail-off connectors (removed); handoff checked only on LAST word ("बोलो भाई" missed
   — now any-word + Devanagari बोलो/बता variants added); greetings ("हेलो") not in any
   set (added, FIRST-word rule to avoid 'aise hi' collision with English 'hi'); no cap
   on consecutive WAITs (now: after 2, respond — WAIT_STREAK_CAP).
2. Silent-skip race (t2/t3/t22 'no reply generated'): prev_task.cancel() is async — the
   response guard saw the dying task as active and returned silently. Now: await the
   cancelled task (1.5s timeout) before responding + RESPONSE_SKIPPED event/diagnostic
   flag so a skip can never again be invisible.
3. Model-emitted merged words (t13 'sahikaam', t17 'baaremein' — distinct from the
   chunk-boundary loss smart_join fixes): deterministic lexicon splitter
   fix_merged_words() applied per spoken piece; exact-match only (zero risk to
   unlisted words). Extends as new cases are observed.
4. tts_audit: HOT? band for peaks >=95% (t12 95.7, t17 95.1 — listen for harshness;
   true clipping >99).

Voice audit verdict: avg 15.4 c/s (healthy), 1 soft FAST flag (short question clip —
threshold noise), no clipping. TTS itself is performing; the LLM-side text glitches
were the "unreal" driver + now both causes are covered.

---

## Call Supervisor shipped (2026-08-29): the "senior jumping in"

Owner: "can't we have an orchestrator/fallback that gets activated like a senior
jumping in to avoid the deal slipping through — which knows the state of the talk,
manages it, and raises an alarm / a way to check what happened?"

Shipped (agent/call_supervisor.py + wiring):
- DORMANT watcher outside the hot path; engages ONLY when a user turn ends with
  no agent audio: skipped (task race) / pipeline_error / unanswered /
  reachout_unanswered (hello-into-silence — the t21 incident class)
- On engage: 4s grace, stand-down if the pipeline became audible OR a newer turn
  is in-flight, then speaks a deterministic persona line (SUPERVISOR_LINES, no
  LLM — the lifeline cannot share the failing brain) as turn_type
  supervisor_rescue (updater: TURN-SUPERVISOR-RESCUE, never PARSE-FAIL)
- Transparent: SUPERVISOR_ENGAGED event with state snapshot (cause, user text,
  engine path, TTS provider, wait streak); 2nd engagement = SUPERVISOR_ESCALATE
  + POST to AIVA_ALERT_WEBHOOK if set (the human-paging hook)
- Safety: dedupe per turn, 15s cooldown (one rescue per incident), echo-dropped
  turns excluded, deliberate WAITs excluded (controller owns that silence)
Tests: test_call_supervisor.py (13) incl. the exact hello-twice incident and
determinism. All suites green.

---

## Self-diagnosis engine + honest correction (2026-08-29, session 141753)

Owner: "can there be a self-healing system that catches WHY it did not work?"

**Correction:** smart_join (the merged-words fix) was a MISDIAGNOSIS — session
141753 showed splits ('th ik', 'nah in', 'k ela') that the heuristic itself
CREATED. The API never drops inter-chunk whitespace; merges are MODEL-emitted.
smart_join reverted everywhere; ownership moved to the deterministic lexicon
(extended: th ik/nah in/k ela/sebaithne/saathchalna/juis) + new clean_specials
scrub (t65 'j}}' junk spoken -> stripped). Audit tests rewritten around the
correction.

**Session findings -> shipped:**
1. 6 turns: Fish 'succeeded' with ZERO audio (reply text, no error) ->
   FallbackTTSProvider now counts chunks; near-zero => automatic EdgeTTS
   replay (failover), provider/reason recorded. Self-healing failover.
2. Echo filter ate user word-repeats ('kharbuja' repeat dropped) -> late-echo
   guard: speech starting >1.5s after agent audio end is REAL speech (echo
   decays faster); kept + logged (echo_overridden).
3. Supervisor PROVED live: turn 1001 'haan bolo, yahin hoon' = SUPERVISOR_LINES
   engagement recorded in the TTS audit.
4. phase5/self_diagnose.py: deterministic post-mortem per session — per failure
   class: count -> WHY -> what auto-handled it -> prescription (owner decision
   vs auto). Saves logs/diagnosis_<session>.md. The 'catches why' layer.
5. tts_audit: FAST flag now duration-aware (short questions are naturally fast).

All suites green (audit-fixes 46, supervisor 13, controller 18, gender, entity,
state, layered). Compile clean.

---

## Speaker attribution Stage 1 (2026-08-29): acoustic echo correlation, shadow mode

Owner: "can we not avoid [echo] at ASR — we have the voice attributes; if different,
it is another speaker; capture speaker_2 attributes, create a key, check across turns."

Verdict: direction agreed (with similarity+confidence+temporal-consistency
refinements). Two stack-specific insights added:
1. For ECHO we have the exact played PCM — multi-band envelope correlation
   against our own played audio beats embeddings (no enrollment problem, no deps).
2. Clean-TTS enrollment would NOT match room-captured agent voice (speaker+room
   distortion) — any agent voice-print must be enrolled from room captures.

Shipped (Stage 1, SHADOW — telemetry only, zero behavior change):
- providers/speaker_signature.py: multi-band (4-band) joint normalized envelope
  correlation, FFT, per-lag energy norm. Synthetic separation: echo 0.43-0.69
  (mild->heavy degradation) vs unrelated speech <=0.30; 68ms worst case.
  Bug found in test: single-band NCC floor was 0.83 (chance alignment) —
  the multi-band joint constraint is what makes it usable.
- main.py: 12s rolling ring of played audio (48k->16k), per-turn
  turn["echo_corr_score"], events ECHO_MULTI_AGREE / ECHO_TEXT_ONLY (possible
  eaten user — our 141753 bug class) / ECHO_CORR_ONLY (missed echo).
- Closure race fixed: score uses the task's audio_data snapshot, not the
  mutable outer float_audio.
- docs/SPEAKER_ATTRIBUTION_DESIGN.md: 4-stage plan + owner decision points
  (embedding dependency, speaker_2 UX, privacy).

Stage gates: 3 live sessions of shadow data -> calibrate -> Stage 2 gate
activation. Tests: test_speaker_signature.py (8). All suites green.

---

## Session 155556: latency decomposition = Fish Audio, not the pipeline (2026-08-29)

Owner: "taking a lot more time." Decomposition: LLM TTFT normal (1.0-1.8s), STT
normal, TTS TTFA spiked 2.9-4.05s (baseline 1.5-1.9s) + turns 1-2 zero-audio
(provider=fish, audio=None). Fish Audio free-tier degradation episode — third
session with Fish misbehaving.

Shipped:
- Diagnostic now flags provider-present silent TTS (t1/t2 signature previously
  slipped through: provider=fish + audio=None had no flag)
- AIVA_TTS_FIRST_TIMEOUT (default 5s, was fixed 7s) — hung Fish fails to Edge sooner
- self_diagnose: new TTS-TTFA-degradation class with owner-decision prescription
Note: supervisor rescue lines ride the same TTS — the zero-audio failover
(b3065ee) is what makes the supervisor audible during TTS incidents.

---

## Voice-key (speaker attribution Stage 1) observability shipped (2026-08-29)

Owner asked how to check the parallel voice-keys infra. Gap found: stage_diagnostic
never printed the shadow corr scores. Shipped:
- stage_diagnostic: per-turn corr= on the STT line + session summary line
  (n/min/med/max) pointing at the new report
- phase5/echo_shadow_report.py: the Stage-2 gate check — aggregates
  echo_corr_score across sessions split by text-filter verdict, prints speech
  floor (p95) vs echo band (p25), proposes T_low/T_high/ambiguous zone, verdict
  KEEP-SHADOWING vs SEPARABLE. Locked calibration rule; deterministic.

---

## One-command health check (2026-08-29): phase5/aiva_health.py

Owner: "can you not merge all this into 1 command — I run it and share the file?"

phase5/aiva_health.py: runs stage_diagnostic + tts_audit + self_diagnose +
echo_shadow_report + identity/memory/events/sqlite scans + config fingerprint,
assembles ONE markdown file (logs/health_<session>.md) to share verbatim.
Each section is the existing tool's own output (single source of truth),
captured verbatim; per-section failure isolation (one broken tool can't kill
the report). `--all` mode for multi-session + full shadow aggregation.
Console prints a 1-line quick verdict (brain bound? supervisor? skips? 429?).

---

## Self-diagnose upgrades (2026-08-29 evening): failed-turn attribution + cross-session trend

Owner shared their real diagnosis file (161510): 24 turns, 9 failures / 6 classes,
3 supervisor engagements, 2 429s — asked about deterioration. Upgrades:
- Supervisor section now attributes failures: failed turn numbers + per-reason
  counts (from event snapshots) + escalation count — no more opaque "1001, 1002".
- New cross-session trend table (last 8 sessions): turns, speech->audio avg/p95,
  TTFA avg, silent count, supervisor count, 429 count — the objective
  "are we degrading vs earlier" answer, included in every diagnosis + aiva_health.

---

## Stage verdict engine (2026-08-29): the owner decision rule, automated

Owner: "add each thing to the report — voice key, ASR, LLM, TTS, time taken, echo,
words spoken/heard, interruption handling. If the log confirms Fish is the bad
experience, we look for an alternative; if everything before that is fine, the
system as a unit works — we just update the outermost part."

phase5/stage_verdict.py (also the first section of aiva_health reports):
- Per-stage PASS/WATCH/FAIL: STT (hearing), LLM (brain, incl 429s/quota), TTS
  (voice: TTFA p95 + silent turns + failovers), ECHO (drops/saves/voice-key corr),
  BARGE-IN (interrupted replies + stop latency), PIPELINE GUARDS, CONVERSATION
  volume (turns, words heard, words spoken, words cut by barge-in)
- FINAL VERDICT implements the owner rule literally: all stages before TTS pass
  and only TTS fails -> "SYSTEM UNIT HEALTHY - BOTTLENECK IS THE TTS PROVIDER"
  with the replace/upgrade prescription. Multiple failures -> ordered fix list.

---

## Session 163907 analysis: pre-audio cancel chain made visible (2026-08-29 evening)

Owner: "not good as an experience." The transcript IS the evidence — user complained
in-session: t4 'tum sahi se baat kyon nahi kar paaye', t15 'idhar baat suno idhar',
t17+t18 'tum itna time lekar kyon bolte ho' (twice).

Root cause chain (4/22 = 18% of turns): user speaks quickly → TTS TTFA ~1.7-2.1s →
newer-turn cancel (prev_task.cancel()) kills the pending reply BEFORE first audio →
user heard nothing → spoke again → frustration. Turns 1/5/15 + cancelled t11.
These were INVISIBLE (interrupted=True, interrupted_at_ms=None → no flag anywhere).

Fixes:
- worker: turn["cancel_pre_audio"]=True in the CancelledError handler when ttfa never fired
- stage_diagnostic: 'REPLY CANCELLED BEFORE AUDIO' flag + summary count
- self_diagnose: new failure class (stale-reply race) with prescription
- stage_verdict: TTS WATCH when pre-audio cancels >10% (TTFA vs user pace)
- tts.py failover: samples-based (<0.1s = silence), not chunk-count
- WORKER_BUILD stamp in every session log + aiva_health mismatch warning
  (stale worker = missing safety nets; the recurring failure mode)

Also noted: one Devanagari-script reply (161510 t6 'mausam saaf hai?') — persona
says Roman Hinglish; watch. Old-session artifacts (th ik / j}}) in the audit are
pre-fix sessions; 163907 text is clean → lexicon/corrections holding.

---

## Reconciliation + regression audit + standing report (2026-08-29 night)

Owner: "reconcile and audit once to ensure no regression, then create/update a
report on where we stand — including voice keys infra."

Audit result: NO REGRESSIONS. 9/9 suites green; pyflakes clean; all 12 wiring
assertions verified present (initial 5 "failures" were grep-pattern artifacts,
each verified by direct inspection); persona TRANSPORT_V1.6 confirmed.

Standing report: docs/STATUS_REPORT.md — system snapshot with live numbers,
audit results, voice-keys exact standing (Stage 1 shipped/shadowed, Stage 2
blocked on shadow data n=3, Stage 3 owner decisions), ranked open items,
day's evidence arc, check commands.

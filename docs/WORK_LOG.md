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

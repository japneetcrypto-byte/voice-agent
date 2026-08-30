# Phase 0 — Measurability & Testability (APPROVED PLAN, 2026-08-30)

**Status:** owner-approved direction. No feature/fix code until this phase is
done. This document is the phase contract.

## 0. Frozen scope (explicitly deferred — DO NOT build)

- Claims Registry
- Persona V2 / persona rewrite (11K chars stay as-is for now)
- A-P1 / detail-mode changes
- Extra MUST_NOT constraints / new guardrails
- Provider/STT/TTS changes
- Any new feature

Nothing in this list may be touched until Phase 0 completes and the evidence
(baseline + replay-verified harness) is reviewed.

## 1. Locked principles

1. **Don't fix incidents. Fix the class of failure that produced the incidents.**
2. **LLM gets freedom inside the rails; the system owns the rails.**

Every issue from now on is CLASSIFIED first (§4), then acted on — never
converted into a rule/patch in isolation.

## 2. Phase-0 goals (all zero-behavior-change)

- **A. Baseline:** capture 2–4 clean sessions of the CURRENT frozen build so
  any future "improvement" has a measured before.
- **B. Harness:** make the critical response path in `main.py` replayable
  offline (dependency-injected), with zero logic change — proven by replaying
  the baseline logs through it and diffing turn records.

## 3. Parallel tracks (owner correction: not sequential)

**Why parallel:** the baseline logs are the harness's GOLDEN fixtures — the
first thing the harness must reproduce is the baseline turns. So the owner
captures baseline sessions while the harness is extracted as behavior-
preserving; the replay test then *proves* the extraction didn't change
behavior (identity of turn dicts).

| Track | Owner | Me (sandbox) |
|---|---|---|
| A. Baseline | Run §5 protocol on commit `e0dc60f` (frozen), archive logs | — |
| B. Harness | Approve §6 extraction scope | Build §6 behavior-preserving; replay baseline logs when they arrive |

## 4. Pattern taxonomy — classify EVERY new issue into exactly one class

| Class | Examples (past) | Owner of fix |
|---|---|---|
| 1. wiring/integration | dead constraints, epoch off-by-one, fall-throughs, engine-never-bound | harness + integration tests |
| 2. input/STT | segment[0] rejection, garble, language drift | STT layer |
| 3. state/policy | VENT-over-everything, tactic goals, ad-hoc nudges | state engine + contract |
| 4. model behavior | parroting, verbatim repeat, Devanagari drift | measurement + model tier |
| 5. provider/reliability | 429 silence, TTS voice flip, TTFA spikes | degradation policy + provider decision |
| 6. latency | TTFA, pre-audio cancels, barge stop | component budget + orchestration |

No issue is "new" until it fails to fit any class → that itself is a finding.

## 5. Baseline protocol (owner, ~30–40 min)

1. `git pull origin arena/01a05304-voice-agent` → confirm
   `git log --oneline -1` = `e0dc60f` (frozen build hash).
2. Quota-calm window. `.env` unchanged (Groq primary, Fish keys).
3. Run 2–4 conversations of 5–10 min each, covering the LIVE_TEST.md script:
   greeting / 30–60s vent with pauses / fragment-talk / interrupt /
   "kya bola tha?" / one "explain in detail" request / one "are you an AI?" /
   one topic-switch. Don't fix anything mid-session.
4. After each: archive to `logs/baseline_YYYYMMDD_N/`:
   - `session_*.log` (turns)
   - `events_*.log` (events)
   - `turn_lifecycle_*.jsonl` (tmarks)
   - `stage_diagnostic.py` output (txt)
   - `contract_ab_report.py` output (txt)
5. Keep the archive OUT of git (logs/ is gitignored) but SHAREABLE — I need
   the raw turn logs to build replay goldens. (Zip + attach, or a path I can
   read if the sandbox has access.)
6. Report to me: 4 numbers per session — speech→audio avg, interruption
   rate, STT rejection rate, provider incidents. (These become the baseline
   row in every future A/B.)

## 6. Harness design (behavior-preserving extraction — APPROVAL REQUESTED)

**Target:** make this callable offline with injected dependencies:
```
run_turn(context) -> turn_dict
```
where `context` = (engine state snapshot, lcm snapshot, memory view, policy,
transcript, acoustic, timing, tts_sink, clock, logger).

**Extraction (no logic change):**
1. `run_agent_response(user_text, turn)` body → `response_pipeline.py` module.
   Livekit deps (AudioSource capture, rtc frames) behind an injected
   `tts_sink` interface (capture_frame/flush).
2. `text_stream_tee` + enforcement chain (trim, gate, repeat-guard, script,
   merged-words, chunk telemetry) → same module, pure-ish (LLM stream via
   injected `fused` interface).
3. `transcribe_and_respond` routing block → `turn_router.py` (decision table
   returns action; main.py stays as the LiveKit adapter).
4. `main.py` keeps: VAD loop, track subscription, telemetry sinks, supervisor,
   agent_task lifecycle — calling the extracted modules.
5. Determinism: inject `time`, `random`-free (already pick_line discipline),
   and a clock object so replays are byte-stable.

**Proof of behavior-preservation (the gate):**
- Replay each baseline `session_*.log` turn through the harness with the same
  state snapshots → new turn dicts must equal the archived ones (modulo
  timestamps/ids). Diff must be EMPTY for all baseline sessions.
- All 21 existing suites stay green (they cover the extracted pure modules;
  the harness adds the previously-untested integration seam).

**Deliverable:** `phase5/harness/replay.py` + `phase5/harness/fixtures/`
(archived baseline logs, sanitized) + `phase5/harness/test_replay_identity.py`.

## 7. Definition of done (Phase 0)

- [ ] ≥2 baseline sessions archived + 4 baseline numbers per session recorded
- [ ] Harness extracts the critical path; `main.py` is a thin adapter
- [ ] Replay identity: baseline turns reproduced byte-equal (modulo ids)
- [ ] All 21 suites green; new replay suite green
- [ ] Pattern-classified issue log exists (every new issue tagged §4 class)
- [ ] Post-extraction smoke session (owner) matches baseline numbers

## 8. What I need from you

1. **Approve §6 extraction scope** (module names, injected seams, replay gate).
2. **Run §5 baseline** on the frozen commit; send me the archived logs
   (especially `session_*.log` + `events_*.log`).
3. Anything in §0 (frozen) that you want explicitly unfrozen — say it now,
   it stays frozen otherwise.

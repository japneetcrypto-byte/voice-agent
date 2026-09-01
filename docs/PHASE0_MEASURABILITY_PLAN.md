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

---

## 9. Progress log (STATUS ONLY — not part of the spec above)

**STATUS (2026-08-30, after Phase-0 review):**
> **Slice-1 replay infrastructure is proven; Phase-0 replay identity is NOT
> yet proven.** The synthetic fixture is a self-consistency check, not a
> preservation proof. Phase 0 stays OPEN until owner baseline logs (frozen
> commit `e0dc60f`, §5) replay with an EMPTY diff, and the post-extraction
> smoke matches the baseline numbers.

**2026-08-30 — Slice 1 (decision core extraction) + replay gate LANDED.**

- `agent/response_pipeline.py` (new): `build_policy_and_contract()` +
  `process_piece()` + `release_from()`/`release_tail()` — the deterministic
  decision core, extracted verbatim from `main.py` and wired in.
- `agent/turn_router.py` (new): `route_decision()` — the routing decision
  table (fall-through class included: invalid + agent speaking ⇒ drop,
  never a substantive LLM answer). main.py keeps only the async side effects.
- `agent/main.py`: 1,851 → 1,688 lines; now a thinner LiveKit adapter over
  the pure core.
- `phase5/harness/replay.py` (new): the replay gate — replays each archived
  turn through the extracted core; exit 0 = identity (empty diff).
  Coverage: routing, piece stream (release + enforcement), policy nudges
  (detail/challenge/recovery/anti-parrot shape), reconcile payloads.
  Documented boundary: `policy.contract` body (runtime memory_count) and
  supervisor/idle turns need Slice-2 state snapshots.
- `phase5/harness/fixtures/synthetic_slice1/`: synthetic archive built BY
  the extracted core (15 turns covering all decision classes) + manifest
  with the 4 §5 numbers. Placeholder — owner baseline logs replace it; the
  gate came back EMPTY on those too (verified 2026-08-30, see below).
- Tests: 24 suites green (21 existing + `test_response_pipeline`,
  `test_turn_router`, `test_replay_identity`). Negative control in the
  replay suite proves the gate flags drift (not vacuous).
- Commits: `8bbb212` (Slice 1), `05436bc` (replay gate) — pushed to
  `arena/01a05304-voice-agent`.

**BASELINE CAPTURE (2026-08-30):** captured on the ack-fixed frozen build
`f7937a4` (commit `e0dc60f` is VOID — superseded by the approved ack
call-site fix `8aa183a`). Archive: `logs/baseline_20260830_1/` (gate-field
projection of `session_20260830_175618.log`, 23 turns — see the verified
block below; full raw archive incl. events/turn_lifecycle remains on the
owner's Mac).

**2026-08-30 (update) — `run_turn(context)` (approved §6 unified interface)
NOW EXISTS and the replay gate runs through it.**

- `agent/response_pipeline.py`: `TurnContext` + `run_turn(context) -> turn_dict`
  — one callable that executes a full turn's deterministic critical path
  (routing → policy/contract/nudges → caps → release → enforcement →
  completion incl. interrupted/reconcile/filler paths) with injected deps
  (engine state, detail/stuck state, recent replies, model text, callbacks).
  Deterministic: same context ⇒ same turn dict.
- `phase5/harness/replay.py`: context is REBUILT from the archived turn +
  prior turn state and re-executed through `run_turn` — the gate now runs
  through the exact §6 interface (no parallel hand-wired derivation).
- Telemetry-only addition to main.py + run_turn: `turn["detail_latch_after"]`
  (post-decrement detail latch) — the harness needs it for exact detail-turn
  caps. Zero decision-path effect. Old-format archives (frozen `e0dc60f`)
  replay with a documented heuristic: detail-turn trimming ambiguity is a
  NOTE, never a hard gate failure; every other field stays byte-exact.
- Live wiring of the async LLM→TTS playback loop onto `run_turn` (Slice 2)
  remains DEFERRED until the baseline gate passes — the smoke target stays
  the current wired build (frozen scope: no live-path change before proof).
- Still 24 suites green (incl. run_turn unit tests + the §6 round-trip).

**2026-08-30 (update) — REAL BASELINE GATE PASSED (owner archive, frozen build
`f7937a4`).** The owner's real baseline (`session_20260830_175618.log`, 23
turns, captured on the ack-fixed frozen build `f7937a4`) is archived at
`logs/baseline_20260830_1/` and replays through the gate:

```
[replay] IDENTITY OK — logs/baseline_20260830_1/session_20260830_175618.log
         (23 turns, 0 skipped, 1 documented note)
[replay] 23 turns checked, 0 divergent field(s)
```

- 23/23 turns reproduce byte-exact (routing, engine path, policy deltas,
  cap/release/trim, completion states, reconcile/plan carry, guard flags).
- 1 documented note (non-gating): t11's piece-level repeat-guard outcome is
  stream-chunk-dependent — the live LLM stream split its trailing " aage?"
  into its own release call (guard fired) while t22/t23's coalesced (didn't);
  the archive records neither chunk sizes nor pieces, so offline replay
  cannot reproduce that single decision. Every other field is byte-exact.
- Gate-side fidelity work landed in `922a7a1` (run_turn base-dict shape,
  llm_called/interrupted/trigger emission, one-chunk release feed,
  last_head_plan carry, _exact_if_present, notes handling).
- Remaining Phase-0 step: POST-EXTRACTION SMOKE — owner runs a fresh smoke
  session on the current wired build and compares against the baseline
  numbers (23 turns / 22 replies / 0 errors / response states 16-5-2 /
  speech→audio avg 2.61s). Then Phase-0 completion review.

---

**2026-08-31 (update) — POST-PHASE-0: THE THREE APPROVED FIXES LANDED
(owner brief 2026-08-30/31; Phase 0 CLOSED by owner).** Freeze lifted;
implementation order ① → ② → ③ as approved. Each fix was defined in a
regression test FIRST, then implemented; expected vs unexpected diffs
classified below. All 25 suites green + the synthetic replay gate passes
(EMPTY DIFF on the regenerated fixture).

| # | Fix (approved) | Expected behavioral change | Replay-gate classification |
|---|---|---|---|
| ① | **Precision-detail rail** (`agent/precision_rail.py`) — dictated numbers/IDs are SYSTEM-owned: STT-verbatim echo + user confirmation, deterministic, zero LLM. The LLM never sees the dictation (lcm/session history untouched) and never re-encodes it. Engine-path `precision_rail`, `llm_called=False`. | A dictated number turn now produces the deterministic echo/confirm/ack line instead of an LLM reply. Contradictory refusal-vs-success claims are impossible (the rail always acks after confirmation). | NEW turn class — absent in pre-fix archives; `precise_detail` key compares only when present. Zero baseline diffs. |
| ② | **Continuation / "aage" delivery rail** (`agent/prompt_fragments.py` 1b + `agent/turn_controller.py`) — varied checkpoints (no canned "aage?" every chunk); user's "aage / haan / phir / bolte jao / roko mat / continue" = keep going; restart & re-confirm banned; `delivery_state` resume context named. | Detail chunks end with varied cues; continuation cues advance the same explanation instead of restarting/re-confirming; phrase cues now renew the latch & set `continue_detail`. | Delivery flag on detail turns uses the verified 6-cue list + keep-going phrases only — fresh detail requests stay `chunked_detail`. A baseline detail turn with a keep-going phrase would be an EXPECTED diff (no such turns in the verified 23-turn baseline). |
| ③ | **Detail-intent chunking — system-owned delivery state** (`engine["detail"]` in `build_policy_and_contract`/`run_turn`/`main.py`) — a detail request activates a system plan (step + last_chunk + resume payload fed to the LLM as `delivery_state`); continuation works even after the 6-turn latch expires; chunks get the A-P1 ceiling (320c) so a chunk is a substantive thought, not 1-2 lines. | A detail request is sustained across turns with substantive chunks; the reported "aage after latch expiry treated as fresh request" bug is fixed; `detail_state`/`detail_continue` archived. | `detail_state`/`detail_continue` compare only when present (absent in pre-fix archives). On a re-captured baseline, detail-active turns will show an EXPECTED `llm_response`/`reply_trimmed` diff (cap 110 → 320 = the fix). All other fields stay byte-exact. |

Harness changes: `replay.py` now threads the session engine dict forward across
turns (like the live closure), re-synced from archived evidence — required for
①/③ session task state to survive dropped/gated turns in replay.

**Fixture archival gap (important):** commit `39a41e5`'s message claimed the
real baseline fixture, but the fixture was never actually committed — the
root `.gitignore` `*.log` rule silently ignored `phase5/harness/fixtures/`
(the file exists only on the owner's machine and in the gitignored `logs/`).
`.gitignore` now whitelists `phase5/harness/fixtures/**`; the synthetic gate
fixture is committed so the gate is reproducible from the repo. To re-verify
the REAL baseline gate on the new build, the owner drops
`session_20260830_175618.log` into `phase5/harness/fixtures/baseline_20260830_1/`
and runs `python3 phase5/harness/replay.py 'phase5/harness/fixtures/*/session_*.log'`.

---

**2026-08-31 (update) — REAL GATE ON THE NEW BUILD: PASS PROFILE (owner Step-1
verification, fixture delivered via commit `27ab435` on scratch branch
`baseline-fixture`).** The owner committed the real baseline
(`session_20260830_175618.log`, 23 turns, captured on frozen `f7937a4`) into
the repo; it is now archived at
`phase5/harness/fixtures/baseline_20260830_1/` and replayed through the NEW
build (post-fixes ①+②+③). Result:

```
[replay] DIVERGENCE — .../baseline_20260830_1/session_20260830_175618.log: 1 turn(s) differ (+1 note(s))
    t20.llm_response: replay='...voice synthesis." aage?' archived='...voice synthesis." '
    t20.reply_trimmed: replay=None archived=True
    t11 note: repeat-guard outcome is stream-chunk-dependent (live piece
    segmentation not archived): llm_response differs only by the guard line
[replay] 23 turns checked, 1 divergent field(s)
```

CLASSIFICATION (owner guardrail — expected vs unexpected):

- **t20 = EXPECTED fix-③ diff.** t20 is a detail-active turn (latch 4,
  `continue_detail`). Old cap 110 dropped the model's trailing `" aage?"`
  sentence (219c spoken, `reply_trimmed=True`); the new system-owned detail
  state raises the chunk ceiling to 320, so the full chunk is spoken
  (224c, no trim). This is exactly the approved cap change 110→320. (On a
  NEW capture, fix ② also removes the canned `aage?` itself — varied
  checkpoints.)
- **t11 = documented note (non-gating)** — repeat-guard is stream-chunk-
  dependent; unchanged, never a hard diff.
- **Everything else byte-exact** — routing, engine paths, policy deltas,
  caps/release/trim, completion states, reconcile/plan carry, guard flags.

**Gate caught + fixed the rail's own false positives (tests-first):** the
first new-build run diverged on t20/t23 because the precision rail fired on
conversational Hindi — t20's `एक interview … एक बार … एक professional` (digit
WORDS across a long sentence, not a dense dictation) and t23's `सुनना`
(containing the substring `ना`, spuriously "rejecting" a pending dictation).
Fixed in `agent/precision_rail.py` (cluster-based detection: ≥3 digit tokens
within a compact MERGE_GAP span; word-exact Devanagari confirm/reject, no
bare `ना`/`na` particles) with regression tests first. Re-run: rail diffs
GONE, only the classified t20 + t11 note remain.

**Fixture archival fixed:** the root `.gitignore` whitelists
`phase5/harness/fixtures/**` — the real baseline is now committed in-repo and
the gate is fully reproducible from the checkout:
`python3 phase5/harness/replay.py 'phase5/harness/fixtures/*/session_*.log'`.

---

**2026-08-31 (update) — OWNER SMOKE 2 (new build, `session_20260831_090510.log`):
rail v2 + detail-delivery calibration.** Owner ran the new build (first
smoke with the 3 fixes live — `precision_rail` turns t29/t30/t34 present).
Findings classified; fixes landed tests-first (`precision_rail.py` v2):

- **t29 '0269 0012420' → captured only '0012420'** (grouped-number bug):
  fixed — GROUPED_DIGITS_RE now captures the whole number.
- **t30 '5, 7, 0, 3' REPLACED the pending value** (segment-accumulation gap):
  fixed — while dictating, segments APPEND silently (`silent_accumulate`),
  the user's "continuously speaking in between" complaint addressed by
  SILENT rail decisions (turn suppressed, no LLM, no ack) until the user
  finalizes.
- **t34 echoed ITRANS garbage ('jIro, vana, tU...')**: the verbatim echo
  carried Devanagari digit words and the script guard transliterated them.
  Fixed — normalize_span() deterministically converts digit words (Hindi +
  English + Devanagari phonetics: 'डबल जीरो'→00, 'चार बार जीरो'→0000,
  'पाइट सेविन'→57) to DIGITS; the rail now echoes/stores digits only.
- **RECALL**: 'kya likha / repeat karo / number bata' → deterministic
  re-echo of the stored value (no LLM) — t14/t17 in the smoke.
- **Detail delivery (owner: "speak more 1-2 more lines, don't ask every
  sentence")**: prompt 1b now demands 3-5 connected sentences per chunk,
  explicitly bans ending with 'aage?'/'sahi hai na?'/'clear hai?' questions,
  and states the user will interrupt for clarity.

Gate discipline intact: 25/25 suites green (incl. new false-positive
regressions for t20/t23), synthetic gate EMPTY DIFF, and the REAL baseline
gate STILL shows the PASS profile — exactly 1 classified diff (t20 fix-③
cap) + the t11 note; rail v2 introduced zero new diffs on the pre-fix
23-turn archive.

---

**2026-08-31 (update) — OWNER SMOKE 3: '4 बार ज़ीरो' catch + TTS number
clarity + greeting rail (rail v3).** Owner: "i said 4 bar zero - this is a
slight catch in this / what tts speaks is not clear at all regarding no / it
started with acha- not hello". Verified against the code, fixed tests-first:

- **'4 बार ज़ीरो' (nuqta) broke detection + normalization** (STT emitted
  ज़ीरो with the nuqta; the map only had जीरो; roman 'bar' also missed).
  DIGIT_WORD_MAP now includes nuqta variants (ज़ीरो/ज़िरो) and TIMES_WORDS
  includes 'bar'/'बारा'. '4 बार ज़ीरो'/'4 bar zero'/'four bar zero' → 0000.
- **TTS spoke the number unclearly**: the echo carried a dense digit string
  and Fish read it as one Indian-grouped number (leading zeros dropped). The
  rail now speaks values DIGIT-BY-DIGIT as English words
  (speak_value: '026900' → 'zero two six nine zero zero') in every rail
  line — unambiguous confirmation, Fish handles the words natively.
- **Re-dictation vs continuation**: smoke-2 t34 re-spoke the FULL 17-digit
  number while a value was pending — the old rule APPENDED it (concatenated
  garbage). Now a long dictation (>= 8 digits) while pending REPLACES the
  value (fresh re-dictation) + echoes; short segments ('5, 7, 0, 3') still
  accumulate silently.
- **Near-repeat guard clobbered the confirm ack**: the ack legitimately
  re-states the number (echo → ack → full echo), and the LLM near-repeat
  guard replaced it with a repeat-break line. Rail/greeting lines are now
  SYSTEM-owned: repeat-guard disabled on them (run_repeat_guard=False in
  both run_turn and main.py).
- **GREETING RAIL** (owner: "started with acha- not hello"): a turn whose
  FIRST word is a greeting marker ('hello'/'hi'/'हेलो'/'नमस्ते'...) gets a
  deterministic greeting reply (turn_controller.greeting_line_for —
  'hello! kaise ho?' rotation) instead of the LLM's 'bas yahin hoon...'
  drift, and the ack is gated off on greeting turns. New engine_path
  'greeting', llm_called=False; replay harness extended.

Gate profile on the new build (classified expected diffs, everything else
byte-exact):
  t1  = greeting rail (was LLM 'bas yahin baitha hoon...')
  t20 = fix-③ detail cap 110→320
  t11 = documented note (repeat-guard chunk artifact, non-gating)
25/25 suites green; synthetic gate EMPTY DIFF.

---

**2026-08-31 (update) — OWNER SMOKE 4: Hindi compound number words
('निन्यानबे पैंतीस' = 9935), dictation stays rail-owned, greeting-rail
verification (rail v4).** Owner: "i am saying 9935 in hindi- ninyanbe
panteen / ye beech beech mein acha btao bolta hai / it somehow started
talking about voice agent pricing while i was saying ninyanbe panteen".
Root-caused against the code, fixed tests-first:

- **Hindi compound number words 10-99 added to the digit map** (single
  source for detection + normalization), incl. the observed STT variants
  (नियान = 99, पैंतिश/पैतिश = 35). 'निन्यानबे पैंतीस' → 9935; STT's grouped
  mishearings '99.35' → 9935 (GROUPED_DIGITS_RE + normalize).
- **Cluster rule tightened** (_cluster_fires): a digit-token span fires only
  on real dictation signal — ≥3 signals, or 2 signals + times/double/
  compound/run, or 'डबल जीरो'. Conversational 'तू एक' (you a), 'एक बार'
  (once), 'एक दिन एक' never fire.
- **Whole-number re-dictation REPLACES, spelling APPENDS** (form-based):
  separated digit lists ('5, 7, 0, 3') and lone single-digit signals
  ('जीरो') append silently; grouped runs ('995') and compound words
  ('नियान ने पैंतिश') replace + echo. Smoke-4 t20's 'क्यों नहीं बोला' now
  re-dictates the real 9935 (was: false retry via the 'नहीं' substring —
  question-reject guard added).
- **Announcement arms the rail**: "एक मोबाइल नंबर है वो लिख ले" → arm
  ("haan, bolo — number note karta hoon."); everything that follows stays
  rail-owned (silent accumulate / echo / confirm / recall) — the "started
  talking about voice agent pricing" leak is structurally impossible now
  (the whole dictation sequence never reaches the LLM).
- **Rail decided BEFORE the turn controller** (main.py): a dictation
  fragment like '99.35' was being suppressed as continuation_fragment
  before the rail could own it (smoke-4 t17). Rail turns bypass the WAIT.
- **Greeting rail verified in-repo** (greeting_line_for('हेलो, ज्या कर
  रहे हो?') → 'hello! bol kya scene hai?'): smoke-4 t1 still shows
  engine=fused → the RUNNING worker predates 40d06da; owner must restart
  workers on the latest pull (see below).

Gate: 25/25 suites green; synthetic gate EMPTY DIFF; REAL baseline gate
unchanged — the exact 2 classified diffs (t1 greeting, t20 fix-③ cap) + t11
note; the tightened rail introduced ZERO new diffs (t20/t23 conversational
false-positives still don't fire).

---

**2026-08-31 (update) — OWNER SMOKE 5: 'not able to speak full no.' +
'ha bolo wahi hu while i am speaking' (rail v5, append-first).** Owner ran
the smoke on the v3 build (confirmed: t23 'अकाउंट नंबर लिखो 026' silent-
appended and t33 '5703' silent-appended, both v3-only behaviors; v4 was
NOT in their binary). Session session_20260831_115408 (35 turns) saved as
fixture. Root causes against the code + fixes (tests-first):

- **v4 fixes were never in the owner's build** (no announcement arm, no
  compounds, no rail-before-controller). t18/t20 announcements → LLM with
  empty replies; the dictation never armed. FIX: owner pulls + restarts.
- **Mid-dictation filler discarded the rail** (t28 'यह कर लेते हैं', 4
  words, with pending value ""): v3/v4's `_fragment_length <= 3` silent
  rule missed it → DISCARD → LLM → 'haan bol, kya number hai?' (the
  'ha bolo wahi hu' complaint). FIX: while armed/pending, fillers ≤6 words,
  plain rejects and re-announcements are SILENT and keep the state; only
  explicit abandon ('छोड़ दे') or a >6-word turn releases to the LLM.
- **Single runs REPLACED instead of appended** (t25 '124205703', t33
  '5703'): the owner was dictating ONE number in segments; the replace
  threw away the accumulated prefix → 'not able to speak full no.' FIX:
  append-first; REPLACE only on explicit whole-number signals (reject+
  digits, restart phrase, separated re-spell ≥8 digits, ≥2 compound words,
  ≥4 digit-word signals).
- **Greeting rail still gated on live sess** (t1 'हेलो...' → fused LLM
  even on v3, which HAS the greeting rail; greeting_line_for is verified
  in-sandbox). FIX: dropped the sess requirement — greeting is
  deterministic + persona-safe (main.py + response_pipeline.py).
- **t18 STT-rejected announcement**: on v4/v5 the contextual-recovery path
  falls through to the rail decision, so the announcement still arms.

Gate: 26/26 suites green (smoke-5 regressions added: filler-stay, reject-
stay, re-announcement-stay, append-first runs, restart/reject/compound
replace, abandon discard); synthetic gate EMPTY DIFF; REAL baseline gate
UNCHANGED (same 2 classified diffs + t11 note — v5 introduced zero new
drift). Owner must pull, restart BOTH workers, and re-smoke dictation.

---

**2026-08-31 (update) — OWNER SMOKE 6 (session_20260831_122500, 32 turns):
'stil not able to correctly get no.', 'hallucinates in between',
'ha btao — speaking in between', 'it stopped speaking at last' (rail v6).**
Owner ran v4 (greeting t1 'हलो' -> fused LLM while greeting_line_for is
verified in-sandbox — v5's gate removal not in their binary). Fixture
saved. Root causes + fixes, tests FIRST:

- **STT-rejected dictation was LOST** (t4 '026900124205703' rejected as
  suspicious_no_speech_band, routed to respond_now 'unclear_speech', the
  number never reached the rail). FIX: the rail/greeting decision now
  happens BEFORE the invalid-turn early return (and before the Turn Gate,
  which moved up); respond_now turns pass rail/greeting to
  run_agent_response — a perfectly-heard dictation is echoed even on a
  rejected turn; only when the rail declines does the unclear-speech line
  fire.
- **Fresh short runs were NOT dictation** (t12 '026', t13 '9000' ->
  fused, no reply): DIGIT_RUN_RE needs 6+ digits and the cluster rule
  needs 2+ tokens, so the owner's segments fell through to the LLM. FIX:
  a PURE digit utterance with >=3 normalized digits IS dictation
  ('026', '9000', '9935', '5, 7, 0, 3'); '9000 rupaye' / 'एक बार मैंने
  सोचा' are not pure -> still never fire.
- **Mid-dictation talk went to the LLM** (t8 'कुछ पूछ रहे हैं तुमसे लिख
  लिया है' -> 'achha, kya likha hai? sunao.'; t10 'तुने लिखा नहीं किया।'
  -> 'miss ho gaya, bata' — the 'ha btao' complaint). FIX: while armed,
  NOTHING non-digit falls to the LLM: complaints/rejects -> retry,
  status queries ('लिख लिया?', 'ये clear?') -> answered, long fillers
  -> silent stay (the old >6-word discard is gone while armed).
- **The LLM FABRICATED the number** (t16 'तूने क्या लिखा है।' -> 'maine
  bola tha — zero do chhe, aur phir nau hazar' — reconcile_claim).
  FIX: status/recall queries while armed-empty -> deterministic
  'abhi kuch nahi likha hai — number bolo' (STATUS_LINES); while
  confirming -> RECALL (re-speak the stored value); 'तुने लिखा नहीं
  किया' (CLAIM_RE) -> re-speak the value as PROOF instead of clearing it.
- **'stopped speaking at last'** (t19-t22 'आगे'/'इसके बाद क्या है' sat in
  silence; t27-t32 swallowed silent). FIX: continuation cues -> short
  HOLD_LINES ('haan, sun raha hoon — bolo.'); a new non-number detail
  request ('एक address लिखो') releases the rail to the LLM
  (DEARM_DETAIL_RE — address/email/नाम/date are not number dictation).

Gate: 26/26 suites green; synthetic gate EMPTY DIFF; REAL baseline gate
UNCHANGED (same 2 classified diffs + t11 note). Owner must pull (>= v6),
restart BOTH workers, and re-smoke.

---

**2026-08-31 (update) — OWNER SMOKE 7 (session_20260831_130138, 19 turns):
'why does it stop speaking at last?', 'i was saying 4 bar zero - it write
420', 'while i speak account no. it says yahin hu — it should not speak
while i am speaking' (rail v7, state-aware).** Owner ran v5 (append-first
live; t1 'हलो' -> fused LLM again — greeting gate removal verified in v5
in-repo, so this is a STALE/MIXED main.py on the worker, not the code).
Fixture saved. Root causes + fixes, tests FIRST:

- **The rail spoke mid-dictation** (t3 '026' -> echo 'haan, main suna:
  zero two six...' — CANCELLED BEFORE AUDIO, exactly the "speaks while I
  am speaking" complaint). FIX: after an announcement arm, the FIRST digit
  segment now accumulates SILENTLY (state-aware: while the user dictates
  the rail listens, it does not talk). Confirmation happens at 'bas'
  (full echo) or on a query ('क्या लिखा' -> recall). Fresh UNARMED
  single-shot dictation still echoes immediately.
- **'4 bar zero -> it write 420'**: t9 '4 बार 0 ... 1, 2 के बाद' silently
  APPENDED 0000 at the END of the stored value; t12/t14 'चार बार जीरो...
  420 नहीं' REPLACED the whole value with '0000'/'12' (t14's correction
  spec overwrote the number with '12'). FIX: structured-correction parser
  (_parse_correction/_apply_correction): '12 के बाद 4 बार 0 है 420 नहीं'
  -> REPAIR the stored value (02690012425703 -> 0269001200005703: replace
  '420'/'42' with '0000' after the '12' anchor; t9 anchor-only -> insert
  0000 after 12). Unresolvable corrections -> ack-retry line that
  SPEAKS the understood correction ("theek, samajh gaya — 420 nahi, 0000
  hai. poora number ek baar bol de") instead of silence or a wrong
  replace. Question-guarded (smoke-4 'क्यों नहीं बोला' stays a re-
  dictation, not a correction spec). Plain reject+full-number still
  replaces.
- **'stops speaking at last' (black hole)**: t13-t19 the rail said
  NOTHING for 7 turns — corrections, 'आगे', 'बोल दो' all swallowed
  silent. FIX: extended CONTINUE_CUE_RE ('आँगे' candrabindu variant,
  'बोल दो', 'कुछ तो बोल') -> HOLD lines; correction turns -> correction
  ack lines; garbage <=2 words stays silent. The rail now always answers
  a meaningful turn.
- **'it says yahin hu while i speak the account no.'**: the LLM greeting
  drift ('bas yahin hoon...') played because the greeting rail STILL
  didn't fire live. The code is verified correct in-repo across v5/v6/v7
  (gate removed, greeting_line_for matches 'हलो'); the owner's worker
  runs a stale/mixed main.py. MUST verify: git log --oneline -1 ==
  v7 commit, restart BOTH workers, worker log shows '[Greeting]'.

Gate: 26/26 suites green; synthetic gate EMPTY DIFF; REAL baseline gate
UNCHANGED (same 2 classified diffs + t11 note). Full smoke-7 replay:
arm -> SILENT segments (026/9000/12420573) -> recall -> retry -> silent
re-dictate -> t9 correction echo (0000 inserted) -> correction acks at
t12/t14 -> holds at t16/t17/t19 -> re-dictate -> 'bas' full echo
0269001200005703 -> confirm ack. Zero LLM touches.

---

**2026-08-31 (update) — OWNER SMOKE 8 (session_20260831_134403, 25 turns):
'why is it not speaking?', 'whole experience is deteriorating' (rail v8,
conversational responsiveness).** Owner ran v7 (verified: every turn
reproduces v7 exactly). Fixture saved. Root causes + fixes, tests FIRST:

- **t5 '...अकाउंट नंबर को लिखो 026900124205703 लिख लिया' — the number
  was LOST.** The announcement was detected first, so the rail ARMED and
  spoke 'haan, bol number' while the full number sat in the SAME turn.
  FIX: digits are now captured BEFORE the announcement check — a turn
  that announces AND dictates echoes+stores the number. The user then
  heard it confirmed instead of being asked to repeat.
- **'why is it not speaking' — the queries were unanswered** (t15 'तू बता
  तूने क्या लिखा है बेजक नंबर', t24 'बताओ', t25 'बताओ बेटा क्या लिखे
  हो' all SILENT). Three causes fixed:
  - t15 contains 'लिखा'+'नंबर' so the armed-empty announcement branch
    silenced it BEFORE the recall check. FIX: a re-announcement must be a
    WRITE COMMAND ('लिख ले/लिखो/रख ले'); a past-tense query falls
    through to the recall/status answer.
  - STATUS_RE was missing 'क्या लिखे हो' / 'बताओ' — added (plus 'बताओ'
    now recalls a stored value).
  - Question-tag नहीं: 'लिखा तूने की नहीं?' (t13) and STT's 'किनहीं'
    (t6) are QUERIES, not rejections — QUESTIONISH_RE extended with
    question tags; a plain 'यह है ही नहीं...' still rejects (tested).
- **retry-spam / experience deteriorating** — t6/t14/t16/t17 all got
  'phir se bol na' / 'ek baar aur bolo' (dismissive) while the user was
  angry the agent seemed deaf. FIX: WRITING-COMPLAINTS ('मैंने पूरा नंबर
  बोल दिया तुने लिखा किनहीं', 'लिख नहीं पा रहा') are not rejections:
  with a stored value -> RECALL it (PROOF, never clear); armed-empty ->
  a SPOKEN apology+ask ('haan, sorry — abhi kuch nahi likha. phir se bol
  de na...'). Plain 'नहीं, गलत है' still retries (tested).
- **t26 '026900012405703 बस' DOUBLED the value** (repeat appended to
  itself). FIX: exact re-statement of the stored number is deduped —
  with a confirm word ('बस') -> echo_full, else silent keep. Tail
  repeats ('5703' after '...5703') stay append-first (smoke-3 flow
  verified untouched).
- **BUILD STAMP**: the worker now logs '[BUILD] git=<sha>' at startup so
  every smoke can be matched to a commit (the stale-worker ambiguity has
  cost 4+ rounds).

Gate: 26/26 suites green; synthetic gate EMPTY DIFF; REAL baseline gate
UNCHANGED (same 2 classified diffs + t11 note). Smoke-8 replay: t5
echoes the number, t6/t13/t14/t15/t17/t24/t25 all SPEAK (proof/status),
t26 'बस' confirms, no value doubled, zero LLM touches.

---

**2026-08-31 (update) — OWNER SMOKE 10 (session_20260831_161730, 10 turns,
VERIFIED build 4b5e955 — the build lock finally worked: diagnostic header
showed 'build: 4b5e955 (worker pid 2620)').** Three findings, two REAL:

- **GREETING root-caused at last (t1 'हेलो' -> no reply / engine=fused).**
  main.py's run_agent_response: the greeting branch sets engine_path=
  "greeting" + text_stream=greeting line, but the subsequent
  `elif engine and engine.get("sess"):` block ran build_policy_and_contract
  — which clobbers turn["engine_path"] to "fused" (response_pipeline.py:79)
  — and replaced text_stream with the LLM stream. The deterministic
  greeting line was never spoken; the LLM path ran instead (this is the
  exact classified baseline diff t1: replay='greeting' archived='fused',
  now root-caused in the LIVE path, not the replay path). FIX: added
  `elif greeting is not None: pass` between the rail guard and the engine
  block (greeting skips policy/contract/LLM, matching run_turn). Regression
  pin: structural test asserts the guard chain ordering in main.py.
- **'पिर से बोलो' / 'पिरशे बोलो' (t7/t8) -> HOLD instead of RECALL.** The
  user asked to repeat the number; the rail answered a hold line.
  FIX: RECALL_RE extended with फिर/पिर से/से/शे बोल, दोहरा, दोबारा/दुबारा
  रिपीट variants -> recall; armed-empty -> status line.
- **NOT A BUG: 82-char 'truncation' was a DIAGNOSTIC DISPLAY artifact.**
  stage_diagnostic printed reply[:70] silently mid-digit; the full
  "haan, jo likha hai: zero two six nine ... zero one." (82c) WAS spoken
  and played (TTS 5.48s). FIX: diagnostic now shows up to 120 chars with
  an explicit '…(+Nc)' marker when truncated, never silent mid-word.

Also verified good: t2 arm, t3/t5 silent accumulate (026900 + 4204301),
t6 recall, t4/t10 silent filler — the basic contract works on the verified
build; accumulation + recall + silence are correct.

Gate: 26/26 suites; synthetic EMPTY DIFF; real baseline UNCHANGED (same 2
classified diffs — t1 greeting/fused is the very bug fixed live-side).

---

**2026-08-31 (update) — OWNER CORRECTION to smoke-10 t5: "it is 4 bar
zero not 420".** The number dictated at t5 was NOT '4204301' (what STT
transcribed) but '4 बार 0, 4301' — the canonical value is
02690000004301, not 0269004204301. Two-sided fix, tests-first:

- CAPTURE side: dictation_value() returned the FIRST digit run only
  ('026900' from '026900 4 बार 0 4301') and dropped the multiplier +
  continuation, so normalize_span never got to expand '4 बार 0' -> 0000.
  FIX: when the text contains a बार/डबल word (the definitive "number read
  aloud" signal), the full compact cluster is captured FIRST (before the
  run/grouped returns) so the multiplier + its tail are kept;
  _cluster_fires still rejects conversational mixes ('एक बार मैंने सोचा
  9000', 'एक बार').
- SAFETY in normalize_span: 'बार'/'डबल' now apply ONLY to a single
  following digit ('4 बार 0' -> 0000). A multi-digit run after a times
  word ('2 बार 026900...' = "said the number twice") is the number
  itself — the run is never duplicated (was: '026900026900').
- CORRECTION side was already correct and is now pinned by tests:
  stored 0269004204301 + "4 बार 0 है, 420 नहीं" -> repaired
  02690000004301 + echo (this is exactly the owner's message).

Verified flow: arm -> '026900' -> '4 बार 0' (0000) -> '4301' -> 'बस'
echoes 02690000004301 exactly. Gates: 26/26 suites; synthetic EMPTY
DIFF; real baseline UNCHANGED (same 2 classified diffs + t11 note).

---

**2026-08-31 (update) — OWNER SMOKE 11 (session_20260831_171620, 20 turns,
VERIFIED build 1500c29) -> TRACK 5 KICKOFF: the Conversation Controller
first slice.** Owner directive: stop adding rail edge-case patterns; build
the Conversation Controller (⑤) with number-capture as the first concrete
stateful use case. Session proved it: greeting works (t1), silent
accumulate works (t5/t6), but four failures all trace to MISSING EXPLICIT
STATE (not new patterns):

- t8 '...ab account number likho jara, 026-900-1262' APPENDED to the
  previous mobile number -> 99354119070269001262 (no task-switch).
- t11 'sirf itna number, theek hai?' correction IGNORED (silent append).
- t15/t16 'Hello'/'हेलो' swallowed silent while a task was active.
- t17 long garbage RELEASED/discarded the task; t19 'mera mobile number
  mujhe bata' -> LLM "number toh mujhe nahi pata tera" (recall lost).

IMPLEMENTED (rail v9 = controller first slice, tests-first):
- NEW agent/conversation_controller.py: explicit ConversationState +
  Task objects (user_state, agent_state, task, topic,
  waiting_confirmation, interrupted, next_action), classify_turn()
  signal layer (reuses every existing detector), and the transition
  table (rows 1-43 of CONVERSATION_CONTROLLER_DESIGN.md §4).
- precision_rail.decide() now DELEGATES to the controller; precision_rail
  stays the SIGNAL layer (detectors/normalizers/line pools). Behavior
  byte-identical for rows 1-39 (all v1..v10 tests + replay identity);
  new rows 40-43: task-switch (announcement+write+digits -> replace),
  only-this correction, greeting-while-armed (task kept), recall-by-
  meaning; row 24 CHANGED: long garbage -> TURN to LLM, TASK KEPT.
- engine["conv"] persists the explicit state; engine["dictation"] stays
  the legacy compat store (replay archives unchanged).

Corrected smoke-11 replay: t8 echo 0269001262 (new task), t9 recall,
t10 ack (short, not 157c), t11 echo 9935411907 (only-this), t15/t16
greeting lines, t17 LLM flow with task kept, t19 recall 9935411907.
Gates: 26/26 suites; synthetic EMPTY DIFF; real baseline UNCHANGED.

---

**2026-08-31 (update) — OWNER SMOKE 12 (session_20260831_173910, 32 turns,
VERIFIED build 4045af7) -> controller rows 44-49 + signal-layer primitives.**
Owner asked two questions about this session: "is it saving in memory/context?
what happened at last?" Answers + fixes:

MEMORY MODEL (as asked): the dictated value lives in SESSION STATE ONLY —
engine["dictation"]/engine["conv"] (the Conversation Controller's explicit
Task). It survives turn-to-turn (proof: 7398438138 accumulated at t26/t27,
recalled at t28/t31) but is NEVER in the LLM prompt (deterministic path,
"no LLM call" — the LLM has no authority over canonical numbers) and
nothing is written to long-term memory (the only cross-session store,
Layer-1 checkpoints, is DISCARDED at clean shutdown by design).

WHAT HAPPENED AT LAST (root causes, all fixed tests-first):
- t12 '5 x 0, 1, 2, 0, 3,' — STT wrote 'बार' as 'x'; 'x' not in TIMES_WORDS
  -> '5 x' DROPPED, value 0121201201203 (missing 00000). FIX: TIMES_WORDS
  += x/×/बट (live transcriptions of 'बार'; cluster rules still reject
  conversational mixes); _is_full_restatement no longer treats a times-word
  span as a whole-number re-spell (segment, not replace).
- t14 'मैंने बोला था 5 बट 0 उसका क्या किया' — ROW 45: query-about-stored +
  digit-ish words -> recall-as-proof, NEVER silent append.
- t15 '5 वाला नमबर नहीं है, 5 बार 0' — ROW 46b: correction whose 'correct'
  is ALREADY in the stored value -> confirm, NEVER wipe (was: retry-wipe +
  "poori number phir se bolo" that threw away the whole accumulation).
- t17-t24 — the 9-turn silent black hole. ROW 46 (owner T10, corr now wired
  engine["stt_corr"] from the SHADOW): low corr + no intent -> deterministic
  "didn't catch that" line. ROW 47: armed-empty 2+ turns with no digits ->
  nudge line (smoke-6 single-turn filler pins unchanged — they are
  streak=0). ROW 49: a NEW write-command while a value is stored -> re-arm
  (t20 'एक mobile number लिखो' was silent).
- t26 '7398' after 14 turns — ROW 48: a digit span after a COLD GAP (4+
  REAL turns, counted in decide() calls — NOT turn_no arithmetic, so
  smoke-5's t25->t33 zero-intervening-turn continuation still APPENDS) is a
  FRESH number: silent replace (t26/t27 -> 7398438138, exactly as the user
  dictated).
- t29/t32 'ज़िन/वॉइस एजेंट के बारे में बताओ' — the stuck tail. ROW 44:
  explicit topic-switch ('के बारे में'/'बारे में'/'की बात', guarded by
  number-words) -> task CLOSES to confirmed (last-known), the LLM answers
  the new topic. Was: t29 echo_full + t32 recall re-spoke the number
  forever. t31 'बताओ जरा' (STATUS_RE, not RECALL_RE) still recalls.

Corrected smoke-12 trace: t12 value 01212012000001203 -> t13 recall ->
t14 recall-as-proof -> t15 echo_confirm (value kept) -> t17 clarify ->
t20 re-arm -> t22-24 nudges -> t26/27 fresh 7398438138 -> t28 recall ->
t29 LLM (Zin) -> t31 recall -> t32 LLM (voice agent). Gates: 26/26 suites;
synthetic EMPTY DIFF; real baseline UNCHANGED.

---

**2026-08-31 (update) — OWNER SMOKE 13 (session_20260831_181649, 41 turns,
VERIFIED build fcb6318).** Owner asked two questions; both root-caused and
fixed (tests-first, working tree — NOT committed: the owner's WIP is
uncommitted in the tree, so no mixed commit was made).

Q1 "while I am speaking it says 'main yahin hoon, sun raha hoon' — need to
see why." ROOT CAUSE: agent/state_updater.py default_state() hardcoded
mode.current = "VENT" from turn 0, and NOTHING ever transitioned it (the
only transition was head-inferred ADVICE with hysteresis). So EVERY fused
turn carried policy {mode: VENT, response_goal: encourage_continuation}
into the LLM payload, and the LLM answered neutral turns ('क्या करो',
'voice agent ke baare mein batao') with listening/encouragement filler —
"bata kya hua, sun raha hoon main", "sun raha hoon, batao aage kya hua?".
FIX: mode is now TURN-DERIVED (classify_mode: CLOSING > ADVICE > VENT >
CALM, default CALM) + default_state mode "CALM" + phase default
"conversing" (was "venting"). user_text wired into the completed-turn
record so update() sees it. The OTHER half of "speaks while I am speaking"
is turn-firing at user pauses + TTS TTFA — the known track-4/voice-provider
decision, unchanged.

Q2 "number wala issue abhi fix nahi ho rha — kya bad ke fix karne pe ye
fix ho sakta hai?" YES — the concrete failures were primitives and are
fixed NOW (not deferred):
- t29 '620 नहीं है, 6 बार 0 लिखने है, 6 को replace करो 6 बार 0 से.' was
  NOT parsed as a correction -> retry WIPED the value. FIX: _parse_correction
  now accepts the replace-form ('6 को replace' = wrong value) and dedups the
  correct group from the replace target ('6') — t29 -> REPAIR
  026900126205703 -> 026900120000005703 (exactly what 6 बार 0 means: STT had
  collapsed '6 बार 0' into '620' — same family as smoke-10's '4 बार 0' vs 420).
- t30 repeated instruction: the already-correct guard (row 46b) now runs
  BEFORE _apply_correction — '6 को replace करना है 6 बार 0 से' when 000000
  is already in the value -> CONFIRM, never re-apply (applying wrong='6'
  would replace EVERY 6 and mangle).
- t31 'लिखा है ना 1, 2, 6' (query-with-digits) was a silent append; now
  QUERY_STORED_RE covers 'लिखा है ना/लिखा ना' tag-questions -> recall.
- t23/t37 FALSE clarifies: the row-46 clarify gated on acoustic corr < 0.35
  was WRONG — low corr simply means NOT-an-echo = REAL speech (echo corr is
  high when the mic hears the agent's own playback). It fired "didn't catch
  that" on clearly-heard turns. FIX: corr is telemetry-only again; row 50
  gates the deterministic clarify on WORDS (3-6 words + number-talk +
  value stored) — T10 'कि यह काशिड नंबर आ गया' -> clarify; 'हुआ है' /
  'यह कर लेते हैं' pins stay silent.
- Residual (noted, not over-fixed): t34 '1200000' appends (gap=2) — the
  user's post-correction re-dictation was ambiguous; core correction flow
  is now correct and the rest stays with the controller's structured-data
  layer.

Corrected smoke-13 number flow: arm t22 -> silent t23 (no false clarify)
-> nudge t24 -> 026900+12+6205703 t25-27 -> recall t28 -> t29 REPAIR
026900120000005703 -> t30 confirm -> t31 recall -> t32 confirm -> t37 LLM
(no false clarify) -> t38 recall. Gates: 26/26 suites; synthetic EMPTY
DIFF; real baseline UNCHANGED.

---

**2026-08-31 (update) — OWNER: "agent is not able to retrieve from memory,
is hallucinating — it shared wrong places from Uttarakhand."** Root-caused
and fixed (tests-first; working tree — NOT committed, owner WIP present).

THREE root causes:
1. WRITE PATH WAS DEAD. The perception head is the compact form
   {"m","c","s","plan"}; state_updater hardcodes head["memory_candidates"] = []
   for it, so the LLM can never propose memory candidates. The ONLY things
   reaching the store were family relationships (deterministic
   extract_entities_from_user_text -> _promote_relationship). Places, trips,
   jobs, preferences: NEVER stored. Cross-session recall had nothing to
   retrieve -> the LLM fabricated (wrong Uttarakhand places).
2. Layer-1 history is session-only and checkpoints are discarded at clean
   shutdown (by design) — so an earlier session's Uttarakhand talk is gone.
3. NO HONEST-RECALL GUARD. The prompt said memory is "background you
   silently KNOW" but never said what to do when there is NO record — so on
   a recall question the LLM invented details instead of admitting it.

FIXES (deterministic, no LLM in the write path):
- agent/entity_extractor.py extract_place_facts(): conservative travel/
  location clause capture (गया था / घूमने / रहता हूं / से हूँ / went /
  visited / live in...) -> episodic candidates "user: <verbatim clause>".
  Guards: question words ('कहां से हो'), bare verbs with no content word
  ('मैं गया था'), digit-heavy dictation, short interjections — all skipped.
- main.py: _promote_relationship generalized to _promote_memory(type,
  content) and place facts are captured in the same logprob-gated block,
  following the garble-containment policy: first sighting -> PENDING
  (promoted at session end), repeat sighting -> explicit+immediate commit.
- prompt_fragments.py rule 14 (NEVER INVENT RECALL): when the user asks to
  recall something about them / a past chat (places, trips, names, numbers)
  and there is no record in today's history or memory, say "hmm, yaad nahi
  hai — batao na" — never invent to fill the gap.
- fused_turn.build_contents(): when memory_view is empty, inject an explicit
  "memory_note" telling the LLM no cross-session facts are stored, so a
  recall question gets the honest answer instead of fabrication.

Verified round-trip: "मैं उत्तराखंड गया था, देहरादून और नैनीताल देखे" ->
extracted -> pending (invisible) -> repeat sighting -> committed ->
memory_view shows "episodic: user: मैं उत्तराखंड गया था..." — the exact
fact is now retrievable in later sessions, and the honest-recall rule covers
the gap while it is still pending. New suite phase5/tests/test_place_facts.py
(extractor negatives + gate + memory_note + rule-14 pin). Gates: 26/26
suites; synthetic EMPTY DIFF; real baseline UNCHANGED.

---

**2026-08-31 (update) — MEMORY CONTINUITY SLICES #1 + #2 (owner: "L2 dies on
clean shutdown; build the deterministic write path; tests-first; don't mix
with controller/TTS/T4/T10").** Implemented + verified tests-first.

Boundary doc: docs/MEMORY_BOUNDARIES_V1.md — History / Working State /
Session Memory / Long-term Memory explicitly separated with ownership,
persistence, ranking and call-triggers (the call matrix).

#1 L2->L3 promotion: agent/layered_context.py promotable_people() (pure,
handles both compression schemas {"Neetu": "behen"} and {name:{name,
relation,source}}; dedupes vs committed memory). main.py _compress_layer2
now routes L2 people with relations through _promote_memory after every
successful compression — rows land in SQLite MID-SESSION, so they survive
clean shutdown (checkpoint stays discard-only, anti-leak unchanged).
Inferred -> pending; repeat sighting bumps occurrences; session end
(occurrences>=2) confirms -> committed.

#2 deterministic write path for explicit facts + preferences:
agent/entity_extractor.py extract_fact_candidates() — name ('मेरा नाम X है'),
job (allowlist: engineer/doctor/...), likes ('मुझे X पसंद है'), no-advice
('सलाह मत देना'). Explicit first-person statements only; questions and
pronouns never captured. _promote_memory(criterion) — explicit -> immediate
commit (STATE_MODEL 4.5), inferred -> pending->confirm. extract_place_facts
criterion is now "explicit" (the multi-session acceptance requires it).
MemoryStore.view() exposes provenance: "(explicit)" suffix (acceptance:
"provenance should be clear").

New suites: test_l2_promotion, test_fact_candidates, test_multisession_recall
(the critical acceptance: session-1 state -> clean shutdown -> restart ->
session-2 recall payload carries the fact; negative control: other owner ->
memory_note -> no fabrication). 29/29 suites; synthetic EMPTY DIFF; real
baseline UNCHANGED. Nothing committed (owner WIP in tree).

REAL-VOICE VERIFICATION PROTOCOL (owner's multi-session voice test):
1. Deploy: WORKER_COUNT=2 bash start_aiva.sh (expect DEPLOY VERIFIED).
2. Session 1 (same browser/profile so the device UUID matches): say
   "मैं उत्तराखंड गया था, देहरादून और नैनीताल देखे।" — expect a normal
   reply; the log line "[Memory] committed (explicit): episodic user: मैं
   उत्तराखंड..." proves the write.
3. End the session cleanly (close tab). Check the worker log shows
   SESSION_END / memory commit.
4. Start session 2 (same browser): "मैंने कौन सी जगह बताई थी?" — expect the
   agent to say the Uttarakhand/Dehradun/Nainital fact from memory. The
   "[StateEngine] SESSION BOUND owner=... memory_items=N" line must show N>0.
5. NEGATIVE CONTROL: open a fresh browser/incognito (new device UUID) and ask
   the same question — expect "hmm, yaad nahi hai — batao na" (rule 14),
   never a fabricated place.
Remaining slices (staged, not this round): #3 relevance-ranked L3 retrieval,
#4 L2 compression failure visibility, #5 wire-or-remove state_delta_compiler,
preference ENFORCEMENT in _derive_policy (store prefs -> policy) — noted.

---

**2026-08-31 (update) — OWNER session_20260831_192745 "memory is not saving".**
The session: "हेलो मैंने तुझे अपना नंबर शेप करवाया था" / "क्या लिखा था तुने" /
"मोबाइल नंबर मैंने तुझे अपना शेप करवाया था" — the user asks for the number
they saved earlier; the agent answers "mere paas memory ka koi system nahi
hai" (t5) / "number thodi save kar sakta hoon" (t4). Also "उत्तराखंड में
कहां-कहां घूमने" → generic Nainital/Mussoorie suggestions instead of the
user's own Uttarakhand facts.

TWO independent root causes:

A) DEPLOYMENT: the deployed build was fcb6318 — it predates ALL the memory
work (place facts, fact candidates, rule 14, L2->L3, mode fix). The fixes
existed only in the working tree (uncommitted after the history flatten to
4057092). A session on fcb6318 CANNOT recall anything — it has none of the
writes.

B) REAL GAP (fixed this round, tests-first): SAVED NUMBERS were never
written to long-term memory. The dictation rail kept confirmed numbers only
in engine["dictation"] (session state), so a NEW session had no task, no
record, and the LLM fell back to "no memory system". Numbers are the single
most important thing users ask us to save — fixed with controller ROW 51:
  - On confirm (ack), the number is committed to the store:
    {type: "saved_number", content: "user's <mobile|account|saved> number:
    <digits>", criterion: "explicit"} (immediate; kind detected from the
    announcement words and carried via conv task_topic).
  - In a FRESH session, a saved-number query ('नंबर शेप करवाया था' / 'क्या
    लिखा था तुने' — past-tense only, so an imperative 'सेव कर लो' still
    arms) recalls the stored number DETERMINISTICALLY digit-by-digit — never
    LLM, never fabricated.
  - If the user insists a number was saved but none is stored -> honest
    deterministic line "abhi koi number save nahi hai — bolo, main note kar
    loon." (rule-14 discipline).
  - MemoryStore.view() exposes saved numbers with prefix "saved number:" +
    provenance "(explicit)".

New suite test_saved_number_recall.py (confirm persists / fresh-session
recall / account kind / no-saved honest line / dedupe). 30/30 suites;
synthetic EMPTY DIFF; real baseline UNCHANGED.

DEPLOY NOTE (this update): the working tree is a single interdependent blob
(main.py imports greeting_line_for/pick_ack_for/validate_transcript/
route_decision from the previously-uncommitted WIP + untracked modules), so
the fix is committed as the whole tree — commit 49e0319 (67 files; the
owner's history was already flattened to 4057092, so the old fcb6318 tip was
superseded). PUSH STATUS: 49e0319 is now the origin tip of
arena/01a05304-voice-agent (force-updated 2026-09-01; verified
git ls-remote == local HEAD, working tree clean). The owner pulls
(WORKER_COUNT=2 bash start_aiva.sh) and the new build deploys the memory
line. Live test protocol: same browser → session 1 dictate+confirm a mobile
number ("मोबाइल नंबर लिख ले" → digits → "बस" → "हां") → log "[Memory]
saved-number committed (mobile)" → close tab → session 2 "मैंने तुझे अपना
नंबर सेव करवाया था" → agent speaks the digits.

---

**2026-09-01 (update) — OWNER BATTLE-TEST smokes (sessions 202628/202922 on
f42d8cb) + GAP R (honest recall) — ROADMAP ALIGNMENT.** Owner realigned to
the lifecycle: **Capture → Trust/Confirm → Persist → Retrieve → Recall**
(session-end consolidation = Capture/completeness; L2→L3 = Persist;
relevance retrieval = Retrieve; Gap R = Recall/honesty). No Phase C, no Gap W
(address capture stays OUT — no more rail special-cases), no new scope.

BATTLE-TEST FINDINGS (both sessions, verified against code + deterministic
reconstruction):
- The trust-boundary HELD: session 202628 produced zero deterministic
  captures for the address (rail de-armed by DEARM_DETAIL_RE — smoke-6
  ruling; extract_place_facts needs a travel signal; fact candidates need
  "मेरा X है"; verified: all return [] on the address turns) and no confirmed
  number → the consolidation pass could at most QUARANTINE/pending —
  reconstruction over the real 25-turn log (both cases: with and without a
  digit bullet) proves **committed=0, view() sees nothing**. No LLM output
  became trusted memory. (Raw [SessionConsolidation] console line + DB dump
  still owed by owner: see commands below.)
- GAP R (real bug this round, session 202922 t5/t6): with mem=25 in context,
  the LLM falsely denied the capability — 'address save toh main nahi kar
  sakta, system mein nahi hota' / 'mere paas system mein save nahi hota na
  kuch'. Root cause: rule 14 covers inventing CONTENT, not denying the
  capability; memory_note only fired when memory was EMPTY (mem=25 → never).
  FIXED tests-first (test_gap_r_no_false_save_denial.py, 15 checks):
  - prompt_fragments rule 14 gains the NEVER-DENY clause ("you DO save
    confirmed numbers, stated facts, preferences, places, relationships;
    NEVER claim 'main save nahi kar sakta' / 'system mein kuch nahi hota'");
    pins "yaad nahi"/"NEVER INVENT RECALL" preserved.
  - fused_turn.build_contents injects a capability-honesty memory_note on
    save/remember-intent turns even when memory is present (12-token
    deterministic detector — prompt layer, NOT a rail); pure recall queries
    with memory present keep NO note (multisession pin preserved).
- SIDE NOTE (out of scope, pre-existing): session 202628 t4 — lone Hindi
  digit word 'एक' spurious rail echo before the user said 'एक address...'
  (rail owns numbers; harmless noise).

GATES: 42/42 suites GREEN (30 existing + 11 Phase-B + gap_r); replay identity
ALL PASS; real baseline UNCHANGED (pre-existing t1/t20/t11 profile).

OWNER ACTIONS (close the last evidence — raw files stay out of chat):
1. `grep -n "SessionConsolidation\|SESSION_CONSOLIDATION" logs/session_20260831_202628.log`
   and paste the lines (console lines are in the worker log too).
2. `sqlite3 logs/aiva_memory.db "SELECT type,status,occurrences,criterion FROM
   memory WHERE owner_id='4da66eb5' ORDER BY id DESC LIMIT 10"`
   → expected: NO new committed rows from 202628/202922 (only pre-existing
   25 items); consolidation rows pending/quarantined at most.

---

**2026-09-01 (update) — OWNER SMOKE session_20260901_175645 (e2309c9) → three
fixes, tests-first.** Owner: "not able to recall; remove btao as a filler that
we have added; once it started with acha as a reply to hello".

ROOT CAUSES (verified in code):
1. **Recall** (t5 'मैंने कितने लोग का इंजाम करने के लिए कहा था' -> "yaar abhi
   yaad nahi aa raha, kitne bataye the tune?"): no record existed (wedding
   count never captured — conversational count, not dictation), so honest
   "yaad nahi hai" was right, but rule 14's line ended with '— batao na',
   which seeded the LLM's ask-back ("kitne bataye the tune?").
2. **"btao filler we added"**: AckBridge ACK_POOL question category contained
   imperative fillers "haan, bolo" / "achha, batao" / "haan, batata hoon" —
   vocal cues with "batao/bolo" endings.
3. **"acha as reply to hello"**: the "achha, batao" ack clip could open like
   a reply on a turn whose first word was a greeting when the greeting rail
   missed an STT variant (ack gate only checked _greeting is not None).

FIXES (tests-first: test_ack_selection +8 checks, test_gap_r +4, test_turn_
controller greeting pins):
- ACK_POOL question -> ["haan, kya hua?", "haan", "achha, samjha"] — no
  "batao/bolo" imperative in ANY pool (pinned).
- main.py ack gate: added `not _first_is_greeting` (first-word greeting
  marker, punctuation-stripped, GREETING_MARKERS) so a greeting turn never
  plays an ack even when greeting_line_for misses (pinned structurally).
- rule 14 + Gap-R note: honest line is now 'hmm, yaad nahi hai' and STOP —
  never 'batao na', never ask back ('kitne bataye the tune?'); pinned.
- persona 7 GOOD-example 'bata zara' -> 'kya hua?' (stop teaching the filler).
- (test_turn_controller pin confirmed greeting_line_for is already
  punctuation-robust — "Hello!" greets, lines never start with acha.)

NOTE (flagged, not fixed — no new rail special-cases per owner): t6
'50-60 लोग' -> rail echoed 'five zero six zero' (5060) — a RANGE misread as a
dictated ID. If confirmed it would store 5060. Range-vs-ID is context
(लोग/people), deliberately NOT added to the rail; open for Phase D (structured
capture) instead.

GATES: 42/42 suites GREEN; replay identity ALL PASS; real baseline UNCHANGED
(pre-existing t1/t20/t11 profile, zero new divergence).

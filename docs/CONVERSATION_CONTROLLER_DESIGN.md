# Conversation Controller — State-Model Design (track ⑤)

**Principle:** *Patterns can help detect signals, but state should determine behavior.*

**Phase goal:** prove that "conversation behavior can be controlled through explicit state,
without adding one more pattern per failure" — the direct successor of Phase 0's "we can
safely extract and reproduce existing behavior."

Status: **v1.4 — rows 44-49 IMPLEMENTED** (2026-08-31, owner smoke-12).
agent/conversation_controller.py owns the dictation decision (rows 1-49);
precision_rail.decide() delegates and stays the signal layer. Slice 1
(v1.3, smoke-11): behavior-preserving rows 1-39 + rows 40-43 (task-switch,
only-this, greeting-while-armed, recall-by-meaning, row-24 task-kept).
Slice 2 (v1.4, smoke-12) added the rows below + two signal-layer
primitives: 'x'/'×'/'बट' are now STT transcriptions of 'बार', and the
acoustic echo-correlation SHADOW (engine["stt_corr"]) became an input
(row 46). Remaining steps per section 7 (single-owner wiring, chat-state
generalization) are staged behind the owner's verified-build smoke.

---

## 1. Why this design, and why now

The precision-detail rail (agent/precision_rail.py, ~600 lines) is a working, fully-tested
state machine — but the state is **implicit**:

- `engine["dictation"] = {value, status}` overloads `status="pending"` to mean BOTH
  "armed, awaiting first digits" (value=="") AND "accumulating segments" (value!="") —
  two different user states, one string.
- Behavior is if-chains over `(seg, val, regex-hit)`. The **order** of the ifs *is* the
  priority table — it works, but it's invisible and each new smoke added another branch.
- There is no explicit "what the user is doing", "what the agent is doing", or "what
  should happen next" — those are re-derived from regexes every turn.

The evidence is the smoke history: v2→v8 each added 2–6 patterns (arm, append-first,
hold-the-floor, corrections, complaints, query-tags, dedup...). Every one of those is
really a **transition row** in a state machine. We're paying pattern-matching costs for
what should be a table lookup.

**The move:** extract the state machine the rail is already simulating, make the state
explicit and typed, and let behavior be a transition function. Incrementally — no blind
600-line rewrite; every extraction step must keep replay identity (real baseline gate =
same 2 classified diffs) and all suites green.

---

## 2. The boundary — Controller DECIDES, Rail ENFORCES (owner model, 2026-08-31)

The rail is not growing forever; it becomes the response-enforcement layer, and a new
Conversation Controller owns the conversation. The owner's mental model:

| Conversation Controller = decides WHAT IS HAPPENING | Rail = enforces THE RESPONSE |
|---|---|
| turn ownership | contract / safety |
| continuation / "aage" | response constraints |
| active explanation / multi-step delivery | anti-parrot |
| interruption + resume | script enforcement |
| correction / re-dictation | caps / delivery limits |
| current conversational goal | other deterministic guardrails |

```
        User → Controller → LLM → Rail → TTS          (conversational turns)
                 │               ▲
                 └─ task-state turns (dictation, recall, confirm) — the
                    controller routes to the rail DIRECTLY; the LLM never
                    sees dictated numbers/IDs (owner directive: LLM is not
                    the source of truth for structured details, PII no-store)
```

The rail is the response enforcer in BOTH paths:
- **LLM turns**: the LLM reasons freely; the rail applies contract/safety, anti-parrot,
  caps, delivery limits to the *response* (the existing reply_guard / response_contract /
  repeat_guard / release_from layer — it sits AFTER the LLM, as the owner's pipeline shows).
- **Task-state turns** (dictation / recall / confirm / hold): the rail IS the deterministic
  response source — the controller decides *what is happening* (user_state/agent_state),
  and the rail produces the enforced response. Zero LLM.

So the owner's pipeline is preserved exactly, with one explicit reconciliation: for
structured-detail turns the middle stage (LLM) is intentionally skipped by the controller —
that is the existing deterministic-dictation directive, now a *routing decision* instead of
a rail special case.

**Patterns detect signals → state decides → rail enforces.** The ~600-line rail is the
evidence: each v2→v8 addition was a transition row hiding inside an if-chain (section 4).

---

## 3. The state model


### 3.1 ConversationState (per session; lives in `engine["conv"]`, built by a new
`agent/conversation_controller.py`)

| Field | Type / enum | Meaning | Current home (implicit) |
|---|---|---|---|
| `user_state` | enum | what the user is doing RIGHT NOW | re-derived from regexes every turn |
| `agent_state` | enum | what the agent is doing right now | spread across main.py / response_pipeline |
| `task` | optional dict | active task: kind (`dictation` / `detail_delivery` / `general`), payload, status | `engine["dictation"]`; `engine["detail"]` |
| `topic` | str | active topic (e.g. "interview prep", "account number") | `sess` memory / lcm (fuzzy) |
| `plan` | dict | current_step, plan_total, resume_point | `head_plan`, detail latch, `remaining_text` |
| `delivery_mode` | enum | `chat` / `chunked_detail` / `recall` / `confirm_flow` | `detail_mode`, policy `delivery` |
| `waiting_confirmation` | Optional[what] | we echoed a value, awaiting yes/no | `dictation.status == "confirming"` |
| `interrupted` | Optional[dict] | last response was PARTIAL/UNHEARD: status + remainder | `engine["last_response"]` (Generated ≠ Delivered) |
| `next_action` | enum (derived) | what this turn should do (incl. `llm`) | the if-chain outcome |

### 3.2 user_state (dictation + general)

```
announcing   — told the agent a detail is coming ("number likh le")
dictating    — actively giving digit segments (first segment / continuation / re-dictation)
correcting   — fixing a digit-group ("12 ke baad 4 baar 0, 420 nahi")
querying     — asking about state ("kya likha?", "likh liya?", "kya likhe ho")
complaining  — claims the agent is deaf/wrong ("likha kinhinhi", "likh nahi pa raha")
confirming   — confirming/rejecting the echoed value ("haan" / "nahi, galat hai")
continuing   — continuation cue ("aage", "iske baad kya hai")
changing_task— new request (incl. non-number detail: "ek address likho")
abandoning   — dropping the dictation ("chhod de", "bhool ja")
conversing   — normal chat (LLM territory)
idle         — nothing pending
```

### 3.3 agent_state

```
listening   — silent, accumulating (dictation in progress)
echoing     — spoke an echo/confirm line
asking      — asked to repeat (retry)
answering   — spoke a recall/status answer
holding     — spoke a short hold line ("sun raha hoon — bolo")
processing  — LLM in flight
speaking    — TTS playing
awaiting_confirmation — echoed value, waiting
idle
```

### 3.4 Signals (the pattern layer — stays, but demoted)

`classify_turn(text, state) -> list[Signal]` — **pure**, reuses every existing detector
verbatim, they become signal classifiers:

`digit_segment`, `digit_full` (restatement signals), `correction{anchor,wrong,correct}`,
`announcement`, `query_status`, `recall_request`, `complaint`, `reject`, `confirm`,
`continue_cue`, `abandon`, `dearm`, `repeat_value`, `filler`, `garbage`.

Signals are **ordered by priority** (the current if-chain order, made explicit):
with a digit payload: `correction > digit_full > repeat_value > digit_segment`;
without: `recall > correction > complaint > reject > confirm > query_status >
continue_cue > dearm/abandon > filler > garbage`.

---

## 4. The transition table (dictation) — one row per learned behavior

Every row cites the smoke that forced it (the regression-test provenance).

Legend: `→` = new state; action in `mono`. States: `IDLE` `ARMED`(awaiting first digits)
`ACCUM`(accumulating) `CONFIRM`(echoed, awaiting confirm) `DONE`(confirmed).

| # | State × Signal | → State | Action | Provenance |
|---|---|---|---|---|
| 1 | IDLE × announce | ARMED | `arm` (speak "bol number") | smoke-4 t15 |
| 2 | IDLE × digit | CONFIRM | `echo_confirm` | v1/smoke-2 |
| 3 | IDLE × (digit + announce) | CONFIRM | `echo_confirm` (capture, don't arm-lose) | smoke-8 t5 |
| 4 | ARMED × digit_segment(first) | ACCUM | `silent_accumulate` (no talk while dictating) | smoke-7 |
| 5 | ARMED × query_status | ARMED | `status` ("abhi kuch nahi likha") | smoke-6 t5/t7 |
| 6 | ARMED × complaint | ARMED | `complaint_ack` (spoken apology+ask) | smoke-8 t6 |
| 7 | ARMED × reject | ARMED | `retry` | smoke-6 t10 |
| 8 | ARMED × correction | ARMED | `correction_ack` (spoken) | smoke-7 t14 |
| 9 | ARMED × announce(re) | ARMED | `silent` (write-command only) | smoke-5 t20, smoke-8 t15 |
| 10 | ARMED × continue_cue | ARMED | `hold` | smoke-6 t19 |
| 11 | ARMED × dearm/abandon | IDLE | release to LLM | smoke-6 t29 |
| 12 | ARMED × filler/garbage | ARMED | `silent` (never drop to LLM while armed) | smoke-8 t8 |
| 13 | ACCUM × digit_segment | ACCUM | `silent_accumulate` (append-first) | smoke-5 t25/t33 |
| 14 | ACCUM × digit_full | CONFIRM | `echo_confirm` (replace) | smoke-4 t20 |
| 15 | ACCUM × repeat_value | ACCUM/CONFIRM | `silent` or `echo_full` (no doubling) | smoke-8 t26 |
| 16 | ACCUM × correction | CONFIRM/ACCUM | `echo_confirm` (repaired) or `correction_ack` | smoke-7 t9/t14 |
| 17 | ACCUM × confirm | CONFIRM | `echo_full` ("bas" speaks the full number) | smoke-2 |
| 18 | ACCUM × reject | ARMED | `retry` (clear) | smoke-1 |
| 19 | ACCUM × query_status | ACCUM | `recall` (re-speak) | smoke-6 t5–t8 |
| 20 | ACCUM × complaint | ACCUM | `recall` (PROOF, never clear) | smoke-8 t6/t14 |
| 21 | ACCUM × continue_cue | ACCUM | `hold` | smoke-6 t19–t22 |
| 22 | ACCUM × dearm/abandon | IDLE | release to LLM | smoke-6 t29 |
| 23 | ACCUM × filler | ACCUM | `silent` | smoke-5 t28 |
| 24 | ACCUM × garbage(long) | IDLE | release to LLM | v1 legacy |
| 25 | CONFIRM × confirm | DONE | `confirm_ack` | smoke-2 |
| 26 | CONFIRM × reject | ARMED | `retry` (clear) | smoke-1 |
| 27 | CONFIRM × query_status | CONFIRM | `recall` | smoke-8 t15/t24/t25 |
| 28 | CONFIRM × complaint | CONFIRM | `recall` | smoke-8 |
| 29 | CONFIRM × correction | CONFIRM | `echo_confirm` (repaired) | smoke-7 t14 |
| 30 | CONFIRM × digit_segment | ACCUM | `silent_accumulate` | smoke-5 |
| 31 | CONFIRM × repeat_value | CONFIRM | `echo_full` / `silent` | smoke-8 t26 |
| 32 | CONFIRM × continue_cue | CONFIRM | `hold` | smoke-6 |
| 33 | CONFIRM × dearm/abandon | IDLE | release to LLM | smoke-6 t29 |
| 34 | DONE × query_status/recall | DONE | `recall` | confirmed-recall |
| 35 | DONE × digit/announce | CONFIRM/ARMED | fresh task | v1 |
| 36 | any × correction(multi-clause) | CONFIRM | apply clauses sequentially -> echo repaired | smoke-9 t7 ("9000 nahi, 900... 1240 nahi, 12...") |
| 37 | any × ambiguous correction | KEEP state | ack the understood part + ASK; NEVER clear (state-honesty invariant) | smoke-9 t7 (both builds lost the value: old build echoed the WRONG fragment 9000; current v8 retries+clears) |
| 38 | DONE/ACCUM × number-with-emphasis ('9900 be pagle') in correction context | CONFIRM | capture the number amid interjections -> canonical update | smoke-9 t11 (9900 never entered state; LLM drifted to "9900 ka hisaab") |
| 39 | CONFIRM/DONE × recall-query containing announce words ('acct number kya likha hai') | CONFIRM | RECALL wins over ARM (write-command gate; v8 already, make explicit) | smoke-9 t18 (re-armed 'bol number' instead of recalling) |
| 40 | ACCUM/CONFIRM × (announcement + write-command + digits) | CONFIRM | NEW task, echo (replace — never append a new task onto the old value) | smoke-11 t8 ('ab account number likho jara, 026-900-1262') |
| 41 | ACCUM/CONFIRM × 'only this' + digits | CONFIRM | replace + echo (trim to the stated number) | smoke-11 t11 ('sirf itna number') |
| 42 | ACCUM/CONFIRM/ARMED × greeting (first-word) | same | greeting line, TASK KEPT (was: silent) | smoke-11 t15/t16 ('Hello'/'हेलो') |
| 43 | ACCUM/CONFIRM × recall-by-meaning ('nambar kya hai', 'mujhe bata kya') | same | recall (was: LLM 'number nahi pata') | smoke-11 t19 |
| 24' | ACCUM/CONFIRM × long garbage | same | TURN to LLM, TASK KEPT (was: discard) | smoke-11 t17/t19 |
| 44 | ACCUM/CONFIRM/ARMED × topic-switch ('के बारे में'/'की बात', guarded by number-words) | CLOSE to confirmed (last-known) | LLM answers the new topic (was: stuck re-echoing the number) | smoke-12 t29/t32 |
| 45 | ACCUM × query-about-stored + digit-ish words ('बोला था...क्या किया') | same | recall-as-proof, NEVER silent append | smoke-12 t14 |
| 46 | ACCUM/ARMED × low corr (< 0.35) + no intent | same | deterministic 'didn't catch that' line (owner T10; corr now wired from the SHADOW) | smoke-12 t17, T10 |
| 46b | ACCUM/CONFIRM × correction whose 'correct' is already in the value | same | confirm, NEVER wipe (was: retry-wipe) | smoke-12 t15 |
| 47 | ARMED-EMPTY × 2+ real turns with no digits | same | nudge line (single-turn filler pins unchanged: streak=0) | smoke-12 t18-24 |
| 48 | ACCUM/CONFIRM × digit span after a COLD GAP (4+ real turns, counted in decide() calls, NOT turn_no) | same | FRESH number, silent replace (continuations with zero intervening turns still append) | smoke-12 t26 |
| 49 | ACCUM/CONFIRM × announcement + write-command, NO digits | RE-ARM new task | 'haan, bol number' (was: silent) | smoke-12 t20 |
| 50 | ACCUM × short number-talk (3-6 words, number word, value stored) | same | deterministic 'didn't catch that' (owner T10; word-gated — the acoustic corr gate was REVERSED: low corr = NOT echo = real speech, smoke-13 t23/t37) | T10, smoke-13 t23/t37 |
| 29'/30' | ACCUM × 'X नहीं है, Y लिखने है' / 'X को replace करो Y से' with multiplier correct | REPAIR | parse the replace-form; dedup the replace target from the correct group; already-correct guard BEFORE apply | smoke-13 t29/t30 |
| 31' | ACCUM × 'लिखा है ना' + digits | same | recall-as-proof (tag-question about writing) | smoke-13 t31 |


**Output of a transition:** `(new_state, action, value, status, line_pool, trigger_reason)`
— exactly the dict `decide()` returns today, plus the derived `response_trigger_reason`
(`user_speech_ended` / `completed`) so replay identity holds.

---

## 5. Current code → table (proof this is extraction, not a rewrite)

Direct mapping of today's `decide()` (read at v8, commit 56ce51b):

| Current code | Row(s) |
|---|---|
| `if active:` (status pending/confirming) | states ARMED/ACCUM/CONFIRM |
| `if seg: if not val:` | row 4 (ARMED × first digits) |
| `if seg: corr = _parse_correction` | rows 8/16/29 |
| `if seg: _is_full_restatement` | row 14 |
| `if seg: seg == val` (dedup) | rows 15/31 |
| `if seg:` else | rows 13/30 |
| `if val: RECALL_RE` | rows 19/27/34 |
| `if val: CLAIM/COMPLAINT` | rows 20/28 |
| `if val: _is_reject` | rows 18/26 |
| `if val: _is_confirm` (pending→echo_full, confirming→ack) | rows 17/25 |
| `if val: STATUS_RE / CONTINUE_CUE` | rows 19/21/27/32 |
| `if val: DEARM/ABANDON` | rows 22/33 |
| `if val: filler≤6 / long-garbage` | rows 23/24 |
| armed-empty block (corr/complaint/reject/abandon/re-announce/recall/cue/dearm/filler) | rows 5–12 |
| `if digits:` (unarmed) | rows 2/3 |
| `if is_dictation_announcement:` (unarmed) | row 1 |
| `confirmed + RECALL` | row 34 |

Nothing new is being invented — the table **is** the current behavior, made legible.

---

## 6. Beyond dictation: the full conversation state

The dictation table is the first slice. The controller's real scope (owner's list):

- **what the user is doing**: `user_state` above + chat states (asking/venting/answering/
  changing_topic) — the signal classifiers generalize (turn_controller's continuation
  cues, stt_validation's turn relation, fused_turn's triggers become signals too).
- **what the agent is doing**: `agent_state`; interruption uses Generated ≠ Delivered
  (`engine["last_response"]`) — `interrupted = {status, remaining_text}` drives resume
  ("aage?" after a barge-in continues from `remaining_text`, not from scratch).
- **active task/goal, active topic, current step/plan**: `task` + `plan` + `delivery_mode`
  — absorbs `detail_mode` latches, `head_plan`, PLAN_CHUNK_CAP logic.
- **waiting for confirmation**: `waiting_confirmation` (dictation confirm_flow AND
  general confirmations).
- **what should happen next**: `next_action` computed for EVERY turn; the LLM runs only
  when `next_action == llm` (state decides, LLM reasons inside the rail).

**Single owner:** the controller becomes the ONE turn-end decision (replacing the current
scatter: precision_rail_decide + turn_controller_decide + greeting_line_for + detail
latches + ack gating). The ack bridge, repeat guard, greeting all consult `next_action`.

---

## 7. Incremental migration (each step: tests-first + full gate)

| Step | What | Gate |
|---|---|---|
| 0 | Freeze rail v8 (commit 56ce51b) as the behavior baseline — **no new rail
  patterns** while the controller is designed (owner directive 2026-08-31); any new
  behavior is a transition-table row + tests-first, not another regex | done |
| 1 | **Extract** `ConversationState` + `classify_turn`; `decide()` delegates, behavior byte-identical | 26/26 suites, synthetic EMPTY DIFF, real baseline = same 2 diffs |
| 2 | Replace `decide()` if-chains with the transition table; add one test per row (35 rows) | suites + gates unchanged; table tests green |
| 3 | Wire the controller into run_turn/main.py as the single turn-end decision (rail + turn_controller + greeting + latches become consumers of `next_action`) | replay identity holds; gates unchanged |
| 4 | Generalize user_state/agent_state to chat; add product behavior ONLY via explicit state edits + tests-first (the "aage?" resume, interruption state, delivery mode) | gates + new behavior tests |

Every step keeps replay identity — that is the Phase-0 discipline carrying into the new
phase: **extract → reproduce → then change, always with the gate as referee.**

---

## 8. Smoke-9 failure-mode mapping (session_20260831_152709, UNVERIFIED build)

The session log was NOT produced by the current rail (v8): t7's "echo 9000" is
not reproducible on v8 (v8 retries+clears; the log build echoed the WRONG
fragment '9000' as canonical). The diagnostic header also printed no build line,
so the worker ran a stale/mixed build. The CLASS of failures is nonetheless
valid and is exactly what the canonical-value state must own:

| Turn | What happened (log) | What v8 would do | Root cause (class) | Controller row |
|---|---|---|---|---|
| t3-t5 | 026 / 9000 / 124205701 accumulate silently | same (intentional) | OK — accumulation works | — |
| t6 | 'ye clear?' -> recall 0269000124205701 | same | OK | — |
| t7 | '9000 nahi, 900... 1240 nahi, 12...' -> echoed 9000 | retry + CLEAR the accumulated value | multi-clause correction unsupported; ambiguous correction destroys state | rows 36/37 |
| t9 | 'ye confirm' -> 'nine zero zero zero note ho gaya' | echo_full 9000 | canonical value = WRONG fragment (9000, not the repaired number) | 36/37 |
| t10 | '9000 nahi 9000 hai bas' -> LLM 'wahi toh bol raha hoon' | retry + clear (v8) | correction-in-context not captured after release | 38 |
| t11 | '9900 be pagle' -> LLM adopted 9900, plan '9900 ka hisaab' | silent (armed-empty) | number with emphasis words never captured; LLM invented a plan | 38 |
| t15 | 'pura batao' -> LLM '9900 ki baat ho rahi thi' | status (armed-empty) | after canonical loss, no true value to recall | 38/34 |
| t18 | 'acct number kya likha hai' -> ARM 'bol number' | status (v8 write-command gate) | recall-query with announce words re-armed | 39 |
| t19 | 'tu lekar chuka hai abhi' -> SILENT | silent | armed-empty filler — dead-end without a value | 37 (ask) |
| t17 | garbled 'tema shika...' -> LLM 'chhod na phir' | discard to LLM (7 tokens) | uncertain STT -> abandonment, not clarify | controller P0 #4 |

Invariants this session proved are missing (canonical-value semantics):
1. **Latest user correction is the authoritative state update** — never confirmed
   the pre-correction value (t9 confirmed 9000), never clear on ambiguity (t7).
2. **A number spoken with emphasis/interjection in a dictation/correction
   context is still the number** ('9900 be pagle' = 9900) — the rail's
   pure-utterance detector is too strict for correction turns.
3. **Recall queries never re-arm** — 'acct number kya likha hai' is a RECALL,
   not a new dictation (t18).
4. **Uncertain/garbled turns clarify, never abandon** (t17: corr=0.27 -> LLM
   'chhod na').

These become ConversationState fields + transition rows (36-39) in the
controller design; NO rail pattern additions (owner freeze 2026-08-31).

---

## 9. Open questions for owner review

1. **Home of the controller**: new `agent/conversation_controller.py` with state in
   `engine["conv"]` (my recommendation — keeps `SessionState` memory-focused), or extend
   `SessionState`?
2. **Compatibility store**: keep `engine["dictation"]` as the dictation task's backing
   store during migration (controller reads/writes it) so replay identity needs no
   archive changes — acceptable?
3. **Scope of step 3**: merge `turn_controller` (wait_streak/greeting) INTO the
   controller as the single owner — my recommendation — vs. leaving it separate until
   step 4?
4. **Interruption source**: confirm `engine["last_response"]` (Generated ≠ Delivered) is
   the authoritative `interrupted` state to build resume on.
5. **PII**: dictated values remain no-store (controller must never write them to memory/
   lcm) — reaffirm as a hard invariant in the controller.
6. **Two-path pipeline**: confirm the reading in section 2 — the controller routes
   task-state turns (dictation/recall/confirm) straight to the rail (LLM skipped by
   design), and conversational turns flow User → Controller → LLM → Rail → TTS.
7. **Multi-clause corrections**: smoke-9 t7 ("9000 nahi, 900... 1240 nahi, 12...")
   — the controller should parse N (wrong -> correct) clauses and apply them in
   order. Confirm the rail's current single-pair parser is replaced by the
   controller's clause list as part of Step 1/2 (tests-first).
8. **Number-in-context**: '9900 be pagle' — a number with interjections in a
   correction context should be captured (row 38). This is the one deliberate
   detection WIDENING; everything else stays strict. Confirm scope before Step 1.

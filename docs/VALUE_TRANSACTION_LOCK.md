# Value Transaction Lock — L1–L6 (dictation lifecycle invariants)

**Date:** 2026-09-04 · **Status:** PENDING OWNER APPROVAL — no code until approved
· **Evidence session:** `session_20260903_103339` (build `e6f08d7`, verified)
· **Companion locks:** `CONTROL_PLANE_P1_LOCK.md` (P1 stays immutable),
`CONVERSATION_CONTROLLER_DESIGN.md` (rows 1–51 stay the implementation),
`PHASE0_MEASURABILITY_PLAN.md` (replay-gate discipline).

**Owner ruling this lock implements (2026-09-04):**

> The core issue is not primarily number parsing. The deeper failure is the value
> transaction/lifecycle: the system mutated the stored value before the user had
> actually heard and confirmed the proposed change. Lock invariants first; let
> individual rail rows remain implementation details underneath them.

**Scope (locked):** six invariants over the *lifecycle of a dictated value* —
L1 two-phase mutation, L2 echo-delivery gate, L3 fragment coalescing, L4
addressability, L5 LLM authority boundary, L6 response supersession.
**Out:** the feeder (L7), any new parsing rule over user speech, any P1/controller-
architecture change beyond what §9 lists as required to *express* these invariants.

---

## 0. Ground truth — what the code did on 2026-09-03

Every transcript of the session was fed offline to `agent.precision_rail.decide()`
on `e6f08d7`; the replay reproduces the live log turn-for-turn.

| Turn | User said (STT) | Row that fired | Stored value AFTER | Heard by user? |
|---|---|---|---|---|
| t3 | यार एक नंबर लिखा तुझे बता रहा हूँ | row 1 arm | `""` pending | yes |
| t5 | 026900125205203 | row 4 silent accumulate | `026900125205203` | — (silent, correct) |
| t7 | बता क्या लिखा | row 19 recall | unchanged | yes (digits spoken) |
| **t8** | नहीं 520 नहीं है | **M2 val-aware anchor-less REMOVAL** → `_apply_correction` removes the *first* `520` → echo_confirm, status→`confirming` | **`026900125203`** | **NO — user resumed 373 ms after the reply started; echo cancelled before any audio (`cancel_pre_audio`, UNHEARD). The mutation persisted.** |
| **t9** | पाइप की जगह 5 बार 0 लिखना है | seg `00000`, no correction spec; `accum_gap` = 4 (t6, t7, t8 + t9's own pre-classify increment) ≥ `GAP_FRESH_TURNS` → **ROW 48 cold-gap "FRESH number" → silent REPLACE** | **`00000`** | — (silent) |
| t10–t13 | सुनातो ने? / एवा / एवा, सुना, कहाँ? / अवाँ | row 23 short filler → silent ×4 | `00000` | — (supervisor rescue #1001 spoke once, 1.3 s into the silence) |
| t14 | हेलो | row 42 greeting-while-armed | `00000` | partially |
| t15 | तुन्हें सुना मैंने चेंज बताए नंबर के अंदर | row 24′ (8 words > 6) → **LLM**, task kept | `00000` | yes — "bol kya change kiya?" |
| t16 | चेंज ये करना है कि तुन्हें अभी 5 | row 24′ → LLM | `00000` | no (barged) |
| **t17** | जो नंबर लगाया है ना पाइप नंबर हटेगा और उसके जगह 50 एड होंगे | row 24′ → **LLM** | `00000` | **yes — "theek hai, samajh gaya — 5 ki jagah 50 add kar deta hoon"** (a fabricated mutation; the contract gate has only an English action-fabrication pattern) — **while supervisor rescue #1002 played concurrently (99.2–105.1 s overlap)** |
| t18 | नहीं पांच जीरो | M1 edit-intent, unresolvable → clarify, value kept | `00000` | partially |
| t19 | मतलब 00000 एड होगा | row 15 exact restatement (`00000 == 00000`) → **silent** | `00000` | — |
| t20–t25 | यह समझे / हलो / अरे बे चुप / बता समझा की नहीं समझा / बोल / हलो | silent ×4, greeting ×2 | `00000` | greetings only |

Experiments that fix the diagnosis (all reproducible with today's code, no changes):

1. **Merged t8+t9** — one sentence split by a 288 ms trailing-silence endpoint:
   `_parse_correction("नहीं 520 नहीं है पाइप की जगह 5 बार 0 लिखना है")` →
   `(None, '520', '00000')`; `decide()` on the merged text → `echo_confirm`, value
   `02690012000005203`. **Today's parser already resolves the whole instruction.**
2. **t9 against the un-mutated base with `accum_gap` < 4** → append (`…520300000`):
   wrong but recoverable. The destruction is a *composition* of three individually
   locked rows (M2 removal · a gap counter that counts the user's own recall/
   correction turns as "cold" · row 48 fresh-replace). None is wrong in isolation.
3. **Merged t18+t19** ("नहीं पांच जीरो मतलब 00000 एड होगा") → `(None, '50', '00000')`
   — one instruction; as two fragments it was a clarify and a silent dedupe.
4. **t15–t17 joined** → no spec at all (`_parse_correction` = None, no anchor in
   base). Even perfectly coalesced, this instruction is *unparseable* by the
   deterministic layer; the correct outcome is a clarify that keeps the value —
   never an LLM claim.

**Diagnosis (locked):** the value was lost by *irreversible mutation before
delivery*, by *fragment-by-fragment application of one instruction*, and by
*silence with no escape*; the LLM then *contradicted* the system. Parsing
improvements do not prevent that class. Lifecycle invariants do.

**Coverage fact (verified, important for §"replay-gate impact" below):** no JSONL
fixture in the replay gate contains a rail turn — the real baseline (23 turns) has no
dictation; `synthetic_slice1` is 14 fused + 1 greeting; the smoke5–13 fixtures are
stage-diagnostic *text* (the gate reports `0 turns checked` on each). The rail's
behavioral pins are the suites (`test_precision_rail.py`, `test_correction_repair.py`,
`test_conversation_controller*.py`), not the gate. This lock adds the first JSONL rail
fixture (§8).

---

## 1. Definitions (shared by all six invariants)

- **base** — the last value the user has accepted: an accumulation the user has not
  objected to (status `pending`) or an explicitly confirmed value. `base` is what
  `recall`/`status` report as "what I have", and the text corrections parse against.
- **proposal** — a candidate mutation, `{base, spec, derived, mode, created_turn,
  delivery}`, produced by any destructive row. `mode ∈ {correction, fresh}`:
  *correction* proposals (removal / replace / repair / only-this / full restatement /
  task-switch) are **spoken at creation** (the echo); *fresh* proposals (row 48
  cold-gap fresh number) are **silent at creation and accept digit appends to
  `derived`** — the user is dictating, and the smoke-7 ruling (never talk over
  dictation) still holds — and are spoken at `bas`/query/confirm exactly as
  accumulation is today. A proposal is **not** the value.
- **delivery boundary** — the moment a proposal's echo has actually reached the user.
  Computed today per turn by `agent/response_state.py` + `main.py`
  (`turn["response_state"] ∈ {FULLY_PLAYED, PARTIALLY_PLAYED, UNHEARD}`,
  `turn["cancel_pre_audio"]`, `turn["heard_text"]`); the rail never reads it today.
- **commit** — `base ← derived; proposal ← None`. Exactly two things commit:
  (a) an explicit user confirm of a proposal whose `delivery == spoken`;
  (b) non-destructive *append* during accumulation (`base ← base + seg`) — already
  the safe default; accumulation is reversible by rejection (row 18, unchanged).
- **edit-intent turn** — carries a correction spec (`_parse_correction` /
  `_val_aware_correction` non-None), or a change frame (`_is_change_frame`), or a
  non-plain rejection (`_is_reject and not _is_plain_reject`). All existing
  detectors; verified on the session: t8, t9, t15, t16, t17, t18 are edit-intent;
  t19 and the digit turns are not (t19 is digit-bearing → a *continuation*, §4).
- **destructive row** — any row whose output value is neither `base` nor `base + seg`.

---

## 2. L1 — Two-phase mutation (LOCK)

**Contract.**
1. A destructive row never writes `Task.value`. It writes `Task.proposal` and leaves
   `Task.value == base`. Rows affected: M1–M4 corrections, 14 (full restatement), 16/29
   (repair), 36–38, 40 (task-switch with digits), 41 (only-this), 48 (fresh-after-gap,
   `mode=fresh`). Rows 4/13/30 (append) and 18/26 (plain reject of an *accumulation*)
   are unchanged.
2. The echo speaks `derived`. `recall`/`status` while a proposal is open must
   distinguish proposal from base ("maine yeh samjha: <derived> — pehle wala: <base>")
   — the wording is implementation; the *distinction* is the lock.
3. Commit happens on **confirm only** (row 25 path) and only if L2 allows
   (`delivery == spoken`). Row 51 `_persist_number` fires after commit, as today.
4. While a proposal is open:
   - a **plain reject** ("नहीं" alone, no digits, no change frame) drops the proposal
     and returns to `base` (status `pending`), spoken ("theek hai, pehle wala rakha:
     … — kya badalna hai?"). Never a wipe.
   - a **new edit-intent turn** opens an L3 buffer parsed against **`base`** (never
     against `derived`). If the buffer closes with a complete spec, the new proposal
     *replaces* the old one; if it closes without one, the **old proposal stays open**
     and the clarify re-speaks it. (Prevents t15–t17 from destroying t9's proposal.)
   - the already-correct guard (46b) MAY consult `derived` to recognise a restatement
     of the open proposal → re-echo, never commit.
   - **digit segments** append to `derived` when `mode=fresh` (the user is dictating
     the new number), and open an L3 buffer as continuation when a buffer is open;
     otherwise (a correction proposal open, bare digits) → clarify against the
     proposal — never a silent append to `base`.
5. Task switch (row 40 announcement + write + digits) discards any open proposal with
   the old task — as today.

**Failure it prevents.** t8's removal could not have been lost by t9: t9 would parse
against `base = 026900125205203`; and t9's ROW-48 fire would have produced a silent
*fresh* proposal `derived = 00000` with `base` intact — a "बता क्या लिखा" at t10 would
have said "naya: 00000 — pehle wala: 026…". Nothing destroyed, one round to repair.

**Why L1 also makes mis-parses survivable (verified).** C5 in §4 — "नहीं 520 नहीं है" +
a fresh 18-digit number in one text — mis-parses today as `replace 520 → <18 digits>`
and yields a 30-digit garbage value. Under L1 that is a *proposal* whose echo the user
rejects; `base` is untouched. L1 does not fix the parse (out of scope — no new rules);
it bounds the cost of every parse error to one echo round.

**Ownership.** `Task` (controller state) gains `proposal`; `precision_rail` signal
layer unchanged; `_apply_correction` unchanged (it computes `derived`).
`engine["dictation"]` compat shape (`{value, status}`) is preserved; `proposal` is an
additive key on the same dict so archives without it are unaffected.

**Adversarial cases.**
- A1 confirm with no open proposal, status `confirming` from a direct echo (rows 2/3,
  fresh dictation echoed immediately): the direct echo *is* the proposal
  (`derived == value`); L2 gate applies; commit as today when heard.
- A2 confirm word inside a change frame ("…बस एक जीरो कम…") — M3 strips `confirm`;
  no commit (unchanged).
- A3 two corrections in a row against the same base — second replaces the first
  proposal; both derived from `base`. (Whether they are one instruction is L3's call.)
- A4 proposal open + row-40 task switch → new task, proposal discarded.
- A5 proposal open + "क्या लिखा" → recall distinguishes derived/base.
- A6 idempotence: the same correction twice → same proposal (46b holds on `base`).
- A7 fresh-mode proposal + `bas` → `echo_full` of `derived` (row 17 semantics) →
  confirm → commit. Identical UX to smoke-12 t26→t27; only storage differs.
- A8 fresh-mode proposal + plain reject → back to `base` spoken (today: wipe).

**Tests.** `test_value_transaction.py`: (i) every destructive row leaves `value`
byte-identical to the pre-turn value; (ii) commit only on confirm; (iii) plain reject
→ base; edit-intent → buffer against base; (iv) recall distinguishes; (v) fresh-mode
appends to `derived`; (vi) determinism.
**Existing pin that changes (declared):** `test_precision_rail.py` section H asserts
`eng["dictation"]["value"] == "7398"` immediately after the cold-gap turn; under L1
the assertion becomes `value == "01212012000001203"` and `proposal.derived == "7398"`,
then `"7398438138"` after t27's append, then base after confirm. The *spoken* behavior
of that flow (silent, silent, echo_full at `bas`) is unchanged.

**Replay-gate impact.** Real baseline: EMPTY DIFF (no rail turns). Synthetic: EMPTY
DIFF (no rail turns). New fixture (§8) added with `precise_detail` extended by
compare-when-present `proposal`.

---

## 3. L2 — Echo-delivery gate (LOCK)

**Contract.**
1. A proposal is *confirmable* only after its echo has crossed the delivery boundary.
   `proposal.delivery` is written by the **playback layer**, never inferred by the
   decision: `unspoken` at creation → `spoken` when the echo's `response_state` is
   `FULLY_PLAYED`, or `PARTIALLY_PLAYED` with the digit span inside `heard_text` →
   `unheard` on `cancel_pre_audio`, `UNHEARD`, or a TTS zero-audio failure.
2. A confirm word while `delivery ∈ {unspoken, unheard}` **re-echoes** the proposal.
   It does not commit and does not clear.
3. An `unheard` proposal never expires into a commit; it survives until confirm of a
   re-echo, plain reject, or a new complete spec (L1.4).
4. Same rule for accumulation's own echo (row 17 `echo_full` after `bas`): status
   `confirming` is only reachable through a delivered echo; a confirm after an unheard
   `echo_full` re-echoes.
5. `mode=fresh` proposals are `unspoken` until their `echo_full`; they are never
   confirmable while silent.

**Failure it prevents.** t8's echo was cancelled before audio, yet the rail entered
`confirming`. Under L2 the state after t8 is "proposal open, unheard"; a later "हाँ"
re-echoes instead of committing something the user never heard.

**Ownership.** Rail/controller **reads** delivery; `main.py` **writes** it at the two
sites that already classify `response_state` (FULLY_PLAYED path ~742–746 and the
`CancelledError` path ~864–905 — one added call `mark_delivery(engine, turn)` next to
`engine["last_response"]`). `response_pipeline.run_turn` (replay path) mirrors it. The
rail never infers delivery from timing. Note: the controller's existing
`conv.interrupted` is dead state (never written by `main.py`) — L2 supersedes it
rather than reviving it.

**Adversarial cases.**
- B1 echo fully played, "हाँ" → commit (unchanged UX).
- B2 echo cancelled pre-audio, "हाँ" → re-echo, no commit.
- B3 echo interrupted after the digits were spoken (`heard_text ⊇ digit span`),
  "हाँ" → commit — the digits are the content, the trailing "sahi hai na?" is not.
- B4 interrupted before the digits ("maine suna:" only) → unheard → re-echo.
- B5 TTS zero-audio and failover fails → unheard.
- B6 user *edits* after an unheard echo → L1.4/L3.
- B7 a supervisor rescue line plays while a proposal is unheard → the rescue is not the
  echo; delivery stays unheard (L6 decides whether the rescue should speak at all).

**Tests.** `test_delivery_gate.py`: (i) matrix `response_state × cancel_pre_audio ×
heard_text ⊇ digits → delivery`; (ii) confirm × delivery → {commit, re-echo};
(iii) `main.py` and `run_turn` set identical delivery for identical turn dicts
(structural pin, `inspect`-based like `test_control_plane_v1`'s wiring checks).

**Replay-gate impact.** Delivery derives from fields the archives already carry
(`response_state`, `cancel_pre_audio`, `heard_text`) so the harness replays it. Real
baseline unchanged (its `cancel_pre_audio` turns 9/12 are fused turns, no task).

---

## 4. L3 — Fragment coalescing (LOCK, owner-refined)

**Invariant (owner wording).** *A locally parseable edit fragment must not mutate
state if it may be an incomplete instruction whose continuation is expected.*

**Contract.**
1. An edit-intent turn while a task is active does not produce a proposal
   immediately. It produces an **instruction buffer** `pending_edit = {fragments,
   since_turn, base}` on the task and the decision **`hold_edit`** — never an echo,
   never a mutation. Whether `hold_edit` is silent or a short spoken hold is policy
   (§10, checklist Q3).
2. **Continuation** = the next turn is edit-intent, or digit-bearing, or a *premature
   resume* (`resume_gap_ms ≤ providers/vad.py RESUME_WINDOW_MS = 3000`, already logged
   per turn as `premature_resume`). Continuations append to `fragments`.
3. The buffer **closes** — and the joined text `" ".join(fragments)` is parsed **once**
   against `base` to create the L1 proposal — when any of these holds:
   (a) the joined text yields a **complete spec** (`wrong` and `correct` both resolved,
   or anchor + correct, or a removal whose target exists in `base`) **and** the turn
   ended with a natural (non-premature) endpoint;
   (b) the user hands off (`bas`, a question/tag "समझे?", a recall query, a confirm
   word — all existing detectors);
   (c) the bound is reached (`EDIT_BUFFER_MAX_TURNS` continuation turns or
   `EDIT_BUFFER_MAX_S` wall time — §10).
4. While the buffer is open, `recall` speaks `base` (and the open proposal, if any).
5. A buffer that closes **without** a complete spec → a *clarify* that names what was
   parsed (removal target / anchor) or, when nothing parsed (t15–t17), restates `base`
   and asks "kya hataana hai, uski jagah kya?". Value untouched (M1 already forbids
   wiping; L3 makes it structural).
6. A **complete single-turn instruction** with a natural endpoint closes immediately
   (3a) — the smoke-7/10/13 corrections keep resolving in one turn; no extra round trip.

**Failure it prevents.** t8 alone parses as a *removal* — locally valid — but is the
prefix of the instruction "520 नहीं है, [उस]की जगह 5 बार 0". Under L3: t8 opens the
buffer (removal target `520`, no replacement, premature resume follows), t9 appends,
the joined text parses to `replace 520 → 00000`, L1 creates the proposal, the echo
speaks `02690012000005203`, the user confirms. Zero LLM, zero new parsing rule.

**Why "locally parseable" is not enough (pinned).** A removal spec
`(None, wrong, None)` is *by construction* the prefix of a replacement spec
`(None, wrong, correct)`. Any system that applies removals on sight destroys the target
of every "X नहीं, Y" correction split by an endpoint. This is a property of the grammar,
not of this user. It is the dictation-layer form of the project's timing principle
(*speech endpoint ≠ conversational endpoint*) which `turn_controller` applies to prose
and nothing applied to edits.

**Ownership.** Buffer on `Task` (controller). Endpoint evidence (`premature_resume`,
`resume_gap_ms`, `trailing_silence_ms`) is already produced by `providers/vad.py` and
logged by `main.py`; the controller receives it read-only. No VAD/endpoint change.
Parsing stays in `precision_rail`.

**Adversarial cases.**
- C1 t8→t9 (acceptance trace) → one proposal `02690012000005203`. Verified parse.
- C2 t18→t19 → `(None,'50','00000')`; `50` ∉ base → unresolvable against base →
  clarify; with a proposal open whose derived already contains `00000`, 46b-vs-derived
  → re-echo. Either way no silence, no mutation. Verified parse.
- C3 complete single-turn correction, natural endpoint → immediate (unchanged).
- C4 removal + long pause + unrelated talk → closes by (b)/(c) → clarify naming the
  removal; value untouched.
- C5 removal + a *fresh full number* ("520 नहीं है" · "026900120000005203") → joined
  text **mis-parses today** as `replace 520 → <18 digits>` (verified: 30-digit
  result). L3 does not fix this (precedence of full-restatement over replace is a
  signal-layer question, logged as OPEN — §13 Q8); L1 makes it a rejected echo, not a
  corrupted base.
- C6 "नहीं" alone (plain reject) → not edit-intent → rows 18/26 as today (accumulation)
  or L1.4 (proposal). L3 never touches plain rejections.
- C7 continuation that is only a confirm word → handoff (3b) → spec incomplete →
  clarify; never a commit.
- C8 buffer open + greeting → greeting line (row 42); buffer stays open.
- C9 STT truncates the digits (t16 "…अभी 5") → fragment stored verbatim; joined text
  may still resolve; else clarify.
- C10 buffer open + row-40 task switch → buffer discarded with the task.

**Tests.** `test_edit_coalescing.py`: (i) C1/C2 byte-exact; (ii) C3 — the full
correction corpus of `test_correction_repair.py` and `test_precision_rail.py`
correction sections resolves in one turn with identical `derived`; (iii) close
conditions a/b/c; (iv) recall-while-open; (v) C10.

**Replay-gate impact.** Real baseline / synthetic: EMPTY DIFF (no rail turns). New
fixture (§8) carries compare-when-present `pending_edit`.

---

## 5. L4 — Addressability (KEEP as invariant; thresholds and classes are policy)

**Invariant (owner wording).** *An armed task must remain addressable and have a
bounded escape/status path.*

**Contract.**
1. **Bounded silence (the mechanism — needs no new detector).** While a task is
   active, consecutive *silent* decisions on **non-digit** turns are bounded by
   `SILENT_STREAK_MAX` (policy constant, tested, configurable — §10). At the bound the
   next non-digit turn gets a deterministic **status** decision: the open proposal is
   re-echoed if one exists (L2 rule 2), else `base` is recalled with the escape offer
   ("…aage bolo, ya 'bas' / 'chhodo' bolo"). Digit turns reset the streak and are
   never forced to speak (smoke-7 ruling intact). `hold_edit` counts as silent when
   it is silent.
2. **Escape.** Explicit abandon (rows 11/22/33) always works, and the status line
   *offers* it. No task may become a black hole.
3. **Optional policy (default OFF in this lock; checklist Q5):** an always-addressable
   class — the agent's name as first word ("एवा"/"अवाँ"/"aiva"), bare imperatives
   ("बोल"/"बता"/"चुप"), comprehension tags ("समझे?") — answered with the status line
   regardless of the streak. This needs a small **marker list** (like
   `GREETING_MARKERS`, consulted only while a task is active). Because it is a new
   input marker, it is *not* part of the invariant and ships only if the owner opts in.
4. **Non-goal (pinned):** L4 does not reduce legitimate silence between digit segments.

**Failure it prevents.** t10–t13 (4 silent turns) and t19–t24 (6 turns of "अरे बे चुप /
बता समझा की नहीं समझा / बोल" into silence). With `SILENT_STREAK_MAX = 2`, t12 and t24
speak the status (and, once L1–L3 hold, that status *is* the correct proposal). The
supervisor's rescue is a symptom of this gap (L6), not its owner.

**Ownership.** Controller (`armed_streak` exists for the armed-empty phase; L4
generalises it to the active task as `silent_streak`). Line pools in `precision_rail`.

**Adversarial cases.**
- D1 5 digit segments with 4 silent gaps → never forced to speak.
- D2 short filler × bound → status, task kept.
- D3 status while a proposal is open → re-echo (L2 takes precedence).
- D4 status while an edit buffer is open → recall base + "change bolo" (L3 rule 4).
- D5 no task active → not L4's domain.

**Tests.** `test_addressability.py`: streak bound/reset; injectable constant (1, 3);
status content by state (proposal / buffer / base); dictation silence unchanged
(smoke-7/10 pins imported).

**Replay-gate impact.** EMPTY on existing fixtures; §8 fixture covers the streak.

---

## 6. L5 — LLM authority boundary (LOCK)

**Contract.**
1. **No fall-through by word count.** While a task is active or a proposal/buffer is
   open, an edit-intent turn is never released to the LLM by row 24′ (`> 6 words`). It
   goes to L3. The LLM never sees it.
2. **Released turns carry task state (no digits).** When a *non*-edit turn is
   legitimately released while a task is active (row 24′ — the user changed subject),
   `fused_turn.build_contents` receives an additive `task_state = {kind, status,
   has_value: bool, proposal_open: bool}` — **never digits** — and the prompt gains one
   MUST_NOT: *never claim to have changed/added/removed/saved a number; the system
   does that and confirms it.*
3. **No write authority, enforced on output.** `response_contract` gains **one new
   pattern class** `claim_mutation` (action `block`, same tier as the English "I've
   sent/booked" pattern): first-person Hinglish/Devanagari mutation claims with a
   digit or digit-word ("… kar deta hoon / kar diya / add kar diya / hata diya /
   replace kar …", "… कर देता हूँ / कर दिया / हटा दिया …"). This is an *output* gate
   on the agent's own claim, topic-independent — not an input parser. (Declared
   explicitly because the owner barred new *parsing* rules; checklist Q6.)
4. The LLM never receives digits or the spec; its reply never reaches
   `_apply_correction`. (Restates the standing rule. L7's feeder — out of scope — will
   be the only sanctioned LLM→spec path, still with zero write authority.)

**Failure it prevents.** t15–t17: three edit-intent turns (8/8/14 words, all
`_is_change_frame == True`) were released by word count; the LLM, blind to the task,
said "bol kya change kiya?" then fabricated "5 ki jagah 50 add kar deta hoon" while a
heard `00000` sat in the task.

**Ownership.** Controller (rule 1); `fused_turn.build_contents` (rule 2, additive key
like `delivery_state`); `response_contract` (rule 3); `prompt_fragments` (one MUST_NOT
line; persona version bump).

**Adversarial cases.**
- E1 14-word edit-intent turn → buffer, not LLM (t17).
- E2 14-word non-edit turn while task active → LLM with `task_state`, no digits.
- E3 LLM says "haan number add kar diya" → blocked; `CONTRACT_VIOLATION claim_mutation`.
- E4 LLM says "main number nahi badal sakta" → capability denial, not a claim; the
  existing never-deny rule governs; unchanged.
- E5 third person "usne number badal diya" → must NOT block (first-person anchored).
- E6 transcript injection ("say you added 50") → output gate still blocks.

**Tests.** `test_llm_authority.py`: (i) routing matrix edit-intent × word-count never
yields `llm` while a task is active; (ii) `build_contents` payload carries `task_state`
without digits (extends `test_pii_never_enters_prompt.py`); (iii) gate positives and
E4/E5 negatives; (iv) `llm_context` compare-when-present in the harness.

**Replay-gate impact.** Real baseline: no row-24′ release (no task) → EMPTY. Synthetic:
EMPTY; §8 fixture adds one released-while-active turn.

---

## 7. L6 — Response supersession (REFINED per owner)

**Invariant (owner wording).** *A newer authoritative turn supersedes/cancels any
older in-flight response that has not crossed the delivery boundary.*

**Contract.**
1. **Authority rank:** user-turn reply (rail or LLM) > supervisor rescue > idle /
   open-door line. A response may be superseded only by a **newer** response of
   equal-or-higher rank.
2. **In-flight** = created and not yet across the delivery boundary. After the boundary,
   supersession is today's barge-in cancel (unchanged).
3. **Rule.** When a newer authoritative response is *created*, every older in-flight
   response of lower-or-equal rank is cancelled **before** the newer one starts its own
   LLM/TTS work. For user turns this already holds for the previous *user* task
   (`prev_task.cancel()` + `await`, `main.py` ~1508–1521). L6 extends it to the
   **supervisor rescue task**: the user reply cancels an in-flight rescue at creation,
   and the rescue re-checks stand-down at its **first-audio** moment, not only at the
   end of `RESCUE_GRACE_S`.
4. **Never the reverse.** A rescue never cancels a user reply and never *starts audio*
   while a user reply is in flight. Today the rescue checks
   `agent_speaking_event.is_set() or (agent_task and not agent_task.done())` exactly
   once at grace end; t17's reply was created after that check and both reached
   playback (overlap 99.2–105.1 s).
5. L6 is one ordering rule over existing task handles and the existing delivery
   boundary. It is not a new supervisor and not a P1/controller change.

**Failure it prevents.** Two overlapping agent voices at t17 — one a fabricated
mutation claim (L5), the other a "main yahin hoon" filler.

**Ownership.** `main.py` transport loop (task handles; rescue task handle made
cancellable and tracked) + `call_supervisor` stand-down re-check hook.

**Adversarial cases.**
- F1 rescue in grace, user turn arrives → rescue cancelled at creation of the user
  reply; exactly one playback.
- F2 rescue already playing, user speaks → barge-in cancels it (today's path).
- F3 user turn N in flight (LLM TTFT), turn N+1 arrives → N cancelled before N+1's LLM
  starts (today); N is marked `cancel_pre_audio` → feeds L2.
- F4 idle open-door line in flight, user speaks → cancelled (today).
- F5 two rescues (escalation) → never overlap (dedupe/cooldown already; pinned).

**Tests.** `test_response_supersession.py` with a fake clock and fake task handles:
rank × in-flight matrix; F1 asserts exactly one playback.

**Replay-gate impact.** None — the harness skips supervisor/idle turns and models no
concurrency. Proven by the unit matrix and the live trace.

---

## 8. Acceptance trace — t5 → t25 under L1–L6 (primary gate)

Same transcripts, same endpoint evidence; becomes the first JSONL rail fixture and a
byte-exact test on **value invariants and decision class**. (Exact positions of L4
status lines depend on `SILENT_STREAK_MAX`; shown with the default 2.)

| Turn | Text | Expected decision class | base / proposal after |
|---|---|---|---|
| t3 | यार एक नंबर लिखा… | arm (today) | base `""` |
| t4 | पीके | silent (streak 1) | — |
| t5 | 026900125205203 | silent accumulate (today) | base `026900125205203` |
| t6 | चलिए तो ने | silent (streak 1) | — |
| t7 | बता क्या लिखा | recall base (today; streak 0) | — |
| **t8** | नहीं 520 नहीं है | **L3 `hold_edit`** — buffer opens (removal target `520`, no replacement); **no mutation** | base unchanged; `pending_edit=[t8]` |
| **t9** | पाइप की जगह 5 बार 0 लिखना है | premature resume → continuation → joined text → `(None,'520','00000')` → **L3 close → L1 correction proposal**, echo "maine yeh samjha: 0 2 6 9 0 0 1 2 0 0 0 0 0 5 2 0 3 — sahi hai na?" | base unchanged; proposal `derived=02690012000005203`, delivery per L2 |
| t10 | सुनातो ने? | silent (streak 1) — or, if the t9 echo was unheard, nothing changes until addressed | — |
| t11 | एवा | silent (streak 2) | — |
| t12 | एवा, सुना, कहाँ? | **L4 bound → re-echo proposal** (L2 rule 2 if unheard) | — |
| t13 | अवाँ | silent (1) | — |
| t14 | हेलो | greeting-while-armed (today) | — |
| t15 | तुन्हें सुना मैंने चेंज बताए नंबर के अंदर | edit-intent, 8 words → **L5: not LLM** → L3 buffer opens → `hold_edit` | proposal stays open |
| t16 | चेंज ये करना है कि तुन्हें अभी 5 | continuation → append | — |
| t17 | जो नंबर लगाया है ना पाइप नंबर हटेगा और उसके जगह 50 एड होंगे | continuation; bound reached; joined text has **no spec** (verified) → **clarify**: restate the open proposal + "kya hataana hai, uski jagah kya?" — **no LLM, no fabricated claim** | proposal unchanged |
| t18 | नहीं पांच जीरो | edit-intent → buffer opens | — |
| t19 | मतलब 00000 एड होगा | digit-bearing continuation → joined `(None,'50','00000')` → `50` ∉ base; 46b-vs-derived recognises `00000` already in the proposal → **re-echo proposal** ("haan, wahi: … — sahi hai na?") | proposal unchanged |
| t20 | यह समझे | handoff-class turn with proposal open → silent (1) or re-echo (policy) | — |
| t21 | हलो | greeting (today) | — |
| t22 | अरे बे चुप | silent (1) | — |
| t23 | बता समझा की नहीं समझा | recall query → **re-echo proposal** (existing recall detector; streak 0) | — |
| t24 | बोल | silent (1) | — |
| t25 | हलो | greeting | — |

Net: `02690012000005203` is proposed once at t9, spoken, re-spoken on request, and
one "हाँ" away from commit throughout; `base` is never destroyed; no run of silence
exceeds the bound; the LLM never speaks about the number. The genuine ambiguity of
"पाइप/पाँच की जगह" (replace `520` vs replace `5`) is not resolvable from text by any
parser; under L1/L2 it costs one echo round, never a wrong commit.

---

## 9. What this requires of P1 / the controller (boundary check)

- **P1 (`control_plane.py`) — no change.** `ACTIONS` already contains
  `rail_echo / rail_repair / rail_confirm / suppress / clarify`; `hold_edit` maps to
  `suppress` or `clarify` in `_RAIL_ACTION_MAP` (one map entry, no schema change).
  I1–I9 untouched. Shadow divergence will rise on L3 turns (the shadow has no buffer
  concept) — expected telemetry, reviewed with the P2 authorization as that lock says.
- **Controller (`conversation_controller.py`) — expression only.** `Task` gains
  `proposal` and `pending_edit`; `ConversationState` gains `silent_streak`; destructive
  rows return proposals instead of writing `value`; rows 1–51 keep their *triggers*,
  only their *write target* changes; new rows: `hold_edit`, buffer-close, status-at-
  bound. `to_compat()` keeps `{value, status}` and adds the optional keys.
- **Signal layer (`precision_rail.py`) — no new parsing rules.** Line pools may gain
  L1/L4 wording. (The optional L4 name-call marker list is a separate opt-in — Q5.)
- **Transport (`main.py`)** — L2 delivery write at the existing `response_state`
  sites; L6 ordering over task handles; L5 payload + gate wiring.
- **Harness (`replay.py`)** — carries `proposal` / `pending_edit` / `silent_streak`
  forward like `precise_detail`; compare-when-present; new §8 fixture.

---

## 10. Policy constants (tested, configurable — NOT invariants)

| Constant | Proposed default | Notes |
|---|---|---|
| `SILENT_STREAK_MAX` (L4) | 2 | 3rd consecutive silent non-digit turn speaks; owner may change freely |
| `EDIT_BUFFER_MAX_TURNS` (L3.3c) | 2 continuation turns | t15–t17 closes at the bound |
| `EDIT_BUFFER_MAX_S` (L3.3c) | 6 s since first fragment | wall-clock guard for a user who stops mid-instruction |
| continuation resume window (L3.2) | reuse `providers/vad.py RESUME_WINDOW_MS = 3000` | read-only; no VAD change |
| `hold_edit` voicing (L3.1) | silent when the endpoint was premature-prone (user mid-flow); short "haan, bol" when natural | Q3 |
| `GAP_FRESH_TURNS` (row 48) | unchanged (4) | row 48 now yields a silent *fresh* proposal, so the constant is no longer destructive |

---

## 11. Tests summary (tests-first; new files under `phase5/tests/`)

| Suite | Covers |
|---|---|
| `test_value_transaction.py` | L1: value immutability under destructive rows; commit on confirm only; plain reject → base; fresh-mode appends to derived |
| `test_delivery_gate.py` | L2 matrix; confirm × delivery; `main.py`/`run_turn` parity pin |
| `test_edit_coalescing.py` | L3 close conditions; C1–C10; single-turn corrections unchanged (imports existing correction corpus) |
| `test_addressability.py` | L4 bound/reset; injectable constant; status content by state; dictation silence unchanged |
| `test_llm_authority.py` | L5 routing matrix; `task_state` PII pin; `claim_mutation` gate positives/negatives |
| `test_response_supersession.py` | L6 rank × in-flight matrix, fake clock |
| `test_session_103339_trace.py` | §8 trace byte-exact on base/proposal and decision class |
| existing 47 suites | green; **declared pin change:** `test_precision_rail.py` §H (row 48 value location, §2) — no other expected change; M1–M4 semantics preserved as proposals |
| replay gate | real baseline: the same 2 classified diffs + t11 note, zero new; synthetic: EMPTY after regeneration; new `session_103339` fixture: EMPTY |

---

## 12. Explicitly out of scope (locked OUT)

- **L7 feeder** (LLM interprets → deterministic derive/validate → echo → confirm) —
  deferred until L1–L6 are proven live; its residue-sizing shadow harness is deferred
  with it. t15–t17 (unparseable even when merged) is the first entry of that residue.
- **New parsing rules / detectors / digit-word variants over user speech** — none. A
  merged text that fails to parse yields a clarify (safe) and a logged residue entry,
  not a regex. The only new *input* marker (L4 name-call) is opt-in and outside the
  invariant; the only new *pattern* (L5 `claim_mutation`) is an output gate.
- **Signal-layer precedence** (full-restatement vs replace, C5) — open question,
  not resolved here.
- **`accum_gap` semantics** (whether recall/correction turns should count as "cold") —
  observation only (Q7); not required for the acceptance trace once L3 holds.
- **Range-vs-ID, address capture, capture-confirm feeder, episode-memory feeder,
  P2–P5, Phase C/D/E** — unchanged status.
- **VAD / endpointing / STT** — L3 *reads* endpoint evidence; no thresholds change.
  The 288 ms endpoint that split t8/t9 is track-4's business.
- **TTS provider / TTFA** — L2 makes the system correct *under* current latency; it
  does not reduce it.
- **Persona/style** — exactly one boundary MUST_NOT (L5); nothing else.

---

## 13. Approval checklist (owner)

1. **L1 — append stays immediate?** Recommendation: yes (reversible; smoke-5/6 silence
   ruling depends on it). Alternative: make accumulation a proposal too (more rounds).
2. **L1 — row 48 as a silent *fresh* proposal** (base kept until confirm) with the
   declared `test_precision_rail §H` pin change. Recommendation: yes — it is the only
   reading consistent with "stored value unchanged until heard + confirmed".
3. **L3 — `hold_edit` voicing** default per §10. Recommendation: silent when a premature
   resume is likely, spoken hold otherwise.
4. **L2 — B3** ("digits heard, tail not heard" = delivered). Recommendation: yes.
5. **L4 — opt-in name-call/imperative marker list.** Recommendation: ship L4 with the
   streak bound only (no new input marker) and revisit after the live re-test.
6. **L5 — `claim_mutation` output-gate pattern class** (Hinglish/Devanagari first-person
   mutation claims). Recommendation: yes — it is a gate on the agent's own output, not a
   parser over user speech.
7. **`accum_gap` observation:** should turns that *address the task* (recall,
   correction, status) count toward the cold gap? Recommendation: no, but defer — L3
   removes the failure path; changing the counter is a separate small pin.
8. **C5 precedence (full restatement vs replace)** — log as OPEN for the signal layer;
   not in this lock.
9. **L6 rank** user reply > supervisor > idle. Recommendation: yes.
10. **Constants in §10.**

On approval: implementation order **L1 → L2 → L3 → L5 → L4 → L6**, each tests-first,
each its own commit, full gate (47 suites + replay) after each. Live verification =
the §8 transcript re-spoken by the owner on the verified build, then the standing
smoke corpus (5–13) re-spoken to confirm no regression in one-turn corrections.


---

## 14. Implementation record (2026-09-04, branch `arena/01a0686c-voice-agent`)

Owner rulings applied: Q1 append immediate · **Q2 row 48 = silent fresh proposal**
(§H pin declared) · Q3/Q4 as recommended · **Q5 opt-in only** (streak bound shipped,
no marker list) · **Q6 `claim_mutation` gate shipped** · **Q7 deferred** (`accum_gap`
semantics pinned unchanged by test) · Q8 C5 logged OPEN (controller precedence at a
buffer close only) · Q9 rank shipped · Q10 constants as §10.

> **History note (2026-09-04 13:05 UTC).** The work was developed and pushed as one
> commit per invariant (SHAs in the first column below). Those commit objects were
> lost from the sandbox clone and from the remote branch after a session reset — only
> `e6f08d7` → `99a4821` (L6 runtime fix) survived on `origin/arena/01a0686c-voice-agent`.
> The identical working tree (verified: 55/55 suites, replay gate `60 turns, 1 divergent
> field(s)`) was re-landed as **one consolidated commit** on top of `99a4821`. The
> per-invariant SHAs below are therefore historical labels for the *sequence*, not
> resolvable refs; the code/tests columns remain accurate.

| Commit (historical, see note) | Invariant | Code | Tests |
|---|---|---|---|
| `8089cd5` | L1 two-phase mutation | `agent/value_transaction.py` (new), `conversation_controller.py` (`Task.proposal`, `_propose_correction/_commit/_revert`, row 48 fresh proposal), `precision_rail.py` (new line pools, `decide(..., turn_meta)`), archive plumbing `main.py`/`response_pipeline.py`/`replay.py` | `test_value_transaction.py` |
| `cf6638d` | L2 delivery gate | `value_transaction.mark_delivery` at main.py FULLY_PLAYED + CancelledError, mirrored in `run_turn` | `test_delivery_gate.py` |
| `25e2c02` | L3 instruction buffer | `Task.pending_edit`, `_open_edit/_close_edit/_edit_buffer_turn`, `control_plane._RAIL_ACTION_MAP[hold_edit]` | `test_edit_coalescing.py` |
| `42b18f1` | L5 LLM authority | routing (change-frame never row 24′), `task_state` (no digits) in contract, `claim_mutation` block, task MUST_NOT | `test_llm_authority.py` |
| `23aeac7` | L4 addressability | `ConversationState.silent_streak`, `SILENT_STREAK_MAX=2` (policy) | `test_addressability.py` |
| `c15ae2a` | L6 supersession | `agent/response_supersession.py` (new); main.py cancellable rescue + boundary re-check at first audio | `test_response_supersession.py` |
| `345b7c8` | acceptance | replay.py replays silent rail turns; fixture `phase5/harness/fixtures/session_103339_rail/` (RECONSTRUCTED) | `test_session_103339_trace.py` (7 properties) |
| `f1b0e08` | adversarial | `control_plane.pre_state` deep-copy | `test_value_transaction_adversarial.py` |

**Pin / test changes beyond the declared §H pin (all in the L1 commit, each annotated in-file):**

- `test_precision_rail.py`, `test_saved_number_recall.py`, `test_correction_repair.py`
  now call `value_transaction.decide_heard` (decide + "echo fully heard" playback
  stand-in) instead of bare `decide` — the offline equivalent of an uninterrupted live
  turn. Behavioural pins are unchanged by this alone.
- `test_precision_rail.py`: smoke-2 t34 (re-dictation is a proposal; appends land on the
  proposal; commit on `haan`), "fresh dictation overrides a stale one" (proposal until
  confirm), smoke-13 t29 (repair proposed; confirm commits), **§H** (row 48 fresh
  proposal: base kept, proposal collects digits, `bas`→`echo_full`, `haan`→commit).
- `test_correction_repair.py`: M1–M4 read the repaired value from `proposal.derived`
  or after a confirm turn; **t26 lone removal `1242 नहीं है` now HOLDS** (`hold_edit`,
  value kept) instead of applying the removal — this was the exact 103339 t8 mechanism;
  t26+t27 close into one proposal `026900000001203`.
- No other suite changed. Replay gate: owner baseline hard-diff profile byte-identical
  to the standing profile (t1 greeting rail, t20 reply cap, t11 note); synthetic and the
  new rail fixture replay to identity.

**Coverage kinds.** Unit/invariant: 8 new suites (55 total). Fixture: one reconstructed
rail archive (21 rail turns, 0 fused) — replay identity of the carrier, not proof of live
behaviour. Live proof = owner re-speaks §8 on this build (`LIVE_TEST.md`).

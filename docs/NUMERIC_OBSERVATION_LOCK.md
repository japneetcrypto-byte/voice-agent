# Numeric Observation Boundary — design lock (proposal for owner review)

Status: **DESIGN ONLY — nothing implemented.** No code, no rail row, no lexicon entry,
no feeder, no STT change. This document fixes the *contract* of the missing layer:

```
HEARD → UNDERSTOOD → OPERATION → PROPOSAL → DELIVERY → CONFIRM → COMMIT
  N0       N1..N6        L1/L3     L1         L2        L2       L1
```

Evidence base: `docs/NUMERIC_PERCEPTION_AUDIT.md` (session 133627 on `b228048`, plus
103339 / 125037 / 131245). Terminology and state names continue
`docs/VALUE_TRANSACTION_LOCK.md` §1 (`base`, `proposal`, `delivery`, `commit`).

Reading guide: §1 places the layer; §2 is the schema; §3–§9 are the invariants
N1–N7 (one per owner bullet, each with its test oracle); §10 is the L1–L6 consumption
contract; §11 the audit record; §12 the echo-gate invariant (E1); §13 answers A–E;
§14 what is *not* being locked; §15 approval checklist.

---

## 0. The one-sentence contract

> **The Numeric Observation layer answers exactly one question — "what numeric
> information did the user provide in this turn, and how certain is each part?" — and
> nothing downstream may treat any numeric content as known unless the observation
> says it is known.**

It does not choose an operation, does not read task state, does not write task state.

---

## 1. Placement

```
Audio ─► VAD ─► STT ─► [N] Numeric Observation ─► Signals / instruction ─► rows (operation)
                 │            │                                              │
                 │            └─ observation record (per turn, immutable)    ▼
                 │                                                    L1 proposal ─► L2 delivery ─► confirm ─► commit
                 └─ endpoint evidence ─┘  (input to N, never to rows directly)
```

Today `classify_turn()` computes `Signals.seg` / `Signals.raw` by calling
`dictation_value` + `normalize_span` on the string, and `full_restatement` /
`correction` are derived from the string too. Under this lock those three fields are
**consumers of the observation**, and the string is not visible to the operation
layer for numeric purposes. The observation is produced **once per turn, before**
`is_echo`-style validity gating is final (see §12) and **before** `controller_decide`.

What the layer is allowed to read: the STT transcript (all of it: text, segments,
per-segment logprob, language), the endpoint evidence already archived per turn
(`turn["endpoint"]`, `turn["premature_resume"]`, `acoustic`), and the **static** digit
lexicon. What it is forbidden to read: `engine["dictation"]`, `conv`, the proposal,
the previous turn's decision. (Determinism + auditability: the same audio/text must
produce the same observation regardless of task state.)

---

## 2. Observation schema (exact)

One `NumericObservation` per turn (also produced for turns with no numeric content —
`items == []` — so the audit record is complete).

```
NumericObservation
  turn:            int                    # source turn number
  source:          {provider, model, language, text, text_sha1,
                    segments:[{text, avg_logprob, no_speech_prob, start_ms?, end_ms?}]}
  endpoint:        {speech_duration_ms, trailing_silence_ms, threshold_ms,
                    premature_resume_ms | null, energy_profile}    # verbatim from VAD archive
  items:           [NumericItem]          # ordered as spoken; usually 0 or 1
  non_numeric_tokens: [{text, start, end}] # everything outside items (instruction layer's input)
  certainty:       COMPLETE | INCOMPLETE | EMPTY                  # derived, see below
  reasons:         [str]                  # machine-readable codes explaining certainty
  version:         str                    # observation-layer version (lexicon + rules hash)

NumericItem                                # one contiguous numeric span
  span:            {start, end, text}      # char offsets in source.text (verbatim slice)
  slots:           [Slot]                  # the digit sequence, one entry per POSITION
  surface:         RUN | SEPARATED | GROUPED | WORDS | MIXED       # descriptive ONLY (N3)
  group_breaks:    [int]                   # slot indices where the speaker paused / STT
                                           # punctuated — descriptive ONLY (N3)
  boundary:        {starts_at_turn_start: bool, ends_at_turn_end: bool}
                                           # is the span flush with the utterance edges
  unknown_count:   int                     # slots with kind == UNKNOWN
  ambiguous_count: int                     # slots with kind == AMBIGUOUS

Slot
  kind:            DIGIT | UNKNOWN | AMBIGUOUS
  digit:           "0".."9" | null         # null unless kind == DIGIT
  alternatives:    [ {digits: str, reading: str} ] | null   # AMBIGUOUS only (N4)
  token:           {text, start, end}      # the STT token(s) this slot came from
  provenance:      LITERAL      # '5', '026900' (one slot per character)
                 | WORD         # 'जीरो', 'nine', 'टू'
                 | COMPOUND     # 'पैंतीस' -> two slots, both provenance COMPOUND, same token
                 | MULTIPLIER   # 'चार बार 0' -> four slots, each MULTIPLIER, count_token='चार'
                 | DOUBLE       # 'डबल जीरो' -> two slots
                 | OOV          # kind UNKNOWN: a token inside a numeric span the lexicon
                                #   cannot read ('सिस')
                 | COUNT_OR_DIGIT  # kind AMBIGUOUS: '5 जीरो' (N4)
  count_token:     {text, start, end} | null   # MULTIPLIER/DOUBLE/COUNT_OR_DIGIT
  confidence:      float | null            # per-token if the STT ever provides it; null today
```

Derived `certainty`:

- `EMPTY` — `items == []`.
- `COMPLETE` — every slot of every item is `DIGIT`.
- `INCOMPLETE` — any slot is `UNKNOWN` or `AMBIGUOUS`, **or** an item's span contains a
  multiplier/count word whose scope could not be bound (`reasons` carries e.g.
  `OOV_TOKEN`, `COUNT_OR_DIGIT`, `UNBOUND_MULTIPLIER`, `MIXED_SCRIPT_RUN`).

Explicitly **not** in the schema: any operation (append/replace/correct), any reference
to `base`/`proposal`, any decision flag. `surface` and `group_breaks` are recorded for
audit and future recognizer work; **no downstream rule may branch on them** (N3).

Size/shape note: a 15-digit run is 15 slots. Slots are cheap; the point is that every
digit position has an identity, a provenance and a certainty — which is what a
*structured digit sequence* means, as opposed to a string.

---

## 3. N1 — Unknown is a first-class state (LOCK)

**Invariant.** A token that occurs *inside* a numeric span and is not readable by the
lexicon produces a slot of `kind = UNKNOWN`; the item is kept; `certainty = INCOMPLETE`.
The observation never drops the token, never drops the item, never substitutes a digit.

Consequences:

- `जीरो टू सिस नाइन डबल जीरो` → **6 slots**: `0 2 ? 9 0 0` (slot 3 `UNKNOWN`,
  provenance `OOV`, token `सिस`). Today: *no observation at all*, sentence read as a
  confirmation (audit §2 t17).
- An `UNKNOWN` slot can never become a committed digit: **N6** forbids deriving a
  proposal from an `INCOMPLETE` observation, and L1 commits only from a proposal.
- "Inside a numeric span" is defined structurally (the token is between two readable
  numeric tokens with no non-numeric word between, or adjacent to a numeric token and
  matched by the *shape* of a digit word — Devanagari/Latin token of ≤ 6 chars that is
  not a known function word). This is a boundary rule, not a lexicon entry: `सिस` is
  captured because of *where* it sits, not because we taught the system that word.

Test oracle: for every input, `sum(len(item.slots))` ≥ number of readable numeric
tokens + number of OOV tokens between them; an OOV token never lowers the slot count.

---

## 4. N2 — Unknown/ambiguous never commits (LOCK)

**Invariant.** `base` may only ever take a value whose every digit traces (via the
proposal's `derived`, via the observation record) to a slot of `kind = DIGIT`.

This is a property of the *whole chain*, stated here because the observation is where
the provenance starts. It is checked by the audit record (§11): every commit must be
reconstructible as `base_before + digits(observation slots) [+ correction spec]`, and
the reconstruction must never touch an `UNKNOWN`/`AMBIGUOUS` slot.

---

## 5. N3 — Punctuation and grouping have zero semantic authority (LOCK)

**Invariant.** Two transcripts whose numeric tokens read to the same slot sequence
produce **equal** `items[].slots`; `surface` / `group_breaks` differ but **no rule in the
observation layer, the signal layer, or the rows may branch on them**.

Consequence for today's code (measured against the shipped pins, not implemented):

`_is_full_restatement` mixes two kinds of evidence. Under N3 they separate:

| signal in `_is_full_restatement` | kind | under N3 |
|---|---|---|
| reject word / restart phrase in the turn | instruction | stays — moves to the operation layer's cue input (N5) |
| ≥ 2 compound number words (`निन्यानबे पैंतीस`) | **provenance** — the *user* chose whole-number words; the slots carry `COMPOUND` | stays — read from slot provenance, not from text |
| ≥ 4 single-digit *words* (`डबल जीरो, वन, तू, …`) | provenance (`WORD` slots) | stays as provenance (the user is spelling), **but it is weak evidence** and is to be re-justified against the structural rule below |
| ≥ `FRESH_DICTATION_MIN` **separated** digits (`1, 2, 5, 8, …`) | **surface** — whisper's punctuation | **retired**. Verified: switching this rule off leaves all six rail/transaction suites green (0 pins depend on it). It is the rule that produced 125037 t4, 131245 t7, 133627 t4 and t12. |

The structural replacement for the retired rule is downstream (N5 rule 3): a
`COMPLETE` observation whose slots **contain the current base as a prefix** or whose
length ≥ the base's length *and* whose span is flush with the utterance start is a
candidate *restatement*; a shorter, later-in-utterance, or endpoint-continued span is
a *continuation*. Smoke-2 t34 (`001200005703` contains base `120000`) and smoke-4 t20
(compound provenance) keep their REPLACE outcome; smoke-5 t25/t33 and smoke-1 t30 keep
APPEND; 133627 t12 becomes APPEND (correct) and t4 becomes APPEND (correct operation on
a wrong-but-COMPLETE digit — see §13 E).

`GROUPED_DIGITS_RE` / `SEPARATED_DIGITS_RE` / `DIGIT_RUN_RE` remain valid as *span
finders* (they locate numeric text); they lose any role in deciding what the digits
*mean*.

Test oracle (property test): for random digit sequences `d`, and for each rendering
`r ∈ {run, comma-separated, space-separated, 3-4 grouped, Devanagari words, English
words, mixed}`, `observe(r(d)).items[0].slots == observe(run(d)).items[0].slots`
and `certainty == COMPLETE`.

---

## 6. N4 — Digit vs count: `5 जीरो` is AMBIGUOUS, never `50` (LOCK)

**Invariant.** A bare number word/literal immediately followed by a digit word, with
no explicit multiplier (`बार`/`times`/`x`) and no explicit double (`डबल`), is
`AMBIGUOUS` with exactly two alternatives, carried as one `Slot` group:

```
'5 जीरो'  →  Slot(kind=AMBIGUOUS, provenance=COUNT_OR_DIGIT,
                  alternatives=[{digits:"50", reading:"digit 5 then digit 0"},
                                {digits:"00000", reading:"five zeros"}],
                  token='5 जीरो', count_token='5')
```

- Explicit forms stay `DIGIT` with provenance: `5 बार 0` → five `MULTIPLIER` slots;
  `डबल जीरो` → two `DOUBLE` slots; `पचास` → two `COMPOUND` slots (`5`,`0`).
- The observation never resolves the ambiguity from *context* (it has none — §1).
  Resolution belongs to the operation layer **only via clarification** (N6) or via a
  second look (§13 C). Choosing the alternative that "fits" the base is guessing and is
  forbidden.
- Why lock this at the observation and not at the parser: the audit shows the
  multiplier phrase is the *systematically* mis-rendered part of this speaker's numbers
  (0/8 in flowing speech). The place it surfaces most often is exactly a count word
  next to `जीरो`. Making that ambiguity explicit is what lets the system *ask the right
  question* ("paanch zero — five zeros, ya paanch aur zero?") instead of producing `50`
  (133627 t15) or `520`.

Test oracle: `observe("5 जीरो").certainty == INCOMPLETE`; `observe("5 बार जीरो")`
`== COMPLETE` with slots `0×5`; `observe("पाँच जीरो")` behaves as `5 जीरो`.

---

## 7. N5 — Continuation is instruction evidence, not numeric content (LOCK)

**Invariant.** Continuation cues (`आगे`, `इसके आगे`, `इसके बाद`, `continue`, `aage`),
restart cues (`पूरा`, `फिर से`, `दोबारा`, `shuru se`), rejection words, and count/
position words that refer to the sequence (`पहला`, `आखिरी`, `बीच में`) are
**`non_numeric_tokens`** in the observation. They never enter `slots`, never affect
`certainty`, and the observation layer never emits an APPEND/REPLACE verdict.

The operation layer decides append vs replace from — in this precedence —
(1) explicit instruction tokens, (2) endpoint evidence (`premature_resume_ms` ≤
`RESUME_WINDOW_MS` ⇒ continuation of the previous utterance), (3) structural
relation of the observed slots to the current base/proposal (prefix match, full-length
restatement, `only_this`), (4) policy default (append while accumulating — the
existing owner Q1 ruling). **Surface form is not in the list.** Rule (3) is the *only*
place task state enters, and it is downstream of the observation.

When (1) contains *both* a restart cue and a continuation cue in one turn (133627 t10
`पूरा बोल पूरा नंबर 026900 आगे`), the operation is **ambiguous** and falls to N6 —
clarify — rather than to the row that happens to match first.

Test oracle: `observe(...).items[0].slots` identical for `"इसके आगे 125205203"`,
`"125205203"`, `"पूरा नंबर 125205203"`; the *decision* differs only through
`non_numeric_tokens` + endpoint evidence.

---

## 8. N6 — Uncertainty stops mutation and forces clarification (LOCK)

**Invariant.** If the observation for a numeric turn is `INCOMPLETE`, or the operation
is ambiguous under N5, the only permitted outcomes are:

- a **clarify** that names the uncertain part (the `UNKNOWN`/`AMBIGUOUS` slot's
  neighbourhood or the conflicting cues) — a *spoken* turn, never silence;
- a **hold** (L3) when the uncertainty is expected to resolve from an in-flight
  instruction (premature resume, open edit buffer);
- a **second look** (§13 C) when one is available for that turn.

Forbidden: creating a proposal, appending to `base`, appending to a `fresh` proposal,
committing, or releasing the turn to the LLM as if it were non-numeric.

The clarify must speak **known digits as known and unknown as unknown** ("zero two
*kuch* nine zero zero — teesra digit phir se bolo") — never a guessed digit and never
the whole number re-asked when one slot is missing. (This is also the addressability
answer to L4: an `INCOMPLETE` observation is never a silent turn.)

Test oracle (adversarial, extends `test_value_transaction_adversarial.py`): over random
sessions with injected `UNKNOWN`/`AMBIGUOUS` slots, `base` never changes on an
`INCOMPLETE` turn; every `INCOMPLETE` turn produces a spoken line or an L3 hold; the
spoken line contains no digit that is not a `DIGIT` slot or already in `base`.

---

## 9. N7 — Observation is pure, stateless and immutable (LOCK)

**Invariant.** `observe(transcript, endpoint) → NumericObservation` is a pure function
of its inputs; it reads no task state; the record is written once to the turn archive
and never edited. Two calls on the same inputs (live, replay, tests) yield the same
record (`version` pins lexicon/rule changes so a replay can detect drift).

This is what makes the audit chain (§11) trustworthy and what lets recognition improve
(§13 D) without touching L1–L6.

---

## 10. Boundary to L1–L6 — what each layer consumes

| layer | consumes from the observation | forbidden |
|---|---|---|
| **Signals** (`classify_turn`) | `seg` ← digits of `COMPLETE` items (concatenated slots); `raw` ← `span.text`; `has_numeric` ← `items != []`; `numeric_certainty`; `non_numeric_tokens` for cue detection | computing `seg` from the string; reading `surface`/`group_breaks` |
| **operation rows** (append / replace / correction / only-this / task-switch) | slots + `certainty` + `non_numeric_tokens` + endpoint | any row fires on an `INCOMPLETE` observation (N6 intercepts first); any row branches on punctuation (N3) |
| **L1 proposal** | `derived` built from `DIGIT` slots (+ spec) — unchanged mechanics; the proposal record gains `observation_ref = turn` so provenance is followable | deriving from `UNKNOWN`/`AMBIGUOUS` |
| **L3 edit buffer** | fragments store the *observation items*, not only text; `resolve_edit` binds references ("इसमें जो 5") against **the sequence under discussion** — the open proposal if one is `spoken`, else `base` | resolving against `base` while a spoken proposal is open (133627 t15 mismatch) |
| **L2 delivery** | unchanged | — |
| **commit** | unchanged (`base ← derived` on explicit confirm of a `spoken` proposal; append during accumulation) | committing digits that do not trace to `DIGIT` slots (N2) |
| **L4** | an `INCOMPLETE` numeric turn is never counted as a silent turn (it always speaks) | — |
| **L5 LLM boundary** | `task_state_view` gains `numeric_uncertainty: bool` (no digits) so the LLM, if ever reached, cannot claim to have understood a number | LLM seeing slots/digits |
| **L6** | unchanged | — |

Explicit non-change: `base`, `proposal`, `pending_edit`, `delivery` keep their
definitions and their test pins from `VALUE_TRANSACTION_LOCK.md`. The observation is
**inserted in front of** the value transaction, not merged into it.

The only L1–L6 *behaviour* changes implied (to be declared as pins when implemented):
(a) L3 reference binding against the spoken proposal (row above); (b) append/replace
no longer reads surface form (N3/N5); (c) the two new clarify outcomes of N6.

---

## 11. Auditability — the per-turn record

Every numeric turn is inspectable as one chain, keyed by turn number, in the existing
`turn_lifecycle_*.jsonl` and `turn["precise_detail"]`:

```
raw STT (text, segments, logprob)
  → observation      (items/slots/certainty/reasons/version)          [new: turn["numeric_observation"]]
  → interpreted op   (row id, inputs used: cues, endpoint, structural)   [new: precise_detail.operation]
  → proposal         (base, spec, derived, mode, observation_ref)        [exists]
  → delivery         (spoken/unheard/partial + heard_text)               [exists]
  → confirmation     (turn no. + the confirm token; echo-gate evidence §12) [new: precise_detail.confirm_evidence]
  → commit           (base_before → base_after)                          [exists via base]
```

Two tools follow from this, both read-only: `stage_diagnostic.py` prints the chain per
turn (today it prints STT and reply only, and truncates STT at 60 chars — t12 and t17
of 133627 were cut; the record must carry the full text), and the replay gate checks
observation identity in addition to decision identity.

**Phase 1 as built (2026-09-04) — placement note.** The `operation` and
`confirm_evidence` records are archived under a NEW top-level key
`turn["numeric_audit"]` (with `stage`, `legacy_signal`, `observation_vs_signal`,
`operation`, `proposal`, `confirm_evidence`, `commit`) rather than inside
`precise_detail`: the replay gate compares `precise_detail` byte-exact when an
archive carries it, so adding keys there would have broken identity on the existing
rail fixtures — the opposite of Phase 1's zero-change requirement. The observation
itself lives at `turn["numeric_observation"]` as specified. Both keys are written on
every turn (rail, LLM, greeting, route-dropped, echo-dropped), once, fail-closed
(`numeric_audit_error` / `numeric_observation_error` instead of an exception). The
replay gate compares the pure part of `numeric_observation` when present (not
`numeric_audit`, which is a derived view over already-compared decisions). Code:
`agent/numeric_observation.py`, `agent/numeric_chain.py`; tests
`phase5/tests/test_numeric_observation.py`, `test_numeric_chain.py`,
`test_numeric_observation_adversarial.py`.

---

## 12. E1 — Echo gate: text similarity alone never discards a user turn (LOCK)

Finding (audit §5): `main.is_echo` compares the romanized user text with the agent's
last line (window ratio > 0.65) and drops the turn. Short confirmations *are* the
agent's own words: `हाँ` is dropped after 35 of 46 rail lines, `ठीक है` after 19,
`सही है` 17, `नहीं` 19 — exactly the words a user says < 1.5 s after "confirm kar de?".
133627 t11 `ठीक है` (sim 0.78, **acoustic corr 0.32 — i.e. not an echo**) was the only
correct confirmation of the session and never reached the engine.

**Invariant E1.** A turn may be classified as agent echo only when **at least two
independent channels** agree, of which at most one is textual:

1. *textual* — the existing similarity (kept as evidence, not as a verdict);
2. *acoustic* — `echo_corr_score` ≥ the agreement threshold (`ECHO_SHADOW_AGREE`,
   0.45 today, already computed on every turn);
3. *temporal/playback* — the user's speech onset lies **inside** agent playback or
   within the room-echo decay window after it (bounded, ≤ a few hundred ms), *and*
   the agent was actually emitting audio (not a cancelled/unheard response).

Corollaries:

- While a proposal is `confirming` and its echo was `spoken`, a short turn that
  contains a confirm/reject token and fails channels 2–3 is **always delivered to the
  rail**. (A dropped confirmation is a lost commit; a wrongly kept echo is at worst a
  harmless `confirm_ack` of something the agent just said — and L2 already requires a
  *spoken* proposal, so an echo cannot commit an unheard one.)
- The gate writes its evidence (`text_sim`, `corr`, `onset_vs_playback_ms`, verdict)
  into the turn record (the confirmation evidence line of §11).
- The late-repeat guard (> 1500 ms after audio end ⇒ keep) stays; it is channel 3 in
  one direction only.

This is a boundary invariant on *turn validity*, sibling of the numeric boundary: both
say "a single lossy text feature is not evidence". Its implementation lives in
`main.py`'s validity gate, not in the rail.

Test oracle: replay 133627 t11 through the gate with its logged corr (0.32) and timing
→ **kept**; replay a genuine echo turn from the baseline archive (text sim high, corr
≥ 0.45, onset inside playback) → dropped; random confirm words vs all 46 rail lines
with corr < floor → 0 dropped.

---

## 13. Architectural questions

### A. Deterministic normalizer, small classifier, or something else?

**A deterministic, versioned observation function with explicit non-decisions** —
i.e. a normalizer that is allowed to output "I don't know". Not a classifier: a
classifier's job is to pick; this layer's job is to *refuse to pick* when the input
does not determine the answer, and to record why. It is table-driven (lexicon, shape
rules) and pure (N7), so it replays and property-tests. Where a learned component
enters is *upstream of it* — as a better or second recogniser feeding **more slots
with confidence** (see C/D) — never as a tie-breaker inside it. This keeps the owner's
standing rule intact: no LLM on the digit path; the LLM never writes digits.

### B. Where does uncertainty live?

In **three places, each owning one kind**:

1. *Per-slot* in the observation (`UNKNOWN`, `AMBIGUOUS` with alternatives,
   per-token `confidence` when available) — perception uncertainty. Immutable.
2. *Per-operation* in the controller decision (`operation_ambiguous`, with the
   conflicting cues) — instruction uncertainty. Resolved only by clarification/hold.
3. *Per-value* in the transaction (`proposal.delivery`, `status`) — verification
   uncertainty. Already exists (L1/L2).

Rule: uncertainty **only flows downstream and only as a blocker**; it is never
summarised into a single score, never resolved by a later layer "knowing better", and
each kind is cleared by exactly one event (1: a second look or a clarified re-say; 2:
an explicit instruction; 3: a heard echo + explicit confirm).

### C. How does the system obtain a second look?

Second looks are the *only* legitimate way to turn an `UNKNOWN`/`AMBIGUOUS` slot into a
`DIGIT` without the user re-saying it. In order of cost:

1. **Same audio, second decode** — the utterance audio is still in memory at
   observation time (`audio_data` in `transcribe_and_respond`). A second decode of the
   *same buffer* with a numeric-biased prompt / different temperature / a second model
   yields a second slot sequence; slots where both agree become `DIGIT` with
   `confidence` from agreement; disagreements stay `AMBIGUOUS` with both readings as
   alternatives. Requires keeping the audio for the turn's lifetime (D). This is the
   *feeder* the earlier locks deferred — and this is where it belongs: **behind the
   observation boundary, as a producer of slots, never as a decider.**
2. **Cross-turn agreement** — the same speaker re-rendering the same segment (133627
   t12 and t19 agreed; t4 disagreed). The observation layer does not do this (N7); the
   operation layer may use agreement between two `COMPLETE` observations as evidence
   for a *structural* decision, never to fill an `UNKNOWN`.
3. **Targeted clarify** — ask for the slot, not the number ("teesra digit?"). The rail
   already has the delivery machinery; N6 supplies the wording constraint.
4. **User audio retention for offline audit** — not a runtime second look, but without
   it the 30 % figure in the audit can never be replaced by a measured one. Retention
   per turn (opt-in env, like `AIVA_TTS_DUMP`) is a prerequisite for choosing 1's
   mechanism on evidence.

Locked here: *that* second looks exist only as slot producers and *where* they plug in.
Not locked: which mechanism, which model, thresholds.

### D. What must survive from STT into the observation layer?

So recognition can improve without touching L1–L6, the observation must carry — and
the STT provider must therefore return —:

- the **full transcript text** (untruncated) and its **segments with per-segment
  `avg_logprob` / `no_speech_prob`**; when the provider offers **word-level
  timestamps/confidences** (`timestamp_granularities=["word"]` on whisper verbose
  JSON — not requested today), each `Slot.token` carries `start_ms/end_ms/confidence`;
- the **provider/model/language/prompt/temperature** used (`source`), so a later
  recogniser can be A/B-replayed against the same record;
- a **handle to the utterance audio** for the turn's lifetime (and optionally a dump
  path), so a second look (C1) can run on the same signal;
- the **endpoint evidence** (`premature_resume_ms`, durations, energy profile) —
  already archived, now formally an input;
- the **verbatim span offsets** for every slot (already implied by the schema), so
  any future model can be evaluated *per slot* against what the user later confirmed.

Nothing in that list is consumed by L1–L6. They see `seg`, `certainty`, and the
provenance flags — the contract of §10 — which is why a better recogniser changes the
observation record and nothing else.

### E. Representation of the actual t3–t17 inputs

Slot notation: `d` = `DIGIT d`; `?` = `UNKNOWN`; `{a|b}` = `AMBIGUOUS`. Offsets are
char offsets into the STT text.

**`026900`** (t3)
```
items[0]: span(0,6) surface=RUN boundary={start:T,end:T}
  slots: 0 2 6 9 0 0   (6× LITERAL, tokens '026900'[i])
certainty=COMPLETE  non_numeric_tokens=[]
```
Operation layer (downstream, for illustration): base empty, task armed ⇒ append. Same
as today.

**`1, 2, 5, 8, 0, 1, 2, 0, 3`** (t4)
```
items[0]: span(0,25) surface=SEPARATED group_breaks=[1..8] boundary={T,T}
  slots: 1 2 5 8 0 1 2 0 3   (9× LITERAL)
certainty=COMPLETE  non_numeric_tokens=[]
endpoint.premature_resume_ms = (as archived; t4 resumed shortly after t3's endpoint)
```
Identical slots to `125801203` (N3). Operation layer: no instruction cue; endpoint
says continuation within the window; the slots do not contain the base ⇒ not a
restatement ⇒ **append** `026900125801203`. Note what the boundary does *not* do: it
cannot fix a wrong-but-`COMPLETE` digit (whisper's `8` is a confident `DIGIT` slot).
Only a second look on the same audio (§13 C1) or the user's later correction can —
and that correction is exactly what t13–t15 were. What changes vs live: the correct
*operation* is chosen, the head is not demoted to "pehle wala", and the user is not
asked to confirm a 9-digit fragment as a new number. This is also the honest limit of
the lock: N1–N7 make uncertainty *visible* where STT exposes it (OOV, count words,
cue conflicts) and stop the string's shape from choosing the operation; they do not
make a confidently wrong transcript right. That is the second look's job (C).

**`पूरा बोल पूरा नंबर 026900 आगे`** (t10)
```
items[0]: span(19,25) surface=RUN boundary={start:F,end:F}
  slots: 0 2 6 9 0 0   (6× LITERAL)
certainty=COMPLETE
non_numeric_tokens=[पूरा(0,4) बोल(5,8) पूरा(9,13) नंबर(14,18) आगे(26,29)]
```
Operation layer: restart cue (`पूरा`) **and** continuation cue (`आगे`) present, and the
6 slots equal the current base prefix ⇒ instruction ambiguous under N5 ⇒ **N6
clarify**: "zero two six nine zero zero — yahi rakhoon aur aage bolo, ya poora phir se
likhoon?" Live: REPLACE-proposal of the head, then the confirmation was eaten (§12).

**`और वहाँ पे 5 जीरो लगा`** (t15)
```
items[0]: span(11,17) surface=WORDS boundary={F,F}
  slots: {50 | 00000}   (1 AMBIGUOUS group, provenance COUNT_OR_DIGIT, count_token '5'(11,12), token 'जीरो'(13,17))
certainty=INCOMPLETE  reasons=[COUNT_OR_DIGIT]
non_numeric_tokens=[और वहाँ पे लगा]  (+ the L3 buffer's earlier fragments)
```
Operation layer: L3 has an open buffer (t13/t14); the joined instruction parses today
to `(wrong 12520 → correct X)`. Bound to the **spoken proposal** `125205203` (§10 L3
binding) the deterministic apply gives, per alternative: `50` → `505203`;
`00000` → `000005203`. Neither is what the user meant (`12000005203`) — the joined
text has lost the position of the *5 the user pointed at*, because t13's STT wrote the
same digits on both sides of its negation. So under the boundary this turn is
**INCOMPLETE in the observation (N4) *and* under-determined in the operation**, and
N6 forbids both derivations. The correct outcome is a clarify that speaks the two
readings and asks which — and, when the answer is "five zeros", asks *where* if the
spec is still not applicable. That is a worse-sounding but honest answer; the live
outcome was a confident wrong one (`50`), a generic "kaunsa hissa?", and a user who
gave up and re-dictated.

**`जीरो टू सिस नाइन डबल जीरो`** (t17, inside `देख … ये ठीक है इसको लिख कर रख लिया`)
```
items[0]: span(4,29) surface=WORDS boundary={F,F}
  slots: 0 2 ? 9 0 0
         WORD WORD OOV('सिस'(12,15)) WORD DOUBLE DOUBLE
certainty=INCOMPLETE  reasons=[OOV_TOKEN]  unknown_count=1
non_numeric_tokens=[देख ये ठीक है इसको लिख कर रख लिया]   ← contains a confirm token
```
Operation layer: the turn is **numeric and INCOMPLETE**, so N6 intercepts *before* the
confirm token can act (a confirm word in a turn that also carries an incomplete number
is not a confirmation of anything — it is part of a re-dictation). ⇒ **clarify the
slot**: "zero two — teesra digit? — nine zero zero". Live: no observation, treated as
`ठीक है`; would have committed `125205203` had the previous echo been heard.

Cross-check of the audit's claim that this is not example-driven: the five inputs
exercise five *different* schema features (LITERAL run; N3 surface equivalence + endpoint;
N5 cue conflict; N4 count/digit; N1 OOV) and none of them required a new word, row, or
threshold — only the boundary.

---

## 14. Explicitly not locked here

- The extraction mechanism (regex vs tokenizer vs small FST), the lexicon contents, the
  shape rule for "OOV inside a span" — implementation choices, to be tests-first.
- Second-look mechanism (model, prompt, thresholds), audio retention format.
- Clarify wording pools.
- Any change to `accum_gap`, `RESUME_WINDOW_MS`, `SILENT_STREAK_MAX` (policy constants
  stay as pinned).
- Whether `surface`/`group_breaks` are ever *used* — they are recorded; using them
  would require a new lock.

---

## 15. Approval checklist (owner)

| # | item | ruling |
|---|---|---|
| Q1 | Schema §2 — slots with `DIGIT/UNKNOWN/AMBIGUOUS`, provenance enum, descriptive-only `surface` | |
| Q2 | N1 OOV-inside-span ⇒ `UNKNOWN` slot (structural rule, not lexicon) | |
| Q3 | N3 — retire surface form as an operation input; smoke-2 t34 keeps its outcome under a different justification | |
| Q4 | N4 — `5 जीरो` is `AMBIGUOUS {50 \| 00000}`; resolution only by clarify/second look | |
| Q5 | N5 precedence: cues → endpoint → structure → policy default; cue conflict ⇒ clarify | |
| Q6 | N6 — `INCOMPLETE` ⇒ clarify naming the slot / hold / second look; never proposal, append, LLM | |
| Q7 | §10 L3 binding: references resolve against the *spoken* proposal when one is open | |
| Q8 | E1 — echo verdict requires ≥ 2 channels, ≤ 1 textual; confirm/reject tokens while `confirming` always reach the rail | |
| Q9 | §13 C — second looks are slot producers behind the boundary (this is where the feeder goes, later) | |
| Q10 | §13 D — STT must return segments/word-level data and the audio handle survives the turn; user-audio retention opt-in | |
| Q11 | Order of implementation once locked: (i) observation record + audit chain (no behaviour change, replay-identical), (ii) E1 echo gate, (iii) N1/N3/N4/N5/N6 behind the rail with the declared pins, (iv) second look | |

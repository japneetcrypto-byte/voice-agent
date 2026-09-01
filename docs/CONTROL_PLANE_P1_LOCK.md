# Control Plane V1 — P1 Design Lock (shadow, telemetry-only)

**Date:** 2026-09-02 · **Status:** PENDING OWNER APPROVAL — no code until approved · **Base doc:** `docs/CONVERSATION_CONTROL_PLANE_V1.md` (proposal)

**P1 scope (locked):** one pure `control_turn()` + the unified Decision schema + the state-conditioned precedence table over **existing** detectors, wired in **shadow mode only** (telemetry, zero behavior change). P2–P5, Phase C, memory patches, and broad refactor are all explicitly **not** part of P1.

---

## 1. Final Decision schema

```python
# agent/control_plane.py  (new — pure, stdlib, deterministic, no LLM, no I/O)
@dataclass(frozen=True)
class Decision:
    turn_intent: str            # NORMAL|CONTINUE|STOP|CORRECT|CONFIRM|REJECT|REPEAT
    memory_intent: str          # NONE|POSSIBLE_SAVE|SAVE|UPDATE|RECALL|CORRECT|FORGET
    conv_state: str             # NORMAL|CONTINUING|CONFIRMING|SAVING|RECALLING|
                                # CORRECTING|TASK_ACTIVE    (state AFTER this turn)
    turn_owner: str             # USER|AGENT|SYSTEM
    delivery_mode: str          # NEW|CONTINUE|HOLD|SILENT
    action: str                 # llm|greeting|rail_echo|rail_accumulate|rail_confirm|
                                # rail_recall|rail_repair|rail_arm|suppress|drop|clarify|idle
    llm_instruction: str | None # what the LLM is told to do; None = no LLM call
```

**What the Decision carries (controller's domain):** intent, next conversation state, turn ownership, delivery mode, the concrete system action, and the LLM instruction.

**What it does NOT carry (locked out — stays where it already belongs):**
- ❌ Response *policy content* (contract, caps, nudges, reconcile payloads) — stays in `response_pipeline.build_policy_and_contract`.
- ❌ Prose / persona / script — stays in the LLM path.
- ❌ Memory rows / gates / store writes — stays in `MemoryStore` + `memory_gate` + consolidation + ROW 51.
- ❌ Transport (VAD/STT/TTS/endpoint/barge) — stays in the LiveKit loop.

`action` is **routing**, not policy: it names which existing deterministic path would run. It maps 1:1 to today's outputs (`precision_rail` actions, `turn_controller` suppress/respond, `route_decision` respond/drop/clarify/recovery).

---

## 2. Enum resolution (no ambiguity)

**`turn_intent` — conversational ONLY** (owner's list, verbatim):

```
NORMAL  CONTINUE  STOP  CORRECT  CONFIRM  REJECT  REPEAT
```

SAVE/RECALL/etc. are **not** turn intents. A memory verb ("याद रखना", "मैंने क्या बताया था?") sets `turn_intent=NORMAL` (it's a normal user statement/question), sets `memory_intent` on the memory axis, and routes via `action`. The conversational axis never conflates the two.

**`memory_intent` — the memory axis**, with `POSSIBLE_SAVE` **declared as a first-class member** (resolved, not an undeclared third state):

```
NONE  POSSIBLE_SAVE  SAVE  UPDATE  RECALL  CORRECT  FORGET
```

| Value | Definition | Routes to (existing) |
|---|---|---|
| `NONE` | nothing memory-worthy this turn | nothing |
| `POSSIBLE_SAVE` | **inferred** self-statement / travel clause / unconfirmed digits | extract_place_facts / fact_candidates **inferred → pending** (criterion=salient) |
| `SAVE` | **explicit** save directive ("याद रखना"), explicit self-statement ("मेरा नाम X है"), or **confirmed** value (ROW 51) | explicit → immediate commit path (criterion=explicit) |
| `UPDATE` | additive correction to a stored fact ("और मेरी बहन भी दिल्ली में है") | **Phase D hook** — P1 computes + logs only |
| `RECALL` | recall query ("मैंने क्या बताया था?") | saved-number rail recall / memory_view / rule-14 honest path |
| `CORRECT` | negating correction of a stored fact ("नहीं, वो नैनीताल नहीं था") | **Phase D hook** — P1 computes + logs only |
| `FORGET` | explicit "भूल जा / हटा दो" | **gated on owner sign-off** (destructive) — P1 computes + logs only |

**Both axes set independently.** "नहीं, वो नैनीताल नहीं था" = `turn_intent=CORRECT` **and** `memory_intent=CORRECT`. "बस" mid-dictation = `STOP` **and** `SAVE` (the pending value).

---

## 3. Formal precedence — intent = f(signal, state)

**The lock (structurally kills precedence bugs):** the table is ONE ordered list of guards; each guard matches on **(signal_group, conv_state)**; the **first matching guard's Decision wins**; there is no second decision-maker and no call-order to mis-chain in main.py.

```python
signals = detect_signals(text, turn_no, state_snapshot)   # pure; reuses existing detectors (§4)
Decision = first_match(GUARDS, signals, state_snapshot)   # GUARDS: ordered list; first match wins
```

**Guard ranks (top → bottom).** Higher rank = more specific / higher-impact. The ordering itself is part of the spec and is pinned by tests.

| Rank | Guard (signal_group × state) | Decides | Rationale |
|---|---|---|---|
| G0 | transport/safety pre-filter: echo / invalid / drop / agent-speaking (input from `route_decision`) | `action=drop\|suppress`, owner=AGENT/SYSTEM | never answer junk (CA6) |
| G1 | task repair/correct: state ∈ {TASK_ACTIVE, CONFIRMING} + correction signals (ONLY_THIS_RE, RESTART_RE, REJECT_EN_RE+digits, _parse_correction) | `turn_intent=CORRECT`, `memory_intent=CORRECT\|POSSIBLE_SAVE` | corrections never wipe/append wrongly |
| G2 | confirm/reject: state ∈ {CONFIRMING, SAVING} (or TASK_ACTIVE + confirm word) + CONFIRM_EN_RE / REJECT_EN_RE / "बस" | `CONFIRM\|REJECT`, `memory_intent=SAVE\|NONE` | confirmation is the trust gate |
| G3 | digits: state=TASK_ACTIVE + digit signals (GROUPED/DIGIT_RUN/SEPARATED/DIGIT_TOKEN/_cluster_fires) | `NORMAL`, `POSSIBLE_SAVE`, state stays TASK_ACTIVE, owner=SYSTEM, `SILENT\|NEW` (accumulate/echo) | dictation is system-owned, never leaks to LLM |
| G4 | memory-explicit verbs (any state): `_SAVE_INTENT_RE`, RECALL_RE, SAVED_NUMBER_QUERY_RE, STATUS_RE, QUERY_STORED_RE, TOPIC_SWITCH_RE | sets `memory_intent` (SAVE/RECALL/CORRECT/UPDATE/FORGET per §2); `turn_intent` stays conversational; `action` routes | memory control is explicit user words |
| G5 | conversational controls (any state): STOP ("बस"/"रुको") > REPEAT ("फिर से बताओ") > CONTINUE (CONTINUE_CUE_RE, only meaningful when delivery active) > CORRECT | `STOP\|REPEAT\|CONTINUE\|CORRECT` | turn-level conversational control |
| G6 | greeting: first word ∈ GREETING_MARKERS | `action=greeting`, owner=SYSTEM | deterministic greeting |
| G7 | default | `NORMAL / NONE / NORMAL / USER / NEW / llm` | everything else → LLM |

**Within-rank sub-order** (also pinned): G2 `REJECT > CONFIRM`; G5 `STOP > REPEAT > CONTINUE > CORRECT`; G4 `FORGET > CORRECT > UPDATE > SAVE > RECALL > POSSIBLE_SAVE` (destructive/negative > explicit imperative > query > inferred).

**Tie-breaks across axes (documented):** axes are independent — no cross-axis conflict exists (each axis resolves separately). Within an axis: negation > affirmation; imperative > interrogative.

**Conflict examples resolved by the ranks:** "9935 बस" (TASK_ACTIVE) → G3 digits accumulate, then G2 "बस" → CONFIRM + SAVE. "नहीं 420 नहीं 0000 है" (TASK_ACTIVE) → G1 repair (never a plain reject-wipe). "आगे बताओ" with no delivery → G7 (G5 CONTINUE requires delivery active). "हेलो 9935" → G0/G6 vs G3: greeting is first-word; digits still accumulate under TASK_ACTIVE — rank resolves by state (if already TASK_ACTIVE, G3 wins; else G6 greeting, digits not fired — matches today's greeting-while-armed row).

---

## 4. Existing-detector map (NO new detector set)

`control_plane.py` defines **zero `re.compile`**. `detect_signals()` imports the existing regexes/detectors and rebinds them to signal keys. Anything without an existing detector stays `False` (a missing signal is better than a new regex — P1 adds no patterns).

| Signal key | Existing detector (file) |
|---|---|
| digits_value / digits_cluster / grouped / separated / digit_words / times | GROUPED_DIGITS_RE, DIGIT_RUN_RE, SEPARATED_DIGITS_RE, DIGIT_TOKEN_RE, DIGIT_WORD_MAP, _cluster_fires, _NUMBER_KIND_RE (`precision_rail.py`) |
| confirm / reject / questionish / claim / complaint | CONFIRM_EN_RE, REJECT_EN_RE, QUESTIONISH_RE, CLAIM_RE, COMPLAINT_RE (`precision_rail.py`) |
| recall / saved_number_query / status / query_stored / only_this / restart / abandon / dearm / continue_cue / write_command / announce / topic_switch | RECALL_RE, SAVED_NUMBER_QUERY_RE, STATUS_RE, QUERY_STORED_RE, ONLY_THIS_RE, RESTART_RE, ABANDON_RE, DEARM_DETAIL_RE, CONTINUE_CUE_RE, WRITE_COMMAND_RE, WRITE_INTENT_RE, ANNOUNCE_RE, TOPIC_SWITCH_RE, _NUMBER_TOPIC_RE (`precision_rail.py`, reused by `conversation_controller.py`) |
| save_intent | `_SAVE_INTENT_RE` (`fused_turn.py` — Gap R) |
| greeting | GREETING_MARKERS + first-word rule (`turn_controller.py`) |
| continuation_fragment / suppress-reasons | `turn_controller.decide()` fragment logic (`turn_controller.py`) |
| route (respond/drop/clarify/recovery) | `route_decision` (`turn_router.py`) |
| turn_relation (backchannel/listen_request/content) | `classify_turn_relation`, BACKCHANNEL_TOKENS, LISTEN_REQUEST_TOKENS (`stt_validation.py`) |
| mode (CALM/VENT/ADVICE/CLOSING) | `classify_mode` (`state_updater.py`) |
| correction parse | `_parse_correction` / `_apply_correction` (`precision_rail.py`) |
| task state snapshot | `engine["conv"]` (ConversationState), `engine["detail"]` (active/last_chunk), `engine["wait_streak"]` |

**State input = a read-only snapshot** of `engine["conv"]` / `engine["detail"]` / `engine["wait_streak"]` / policy.mode. The shadow never mutates them.

---

## 5. Adversarial expected Decisions (locked — these become unit tests)

| # | text | state (input) | turn_intent | memory_intent | conv_state (after) | owner | delivery | action | llm_instruction |
|---|---|---|---|---|---|---|---|---|---|
| 1 | "हाँ" | NORMAL | NORMAL | NONE | NORMAL | USER | NEW | llm | None |
| 2 | "हाँ" | CONFIRMING (value pending) | CONFIRM | SAVE | NORMAL | SYSTEM | NEW | rail_confirm | None |
| 3 | "हाँ" | TASK_ACTIVE (dictating, no confirm pending) | NORMAL | NONE | TASK_ACTIVE | SYSTEM | SILENT | suppress | None |
| 4 | "बस" | NORMAL | STOP | NONE | NORMAL | USER | NEW | llm | "acknowledge stop" |
| 5 | "बस" | CONFIRMING | CONFIRM | SAVE | NORMAL | SYSTEM | NEW | rail_confirm | None |
| 6 | "बस" | TASK_ACTIVE (value present) | CONFIRM | SAVE | CONFIRMING | SYSTEM | NEW | rail_confirm | None (echo full + ask) |
| 7 | "आगे बताओ" | delivery active (detail.active) | CONTINUE | NONE | CONTINUING | USER | CONTINUE | llm | "continue current explanation from step N" |
| 8 | "आगे बताओ" | no delivery | NORMAL | NONE | NORMAL | USER | NEW | llm | None |
| 9 | "नहीं, वो नैनीताल नहीं था" | memory has Uttarakhand row | CORRECT | CORRECT | CORRECTING | USER | NEW | llm | "supersede stored memory (Phase D hook)" |
| 10 | "नहीं, वो नैनीताल नहीं था" | no memory | CORRECT | NONE | NORMAL | USER | NEW | llm | None (conversational only, honest) |
| 11 | "9935" | TASK_ACTIVE (dictating) | NORMAL | POSSIBLE_SAVE | TASK_ACTIVE | SYSTEM | SILENT | rail_accumulate | None |
| 12 | "9935" | NORMAL (unarmed) | NORMAL | POSSIBLE_SAVE | TASK_ACTIVE | SYSTEM | NEW | rail_echo | None |
| 13 | "50-60 लोग" | NORMAL | NORMAL | POSSIBLE_SAVE | TASK_ACTIVE | SYSTEM | NEW | rail_echo | None (records current 5060 behavior; range-vs-ID is a separate decision, NOT P1) |
| 14 | "याद रख लेना" | NORMAL | NORMAL | SAVE | SAVING | USER | NEW | llm | "acknowledge + capture (explicit path)" |
| 15 | "मैंने कौन सी जगह बताई थी?" | NORMAL | NORMAL | RECALL | RECALLING | USER | NEW | llm | "answer from memory_view, else honest no-record" |
| 16 | "हेलो" | NORMAL | NORMAL | NONE | NORMAL | SYSTEM | NEW | greeting | None |

Rows 2, 6, 11, 12, 13 exercise the dictation/task axis; 7/8 the delivery axis; 9/10 the memory-correction axis with and without a record; 1/2/3 the "हाँ" state-conditioning the owner asked for.

---

## 6. Ownership boundaries (locked)

| Owner | Owns | Never does |
|---|---|---|
| **Control plane** (`control_turn`) | deterministic decision/routing: intent, state, ownership, delivery mode, action, llm_instruction | no prose, no policy content, no memory writes, no transport |
| **Response pipeline** (`response_pipeline`) | response policy/expression: contract, caps, repeat-guard, script, release | no intent/state decisions, no memory writes |
| **Memory** (`MemoryStore`, `memory_gate`, extractors, consolidation, ROW 51) | memory lifecycle: capture → validate → pending → deterministic confirm → commit → recall/supersede | no conversation decisions, no prose |
| **LiveKit/VAD loop** (`main.py`) | transport: listen/speak/interrupt/endpoint | no intent/state/memory/policy decisions |

The controller **routes** memory (`memory_intent` → which existing path) and **never owns** it; the pipeline **consumes** the Decision's instruction and **never decides** intent.

---

## 7. P1 shadow mode (telemetry only — cannot alter production)

1. **Wiring:** in `main.py` `transcribe_and_respond`, after the existing `_rail`/`_greeting`/`turn_controller` computation, compute `decision = control_turn(text, turn_no, snapshot)` — inputs read-only, output written to `turn["control_shadow"] = asdict(decision)` + `tmark("DECISION_SHADOW", ...)`. Entirely inside `try/except` → on any error: log `control_shadow_error`, continue. **The existing chain runs byte-identical.**
2. **Same shadow in `run_turn`** (`response_pipeline.py`) so the harness can exercise it. Both wiring points are the ONLY main-path touches; nothing else changes.
3. **Replay:** `control_shadow` follows the established **compare-when-present** pattern (like `precise_detail`): synthetic fixtures regenerate WITH the key; the real baseline archives lack it → real gate stays EMPTY DIFF.
4. **Divergence policy:** shadow-vs-chain divergence is **logged, never acted on**: `tmark("DECISION_SHADOW_DIVERGENCE", chain_action=..., shadow_action=...)` when `action` differs from the executed path. This is exactly what P1 exists to surface; a divergence is a finding to review, not a P1 failure.
5. **Determinism:** same `(text, turn_no, snapshot)` ⇒ same Decision (unit-tested).

---

## 8. P1 acceptance gate

1. **Tests-first**, new suite `phase5/tests/test_control_plane_v1.py`:
   - the 16-row adversarial table, exact-match on all 7 fields;
   - conflict/precedence rows: REJECT > CONFIRM; STOP > CONTINUE; G1 repair vs G2 reject; digits vs recall-query in TASK_ACTIVE; greeting vs armed-state;
   - `POSSIBLE_SAVE` declared + routes to pending (never explicit);
   - axes independence: "नहीं, वो नैनीताल नहीं था" sets BOTH CORRECT axes;
   - determinism (same input → same Decision);
   - no-crash on garbage / empty / Devanagari-punctuation / long input;
   - **structural pin: `control_plane.py` contains no `re.compile`** (zero new detectors by construction).
2. **Replay identity = EMPTY DIFF** on the synthetic gate (regenerated with `control_shadow`) AND the real baseline gate unchanged (compare-when-present).
3. **All existing suites green (42 today).**
4. **No behavior change:** `git diff` shows only `agent/control_plane.py` (new) + the two shadow wiring points + tests. No changes to `precision_rail.py`, `conversation_controller.py`, `turn_controller.py`, `turn_router.py`, `state_updater.py`, `fused_turn.py`, `memory_*`, `prompt_fragments.py`, or any rail/response behavior.
5. **No memory patches, no Phase C, no P2–P5, no broad refactor.**

**Definition of done (P1):** the gate above green + `[ControlPlane] shadow ok (N decisions, M divergences)` visible in a synthetic end-to-end run + owner review of the divergence log before P2 is authorized.

---

## 9. Explicitly deferred (locked OUT of P1)

P2 AgentState first-class · P3 transition-table consolidation (rows 1–51 migrate) · P4 memory_intent routing (RECALL/CORRECT/FORGET execute) · P5 turn_owner + delivery_mode ownership · Phase C L2→L3 · memory patches (incl. the "50-60" range question) · sales/domain policy · new event-log system (extend `tmark` only).

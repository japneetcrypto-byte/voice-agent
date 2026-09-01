# Control Plane V1 — P1 Design Lock (shadow, telemetry-only)

**Date:** 2026-09-02 · **Status:** PENDING OWNER APPROVAL — no code until approved · **Base doc:** `docs/CONVERSATION_CONTROL_PLANE_V1.md` (proposal)

**P1 scope (locked):** one pure `control_turn()` + the unified Decision schema + the state-conditioned precedence table over **existing** detectors + a read-only **Decision Safety / Invariant gate** (§9), wired in **shadow mode only** (telemetry, zero behavior change). P2–P5, Phase C, memory patches, and broad refactor are all explicitly **not** part of P1.

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
    llm_instruction: str | None # CANONICAL semantic directive key (locked set below) —
                                # rendered into the session language by the response layer;
                                # the controller never emits language-specific prose
```

**Locked `llm_instruction` directive-key set (enumerated — no `"..."`):**

```
None
CONTINUE                # continue current delivery from delivery_state (pairs with engine["detail"] resume)
RECALL_MEMORY           # answer from memory_view; if no record -> honest no-record (rule-14)
ACKNOWLEDGE_SAVE        # explicit save accepted/confirmed
ACKNOWLEDGE_STOP        # acknowledged stop (conversational)
ACKNOWLEDGE_FORGET      # acknowledged forget (P1: conversational only; FORGET never executes)
SUPERSEDE_MEMORY_HOOK   # memory-correction hook (Phase D — P1 only logs the intent)
GREET                   # reserved (greeting action uses its own deterministic lines; no LLM)
```

Each key is **semantic and language-neutral**; the response layer renders it into the
session language (e.g. `CONTINUE` → "aage badhte hain…" / "continue…", `RECALL_MEMORY` →
the honest-recall rendering). A value outside this set is an **S1/I9 violation**. The
delivery *content* (resume step/payload) lives in the existing `delivery_state`, never
inside `llm_instruction`.

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

**4.1 Language-agnostic control plane — canonical signal contract (locked principle)**

> **The Control Plane is language-agnostic. Language-specific interpretation belongs
> exclusively in the signal/detector adapter layer. All adapters must emit the same
> canonical signal contract consumed by `control_turn()`. Adding a language must not
> require changes to `control_turn()`, the Decision schema, the precedence rules, or the
> validator — except where a genuinely language-independent semantic rule changes.**

**The canonical signal contract** = a fixed set of keys + types, language-agnostic by
definition (English names, no language content in the values beyond what the detector
produced):

```python
SignalContract = {
    "digits_present": bool, "digits_value": str, "digits_cluster": bool,
    "confirm": bool, "reject": bool, "questionish": bool, "claim": bool, "complaint": bool,
    "recall": bool, "saved_number_query": bool, "status_query": bool,
    "query_stored": bool, "only_this": bool, "restart": bool, "abandon": bool,
    "dearm_detail": bool, "continue_cue": bool, "write_command": bool,
    "announce": bool, "topic_switch": bool, "save_intent": bool,
    "stop": bool,                     # STOP cue ("बस"/"bas"/"रुको"/"ruko"/"stop") —
                                      # adapter-computed exact-token, language-neutral key
    "greeting_first_word": bool, "continuation_fragment": bool,
    "route": str, "turn_relation": str, "mode": str,
    "correction_spec": object | None,   # parsed correction (from _parse_correction)
}
```

`stop` is the one key the locked G5 row ("बस" NORMAL → STOP + ACKNOWLEDGE_STOP)
requires that the §4.1 first draft listed without a slot — a STOP cue is a
*distinct* signal from a confirm word (the Hindi adapter emits BOTH `confirm`
and `stop` for "बस"; the core resolves by state: CONFIRMING/TASK_ACTIVE+value →
CONFIRM (G2 outranks G5), NORMAL → STOP). `repeat` is deliberately NOT a key:
a repeat request ("फिर से बताओ") is the existing `recall` signal; the core
disambiguates by delivery state (G5 REPEAT = `recall` + delivery active, G4
RECALL = `recall` without delivery).

**Implications (locked):**
1. `detect_signals()` returns ONLY `SignalContract` keys. `control_turn()`, the Decision
   schema, the precedence guards, and the validator consume ONLY this contract — they
   contain **no language-specific logic, no language tokens, no language regexes**.
2. The detectors are **language adapters**: `precision_rail`, `stt_validation`,
   `turn_controller` markers, `fused_turn._SAVE_INTENT_RE`, digit-word maps — all emit
   the contract. A language's morphology/vocabulary lives entirely here.
3. **Adding a language** (e.g., Tamil, English-only mode) = adding/updating adapters
   only: new regexes/digit maps/marker sets that emit the SAME contract. No change to
   `control_turn()`, the Decision schema, precedence, or validator. (This is what the
   owner's "future rework prevention" targets: the contract is the stable seam.)
4. `llm_instruction` in the Decision is a **canonical semantic directive key** from the
   enumerated locked set (§1, invariant I9) — rendered by the response layer into the
   session language. The controller never emits language-specific text.
5. A semantic rule change is still allowed to touch the core — but it must be a
   genuinely language-independent semantic change (e.g., "a confirmed value always
   routes to SAVE"), reviewed as such, never a "this language says it this way" change.

**4.2 Language adapter seam + coverage-gap semantics (locked)**

The detector layer is NOT a pile of language regexes in one file. It is a per-language
adapter structure over the contract:

```
Language Adapter   (per language: hi / hi-latn / en — owner 2026-09-02)
   ├── Hindi       (hi — Devanagari; today = the existing detectors:
   │                precision_rail, stt_validation, turn_controller markers,
   │                fused_turn._SAVE_INTENT_RE, digit maps)
   ├── Hinglish    (hi-latn — code-mixed Hindi/English in Roman script; the
   │                existing Romanized detectors (CONFIRM_EN_RE, REJECT_EN_RE,
   │                RECALL_RE, CONTINUE_CUE_RE, …) already cover it — the
   │                Hindi adapter is the Hindi+Hinglish surface in P1)
   ├── English     (en — future)
   └── …
        ↓  emit ONLY SignalContract keys (language-neutral)
Canonical Signals
        ↓
Control Plane (unchanged — state + precedence → Decision)
```

Rules locked:
1. **No detector sprawl:** a language's patterns live in that language's adapter module.
   Adding Tamil = a new adapter against the SAME SignalContract — never dumping Tamil
   regexes into `precision_rail.py` / any core file, never a control-plane rule change.
2. **Coverage gap ≠ new rule:** if an adapter cannot detect a canonical signal
   ("remember this" in Tamil), it emits the contract's not-detected default
   (`save_intent=false`, `confirm=false`, …). It never invents a new rule, signal, or
   enum value. The gap is a **language-adapter coverage gap** — surfaced by the
   contract-conformance tests, fixed by improving the adapter; the control plane stays
   stable.
3. **Contract-conformance baseline:** the Hindi adapter's signal outputs for the §5 rows
   are the conformance baseline (unit-tested). A future language adapter must pass the
   same canonical cases (same expected signals) — the progressive-construction loop:
   add language → map to canonical signals → test against the contract → fix the adapter;
   core unchanged.
4. **No multilingual framework in P1:** P1 does not build adapters for other languages or
   an adapter framework — it proves the seam with Hindi (one language's existing
   detectors → contract → language-neutral core → Decision → validator).

---

## 5. Adversarial expected Decisions (locked — these become unit tests)

| # | text | state (input) | turn_intent | memory_intent | conv_state (after) | owner | delivery | action | llm_instruction |
|---|---|---|---|---|---|---|---|---|---|
| 1 | "हाँ" | NORMAL | NORMAL | NONE | NORMAL | USER | NEW | llm | None |
| 2 | "हाँ" | CONFIRMING (value pending) | CONFIRM | SAVE | NORMAL | SYSTEM | NEW | rail_confirm | None |
| 3 | "हाँ" | TASK_ACTIVE (dictating, no confirm pending) | NORMAL | NONE | TASK_ACTIVE | SYSTEM | SILENT | suppress | None |
| 4 | "बस" | NORMAL | STOP | NONE | NORMAL | USER | NEW | llm | ACKNOWLEDGE_STOP |
| 5 | "बस" | CONFIRMING | CONFIRM | SAVE | NORMAL | SYSTEM | NEW | rail_confirm | None |
| 6 | "बस" | TASK_ACTIVE (value present) | CONFIRM | SAVE | CONFIRMING | SYSTEM | NEW | rail_confirm | None (echo full + ask) |
| 7 | "आगे बताओ" | delivery active (detail.active) | CONTINUE | NONE | CONTINUING | USER | CONTINUE | llm | CONTINUE |
| 8 | "आगे बताओ" | no delivery | NORMAL | NONE | NORMAL | USER | NEW | llm | None |
| 9 | "नहीं, वो नैनीताल नहीं था" | memory has Uttarakhand row | CORRECT | CORRECT | CORRECTING | USER | NEW | llm | SUPERSEDE_MEMORY_HOOK |
| 10 | "नहीं, वो नैनीताल नहीं था" | no memory | CORRECT | NONE | NORMAL | USER | NEW | llm | None (conversational only, honest) |
| 11 | "9935" | TASK_ACTIVE (dictating) | NORMAL | POSSIBLE_SAVE | TASK_ACTIVE | SYSTEM | SILENT | rail_accumulate | None |
| 12 | "9935" | NORMAL (unarmed) | NORMAL | POSSIBLE_SAVE | TASK_ACTIVE | SYSTEM | NEW | rail_echo | None |
| 13 | "50-60 लोग" | NORMAL | NORMAL | POSSIBLE_SAVE | TASK_ACTIVE | SYSTEM | NEW | rail_echo | None (records current 5060 behavior; range-vs-ID is a separate decision, NOT P1) |
| 14 | "याद रख लेना" | NORMAL | NORMAL | SAVE | SAVING | USER | NEW | llm | ACKNOWLEDGE_SAVE |
| 15 | "मैंने कौन सा नंबर बताया था?" | NORMAL (saved record exists) | NORMAL | RECALL | RECALLING | SYSTEM | NEW | rail_recall | None (deterministic re-speak, ROW 51) |
| 16 | "हेलो" | NORMAL | NORMAL | NONE | NORMAL | SYSTEM | NEW | greeting | None |

Rows 2, 6, 11, 12, 13 exercise the dictation/task axis; 7/8 the delivery axis; 9/10 the memory-correction axis with and without a record; 1/2/3 the "हाँ" state-conditioning the owner asked for. *(`llm_instruction` values above are the locked directive keys from §1; the response layer renders them into the session language.)*

**Implementation refinements (2026-09-02, tests-first):**
- **Row 15 is record-conditional.** With a saved record the existing chain is a
  DETERMINISTIC rail re-speak (ROW 51 — never the LLM, never a fabrication), so the
  pinned row is `SYSTEM / rail_recall / None`. With NO record the same text →
  `USER / llm / RECALL_MEMORY` (rule-14 honest no-record) — covered as an extra
  adversarial case in the suite. The original example text "मैंने कौन सी जगह बताई थी?"
  matches no existing detector (a generic-fact recall query) — documented adapter
  coverage gap: it falls to G7 (LLM) in P1, and the row's text is changed to the
  canonical saved-number recall form the adapter CAN detect.
- **I7 excludes only CONFIRMING** (the dictation-echo state), not SAVING — rows 9/14/15
  pin `llm` action with CORRECTING/SAVING/RECALLING memory-axis states (the LLM speaks
  the acknowledgment; the memory path stays deterministic). `llm` remains forbidden on
  dictation-owned states: CONFIRMING, TASK_ACTIVE+digits, SILENT delivery.

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

1. **Wiring:** in `main.py` `transcribe_and_respond`, after the existing `_rail`/`_greeting`/`turn_controller` computation, compute `signals = detect_signals(text, turn_no, snapshot)` → `decision = control_turn(signals, snapshot)` → `ok, violations = validate_decision(decision, signals, snapshot)` (inputs read-only; the whole block inside one `try/except` → on any error: log `control_shadow_error`, continue). **The existing chain runs byte-identical.**
   - **Pre-chain state capture (implementation pin, 2026-09-02):** the snapshot
     must be taken BEFORE the chain's own mutations — `precision_rail_decide`
     reassigns `engine["conv"]`/`engine["dictation"]` as a side effect, so a
     snapshot read after the chain would decide on the chain's OUTPUT, not on
     the inputs the chain saw (phantom divergences). Both wiring points capture
     a shallow pre-chain copy (`pre_state(engine)`) at the top of the turn, and
     the shadow block reads that copy. Same inputs, read-only, byte-identical
     chain.
2. **Emission (fail-closed):** only a VALID decision is written to `turn["control_shadow"]` + `tmark("DECISION_SHADOW", ...)`. An INVALID one is written to `tmark("INVARIANT_VIOLATION", rule=..., decision=...)` and emits **no** shadow decision — the production chain (today's legacy path, running unchanged) is the fail-closed fallback by construction (§9.2).
3. **Same shadow in `run_turn`** (`response_pipeline.py`) so the harness can exercise it. Both wiring points are the ONLY main-path touches; nothing else changes.
4. **Replay:** `control_shadow` follows the established **compare-when-present** pattern (like `precise_detail`): synthetic fixtures regenerate WITH the key; the real baseline archives lack it → real gate stays EMPTY DIFF.
5. **Divergence policy:** shadow-vs-chain divergence is **logged, never acted on**: `tmark("DECISION_SHADOW_DIVERGENCE", chain_action=..., shadow_action=...)` when `action` differs from the executed path. This is exactly what P1 exists to surface; a divergence is a finding to review, not a P1 failure.
6. **Determinism:** same `(text, turn_no, snapshot)` ⇒ same Decision (unit-tested).

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
   - **language-agnostic pin (§4.1):** the CORE (`control_turn`, `validate_decision`,
     `build_snapshot`) contains no language-specific content — no Devanagari/Indic
     characters, no language words/tokens, no regexes, no imports (pinned by test).
     The Hindi adapter `detect_signals` is the documented language seam and the only
     carrier of language vocabulary in the module (its STOP cue set is the locked G5
     row's vocabulary — a future language adapter emits the SAME `stop` key with its
     own tokens). A language-neutrality test asserts identical Decisions for
     equivalent signals from different-language adapters (in the invariant suite §9.4).
   - **contract-conformance baseline (§4.2):** the Hindi adapter's signal outputs for
     the §5 rows are the conformance baseline (unit-tested); no adapter restructuring in
     P1 — the existing detectors are the Hindi adapter, reused as-is.
   - **NEW (CTO): `test_control_plane_invariants.py`** — one test per invariant I1–I8 (§9.4) + fail-closed emission + validator robustness + no-second-authority structural pins.
2. **Replay identity = EMPTY DIFF** on the synthetic gate (regenerated with `control_shadow`) AND the real baseline gate unchanged (compare-when-present).
3. **All existing suites green (42 today).**
4. **No behavior change:** `git diff` shows only `agent/control_plane.py` (new) + the two shadow wiring points + tests. No changes to `precision_rail.py`, `conversation_controller.py`, `turn_controller.py`, `turn_router.py`, `state_updater.py`, `fused_turn.py`, `memory_*`, `prompt_fragments.py`, or any rail/response behavior.
5. **No memory patches, no Phase C, no P2–P5, no broad refactor.**

**Definition of done (P1):** the gate above green + `[ControlPlane] shadow ok (N decisions, M divergences)` visible in a synthetic end-to-end run + owner review of the divergence log before P2 is authorized.

---

## 9. Decision Safety / Invariant layer (CTO addition — locked)

**Position: validation, NOT a second authority.** The controller is the single authority
that *chooses* the Decision. The invariant layer is a pure, read-only *gate on the
Decision's output*: it never re-derives intent/state from the raw text, never emits or
modifies a Decision, and has no store/LLM access. It exists so a wrong-but-well-formed
Decision is caught structurally instead of silently shipped.

```
detect_signals (once) → control_turn (ONLY Decision authority) → Decision
    → validate_decision (read-only gate) → valid: shadow telemetry
                                          → invalid: INVARIANT_VIOLATION, no shadow decision (fail-closed)
```

```python
def validate_decision(decision: Decision, signals: dict, state: AgentState) -> tuple[bool, list[str]]:
    # inputs = the SAME signals dict + state snapshot control_turn consumed (never recomputed here)
    # output = (ok, [violated rule names]); NEVER returns or mutates a Decision
```

**9.1 Locked invariants** (each becomes a test; checked in order; first violation reported set is the whole set):

*Schema (always first):*
- **S1** — every enum field ∈ its locked enum; `action` ∈ the locked action set
  `{llm, greeting, rail_echo, rail_accumulate, rail_confirm, rail_recall, rail_repair,
  rail_arm, suppress, drop, clarify, idle}`. An unknown action is itself a violation —
  no invented action can ever ship.

*Behavioral (Decision vs the shared snapshot — the snapshot is read, never re-derived):*
- **I1** — `TASK_ACTIVE + digits` can never route to LLM: if `state.conv == TASK_ACTIVE`
  and `signals["digits_present"]` ⇒ `action ∈ {rail_accumulate, rail_echo, rail_confirm,
  rail_recall, rail_repair, rail_arm, suppress}` and `action != "llm"` and
  `delivery_mode != "CONTINUE"`.
- **I2** — `CONFIRMING + confirm` can only use the confirmation path: if `state.conv ==
  CONFIRMING` and `turn_intent ∈ {CONFIRM, REJECT}` ⇒ `action ∈ {rail_confirm,
  rail_repair}` — never `llm`, never `greeting`.
- **I3** — `REJECT` can never become `CONFIRM`: if `turn_intent == REJECT` ⇒
  `action != "rail_confirm"` and `memory_intent != "SAVE"` (a rejection never confirms
  the pending value).
- **I4** — `memory_intent == FORGET` never executes in P1: ⇒ `action == "llm"`
  (conversational acknowledgment only) and `llm_instruction` present; P1 logs the intent
  and routes no write/delete anywhere.
- **I5** — `memory_intent` alone can never cause a memory write in P1: the Decision
  carries no write action by construction (S1's action set has none) — any
  `memory_intent != NONE` must still pair with a locked action (rail_* / llm). Actual
  writes remain the existing extractors / ROW 51 / consolidation's job, untouched by the
  Decision.
- **I6** — `delivery_mode == CONTINUE` requires active delivery: ⇒
  `state.delivery_active == True` and `action == "llm"`; otherwise the decision is
  invalid (fail-closed target: NEW).
- **I7** — `action == llm` never coexists with a dictation-owned action: ⇒
  `conv_state != "CONFIRMING"` and not (`TASK_ACTIVE` + `digits_present`) and
  `delivery_mode != "SILENT"`. Conversely: `action ∈ {rail_*, suppress, drop, greeting}`
  ⇒ `turn_owner == "SYSTEM"`. (Memory-axis states SAVING/RECALLING/CORRECTING DO pair
  with `llm` — the LLM speaks the acknowledgment; refined 2026-09-02 so I7 no longer
  contradicts pinned rows 9/14/15.)
- **I8** — fail-closed on unknown/invalid: any S1 or I1–I7 violation ⇒ the Decision is
  **not** emitted as shadow telemetry; `INVARIANT_VIOLATION {rule, decision}` is logged;
  production behavior is the existing legacy deterministic path (in P1 that path runs
  unchanged by construction).
- **I9** — language-agnostic Decision (§1, §4.1): `llm_instruction` must be one of the
  **enumerated locked directive keys in §1**; free-form prose or raw user text in any
  Decision field is a violation. The Decision never carries transcript text.

**9.2 Fail-closed semantics (P1 vs after):**
- **P1 (shadow):** the production chain already IS the safest existing deterministic
  behavior. Fail-closed = the invalid Decision is dropped from telemetry, the violation
  is logged, and nothing about production changes (it never did in P1).
- **P2+ (when the Decision starts influencing behavior):** the same gate becomes the
  enforcement point — a violating Decision causes the turn to fall back to the legacy
  deterministic path (today's chain) for that turn. Validation-with-teeth, same single
  authority, same invariants.

**9.3 Why validation cannot become a second precedence/decision system (structural):**
1. `validate_decision` returns `(bool, list[str])` — it *cannot* produce a Decision
   (type-level).
2. It performs zero pattern matching: it reads the already-computed `signals` dict and
   never calls `detect_signals` / `control_turn` / any detector.
3. Exactly ONE function in `control_plane.py` returns a `Decision` (`control_turn`);
   the validator is the only other exported function and returns a tuple (pinned by
   test).
4. It has no store/LLM access and never mutates state — it cannot start a memory write,
   an LLM call, or a state transition.

**9.4 Invariant tests** (`phase5/tests/test_control_plane_invariants.py`):
- one test per invariant (I1–I9): craft a violating `(decision, signals, state)` →
  assert `ok=False` and the rule name present; craft a compliant one → `ok=True`.
- fail-closed wiring: an invalid Decision ⇒ no `control_shadow` emitted + one
  `INVARIANT_VIOLATION` event (unit-test the emission wrapper).
- validator robustness: never raises on garbage / None / empty; unknown enum value → S1.
- determinism: same inputs ⇒ same `(ok, violations)`.
- **language-neutrality (§4.1):** the same canonical `SignalContract` produced by
  different-language adapters (e.g., Hindi + English confirm/recall/greeting signals)
  ⇒ identical Decision; and an `llm_instruction` not in the locked key set → S1/I9
  violation. Proves the core is language-agnostic, not just declared so.
- structural pins (no-second-authority): validator returns tuple not Decision; exactly
  one Decision-producing function in `control_plane.py`; no `re.compile` in the
  validator; validator imports none of `memory_store` / `memory_gate` / `fused_turn`.

---

## 10. Explicitly deferred (locked OUT of P1)

P2 AgentState first-class · P3 transition-table consolidation (rows 1–51 migrate) · P4 memory_intent routing (RECALL/CORRECT/FORGET execute) · P5 turn_owner + delivery_mode ownership · Phase C L2→L3 · memory patches (incl. the "50-60" range question) · sales/domain policy · new event-log system (extend `tmark` only) · multilingual framework / new language adapters (English; Hinglish-as-first-class — after P1 proves the seam with the Hindi+Hinglish surface).

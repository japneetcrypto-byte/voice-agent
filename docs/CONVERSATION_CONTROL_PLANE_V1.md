# Conversation Control Plane V1 — a tiny deterministic decision core around the LLM

**Date:** 2026-09-02 · **Status:** PROPOSAL (owner brief: "take these as ideas and propose
the architecture you think will be reliable and performant" — not an agreement doc, not
implemented). **Phase C remains NOT started; no memory patches this round.**

---

## 0. TL;DR — where I agree and where I push back

**Agree (and the codebase already proves it):** LLM proposes → deterministic control
disposes; intent must not be left to the LLM; memory needs an explicit lifecycle +
`memory_intent`; a deterministic control layer is the path to general → sales/support
without rewrites; per-turn event logging is the debugging tool.

**Push back on four things:**

1. **Don't build a new layer — consolidate the existing ones.** `conversation_controller`
   (task/state/transition rows 1–51), `turn_controller` (WAIT/greeting), `turn_router`
   (route decision), `response_pipeline` (policy/contract), `state_updater` (mode/goal),
   and `engine["detail"]` (delivery state) already ARE a control plane — split across
   six modules with **implicit precedence** (the greeting-clobber, WAIT-suppression and
   dictation-leak bugs were all precedence bugs). The fix is ONE decision core they feed
   into, not a seventh layer beside them.
2. **One enum is wrong — it's two axes.** `CONTINUE/STOP/CORRECT/CONFIRM/REJECT/REPEAT`
   are *conversational* intents; `SAVE/RECALL` are *memory* intents. One utterance can
   carry both ("नहीं, वो नैनीताल नहीं था" = CORRECT + memory CORRECT). Split:
   `turn_intent` and `memory_intent` (§4).
3. **No turn-level FSM.** IDLE→LISTENING→THINKING→SPEAKING→INTERRUPTED is the LiveKit
   VAD loop, already real; a parallel FSM would drift from it and double the work. Only
   the *conversation-level* state is the controller's job (§5).
4. **Reconcile once per turn, not a loop.** Desired-vs-current for delivery is real
   ("continue from part 3") but is a single pure function's output, not a reconcile loop.

**And the uncomfortable part:** the pieces exist because they were built as bug fixes.
The architecture proposal below is largely a **re-organization into one pure, logged,
replayable decision core** + two genuinely new axes (unified state-conditioned intent
table; `memory_intent`). That is the honest shape of it — and it is the right shape,
because every past failure was a precedence/state bug, which a single decision core
eliminates structurally.

---

## 1. Goal + non-goals

**Goal:** every turn produces ONE deterministic `Decision` — intent, next conversation
state, turn ownership, delivery mode, memory intent, response policy bounds, and the
LLM instruction — computed by one pure function, logged as one event, replayable by the
harness. The LLM receives the decision's *freedom bounds* and decides what to say.

**Non-goals (locked):** no new framework, no new model, no new event-log system, no
sales/domain policy now, no re-implementation of the memory store/gate/consolidation
(those stay), no VAD/STT/TTS changes, no giant state machine.

---

## 2. Current-state inventory (honest — what already exists)

| Control-plane concern | Where it lives today | State |
|---|---|---|
| Task state + transitions (dictation) | `conversation_controller` — `ConversationState`, `Signals`, `_transition()` rows 1–51 | ✅ exists (task-scoped) |
| WAIT/suppress + greeting | `turn_controller.decide()`, `greeting_line_for()` | ✅ exists |
| Route decision (respond/drop/clarify/recovery) | `turn_router.route_decision()` | ✅ exists |
| Response policy/contract/nudges | `response_pipeline.build_policy_and_contract()` | ✅ exists |
| Mode/goal (VENT/CALM/…) | `state_updater.classify_mode()/derive_policy()` | ✅ exists |
| Delivery state (fix ③ continuation) | `engine["detail"]` (active/last_chunk/resume) | ✅ exists |
| Conversation state | `engine["conv"]` / `engine["detail"]` / `engine["wait_streak"]` … (ad-hoc dict keys) | ⚠️ implicit, scattered |
| Memory intent | `_SAVE_INTENT_RE` in `fused_turn` (Gap R) | ⚠️ 1 of 6 intents |
| Intent classification | detectors scattered: rail `RECALL_RE/STATUS_RE`, controller `classify_turn`, `classify_turn_relation`, continue-cues | ⚠️ duplicated, state-less |
| Event log | `tmark()` → `turn_lifecycle_*.jsonl`, `log_event()` → `events_*.log` | ✅ exists |

**The real defect is not missing capability — it is fragmentation + implicit precedence.**
Six modules each make a decision; `main.py` chains them in a fixed order; a bug = a
mis-ordered or mis-guarded chain (the greeting-clobber and WAIT-suppression bugs were
exactly that).

---

## 3. The one pure function

```python
# agent/control_plane.py  (new; pure, stdlib, deterministic, no LLM)
@dataclass
class Decision:
    turn_intent: str            # CONTINUE|STOP|CORRECT|CONFIRM|REJECT|REPEAT|SAVE|RECALL|NORMAL
    memory_intent: str          # NONE|SAVE|UPDATE|RECALL|CORRECT|FORGET
    conv_state: str             # NORMAL|CONTINUING|CONFIRMING|SAVING|RECALLING|CORRECTING|TASK_ACTIVE
    turn_owner: str             # USER|AGENT|SYSTEM
    delivery_mode: str          # NEW|CONTINUE|HOLD|SILENT
    pending_action: str | None  # e.g. {"kind": "confirm_number", "value": "..."}
    policy: dict                # bounds handed to build_policy_and_contract
    llm_instruction: str | None # what the LLM is told to do (or None = no LLM)

def control_turn(*, state: AgentState, text: str, turn_no: int,
                 signals: dict) -> Decision:
    ...
```

**Contract:**
- **Pure** — same inputs ⇒ same Decision (the Phase-0 harness philosophy extended to the
  whole decision layer; `run_turn` becomes a wrapper around it).
- **One call per turn** in `main.py` — replaces the current chain
  (`route_decision` → `precision_rail_decide` → `turn_controller_decide` → greeting →
  `build_policy_and_contract` → apply). Precedence bugs become impossible: the table
  decides, not the call order.
- **Bounded** — regex passes only; microseconds; no LLM, no I/O.
- **Logged** — `main.py` emits one `DECISION` event per turn (see §7) with the full
  Decision; the `turn_lifecycle` file becomes a true control-plane trace.
- **Degradable** — if `control_turn` raises, the existing unbound-guard fires the
  deterministic filler; the response path never dies with the controller.

---

## 4. Intents — two axes, state-conditioned, over EXISTING detectors

**Axis 1 — `turn_intent`** (what the user wants the conversation to do):

```
CONTINUE  STOP  CORRECT  CONFIRM  REJECT  REPEAT  SAVE  RECALL  NORMAL
```

**Axis 2 — `memory_intent`** (what the system should do with memory this turn):

```
NONE  SAVE  UPDATE  RECALL  CORRECT  FORGET
```

**The rule that makes this reliable: intent = f(signal, state), never signal alone.**

| Signal | State | turn_intent | memory_intent |
|---|---|---|---|
| "बस" | dictating | **STOP** (+confirm) | SAVE (the pending value) |
| "बस" | confirming | **CONFIRM** | SAVE (confirmed) |
| "हां" | confirming | **CONFIRM** | SAVE |
| "हां" | normal | NORMAL | NONE |
| "आगे बताओ" | LLM detail active | **CONTINUE** | NONE |
| "आगे बताओ" | nothing active | NORMAL | NONE |
| "याद रख लेना" | — | **SAVE** | SAVE |
| "मैंने क्या बताया था?" | — | **RECALL** | RECALL |
| "नहीं, वो नैनीताल नहीं था" | memory present | **CORRECT** | CORRECT |
| "50-60 लोग" | dictating | STOP/accumulate | POSSIBLE_SAVE→candidate |
| "कौशलपुरी अशोक नगर कानपुर" | address-request | NORMAL (LLM) | NONE (Gap W stays out) |

**Implementation discipline (the push-back on your §2):** do NOT hand-write a new
detector set. The 30+ regexes already exist (rail `RECALL_RE/STATUS_RE/SAVE_RE`,
controller `classify_turn`, `turn_controller` cues, `_SAVE_INTENT_RE`, greeting markers,
question-reject). The slice consolidates them into ONE table, keyed
`(signal_group, state)` — same patterns, single source, state-conditioned. This is
where "no more special-case rails" becomes structurally true: rails stop being
independent decision-makers and become *signal providers* to the table.

---

## 5. State — two levels, one of which already exists

**Level 1 (transport lifecycle — NOT the controller's job):**
`IDLE→LISTENING→THINKING→SPEAKING→INTERRUPTED→LISTENING`. This is the LiveKit VAD/agent
loop in `main.py`. We keep the event loop as the source of truth; the controller only
*reads* `agent_speaking_event`/`agent_task` for turn ownership. Building a parallel FSM
for this = drift risk + zero benefit.

**Level 2 (conversation state — the controller's job):**

```
NORMAL  CONTINUING  CONFIRMING  SAVING  RECALLING  CORRECTING  TASK_ACTIVE
```

One value per turn, computed by the transition table, persisted in `AgentState`
(§6), logged per turn. `TASK_ACTIVE` absorbs the dictation task (the controller's
existing rows 1–51 migrate here unchanged — same behavior, new home).

**Transition examples (deterministic, LLM has no authority):**

```
TASK_ACTIVE + digits               -> TASK_ACTIVE (accumulate)
TASK_ACTIVE + "बस"                  -> CONFIRMING
CONFIRMING  + "हां"                  -> SAVING -> (commit) -> NORMAL
SPEAKING    + user_speech (VAD)     -> (interrupt) LISTENING   [transport level]
CONTINUING  + "आगे"                  -> CONTINUING (delivery_mode=CONTINUE)
RECALLING   + no record              -> NORMAL + llm_instruction=honest-no-record
```

---

## 6. AgentState — first-class, serialized, logged

Replace the ad-hoc `engine["conv"]`/`engine["detail"]`/`engine["wait_streak"]` dict
keys with one dataclass (the existing `ConversationState` becomes its task sub-object):

```python
@dataclass
class AgentState:
    mode: str                       # CALM|VENT|ADVICE|CLOSING (from state_updater)
    current_goal: str               # derived from mode
    current_topic: str              # from controller topic + detail topic
    current_task: Task | None       # the dictation/structured task (existing Task)
    task_step: int                  # detail delivery step (from engine["detail"])
    delivery_mode: str              # NEW|CONTINUE|HOLD|SILENT
    turn_owner: str                 # USER|AGENT|SYSTEM
    pending_action: dict | None     # {kind, value} awaiting confirm
    memory_intent: str              # last turn's memory intent
    last_user_intent: str           # last turn's turn_intent
    # + existing: waiting_confirmation, interrupted, armed_streak, accum_gap
```

- Serialized to the session log every turn (part of the DECISION event) — debugging is
  replaying the state trace, not guessing.
- `current_topic` comes from the controller's topic tracking (already there) +
  `detail_state.topic`; the topic-switch rows (44) keep working unchanged.

---

## 7. Event log — extend, don't build

The infra exists (`tmark` → `turn_lifecycle_*.jsonl`, `log_event` → `events_*.log`).
Add exactly **four** event types, emitted by the controller (not scattered):

```
INTENT_DETECTED   {turn_intent, memory_intent, signals_hit}
STATE_CHANGED     {conv_state_before -> conv_state_after, turn_owner}
MEMORY_INTENT     {memory_intent, routed_to: capture|recall|correction|none, record_present}
DECISION          {full Decision for the turn}   # one per turn, the trace anchor
```

A weird conversation then reads as the owner imagined — but without a new log system:

```
12:01 USER_SPEECH      ...
12:01 INTENT_DETECTED  CONTINUE
12:01 STATE_CHANGED    CONTINUING -> CONTINUING
12:01 MEMORY_INTENT    NONE
12:01 DECISION         {delivery_mode: CONTINUE, llm_instruction: "continue from step 3"}
12:01 LLM_CALLED       ...
12:02 SPEAKING
12:02 USER_INTERRUPT   ...
12:02 STATE_CHANGED    -> LISTENING
```

---

## 8. Memory lifecycle — the controller routes, the store owns

The owner's memory-flow diagram is already implemented (candidate → gate → pending →
deterministic confirm → commit). The control plane adds **one** thing: `memory_intent`
computation + routing, so the LLM never decides memory *and* the pipeline knows
per-turn what memory work to do:

| memory_intent | Controller action |
|---|---|
| NONE | nothing |
| SAVE | route to existing capture path (place/fact extractors; confirmed number → ROW 51) |
| POSSIBLE_SAVE (inferred) | capture → pending (existing) |
| RECALL | route to existing recall (saved-number rail, memory_view, rule-14 honesty) |
| CORRECT | **new (Phase D hook):** find matching committed row → supersede/update (the preference-supersession gap, deferred — the controller only sets the intent + logs) |
| FORGET | new (Phase D): explicit "भूल जा / हटा दो" → purge path (needs owner sign-off — destructive) |

The consolidation pass (Phase B) stays exactly where it is: Capture/completeness at
session end. The controller does not re-implement any of it.

---

## 9. What the controller does NOT own (locked)

- VAD / STT / TTS / transport, endpointing, barge-in latency (track-4, provider scope)
- The memory store, gate, extractors, consolidation (existing)
- The LLM's prose / persona / contract content (existing)
- Greeting/dictation *lines* (they become signals, not decision-makers)

---

## 10. MemoryPolicy — sketch only, deferred

```python
@dataclass(frozen=True)
class MemoryPolicy:
    scope: str = "GENERAL"                      # GENERAL today; SALES later
    allowed_types: tuple[str, ...] = ("relationship", "episodic", "semantic",
                                      "preference", "saved_number")
    # SALES future: ("company", "role", "requirement", "budget", "timeline",
    #                "pain_point", "competitor")
    max_recall_items: int = 5
```

The control plane reads `policy.scope` to bound capture/retrieval — nothing else.
Building SALES now is explicitly out of scope (owner's §4 — agreed).

---

## 11. Phased build (each slice: tests-first, replay identity ALL PASS, real baseline
UNCHANGED, 42+ suites green)

| # | Slice | Deliverable | Gate |
|---|---|---|---|
| P1 | **Intent table** — consolidate existing detectors into `classify_intent(signal, state)`; wire as a *decision input* first (shadow/telemetry), behavior byte-identical | `control_plane.py` (pure fn + table) + tests | replay EMPTY DIFF, real baseline unchanged |
| P2 | **AgentState first-class** — one serializable state replaces `engine["conv"]/["detail"]/...`; logged per turn | dataclass + migration + tests | same |
| P3 | **Transition table consolidation** — dictation rows 1–51 migrate into the unified table (same behavior, new home); WAIT/greeting become rows | `_transition()` rewrite + tests | same |
| P4 | **memory_intent axis** — compute + route SAVE/RECALL; RECALL/CORRECT hook (Phase D); FORGET gated on sign-off | memory routing + tests | same |
| P5 | **turn_owner + delivery_mode** — detail continuation (fix ③) becomes controller-owned state; "आगे बताओ" never re-starts | delivery state migration + tests | same |

After P5, the sales/support transition is a `MemoryPolicy` change, not an architecture
change — which is the owner's stated goal.

---

## 12. What this buys vs today (reliability + performance)

| Today's failure | After control plane |
|---|---|
| Greeting clobbered by engine block (precedence) | impossible — table decides, not call order |
| "आगे बताओ" re-starts the explanation | CONTINUE + delivery_mode=CONTINUE, instruction "continue from step N" |
| "बस" ambiguous (stop vs confirm) | state-conditioned intent (TASK_ACTIVE→STOP, CONFIRMING→CONFIRM) |
| Dictation leak to LLM ("voice agent pricing" mid-number) | TASK_ACTIVE + digits → system-owned, never NORMAL |
| Memory intent implicit ("नहीं, वो X नहीं था") | memory_intent=CORRECT, logged, routed |
| Debugging by guessing | per-turn DECISION/INTENT/STATE events in existing logs |
| LLM decides everything | LLM gets bounded freedom; controller owns transitions |
| General → sales = rewrite | policy/scope change |
| Performance | one pure pass/turn (µs), no extra LLM calls, no extra I/O beyond the already-logged event |

---

## 13. Explicit rejections (owner asked for pushback)

1. Kubernetes reconcile *loops* — one pure `control_turn()` per turn; no desired/current
   loops.
2. Turn-level FSM — the transport loop is the source of truth; conversation state only.
3. New intent detector set — reuse the existing regexes in one table; rails become
   signal providers.
4. New event-log system — extend `tmark`/`log_event` with 4 event types.
5. Re-implementing memory in the controller — route, don't own.
6. Sales/domain anything now — `MemoryPolicy` sketch only.
7. New frameworks/models — Python + existing SQLite + existing Gemini.
